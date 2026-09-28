"""Ввод с клавиатуры и мыши на уровне ядра через /dev/uinput.

Почему так: в Wayland программы не могут синтезировать ввод обычными
средствами — это осознанное ограничение протокола. Единственный надёжный
путь, работающий в любом приложении (включая игры и полноэкранные окна) —
создать виртуальное устройство ввода через uinput и эмулировать нажатия
на уровне драйвера. Система видит его как обычную клавиатуру и мышь.

Требования: доступ к /dev/uinput (обычно группа input или правило udev).
Отсутствие доступа не роняет программу: кнопки и команды, которым нужен
ввод, просто сообщат о недоступности, а остальное продолжит работать.

Реализация работает и через python-evdev (если установлен), и напрямую
через ioctl — второй путь не требует лишних зависимостей.
"""

from __future__ import annotations

import ctypes
import fcntl
import logging
import os
import re
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# --- Константы uinput -------------------------------------------------------

UINPUT_MAX_NAME_SIZE = 80
UINPUT_IOCTL_BASE = ord("U")

# Типы событий.
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03

# Синхронизация.
SYN_REPORT = 0
REL_X, REL_Y = 0x00, 0x01
REL_WHEEL, REL_HWHEEL = 0x08, 0x06

# Кнопки мыши.
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_SIDE, BTN_EXTRA = 0x113, 0x114

# --- ioctl ------------------------------------------------------------------

