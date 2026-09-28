"""Захват звука с микрофона и определение речи.

Запись идёт через звуковой сервер PipeWire/PulseAudio (модуль sounddevice
обращается к нему через ALSA-совместимый слой). Поток аудио разбивается
на кадры, к каждому применяется определение речи: пока человек говорит,
кадры накапливаются; после паузы фраза считается законченной и уходит
на распознавание.

Определение речи (VAD) двухуровневое:
  * энергетический порог — быстрый и надёжный для чистого сигнала;
  * спектральная оценка через webrtcvad, если он установлен, — точнее
    отсекает музыку и шум.

Это позволяет не гонять распознавание на тишине: на слабом процессоре
экономия существенная, а ложных срабатываний заметно меньше.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from luxvoice.core.events import STT_ERROR, STT_LEVEL, bus
from luxvoice.stt import dsp

log = logging.getLogger(__name__)

# Частота дискретизации для распознавания.
SAMPLE_RATE = 16000
# Длина кадра для VAD: 10, 20 или 30 мс — требование webrtcvad.
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
# Формат: 16-битные целые, моно.
SAMPLE_WIDTH = 2
CHANNELS = 1


@dataclass
class MicrophoneInfo:
    """Устройство ввода."""

    index: int
    name: str
    channels: int = 1
    default_rate: int = 48000
    is_default: bool = False

    @property
    def label(self) -> str:
        return self.name


def list_microphones() -> list[MicrophoneInfo]:
    """Список доступных устройств записи."""
    try:
        import sounddevice as sd
    except ImportError:
        log.warning("sounddevice не установлен — микрофоны недоступны")
        return []

    result: list[MicrophoneInfo] = []
    try:
        devices = sd.query_devices()
        default_index = -1
        try:
            default_index = sd.default.device[0]
        except (TypeError, IndexError):
            pass

        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            result.append(MicrophoneInfo(
                index=index,
                name=str(device.get("name", f"Устройство {index}")),
                channels=int(device.get("max_input_channels", 1)),
                default_rate=int(device.get("default_samplerate", 48000) or 48000),
                is_default=index == default_index,
            ))
    except Exception as exc:  # noqa: BLE001 — библиотека отвечает разными ошибками
        log.error("Не удалось получить список микрофонов: %s", exc)
    return result


class VoiceActivityDetector:
    """Определение наличия речи в кадре аудио."""

    def __init__(self, aggressiveness: int = 2, threshold_db: float = -45.0) -> None:
        self._aggressiveness = max(0, min(3, int(aggressiveness)))
        self._threshold_db = threshold_db
        self._vad = None
        self._noise_floor = -60.0
        self._calibrated = False

        # Порог вероятности речи: чем выше агрессивность, тем строже.
        # 0 — мягко (0.25), 3 — отсекает почти всё (0.75).
        self._speech_threshold = 0.25 + 0.165 * self._aggressiveness

        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self._aggressiveness)
            log.debug("Определение речи: webrtcvad, уровень %d", self._aggressiveness)
        except (ImportError, ModuleNotFoundError):
            # webrtcvad несовместим с Python 3.13+ (требует pkg_resources).
            # Используем собственную оценку на numpy — она учитывает
            # и уровень, и спектр, что надёжнее простого порога.
            log.debug("Определение речи: спектральная оценка (порог %.2f)",
                      self._speech_threshold)

    # --- Оценка -----------------------------------------------------------

    @staticmethod
    def rms_db(frame: bytes) -> float:
        """Уровень сигнала в дБFS (отрицательное число, 0 — максимум)."""
        return dsp.rms_db(frame)

    def calibrate(self, frame: bytes) -> None:
        """Оценить уровень шума в помещении по первым кадрам тишины."""
        level = self.rms_db(frame)
        if level <= -95.0:
            return
        # Экспоненциальное сглаживание: фон меняется медленно.
        self._noise_floor = self._noise_floor * 0.9 + level * 0.1
        self._calibrated = True

    def is_speech(self, frame: bytes) -> bool:
        """Есть ли речь в кадре."""
        if len(frame) != FRAME_SAMPLES * SAMPLE_WIDTH:
            # Кадр не той длины — подгоняем.
            if len(frame) < FRAME_SAMPLES * SAMPLE_WIDTH:
                frame = frame + b"\x00" * (FRAME_SAMPLES * SAMPLE_WIDTH - len(frame))
            else:
                frame = frame[:FRAME_SAMPLES * SAMPLE_WIDTH]

        level = self.rms_db(frame)

        # Порог подстраивается под фон: в тихой комнате он ниже.
        threshold = self._threshold_db
        if self._calibrated:
            adaptive = self._noise_floor + 9.0
            # Не даём порогу уехать слишком высоко — иначе тихую речь не слышно.
            threshold = min(threshold, max(adaptive, -55.0))

        if level < threshold:
            # Явная тишина — не тратим время на спектральную проверку.
            if not self._calibrated or level < self._noise_floor + 6.0:
                self.calibrate(frame)
            return False

        if self._vad is not None:
            try:
                return bool(self._vad.is_speech(frame, SAMPLE_RATE))
            except Exception:  # noqa: BLE001 — VAD может ругаться на формат
                pass

        # Спектральная оценка: отличает речь от музыки и ровного шума.
        probability = dsp.speech_probability(
            frame, SAMPLE_RATE, self._noise_floor)
        return probability >= self._speech_threshold


@dataclass
class CaptureConfig:
    """Параметры записи."""

    device: int | None = None
    sample_rate: int = SAMPLE_RATE
    frame_ms: int = FRAME_MS
    silence_timeout: float = 0.9      # пауза, завершающая фразу
    max_phrase_seconds: float = 15.0  # предохранитель
    min_phrase_seconds: float = 0.25  # слишком короткое — шум
    sensitivity: int = 50             # 0..100, влияет на порог
    vad_enabled: bool = True
    vad_aggressiveness: int = 2
    gain: float = 1.0
    pre_roll: float = 0.3             # сколько звука до начала речи сохранять


class MicrophoneRecorder:
    """Запись фраз с микрофона.

    Работает в отдельном потоке. Наружу отдаёт:
      * on_phrase(bytes) — готовая фраза в формате 16 кГц, 16 бит, моно;
      * on_frame(bytes) — каждый кадр (для промежуточного распознавания);
      * уровень сигнала через шину событий.
    """

    def __init__(self, on_phrase: Callable[[bytes], None],
                 on_frame: Callable[[bytes], None] | None = None,
                 on_level: Callable[[float], None] | None = None) -> None:
        self._on_phrase = on_phrase
        self._on_frame = on_frame
        self._on_level = on_level

        self._config = CaptureConfig()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = threading.Event()
        self._lock = threading.RLock()

        self._detector = VoiceActivityDetector()
        self._buffer: list[bytes] = []
        self._pre_roll: deque[bytes] = deque(maxlen=10)
        self._speaking = False
        self._silence_frames = 0
        self._speech_started = 0.0
        self._last_level = -100.0
        self._error = ""

        # Счётчики для диагностики.
        self.frames_seen = 0
        self.phrases_captured = 0

    # --- Управление -------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def error(self) -> str:
        return self._error

    def configure(self, **values: object) -> None:
        """Применить настройки записи."""
        with self._lock:
            for key, value in values.items():
                if hasattr(self._config, key) and value is not None:
                    setattr(self._config, key, value)

            # Порог определения речи зависит от чувствительности:
            # 0% — порог -30 дБ (слышит только громкое), 100% — -60 дБ.
            sensitivity = max(0, min(100, int(self._config.sensitivity)))
            threshold = -60.0 + (sensitivity / 100.0) * 30.0
            self._detector._threshold_db = threshold
            if self._config.vad_aggressiveness != self._detector._aggressiveness:
                self._detector = VoiceActivityDetector(
                    self._config.vad_aggressiveness, threshold)

    def start(self) -> bool:
        """Начать запись."""
        with self._lock:
            if self._running.is_set():
                return True

            try:
                import sounddevice as sd
            except ImportError:
                self._error = "модуль записи звука не установлен (sounddevice)"
                log.error(self._error)
                bus.publish(STT_ERROR, message=self._error)
                return False

            self._stop_event.clear()
            try:
                device = self._config.device
                if device is not None and self._config.sample_rate:
                    # Проверяем, поддерживает ли устройство нужную частоту.
                    try:
                        sd.check_input_settings(
                            device=device, channels=CHANNELS,
                            samplerate=self._config.sample_rate,
                            dtype="int16",
                        )
                    except Exception:  # noqa: BLE001
                        # Многие устройства не умеют 16 кГц — берём 48 кГц
                        # и приводим частоту сами.
                        self._config.sample_rate = 48000

                self._stream = sd.RawInputStream(
                    samplerate=self._config.sample_rate,
                    blocksize=FRAME_SAMPLES,
                    device=device,
                    channels=CHANNELS,
                    dtype="int16",
                    callback=self._callback,
                )
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                self._error = f"не удалось открыть микрофон: {exc}"
                log.error(self._error)
                bus.publish(STT_ERROR, message=self._error)
                self._stream = None
                return False

            self._running.set()
            self._error = ""
            log.info("Запись с микрофона начата (частота %d Гц)",
                     self._config.sample_rate)
            return True

    def stop(self) -> None:
        """Остановить запись."""
        with self._lock:
            self._stop_event.set()
            self._running.clear()
            stream, self._stream = self._stream, None

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("Ошибка при остановке потока: %s", exc)
        log.info("Запись с микрофона остановлена")

    def restart(self) -> bool:
        """Перезапустить с новыми настройками."""
        was_running = self.running
        self.stop()
        time.sleep(0.15)
        return self.start() if was_running else True

    # --- Обработка аудио --------------------------------------------------

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        """Приём данных из звуковой подсистемы (отдельный поток)."""
        if status:
            log.debug("Состояние потока записи: %s", status)
        if self._stop_event.is_set():
            return

        try:
            data = bytes(indata)
        except (TypeError, ValueError):
            return

        # Приведение частоты к 16 кГц, если микрофон работает на другой.
        rate = self._config.sample_rate
        if rate != SAMPLE_RATE:
            data = dsp.resample(data, rate, SAMPLE_RATE)
            if not data:
                return

        # Усиление сигнала.
        gain = self._config.gain
        if gain and abs(gain - 1.0) > 0.01:
            data = dsp.apply_gain(data, gain)

        self._process_frame(data)

    def _process_frame(self, data: bytes) -> None:
        """Разобрать кадр: речь, пауза, накопление фразы."""
        self.frames_seen += 1
        level = VoiceActivityDetector.rms_db(data)
        self._last_level = level

        if self._on_level is not None:
            try:
                # Переводим дБ в 0..1 для индикатора.
                normalized = max(0.0, min(1.0, (level + 60.0) / 45.0))
                self._on_level(normalized)
            except Exception:  # noqa: BLE001
                pass

        cfg = self._config
        speech = False
        if cfg.vad_enabled:
            speech = self._detector.is_speech(data)
        else:
            speech = level > self._detector._threshold_db

        if not self._speaking:
            # Копим звук до начала речи, чтобы не потерять первое слово.
            self._pre_roll.append(data)
            if speech:
                self._speaking = True
                self._speech_started = time.time()
                self._buffer = list(self._pre_roll)
                self._pre_roll.clear()
                self._silence_frames = 0
            return

        # Идёт речь.
        self._buffer.append(data)
        if self._on_frame is not None:
            try:
                self._on_frame(data)
            except Exception:  # noqa: BLE001
                pass

        if speech:
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        elapsed = time.time() - self._speech_started
        silence_seconds = self._silence_frames * FRAME_MS / 1000.0

        finished = False
        if silence_seconds >= cfg.silence_timeout:
            finished = True
        elif elapsed >= cfg.max_phrase_seconds:
            finished = True

        if finished:
            self._finish_phrase()

    def _finish_phrase(self) -> None:
        """Отдать накопленную фразу на распознавание."""
        collected = b"".join(self._buffer)
        duration = len(collected) / (SAMPLE_RATE * SAMPLE_WIDTH)

        self._speaking = False
        self._buffer = []
        self._silence_frames = 0

        if duration < self._config.min_phrase_seconds:
            log.debug("Фраза слишком короткая (%.2f с) — пропущена", duration)
            return

        self.phrases_captured += 1
        log.debug("Фраза записана: %.2f с", duration)
        try:
            self._on_phrase(collected)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка обработки записанной фразы: %s", exc)

    def flush(self) -> None:
        """Принудительно завершить текущую фразу."""
        if self._speaking:
            self._finish_phrase()

    # --- Диагностика ------------------------------------------------------

    def level(self) -> float:
        """Текущий уровень сигнала в дБ."""
        return self._last_level

    def diagnostics(self) -> dict[str, object]:
        return {
            "running": self.running,
            "error": self._error,
            "frames": self.frames_seen,
            "phrases": self.phrases_captured,
            "level_db": round(self._last_level, 1),
            "noise_floor_db": round(self._detector._noise_floor, 1),
            "device": self._config.device,
            "sample_rate": self._config.sample_rate,
        }


class AudioLevelMeter:
    """Измерение уровня сигнала без распознавания — для индикатора в интерфейсе."""

    def __init__(self) -> None:
        self._stream = None
        self._level = 0.0
        self._lock = threading.RLock()

    def start(self, device: int | None = None) -> bool:
        try:
            import sounddevice as sd
        except ImportError:
            return False

        with self._lock:
            if self._stream is not None:
                return True
            try:
                self._stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES,
                    device=device, channels=CHANNELS, dtype="int16",
                    callback=self._callback,
                )
                self._stream.start()
                return True
            except Exception as exc:  # noqa: BLE001
                log.debug("Индикатор уровня не запущен: %s", exc)
                self._stream = None
                return False

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        level = VoiceActivityDetector.rms_db(bytes(indata))
        with self._lock:
            self._level = max(0.0, min(1.0, (level + 60.0) / 45.0))

    def level(self) -> float:
        with self._lock:
            return self._level

    def stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass


def resample(data: bytes, from_rate: int, to_rate: int = SAMPLE_RATE) -> bytes:
    """Привести частоту дискретизации к нужной."""
    if from_rate == to_rate or not data:
        return data
    return dsp.resample(data, from_rate, to_rate)


def pcm_to_wav(data: bytes, rate: int = SAMPLE_RATE,
               channels: int = CHANNELS) -> bytes:
    """Обернуть сырой звук в контейнер WAV — нужен для облачного распознавания."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(SAMPLE_WIDTH)
        writer.setframerate(rate)
        writer.writeframes(data)
    return buffer.getvalue()