"""Управление окнами и приложениями в KDE Plasma (Wayland).

Через D-Bus KWin доступно: список окон, активное окно, сворачивание,
переключение, закрытие. Через KGlobalAccel — системные сочетания.

Если D-Bus недоступен (другой рабочий стол), используются запасные пути:
  * wmctrl для X11;
  * эвристика по процессам через /proc.

Всё, что не удаётся, возвращает понятную ошибку — не исключение.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    """Сведения об окне."""

    id: str = ""
    title: str = ""
    app: str = ""            # класс ресурса приложения
    pid: int = 0
    active: bool = False
    minimized: bool = False
    desktop: int = -1
    geometry: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def searchable(self) -> str:
        """Строка для поиска по названию окна."""
        return f"{self.title} {self.app}".lower()


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    """Запустить программу и вернуть код, вывод и ошибки."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout or "", result.stderr or ""
    except FileNotFoundError:
        return 127, "", f"программа не найдена: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "превышено время ожидания"
    except OSError as exc:
        return 1, "", str(exc)


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def desktop_environment() -> dict[str, str]:
    """Определить рабочее окружение и сессию."""
    return {
        "session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
        "kde_full": os.environ.get("KDE_FULL_SESSION", ""),
        "wayland": os.environ.get("WAYLAND_DISPLAY", ""),
        "display": os.environ.get("DISPLAY", ""),
    }


def is_kde() -> bool:
    env = desktop_environment()
    return "kde" in env["desktop"].lower() or bool(env["kde_full"])


def is_wayland() -> bool:
    return desktop_environment()["session"] == "wayland"


# --- Окна через KWin D-Bus --------------------------------------------------

# Скрипт для KWin. Работает так: скрипт загружается в KWin один раз,
# затем вызывается повторно. Результат печатается в системный журнал
# с меткой — иначе забрать данные из KWin в Wayland невозможно.
_MARKER = "LUXVOICE_WINDOWS:"

_KWIN_SCRIPT = """
function luxvoiceReport() {
    var list = [];
    var clients = workspace.windowList ? workspace.windowList() : workspace.clientList();
    var aw = workspace.activeWindow;
    var activeId = "";
    if (aw) {
        activeId = aw.internalId ? aw.internalId.toString() : aw.windowId.toString();
    }
    for (var i = 0; i < clients.length; i++) {
        var w = clients[i];
        var id = w.internalId ? w.internalId.toString() : w.windowId.toString();
        var g = w.frameGeometry;
        list.push({
            id: id,
            title: w.caption || "",
            app: w.resourceClass ? w.resourceClass.toString() : "",
            pid: w.pid || 0,
            active: (id === activeId),
            minimized: w.minimized || false,
            desktop: w.desktop || -1,
            x: g ? g.x : 0,
            y: g ? g.y : 0,
            w: g ? g.width : 0,
            h: g ? g.height : 0
        });
    }
    console.info("__MARKER__" + JSON.stringify(list));
}

luxvoiceReport();
""".replace("__MARKER__", _MARKER)