def _ioc(direction: int, type_: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (type_ << 8) | nr


_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2

UI_SET_EVBIT = _ioc(_IOC_WRITE, UINPUT_IOCTL_BASE, 100, 4)
UI_SET_KEYBIT = _ioc(_IOC_WRITE, UINPUT_IOCTL_BASE, 101, 4)
UI_SET_RELBIT = _ioc(_IOC_WRITE, UINPUT_IOCTL_BASE, 102, 4)
UI_SET_ABSBIT = _ioc(_IOC_WRITE, UINPUT_IOCTL_BASE, 103, 4)
UI_DEV_CREATE = _ioc(_IOC_NONE, UINPUT_IOCTL_BASE, 1, 0)
UI_DEV_DESTROY = _ioc(_IOC_NONE, UINPUT_IOCTL_BASE, 2, 0)
UI_SET_PHYS = _ioc(_IOC_WRITE, UINPUT_IOCTL_BASE, 108, 4)
UI_SET_NAME = _ioc(_IOC_WRITE, UINPUT_IOCTL_BASE, 107, 4)


class InputEvent(ctypes.Structure):
    """struct input_event — 24 байта на 64-битной системе."""

    _fields_ = [
        ("time_sec", ctypes.c_long),
        ("time_usec", ctypes.c_long),
        ("type", ctypes.c_uint16),
        ("code", ctypes.c_uint16),
        ("value", ctypes.c_int32),
    ]


class UInputSetup(ctypes.Structure):
    """struct uinput_setup."""

    _fields_ = [
        ("id_bustype", ctypes.c_uint16),
        ("id_vendor", ctypes.c_uint16),
        ("id_product", ctypes.c_uint16),
        ("id_version", ctypes.c_uint16),
        ("name", ctypes.c_char * UINPUT_MAX_NAME_SIZE),
        ("ff_effects_max", ctypes.c_uint32),
    ]


# --- Таблица клавиш ---------------------------------------------------------

# Соответствие «имя клавиши → код». Основа — Linux input event codes.
KEYS: dict[str, int] = {
    # Буквы
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    # Цифры верхнего ряда
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11,
    # Русские буквы — те же физические клавиши
    "й": 30, "ц": 48, "у": 46, "к": 32, "е": 18, "н": 33, "г": 34, "ш": 35,
    "щ": 23, "з": 36, "х": 37, "ъ": 38, "ф": 50, "ы": 49, "в": 24, "а": 25,
    "п": 16, "р": 19, "о": 31, "л": 20, "д": 22, "ж": 47, "э": 17, "я": 45,
    "ч": 21, "с": 44, "б": 48, "ю": 46, "ё": 41,
    # Управление
    "esc": 1, "escape": 1, "backspace": 14, "tab": 15, "enter": 28, "return": 28,
    "space": 57, "пробел": 57, "capslock": 58, "numlock": 69, "scrolllock": 70,
    "delete": 111, "del": 111, "insert": 110, "ins": 110, "home": 102,
    "end": 107, "pageup": 104, "pagedown": 109, "pause": 119, "print": 99,
    "sysrq": 99, "printscreen": 99, "prtsc": 99,
    # Стрелки
    "left": 105, "right": 106, "up": 103, "down": 108,
    "влево": 105, "вправо": 106, "вверх": 103, "вниз": 108,
    # Модификаторы
    "leftctrl": 29, "rightctrl": 97, "ctrl": 29, "control": 29,
    "leftshift": 42, "rightshift": 54, "shift": 42,
    "leftalt": 56, "rightalt": 100, "alt": 56, "altgr": 100,
    "leftmeta": 125, "rightmeta": 126, "meta": 125, "super": 125, "win": 125,
    "cmd": 125, "logo": 125,
    # Функциональные
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65,
    "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88, "f13": 183,
    "f14": 184, "f15": 185, "f16": 186, "f17": 187, "f18": 188, "f19": 189,
    "f20": 190, "f21": 191, "f22": 192, "f23": 193, "f24": 194,
    # Медиа и мультимедиа
    "mute": 113, "volumeup": 115, "volumedown": 114,
    "nextsong": 163, "previoussong": 165, "playpause": 164, "stopcd": 166,
    "next": 163, "previous": 165, "play": 164,
    "brightnessup": 225, "brightnessdown": 224,
    "calculator": 148, "mail": 155, "search": 217,
    "monbrightnessup": 225, "monbrightnessdown": 224,
    # Знаки и служебные
    "minus": 12, "equal": 13, "leftbrace": 26, "rightbrace": 27,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "backslash": 43,
    "comma": 51, "dot": 52, "slash": 53, "kpasterisk": 55, "kpminus": 74,
    "kpplus": 78, "kpdot": 83, "kpenter": 96, "kp0": 82, "kp1": 79, "kp2": 80,
    "kp3": 81, "kp4": 75, "kp5": 76, "kp6": 77, "kp7": 71, "kp8": 72, "kp9": 73,
    "кплюс": 78, "кпминус": 74,
}

# Клавиши-модификаторы: при вводе текста не отпускаем до конца.
MODIFIERS = {29, 97, 42, 54, 56, 100, 125, 126}

# --- Раскладки для ввода текста ---------------------------------------------

# Символ → (код клавиши, нужен ли Shift). Латиница.
_SHIFT_MAP_EN: dict[str, tuple[str, bool]] = {
    "!": ("1", True), "@": ("2", True), "#": ("3", True), "$": ("4", True),
    "%": ("5", True), "^": ("6", True), "&": ("7", True), "*": ("8", True),
    "(": ("9", True), ")": ("0", True), "_": ("minus", True), "+": ("equal", True),
    "{": ("leftbrace", True), "}": ("rightbrace", True), ":": ("semicolon", True),
    '"': ("apostrophe", True), "~": ("grave", True), "|": ("backslash", True),
    "<": ("comma", True), ">": ("dot", True), "?": ("slash", True),
}

# Русская раскладка: символ → (латинская клавиша, shift).
_SHIFT_MAP_RU: dict[str, tuple[str, bool]] = {
    "!": ("1", True), '"': ("2", True), "№": ("3", True), ";": ("4", True),
    "%": ("5", True), ":": ("6", True), "?": ("7", True), "*": ("8", True),
    "(": ("9", True), ")": ("0", True), "_": ("minus", True), "+": ("equal", True),
    "/": ("backslash", True), ",": ("slash", True), ".": ("slash", True),
    "«": ("leftbrace", True), "»": ("rightbrace", True),
}


@dataclass
class InputCapabilities:
    """Что доступно на этой системе."""

    uinput: bool = False
    reason: str = ""
    keyboard: bool = False
    mouse: bool = False


class UInputDevice:
    """Виртуальное устройство ввода: клавиатура и мышь в одном."""

    def __init__(self, name: str = "LuxVoice Virtual Input") -> None:
        self._name = name
        self._fd: int | None = None
        self._lock = threading.RLock()
        self._created = False
        self._error = ""

    # --- Жизненный цикл ---------------------------------------------------

    @staticmethod
    def available() -> InputCapabilities:
        """Проверить, можно ли создавать виртуальные устройства."""
        path = "/dev/uinput"
        if not os.path.exists(path):
            return InputCapabilities(False, f"{path} не найден")
        if not os.access(path, os.W_OK):
            return InputCapabilities(
                False,
                "нет доступа к /dev/uinput — добавьте пользователя в группу input "
                "или установите правило udev",
            )
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError as exc:
            return InputCapabilities(False, f"не удалось открыть {path}: {exc}")
        return InputCapabilities(True, "", keyboard=True, mouse=True)

    def create(self) -> bool:
        """Создать устройство. Возвращает True при успехе."""
        with self._lock:
            if self._created:
                return True
            try:
                self._fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
            except OSError as exc:
                self._error = f"не удалось открыть /dev/uinput: {exc}"
                log.error(self._error)
                return False

            try:
                # Разрешаем нужные типы событий.
                fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
                fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_SYN)
                fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_REL)

                # Все клавиши из таблицы.
                for code in set(KEYS.values()):
                    fcntl.ioctl(self._fd, UI_SET_KEYBIT, code)
                # Кнопки мыши.
                for button in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA):
                    fcntl.ioctl(self._fd, UI_SET_KEYBIT, button)
                # Относительные оси: движение и прокрутка.
                for axis in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
                    fcntl.ioctl(self._fd, UI_SET_RELBIT, axis)

                # Описание устройства.
                setup = UInputSetup()
                setup.id_bustype = 0x03  # BUS_USB
                setup.id_vendor = 0x1234
                setup.id_product = 0x5678
                setup.id_version = 1
                setup.name = self._name.encode()[:UINPUT_MAX_NAME_SIZE - 1]
                setup.ff_effects_max = 0

                # Современный интерфейс настройки; если ядро старое —
                # пробуем устаревший через запись структуры.
                try:
                    fcntl.ioctl(self._fd, _ioc(_IOC_WRITE, UINPUT_IOCTL_BASE, 3,
                                               ctypes.sizeof(UInputSetup)), setup)
                except OSError:
                    log.debug("uinput: современная настройка недоступна, используем запись")

                fcntl.ioctl(self._fd, UI_DEV_CREATE)
                self._created = True
                # Даём ядру время создать устройство в /dev/input.
                time.sleep(0.15)
                log.info("Виртуальное устройство ввода создано: %s", self._name)
                return True

            except OSError as exc:
                self._error = f"не удалось создать устройство ввода: {exc}"
                log.error(self._error)
                self.close()
                return False

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                try:
                    if self._created:
                        fcntl.ioctl(self._fd, UI_DEV_DESTROY)
                except OSError:
                    pass
                try:
                    os.close(self._fd)
                except OSError:
                    pass
            self._fd = None
            self._created = False

    @property
    def ready(self) -> bool:
        return self._created and self._fd is not None

    @property
    def error(self) -> str:
        return self._error

    def __enter__(self) -> "UInputDevice":
        self.create()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- Отправка событий -------------------------------------------------

    def _emit(self, type_: int, code: int, value: int) -> None:
        if self._fd is None:
            raise RuntimeError("устройство ввода не создано")
        event = InputEvent()
        event.time_sec = 0
        event.time_usec = 0
        event.type = type_
        event.code = code
        event.value = value
        os.write(self._fd, bytes(event))

    def _sync(self) -> None:
        self._emit(EV_SYN, SYN_REPORT, 0)

    # --- Клавиатура -------------------------------------------------------

    def key_down(self, code: int) -> None:
        with self._lock:
            self._emit(EV_KEY, code, 1)
            self._sync()

    def key_up(self, code: int) -> None:
        with self._lock:
            self._emit(EV_KEY, code, 0)
            self._sync()

    def tap(self, code: int, hold: float = 0.01) -> None:
        """Одиночное нажатие."""
        with self._lock:
            self._emit(EV_KEY, code, 1)
            self._sync()
            if hold > 0:
                time.sleep(hold)
            self._emit(EV_KEY, code, 0)
            self._sync()

    def combo(self, codes: list[int], hold: float = 0.02) -> None:
        """Нажатие сочетания: все клавиши вниз, потом вверх в обратном порядке."""
        with self._lock:
            pressed: list[int] = []
            try:
                for code in codes:
                    self._emit(EV_KEY, code, 1)
                    self._sync()
                    pressed.append(code)
                    time.sleep(0.005)
                if hold > 0:
                    time.sleep(hold)
            finally:
                for code in reversed(pressed):
                    self._emit(EV_KEY, code, 0)
                    self._sync()
                    time.sleep(0.005)

    def type_text(self, text: str, layout: str = "auto", delay: float = 0.012) -> int:
        """Напечатать текст посимвольно.

        Для символов, которых нет на клавиатуре напрямую, используется
        временная подмена через Ctrl+Shift+U (ввод Unicode) — работает
        в большинстве приложений Linux.
        """
        typed = 0
        for char in text:
            code_and_shift = self._char_to_key(char, layout)
            if code_and_shift is not None:
                code, shift = code_and_shift
                if shift:
                    self.combo([KEYS["leftshift"], code], hold=0.005)
                else:
                    self.tap(code, hold=0.005)
                typed += 1
            else:
                # Неизвестный символ — пробуем Unicode-ввод.
                if self._type_unicode(char):
                    typed += 1
                else:
                    log.debug("Символ %r пропущен: нет способа ввести", char)
            if delay > 0:
                time.sleep(delay)
        return typed

    def _type_unicode(self, char: str) -> bool:
        """Ввод символа через Ctrl+Shift+U + код + Enter (GTK/Qt поддерживают)."""
        try:
            codepoint = ord(char)
        except TypeError:
            return False
        if codepoint == 0:
            return False

        hex_digits = f"{codepoint:x}"
        digit_keys = {"0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
                      "6": "6", "7": "7", "8": "8", "9": "9", "a": "a", "b": "b",
                      "c": "c", "d": "d", "e": "e", "f": "f"}

        with self._lock:
            # Ctrl+Shift+U
            self._emit(EV_KEY, KEYS["leftctrl"], 1)
            self._emit(EV_KEY, KEYS["leftshift"], 1)
            self._emit(EV_KEY, KEYS["u"], 1)
            self._sync()
            time.sleep(0.01)
            self._emit(EV_KEY, KEYS["u"], 0)
            self._emit(EV_KEY, KEYS["leftshift"], 0)
            self._emit(EV_KEY, KEYS["leftctrl"], 0)
            self._sync()
            time.sleep(0.02)

            for digit in hex_digits:
                key = digit_keys.get(digit)
                if key is None:
                    continue
                self._emit(EV_KEY, KEYS[key], 1)
                self._emit(EV_KEY, KEYS[key], 0)
                self._sync()
                time.sleep(0.01)

            self._emit(EV_KEY, KEYS["enter"], 1)
            self._emit(EV_KEY, KEYS["enter"], 0)
            self._sync()
            time.sleep(0.01)
        return True

    def _char_to_key(self, char: str, layout: str) -> tuple[int, bool] | None:
        """Подобрать клавишу и необходимость Shift для символа."""
        if char == " ":
            return KEYS["space"], False

        lower = char.lower()
        upper = char.isupper()

        # Латиница и цифры.
        if lower in KEYS and (lower.isascii() or lower.isalpha()):
            code = KEYS[lower]
            # Русские буквы набираем через раскладку, если она активна.
            if not lower.isascii() and layout == "en":
                return None
            return code, upper

        # Знаки.
        table = _SHIFT_MAP_RU if layout == "ru" else _SHIFT_MAP_EN
        entry = table.get(char)
        if entry is not None:
            key_name, shift = entry
            return KEYS.get(key_name), shift

        # Если раскладка неизвестна — пробуем обе таблицы.
        if layout == "auto":
            for table in (_SHIFT_MAP_EN, _SHIFT_MAP_RU):
                entry = table.get(char)
                if entry is not None:
                    key_name, shift = entry
                    return KEYS.get(key_name), shift

        return None

    # --- Мышь -------------------------------------------------------------

    def move(self, dx: int, dy: int) -> None:
        """Сдвинуть курсор относительно текущего положения."""
        with self._lock:
            if dx:
                self._emit(EV_REL, REL_X, int(dx))
            if dy:
                self._emit(EV_REL, REL_Y, int(dy))
            self._sync()

    def move_smooth(self, dx: int, dy: int, steps: int = 12, duration: float = 0.08) -> None:
        """Плавное перемещение — приложения читают движение корректнее."""
        if steps <= 1:
            self.move(dx, dy)
            return
        step_delay = duration / steps
        remaining_x, remaining_y = int(dx), int(dy)
        for index in range(steps):
            step_x = remaining_x // (steps - index)
            step_y = remaining_y // (steps - index)
            remaining_x -= step_x
            remaining_y -= step_y
            if step_x or step_y:
                self.move(step_x, step_y)
            time.sleep(step_delay)

    def click(self, button: int = BTN_LEFT, double: bool = False,
              count: int = 1, hold: float = 0.03) -> None:
        """Клик мышью."""
        presses = 2 if double else max(1, count)
        for index in range(presses):
            with self._lock:
                self._emit(EV_KEY, button, 1)
                self._sync()
                time.sleep(hold)
                self._emit(EV_KEY, button, 0)
                self._sync()
            if index < presses - 1:
                time.sleep(0.06 if double else 0.08)

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        """Прокрутка: положительное — вверх/вправо."""
        axis = REL_HWHEEL if horizontal else REL_WHEEL
        with self._lock:
            step = 1 if amount > 0 else -1
            for _ in range(abs(int(amount))):
                self._emit(EV_REL, axis, step)
                self._sync()
                time.sleep(0.012)

    def drag(self, dx: int, dy: int, button: int = BTN_LEFT,
             duration: float = 0.3) -> None:
        """Перетаскивание: зажать, сдвинуть, отпустить."""
        with self._lock:
            self._emit(EV_KEY, button, 1)
            self._sync()
        time.sleep(0.05)
        self.move_smooth(dx, dy, steps=15, duration=duration)
        time.sleep(0.05)
        with self._lock:
            self._emit(EV_KEY, button, 0)
            self._sync()

    def hold(self, button: int, seconds: float) -> None:
        with self._lock:
            self._emit(EV_KEY, button, 1)
            self._sync()
        time.sleep(max(0.01, seconds))
        with self._lock:
            self._emit(EV_KEY, button, 0)
            self._sync()


