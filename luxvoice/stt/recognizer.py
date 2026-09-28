"""Распознаватель речи: связывает микрофон, движок и шину событий.

Логика работы:
  1. Микрофон пишет кадры и определяет речь.
  2. Готовую фразу отправляем в поток обработки (не в поток записи,
     иначе звук начинает «захлёбываться»).
  3. Движок распознаёт текст, результат публикуется в шине событий.
  4. Параллельно идёт промежуточное распознавание для показа текста
     по мере речи.

Отдельно решается задача самопрослушивания: пока ассистент говорит,
запись приостанавливается, иначе собственный голос становится командой.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

from luxvoice.core.events import (
    STT_ERROR,
    STT_FINAL,
    STT_LEVEL,
    STT_PARTIAL,
    STT_PREFIX,
    STT_STARTED,
    STT_STOPPED,
    bus,
)
from luxvoice.stt.capture import MicrophoneRecorder
from luxvoice.stt.engines import (
    CloudEngine,
    Recognition,
    SpeechEngine,
    VoskEngine,
    WhisperEngine,
)

log = logging.getLogger(__name__)


class SpeechRecognizer:
    """Приём речи и превращение её в текст."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._engine: SpeechEngine | None = None
        self._assistant_engine: SpeechEngine | None = None
        self._engine_kind = ""
        self._lock = threading.RLock()

        # Очередь фраз: распознавание может быть медленнее записи.
        self._queue: queue.Queue[tuple[bytes, float] | None] = queue.Queue(maxsize=12)
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._recorder: MicrophoneRecorder | None = None
        self._listening = False
        self._paused = 0                # счётчик пауз (озвучка, выполнение)
        self._pause_lock = threading.RLock()

        # Статистика.
        self.phrases = 0
        self.recognized = 0
        self.last_text = ""
        self.last_error = ""
        self.last_elapsed = 0.0
        self._last_final_time = 0.0

        # Обработчики, которым нужен текст помимо шины событий.
        self._text_handlers: list[Callable[[Recognition], None]] = []

    # --- Настройка --------------------------------------------------------

    def set_settings(self, settings) -> None:
        self._settings = settings

    def on_text(self, handler: Callable[[Recognition], None]) -> None:
        """Добавить обработчик распознанного текста."""
        self._text_handlers.append(handler)

    # --- Выбор движка -----------------------------------------------------

    def _engine_name(self) -> str:
        if self._settings is None:
            return "vosk"
        return self._settings.text("stt.engine", "vosk")

    def _language(self) -> str:
        if self._settings is None:
            return "ru"
        return self._settings.text("stt.language", "ru")

    def _create_engine(self, kind: str, long_form: bool = False) -> SpeechEngine:
        """Создать движок по имени."""
        language = self._language()

        if kind == "vosk":
            path = self._settings.text("stt.vosk_model_path", "") if self._settings else ""
            sensitivity = int(self._settings.number("stt.sensitivity", 50)) \
                if self._settings else 50
            return VoskEngine(language, path, sensitivity)

        if kind == "whisper":
            size = self._settings.text("stt.whisper_model", "small") \
                if self._settings else "small"
            device = self._settings.text("stt.whisper_device", "auto") \
                if self._settings else "auto"
            compute = self._settings.text("stt.whisper_compute", "int8") \
                if self._settings else "int8"
            return WhisperEngine(language, size, device, compute)

        if kind == "cloud":
            provider = self._settings.text("stt.cloud_provider", "openai") \
                if self._settings else "openai"
            key, base_url = self._ai_credentials(provider)
            return CloudEngine(language, provider, key, base_url)

        # Неизвестный движок — используем Vosk.
        return VoskEngine(language)

    def _ai_credentials(self, provider: str) -> tuple[str, str]:
        """Взять ключ и адрес для облачного распознавания."""
        if self._settings is None:
            return "", ""
        keys = self._settings.get("ai.keys") or {}
        key = ""
        if isinstance(keys, dict):
            key = str(keys.get(provider, "") or "")
        if not key:
            # Ключ основного провайдера, если он совпадает.
            current = self._settings.text("ai.provider", "")
            if current == provider:
                key = self._settings.text("ai.api_key", "")
        base_url = ""
        if provider == "custom":
            base_url = self._settings.text("ai.base_url", "")
        return key, base_url

    def engine(self, long_form: bool = False) -> SpeechEngine | None:
        """Действующий движок, при необходимости — для длинной речи."""
        with self._lock:
            kind = self._engine_name()
            if long_form:
                # Для диктовки и запросов к ИИ можно взять другой движок.
                alternative = self._settings.text("stt.assistant_engine", "") \
                    if self._settings else ""
                if alternative and alternative != kind:
                    if self._assistant_engine is None:
                        self._assistant_engine = self._create_engine(alternative, True)
                    return self._assistant_engine
            if self._engine is None or self._engine_kind != kind:
                if self._engine is not None:
                    self._engine.close()
                self._engine = self._create_engine(kind)
                self._engine_kind = kind
            return self._engine

    def reload_engine(self) -> None:
        """Пересоздать движок — после смены настроек."""
        with self._lock:
            for engine in (self._engine, self._assistant_engine):
                if engine is not None:
                    engine.close()
            self._engine = None
            self._assistant_engine = None
            self._engine_kind = ""

    def prepare(self, long_form: bool = False) -> tuple[bool, str]:
        """Подготовить движок (загрузить модель). Возвращает (успех, сообщение)."""
        engine = self.engine(long_form)
        if engine is None:
            return False, "движок распознавания недоступен"
        if engine.prepare():
            return True, ""
        return False, engine.error

    # --- Запись -----------------------------------------------------------

    def _recorder_instance(self) -> MicrophoneRecorder:
        if self._recorder is None:
            self._recorder = MicrophoneRecorder(
                on_phrase=self._on_phrase,
                on_frame=self._on_frame,
                on_level=self._on_level,
            )
            self._apply_capture_settings()
        return self._recorder

    def _apply_capture_settings(self) -> None:
        """Перенести настройки записи в микрофон."""
        if self._recorder is None or self._settings is None:
            return

        device_raw = self._settings.get("stt.mic_device", "")
        device: int | None = None
        if device_raw not in ("", None):
            try:
                device = int(device_raw)
            except (TypeError, ValueError):
                device = None

        self._recorder.configure(
            device=device,
            sensitivity=int(self._settings.number("stt.sensitivity", 50)),
            silence_timeout=float(self._settings.number("stt.silence_timeout", 0.9)),
            max_phrase_seconds=float(self._settings.number("stt.max_phrase_seconds", 15)),
            vad_enabled=self._settings.flag("stt.vad_enabled", True),
            vad_aggressiveness=int(self._settings.number("stt.vad_aggressiveness", 2)),
            gain=float(self._settings.number("stt.input_gain", 1.0)),
        )

    def start_listening(self) -> tuple[bool, str]:
        """Включить прослушивание микрофона."""
        with self._lock:
            if self._listening:
                return True, ""

            self._apply_capture_settings()
            recorder = self._recorder_instance()

            # Готовим движок заранее — иначе первая фраза потеряется.
            engine = self.engine()
            if engine is not None and not engine.ready:
                ok, message = self.prepare()
                if not ok:
                    self.last_error = message
                    bus.publish(STT_ERROR, message=message)
                    # Продолжаем: запись возможна, распознавание подключится позже.

            if not recorder.start():
                self.last_error = recorder.error
                return False, recorder.error

            self._stop_event.clear()
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._work_loop, name="stt-worker", daemon=True)
                self._worker.start()

            self._listening = True
            bus.publish(STT_STARTED)
            log.info("Прослушивание включено")
            return True, ""

    def stop_listening(self) -> None:
        """Выключить прослушивание."""
        with self._lock:
            if self._recorder is not None:
                self._recorder.stop()
            self._listening = False
            self._stop_event.set()
            # Будим рабочий поток, чтобы он завершился.
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            bus.publish(STT_STOPPED)
        log.info("Прослушивание выключено")

    @property
    def listening(self) -> bool:
        return self._listening

    def toggle_listening(self) -> bool:
        """Переключить прослушивание. Возвращает новое состояние."""
        if self._listening:
            self.stop_listening()
            return False
        self.start_listening()
        return self._listening

    def restart_listening(self) -> None:
        """Перезапустить запись — после смены микрофона или настроек."""
        if self._listening:
            self.stop_listening()
            time.sleep(0.2)
            self.start_listening()

    # --- Пауза (во время озвучки) ----------------------------------------

    def pause(self) -> None:
        """Приостановить приём речи — на время ответа ассистента."""
        with self._pause_lock:
            self._paused += 1
            if self._paused == 1 and self._recorder is not None:
                # Не останавливаем поток: дешевле игнорировать фразы,
                # чем перезапускать устройство (это заметная задержка).
                log.debug("Приём речи приостановлен")

    def resume(self) -> None:
        """Возобновить приём речи."""
        with self._pause_lock:
            self._paused = max(0, self._paused - 1)
            if self._paused == 0:
                log.debug("Приём речи возобновлён")

    @property
    def paused(self) -> bool:
        with self._pause_lock:
            return self._paused > 0

    # --- Обработка --------------------------------------------------------

    def _on_phrase(self, pcm: bytes) -> None:
        """Записана готовая фраза."""
        if self.paused:
            log.debug("Фраза пропущена: приём приостановлен")
            return
        self.phrases += 1
        try:
            self._queue.put_nowait((pcm, time.time()))
        except queue.Full:
            # Очередь переполнена — распознавание не успевает.
            log.warning("Очередь распознавания переполнена, фраза пропущена")

    def _on_frame(self, pcm: bytes) -> None:
        """Кадр речи — для промежуточного текста."""
        if self.paused or self._settings is None:
            return
        if not self._settings.flag("stt.live_partial", True):
            return
        # Промежуточный текст только для потоковых движков.
        engine = self.engine()
        if engine is None or not engine.supports_streaming:
            return

    def _on_level(self, level: float) -> None:
        bus.publish(STT_LEVEL, level=level)

    def _work_loop(self) -> None:
        """Поток распознавания: берёт фразы из очереди и обрабатывает."""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            pcm, captured_at = item
            try:
                self._recognize(pcm, captured_at)
            except Exception as exc:  # noqa: BLE001
                log.exception("Ошибка распознавания: %s", exc)
                self.last_error = str(exc)
                bus.publish(STT_ERROR, message=str(exc))

    def _recognize(self, pcm: bytes, captured_at: float) -> None:
        """Распознать одну фразу и опубликовать результат."""
        engine = self.engine()
        if engine is None:
            return

        started = time.time()
        result = engine.recognize(pcm)
        result.duration = len(pcm) / (16000 * 2)
        self.last_elapsed = time.time() - started

        if result.error:
            self.last_error = result.error
            log.warning("Распознавание не удалось: %s", result.error)
            bus.publish(STT_ERROR, message=result.error)
            return

        text = (result.text or "").strip()
        if not text:
            log.debug("Фраза распознана как пустая (шум?)")
            return

        self.recognized += 1
        self.last_text = text
        self._last_final_time = time.time()
        self.last_error = ""

        log.info("Распознано: %r (%.2f с)", text, self.last_elapsed)

        # Уведомляем всех подписчиков.
        bus.publish(STT_FINAL, text=text, confidence=result.confidence,
                    engine=result.engine, duration=result.duration)
        for handler in list(self._text_handlers):
            try:
                handler(result)
            except Exception as exc:  # noqa: BLE001
                log.exception("Обработчик текста упал: %s", exc)

    # --- Разовое распознавание -------------------------------------------

    def recognize_file(self, path: str, long_form: bool = True) -> Recognition:
        """Распознать готовый звуковой файл (голосовое из Telegram, запись)."""
        engine = self.engine(long_form)
        if engine is None:
            return Recognition(error="движок распознавания недоступен")

        if isinstance(engine, WhisperEngine):
            return engine.recognize_file(path)

        # Для остальных движков читаем файл и приводим к 16 кГц.
        try:
            pcm = self._read_audio(path)
        except Exception as exc:  # noqa: BLE001
            return Recognition(error=f"не удалось прочитать файл: {exc}")

        if not pcm:
            return Recognition(error="файл пуст или формат не поддерживается")
        return engine.recognize(pcm)

    @staticmethod
    def _read_audio(path: str) -> bytes:
        """Прочитать звук и привести к 16 кГц, 16 бит, моно."""
        target = str(path)
        suffix = target.lower().rsplit(".", 1)[-1] if "." in target else ""

        if suffix == "wav":
            try:
                with wave.open(target, "rb") as reader:
                    channels = reader.getnchannels()
                    width = reader.getsampwidth()
                    rate = reader.getframerate()
                    frames = reader.readframes(reader.getnframes())
            except (wave.Error, OSError):
                frames, channels, width, rate = b"", 1, 2, 16000
            if not frames:
                raise ValueError("файл не читается как WAV")
        else:
            # Прочие форматы (OGG, MP3) декодируем через ffmpeg — он есть
            # практически везде, где нужен звук.
            import shutil
            import subprocess
            if not shutil.which("ffmpeg"):
                raise RuntimeError("для этого формата нужен ffmpeg")
            completed = subprocess.run(
                ["ffmpeg", "-i", target, "-f", "s16le", "-ac", "1",
                 "-ar", "16000", "-loglevel", "quiet", "-"],
                capture_output=True, timeout=120,
            )
            if completed.returncode != 0:
                raise RuntimeError("ffmpeg не смог прочитать файл")
            return completed.stdout

        from luxvoice.stt import dsp

        # Приведение к моно.
        if channels > 1 and width == 2:
            import array
            samples = array.array("h")
            samples.frombytes(frames[:len(frames) - len(frames) % (2 * channels)])
            mono = array.array("h")
            for index in range(0, len(samples) - channels + 1, channels):
                mono.append(samples[index])
            frames = mono.tobytes()

        # Приведение частоты.
        if rate != 16000:
            frames = dsp.resample(frames, rate, 16000)
        return frames

    # --- Диагностика ------------------------------------------------------

    def diagnostics(self) -> dict[str, object]:
        engine = self._engine
        return {
            "listening": self._listening,
            "paused": self.paused,
            "engine": self._engine_kind or self._engine_name(),
            "engine_ready": bool(engine and engine.ready),
            "engine_error": engine.error if engine else "",
            "phrases": self.phrases,
            "recognized": self.recognized,
            "queue": self._queue.qsize(),
            "last_text": self.last_text,
            "last_elapsed": round(self.last_elapsed, 3),
            "error": self.last_error,
        }

    def close(self) -> None:
        self.stop_listening()
        with self._lock:
            for engine in (self._engine, self._assistant_engine):
                if engine is not None:
                    engine.close()
            self._engine = None
            self._assistant_engine = None


# --- Одиночный экземпляр ----------------------------------------------------

_recognizer: SpeechRecognizer | None = None
_lock = threading.Lock()


def get_recognizer(settings=None) -> SpeechRecognizer:
    global _recognizer
    with _lock:
        if _recognizer is None:
            _recognizer = SpeechRecognizer(settings)
        elif settings is not None:
            _recognizer.set_settings(settings)
        return _recognizer