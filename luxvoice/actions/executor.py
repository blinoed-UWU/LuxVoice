"""Исполнение действий команды.

Здесь описано, что именно происходит при выполнении каждого шага.
Общие правила:

  * ни одно действие не роняет программу — ошибка возвращается как
    результат с понятным текстом;
  * опасные действия уважают настройки безопасности и не выполняются
    молча;
  * проверка разрешений делается до выполнения, а не после;
  * длительные операции можно прервать (стоп-флаг).
"""

from __future__ import annotations

import logging
import os
import random
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from luxvoice.actions import catalog
from luxvoice.core import paths
from luxvoice.core.model import Action

log = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Результат одного шага."""

    ok: bool = True
    message: str = ""
    output: str = ""          # текст для показа или озвучки
    skipped: bool = False     # шаг пропущен по условию
    control: str = ""         # управляющая команда: stop, repeat, skip_rest

    def __bool__(self) -> bool:
        return self.ok


# Что нельзя выполнять ни при каких настройках — защита от разрушения системы.
_HARD_BLOCKED_PATTERNS = (
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=/dev/zero of=/dev",
    ":(){:|:&};:", "chmod -R 777 /", "> /dev/sda", "mv / /dev/null",
)


class ActionExecutor:
    """Выполнение действий с учётом настроек безопасности."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._stop_event = threading.Event()
        self._variables: dict[str, Any] = {}
        self._context: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._last_action: Action | None = None

        # Обработчики для голосового взаимодействия.
        self._ask_handler = None
        self._speak_handler = None
        self._notify_handler = None
        self._command_runner = None

    # --- Внешние зависимости --------------------------------------------

    def set_settings(self, settings) -> None:
        self._settings = settings

    def on_ask(self, handler) -> None:
        """Обработчик вопроса с ожиданием ответа. handler(question, timeout) → str."""
        self._ask_handler = handler

    def on_speak(self, handler) -> None:
        """Обработчик озвучки."""
        self._speak_handler = handler

    def on_notify(self, handler) -> None:
        """Обработчик системных уведомлений."""
        self._notify_handler = handler

    def on_run_command(self, handler) -> None:
        """Обработчик запуска другой команды. handler(name) → bool."""
        self._command_runner = handler

    def stop(self) -> None:
        """Запросить остановку выполнения."""
        self._stop_event.set()

    def reset(self) -> None:
        """Сбросить состояние перед новой командой."""
        self._stop_event.clear()
        self._variables = {}
        self._last_action = None

    # --- Настройки безопасности -----------------------------------------

    def _flag(self, key: str, default: bool = True) -> bool:
        if self._settings is None:
            return default
        return self._settings.flag(key, default)

    def _number(self, key: str, default: float) -> float:
        if self._settings is None:
            return default
        return self._settings.number(key, default)

    def _text(self, key: str, default: str = "") -> str:
        if self._settings is None:
            return default
        return self._settings.text(key, default)

    def permission_error(self, action: Action) -> str:
        """Проверить разрешения до выполнения. Пусто — можно выполнять."""
        spec = catalog.get_spec(action.type)
        if spec is None:
            return f"неизвестное действие: {action.type}"

        if spec.needs_shell:
            if not self._flag("safety.allow_shell", True):
                return "команды консоли запрещены в настройках безопасности"
            if action.type == "run_shell" and not self._flag(
                    "safety.allow_powershell", True):
                return "выполнение сценариев запрещено в настройках"

        if action.type == "run_script" and not self._flag("safety.allow_scripts", False):
            return ("запуск сценариев выключен в настройках "
                    "(«Действия и безопасность» → «Разрешить запуск скриптов»)")

        if spec.needs_write and action.type != "delete_path":
            if not self._flag("safety.allow_file_write", True):
                return "запись файлов запрещена в настройках безопасности"

        if action.type in ("shutdown", "reboot", "sleep", "logout"):
            if not self._flag("safety.allow_shutdown", True):
                return "управление питанием запрещено в настройках безопасности"

        if spec.needs_input:
            from luxvoice.sysint.uinput import input_available
            if action.type.startswith("mouse") or action.type in (
                    "show_desktop", "minimize_all", "minimize_window",
                    "maximize_window", "switch_window", "next_window",
                    "previous_window", "switch_desktop"):
                if not self._flag("safety.allow_mouse", True):
                    return "управление мышью и окнами запрещено в настройках"
            if action.type in ("press_keys", "type_text", "type_enter",
                               "type_hotkey_text", "dictate", "clipboard_paste"):
                if not self._flag("safety.allow_keyboard", True):
                    return "нажатия клавиш запрещены в настройках"

            # Отдельно проверяем доступ к виртуальному вводу.
            available, reason = input_available()
            if not available:
                return (f"нет доступа к виртуальному вводу: {reason}. "
                        "Добавьте пользователя в группу input "
                        "(sudo usermod -aG input $USER) и войдите заново.")

        if action.type in ("close_window", "close_app"):
            if not self._flag("safety.allow_close_apps", True):
                return "закрытие программ запрещено в настройках"

        return ""

    # --- Выполнение ------------------------------------------------------

    def execute(self, action: Action, context: dict[str, Any] | None = None) -> ActionResult:
        """Выполнить одно действие."""
        if not action.enabled:
            return ActionResult(ok=True, skipped=True, message="шаг выключен")

        if self._stop_event.is_set():
            return ActionResult(ok=False, message="выполнение прервано",
                                control="stop")

        self._last_action = action
        self._context = context or {}

        # Проверка разрешений.
        error = self.permission_error(action)
        if error:
            log.warning("Действие %s не разрешено: %s", action.type, error)
            return ActionResult(ok=False, message=error)

        handler = getattr(self, f"_do_{action.type}", None)
        if handler is None:
            return ActionResult(ok=False, message=f"действие не реализовано: {action.type}")

        # Паузы вокруг шага.
        if action.delay_before > 0:
            self._sleep(action.delay_before)

        attempts = max(1, action.retries + 1)
        result = ActionResult(ok=False, message="не выполнено")

        for attempt in range(attempts):
            if self._stop_event.is_set():
                return ActionResult(ok=False, message="выполнение прервано",
                                    control="stop")
            try:
                result = handler(action)
            except Exception as exc:  # noqa: BLE001 — падать недопустимо
                log.exception("Сбой действия %s", action.type)
                result = ActionResult(ok=False, message=f"ошибка: {exc}")

            if result.ok or attempt == attempts - 1:
                break
            if action.retry_delay > 0:
                self._sleep(action.retry_delay)

        if action.delay_after > 0:
            self._sleep(action.delay_after)

        return result

    def _sleep(self, seconds: float) -> None:
        """Пауза с возможностью прерывания."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop_event.is_set():
                return
            time.sleep(min(0.05, max(0.005, deadline - time.time())))

    # --- Подстановка значений -------------------------------------------

    def _value(self, action: Action, key: str = "value", default: str = "") -> str:
        """Взять параметр действия и подставить переменные."""
        raw = action.params.get(key, default)
        if raw is None:
            raw = default
        text = str(raw)

        context = dict(self._context)
        context["variables"] = self._variables
        return catalog.substitute(text, context, self._settings)

    def _number_param(self, action: Action, key: str, default: float) -> float:
        """Числовой параметр с подстановкой и приведением."""
        raw = self._value(action, key, str(default))
        try:
            return float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return default

    def _int_param(self, action: Action, key: str, default: int) -> int:
        return int(round(self._number_param(action, key, default)))

    def _bool_param(self, action: Action, key: str, default: bool = False) -> bool:
        raw = action.params.get(key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "да", "on", "вкл")

    # ================= ЗАПУСК =================

    def _do_open_url(self, action: Action) -> ActionResult:
        from luxvoice.sysint.launcher import get_locator

        url = self._value(action, "url").strip()
        if not url:
            return ActionResult(ok=False, message="не задан адрес сайта")

        browser = self._value(action, "browser").strip()
        if not browser:
            browser = self._text("files.browser", "")

        # Название сервиса превращаем в адрес.
        if not url.startswith(("http://", "https://", "ftp://", "file://")):
            from luxvoice.sysint.launcher import guess_target
            kind, value = guess_target(url, get_locator(), self._settings)
            if kind == "url":
                url = value
            elif "." in url and " " not in url:
                url = "https://" + url
            else:
                # Это не адрес, а название — открываем поиск.
                template = self._text("files.search_template",
                                      "https://duckduckgo.com/?q={query}")
                from urllib.parse import quote_plus
                url = template.replace("{query}", quote_plus(url))

        if not url.startswith(("http://", "https://", "ftp://", "file://")):
            url = "https://" + url

        ok, error = get_locator().open_url(url, browser)
        if not ok:
            return ActionResult(ok=False, message=error)
        return ActionResult(ok=True, message=f"открыто: {url}", output="Открываю")

    def _do_launch_app(self, action: Action) -> ActionResult:
        from luxvoice.sysint.launcher import get_locator

        target = self._value(action, "path").strip()
        if not target:
            return ActionResult(ok=False, message="не задана программа")

        args = self._value(action, "args").strip()
        locator = get_locator()

        command = target
        # Если это название, а не путь — ищем программу.
        if self._bool_param(action, "as_app", True) and "/" not in target[:2]:
            entry = locator.best_match(target)
            if entry is not None:
                command = entry.command
                label = entry.name
            elif Path(target).expanduser().exists():
                command = str(Path(target).expanduser())
                label = Path(target).name
            else:
                # Не нашли программу — возможно, это файл или сайт.
                from luxvoice.sysint.launcher import guess_target
                kind, value = guess_target(target, locator, self._settings)
                if kind == "url":
                    ok, error = locator.open_url(value)
                    return (ActionResult(ok=True, message=f"открыто: {value}")
                            if ok else ActionResult(ok=False, message=error))
                if kind == "file":
                    ok, error = locator.open_file(value)
                    return (ActionResult(ok=True, message=f"открыто: {value}")
                            if ok else ActionResult(ok=False, message=error))
                return ActionResult(
                    ok=False,
                    message=f"программа «{target}» не найдена. "
                            "Укажите путь к ней в действии команды.",
                )
        else:
            label = Path(target).name

        if args:
            command = f"{command} {args}"

        ok, error = locator.launch(command)
        if not ok:
            return ActionResult(ok=False, message=error)
        return ActionResult(ok=True, message=f"запущено: {label}",
                            output=f"Запускаю {label}")

    def _do_open_path(self, action: Action) -> ActionResult:
        from luxvoice.sysint.launcher import get_locator

        target = self._value(action, "path").strip()
        if not target:
            return ActionResult(ok=False, message="не задан путь")
        opener = self._text("files.open_with", "xdg-open") or "xdg-open"
        ok, error = get_locator().open_file(target, opener)
        if not ok:
            return ActionResult(ok=False, message=error)
        return ActionResult(ok=True, message=f"открыто: {target}")

    def _do_focus_app(self, action: Action) -> ActionResult:
        from luxvoice.sysint.launcher import get_locator
        from luxvoice.sysint.windows import get_windows

        title = self._value(action, "title").strip()
        if not title:
            return ActionResult(ok=False, message="не указана программа")

        windows = get_windows()
        if windows.activate(title):
            return ActionResult(ok=True, message=f"переключился на {title}",
                                output="Переключаю")

        # Программа не запущена — запускаем.
        entry = get_locator().best_match(title)
        if entry is None:
            return ActionResult(ok=False, message=f"программа «{title}» не найдена")

        ok, error = get_locator().launch(entry.command)
        if not ok:
            return ActionResult(ok=False, message=error)
        return ActionResult(ok=True, message=f"запущено: {entry.name}",
                            output=f"Запускаю {entry.name}")

    def _do_close_app(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_windows

        title = self._value(action, "title").strip()
        windows = get_windows()

        if not title:
            if windows.close_active():
                return ActionResult(ok=True, message="окно закрыто", output="Закрываю")
            return ActionResult(ok=False, message="не удалось закрыть активное окно")

        if windows.close_by_title(title):
            return ActionResult(ok=True, message=f"закрыто: {title}", output="Закрываю")
        return ActionResult(ok=False, message=f"окно «{title}» не найдено")

    def _do_run_terminal(self, action: Action) -> ActionResult:
        from luxvoice.sysint.launcher import get_locator

        command = self._value(action, "command").strip()
        if not command:
            return ActionResult(ok=False, message="не задана команда")
        ok, error = get_locator().open_in_terminal(command)
        if not ok:
            return ActionResult(ok=False, message=error)
        return ActionResult(ok=True, message="команда открыта в терминале")

    # ================= КЛАВИШИ =================

    def _do_press_keys(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import describe_keys, press_key

        spec = self._value(action, "keys").strip()
        if not spec:
            return ActionResult(ok=False, message="не задано сочетание клавиш")
        if not press_key(spec):
            return ActionResult(ok=False, message=f"не удалось нажать {spec}")
        return ActionResult(ok=True, message=f"нажато: {describe_keys(spec)}")

    def _do_type_text(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import type_string

        text = self._value(action, "text")
        if not text:
            return ActionResult(ok=False, message="не задан текст")

        limit = int(self._number("safety.max_text_input", 5000))
        if len(text) > limit:
            text = text[:limit]

        typed = type_string(text)
        if typed == 0:
            return ActionResult(ok=False, message="не удалось ввести текст")
        return ActionResult(ok=True, message=f"введено символов: {typed}")

    def _do_type_enter(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key

        count = max(1, min(20, self._int_param(action, "count", 1)))
        for _ in range(count):
            if not press_key("enter"):
                return ActionResult(ok=False, message="не удалось нажать Enter")
            time.sleep(0.05)
        return ActionResult(ok=True, message="нажат Enter")

    def _do_type_hotkey_text(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key, type_string

        spec = self._value(action, "keys").strip()
        text = self._value(action, "text")

        if spec and not press_key(spec):
            return ActionResult(ok=False, message=f"не удалось нажать {spec}")
        time.sleep(0.2)

        if text:
            type_string(text)
            time.sleep(0.1)

        if self._bool_param(action, "enter", True):
            press_key("enter")

        return ActionResult(ok=True, message="введено")

    def _do_dictate(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key

        spec = self._value(action, "keys", "meta+h").strip() or "meta+h"
        if not press_key(spec):
            return ActionResult(
                ok=False,
                message="не удалось открыть диктовку. В Linux встроенной "
                        "диктовки нет — используйте действие «Ввести текст».",
            )
        return ActionResult(ok=True, message="диктовка открыта")

    # ================= МЫШЬ =================

    def _do_mouse_click(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import click_mouse

        button = self._value(action, "button", "left").strip().lower()
        double = self._bool_param(action, "double", False)
        count = max(1, min(10, self._int_param(action, "count", 1)))

        if not click_mouse(button, double=double, count=count):
            return ActionResult(ok=False, message="не удалось выполнить клик")
        return ActionResult(ok=True, message="клик выполнен")

    def _do_mouse_move(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import move_by

        dx = self._int_param(action, "dx", 0)
        dy = self._int_param(action, "dy", 0)
        if dx == 0 and dy == 0:
            return ActionResult(ok=False, message="не задано смещение курсора")
        if not move_by(dx, dy):
            return ActionResult(ok=False, message="не удалось переместить курсор")
        return ActionResult(ok=True, message=f"курсор сдвинут на {dx}, {dy}")

    def _do_mouse_to(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import move_to

        x = self._int_param(action, "x", 0)
        y = self._int_param(action, "y", 0)
        if not move_to(x, y):
            return ActionResult(ok=False, message="не удалось переместить курсор")
        return ActionResult(ok=True, message=f"курсор в точке {x}, {y}")

    def _do_mouse_scroll(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import scroll_by

        amount = self._int_param(action, "amount", 3)
        horizontal = self._bool_param(action, "horizontal", False)
        if amount == 0:
            return ActionResult(ok=False, message="не задано направление прокрутки")
        if not scroll_by(amount, horizontal):
            return ActionResult(ok=False, message="не удалось прокрутить")
        return ActionResult(ok=True, message="прокручено")

    def _do_mouse_drag(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import drag_by

        dx = self._int_param(action, "dx", 0)
        dy = self._int_param(action, "dy", 0)
        button = self._value(action, "button", "left")
        if not drag_by(dx, dy, button):
            return ActionResult(ok=False, message="не удалось перетащить")
        return ActionResult(ok=True, message="перетащено")

    def _do_mouse_hold(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import hold_button

        button = self._value(action, "button", "left")
        seconds = self._number_param(action, "seconds", 1.0)
        if not hold_button(button, seconds):
            return ActionResult(ok=False, message="не удалось зажать кнопку")
        return ActionResult(ok=True, message="кнопка удерживалась")

    # ================= ОКНА =================

    def _do_show_desktop(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_windows
        if get_windows().show_desktop():
            return ActionResult(ok=True, message="показан рабочий стол")
        return ActionResult(ok=False, message="не удалось показать рабочий стол")

    def _do_minimize_all(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_windows
        if get_windows().minimize_all():
            return ActionResult(ok=True, message="все окна свёрнуты")
        return ActionResult(ok=False, message="не удалось свернуть окна")

    def _do_minimize_window(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key
        if press_key("meta+pagedown"):
            return ActionResult(ok=True, message="окно свёрнуто")
        return ActionResult(ok=False, message="не удалось свернуть окно")

    def _do_maximize_window(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key
        if press_key("meta+pgup"):
            return ActionResult(ok=True, message="окно развёрнуто")
        return ActionResult(ok=False, message="не удалось развернуть окно")

    def _do_close_window(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_windows
        if get_windows().close_active():
            return ActionResult(ok=True, message="окно закрыто")
        # Запасной путь — сочетание закрытия.
        from luxvoice.sysint.uinput import press_key
        if press_key("alt+f4"):
            return ActionResult(ok=True, message="окно закрыто")
        return ActionResult(ok=False, message="не удалось закрыть окно")

    def _do_switch_window(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_windows

        title = self._value(action, "title").strip()
        if not title:
            return self._do_next_window(action)
        if get_windows().activate(title):
            return ActionResult(ok=True, message=f"переключился на {title}")
        return ActionResult(ok=False, message=f"окно «{title}» не найдено")

    def _do_next_window(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key
        if press_key("alt+tab"):
            return ActionResult(ok=True, message="следующее окно")
        return ActionResult(ok=False, message="не удалось переключить окно")

    def _do_previous_window(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key
        if press_key("alt+shift+tab"):
            return ActionResult(ok=True, message="предыдущее окно")
        return ActionResult(ok=False, message="не удалось переключить окно")

    def _do_switch_desktop(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key

        number = max(1, min(20, self._int_param(action, "number", 1)))
        if number <= 10:
            # KDE: Meta+1 … Meta+0 для первых десяти столов.
            key = str(number % 10)
            if press_key(f"meta+{key}"):
                return ActionResult(ok=True, message=f"рабочий стол {number}")
        return ActionResult(ok=False, message="не удалось сменить рабочий стол")

    # ================= ЗВУК =================

    def _do_set_volume(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio

        level = max(0, min(100, self._int_param(action, "level", 50)))
        mode = self._value(action, "smooth", "smooth").strip()
        smooth = mode in ("smooth", "slow")
        duration = 0.5 if mode == "smooth" else 1.0

        audio = get_audio()
        if not audio.set_volume(level, smooth=smooth, duration=duration):
            return ActionResult(ok=False, message="не удалось изменить громкость")
        return ActionResult(ok=True, message=f"громкость {level}%",
                            output=f"Громкость {level}")

    def _do_volume_up(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio

        amount = max(1, min(100, self._int_param(action, "amount", 10)))
        audio = get_audio()
        before = audio.volume()
        if not audio.adjust_volume(amount, smooth=True):
            return ActionResult(ok=False, message="не удалось увеличить громкость")
        after = audio.volume()
        return ActionResult(ok=True, message=f"громкость {after}%",
                            output=f"Громкость {after}")

    def _do_volume_down(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio

        amount = max(1, min(100, self._int_param(action, "amount", 10)))
        audio = get_audio()
        if not audio.adjust_volume(-amount, smooth=True):
            return ActionResult(ok=False, message="не удалось уменьшить громкость")
        after = audio.volume()
        return ActionResult(ok=True, message=f"громкость {after}%",
                            output=f"Громкость {after}")

    def _do_toggle_mute(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio

        muted = get_audio().toggle_mute()
        return ActionResult(ok=True, output="Звук выключен" if muted else "Звук включён",
                            message="звук выключен" if muted else "звук включён")

    def _do_mute(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio
        if get_audio().mute(True):
            return ActionResult(ok=True, message="звук выключен", output="Звук выключен")
        return ActionResult(ok=False, message="не удалось выключить звук")

    def _do_unmute(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio
        if get_audio().mute(False):
            return ActionResult(ok=True, message="звук включён", output="Звук включён")
        return ActionResult(ok=False, message="не удалось включить звук")

    def _do_app_volume(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio

        app = self._value(action, "app").strip()
        if not app:
            # Берём приложение активного окна.
            try:
                from luxvoice.sysint.windows import get_windows
                active = get_windows().active_window()
                app = active.app if active else ""
            except Exception:  # noqa: BLE001
                app = ""

        mode = self._value(action, "action", "delta").strip()
        value = self._int_param(action, "value", 10)
        audio = get_audio()

        if mode == "mute":
            ok = audio.set_app_volume(app, mute=True)
            text = "звук приложения выключен"
        elif mode == "unmute":
            ok = audio.set_app_volume(app, mute=False)
            text = "звук приложения включён"
        elif mode == "set":
            ok = audio.set_app_volume(app, percent=max(0, min(200, value)))
            text = f"громкость приложения {value}%"
        else:
            ok = audio.set_app_volume(app, delta=value)
            text = f"громкость приложения изменена на {value}%"

        if not ok:
            hint = f" ({app})" if app else ""
            return ActionResult(
                ok=False,
                message=f"приложение{hint} не найдено в микшере — "
                        "возможно, оно сейчас ничего не воспроизводит",
            )
        return ActionResult(ok=True, message=text, output=text.capitalize())

    def _do_play_sound(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio

        path = self._value(action, "path").strip()
        if not path:
            return ActionResult(ok=False, message="не задан звуковой файл")
        if not Path(path).expanduser().exists():
            return ActionResult(ok=False, message=f"файл не найден: {path}")

        wait = self._bool_param(action, "wait", False)
        if not get_audio().play_file(path, wait=wait):
            return ActionResult(ok=False, message="не удалось воспроизвести файл")
        return ActionResult(ok=True, message="звук воспроизводится")

    def _do_stop_sound(self, action: Action) -> ActionResult:
        from luxvoice.sysint.audio import get_audio
        get_audio().stop_playback()
        return ActionResult(ok=True, message="воспроизведение остановлено")

    # ================= ОЗВУЧКА =================

    def _do_speak(self, action: Action) -> ActionResult:
        text = self._value(action, "text")
        if not text:
            return ActionResult(ok=False, message="не задан текст для озвучки")
        self._speak(text, interrupt=False)
        return ActionResult(ok=True, message="фраза произнесена")

    def _do_speak_phrase(self, action: Action) -> ActionResult:
        kind = self._value(action, "kind", "ok").strip() or "ok"
        self._speak(None, interrupt=False, phrase_kind=kind)
        return ActionResult(ok=True, message="фраза произнесена")

    def _do_speak_random(self, action: Action) -> ActionResult:
        raw = self._value(action, "phrases")
        options = [line.strip() for line in raw.splitlines() if line.strip()]
        if not options:
            return ActionResult(ok=False, message="не задан список фраз")
        self._speak(random.choice(options), interrupt=False)
        return ActionResult(ok=True, message="фраза произнесена")

    def _do_beep(self, action: Action) -> ActionResult:
        kind = self._value(action, "kind", "soft").strip() or "soft"
        self._beep(kind)
        return ActionResult(ok=True, message="сигнал подан")

    def _do_stop_speech(self, action: Action) -> ActionResult:
        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            get_synthesizer(self._settings).stop()
        except Exception as exc:  # noqa: BLE001
            log.debug("Не удалось остановить речь: %s", exc)
        return ActionResult(ok=True, message="речь остановлена")

    def _speak(self, text: str | None, interrupt: bool = True,
               phrase_kind: str = "") -> None:
        """Произнести фразу своим обработчиком или напрямую."""
        if self._speak_handler is not None:
            try:
                self._speak_handler(text, phrase_kind, interrupt)
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("Обработчик озвучки упал: %s", exc)

        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            synth = get_synthesizer(self._settings)
            if phrase_kind:
                synth.say_phrase(phrase_kind)
            elif text:
                synth.say(text, interrupt=interrupt)
        except Exception as exc:  # noqa: BLE001
            log.debug("Озвучка недоступна: %s", exc)

    def _beep(self, kind: str) -> None:
        """Подать короткий сигнал."""
        try:
            from luxvoice.core.events import NOTIFY, bus
            from luxvoice.sysint.audio import get_audio

            audio = get_audio()
            if kind == "error":
                # Низкий двойной сигнал.
                _generate_tone(audio, 330, 0.12)
                time.sleep(0.06)
                _generate_tone(audio, 260, 0.16)
            elif kind == "ok":
                _generate_tone(audio, 880, 0.08)
                time.sleep(0.04)
                _generate_tone(audio, 1180, 0.10)
            elif kind == "beep":
                _generate_tone(audio, 1000, 0.08)
            else:
                _generate_tone(audio, 660, 0.09)
        except Exception as exc:  # noqa: BLE001
            log.debug("Сигнал не подан: %s", exc)

    # ================= СИСТЕМА =================

    def _do_lock_screen(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_power
        ok, error = get_power().lock_screen()
        if ok:
            return ActionResult(ok=True, message="экран заблокирован")
        return ActionResult(ok=False, message=error)

    def _do_sleep(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_power
        ok, error = get_power().suspend()
        if ok:
            return ActionResult(ok=True, message="спящий режим", output="Засыпаю")
        return ActionResult(ok=False, message=error or "не удалось уснуть")

    def _do_shutdown(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_power
        delay = max(0, self._int_param(action, "delay", 0))
        ok, error = get_power().shutdown(delay)
        if ok:
            text = ("выключение через " + str(delay) + " с") if delay else "выключаюсь"
            return ActionResult(ok=True, message=text, output="Выключаюсь")
        return ActionResult(
            ok=False,
            message=f"не удалось выключить компьютер: {error}. "
                    "Проверьте права: systemctl poweroff обычно работает "
                    "без пароля для активного сеанса.",
        )

    def _do_reboot(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_power
        ok, error = get_power().reboot()
        if ok:
            return ActionResult(ok=True, message="перезагрузка", output="Перезагружаюсь")
        return ActionResult(ok=False, message=f"не удалось перезагрузить: {error}")

    def _do_logout(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_power
        ok, error = get_power().logout()
        if ok:
            return ActionResult(ok=True, message="выход из сеанса")
        return ActionResult(ok=False, message=error)

    def _do_screen_off(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_power
        ok, error = get_power().screen_off()
        if ok:
            return ActionResult(ok=True, message="монитор выключен")
        return ActionResult(ok=False, message=error)

    def _do_screen_on(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_power
        ok, error = get_power().screen_on()
        if ok:
            return ActionResult(ok=True, message="монитор включён")
        return ActionResult(ok=False, message=error)

    def _do_screenshot(self, action: Action) -> ActionResult:
        target_dir = self._value(action, "path").strip()
        if not target_dir:
            target_dir = str(Path.home() / "Изображения")
        folder = Path(target_dir).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        filename = time.strftime("Снимок %Y-%m-%d %H-%M-%S.png")
        target = folder / filename

        saved = False
        # spectacle — родной для KDE, умеет снимать весь экран в Wayland.
        if shutil.which("spectacle"):
            try:
                completed = subprocess.run(
                    ["spectacle", "-b", "-n", "-f", "-o", str(target)],
                    capture_output=True, timeout=20)
                saved = completed.returncode == 0 and target.exists()
            except (OSError, subprocess.TimeoutExpired):
                saved = False

        if not saved and shutil.which("grim"):
            try:
                completed = subprocess.run(["grim", str(target)],
                                           capture_output=True, timeout=15)
                saved = completed.returncode == 0 and target.exists()
            except (OSError, subprocess.TimeoutExpired):
                saved = False

        if not saved and shutil.which("gnome-screenshot"):
            try:
                completed = subprocess.run(
                    ["gnome-screenshot", "-f", str(target)],
                    capture_output=True, timeout=15)
                saved = completed.returncode == 0 and target.exists()
            except (OSError, subprocess.TimeoutExpired):
                saved = False

        if not saved:
            return ActionResult(
                ok=False,
                message="не удалось сделать снимок экрана. "
                        "Установите spectacle (KDE) или grim.",
            )

        if self._bool_param(action, "copy", False):
            from luxvoice.sysint import clipboard
            clipboard.copy_file_to_clipboard(str(target))

        return ActionResult(ok=True, message=f"снимок сохранён: {target}",
                            output="Снимок готов")

    def _do_clipboard_copy(self, action: Action) -> ActionResult:
        from luxvoice.sysint import clipboard

        text = self._value(action, "text")
        if not text:
            return ActionResult(ok=False, message="не задан текст")
        if clipboard.set_text(text):
            return ActionResult(ok=True, message="текст скопирован")
        return ActionResult(ok=False, message="не удалось скопировать текст")

    def _do_clipboard_paste(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key
        if press_key("ctrl+v"):
            return ActionResult(ok=True, message="вставлено из буфера")
        return ActionResult(ok=False, message="не удалось вставить")

    def _do_notification(self, action: Action) -> ActionResult:
        text = self._value(action, "text")
        title = self._value(action, "title", "Ассистент") or "Ассистент"
        if not text:
            return ActionResult(ok=False, message="не задан текст уведомления")
        if self._notify_handler is not None:
            self._notify_handler(title, text)
        else:
            try:
                subprocess.run(["notify-send", title, text], timeout=5,
                               capture_output=True)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return ActionResult(ok=True, message="уведомление показано")

    def _do_open_settings(self, action: Action) -> ActionResult:
        from luxvoice.sysint.launcher import get_locator

        page = self._value(action, "page").strip()
        command = "systemsettings"
        if page:
            command = f"systemsettings {page}"
        ok, error = get_locator().launch(command)
        if not ok:
            return ActionResult(ok=False, message=error)
        return ActionResult(ok=True, message="настройки открыты")

    def _do_media_control(self, action: Action) -> ActionResult:
        from luxvoice.sysint.uinput import press_key

        command = self._value(action, "command", "play_pause").strip()
        mapping = {
            "play_pause": "playpause", "next": "nextsong",
            "previous": "previoussong", "stop": "stopcd",
        }
        key = mapping.get(command, "playpause")

        # Сначала пробуем плеер через MPRIS — это работает надёжнее.
        if self._mpris(command):
            return ActionResult(ok=True, message="медиа управляется")

        if press_key(key):
            return ActionResult(ok=True, message="медиа управление выполнено")
        return ActionResult(ok=False, message="не удалось управлять медиа")

    @staticmethod
    def _mpris(command: str) -> bool:
        """Управление плеером через MPRIS (D-Bus)."""
        players = ("org.mpris.MediaPlayer2.spotify",
                   "org.mpris.MediaPlayer2.vlc",
                   "org.mpris.MediaPlayer2.rhythmbox",
                   "org.mpris.MediaPlayer2.audacious",
                   "org.mpris.MediaPlayer2.elisa",
                   "org.mpris.MediaPlayer2.firefox.instance")
        method = {
            "play_pause": "PlayPause", "next": "Next",
            "previous": "Previous", "stop": "Stop",
        }.get(command, "PlayPause")

        for player in players:
            try:
                completed = subprocess.run(
                    ["gdbus", "call", "--session", "--dest", player,
                     "--object-path", "/org/mpris/MediaPlayer2",
                     "--method", f"org.mpris.MediaPlayer2.Player.{method}"],
                    capture_output=True, timeout=3)
                if completed.returncode == 0:
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
        return False

    def _do_brightness(self, action: Action) -> ActionResult:
        mode = self._value(action, "action", "up").strip()
        value = max(1, min(100, self._int_param(action, "value", 10)))

        # brightnessctl — самый распространённый способ.
        if shutil.which("brightnessctl"):
            try:
                if mode == "set":
                    args = ["brightnessctl", "set", f"{value}%"]
                elif mode == "down":
                    args = ["brightnessctl", "set", f"{value}%-"]
                else:
                    args = ["brightnessctl", "set", f"{value}%+"]
                completed = subprocess.run(args, capture_output=True, timeout=5)
                if completed.returncode == 0:
                    return ActionResult(ok=True, message="яркость изменена")
            except (OSError, subprocess.TimeoutExpired):
                pass

        # Через D-Bus KDE (работает без дополнительных программ).
        from luxvoice.sysint.uinput import press_key
        if mode == "up":
            if press_key("brightnessup"):
                return ActionResult(ok=True, message="яркость увеличена")
        elif mode == "down":
            if press_key("brightnessdown"):
                return ActionResult(ok=True, message="яркость уменьшена")

        return ActionResult(
            ok=False,
            message="не удалось изменить яркость. Установите brightnessctl.",
        )

    # ================= ФАЙЛЫ =================

    def _check_write_path(self, path: Path) -> str:
        """Проверить, можно ли писать по этому пути."""
        forbidden = self._settings.items("safety.sandbox_paths") \
            if self._settings is not None else []
        if not forbidden:
            forbidden = ["/etc", "/usr", "/boot", "/sys", "/proc", "/dev"]

        resolved = path.expanduser().resolve() if path.is_absolute() else path
        text = str(resolved)
        for blocked in forbidden:
            blocked = str(blocked).strip()
            if blocked and (text == blocked or text.startswith(blocked.rstrip("/") + "/")):
                return (f"запись в {blocked} запрещена настройками безопасности "
                        "— это системный каталог")
        return ""

    def _do_create_folder(self, action: Action) -> ActionResult:
        raw = self._value(action, "path").strip()
        if not raw:
            return ActionResult(ok=False, message="не задан путь к папке")

        target = Path(raw).expanduser()
        error = self._check_write_path(target)
        if error:
            return ActionResult(ok=False, message=error)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ActionResult(ok=False, message=f"не удалось создать папку: {exc}")
        return ActionResult(ok=True, message=f"папка создана: {target}")

    def _do_write_file(self, action: Action) -> ActionResult:
        raw = self._value(action, "path").strip()
        if not raw:
            return ActionResult(ok=False, message="не задан путь к файлу")

        text = self._value(action, "text")
        target = Path(raw).expanduser()
        error = self._check_write_path(target)
        if error:
            return ActionResult(ok=False, message=error)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if self._bool_param(action, "append", False) else "w"
            with open(target, mode, encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            return ActionResult(ok=False, message=f"не удалось записать файл: {exc}")
        return ActionResult(ok=True, message=f"записано в {target}")

    def _do_delete_path(self, action: Action) -> ActionResult:
        raw = self._value(action, "path").strip()
        if not raw:
            return ActionResult(ok=False, message="не задан путь")

        target = Path(raw).expanduser()
        if not target.exists():
            return ActionResult(ok=False, message=f"путь не найден: {target}")

        error = self._check_write_path(target)
        if error:
            return ActionResult(ok=False, message=error)

        # Защита: не удаляем домашний каталог и его корневые папки.
        home = Path.home().resolve()
        try:
            resolved = target.resolve()
            if resolved == home or resolved == Path("/"):
                return ActionResult(
                    ok=False,
                    message="удаление этого каталога запрещено — "
                            "это защита от случайной потери данных",
                )
        except OSError:
            pass

        permanent = self._bool_param(action, "permanent", False)
        trash = self._flag("files.trash_instead_delete", True)

        if permanent or not trash:
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            except OSError as exc:
                return ActionResult(ok=False, message=f"не удалось удалить: {exc}")
            return ActionResult(ok=True, message=f"удалено: {target}")

        # Перемещение в корзину — безопасный путь.
        try:
            self._to_trash(target)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, message=f"не удалось переместить в корзину: {exc}")
        return ActionResult(ok=True, message=f"перемещено в корзину: {target.name}")

    @staticmethod
    def _to_trash(target: Path) -> None:
        """Переместить в корзину по стандарту XDG."""
        import urllib.parse

        trash_dir = Path.home() / ".local/share/Trash"
        files_dir = trash_dir / "files"
        info_dir = trash_dir / "info"
        files_dir.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)

        name = target.name
        destination = files_dir / name
        counter = 1
        while destination.exists():
            destination = files_dir / f"{name}.{counter}"
            counter += 1

        shutil.move(str(target), str(destination))

        info = (
            "[Trash Info]\n"
            f"Path={urllib.parse.quote(str(target))}\n"
            f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        )
        (info_dir / f"{destination.name}.trashinfo").write_text(info, encoding="utf-8")

    def _do_copy_path(self, action: Action) -> ActionResult:
        source = self._value(action, "source").strip()
        target = self._value(action, "target").strip()
        if not source or not target:
            return ActionResult(ok=False, message="не заданы источник и назначение")

        src, dst = Path(source).expanduser(), Path(target).expanduser()
        if not src.exists():
            return ActionResult(ok=False, message=f"источник не найден: {src}")

        error = self._check_write_path(dst)
        if error:
            return ActionResult(ok=False, message=error)

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        except OSError as exc:
            return ActionResult(ok=False, message=f"не удалось скопировать: {exc}")
        return ActionResult(ok=True, message=f"скопировано в {dst}")

    def _do_move_path(self, action: Action) -> ActionResult:
        source = self._value(action, "source").strip()
        target = self._value(action, "target").strip()
        if not source or not target:
            return ActionResult(ok=False, message="не заданы источник и назначение")

        src, dst = Path(source).expanduser(), Path(target).expanduser()
        if not src.exists():
            return ActionResult(ok=False, message=f"источник не найден: {src}")

        error = self._check_write_path(dst)
        if error:
            return ActionResult(ok=False, message=error)

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return ActionResult(ok=False, message=f"не удалось переместить: {exc}")
        return ActionResult(ok=True, message=f"перемещено в {dst}")

    # ================= КОНСОЛЬ =================

    def _do_run_shell(self, action: Action) -> ActionResult:
        command = self._value(action, "command").strip()
        if not command:
            return ActionResult(ok=False, message="не задана команда")

        lowered = command.lower()
        for pattern in _HARD_BLOCKED_PATTERNS:
            if pattern in lowered:
                return ActionResult(
                    ok=False,
                    message=f"команда заблокирована: содержит «{pattern}». "
                            "Такие операции запрещены.",
                )

        blocked = self._settings.items("safety.blocked_commands") \
            if self._settings is not None else []
        for pattern in blocked:
            pattern = str(pattern).strip().lower()
            if pattern and pattern in lowered:
                return ActionResult(
                    ok=False,
                    message=f"команда заблокирована настройками: «{pattern}»",
                )

        keep_open = self._bool_param(action, "keep_open", False)
        if command.lower().startswith("keep:"):
            keep_open = True
            command = command[5:].strip()

        timeout = self._number("safety.shell_timeout", 60)
        show_output = self._bool_param(action, "show_output", False)

        if keep_open:
            from luxvoice.sysint.launcher import get_locator
            ok, error = get_locator().open_in_terminal(command)
            if not ok:
                return ActionResult(ok=False, message=error)
            return ActionResult(ok=True, message="команда открыта в терминале")

        try:
            completed = subprocess.run(
                ["bash", "-lc", command],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ActionResult(
                ok=False,
                message=f"команда не завершилась за {timeout:.0f} с и была прервана",
            )
        except OSError as exc:
            return ActionResult(ok=False, message=f"не удалось выполнить: {exc}")

        output = ((completed.stdout or "") + (completed.stderr or "")).strip()

        if completed.returncode != 0:
            snippet = output[:300] if output else "без вывода"
            return ActionResult(
                ok=False,
                message=f"команда завершилась с ошибкой ({completed.returncode}): "
                        f"{snippet}",
                output=output[:1500],
            )

        message = "команда выполнена"
        if output:
            message = output.splitlines()[0][:200] if output else message

        return ActionResult(ok=True, message=message, output=output[:1500])

    def _do_run_script(self, action: Action) -> ActionResult:
        from luxvoice.sysint.launcher import get_locator

        path = self._value(action, "path").strip()
        if not path:
            return ActionResult(ok=False, message="не задан путь к сценарию")

        target = Path(path).expanduser()
        if not target.exists():
            return ActionResult(ok=False, message=f"сценарий не найден: {target}")

        args = self._value(action, "args").strip()
        command = f"{target} {args}".strip()
        ok, error = get_locator().launch(command)
        if not ok:
            return ActionResult(ok=False, message=error)
        return ActionResult(ok=True, message=f"сценарий запущен: {target.name}")

    # ================= УПРАВЛЕНИЕ =================

    def _do_pause(self, action: Action) -> ActionResult:
        seconds = self._number_param(action, "seconds", 1.0)
        self._sleep(max(0.0, seconds))
        return ActionResult(ok=True, message=f"пауза {seconds:g} с")

    def _do_pause_ms(self, action: Action) -> ActionResult:
        milliseconds = self._int_param(action, "milliseconds", 300)
        self._sleep(max(0.01, milliseconds / 1000.0))
        return ActionResult(ok=True, message=f"пауза {milliseconds} мс")

    def _do_pause_random(self, action: Action) -> ActionResult:
        low = self._number_param(action, "minimum", 0.5)
        high = self._number_param(action, "maximum", 1.5)
        if low > high:
            low, high = high, low
        seconds = random.uniform(max(0.05, low), max(0.05, high))
        self._sleep(seconds)
        return ActionResult(ok=True, message=f"пауза {seconds:.2f} с")

    def _do_repeat(self, action: Action) -> ActionResult:
        count = max(1, min(100, self._int_param(action, "count", 2)))
        return ActionResult(ok=True, message=f"повторить {count} раз",
                            control=f"repeat:{count}")

    def _do_repeat_block(self, action: Action) -> ActionResult:
        count = max(1, min(100, self._int_param(action, "count", 3)))
        start = max(1, self._int_param(action, "start", 1))
        return ActionResult(ok=True, message=f"повторить группу {count} раз",
                            control=f"repeat_block:{count}:{start}")

    def _do_wait_window(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_windows

        title = self._value(action, "title").strip()
        timeout = self._number_param(action, "timeout", 10.0)
        if not title:
            return ActionResult(ok=False, message="не указано название окна")

        deadline = time.time() + timeout
        windows = get_windows()
        while time.time() < deadline:
            if self._stop_event.is_set():
                return ActionResult(ok=False, message="прервано", control="stop")
            for window in windows.windows(use_cache=False):
                if title.lower() in window.searchable:
                    return ActionResult(ok=True, message=f"окно появилось: {window.title}")
            time.sleep(0.4)

        return ActionResult(ok=False, message=f"окно «{title}» не появилось за {timeout:g} с")

    def _do_if_running(self, action: Action) -> ActionResult:
        from luxvoice.sysint.windows import get_windows

        name = self._value(action, "name").strip()
        if not name:
            return ActionResult(ok=False, message="не указана программа")

        running = get_windows().is_running(name)
        mode = self._value(action, "then", "skip").strip()

        if running and mode == "skip":
            return ActionResult(ok=True, skipped=True,
                                message=f"{name} уже запущена — команда пропущена",
                                control="skip_rest")
        if running:
            return ActionResult(ok=True, message=f"{name} запущена")
        return ActionResult(ok=True, message=f"{name} не запущена")

    def _do_set_variable(self, action: Action) -> ActionResult:
        name = self._value(action, "name", "my_var").strip() or "my_var"
        # Имя переменной должно быть безопасным идентификатором.
        import re
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            return ActionResult(
                ok=False,
                message="имя переменной может содержать только латинские буквы, "
                        "цифры и подчёркивание",
            )
        value = self._value(action, "value")
        with self._lock:
            self._variables[name] = value
        return ActionResult(ok=True, message=f"запомнено: {name}")

    def _do_await_voice(self, action: Action) -> ActionResult:
        question = self._value(action, "question").strip()
        timeout = self._number_param(action, "timeout", 15.0)
        save_as = self._value(action, "save_as", "answer").strip() or "answer"

        answer = self._ask(question, timeout)
        if answer is None:
            return ActionResult(ok=False, message="ответ не получен")

        with self._lock:
            self._variables[save_as] = answer
        return ActionResult(ok=True, message=f"ответ: {answer}")

    def _do_confirm(self, action: Action) -> ActionResult:
        question = self._value(action, "question", "Выполнить?").strip()
        timeout = self._number_param(action, "timeout", 30.0)

        answer = self._ask(question, timeout)
        if answer is None:
            return ActionResult(ok=False, message="подтверждение не получено",
                                control="stop")

        words_yes = self._words("confirm.words_yes",
                                "да,правильно,подтверждаю,верно,ага,yes,ok")
        words_no = self._words("confirm.words_no",
                               "нет,отмена,стоп,не надо,отменить,no,cancel")

        from luxvoice.core.store import normalize_phrase
        normalized = normalize_phrase(answer)
        if any(word in normalized for word in words_yes):
            return ActionResult(ok=True, message="подтверждено")
        if any(word in normalized for word in words_no):
            return ActionResult(ok=False, message="отменено пользователем",
                                control="stop")
        return ActionResult(ok=False, message="ответ не распознан как подтверждение",
                            control="stop")

    def _ask(self, question: str, timeout: float) -> str | None:
        """Задать вопрос и получить голосовой ответ."""
        if self._ask_handler is None:
            # Без обработчика вопроса подтверждение невозможно — не выполняем.
            log.warning("Диалоговый шаг пропущен: обработчик ответов не подключён")
            return None

        if question:
            self._speak(question, interrupt=True)
            # Даём фразе закончиться, чтобы микрофон не поймал свой голос.
            self._sleep(0.3)

        try:
            return self._ask_handler(question, timeout)
        except Exception as exc:  # noqa: BLE001
            log.debug("Не удалось получить ответ: %s", exc)
            return None

    def _words(self, key: str, default: str) -> list[str]:
        raw = self._text(key, default)
        from luxvoice.core.store import normalize_phrase
        return [normalize_phrase(part) for part in raw.replace(";", ",").split(",")
                if part.strip()]

    def _do_notify_history(self, action: Action) -> ActionResult:
        text = self._value(action, "text")
        return ActionResult(ok=True, message=text or "заметка добавлена", output=text)

    def _do_stop_command(self, action: Action) -> ActionResult:
        reason = self._value(action, "reason", "остановлено") or "остановлено"
        self._stop_event.set()
        return ActionResult(ok=True, message=reason, control="stop")

    def _do_run_command(self, action: Action) -> ActionResult:
        target = self._value(action, "command").strip()
        if not target:
            return ActionResult(ok=False, message="не указана команда для вызова")

        if self._command_runner is None:
            return ActionResult(ok=False, message="вызов команд недоступен")

        try:
            ok = self._command_runner(target)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(ok=False, message=f"не удалось выполнить: {exc}")

        if ok:
            return ActionResult(ok=True, message=f"выполнена команда «{target}»")
        return ActionResult(ok=False, message=f"команда «{target}» не найдена")

    def _do_try_block(self, action: Action) -> ActionResult:
        # Управляющий шаг: помечает, что следующие шаги можно пропустить
        # при ошибке. Обработка — в исполнителе команд.
        start = max(1, self._int_param(action, "start", 1))
        return ActionResult(ok=True, message="режим мягкой ошибки",
                            control=f"try:{start}")


def _generate_tone(audio, frequency: float, duration: float) -> None:
    """Сгенерировать короткий тон и проиграть его."""
    import io
    import math
    import struct
    import wave

    sample_rate = 22050
    total = int(sample_rate * duration)
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)

        frames = bytearray()
        fade = max(1, int(sample_rate * 0.01))
        for index in range(total):
            # Плавное появление и затухание — без щелчков.
            envelope = 1.0
            if index < fade:
                envelope = index / fade
            elif index > total - fade:
                envelope = (total - index) / fade
            value = int(0.35 * envelope * 32767 *
                        math.sin(2 * math.pi * frequency * index / sample_rate))
            frames.extend(struct.pack("<h", value))
        writer.writeframes(bytes(frames))

    audio.play_bytes(buffer.getvalue(), ".wav")


# --- Единственный экземпляр -------------------------------------------------

_executor: ActionExecutor | None = None
_lock = threading.Lock()


def get_executor(settings=None) -> ActionExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ActionExecutor(settings)
        elif settings is not None:
            _executor.set_settings(settings)
        return _executor