# --- Разбор сочетаний клавиш ------------------------------------------------

# Синонимы для удобства записи: «ctrl», «ctl», «управление» и т. п.
_KEY_ALIASES: dict[str, str] = {
    "ctl": "ctrl", "control": "ctrl", "управление": "ctrl", "ктрл": "ctrl",
    "контрол": "ctrl",
    "cmd": "meta", "super": "meta", "win": "meta", "windows": "meta",
    "logo": "meta", "os": "meta", "пуск": "meta", "вин": "meta",
    "alt": "alt", "альт": "alt", "shift": "shift", "шифт": "shift",
    "enter": "enter", "return": "enter", "энтер": "enter", "ввод": "enter",
    "esc": "escape", "escape": "escape", "эскейп": "escape",
    "del": "delete", "delete": "delete", "ins": "insert", "insert": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "next": "pagedown",
    "пробел": "space", "space": "space", "spacebar": "space",
    "вверх": "up", "вниз": "down", "влево": "left", "вправо": "right",
    "тире": "minus", "минус": "minus", "плюс": "equal",
    "prtsc": "printscreen", "print": "printscreen", "скрин": "printscreen",
    "tab": "tab", "таб": "tab", "home": "home", "дом": "home",
    "end": "end", "конец": "end", "backspace": "backspace",
}


