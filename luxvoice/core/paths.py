"""Пути файловой системы по стандарту XDG.

Все каталоги создаются лениво. Пользователь может переопределить корень
переменной окружения LUXVOICE_HOME (удобно для портативного режима).
"""

from __future__ import annotations

import os
from pathlib import Path

from luxvoice import __app_id__

# Имя каталога внутри XDG-базы.
_DIR_NAME = __app_id__.capitalize()  # Luxvoice -> используем как есть
_DIR_NAME = "luxvoice"


def _xdg(env_var: str, default: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / default


def _portable_root() -> Path | None:
    """Портативный режим: всё внутри одной папки рядом с проектом."""
    raw = os.environ.get("LUXVOICE_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return None


_ROOT = _portable_root()


def config_dir() -> Path:
    if _ROOT:
        return _ROOT / "config"
    return _xdg("XDG_CONFIG_HOME", ".config") / _DIR_NAME


def data_dir() -> Path:
    if _ROOT:
        return _ROOT / "data"
    return _xdg("XDG_DATA_HOME", ".local/share") / _DIR_NAME


def cache_dir() -> Path:
    if _ROOT:
        return _ROOT / "cache"
    return _xdg("XDG_CACHE_HOME", ".cache") / _DIR_NAME


def state_dir() -> Path:
    if _ROOT:
        return _ROOT / "state"
    return _xdg("XDG_STATE_HOME", ".local/state") / _DIR_NAME


def runtime_dir() -> Path:
    """Каталог для сокетов и pid-файлов текущей сессии."""
    raw = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(raw) if raw else Path(f"/tmp/luxvoice-{os.getuid()}")
    return base / _DIR_NAME


def log_dir() -> Path:
    return state_dir() / "logs"


# --- Конкретные файлы -------------------------------------------------------


def config_file() -> Path:
    return config_dir() / "config.json"


def commands_file() -> Path:
    return data_dir() / "commands.json"


def history_db() -> Path:
    return data_dir() / "history.sqlite3"


def memory_file() -> Path:
    return data_dir() / "ai_memory.json"


def models_dir() -> Path:
    """Модели распознавания речи."""
    return data_dir() / "models"


def voices_dir() -> Path:
    """Голосовые модели синтеза речи."""
    return data_dir() / "voices"


def packs_dir() -> Path:
    """Установленные паки команд и пакеты дополнений."""
    return data_dir() / "packs"


def plugins_dir() -> Path:
    """Пользовательские плагины."""
    return data_dir() / "plugins"


def backups_dir() -> Path:
    return data_dir() / "backups"


def exports_dir() -> Path:
    return data_dir() / "exports"


def tts_cache_dir() -> Path:
    return cache_dir() / "tts"


def assets_dir() -> Path:
    """Каталог с ресурсами, поставляемыми вместе с программой."""
    here = Path(__file__).resolve().parent.parent
    return here / "resources"


def pid_file() -> Path:
    return runtime_dir() / "luxvoice.pid"


def socket_file() -> Path:
    return runtime_dir() / "luxvoice.sock"


def autostart_file() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "luxvoice.desktop"


# --- Служебное --------------------------------------------------------------

# Каталоги, создаваемые при первом обращении.
_MANAGED_DIRS = (
    config_dir,
    data_dir,
    cache_dir,
    state_dir,
    log_dir,
    models_dir,
    voices_dir,
    packs_dir,
    plugins_dir,
    backups_dir,
    exports_dir,
    tts_cache_dir,
)

_created = False


def ensure_dirs() -> None:
    """Создать все рабочие каталоги. Безопасно вызывать многократно."""
    global _created
    if _created:
        return
    for factory in _MANAGED_DIRS:
        try:
            factory().mkdir(parents=True, exist_ok=True)
        except OSError:
            # Каталог может быть недоступен в урезанном окружении —
            # не роняем запуск из-за этого.
            pass
    try:
        runtime_dir().mkdir(parents=True, exist_ok=True)
        os.chmod(runtime_dir(), 0o700)
    except OSError:
        pass
    _created = True


def is_portable() -> bool:
    return _ROOT is not None