"""История выполненных команд: база SQLite с поиском и статистикой.

Зачем база, а не файл: истории свойственны частые записи и выборки
с фильтрами. SQLite даёт это без внешних зависимостей и переживает
внезапное завершение процесса.

Хранится: что услышано, какая команда сработала, оценка совпадения,
результат каждого шага, время выполнения. Это нужно и для разбора
ошибок распознавания, и для подсказок «вы это имели в виду».
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from luxvoice.core import paths

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL    NOT NULL,
    phrase      TEXT    NOT NULL DEFAULT '',
    command_id  TEXT    NOT NULL DEFAULT '',
    command     TEXT    NOT NULL DEFAULT '',
    ok          INTEGER NOT NULL DEFAULT 1,
    cancelled   INTEGER NOT NULL DEFAULT 0,
    score       REAL    NOT NULL DEFAULT 0,
    elapsed     REAL    NOT NULL DEFAULT 0,
    steps       TEXT    NOT NULL DEFAULT '[]',
    output      TEXT    NOT NULL DEFAULT '',
    error       TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'voice'
);

CREATE INDEX IF NOT EXISTS idx_history_time ON history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_history_command ON history(command_id);
CREATE INDEX IF NOT EXISTS idx_history_phrase ON history(phrase);

CREATE TABLE IF NOT EXISTS phrases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT    NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    matched     INTEGER NOT NULL DEFAULT 0,
    last_seen   REAL    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_phrases_text ON phrases(text);

CREATE TABLE IF NOT EXISTS stats (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""


@dataclass
class HistoryEntry:
    """Одна запись истории."""

    id: int = 0
    timestamp: float = 0.0
    phrase: str = ""
    command_id: str = ""
    command: str = ""
    ok: bool = True
    cancelled: bool = False
    score: float = 0.0
    elapsed: float = 0.0
    steps: list[tuple[str, bool, str]] = field(default_factory=list)
    output: str = ""
    error: str = ""
    source: str = "voice"

    @property
    def when(self) -> str:
        return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(self.timestamp))

    @property
    def status(self) -> str:
        if self.cancelled:
            return "отменено"
        if not self.ok:
            return "ошибка"
        return "выполнено"

    def summary(self) -> str:
        if self.error and not self.ok:
            return self.error
        if self.steps:
            done = sum(1 for _, ok, _ in self.steps if ok)
            return f"шагов: {done} из {len(self.steps)}"
        return self.output or "выполнено"


class HistoryStore:
    """Хранилище истории команд."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else paths.history_db()
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._max_entries = 2000
        self._enabled = True
        self._pending = 0

    # --- Подключение ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection

        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._path), timeout=10,
                                     check_same_thread=False)
        connection.row_factory = sqlite3.Row
        # WAL-режим: чтение не блокирует запись — интерфейс остаётся
        # отзывчивым, пока идёт запись истории.
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error as exc:
            log.debug("Не удалось настроить режим журнала: %s", exc)
        connection.executescript(SCHEMA)
        connection.commit()
        self._connection = connection
        return connection

    def configure(self, max_entries: int, enabled: bool = True) -> None:
        """Применить настройки хранения."""
        with self._lock:
            self._max_entries = max(0, int(max_entries))
            self._enabled = bool(enabled)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.commit()
                    self._connection.close()
                except sqlite3.Error:
                    pass
                self._connection = None

    # --- Запись -----------------------------------------------------------

    def add(self, entry: HistoryEntry) -> int:
        """Добавить запись. Возвращает её номер."""
        if not self._enabled:
            return 0

        with self._lock:
            try:
                connection = self._connect()
                cursor = connection.execute(
                    """INSERT INTO history
                       (timestamp, phrase, command_id, command, ok, cancelled,
                        score, elapsed, steps, output, error, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.timestamp or time.time(),
                        entry.phrase[:500],
                        entry.command_id,
                        entry.command[:200],
                        int(entry.ok),
                        int(entry.cancelled),
                        float(entry.score),
                        float(entry.elapsed),
                        json.dumps(entry.steps, ensure_ascii=False)[:8000],
                        entry.output[:2000],
                        entry.error[:1000],
                        entry.source,
                    ),
                )
                connection.commit()
                entry.id = int(cursor.lastrowid or 0)

                # Учёт фразы для статистики распознавания.
                if entry.phrase:
                    self._track_phrase(connection, entry.phrase, bool(entry.command_id))
                self._pending += 1

                # Чистим историю не на каждой записи.
                if self._pending >= 50 and self._max_entries > 0:
                    self._pending = 0
                    self._trim(connection)
                return entry.id
            except sqlite3.Error as exc:
                log.error("Не удалось записать в историю: %s", exc)
                return 0

    def _track_phrase(self, connection: sqlite3.Connection, text: str,
                      matched: bool) -> None:
        """Обновить статистику по фразе."""
        from luxvoice.core.store import normalize_phrase
        cleaned = normalize_phrase(text)
        if not cleaned:
            return
        try:
            connection.execute(
                """INSERT INTO phrases (text, count, matched, last_seen)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(text) DO UPDATE SET
                       count = count + 1,
                       matched = matched + ?,
                       last_seen = ?""",
                (cleaned, int(matched), time.time(), int(matched), time.time()),
            )
            connection.commit()
        except sqlite3.Error as exc:
            log.debug("Не удалось обновить статистику фраз: %s", exc)

    def _trim(self, connection: sqlite3.Connection) -> None:
        """Удалить записи сверх лимита и старые по сроку хранения."""
        if self._max_entries <= 0:
            return
        try:
            connection.execute(
                """DELETE FROM history WHERE id NOT IN (
                       SELECT id FROM history ORDER BY timestamp DESC LIMIT ?
                   )""",
                (self._max_entries,),
            )
            connection.commit()
        except sqlite3.Error as exc:
            log.debug("Не удалось очистить историю: %s", exc)

    def trim_by_days(self, days: int) -> int:
        """Удалить записи старше указанного числа дней. Возвращает число удалённых."""
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self._lock:
            try:
                connection = self._connect()
                cursor = connection.execute(
                    "DELETE FROM history WHERE timestamp < ?", (cutoff,))
                connection.commit()
                return cursor.rowcount or 0
            except sqlite3.Error as exc:
                log.error("Не удалось удалить старые записи: %s", exc)
                return 0

    # --- Чтение -----------------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row) -> HistoryEntry:
        try:
            steps = json.loads(row["steps"] or "[]")
            if not isinstance(steps, list):
                steps = []
            steps = [tuple(s) if isinstance(s, list) else (str(s), True, "")
                     for s in steps]
        except (json.JSONDecodeError, TypeError):
            steps = []

        return HistoryEntry(
            id=int(row["id"]),
            timestamp=float(row["timestamp"]),
            phrase=str(row["phrase"]),
            command_id=str(row["command_id"]),
            command=str(row["command"]),
            ok=bool(row["ok"]),
            cancelled=bool(row["cancelled"]),
            score=float(row["score"]),
            elapsed=float(row["elapsed"]),
            steps=steps,
            output=str(row["output"]),
            error=str(row["error"]),
            source=str(row["source"]) if "source" in row.keys() else "voice",
        )

    def recent(self, limit: int = 50, offset: int = 0) -> list[HistoryEntry]:
        """Последние записи."""
        with self._lock:
            try:
                connection = self._connect()
                rows = connection.execute(
                    "SELECT * FROM history ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (max(1, limit), max(0, offset)),
                ).fetchall()
                return [self._row_to_entry(row) for row in rows]
            except sqlite3.Error as exc:
                log.error("Не удалось прочитать историю: %s", exc)
                return []

    def search(self, query: str, limit: int = 100) -> list[HistoryEntry]:
        """Найти записи по фразе, команде или тексту ошибки."""
        needle = f"%{query.strip()}%"
        with self._lock:
            try:
                connection = self._connect()
                rows = connection.execute(
                    """SELECT * FROM history
                       WHERE phrase LIKE ? OR command LIKE ?
                          OR output LIKE ? OR error LIKE ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (needle, needle, needle, needle, max(1, limit)),
                ).fetchall()
                return [self._row_to_entry(row) for row in rows]
            except sqlite3.Error as exc:
                log.error("Не удалось выполнить поиск: %s", exc)
                return []

    def for_command(self, command_id: str, limit: int = 50) -> list[HistoryEntry]:
        with self._lock:
            try:
                connection = self._connect()
                rows = connection.execute(
                    """SELECT * FROM history WHERE command_id = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (command_id, max(1, limit)),
                ).fetchall()
                return [self._row_to_entry(row) for row in rows]
            except sqlite3.Error:
                return []

    def count(self) -> int:
        with self._lock:
            try:
                connection = self._connect()
                row = connection.execute("SELECT COUNT(*) AS n FROM history").fetchone()
                return int(row["n"]) if row else 0
            except sqlite3.Error:
                return 0

    def failed_phrases(self, limit: int = 20) -> list[tuple[str, int]]:
        """Фразы, которые часто не распознавались — подсказка для настройки."""
        with self._lock:
            try:
                connection = self._connect()
                rows = connection.execute(
                    """SELECT text, count FROM phrases
                       WHERE matched = 0 AND count >= 2
                       ORDER BY count DESC LIMIT ?""",
                    (max(1, limit),),
                ).fetchall()
                return [(str(row["text"]), int(row["count"])) for row in rows]
            except sqlite3.Error:
                return []

    def top_phrases(self, limit: int = 20) -> list[tuple[str, int, int]]:
        """Самые частые фразы: (текст, всего, распознано успешно)."""
        with self._lock:
            try:
                connection = self._connect()
                rows = connection.execute(
                    """SELECT text, count, matched FROM phrases
                       ORDER BY count DESC LIMIT ?""",
                    (max(1, limit),),
                ).fetchall()
                return [(str(r["text"]), int(r["count"]), int(r["matched"]))
                        for r in rows]
            except sqlite3.Error:
                return []

    def statistics(self, days: int = 30) -> dict[str, Any]:
        """Сводка по истории за период."""
        cutoff = time.time() - max(1, days) * 86400
        with self._lock:
            try:
                connection = self._connect()
                row = connection.execute(
                    """SELECT COUNT(*) AS total,
                              SUM(ok) AS ok_count,
                              SUM(cancelled) AS cancelled_count,
                              AVG(elapsed) AS avg_time,
                              MAX(timestamp) AS last
                       FROM history WHERE timestamp >= ?""",
                    (cutoff,),
                ).fetchone()
                if row is None:
                    return {}

                total = int(row["total"] or 0)
                ok_count = int(row["ok_count"] or 0)

                top = connection.execute(
                    """SELECT command, COUNT(*) AS n FROM history
                       WHERE timestamp >= ? AND command != ''
                       GROUP BY command ORDER BY n DESC LIMIT 5""",
                    (cutoff,),
                ).fetchall()

                by_hour = connection.execute(
                    """SELECT CAST(strftime('%H', timestamp, 'unixepoch', 'localtime')
                                   AS INTEGER) AS hour,
                              COUNT(*) AS n
                       FROM history WHERE timestamp >= ?
                       GROUP BY hour ORDER BY hour""",
                    (cutoff,),
                ).fetchall()

                return {
                    "total": total,
                    "ok": ok_count,
                    "failed": total - ok_count,
                    "cancelled": int(row["cancelled_count"] or 0),
                    "success_rate": round(ok_count * 100 / total, 1) if total else 0.0,
                    "avg_seconds": round(float(row["avg_time"] or 0), 2),
                    "last_run": float(row["last"] or 0),
                    "top_commands": [(str(r["command"]), int(r["n"])) for r in top],
                    "by_hour": [(int(r["hour"]), int(r["n"])) for r in by_hour],
                    "days": days,
                }
            except sqlite3.Error as exc:
                log.error("Не удалось собрать статистику: %s", exc)
                return {}

    def export_rows(self, limit: int = 10000) -> list[dict[str, Any]]:
        """Выгрузка истории для сохранения в файл."""
        result: list[dict[str, Any]] = []
        for entry in self.recent(limit=limit):
            result.append({
                "when": entry.when,
                "phrase": entry.phrase,
                "command": entry.command,
                "ok": entry.ok,
                "cancelled": entry.cancelled,
                "score": round(entry.score, 1),
                "elapsed": round(entry.elapsed, 3),
                "error": entry.error,
                "steps": entry.steps,
            })
        return result

    # --- Очистка ----------------------------------------------------------

    def clear(self) -> int:
        """Полностью очистить историю."""
        with self._lock:
            try:
                connection = self._connect()
                cursor = connection.execute("DELETE FROM history")
                connection.commit()
                return cursor.rowcount or 0
            except sqlite3.Error as exc:
                log.error("Не удалось очистить историю: %s", exc)
                return 0

    def clear_phrase_stats(self) -> None:
        with self._lock:
            try:
                connection = self._connect()
                connection.execute("DELETE FROM phrases")
                connection.commit()
            except sqlite3.Error:
                pass

    def optimize(self) -> None:
        """Уплотнить базу — полезно после больших удалений."""
        with self._lock:
            try:
                connection = self._connect()
                connection.execute("VACUUM")
            except sqlite3.Error:
                pass


# --- Одиночный экземпляр ----------------------------------------------------

_history: HistoryStore | None = None
_lock = threading.Lock()


def get_history(settings=None) -> HistoryStore:
    global _history
    with _lock:
        if _history is None:
            _history = HistoryStore()
            if settings is not None:
                _history.configure(
                    settings.number("exec.history_size", 2000),
                    settings.flag("privacy.save_history", True),
                )
        elif settings is not None:
            _history.configure(
                settings.number("exec.history_size", 2000),
                settings.flag("privacy.save_history", True),
            )
        return _history