def parse_keys(spec: str) -> list[int]:
    """Разобрать запись сочетания в коды клавиш.

    Понимает «ctrl+shift+esc», «Win+D», «alt+F4», «Ctrl+Alt+T»,
    русские названия клавиш и разделители «+», «-», пробел.
    Неизвестные части игнорируются, но попадают в журнал.
    """
    if not spec:
        return []

    # Разделители: плюс, дефис между клавишами, запятая, пробел вокруг плюса.
    text = spec.strip().replace("＋", "+").replace(" +", "+").replace("+ ", "+")
    parts: list[str] = []
    for chunk in text.replace(",", "+").split("+"):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)

    # Дефис как разделитель: «ctrl-alt-t». Разбираем только если все
    # части — известные клавиши (иначе это имя вроде «page-up»).
    if len(parts) == 1 and "-" in parts[0]:
        candidate = [p.strip() for p in parts[0].split("-") if p.strip()]
        if len(candidate) > 1 and all(
                _KEY_ALIASES.get(p.lower(), p.lower()) in KEYS for p in candidate):
            parts = candidate

    codes: list[int] = []
    for part in parts:
        name = part.strip().lower().replace(" ", "")
        name = _KEY_ALIASES.get(name, name)

        if name in KEYS:
            codes.append(KEYS[name])
            continue

        # Несколько клавиш без разделителя: «ctrlshiftesc».
        if len(name) > 4:
            expanded = _split_concatenated(name)
            if expanded:
                codes.extend(expanded)
                continue

        log.warning("Неизвестная клавиша в сочетании: %r", part)

    return codes


