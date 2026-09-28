"""Простые функции управления громкостью через платформенный интерфейс.

Для расширенного управления (микшер приложений, устройства) используйте
AudioManager из audio.py напрямую.
"""

from __future__ import annotations

from luxvoice.sysint.base import platform


def get_volume() -> int:
    """Текущая громкость (0-100)."""
    return platform().get_volume()


def set_volume(level: int) -> bool:
    """Установить громкость (0-100)."""
    return platform().set_volume(level)


def volume_up(amount: int = 10) -> int:
    """Увеличить громкость. Возвращает новый уровень."""
    return platform().volume_up(amount)


def volume_down(amount: int = 10) -> int:
    """Уменьшить громкость. Возвращает новый уровень."""
    return platform().volume_down(amount)


def is_muted() -> bool:
    """Проверить, выключен ли звук."""
    return platform().is_muted()


def toggle_mute() -> bool:
    """Переключить mute. Возвращает новое состояние."""
    return platform().toggle_mute()
