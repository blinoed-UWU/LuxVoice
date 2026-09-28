"""Запуск программ, открытие файлов и сайтов, поиск приложений.

Программы ищутся в трёх источниках:
  1. ярлыки рабочего стола (.desktop) — дают человеческие названия;
  2. каталоги из PATH и пользовательские пути из настроек;
  3. команды Flatpak, Snap и AppImage.

Название сопоставляется нечётко: «фотошоп» найдёт ярлык «Adobe Photoshop»,
«телега» — «Telegram Desktop».
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from luxvoice.core.matcher import phrase_similarity, token_similarity

log = logging.getLogger(__name__)

# --- Транслитерация ---------------------------------------------------------

# Русская раскладка, соответствующая латинской. Нужна, чтобы «файрфокс»
# находил Firefox, а «стим» — Steam: распознавание речи выдаёт кириллицу,
# а программы в системе названы латиницей.
_RU_TO_EN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Обиходные русские названия популярных программ и сервисов.
_COLLOQUIAL: dict[str, str] = {
    "файрфокс": "firefox", "фаерфокс": "firefox", "огнелис": "firefox",
    "хром": "chrome", "гугл хром": "chrome", "хромиум": "chromium",
    "яндекс браузер": "yandex-browser", "опера": "opera", "браве": "brave",
    "стим": "steam", "телега": "telegram", "телеграм": "telegram",
    "ватсап": "whatsapp", "вотсап": "whatsapp", "дискорд": "discord",
    "дискорт": "discord", "спотифай": "spotify", "спотифи": "spotify",
    "скайп": "skype", "зум": "zoom", "тимс": "teams", "слак": "slack",
    "код": "code", "вс код": "code", "визуал студио код": "code",
    "студия": "code", "вижн": "vscode", "курсор": "cursor",
    "блендер": "blender", "фигма": "figma", "фотошоп": "photoshop",
    "иллюстратор": "illustrator", "премиер": "premiere", "эффекты": "aftereffects",
    "обс": "obs", "девайн": "davinci", "давинчи": "davinci",
    "калькулятор": "calc", "проводник": "dolphin", "файлы": "dolphin",
    "терминал": "konsole", "консоль": "konsole", "консоле": "konsole",
    "блокнот": "kate", "текстовый редактор": "kate", "кейт": "kate",
    "настройки": "systemsettings", "параметры": "systemsettings",
    "диспетчер": "systemmonitor", "монитор": "systemmonitor",
    "почта": "thunderbird", "календарь": "kalendar",
    "майнкрафт": "minecraft", "майн": "minecraft", "кс": "cs2",
    "дота": "dota", "дота 2": "dota", "фортнайт": "fortnite",
    "пабг": "pubg", "танки": "wot", "робикс": "roblox",
    "вк": "vk", "вконтакте": "vk", "одноклассники": "ok",
    "кинопоиск": "kinopoisk", "ютуб": "youtube", "ютуб музыка": "youtube-music",
    "яндекс музыка": "yandex-music", "яндексмузыка": "yandex-music",
    "гугл": "google", "яндекс": "yandex", "почта гугл": "gmail",
    "стимдек": "steam", "андеск": "anydesk", "анидеск": "anydesk",
    "лунар": "lunarclient", "соber": "sober", "собер": "sober",
    "клод": "claude", "чатгпт": "chatgpt", "чат гпт": "chatgpt",
    "ноушн": "notion", "обсидиан": "obsidian", "тикток": "tiktok",
    "вегас": "vegas", "капкат": "capcut", "кап кат": "capcut",
}


def _translit_ru(text: str) -> str:
    """Перевести кириллицу в латиницу приблизительной транслитерацией."""
    return "".join(_RU_TO_EN.get(char, char) for char in text.lower())


def _variants(text: str) -> list[str]:
    """Все варианты написания для сопоставления."""
    from luxvoice.core.store import normalize_phrase
    base = normalize_phrase(text)
    result = [base]

    colloquial = _COLLOQUIAL.get(base)
    if colloquial:
        result.append(colloquial)
        # Также по частям: «яндекс музыка» → «яндекс», «музыка».
        for word in colloquial.split():
            if len(word) >= 3:
                result.append(word)

    # Транслитерация кириллицы.
    if any("\u0400" <= char <= "\u04ff" for char in base):
        translit = _translit_ru(base)
        if translit != base:
            result.append(translit)
        # Упрощённая транслитерация без диграфов: «файрфокс» → «fajrfoks».
        simple = base.translate(str.maketrans({
            "ж": "j", "х": "x", "ц": "z", "ч": "c", "ш": "s", "щ": "s",
            "ю": "u", "я": "a", "й": "i", "ы": "i", "э": "e",
        }))
        simple = _translit_ru(simple)
        if simple not in result:
            result.append(simple)

    return [v for v in dict.fromkeys(result) if v]


@dataclass
class AppEntry:
    """Найденная программа."""

    name: str                    # человеческое название
    command: str                 # команда запуска
    path: str = ""               # путь к файлу, если есть
    source: str = "path"         # desktop, path, flatpak, snap, appimage, user
    icon: str = ""
    keywords: list[str] = field(default_factory=list)
    terminal: bool = False

    @property
    def searchable(self) -> str:
        """Строка для сопоставления с голосовой фразой."""
        parts = [self.name, self.command, self.path]
        parts.extend(self.keywords)
        return " ".join(p for p in parts if p).lower()


# Каталоги ярлыков.
_DESKTOP_DIRS = (
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "~/.local/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)


class AppLocator:
    """Поиск и запуск программ."""

    def __init__(self) -> None:
        self._entries: list[AppEntry] = []
        self._loaded = 0.0
        self._lock = threading.RLock()
        self._search_dirs: list[Path] = []
        self._index_ttl = 300.0   # секунд до пересканирования

    # --- Настройки --------------------------------------------------------

    def set_search_dirs(self, directories: list[str]) -> None:
        """Задать каталоги поиска исполняемых файлов."""
        result: list[Path] = []
        for item in directories:
            for chunk in str(item).replace(";", ":").split(":"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                expanded = Path(chunk).expanduser()
                if expanded not in result:
                    result.append(expanded)
        self._search_dirs = result
        with self._lock:
            self._loaded = 0.0   # заставляем перестроить индекс

    # --- Индекс -----------------------------------------------------------

    def entries(self, refresh: bool = False) -> list[AppEntry]:
        """Список всех найденных программ."""
        with self._lock:
            fresh = time.time() - self._loaded < self._index_ttl
            if fresh and self._entries and not refresh:
                return list(self._entries)

        result: list[AppEntry] = []
        result.extend(self._scan_desktop())
        result.extend(self._scan_path())
        result.extend(self._scan_flatpak())
        result.extend(self._scan_snap())

        # Убираем дубликаты по команде запуска, сохраняя более «богатые» записи.
        unique: dict[str, AppEntry] = {}
        for entry in result:
            key = entry.command.split()[0] if entry.command else entry.name
            existing = unique.get(key)
            if existing is None:
                unique[key] = entry
            elif entry.source == "desktop" and existing.source != "desktop":
                unique[key] = entry
            elif len(entry.keywords) > len(existing.keywords):
                unique[key] = entry

        with self._lock:
            self._entries = list(unique.values())
            self._loaded = time.time()
        log.info("Найдено программ: %d", len(self._entries))
        return list(self._entries)

    def _scan_desktop(self) -> list[AppEntry]:
        """Разобрать ярлыки рабочего стола."""
        result: list[AppEntry] = []
        seen_paths: set[str] = set()

        for directory in _DESKTOP_DIRS:
            folder = Path(directory).expanduser()
            if not folder.exists():
                continue
            try:
                files = sorted(folder.glob("*.desktop"))
            except OSError:
                continue

            for file in files:
                if str(file) in seen_paths:
                    continue
                seen_paths.add(str(file))
                entry = self._parse_desktop(file)
                if entry is not None:
                    result.append(entry)
        return result

    def _parse_desktop(self, path: Path) -> AppEntry | None:
        """Прочитать .desktop и собрать запись о программе."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        name = ""
        generic = ""
        command = ""
        icon = ""
        keywords: list[str] = []
        in_entry = False
        hidden = False
        no_display = False
        terminal = False
        name_localized = ""

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_entry = stripped == "[Desktop Entry]"
                continue
            if not in_entry or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()

            if key == "Name":
                name = value
            elif key.startswith("Name[") and not name_localized:
                # Локализованное название — предпочитаем русское.
                if "ru" in key:
                    name_localized = value
            elif key == "GenericName":
                generic = value
            elif key in ("Exec", "TryExec"):
                if key == "Exec":
                    command = value
            elif key == "Icon":
                icon = value
            elif key == "Keywords":
                keywords.extend(k.strip() for k in value.split(";") if k.strip())
            elif key.startswith("Keywords["):
                keywords.extend(k.strip() for k in value.split(";") if k.strip())
            elif key == "NoDisplay":
                no_display = value.lower() == "true"
            elif key == "Hidden":
                hidden = value.lower() == "true"
            elif key == "Terminal":
                terminal = value.lower() == "true"

        if hidden or no_display or not command:
            return None
        # Пропускаем ярлыки вида «Открыть в терминале».
        if "org.gnome.Terminal" in command and "Desktop" in path.name:
            pass

        display_name = name_localized or name or path.stem
        command_clean = self._clean_exec(command)
        if not command_clean:
            return None

        return AppEntry(
            name=display_name,
            command=command_clean,
            path=str(path),
            source="desktop",
            icon=icon,
            keywords=[k for k in (generic, path.stem, *keywords) if k],
            terminal=terminal,
        )

    @staticmethod
    def _clean_exec(exec_line: str) -> str:
        """Убрать из Exec поля-заполнители вроде %U, %f."""
        text = re.sub(r"%[fFuUdDnNickvm]", "", exec_line)
        return " ".join(text.split())

    def _scan_path(self) -> list[AppEntry]:
        """Найти исполняемые файлы в каталогах поиска."""
        result: list[AppEntry] = []
        directories = self._search_dirs or [
            Path(p) for p in os.environ.get("PATH", "").split(":") if p
        ]
        # Всегда добавляем стандартные каталоги и пользовательские.
        for extra in ("~/.local/bin", "~/bin"):
            folder = Path(extra).expanduser()
            if folder not in directories:
                directories.append(folder)

        seen: set[str] = set()
        for directory in directories:
            if not directory.exists() or not directory.is_dir():
                continue
            try:
                items = sorted(directory.iterdir())
            except OSError:
                continue

            for item in items:
                try:
                    if not item.is_file() or not os.access(item, os.X_OK):
                        continue
                except OSError:
                    continue
                name = item.name
                if name in seen or name.endswith((".so", ".pyc")):
                    continue
                seen.add(name)
                result.append(AppEntry(
                    name=name,
                    command=str(item),
                    path=str(item),
                    source="path",
                ))
        return result

    def _scan_flatpak(self) -> list[AppEntry]:
        """Программы Flatpak."""
        if not shutil.which("flatpak"):
            return []
        try:
            completed = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application,name"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []

        result: list[AppEntry] = []
        for line in completed.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            app_id, name = parts[0].strip(), parts[1].strip()
            if not app_id:
                continue
            result.append(AppEntry(
                name=name or app_id,
                command=f"flatpak run {app_id}",
                source="flatpak",
                keywords=[app_id, app_id.split(".")[-1]],
            ))
        return result

    def _scan_snap(self) -> list[AppEntry]:
        """Программы Snap."""
        if not shutil.which("snap"):
            return []
        try:
            completed = subprocess.run(
                ["snap", "list"], capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []

        result: list[AppEntry] = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0].strip()
            if name:
                result.append(AppEntry(name=name, command=name, source="snap"))
        return result

    # --- Поиск ------------------------------------------------------------

    def find(self, query: str, limit: int = 8) -> list[AppEntry]:
        """Найти программы по названию, сказанному голосом.

        Учитывает транслитерацию и обиходные названия, поэтому «стим»
        находит Steam, «файрфокс» — Firefox.
        """
        needle_variants = _variants(query)
        if not needle_variants:
            return []

        entries = self.entries()
        if not entries:
            return []

        scored: list[tuple[float, AppEntry]] = []

        for entry in entries:
            name = self._normalize(entry.name)
            command_name = self._normalize(Path(entry.command.split()[0]).name)
            # Идентификатор приложения из ярлыка: com.microsoft.VSCode.
            id_tokens = []
            for keyword in entry.keywords:
                id_tokens.extend(self._normalize(keyword).split())
            name_tokens = name.split()
            command_tokens = command_name.split()

            best = 0.0

            for needle in needle_variants:
                needle_tokens = needle.split()
                if not needle_tokens:
                    continue

                # --- Уровень 1: название программы ---
                if needle == name:
                    best = max(best, 100.0)
                elif name.startswith(needle):
                    best = max(best, 94.0)
                elif len(needle) >= 4 and needle in name:
                    best = max(best, 88.0)

                # --- Уровень 2: имя исполняемого файла ---
                if needle == command_name:
                    best = max(best, 90.0)
                elif command_name.startswith(needle):
                    best = max(best, 84.0)
                elif len(needle) >= 5 and needle in command_name:
                    best = max(best, 78.0)

                # --- Уровень 3: отдельные слова названия ---
                for token in needle_tokens:
                    if len(token) < 4:
                        continue
                    for candidate in name_tokens:
                        if len(candidate) < 4:
                            continue
                        if token == candidate:
                            best = max(best, 86.0)
                        elif candidate.startswith(token) or token.startswith(candidate):
                            best = max(best, 80.0)
                        elif token_similarity(token, candidate, 1) >= 0.85:
                            best = max(best, 74.0)

                # --- Уровень 4: идентификатор приложения в ярлыке ---
                for token in needle_tokens:
                    if len(token) < 4:
                        continue
                    if token in id_tokens:
                        best = max(best, 76.0)

                # --- Уровень 5: нечёткое сравнение целиком ---
                if best < 70.0:
                    similarity = phrase_similarity(needle_tokens, name_tokens, 1)
                    if similarity >= 0.75:
                        best = max(best, 56.0 + 18.0 * similarity)

            if best <= 0:
                continue

            # Игры из ярлыков Steam запускаются через steam — они не должны
            # перебивать саму программу Steam по имени команды.
            if entry.command.startswith("steam steam://") or "rungameid" in entry.command:
                best -= 12.0

            # Настоящий ярлык приложения предпочтительнее служебной утилиты.
            if entry.source == "desktop":
                best += 3.0

            if best > 0:
                scored.append((best, entry))

        # Сортировка: сначала оценка, затем короткое название
        # (обычно это сама программа, а не её вспомогательная утилита).
        scored.sort(key=lambda pair: (-pair[0], len(pair[1].name), pair[1].name.lower()))
        return [entry for _, entry in scored[:limit]]

    @staticmethod
    def _normalize(text: str) -> str:
        from luxvoice.core.store import normalize_phrase
        return normalize_phrase(text)

    def best_match(self, query: str, threshold: float = 72.0) -> AppEntry | None:
        """Одна наиболее вероятная программа.

        Возвращает результат только при достаточной уверенности: лучше
        честно не найти программу, чем запустить не ту.
        """
        needle_variants = _variants(query)
        if not needle_variants:
            return None

        found = self.find(query, limit=5)
        if not found:
            return None

        # Явное совпадение с первым результатом — доверяем безусловно.
        top = found[0]
        name = self._normalize(top.name)
        command_name = self._normalize(Path(top.command.split()[0]).name)
        for needle in needle_variants:
            if needle == name or needle == command_name:
                return top
            if name.startswith(needle) or command_name.startswith(needle):
                return top
            if len(needle) >= 5 and (needle in name or needle in command_name):
                return top

        # Совпадение по целому слову названия.
        for needle in needle_variants:
            for token in needle.split():
                if len(token) >= 4 and token in name.split():
                    return top

        # Совпадение с идентификатором приложения из ярлыка:
        # «vscode» находит «com.microsoft.VSCode».
        for needle in needle_variants:
            for token in needle.split():
                if len(token) < 4:
                    continue
                for keyword in top.keywords:
                    for part in self._normalize(keyword).split():
                        if token == part or part.startswith(token):
                            return top

        # Нечёткое совпадение — только при заметном отрыве от второго места.
        similarity = max(
            (phrase_similarity(needle.split(), name.split(), 1)
             for needle in needle_variants),
            default=0.0,
        )
        if similarity >= threshold / 100:
            if len(found) > 1:
                # Проверяем, что лучший вариант не «размазан» с соседним.
                second = self._normalize(found[1].name)
                second_similarity = max(
                    (phrase_similarity(needle.split(), second.split(), 1)
                     for needle in needle_variants),
                    default=0.0,
                )
                if similarity - second_similarity < 0.1:
                    log.debug("Программа не определена однозначно: %r", query)
                    return None
            return top

        return None

    # --- Запуск -----------------------------------------------------------

    def launch(self, command: str, detached: bool = True,
               cwd: str | None = None) -> tuple[bool, str]:
        """Запустить программу по команде или пути."""
        if not command.strip():
            return False, "пустая команда"

        parts, error = self._split_command(command)
        if error:
            return False, error

        executable = parts[0]
        # Проверяем существование, если это путь.
        if "/" in executable:
            path = Path(executable).expanduser()
            if not path.exists():
                return False, f"файл не найден: {path}"
            parts[0] = str(path)
            if not os.access(parts[0], os.X_OK):
                return False, f"файл не исполняемый: {parts[0]}"

        try:
            subprocess.Popen(
                parts,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=detached,
                cwd=cwd,
            )
        except FileNotFoundError:
            return False, f"программа не найдена: {parts[0]}"
        except PermissionError:
            return False, f"нет прав на запуск: {parts[0]}"
        except OSError as exc:
            return False, f"ошибка запуска: {exc}"
        return True, ""

    @staticmethod
    def _split_command(command: str) -> tuple[list[str], str]:
        """Разобрать командную строку с учётом кавычек."""
        import shlex
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return [], f"не удалось разобрать команду: {exc}"
        if not parts:
            return [], "пустая команда"
        return parts, ""

    def open_file(self, path: str, opener: str = "xdg-open") -> tuple[bool, str]:
        """Открыть файл программой по умолчанию."""
        target = Path(path).expanduser()
        if not target.exists():
            return False, f"файл не найден: {target}"
        return self.launch(f"{opener} {shlex_quote(str(target))}")

    def open_url(self, url: str, browser: str = "") -> tuple[bool, str]:
        """Открыть адрес в браузере."""
        if not url:
            return False, "пустой адрес"
        if not url.startswith(("http://", "https://", "ftp://", "file://")):
            url = "https://" + url

        if browser:
            return self.launch(f"{browser} {shlex_quote(url)}")
        return self.launch(f"xdg-open {shlex_quote(url)}")

    def open_in_terminal(self, command: str) -> tuple[bool, str]:
        """Открыть команду в эмуляторе терминала."""
        terminals = (
            ("konsole", ["konsole", "-e"]),
            ("gnome-terminal", ["gnome-terminal", "--"]),
            ("alacritty", ["alacritty", "-e"]),
            ("kitty", ["kitty"]),
            ("xterm", ["xterm", "-e"]),
        )
        for name, prefix in terminals:
            if shutil.which(name):
                return self.launch(" ".join([*prefix, command]), detached=True)
        return False, "эмулятор терминала не найден"

    def is_running(self, name: str) -> bool:
        """Работает ли программа с таким именем."""
        needle = Path(name).name.lower()
        if not needle:
            return False
        for process in self._process_names():
            if needle in process:
                return True
        return False

    @staticmethod
    def _process_names() -> list[str]:
        names: list[str] = []
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    names.append(Path(f"/proc/{entry}/comm").read_text(
                        encoding="utf-8").strip().lower())
                except OSError:
                    continue
        except OSError:
            pass
        return names