def _split_concatenated(name: str) -> list[int]:
    """Разобрать слитную запись «ctrlaltt» на составляющие."""
    modifiers = ("ctrl", "alt", "shift", "meta")
    found: list[int] = []
    rest = name
    changed = True
    while changed:
        changed = False
        for modifier in modifiers:
            if rest.startswith(modifier):
                found.append(KEYS[modifier])
                rest = rest[len(modifier):]
                changed = True
                break
    if rest and rest in KEYS:
        found.append(KEYS[rest])
        return found
    if rest and not found:
        return []
    if rest:
        # Хвост не распознан — сочетание считаем сомнительным.
        return []
    return found


def key_name(code: int) -> str:
    """Обратное преобразование: код → читаемое имя клавиши."""
    for name, value in KEYS.items():
        if value == code:
            # Предпочитаем короткие осмысленные имена.
            if len(name) <= 12:
                return name
    return f"код {code}"


def describe_keys(spec: str) -> str:
    """Человекочитаемое описание сочетания для интерфейса."""
    codes = parse_keys(spec)
    if not codes:
        return spec or "не задано"
    return " + ".join(key_name(code).capitalize() for code in codes)


# --- Общая точка доступа ----------------------------------------------------

_device: UInputDevice | None = None
_device_lock = threading.Lock()


