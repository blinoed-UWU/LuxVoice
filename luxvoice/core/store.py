"""Хранилище дерева команд.

Отвечает за:
  * загрузку и атомарное сохранение дерева;
  * быстрый поиск команд по фразам (обратный индекс);
  * операции над деревом: создание, перемещение, клонирование, удаление;
  * импорт и экспорт в совместимом формате;
  * автоматические резервные копии перед опасными операциями.

Дерево и команды хранятся в одном файле: структура в collection.children,
содержимое — в commands. Так файл остаётся человекочитаемым и переносимым.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from luxvoice.core import paths, schema
from luxvoice.core.events import COMMANDS_CHANGED, bus
from luxvoice.core.model import (
    KIND_COLLECTION,
    KIND_COMMAND,
    KIND_FOLDER,
    Action,
    Command,
    Node,
    new_id,
)

log = logging.getLogger(__name__)

FORMAT_VERSION = 2

# Имя коллекции по умолчанию.
DEFAULT_COLLECTION = "Ассистент"
DEFAULT_FOLDER = "Разное"


def normalize_phrase(text: str) -> str:
    """Привести фразу к сравнимому виду: регистр, ё, лишние пробелы, знаки."""
    text = (text or "").lower().replace("ё", "е")
    # Убираем знаки препинания, оставляя буквы, цифры и пробелы.
    cleaned = []
    for char in text:
        if char.isalnum() or char.isspace():
            cleaned.append(char)
        else:
            cleaned.append(" ")
    text = "".join(cleaned)
    return " ".join(text.split())


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class CommandStore:
    """Потокобезопасное хранилище команд."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else paths.commands_file()
        self._lock = threading.RLock()
        self._nodes: list[Node] = []          # корневые узлы
        self._commands: dict[str, Command] = {}   # id → команда
        self._links: dict[str, Node] = {}         # id команды → узел-владелец
        self._by_phrase: dict[str, list[str]] = {}  # нормализованная фраза → id команд
        self._dirty = False
        self._load_error = ""
        self._rate_log: dict[str, list[float]] = {}
        self._batch = 0

    # --- Свойства ---------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def load_error(self) -> str:
        return self._load_error

    # --- Загрузка и сохранение -------------------------------------------

    def load(self) -> None:
        paths.ensure_dirs()
        data = self._read(self._path)
        if data is None:
            data = self._read_latest_backup()
            if data is not None:
                log.warning("Файл команд повреждён — загружена резервная копия")
                self._load_error = "восстановлено из копии"

        if not isinstance(data, dict):
            self._nodes = []
            self._commands = {}
            self._reindex()
            return

        self._apply(data)
        log.info("Загружено команд: %d, узлов верхнего уровня: %d",
                 len(self._commands), len(self._nodes))

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            log.error("Не удалось прочитать файл команд: %s", exc)
            return None

    def _read_latest_backup(self) -> dict[str, Any] | None:
        try:
            candidates = sorted(paths.backups_dir().glob("commands-*.json"), reverse=True)
        except OSError:
            return None
        for candidate in candidates[:5]:
            data = self._read(candidate)
            if data is not None:
                return data
        return None

    def _apply(self, data: dict[str, Any]) -> None:
        """Разобрать файл: дерево узлов плюс карта «команда → узел»."""
        nodes_raw = data.get("collections") or data.get("tree") or data.get("nodes") or []
        commands_raw = data.get("commands") or []

        nodes: list[Node] = []
        commands: dict[str, Command] = {}
        links: dict[str, Node] = {}

        # --- Дерево с командами внутри (основной формат) ---
        def walk(node_data: dict[str, Any], parent_id: str = "") -> Node | None:
            kind = str(node_data.get("type") or node_data.get("kind") or "")

            if kind == KIND_COMMAND or (not kind and "actions" in node_data):
                cmd = Command.from_dict(node_data)
                commands[cmd.id] = cmd
                return None

            node = Node.from_dict(node_data)
            node.parent = parent_id

            children: list[Node] = []
            for child_data in node_data.get("children") or []:
                if not isinstance(child_data, dict):
                    continue
                child_kind = str(child_data.get("type") or "")
                if child_kind == KIND_COMMAND or (not child_kind and "actions" in child_data):
                    cmd = Command.from_dict(child_data)
                    commands[cmd.id] = cmd
                    links[cmd.id] = node
                    continue
                child = walk(child_data, node.id)
                if child is not None:
                    children.append(child)

            node.children = children
            return node

        for item in nodes_raw:
            if isinstance(item, dict):
                node = walk(item)
                if node is not None:
                    nodes.append(node)

        # --- Плоский список команд (экспорт или простой файл) ---
        for item in commands_raw:
            if not isinstance(item, dict):
                continue
            cmd = Command.from_dict(item)
            commands[cmd.id] = cmd

        # Команды без узла раскладываем по коллекциям из их полей.
        for cmd_id, cmd in commands.items():
            if cmd_id in links:
                continue
            collection_title = cmd.collection or DEFAULT_COLLECTION
            folder_title = cmd.folder or DEFAULT_FOLDER
            links[cmd_id] = self._ensure_chain(nodes, collection_title, folder_title)

        self._nodes = nodes
        self._commands = commands
        self._links = links
        self._reindex()

    def _all_nodes(self, nodes: list[Node]) -> Iterator[Node]:
        for node in nodes:
            yield from node.walk()

    def _ensure_chain(self, nodes: list[Node], collection_title: str,
                      folder_title: str) -> Node:
        collection = None
        for node in nodes:
            if node.kind == KIND_COLLECTION and node.title == collection_title:
                collection = node
                break
        if collection is None:
            collection = Node(title=collection_title, kind=KIND_COLLECTION)
            nodes.append(collection)

        for child in collection.children:
            if child.kind == KIND_FOLDER and child.title == folder_title:
                return child
        folder = Node(title=folder_title, kind=KIND_FOLDER, parent=collection.id)
        collection.children.append(folder)
        return folder

    def _reindex(self) -> None:
        """Перестроить обратный индекс «фраза → команды»."""
        index: dict[str, list[str]] = {}
        for cmd in self._commands.values():
            for phrase in cmd.all_phrases():
                key = normalize_phrase(phrase)
                if not key:
                    continue
                index.setdefault(key, []).append(cmd.id)
            # Индексируем и по отдельным словам — для быстрого отсева.
            for phrase in cmd.all_phrases():
                for word in normalize_phrase(phrase).split():
                    if len(word) < 3:
                        continue
                    key = f"#{word}"
                    bucket = index.setdefault(key, [])
                    if cmd.id not in bucket:
                        bucket.append(cmd.id)
        self._by_phrase = index

    def save(self, force: bool = False) -> bool:
        with self._lock:
            if not self._dirty and not force:
                return True
            payload = self._serialize()
            with self._lock:
                self._backup_before_save()
        try:
            _atomic_write(self._path, json.dumps(payload, ensure_ascii=False, indent=2))
            with self._lock:
                self._dirty = False
            return True
        except OSError as exc:
            log.error("Не удалось сохранить команды: %s", exc)
            return False

    def save_soon(self, delay: float = 1.5) -> None:
        with self._lock:
            self._dirty = True
        timer = getattr(self, "_timer", None)
        if timer is not None and timer.is_alive():
            timer.cancel()
        timer = threading.Timer(delay, self.save)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _serialize(self) -> dict[str, Any]:
        """Собрать дерево для записи: команды вкладываются в свои узлы."""
        collections: list[dict[str, Any]] = []

        # Карта: id узла → список команд
        by_node: dict[str, list[Command]] = {}
        links = self._links
        orphans: list[Command] = []

        for cmd_id, cmd in self._commands.items():
            node = links.get(cmd_id)
            if node is None:
                orphans.append(cmd)
            else:
                by_node.setdefault(node.id, []).append(cmd)

        def build(node: Node) -> dict[str, Any]:
            data = node.to_dict()
            children: list[dict[str, Any]] = []
            for child in node.children:
                children.append(build(child))
            # Команды этого узла — как листья дерева.
            for cmd in sorted(by_node.get(node.id, []), key=lambda c: c.title.lower()):
                children.append(cmd.to_dict())
            data["children"] = children
            return data

        for node in self._nodes:
            collections.append(build(node))

        # Бесхозные команды — в отдельную коллекцию, чтобы не потерять.
        if orphans:
            holder = Node(title="Без папки", kind=KIND_COLLECTION)
            collections.append({
                **holder.to_dict(),
                "children": [c.to_dict() for c in sorted(orphans, key=lambda c: c.title.lower())],
            })

        return {
            "format": "luxvoice",
            "version": FORMAT_VERSION,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
            "collections": collections,
        }

    def _backup_before_save(self) -> None:
        settings_ok = True
        if not self._path.exists():
            return
        try:
            directory = paths.backups_dir()
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d")
            target = directory / f"commands-{stamp}.json"
            if not target.exists():
                shutil.copy2(self._path, target)
                self._prune_backups()
        except OSError as exc:
            log.debug("Копия команд не создана: %s", exc)

    def _prune_backups(self) -> None:
        directory = paths.backups_dir()
        try:
            files = sorted(directory.glob("commands-*.json"))
        except OSError:
            return
        keep = 14
        for old in files[:-keep] if len(files) > keep else []:
            try:
                old.unlink()
            except OSError:
                pass

    # --- Доступ к данным --------------------------------------------------

    def commands(self) -> list[Command]:
        with self._lock:
            return list(self._commands.values())

    def enabled_commands(self) -> list[Command]:
        return [c for c in self.commands() if c.enabled]

    def get(self, cmd_id: str) -> Command | None:
        with self._lock:
            return self._commands.get(cmd_id)

    def count(self) -> int:
        with self._lock:
            return len(self._commands)

    def collections(self) -> list[Node]:
        with self._lock:
            return list(self._nodes)

    def find_node(self, node_id: str) -> Node | None:
        with self._lock:
            for node in self._all_nodes(self._nodes):
                if node.id == node_id:
                    return node
        return None

    def node_of(self, cmd_id: str) -> Node | None:
        return self._links.get(cmd_id)

    def commands_in(self, node_id: str, recursive: bool = True) -> list[Command]:
        """Команды внутри узла."""
        node = self.find_node(node_id)
        if node is None:
            return []
        links = self._links
        ids: set[str] = set()
        targets = list(node.walk()) if recursive else [node]
        for target in targets:
            for cmd_id, owner in links.items():
                if owner is target:
                    ids.add(cmd_id)
        return [self._commands[i] for i in ids if i in self._commands]

    def parent_of(self, cmd_id: str) -> Node | None:
        return self.node_of(cmd_id)

    # --- Поиск ------------------------------------------------------------

    def search(self, query: str, limit: int = 200) -> list[Command]:
        """Поиск по названию, фразам и меткам.

        Совпадение ищется и целиком, и по началу слова: запрос «громк»
        находит команду с фразой «сделай громче».
        """
        needle = normalize_phrase(query)
        if not needle:
            return self.commands()[:limit]

        def hits(text: str) -> bool:
            """Запрос встречается в тексте целиком или как начало слова."""
            if not text:
                return False
            if needle in text:
                return True
            return any(word.startswith(needle) for word in text.split())

        results: list[tuple[int, Command]] = []
        for cmd in self.commands():
            score = 0
            title = normalize_phrase(cmd.title)
            if title.startswith(needle):
                score += 100
            elif hits(title):
                score += 60
            for phrase in cmd.all_phrases():
                if hits(normalize_phrase(phrase)):
                    score += 40
                    break
            for tag in cmd.tags:
                if hits(normalize_phrase(tag)):
                    score += 30
                    break
            if score:
                results.append((score, cmd))

        results.sort(key=lambda pair: (-pair[0], pair[1].title.lower()))
        return [cmd for _, cmd in results[:limit]]

    def candidates_for_words(self, words: list[str]) -> list[Command]:
        """Быстрый отсев: команды, у которых встречается хотя бы одно слово."""
        with self._lock:
            index = self._by_phrase
            seen: dict[str, Command] = {}
            for word in words:
                word = normalize_phrase(word)
                if len(word) < 3:
                    continue
                for cmd_id in index.get(f"#{word}", ()):
                    cmd = self._commands.get(cmd_id)
                    if cmd is not None:
                        seen[cmd_id] = cmd
            # Если отсев ничего не дал, возвращаем все — лучше найти, чем пропустить.
            if not seen:
                return list(self._commands.values())
            return list(seen.values())

    def phrase_owner(self, phrase: str) -> Command | None:
        """Команда, у которой уже есть точно такая фраза."""
        key = normalize_phrase(phrase)
        with self._lock:
            for cmd_id in self._by_phrase.get(key, ()):
                cmd = self._commands.get(cmd_id)
                if cmd is not None:
                    return cmd
        return None

    def duplicate_phrases(self) -> dict[str, list[Command]]:
        """Фразы, встречающиеся у нескольких команд — для предупреждения."""
        out: dict[str, list[Command]] = {}
        with self._lock:
            for key, ids in self._by_phrase.items():
                if key.startswith("#"):
                    continue
                if len(ids) > 1:
                    out[key] = [self._commands[i] for i in ids if i in self._commands]
        return out

    # --- Создание и изменение --------------------------------------------

    def create_collection(self, title: str) -> Node:
        node = Node(title=title.strip() or DEFAULT_COLLECTION, kind=KIND_COLLECTION)
        with self._lock:
            self._nodes.append(node)
            self._dirty = True
        self._notify()
        return node

    def create_folder(self, parent_id: str, title: str) -> Node | None:
        parent = self.find_node(parent_id)
        if parent is None or parent.kind == KIND_FOLDER:
            # Папку можно создать только внутри коллекции или другой папки.
            if parent is None:
                return None
        node = Node(title=title.strip() or DEFAULT_FOLDER, kind=KIND_FOLDER, parent=parent_id)
        with self._lock:
            parent.children.append(node)
            parent.modified = time.time()
            self._dirty = True
        self._notify()
        return node

    def create_command(self, node_id: str, title: str = "Новая команда") -> Command | None:
        node = self.find_node(node_id)
        if node is None or node.kind == KIND_COMMAND:
            return None
        cmd = Command(title=title)
        with self._lock:
            self._commands[cmd.id] = cmd
            self._links[cmd.id] = node
            node.modified = time.time()
            self._dirty = True
            self._reindex()
        self._notify()
        return cmd

    def add_command(self, command: Command, node_id: str) -> Command:
        """Добавить готовую команду в узел."""
        node = self.find_node(node_id)
        if node is None:
            # Создаём коллекцию с нужным названием.
            node = self.create_collection(command.collection or DEFAULT_COLLECTION)
        with self._lock:
            if command.id in self._commands:
                command.id = new_id()
            self._commands[command.id] = command
            self._links[command.id] = node
            self._dirty = True
            self._reindex()
        self._notify()
        return command

    def update_command(self, command: Command, mark_modified: bool = True) -> None:
        with self._lock:
            if command.id not in self._commands:
                return
            if command.pack and mark_modified:
                # Команда из пака правится пользователем — пак её не перезапишет.
                original = self._commands[command.id]
                if original.to_dict() != command.to_dict():
                    command.user_modified = True
            command.modified = time.time()
            self._commands[command.id] = command
            self._dirty = True
            self._reindex()
        self._notify()

    def remove_command(self, cmd_id: str) -> bool:
        with self._lock:
            if cmd_id not in self._commands:
                return False
            del self._commands[cmd_id]
            links = self._links
            links.pop(cmd_id, None)
            self._dirty = True
            self._reindex()
        self._notify()
        return True

    def remove_node(self, node_id: str, remove_commands: bool = True) -> int:
        """Удалить узел. Возвращает число удалённых команд."""
        with self._lock:
            node = self.find_node(node_id)
            if node is None:
                return 0

            def detach(nodes: list[Node]) -> bool:
                for index, candidate in enumerate(nodes):
                    if candidate.id == node_id:
                        del nodes[index]
                        return True
                    if detach(candidate.children):
                        return True
                return False

            detach(self._nodes)

            removed = 0
            if remove_commands:
                links = self._links
                for target in node.walk():
                    for cmd_id in [cid for cid, owner in links.items() if owner is target]:
                        self._commands.pop(cmd_id, None)
                        links.pop(cmd_id, None)
                        removed += 1
            self._dirty = True
            self._reindex()
        self._notify()
        return removed

    def rename_node(self, node_id: str, title: str) -> bool:
        node = self.find_node(node_id)
        if node is None or not title.strip():
            return False
        with self._lock:
            node.title = title.strip()
            node.modified = time.time()
            node.user_modified = True
            self._dirty = True
        self._notify()
        return True

    def set_enabled(self, node_id: str, enabled: bool) -> int:
        """Включить/выключить узел со всем содержимым. Возвращает число команд."""
        node = self.find_node(node_id)
        if node is None:
            return 0
        affected = 0
        with self._lock:
            for target in node.walk():
                target.enabled = enabled
                target.modified = time.time()
            links = self._links
            for cmd_id, owner in links.items():
                if any(owner is t for t in node.walk()):
                    cmd = self._commands.get(cmd_id)
                    if cmd is not None:
                        cmd.enabled = enabled
                        affected += 1
            self._dirty = True
        self._notify()
        return affected

    def move_node(self, node_id: str, new_parent_id: str, index: int = -1) -> bool:
        """Переместить узел к новому родителю, не создавая циклов."""
        node = self.find_node(node_id)
        if node is None:
            return False
        target = self.find_node(new_parent_id)
        if target is None or target.kind == KIND_COMMAND:
            return False
        # Нельзя вложить узел в самого себя или в своего потомка.
        if any(desc.id == target.id for desc in node.walk()):
            return False

        with self._lock:
            def detach(nodes: list[Node]) -> bool:
                for i, candidate in enumerate(nodes):
                    if candidate.id == node_id:
                        del nodes[i]
                        return True
                    if detach(candidate.children):
                        return True
                return False

            detach(self._nodes)
            node.parent = target.id
            if index < 0 or index > len(target.children):
                target.children.append(node)
            else:
                target.children.insert(index, node)
            target.modified = time.time()
            self._dirty = True
        self._notify()
        return True

    def move_command(self, cmd_id: str, new_node_id: str) -> bool:
        node = self.find_node(new_node_id)
        if node is None or node.kind == KIND_COMMAND:
            return False
        cmd = self.get(cmd_id)
        if cmd is None:
            return False
        with self._lock:
            self._links[cmd_id] = node
            cmd.collection = node.title
            cmd.modified = time.time()
            self._dirty = True
        self._notify()
        return True

    def reorder_node(self, node_id: str, offset: int) -> bool:
        """Сдвинуть узел вверх/вниз среди соседей."""
        node = self.find_node(node_id)
        if node is None:
            return False

        def siblings(nodes: list[Node]) -> list[Node] | None:
            for candidate in nodes:
                if candidate.id == node_id:
                    return nodes
                found = siblings(candidate.children)
                if found is not None:
                    return found
            return None

        with self._lock:
            group = siblings(self._nodes)
            if group is None:
                return False
            index = next((i for i, n in enumerate(group) if n.id == node_id), -1)
            target = index + offset
            if index < 0 or not 0 <= target < len(group):
                return False
            group[index], group[target] = group[target], group[index]
            self._dirty = True
        self._notify()
        return True

    def touch_run(self, cmd_id: str) -> None:
        """Отметить выполнение команды (для статистики и лимитов)."""
        cmd = self.get(cmd_id)
        if cmd is None:
            return
        with self._lock:
            cmd.run_count += 1
            cmd.last_run = time.time()
            log_times = self._rate_log.setdefault(cmd_id, [])
            now = time.time()
            log_times.append(now)
            # Держим только записи за последнюю минуту.
            self._rate_log[cmd_id] = [t for t in log_times if now - t < 60]
            self._dirty = True

    def rate_limit_exceeded(self, cmd: Command) -> bool:
        if cmd.rate_limit <= 0:
            return False
        now = time.time()
        recent = [t for t in self._rate_log.get(cmd.id, []) if now - t < 60]
        return len(recent) >= cmd.rate_limit

    # --- Навигация --------------------------------------------------------

    def path_of(self, node_id: str) -> list[Node]:
        """Путь от корня до узла."""
        result: list[Node] = []

        def search(nodes: list[Node], trail: list[Node]) -> bool:
            for node in nodes:
                current = trail + [node]
                if node.id == node_id:
                    result.extend(current)
                    return True
                if search(node.children, current):
                    return True
            return False

        search(self._nodes, [])
        return result

    def flatten(self, node_ids: list[str] | None = None) -> list[tuple[Node, Command]]:
        """Плоский список «узел → команда» для табличных представлений."""
        links = self._links
        out: list[tuple[Node, Command]] = []
        for cmd_id, cmd in self._commands.items():
            owner = links.get(cmd_id)
            if owner is None:
                continue
            if node_ids is not None and owner.id not in node_ids:
                continue
            out.append((owner, cmd))
        return out

    def statistics(self) -> dict[str, Any]:
        total = len(self._commands)
        enabled = sum(1 for c in self._commands.values() if c.enabled)
        actions = sum(len(c.actions) for c in self._commands.values())
        phrases = sum(len(c.phrases) for c in self._commands.values())
        runs = sum(c.run_count for c in self._commands.values())
        return {
            "commands": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "actions": actions,
            "phrases": phrases,
            "runs": runs,
            "collections": len(self._nodes),
        }

    # --- Импорт и экспорт -------------------------------------------------

    def export_command(self, cmd_id: str) -> dict[str, Any] | None:
        cmd = self.get(cmd_id)
        return cmd.to_dict() if cmd else None

    def export_many(self, cmd_ids: list[str]) -> dict[str, Any]:
        """Экспорт выбранных команд с сохранением структуры."""
        links = self._links
        items = []
        for cmd_id in cmd_ids:
            cmd = self.get(cmd_id)
            if cmd is None:
                continue
            owner = links.get(cmd_id)
            path: list[str] = []
            if owner is not None:
                path = [n.title for n in self.path_of(owner.id)]
            data = cmd.to_dict()
            data["path"] = path
            items.append(data)
        return {
            "format": "luxvoice.commands",
            "version": FORMAT_VERSION,
            "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "commands": items,
        }

    def export_tree(self, node_id: str | None = None) -> dict[str, Any]:
        """Экспорт узла со всем содержимым во вложенном виде."""
        with self._lock:
            payload = self._serialize()
        if node_id is None:
            return payload

        def find(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
            for node in nodes:
                if node.get("id") == node_id:
                    return node
                found = find(node.get("children") or [])
                if found is not None:
                    return found
            return None

        found = find(payload.get("collections") or [])
        return {
            "format": "luxvoice",
            "version": FORMAT_VERSION,
            "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
            "collections": [found] if found else [],
        }

    def import_data(self, data: dict[str, Any], target_node_id: str | None = None,
                    conflict: str = "rename") -> dict[str, int]:
        """Импорт команд из файла.

        conflict: rename (добавить «(импорт)»), skip (пропустить дубликаты),
                  overwrite (заменить команду с той же фразой).
        Возвращает статистику.
        """
        stats = {"added": 0, "updated": 0, "skipped": 0, "folders": 0}

        target = self.find_node(target_node_id) if target_node_id else None

        def add(cmd: Command, parent: Node) -> None:
            existing = self.phrase_owner(cmd.primary_phrase()) if cmd.primary_phrase() else None
            if existing is not None:
                if conflict == "skip":
                    stats["skipped"] += 1
                    return
                if conflict == "overwrite":
                    cmd.id = existing.id
                    self.update_command(cmd)
                    stats["updated"] += 1
                    return
                cmd.title = f"{cmd.title} (импорт)"
                for index in range(len(cmd.phrases)):
                    cmd.phrases[index] = f"{cmd.phrases[index]} импорт"

            cmd.id = new_id()
            self._commands[cmd.id] = cmd
            self._links[cmd.id] = parent
            stats["added"] += 1

        def walk_import(node_data: dict[str, Any], parent: Node | None) -> None:
            kind = str(node_data.get("type") or "")
            if kind == KIND_COMMAND or (not kind and "actions" in node_data):
                cmd = Command.from_dict(node_data)
                add(cmd, parent or target or self._default_node())
                return

            title = str(node_data.get("title") or "Импорт")
            if parent is None and target is not None:
                owner = target
            else:
                holder = parent or target
                if holder is None:
                    holder = self._default_node()
                folder = Node(title=title, kind=KIND_FOLDER, parent=holder.id)
                holder.children.append(folder)
                owner = folder
                stats["folders"] += 1

            for child in node_data.get("children") or []:
                if isinstance(child, dict):
                    walk_import(child, owner)

        collections = data.get("collections") or data.get("tree") or []
        if collections:
            for item in collections:
                if isinstance(item, dict):
                    walk_import(item, None)

        for item in data.get("commands") or []:
            if isinstance(item, dict):
                cmd = Command.from_dict(item)
                add(cmd, target or self._default_node())

        self._reindex()
        self._dirty = True
        self._notify()
        return stats

    def _default_node(self) -> Node:
        """Узел для импорта, если не указан: первая коллекция или новая."""
        if self._nodes:
            first = self._nodes[0]
            if first.kind == KIND_COLLECTION:
                for child in first.children:
                    if child.kind == KIND_FOLDER:
                        return child
                folder = Node(title=DEFAULT_FOLDER, kind=KIND_FOLDER, parent=first.id)
                first.children.append(folder)
                return folder
            return first
        return self.create_collection(DEFAULT_COLLECTION)

    # --- Утилиты ----------------------------------------------------------

    def start_batch(self) -> None:
        """Начать массовое изменение — уведомления и запись откладываются."""
        with self._lock:
            self._batch = getattr(self, "_batch", 0) + 1

    def end_batch(self, save: bool = True) -> None:
        with self._lock:
            self._batch = max(0, getattr(self, "_batch", 1) - 1)
            pending = self._batch == 0
        if pending:
            if save:
                self.save()
            self._notify()

    def _notify(self) -> None:
        if getattr(self, "_batch", 0) > 0:
            return
        bus.publish(COMMANDS_CHANGED)

    def ensure_defaults(self) -> None:
        """Если дерево пустое — создать базовую структуру и набор команд."""
        if self._nodes and self._commands:
            return
        if not self._nodes:
            collection = self.create_collection(DEFAULT_COLLECTION)
            main_folder = self.create_folder(collection.id, "Основные")
            self.create_folder(collection.id, "Сценарии")

            # Базовые команды для быстрого старта.
            self._add_default_commands(main_folder.id, collection.title)

        self.save()

    def _add_default_commands(self, folder_id: str, collection: str) -> None:
        """Добавить набор часто используемых команд."""
        from luxvoice.core.model import Action, Command

        commands = [
            Command(
                title="Громче",
                phrases=["сделай громче", "увеличь громкость", "громче"],
                actions=[Action(type="volume_up", params={"amount": 10})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Тише",
                phrases=["сделай тише", "уменьши громкость", "тише"],
                actions=[Action(type="volume_down", params={"amount": 10})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Без звука",
                phrases=["выключи звук", "без звука", "мут"],
                actions=[Action(type="toggle_mute", params={})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Снимок экрана",
                phrases=["сделай скриншот", "снимок экрана", "скрин"],
                actions=[Action(type="screenshot", params={})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Заблокировать экран",
                phrases=["заблокируй экран", "заблокируй", "блок"],
                actions=[Action(type="lock_screen", params={})],
                folder=folder_id,
                collection=collection,
                confirm=True,
            ),
            Command(
                title="Закрыть окно",
                phrases=["закрой окно", "закрой"],
                actions=[Action(type="close_window", params={})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Свернуть все окна",
                phrases=["сверни всё", "сверни все окна", "покажи рабочий стол"],
                actions=[Action(type="minimize_all", params={})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Открыть файловый менеджер",
                phrases=["открой файлы", "открой проводник", "файловый менеджер"],
                actions=[Action(type="launch_app", params={"path": "dolphin"})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Открыть терминал",
                phrases=["открой терминал", "запусти консоль"],
                actions=[Action(type="launch_app", params={"path": "konsole"})],
                folder=folder_id,
                collection=collection,
            ),
            Command(
                title="Выключить монитор",
                phrases=["выключи монитор", "погаси экран"],
                actions=[Action(type="screen_off", params={})],
                folder=folder_id,
                collection=collection,
            ),
        ]

        for cmd in commands:
            self.add_command(cmd, folder_id)


# --- Единственный экземпляр -------------------------------------------------

_store: CommandStore | None = None
_store_lock = threading.Lock()


def get_store(reload: bool = False) -> CommandStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CommandStore()
            _store.load()
            _store._links = getattr(_store, "_links", {})
        elif reload:
            _store.load()
        return _store