class WindowManager:
    """Управление окнами. Работает через KWin, при неудаче — через X11-утилиты."""

    def __init__(self) -> None:
        self._kde = is_kde()
        self._wayland = is_wayland()
        self._wmctrl = has_command("wmctrl")
        self._xdotool = has_command("xdotool")
        self._kdotool = has_command("kdotool")
        self._cache: tuple[float, list[WindowInfo]] = (0.0, [])
        self._cache_ttl = 0.4
        self._kwin_script_id: int | None = None

    # --- Список окон ------------------------------------------------------

    def windows(self, use_cache: bool = True) -> list[WindowInfo]:
        """Получить список открытых окон."""
        now = time.time()
        if use_cache and now - self._cache[0] < self._cache_ttl:
            return self._cache[1]

        result: list[WindowInfo] = []

        if self._kde and self._wayland:
            result = self._windows_kwin_script()
        if not result and self._kdotool:
            result = self._windows_kdotool()
        if not result and self._wmctrl and os.environ.get("DISPLAY"):
            result = self._windows_wmctrl()

        self._cache = (now, result)
        return result

    def _windows_kwin_script(self) -> list[WindowInfo]:
        """Получить список окон через скрипт KWin.

        Скрипт загружается один раз, затем вызывается сколько нужно.
        KWin печатает результат в системный журнал с меткой — это
        единственный надёжный способ вернуть данные в Wayland.
        """
        script_id = self._ensure_kwin_script()
        if script_id is None:
            return []

        before = time.time()
        # Запускаем скрипт повторно.
        _run([
            "gdbus", "call", "--session", "--dest", "org.kde.KWin",
            "--object-path", f"/Scripting/Script{script_id}",
            "--method", "org.kde.kwin.Script.run",
        ], timeout=4)

        # Ждём появления записи в журнале.
        for attempt in range(3):
            time.sleep(0.12 if attempt == 0 else 0.2)
            windows = self._windows_from_journal(since=before - 0.5)
            if windows:
                return windows
        return []

    def _ensure_kwin_script(self) -> int | None:
        """Загрузить скрипт в KWin, если он ещё не загружен."""
        if self._kwin_script_id is not None:
            return self._kwin_script_id

        script_path = "/tmp/luxvoice-kwin-windows.js"
        try:
            with open(script_path, "w", encoding="utf-8") as handle:
                handle.write(_KWIN_SCRIPT)
        except OSError as exc:
            log.debug("Не удалось записать скрипт KWin: %s", exc)
            return None

        code, out, err = _run([
            "gdbus", "call", "--session", "--dest", "org.kde.KWin",
            "--object-path", "/Scripting",
            "--method", "org.kde.kwin.Scripting.loadScript", script_path,
        ], timeout=4)

        if code != 0:
            log.debug("Скрипт KWin не загружен: %s", err.strip())
            return None

        match = re.search(r"(\d+)", out)
        if not match:
            return None
        self._kwin_script_id = int(match.group(1))
        log.debug("Скрипт KWin загружен с номером %s", self._kwin_script_id)
        return self._kwin_script_id

    def _windows_from_journal(self, since: float = 0.0) -> list[WindowInfo]:
        """Прочитать отчёт скрипта из системного журнала."""
        seconds = max(2, int(time.time() - since) + 3) if since else 10
        code, out, _ = _run([
            "journalctl", "--user", "-n", "120", "--no-pager",
            "-t", "kwin_wayland", "--since", f"-{seconds}s",
        ], timeout=6)
        if code != 0 or _MARKER not in out:
            return []
        return self._parse_report(out)

    def _parse_report(self, text: str) -> list[WindowInfo]:
        """Разобрать строку отчёта."""
        windows: list[WindowInfo] = []
        for line in reversed(text.splitlines()):
            index = line.find(_MARKER)
            if index < 0:
                continue
            payload = line[index + len(_MARKER):].strip()
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                windows.append(WindowInfo(
                    id=str(item.get("id", "")),
                    title=str(item.get("title", "")),
                    app=str(item.get("app", "")),
                    pid=int(item.get("pid", 0) or 0),
                    active=bool(item.get("active", False)),
                    minimized=bool(item.get("minimized", False)),
                    desktop=int(item.get("desktop", -1) or -1),
                    geometry=(int(item.get("x", 0) or 0), int(item.get("y", 0) or 0),
                              int(item.get("w", 0) or 0), int(item.get("h", 0) or 0)),
                ))
            if windows:
                return windows
        return windows

    def _windows_kdotool(self) -> list[WindowInfo]:
        """Запасной путь: kdotool (AUR-утилита, клон xdotool для KDE)."""
        code, out, _ = _run(["kdotool", "search", "--name", "", "--all"], timeout=5)
        if code != 0:
            return []
        windows: list[WindowInfo] = []
        for line in out.splitlines():
            window_id = line.strip()
            if not window_id:
                continue
            _, name, _ = _run(["kdotool", "getwindowname", window_id], timeout=3)
            _, cls, _ = _run(["kdotool", "getwindowclassname", window_id], timeout=3)
            windows.append(WindowInfo(id=window_id, title=name.strip(), app=cls.strip()))
        return windows

    def _windows_wmctrl(self) -> list[WindowInfo]:
        """Запасной путь для X11."""
        code, out, _ = _run(["wmctrl", "-lx"], timeout=5)
        if code != 0:
            return []
        windows: list[WindowInfo] = []
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            window_id, desktop, _, host, rest = parts
            # rest = "класс.Приложение  Заголовок"
            app, _, title = rest.partition(" ")
            windows.append(WindowInfo(
                id=window_id,
                title=title.strip(),
                app=app.strip(),
                desktop=int(desktop) if desktop.lstrip("-").isdigit() else -1,
            ))
        return windows

    # --- Активное окно ----------------------------------------------------

    def active_window(self) -> WindowInfo | None:
        """Текущее активное окно."""
        for window in self.windows(use_cache=False):
            if window.active:
                return window

        # Запасной путь: активное окно по данным X11.
        if self._xdotool and os.environ.get("DISPLAY"):
            code, out, _ = _run(["xdotool", "getactivewindow"], timeout=3)
            if code == 0 and out.strip():
                return WindowInfo(id=out.strip())

        # Последний вариант: активное окно по pid активного процесса
        # определить нельзя, поэтому возвращаем самое вероятное —
        # первое окно с заголовком.
        for window in self.windows():
            if window.title:
                return window
        return None

    def active_title(self) -> str:
        info = self.active_window()
        return info.title if info else ""

    def active_contexts(self, limit: int = 6) -> list[str]:
        """Строки для контекстного поиска: заголовки открытых окон.

        Служебные окна рабочего стола (панель, композитор) пропускаются —
        иначе короткая фраза «пауза» считалась бы уместной всегда.
        """
        titles: list[str] = []
        active = self.active_window()
        if active is not None and not self._is_shell_window(active):
            if active.title:
                titles.append(active.title)
            if active.app:
                titles.append(active.app)

        for window in self.windows()[:limit + 4]:
            if self._is_shell_window(window):
                continue
            if window.title and window.title not in titles:
                titles.append(window.title)
            if window.app and window.app not in titles:
                titles.append(window.app)
            if len(titles) >= limit:
                break
        return titles[:limit]

    # Окна рабочего стола и системных служб — не контекст для команд.
    _SHELL_APPS = frozenset({
        "plasmashell", "kwin_wayland", "kwin_x11", "ksmserver", "kded5", "kded6",
        "kglobalaccel", "xembedsniproxy", "polkit-kde-authentication-agent-1",
        "plasma-desktop",
    })

    @classmethod
    def _is_shell_window(cls, window: WindowInfo) -> bool:
        app = (window.app or "").lower()
        if app in cls._SHELL_APPS:
            return True
        # Пустое окно без названия — служебное.
        return not (window.title or "").strip() and not app

    # --- Действия над окнами ---------------------------------------------

    def show_desktop(self) -> bool:
        """Показать рабочий стол (свернуть все окна)."""
        if self._kde:
            code, _, _ = _run([
                "gdbus", "call", "--session", "--dest", "org.kde.KWin",
                "--object-path", "/KWin",
                "--method", "org.kde.KWin.showDesktop", "true",
            ], timeout=4)
            if code == 0:
                return True
        # Универсальный путь — сочетание клавиш.
        from luxvoice.sysint.uinput import combo_by_spec
        return combo_by_spec("meta+d")

    def toggle_desktop(self) -> bool:
        """Переключить состояние «показать рабочий стол»."""
        from luxvoice.sysint.uinput import combo_by_spec
        return combo_by_spec("meta+d")

    def minimize_all(self) -> bool:
        """Свернуть все окна."""
        return self.show_desktop()

    def close_active(self) -> bool:
        """Закрыть активное окно."""
        if self._kde and self._wayland:
            code, _, _ = _run([
                "gdbus", "call", "--session", "--dest", "org.kde.KWin",
                "--object-path", "/KWin",
                "--method", "org.kde.KWin.killWindow",
            ], timeout=4)
            if code == 0:
                return True
        if self._wmctrl:
            code, _, _ = _run(["wmctrl", "-c", ":ACTIVE:"], timeout=4)
            return code == 0
        return False

    def close_by_title(self, pattern: str) -> bool:
        """Закрыть окно, подходящее по названию."""
        needle = pattern.lower()
        for window in self.windows():
            if needle in window.searchable:
                if window.id and self._wmctrl:
                    code, _, _ = _run(["wmctrl", "-i", "-c", window.id], timeout=4)
                    if code == 0:
                        return True
                elif window.pid:
                    return self.kill_pid(window.pid)
        return False

    def activate(self, pattern: str) -> bool:
        """Переключиться на окно по названию."""
        needle = pattern.lower()
        for window in self.windows():
            if needle in window.searchable:
                if window.id and self._wmctrl:
                    code, _, _ = _run(["wmctrl", "-i", "-a", window.id], timeout=4)
                    if code == 0:
                        return True
        return False

    def minimize(self, pattern: str = "") -> bool:
        if not pattern:
            from luxvoice.sysint.uinput import combo_by_spec
            return combo_by_spec("meta+pagedown")
        return False

    def maximize_active(self) -> bool:
        from luxvoice.sysint.uinput import combo_by_spec
        return combo_by_spec("meta+pgup")

    # --- Процессы ---------------------------------------------------------

    def kill_pid(self, pid: int, graceful: bool = True) -> bool:
        """Завершить процесс: сначала вежливо, затем настойчиво."""
        import signal
        try:
            if graceful:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    time.sleep(0.05)
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        return True
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError as exc:
            log.warning("Не удалось завершить процесс %s: %s", pid, exc)
            return False

    def running_processes(self) -> dict[str, int]:
        """Карта «имя процесса → pid» для поиска запущенных программ."""
        result: dict[str, int] = {}
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/comm", "r", encoding="utf-8") as handle:
                        name = handle.read().strip().lower()
                    if name:
                        result.setdefault(name, int(entry))
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        return result

    def is_running(self, name: str) -> bool:
        needle = name.lower().strip()
        if not needle:
            return False
        for process in self.running_processes():
            if needle in process:
                return True
        return False


