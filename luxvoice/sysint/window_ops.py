"""Кроссплатформенные функции управления окнами через платформенный интерфейс."""

from __future__ import annotations

from typing import Optional

from luxvoice.sysint.base import platform, WindowInfo


def get_windows() -> list[WindowInfo]:
    """Получить список всех окон."""
    return platform().get_windows()


def get_active_window() -> Optional[WindowInfo]:
    """Получить активное окно."""
    return platform().get_active_window()


def close_window(window_id: str = "") -> bool:
    """Закрыть окно (текущее, если window_id пустой)."""
    return platform().close_window(window_id)


def minimize_window(window_id: str = "") -> bool:
    """Свернуть окно."""
    return platform().minimize_window(window_id)


def maximize_window(window_id: str = "") -> bool:
    """Развернуть окно."""
    return platform().maximize_window(window_id)


def minimize_all() -> bool:
    """Свернуть все окна."""
    return platform().minimize_all()


def show_desktop() -> bool:
    """Показать рабочий стол."""
    return platform().show_desktop()
