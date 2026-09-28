"""Кроссплатформенный ввод с клавиатуры и мыши.

На Linux: использует uinput через sysint/uinput.py
На Windows: использует pyautogui
На macOS: использует pyautogui (пока не реализовано)
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def input_available() -> bool:
    """Проверить, доступен ли виртуальный ввод."""
    if sys.platform == "win32":
        try:
            import pyautogui  # noqa: F401
            return True
        except ImportError:
            return False
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import input_available as linux_input_available
        return linux_input_available()
    return False


def press_key(key: str, modifiers: list[str] | None = None) -> bool:
    """Нажать клавишу с модификаторами."""
    if sys.platform == "win32":
        return _win_press_key(key, modifiers)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import press_key as linux_press_key
        return linux_press_key(key, modifiers)
    return False


def type_string(text: str) -> bool:
    """Напечатать строку."""
    if sys.platform == "win32":
        return _win_type_string(text)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import type_string as linux_type_string
        return linux_type_string(text)
    return False


def click_mouse(button: str = "left", count: int = 1) -> bool:
    """Клик мышью."""
    if sys.platform == "win32":
        return _win_click_mouse(button, count)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import click_mouse as linux_click_mouse
        return linux_click_mouse(button, count)
    return False


def move_by(dx: int, dy: int) -> bool:
    """Переместить курсор на относительное расстояние."""
    if sys.platform == "win32":
        return _win_move_by(dx, dy)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import move_by as linux_move_by
        return linux_move_by(dx, dy)
    return False


def move_to(x: int, y: int) -> bool:
    """Переместить курсор в абсолютную позицию."""
    if sys.platform == "win32":
        return _win_move_to(x, y)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import move_to as linux_move_to
        return linux_move_to(x, y)
    return False


def scroll_by(dx: int = 0, dy: int = 0) -> bool:
    """Прокрутка."""
    if sys.platform == "win32":
        return _win_scroll_by(dx, dy)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import scroll_by as linux_scroll_by
        return linux_scroll_by(dx, dy)
    return False


def drag_by(dx: int, dy: int, button: str = "left") -> bool:
    """Перетащить на относительное расстояние."""
    if sys.platform == "win32":
        return _win_drag_by(dx, dy, button)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import drag_by as linux_drag_by
        return linux_drag_by(dx, dy, button)
    return False


def hold_button(button: str = "left", duration: float = 1.0) -> bool:
    """Удержать кнопку мыши."""
    if sys.platform == "win32":
        return _win_hold_button(button, duration)
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import hold_button as linux_hold_button
        return linux_hold_button(button, duration)
    return False


def describe_keys() -> str:
    """Описание доступных клавиш."""
    if sys.platform == "win32":
        return _win_describe_keys()
    elif sys.platform == "linux":
        from luxvoice.sysint.uinput import describe_keys as linux_describe_keys
        return linux_describe_keys()
    return "Ввод недоступен"


# --- Windows реализация через pyautogui ---

def _win_press_key(key: str, modifiers: list[str] | None = None) -> bool:
    """Нажать клавишу в Windows."""
    try:
        import pyautogui
        
        # Преобразование имен клавиш
        key_map = {
            "return": "enter",
            "kp_enter": "enter",
            "escape": "esc",
            "backspace": "backspace",
            "delete": "delete",
            "tab": "tab",
            "space": "space",
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "home": "home",
            "end": "end",
            "pageup": "pageup",
            "pagedown": "pagedown",
            "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
            "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
            "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
        }
        
        mod_map = {
            "ctrl": "ctrl",
            "control": "ctrl",
            "alt": "alt",
            "shift": "shift",
            "meta": "win",
            "super": "win",
        }
        
        pyautogui_key = key_map.get(key.lower(), key.lower())
        
        if modifiers:
            pyautogui_mods = [mod_map.get(m.lower(), m.lower()) for m in modifiers]
            pyautogui.hotkey(*pyautogui_mods, pyautogui_key)
        else:
            pyautogui.press(pyautogui_key)
        
        return True
    except Exception as exc:
        log.debug("Не удалось нажать клавишу: %s", exc)
        return False


def _win_type_string(text: str) -> bool:
    """Напечатать строку в Windows."""
    try:
        import pyautogui
        pyautogui.write(text, interval=0.02)
        return True
    except Exception as exc:
        log.debug("Не удалось напечатать строку: %s", exc)
        return False


def _win_click_mouse(button: str = "left", count: int = 1) -> bool:
    """Клик мышью в Windows."""
    try:
        import pyautogui
        pyautogui.click(button=button, clicks=count)
        return True
    except Exception as exc:
        log.debug("Не удалось кликнуть мышью: %s", exc)
        return False


def _win_move_by(dx: int, dy: int) -> bool:
    """Переместить курсор в Windows."""
    try:
        import pyautogui
        pyautogui.move(dx, dy)
        return True
    except Exception as exc:
        log.debug("Не удалось переместить курсор: %s", exc)
        return False


def _win_move_to(x: int, y: int) -> bool:
    """Переместить курсор в абсолютную позицию в Windows."""
    try:
        import pyautogui
        pyautogui.moveTo(x, y)
        return True
    except Exception as exc:
        log.debug("Не удалось переместить курсор: %s", exc)
        return False


def _win_scroll_by(dx: int = 0, dy: int = 0) -> bool:
    """Прокрутка в Windows."""
    try:
        import pyautogui
        if dy != 0:
            pyautogui.scroll(dy)
        if dx != 0:
            pyautogui.hscroll(dx)
        return True
    except Exception as exc:
        log.debug("Не удалось прокрутить: %s", exc)
        return False


def _win_drag_by(dx: int, dy: int, button: str = "left") -> bool:
    """Перетащить в Windows."""
    try:
        import pyautogui
        pyautogui.drag(dx, dy, button=button, duration=0.5)
        return True
    except Exception as exc:
        log.debug("Не удалось перетащить: %s", exc)
        return False


def _win_hold_button(button: str = "left", duration: float = 1.0) -> bool:
    """Удержать кнопку в Windows."""
    try:
        import pyautogui
        import time
        pyautogui.mouseDown(button=button)
        time.sleep(duration)
        pyautogui.mouseUp(button=button)
        return True
    except Exception as exc:
        log.debug("Не удалось удержать кнопку: %s", exc)
        return False


def _win_describe_keys() -> str:
    """Описание клавиш в Windows."""
    return "Доступны все стандартные клавиши через pyautogui"
