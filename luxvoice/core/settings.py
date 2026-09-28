"""Хранилище настроек: загрузка, проверка, атомарная запись, резервные копии.

Гарантии:
  * запись атомарная (временный файл + переименование) — обрыв питания
    не оставит битый конфиг;
  * при чтении битого файла автоматически подхватывается последняя
    резервная копия;
  * значения всегда приводятся к типам схемы, чужие ключи сохраняются
    в отдельной секции и не теряются при обновлении программы;
  * неверное значение откатывается к значению по умолчанию.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from luxvoice.core import paths, schema
from luxvoice.core.events import SETTINGS_CHANGED, bus

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Поле, куда складываются ключи, которых нет в текущей схеме.
_UNKNOWN_KEY = "_unknown"


def _atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    """Записать файл так, чтобы он никогда не оставался половинчатым."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
        # Синхронизируем каталог, чтобы переименование пережило сбой.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class Settings:
    """Потокобезопасное хранилище настроек с уведомлением об изменениях."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else paths.config_file()
        self._lock = threading.RLock()
        self._values: dict[str, Any] = schema.defaults()
        self._unknown: dict[str, Any] = {}
        self._dirty = False
        self._last_save = 0.0
        self._listeners: list[Any] = []

    # --- Загрузка ---------------------------------------------------------

    def load(self) -> None:
        """Прочитать конфигурацию с диска с автоматическим откатом."""
        paths.ensure_dirs()
        raw = self._read_json(self._path)

        if raw is None:
            # Пробуем последнюю резервную копию.
            raw = self._read_latest_backup()
            if raw is not None:
                log.warning("Конфигурация повреждена — восстановлена из резервной копии")

        if not isinstance(raw, dict):
            raw = {}

        self._apply_raw(raw)
        log.info("Настройки загружены: %s", self._path)

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            log.error("Не удалось прочитать %s: %s", path, exc)
            return None

    def _read_latest_backup(self) -> dict[str, Any] | None:
        directory = paths.backups_dir()
        try:
            candidates = sorted(directory.glob("config-*.json"), reverse=True)
        except OSError:
            return None
        for candidate in candidates[:5]:
            data = self._read_json(candidate)
            if data is not None:
                return data
        return None

    def _apply_raw(self, raw: dict[str, Any]) -> None:
        """Разложить прочитанное по схеме, сохранив неизвестные ключи."""
        with self._lock:
            values = schema.defaults()
            unknown: dict[str, Any] = {}
            stored_unknown = raw.get(_UNKNOWN_KEY)
            if isinstance(stored_unknown, dict):
                unknown.update(stored_unknown)

            for key, value in raw.items():
                if key in (_UNKNOWN_KEY, "schema_version"):
                    continue
                setting = schema.get_setting(key)
                if setting is None:
                    # Ключ из будущей версии — сохраняем как есть.
                    unknown[key] = value
                    continue
                values[key] = schema.coerce(setting, value)

            # Заполняем зависимые поля, которые могли появиться в новой версии.
            for setting in schema.all_settings():
                if setting.key not in values:
                    values[setting.key] = setting.default

            self._values = values
            self._unknown = unknown
            self._dirty = False

    # --- Доступ -----------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._values:
                return self._values[key]
        setting = schema.get_setting(key)
        if setting is not None:
            return setting.default
        return default

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def set(self, key: str, value: Any, save: bool = True, notify: bool = True) -> bool:
        """Установить значение. Возвращает True, если оно изменилось."""
        setting = schema.get_setting(key)
        if setting is not None:
            value = schema.coerce(setting, value)

        with self._lock:
            old = self._values.get(key, _MISSING)
            if old is not _MISSING and old == value:
                return False
            self._values[key] = value
            self._dirty = True

        if save:
            self.save()
        if notify:
            bus.publish(SETTINGS_CHANGED, keys=[key], values={key: value})
        return True

    def update(self, values: dict[str, Any], save: bool = True, notify: bool = True) -> list[str]:
        """Массовое обновление. Возвращает список реально изменившихся ключей."""
        changed: list[str] = []
        with self._lock:
            for key, value in values.items():
                setting = schema.get_setting(key)
                if setting is not None:
                    value = schema.coerce(setting, value)
                old = self._values.get(key, _MISSING)
                if old is not _MISSING and old == value:
                    continue
                self._values[key] = value
                changed.append(key)
            if changed:
                self._dirty = True

        if changed and save:
            self.save()
        if changed and notify:
            bus.publish(SETTINGS_CHANGED, keys=changed, values={k: self.get(k) for k in changed})
        return changed

    def reset(self, key: str, save: bool = True) -> Any:
        setting = schema.get_setting(key)
        if setting is None:
            return None
        self.set(key, setting.default, save=save)
        return setting.default

    def reset_section(self, section: str, save: bool = True) -> list[str]:
        changed = []
        for setting in schema.settings_in(section):
            if self.set(setting.key, setting.default, save=False, notify=False):
                changed.append(setting.key)
        if changed and save:
            self.save()
        if changed:
            bus.publish(SETTINGS_CHANGED, keys=changed, values={k: self.get(k) for k in changed})
        return changed

    def reset_all(self, save: bool = True) -> None:
        with self._lock:
            self._values = schema.defaults()
            self._dirty = True
        if save:
            self.save()
        bus.publish(SETTINGS_CHANGED, keys=[], values={}, full=True)

    def as_dict(self, include_unknown: bool = False) -> dict[str, Any]:
        with self._lock:
            out = dict(self._values)
            if include_unknown and self._unknown:
                out[_UNKNOWN_KEY] = dict(self._unknown)
        return out

    def all_keys(self) -> list[str]:
        with self._lock:
            return list(self._values.keys())

    def is_dirty(self) -> bool:
        with self._lock:
            return self._dirty

    # --- Зависимые списки -------------------------------------------------

    def visible_settings(self, section: str, level: str = "basic") -> list[schema.Setting]:
        """Настройки раздела с учётом текущего уровня детализации и зависимостей."""
        levels = {
            "basic": ("basic",),
            "advanced": ("basic", "advanced"),
            "expert": ("basic", "advanced", "expert"),
        }[level]

        out: list[schema.Setting] = []
        for setting in schema.settings_in(section):
            if setting.level not in levels:
                continue
            if not self._dependency_ok(setting):
                continue
            out.append(setting)
        return out

    def _dependency_ok(self, setting: schema.Setting) -> bool:
        """Проверить depends_on: поле показывается только при нужном значении."""
        if not setting.depends_on:
            return True
        key, expected = setting.depends_on
        value = self.get(key)
        if isinstance(value, bool):
            value = "true" if value else "false"
        return str(value) == str(expected)

    def has_choice(self, key: str) -> bool:
        setting = schema.get_setting(key)
        return bool(setting and setting.choices and not setting.dynamic)

    # --- Сохранение -------------------------------------------------------

    def save(self, force: bool = False) -> bool:
        """Записать конфигурацию. Возвращает True при успехе."""
        with self._lock:
            if not self._dirty and not force:
                return True
            payload = dict(self._values)
            if self._unknown:
                payload[_UNKNOWN_KEY] = dict(self._unknown)
            payload["schema_version"] = SCHEMA_VERSION

        # Ключи API не должны лежать в открытом виде больше, чем нужно.
        # Файл создаётся с правами 600 (см. _atomic_write).
        try:
            with self._lock:
                if self._dirty:
                    self._backup_before_save()
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            _atomic_write(self._path, text, mode=0o600)
            with self._lock:
                self._dirty = False
                self._last_save = time.time()
            log.debug("Настройки сохранены (%d ключей)", len(payload))
            return True
        except OSError as exc:
            log.error("Не удалось сохранить настройки: %s", exc)
            return False

    def save_soon(self, delay: float = 1.5) -> None:
        """Отложенное сохранение — для частых правок в интерфейсе."""
        with self._lock:
            self._dirty = True
        timer = getattr(self, "_timer", None)
        if timer is not None and timer.is_alive():
            timer.cancel()
        timer = threading.Timer(delay, self.save)
        timer.daemon = True
        self._timer = timer
        timer.start()

    # --- Резервные копии --------------------------------------------------

    def _backup_before_save(self) -> None:
        """Сохранить предыдущую версию перед перезаписью."""
        if not self.get("adv.autobackup", True):
            return
        if not self._path.exists():
            return
        directory = paths.backups_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = directory / f"config-{stamp}.json"
            if not target.exists():
                shutil.copy2(self._path, target)
            self._prune_backups()
        except OSError as exc:
            log.debug("Резервная копия не создана: %s", exc)

    def _prune_backups(self) -> None:
        keep = int(self.get("adv.backup_count", 10) or 10)
        directory = paths.backups_dir()
        try:
            files = sorted(directory.glob("config-*.json"))
        except OSError:
            return
        for old in files[:-keep] if len(files) > keep else []:
            try:
                old.unlink()
            except OSError:
                pass

    def make_backup(self, label: str = "") -> Path | None:
        """Создать именованную копию по требованию (перед экспортом, правкой)."""
        directory = paths.backups_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            suffix = f"-{label}" if label else ""
            target = directory / f"config-{stamp}{suffix}.json"
            self.save()
            if self._path.exists():
                shutil.copy2(self._path, target)
            return target
        except OSError as exc:
            log.error("Не удалось создать копию: %s", exc)
            return None

    def backups(self) -> list[Path]:
        try:
            return sorted(paths.backups_dir().glob("config-*.json"), reverse=True)
        except OSError:
            return []

    def restore_backup(self, path: Path) -> bool:
        data = self._read_json(Path(path))
        if data is None:
            return False
        self._apply_raw(data)
        self._dirty = True
        self.save()
        bus.publish(SETTINGS_CHANGED, keys=[], values={}, full=True)
        return True

    # --- Экспорт и импорт -------------------------------------------------

    def export_dict(self, include_secrets: bool = False) -> dict[str, Any]:
        """Выгрузка настроек. Ключи API по умолчанию не попадают в файл."""
        out: dict[str, Any] = {}
        for setting in schema.all_settings():
            if not include_secrets:
                # Секреты и связанные с ними поля в файл не попадают.
                if setting.kind == schema.SECRET:
                    continue
                if setting.key in ("ai.keys",) or "token" in setting.key:
                    continue
            out[setting.key] = self.get(setting.key)
        out["schema_version"] = SCHEMA_VERSION
        out["_exported"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return out

    def import_dict(self, data: dict[str, Any], only_keys: Iterable[str] | None = None) -> list[str]:
        if not isinstance(data, dict):
            return []
        allowed = set(only_keys) if only_keys is not None else None
        values: dict[str, Any] = {}
        for key, value in data.items():
            if key in ("schema_version", "_exported", _UNKNOWN_KEY):
                continue
            if allowed is not None and key not in allowed:
                continue
            if schema.get_setting(key) is None:
                continue
            values[key] = value
        return self.update(values)

    # --- Подписка на изменения -------------------------------------------

    def on_change(self, handler: Any) -> None:
        """Локальный обработчик изменений (в дополнение к шине событий)."""
        self._listeners.append(handler)

    # --- Удобные типизированные геттеры ----------------------------------

    def flag(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        return bool(value)

    def number(self, key: str, default: float = 0) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def integer(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get(key, default)))
        except (TypeError, ValueError):
            return default

    def text(self, key: str, default: str = "") -> str:
        value = self.get(key, default)
        return "" if value is None else str(value)

    def items(self, key: str) -> list[str]:
        value = self.get(key, [])
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [p.strip() for p in value.replace("\n", ",").split(",") if p.strip()]
        return []


_MISSING = object()

# Единственный экземпляр на процесс.
_instance: Settings | None = None
_instance_lock = threading.Lock()


def get_settings(reload: bool = False) -> Settings:
    """Получить общие настройки приложения."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Settings()
            _instance.load()
        elif reload:
            _instance.load()
        return _instance