def get_device() -> UInputDevice:
    """Общее виртуальное устройство ввода для всего приложения."""
    global _device
    with _device_lock:
        if _device is None:
            _device = UInputDevice()
            _device.create()
        elif not _device.ready:
            _device.create()
        return _device


def release_device() -> None:
    global _device
    with _device_lock:
        if _device is not None:
            _device.close()
            _device = None

# --- Функциональные обёртки для остальных модулей ---------------------------

def press_key(spec: str) -> bool:
    """Нажать клавишу или сочетание, записанное строкой."""
    codes = parse_keys(spec)
    if not codes:
        log.warning("Не удалось разобрать сочетание: %r", spec)
        return False
    device = get_device()
    if not device.ready:
        log.error("Ввод недоступен: %s", device.error or "устройство не создано")
        return False
    if len(codes) == 1:
        device.tap(codes[0])
    else:
        device.combo(codes)
    return True


# Синоним для читаемости в вызывающем коде.
combo_by_spec = press_key


def type_string(text: str, layout: str = "auto") -> int:
    """Напечатать текст."""
    device = get_device()
    if not device.ready:
        log.error("Ввод недоступен: %s", device.error or "устройство не создано")
        return 0
    return device.type_text(text, layout=layout)


def click_mouse(button: str = "left", double: bool = False, count: int = 1) -> bool:
    """Клик мышью: left, right, middle, side, extra."""
    device = get_device()
    if not device.ready:
        return False
    mapping = {
        "left": BTN_LEFT, "левая": BTN_LEFT, "лкм": BTN_LEFT,
        "right": BTN_RIGHT, "правая": BTN_RIGHT, "пкм": BTN_RIGHT,
        "middle": BTN_MIDDLE, "средняя": BTN_MIDDLE,
        "side": BTN_SIDE, "extra": BTN_EXTRA,
    }
    code = mapping.get(button.strip().lower(), BTN_LEFT)
    device.click(code, double=double, count=count)
    return True