# --- Завершение работы и питание --------------------------------------------

class PowerManager:
    """Питание, блокировка, монитор, спящий режим, выход из сеанса."""

    def __init__(self) -> None:
        self._systemctl = has_command("systemctl")
        self._loginctl = has_command("loginctl")
        self._kde = is_kde()

    def _system(self, action: str) -> tuple[bool, str]:
        """Вызвать действие через systemctl. Может требовать прав."""
        if not self._systemctl:
            return False, "systemctl недоступен"
        code, _, err = _run(["systemctl", action], timeout=15)
        if code == 0:
            return True, ""
        return False, err.strip() or f"код {code}"

    def _session(self) -> str:
        """Идентификатор текущего сеанса для loginctl."""
        try:
            session = os.environ.get("XDG_SESSION_ID")
            if session:
                return session
            code, out, _ = _run(["loginctl", "list-sessions", "--no-legend"], timeout=4)
            if code == 0 and out.strip():
                return out.split()[0]
        except Exception:  # noqa: BLE001
            pass
        return ""

    def shutdown(self, delay_seconds: int = 0) -> tuple[bool, str]:
        """Выключить компьютер."""
        if delay_seconds > 0:
            # Отложенное выключение через shutdown.
            code, _, err = _run(["shutdown", "-h", f"+{max(1, delay_seconds // 60)}"],
                                timeout=8)
            if code == 0:
                return True, ""
            return False, err.strip()
        if self._loginctl:
            session = self._session()
            args = ["loginctl", "poweroff"]
            if session:
                args = ["loginctl", "poweroff"]
            code, _, err = _run(args, timeout=10)
            if code == 0:
                return True, ""
        return self._system("poweroff")

    def reboot(self) -> tuple[bool, str]:
        if self._loginctl:
            code, _, err = _run(["loginctl", "reboot"], timeout=10)
            if code == 0:
                return True, ""
        return self._system("reboot")

    def suspend(self) -> tuple[bool, str]:
        # KDE умеет засыпать без особых прав через свой D-Bus.
        if self._kde:
            code, _, _ = _run([
                "gdbus", "call", "--session",
                "--dest", "org.kde.Solid.PowerManagement",
                "--object-path", "/org/kde/Solid/PowerManagement",
                "--method", "org.kde.Solid.PowerManagement.suspend",
            ], timeout=6)
            if code == 0:
                return True, ""
        if self._loginctl:
            code, _, err = _run(["loginctl", "suspend"], timeout=10)
            if code == 0:
                return True, ""
        return self._system("suspend")

    def hibernate(self) -> tuple[bool, str]:
        if self._loginctl:
            code, _, err = _run(["loginctl", "hibernate"], timeout=10)
            if code == 0:
                return True, ""
        return self._system("hibernate")

    def lock_screen(self) -> tuple[bool, str]:
        """Заблокировать экран."""
        candidates = [
            ["loginctl", "lock-session"],
            ["qdbus", "org.freedesktop.ScreenSaver", "/ScreenSaver",
             "org.freedesktop.ScreenSaver.Lock"],
            ["dbus-send", "--type=method_call", "--dest=org.freedesktop.ScreenSaver",
             "/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
        ]
        for cmd in candidates:
            if not has_command(cmd[0]):
                continue
            code, _, _ = _run(cmd, timeout=6)
            if code == 0:
                return True, ""
        return False, "не удалось заблокировать экран"

    def logout(self) -> tuple[bool, str]:
        if self._kde:
            session = self._session()
            args = ["loginctl", "terminate-session", session] if session else []
            if args:
                code, _, err = _run(args, timeout=8)
                if code == 0:
                    return True, ""
        return False, "выход из сеанса не выполнен"

    def screen_off(self) -> tuple[bool, str]:
        """Погасить монитор."""
        candidates = [
            ["loginctl", "lock-session"],
            ["systemctl", "suspend"],
        ]
        # Безопасный путь: погасить подсветку через D-Bus KDE.
        if self._kde:
            code, _, _ = _run([
                "gdbus", "call", "--session",
                "--dest", "org.kde.Solid.PowerManagement",
                "--object-path", "/org/kde/Solid/PowerManagement",
                "--method", "org.kde.Solid.PowerManagement.setBrightness",
                "0",
            ], timeout=5)
            if code == 0:
                return True, ""
        code, _, err = _run(["xset", "dpms", "force", "off"], timeout=4)
        if code == 0:
            return True, ""
        return False, "не удалось выключить монитор"

    def screen_on(self) -> tuple[bool, str]:
        code, _, _ = _run(["xset", "dpms", "force", "on"], timeout=4)
        if code == 0:
            return True, ""
        # В Wayland монитор включается движением мыши.
        from luxvoice.sysint.uinput import move_by
        move_by(3, 0)
        move_by(-3, 0)
        return True, ""


# --- Единственные экземпляры ------------------------------------------------

_windows: WindowManager | None = None
_power: PowerManager | None = None


def get_windows() -> WindowManager:
    global _windows
    if _windows is None:
        _windows = WindowManager()
    return _windows


def get_power() -> PowerManager:
    global _power
    if _power is None:
        _power = PowerManager()
    return _power