def shlex_quote(text: str) -> str:
    """Заключить строку в кавычки для командной строки."""
    import shlex
    return shlex.quote(text)


# --- Открытие по фразам -----------------------------------------------------

def guess_target(phrase: str, locator: AppLocator, settings=None,
                 extra_dirs: list[str] | None = None) -> tuple[str, str]:
    """Понять, что именно просят открыть.

    Возвращает (вид, значение): вид — url, app, file или unknown.
    Сначала проверяем, не адрес ли это, затем — программу, затем файл.
    """
    import re as _re
    from pathlib import Path as _Path

    text = phrase.strip().strip("\"'")
    if not text:
        return "unknown", ""

    # 1. Адрес или домен.
    if _re.match(r"^(https?://|www\.)", text, _re.IGNORECASE):
        return "url", text

    # Известные сервисы без домена.
    known_sites = {
        "ютуб": "https://youtube.com", "youtube": "https://youtube.com",
        "вконтакте": "https://vk.com", "вк": "https://vk.com",
        "телеграм": "https://web.telegram.org", "telegram": "https://web.telegram.org",
        "почта": "https://mail.google.com", "gmail": "https://mail.google.com",
        "гугл": "https://google.com", "google": "https://google.com",
        "яндекс": "https://yandex.ru", "github": "https://github.com",
        "кинопоиск": "https://kinopoisk.ru", "википедия": "https://ru.wikipedia.org",
        "steam": "https://store.steampowered.com", "twitch": "https://twitch.tv",
        "дискорд": "https://discord.com/app", "spotify": "https://open.spotify.com",
    }
    from luxvoice.core.store import normalize_phrase
    normalized = normalize_phrase(text)
    if normalized in known_sites:
        return "url", known_sites[normalized]

    # Домен вида "example.com".
    if _re.match(r"^[\w-]+\.[a-z]{2,}(/\S*)?$", text, _re.IGNORECASE):
        return "url", text

    # 2. Программа.
    app = locator.best_match(text)
    if app is not None:
        return "app", app.command

    # 3. Файл или папка.
    candidate = _Path(text).expanduser()
    if candidate.exists():
        return "file", str(candidate)

    # Поиск файла по имени в личных папках.
    if extra_dirs:
        found = find_file(text, extra_dirs)
        if found:
            return "file", found

    return "unknown", ""