def move_by(dx: int, dy: int, smooth: bool = True) -> bool:
    """Сдвинуть курсор на заданное расстояние."""
    device = get_device()
    if not device.ready:
        return False
    if smooth:
        device.move_smooth(dx, dy)
    else:
        device.move(dx, dy)
    return True


def scroll_by(amount: int, horizontal: bool = False) -> bool:
    """Прокрутить колесо."""
    device = get_device()
    if not device.ready:
        return False
    device.scroll(amount, horizontal=horizontal)
    return True


def drag_by(dx: int, dy: int, button: str = "left") -> bool:
    """Перетащить мышью."""
    device = get_device()
    if not device.ready:
        return False
    code = BTN_RIGHT if button.strip().lower() in ("right", "правая", "пкм") else BTN_LEFT
    device.drag(dx, dy, button=code)
    return True


def hold_button(button: str, seconds: float) -> bool:
    """Зажать кнопку мыши на время."""
    device = get_device()
    if not device.ready:
        return False
    code = BTN_RIGHT if button.strip().lower() in ("right", "правая", "пкм") else BTN_LEFT
    device.hold(code, seconds)
    return True


def input_available() -> tuple[bool, str]:
    """Доступен ли ввод: (можно, причина отказа)."""
    caps = UInputDevice.available()
    if caps.uinput:
        return True, ""
    return False, caps.reason


def screen_size() -> tuple[int, int]:
    """Размер основного экрана — для координат и аватара."""
    # В Wayland спрашиваем у KWin через D-Bus, при неудаче — xrandr.
    for cmd in (["xrandr", "--current"], ["xdpyinfo"]):
        if not has_command_name(cmd[0]):
            continue
        code, out, _ = run_command(cmd)
        if code != 0:
            continue
        match = re.search(r"(\d+)x(\d+)\+0\+0", out)
        if match:
            return int(match.group(1)), int(match.group(2))
    # Значение по умолчанию — распространённое разрешение ноутбука.
    return 1920, 1080


def has_command_name(name: str) -> bool:
    import shutil as _shutil
    return _shutil.which(name) is not None


def run_command(cmd: list[str]) -> tuple[int, str, str]:
    import subprocess as _subprocess
    try:
        result = _subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return result.returncode, result.stdout or "", result.stderr or ""
    except Exception:  # noqa: BLE001
        return 1, "", ""


def _cursor_position() -> tuple[int, int]:
    """Текущее положение курсора. Работает только в X11-совместимом режиме."""
    # В Wayland позиция курсора недоступна приложениям. Для абсолютного
    # позиционирования используем перемещение от левого верхнего угла
    # через сброс курсора сочетанием, если это поддерживается.
    if os.environ.get("DISPLAY") and has_command_name("xdotool"):
        code, out, _ = run_command(["xdotool", "getmouselocation", "--shell"])
        if code == 0:
            x = re.search(r"X=(\d+)", out)
            y = re.search(r"Y=(\d+)", out)
            if x and y:
                return int(x.group(1)), int(y.group(1))
    return -1, -1


def move_to(x: int, y: int, smooth: bool = True) -> bool:
    """Переместить курсор в абсолютные координаты.

    В Wayland приложение не знает текущей позиции курсора, поэтому
    координаты отсчитываются от центра экрана: экран делится пополам,
    курсор сначала приводится к известной точке, затем сдвигается.
    В X11 используется точное позиционирование.
    """
    device = get_device()
    if not device.ready:
        return False

    # Точный путь для X11.
    current_x, current_y = _cursor_position()
    if current_x >= 0:
        device.move_smooth(x - current_x, y - current_y)
        return True

    # Wayland: возвращаемся в левый верхний угол экрана резким движением
    # влево-вверх с запасом, затем идём к цели. Запас гарантирует, что
    # курсор упрётся в край и координаты станут предсказуемыми.
    width, height = screen_size()
    device.move_smooth(-width * 2, -height * 2, steps=8, duration=0.05)
    time.sleep(0.03)
    device.move_smooth(max(0, x), max(0, y))
    return True


def cursor_known() -> bool:
    """Известна ли точная позиция курсора (для честного предупреждения)."""
    x, _ = _cursor_position()
    return x >= 0
