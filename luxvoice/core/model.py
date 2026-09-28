"""Модель данных команд: коллекция → папка → команда → действия.

Формат хранения — JSON, совместимый с импортом/экспортом Luxify:
дерево узлов с полями id/type/title/enabled/children, у команды —
список фраз и список действий.

Отличия от исходного формата, добавленные как удобство:
  * у узла есть weight — приоритет при равном совпадении фраз;
  * у команды есть tags — метки для поиска и группировки;
  * у команды есть contexts — привязка к окнам, где фраза уместна;
  * у действия есть delay_before/delay_after и continue_on_error;
  * у команды есть cooldown — защита от повторного запуска;
  * хранится счётчик запусков и время последнего выполнения.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Iterator

# --- Типы узлов дерева ------------------------------------------------------

KIND_COLLECTION = "collection"
KIND_FOLDER = "folder"
KIND_COMMAND = "command"


def new_id() -> str:
    """Короткий уникальный идентификатор."""
    return uuid.uuid4().hex[:12]


# --- Действие ---------------------------------------------------------------


@dataclass
class Action:
    """Один шаг команды."""

    type: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    # Необязательное название для интерфейса.
    title: str = ""
    # Пауза перед и после шага, секунды (0 — не ждать).
    delay_before: float = 0.0
    delay_after: float = 0.0
    # Выключенный шаг пропускается.
    enabled: bool = True
    # Продолжать команду, если шаг не удался.
    continue_on_error: bool = False
    # Требует подтверждения (для отдельных шагов внутри безопасной команды).
    confirm: bool = False
    # Повторы при неудаче и пауза между ними.
    retries: int = 0
    retry_delay: float = 0.5
    # Идентификатор для интерфейса.
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "Action":
        if isinstance(data, str):
            # Упрощённая запись: "тип:параметр".
            if ":" in data:
                kind, _, value = data.partition(":")
                return cls(type=kind.strip(), params={"value": value.strip()})
            return cls(type=data.strip())
        if not isinstance(data, dict):
            return cls()

        params = data.get("params")
        if not isinstance(params, dict):
            # Совместимость: параметр мог лежать в поле "value" или "param".
            params = {}
            for key in ("value", "param", "parameter"):
                if key in data:
                    params["value"] = data[key]
                    break
            # Либо параметры лежат прямо в объекте действия.
            known = {"type", "params", "title", "delay_before", "delay_after", "enabled",
                     "continue_on_error", "confirm", "retries", "retry_delay", "id",
                     "value", "param", "parameter"}
            for key, value in data.items():
                if key not in known:
                    params[key] = value

        return cls(
            type=str(data.get("type", "")),
            params=params,
            title=str(data.get("title", "")),
            delay_before=float(data.get("delay_before", 0) or 0),
            delay_after=float(data.get("delay_after", 0) or 0),
            enabled=bool(data.get("enabled", True)),
            continue_on_error=bool(data.get("continue_on_error", False)),
            confirm=bool(data.get("confirm", False)),
            retries=int(data.get("retries", 0) or 0),
            retry_delay=float(data.get("retry_delay", 0.5) or 0.5),
            id=str(data.get("id") or new_id()),
        )

    def get(self, key: str, default: Any = "") -> Any:
        return self.params.get(key, default)

    @property
    def value(self) -> str:
        """Основной параметр действия — короткий доступ."""
        for key in ("value", "text", "url", "path", "keys", "level", "seconds", "command",
                    "name", "query", "phrase", "amount", "coords", "x", "regex"):
            if key in self.params:
                return str(self.params[key])
        for value in self.params.values():
            if isinstance(value, (str, int, float)):
                return str(value)
        return ""


# --- Команда ----------------------------------------------------------------


@dataclass
class Command:
    """Команда: фразы-триггеры плюс последовательность действий."""

    id: str = field(default_factory=new_id)
    title: str = ""
    # Название коллекции, к которой относится команда (для экспорта).
    collection: str = ""
    folder: str = ""

    # Фразы, по которым команда срабатывает (обязательные).
    phrases: list[str] = field(default_factory=list)
    # Служебные слова, которые можно сказать до или после фразы.
    optional: list[str] = field(default_factory=list)

    actions: list[Action] = field(default_factory=list)

    enabled: bool = True
    confirm: bool = False
    # Участвует в связках «команда1 и команда2».
    chainable: bool = True
    # Только для Plus-функций: команда помечается как расширенная.
    requires_shell: bool = False

    # Приоритет (больше — выше при равном совпадении).
    weight: int = 0
    # Метки для поиска и группировки.
    tags: list[str] = field(default_factory=list)
    # Регулярные выражения совпадений окон, где команда уместна.
    contexts: list[str] = field(default_factory=list)
    # Секунды, в течение которых команду нельзя запустить повторно.
    cooldown: float = 0.0
    # Ограничение: не чаще N раз в минуту (0 — без ограничения).
    rate_limit: int = 0

    # Статистика.
    run_count: int = 0
    last_run: float = 0.0
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)

    # Служебное: пак, из которого пришла команда, и его версия.
    pack: str = ""
    pack_version: str = ""
    # Изменена пользователем — пак не перезапишет её при обновлении.
    user_modified: bool = False

    # --- Фразы ------------------------------------------------------------

    def all_phrases(self) -> list[str]:
        """Все фразы, включая необязательные слова как подсказки."""
        seen: list[str] = []
        for phrase in self.phrases:
            text = phrase.strip()
            if text and text not in seen:
                seen.append(text)
        return seen

    def primary_phrase(self) -> str:
        phrases = self.all_phrases()
        return phrases[0] if phrases else ""

    def add_phrase(self, phrase: str) -> bool:
        text = " ".join(phrase.split())
        if not text or text in self.phrases:
            return False
        self.phrases.append(text)
        self.modified = time.time()
        return True

    def add_optional(self, word: str) -> bool:
        text = " ".join(word.split()).lower()
        if not text or text in self.optional:
            return False
        self.optional.append(text)
        self.modified = time.time()
        return True

    def set_phrase(self, index: int, phrase: str) -> bool:
        if not 0 <= index < len(self.phrases):
            return False
        self.phrases[index] = " ".join(phrase.split())
        self.modified = time.time()
        return True

    # --- Действия ---------------------------------------------------------

    def add_action(self, action: Action, index: int | None = None) -> None:
        if index is None or not 0 <= index <= len(self.actions):
            self.actions.append(action)
        else:
            self.actions.insert(index, action)
        self.modified = time.time()

    def move_action(self, index: int, offset: int) -> bool:
        target = index + offset
        if not (0 <= index < len(self.actions) and 0 <= target < len(self.actions)):
            return False
        self.actions[index], self.actions[target] = self.actions[target], self.actions[index]
        self.modified = time.time()
        return True

    def remove_action(self, index: int) -> bool:
        if 0 <= index < len(self.actions):
            del self.actions[index]
            self.modified = time.time()
            return True
        return False

    def duplicate(self, keep_phrases: bool = False) -> "Command":
        """Копия команды с новым идентификатором."""
        clone = Command.from_dict(self.to_dict())
        clone.id = new_id()
        clone.title = f"{self.title} (копия)"
        clone.parent_collection = self.collection
        clone.run_count = 0
        clone.last_run = 0.0
        clone.created = time.time()
        clone.modified = time.time()
        if keep_phrases:
            for index in range(len(clone.phrases)):
                clone.phrases[index] = f"{clone.phrases[index]} копия"
        return clone

    # --- Сериализация -----------------------------------------------------

    @property
    def parent_collection(self) -> str:
        return self.collection

    @parent_collection.setter
    def parent_collection(self, value: str) -> None:
        self.collection = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": KIND_COMMAND,
            "title": self.title,
            "collection": self.collection,
            "folder": self.folder,
            "phrases": list(self.phrases),
            "optional": list(self.optional),
            "actions": [a.to_dict() for a in self.actions],
            "enabled": self.enabled,
            "confirm": self.confirm,
            "chainable": self.chainable,
            "requires_shell": self.requires_shell,
            "weight": self.weight,
            "tags": list(self.tags),
            "contexts": list(self.contexts),
            "cooldown": self.cooldown,
            "rate_limit": self.rate_limit,
            "run_count": self.run_count,
            "last_run": self.last_run,
            "created": self.created,
            "modified": self.modified,
            "pack": self.pack,
            "pack_version": self.pack_version,
            "user_modified": self.user_modified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Command":
        phrases = data.get("phrases")
        if phrases is None:
            # Совместимость: одна фраза в поле phrase.
            single = data.get("phrase") or data.get("trigger") or ""
            phrases = [single] if single else []
        if isinstance(phrases, str):
            phrases = [p.strip() for p in phrases.split("|") if p.strip()]

        optional = data.get("optional") or data.get("optional_words") or []
        if isinstance(optional, str):
            optional = [w.strip().lower() for w in optional.replace(";", ",").split(",") if w.strip()]

        actions_raw = data.get("actions") or data.get("steps") or []
        if isinstance(actions_raw, dict):
            actions_raw = [actions_raw]
        actions = [Action.from_dict(a) for a in actions_raw]

        return cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or data.get("name") or ""),
            collection=str(data.get("collection") or ""),
            folder=str(data.get("folder") or ""),
            phrases=[str(p) for p in phrases if str(p).strip()],
            optional=[str(w).lower() for w in optional],
            actions=actions,
            enabled=bool(data.get("enabled", True)),
            confirm=bool(data.get("confirm", False)),
            chainable=bool(data.get("chainable", True)),
            requires_shell=bool(data.get("requires_shell", False)),
            weight=int(data.get("weight", 0) or 0),
            tags=[str(t) for t in (data.get("tags") or [])],
            contexts=[str(c) for c in (data.get("contexts") or [])],
            cooldown=float(data.get("cooldown", 0) or 0),
            rate_limit=int(data.get("rate_limit", 0) or 0),
            run_count=int(data.get("run_count", 0) or 0),
            last_run=float(data.get("last_run", 0) or 0),
            created=float(data.get("created", time.time())),
            modified=float(data.get("modified", time.time())),
            pack=str(data.get("pack") or ""),
            pack_version=str(data.get("pack_version") or ""),
            user_modified=bool(data.get("user_modified", False)),
        )

    # --- Проверки ---------------------------------------------------------

    def validate(self) -> list[str]:
        """Список замечаний к команде. Пустой — всё в порядке."""
        problems: list[str] = []
        if not self.title.strip():
            problems.append("Не задано название команды")
        if not self.all_phrases():
            problems.append("Не заданы фразы для активации")
        if not self.actions:
            problems.append("Нет ни одного действия")
        for index, action in enumerate(self.actions, 1):
            if not action.type:
                problems.append(f"Действие {index}: не выбран тип")
        return problems

    def is_runnable(self) -> bool:
        return self.enabled and bool(self.actions) and bool(self.all_phrases())

    def cooldown_left(self, now: float | None = None) -> float:
        """Сколько секунд осталось до возможности повторного запуска."""
        if self.cooldown <= 0:
            return 0.0
        now = now if now is not None else time.time()
        left = self.cooldown - (now - self.last_run)
        return max(0.0, left)

    def describe(self) -> str:
        """Краткое человекочитаемое описание для истории и уведомлений."""
        phrase = self.primary_phrase()
        if phrase:
            return self.title or phrase
        return self.title or "Без названия"


# --- Узел дерева ------------------------------------------------------------


@dataclass
class Node:
    """Коллекция или папка. Команды хранятся отдельно, в Command.

    Дерево даёт структуру и порядок, команды — содержимое. Так проще
    и искать по фразам, и переставлять узлы перетаскиванием.
    """

    id: str = field(default_factory=new_id)
    title: str = ""
    kind: str = KIND_FOLDER
    enabled: bool = True
    children: list["Node"] = field(default_factory=list)
    parent: str = ""            # id родителя, пусто — корень
    weight: int = 0
    tags: list[str] = field(default_factory=list)
    icon: str = ""
    expanded: bool = True
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    pack: str = ""
    user_modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.kind,
            "title": self.title,
            "enabled": self.enabled,
            "children": [c.to_dict() for c in self.children],
            "parent": self.parent,
            "weight": self.weight,
            "tags": list(self.tags),
            "icon": self.icon,
            "expanded": self.expanded,
            "created": self.created,
            "modified": self.modified,
            "pack": self.pack,
            "user_modified": self.user_modified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        kind = str(data.get("type") or data.get("kind") or KIND_FOLDER)
        if kind not in (KIND_COLLECTION, KIND_FOLDER, KIND_COMMAND):
            kind = KIND_FOLDER
        children_raw = data.get("children") or []
        children = [Node.from_dict(c) for c in children_raw if isinstance(c, dict)]
        return cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or data.get("name") or ""),
            kind=kind,
            enabled=bool(data.get("enabled", True)),
            children=children,
            parent=str(data.get("parent") or ""),
            weight=int(data.get("weight", 0) or 0),
            tags=[str(t) for t in (data.get("tags") or [])],
            icon=str(data.get("icon") or ""),
            expanded=bool(data.get("expanded", True)),
            created=float(data.get("created", time.time())),
            modified=float(data.get("modified", time.time())),
            pack=str(data.get("pack") or ""),
            user_modified=bool(data.get("user_modified", False)),
        )

    def walk(self) -> Iterator["Node"]:
        """Обход всех узлов, включая себя."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, node_id: str) -> "Node | None":
        for node in self.walk():
            if node.id == node_id:
                return node
        return None

    def path(self) -> str:
        """Полное имя узла через родительские связи — только если известно дерево."""
        return self.title