def find_file(name: str, directories: list[str], max_depth: int = 4,
              limit: int = 1) -> str:
    """Найти файл или папку по названию в указанных каталогах."""
    from luxvoice.core.store import normalize_phrase
    needle = normalize_phrase(name)
    if not needle:
        return ""

    results: list[str] = []
    for item in directories:
        for chunk in str(item).replace(";", ":").split(":"):
            chunk = chunk.strip()
            if not chunk:
                continue
            root = Path(chunk).expanduser()
            if not root.exists() or not root.is_dir():
                continue
            try:
                for path in _walk_limited(root, max_depth):
                    if normalize_phrase(path.stem) == needle:
                        results.append(str(path))
                        if len(results) >= limit:
                            return results[0]
                    elif needle in normalize_phrase(path.name) and len(results) < limit:
                        results.append(str(path))
            except OSError:
                continue
    return results[0] if results else ""


def _walk_limited(root: Path, max_depth: int):
    """Обход каталога с ограничением глубины и пропуском служебных папок."""
    skip = {".git", "node_modules", ".cache", "__pycache__", ".venv", "venv",
            ".local", "snap", "flatpak", ".steam", "SteamLibrary", ".mozilla",
            "site-packages", ".gradle", ".cargo", ".rustup"}
    root_depth = len(root.parts)
    try:
        for current, dirs, files in os.walk(root, topdown=True):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            if depth >= max_depth:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip]
            for name in files:
                yield current_path / name
            for name in dirs:
                yield current_path / name
    except OSError:
        return


# --- Единственный экземпляр -------------------------------------------------

_locator: AppLocator | None = None


def get_locator() -> AppLocator:
    global _locator
    if _locator is None:
        _locator = AppLocator()
    return _locator