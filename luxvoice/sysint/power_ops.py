"""Кроссплатформенное управление питанием (выключение, перезагрузка, сон).

На Linux: через systemctl/logind
На Windows: через Windows API
"""

from __future__ import annotations

import logging
import sys
import subprocess

log = logging.getLogger(__name__)


def shutdown() -> bool:
    """Выключить компьютер."""
    try:
        if sys.platform == "win32":
            subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
        else:
            subprocess.run(["systemctl", "poweroff"], check=True)
        return True
    except Exception as exc:
        log.debug("Не удалось выключить: %s", exc)
        return False


def reboot() -> bool:
    """Перезагрузить компьютер."""
    try:
        if sys.platform == "win32":
            subprocess.run(["shutdown", "/r", "/t", "0"], check=True)
        else:
            subprocess.run(["systemctl", "reboot"], check=True)
        return True
    except Exception as exc:
        log.debug("Не удалось перезагрузить: %s", exc)
        return False


def suspend() -> bool:
    """Перевести в сон (suspend)."""
    try:
        if sys.platform == "win32":
            # Windows: rundll32 для сна
            import ctypes
            ctypes.windll.PowrProf.SetSuspendState(False, True, False)
        else:
            subprocess.run(["systemctl", "suspend"], check=True)
        return True
    except Exception as exc:
        log.debug("Не удалось перевести в сон: %s", exc)
        return False


def hibernate() -> bool:
    """Гибернация."""
    try:
        if sys.platform == "win32":
            subprocess.run(["shutdown", "/h"], check=True)
        else:
            subprocess.run(["systemctl", "hibernate"], check=True)
        return True
    except Exception as exc:
        log.debug("Не удалось гибернировать: %s", exc)
        return False


def logout() -> bool:
    """Выйти из системы."""
    try:
        if sys.platform == "win32":
            subprocess.run(["shutdown", "/l"], check=True)
        else:
            # Попытка выйти через разные DE
            for cmd in [["loginctl", "terminate-user", ""], ["gnome-session-quit"], ["qdbus", "org.kde.ksmserver", "/KSMServer", "logout", "0", "0", "0"]]:
                try:
                    subprocess.run(cmd, check=True, timeout=5)
                    return True
                except:
                    continue
        return False
    except Exception as exc:
        log.debug("Не удалось выйти: %s", exc)
        return False


def lock_screen() -> bool:
    """Заблокировать экран."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return True
        else:
            # Linux: loginctl или dbus
            for cmd in [["loginctl", "lock-session"], 
                       ["dbus-send", "--type=method_call", "--dest=org.freedesktop.ScreenSaver",
                        "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"]]:
                try:
                    subprocess.run(cmd, check=True, timeout=5)
                    return True
                except:
                    continue
            return False
    except Exception as exc:
        log.debug("Не удалось заблокировать экран: %s", exc)
        return False
