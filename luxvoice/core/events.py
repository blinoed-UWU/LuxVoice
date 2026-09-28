"""Простая потокобезопасная шина событий.

Компоненты приложения не знают друг о друге: распознаватель публикует текст,
исполнитель — результат, интерфейс слушает. Ошибка одного подписчика
не должна ломать остальных, поэтому исключения перехватываются и логируются.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# --- Имена событий ----------------------------------------------------------
# Голосовой ввод
STT_STARTED = "stt.started"            # микрофон слушает
STT_STOPPED = "stt.stopped"            # микрофон остановлен
STT_PARTIAL = "stt.partial"            # промежуточный текст ({"text": str})
STT_FINAL = "stt.final"                # финальная фраза ({"text": str})
STT_LEVEL = "stt.level"                # уровень сигнала микрофона ({"level": float})
STT_PREFIX = "stt.prefix"              # услышано обращение
STT_ERROR = "stt.error"                # {"message": str}

# Сопоставление и исполнение
COMMAND_MATCHED = "cmd.matched"        # {"command": Command, "phrase": str, "score": float}
COMMAND_UNKNOWN = "cmd.unknown"        # {"text": str}
COMMAND_ASK_CONFIRM = "cmd.ask_confirm"  # {"command": Command}
COMMAND_CONFIRMED = "cmd.confirmed"    # {"command": Command, "confirmed": bool}
COMMAND_STARTED = "cmd.started"        # {"command": Command}
COMMAND_FINISHED = "cmd.finished"      # {"command": Command, "ok": bool, "report": list}
COMMAND_BLOCKED = "cmd.blocked"        # {"command": Command, "reason": str}
ACTION_PROGRESS = "cmd.action"         # {"index": int, "total": int, "title": str}

# Озвучка
TTS_STARTED = "tts.started"            # {"text": str}
TTS_FINISHED = "tts.finished"          # {"text": str}
TTS_ERROR = "tts.error"                # {"message": str}

# ИИ
AI_REQUEST = "ai.request"              # {"text": str}
AI_RESPONSE = "ai.response"            # {"text": str}
AI_ERROR = "ai.error"                  # {"message": str}
AI_THINKING = "ai.thinking"            # None

# Настройки и данные
SETTINGS_CHANGED = "settings.changed"  # {"keys": list[str]}
COMMANDS_CHANGED = "commands.changed"  # None
PACKS_CHANGED = "packs.changed"        # None
MODE_CHANGED = "mode.changed"          # {"prefix_mode": bool, "silent": bool}

# Приложение
APP_READY = "app.ready"                # None
APP_QUIT = "app.quit"                  # None
STATUS_MESSAGE = "app.status"          # {"text": str, "level": str}
NOTIFY = "app.notify"                  # {"title": str, "text": str}

# Плагины
TELEGRAM_STARTED = "plugins.telegram.started"
TELEGRAM_STOPPED = "plugins.telegram.stopped"


class EventBus:
    """Подписка по имени события. Публикация синхронная, в потоке отправителя."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._all: list[Callable[..., Any]] = []

    def subscribe(self, topic: str, handler: Callable[..., Any]) -> Callable[[], None]:
        """Подписаться. Возвращённая функция отписывает обработчик."""
        with self._lock:
            self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            self.unsubscribe(topic, handler)

        return unsubscribe

    def subscribe_all(self, handler: Callable[[str, dict], Any]) -> Callable[[], None]:
        """Подписка на всё сразу — для журнала и отладочной панели."""
        with self._lock:
            self._all.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._all:
                    self._all.remove(handler)

        return unsubscribe

    def unsubscribe(self, topic: str, handler: Callable[..., Any]) -> None:
        with self._lock:
            handlers = self._handlers.get(topic)
            if handlers and handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    self._handlers.pop(topic, None)

    def publish(self, topic: str, **payload: Any) -> None:
        with self._lock:
            handlers = list(self._handlers.get(topic, ()))
            global_handlers = list(self._all)

        for handler in handlers + global_handlers:
            if topic.startswith("_"):
                continue
            try:
                handler(**payload)
            except TypeError:
                # Обработчик с другой сигнатурой — сообщаем, но не падаем.
                try:
                    handler(payload)
                except Exception:
                    log.exception("Подписчик события %s упал", topic)
            except Exception:
                log.exception("Подписчик события %s упал", topic)


# Глобальная шина приложения.
bus = EventBus()


def on(topic: str):
    """Декоратор подписки: @on(STT_FINAL)."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        bus.subscribe(topic, func)
        return func

    return decorator