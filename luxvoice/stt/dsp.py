"""Цифровая обработка звука для распознавания речи.

Заменяет стандартный модуль audioop, удалённый из Python 3.13.
Реализовано на numpy — он уже требуется для распознавания, поэтому
дополнительных зависимостей не появляется.

Что здесь есть:
  * измерение уровня сигнала в дБ;
  * усиление с защитой от перегрузки;
  * пересчёт частоты дискретизации с фильтром против наложения спектров;
  * обрезка и объединение фрагментов;
  * оценка спектра для отсечения музыки и шума.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache

log = logging.getLogger(__name__)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover — numpy ставится вместе с распознаванием
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False
    log.warning("numpy недоступен — обработка звука работает в упрощённом режиме")


# Пределы 16-битного звука.
_MAX_INT16 = 32767
_MIN_INT16 = -32768


def to_array(data: bytes):
    """Преобразовать байты в массив чисел."""
    if not _HAS_NUMPY:
        return None
    if not data:
        return np.zeros(0, dtype=np.float32)
    try:
        return np.frombuffer(data, dtype="<i2").astype(np.float32)
    except ValueError:
        return None


def to_bytes(array) -> bytes:
    """Преобразовать массив обратно в байты."""
    if not _HAS_NUMPY:
        return b""
    clipped = np.clip(array, _MIN_INT16, _MAX_INT16).astype("<i2")
    return clipped.tobytes()


def rms(data: bytes) -> float:
    """Среднеквадратичный уровень сигнала."""
    if _HAS_NUMPY:
        array = to_array(data)
        if array is None or array.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(array ** 2)))

    # Резервный путь без numpy.
    if not data or len(data) < 2:
        return 0.0
    total = 0
    count = 0
    for index in range(0, len(data) - 1, 2):
        sample = int.from_bytes(data[index:index + 2], "little", signed=True)
        total += sample * sample
        count += 1
    if count == 0:
        return 0.0
    return math.sqrt(total / count)


def rms_db(data: bytes) -> float:
    """Уровень сигнала в дБFS: 0 — максимум, -100 — тишина."""
    value = rms(data)
    if value <= 0.5:
        return -100.0
    # Нормируем на полную шкалу 16-битного звука.
    return 20.0 * math.log10(value / 32768.0)


def peak_db(data: bytes) -> float:
    """Пиковый уровень в дБ."""
    if _HAS_NUMPY:
        array = to_array(data)
        if array is None or array.size == 0:
            return -100.0
        peak = float(np.max(np.abs(array)))
        if peak <= 0.5:
            return -100.0
        return 20.0 * math.log10(peak / 32768.0)
    return rms_db(data)


def apply_gain(data: bytes, gain: float) -> bytes:
    """Усилить сигнал. При перегрузке включается мягкое ограничение."""
    if not data or abs(gain - 1.0) < 0.01:
        return data

    if _HAS_NUMPY:
        array = to_array(data)
        if array is None:
            return data
        amplified = array * gain

        # Мягкое ограничение: пики сжимаются, а не срезаются в клиппинг,
        # иначе речь становится неразборчивой.
        overshoot = np.abs(amplified) > _MAX_INT16 * 0.95
        if np.any(overshoot):
            peaking = np.abs(amplified)
            compressed = np.tanh(peaking / (_MAX_INT16 * 0.95)) * _MAX_INT16 * 0.95
            amplified = np.where(overshoot, np.sign(amplified) * compressed, amplified)

        return to_bytes(amplified)

    # Без numpy — простое умножение с ограничением.
    step = int(round(gain * 256))
    output = bytearray(len(data))
    for index in range(0, len(data) - 1, 2):
        sample = int.from_bytes(data[index:index + 2], "little", signed=True)
        value = (sample * step) >> 8
        value = max(_MIN_INT16, min(_MAX_INT16, value))
        output[index:index + 2] = value.to_bytes(2, "little", signed=True)
    return bytes(output)


def resample(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Пересчитать частоту дискретизации.

    При понижении частоты сначала применяется фильтр низких частот,
    иначе высокие частоты «заворачиваются» и портят речь помехами.
    """
    if not data or from_rate == to_rate or from_rate <= 0 or to_rate <= 0:
        return data

    if not _HAS_NUMPY:
        # Без numpy пересчёт невозможен — возвращаем как есть, чтобы
        # вызывающий код мог решить, что делать.
        return data

    source = to_array(data)
    if source is None or source.size == 0:
        return data

    ratio = to_rate / from_rate
    target_length = max(1, int(round(source.size * ratio)))

    # Понижение частоты: сглаживаем сигнал скользящим средним,
    # ширина окна примерно равна коэффициенту прореживания.
    if to_rate < from_rate:
        window = max(2, int(round(from_rate / to_rate)))
        if window > 1 and source.size > window:
            kernel = np.ones(window, dtype=np.float32) / window
            # Свёртка «same» по краям с отражением — без искажения границ.
            padded = np.pad(source, (window // 2, window - 1 - window // 2), mode="edge")
            source = np.convolve(padded, kernel, mode="valid")[:source.size]

    # Линейная интерполяция к новой длине.
    positions = np.linspace(0, source.size - 1, target_length, dtype=np.float32)
    left = np.floor(positions).astype(np.int32)
    right = np.minimum(left + 1, source.size - 1)
    weight = positions - left

    result = source[left] * (1.0 - weight) + source[right] * weight
    return to_bytes(result)


def trim_silence(data: bytes, rate: int = 16000, threshold_db: float = -50.0,
                 frame_ms: int = 20) -> bytes:
    """Обрезать тишину в начале и конце фразы."""
    if not data or not _HAS_NUMPY:
        return data

    frame_bytes = int(rate * frame_ms / 1000) * 2
    if frame_bytes <= 0 or len(data) < frame_bytes * 2:
        return data

    total_frames = len(data) // frame_bytes
    levels = []
    for index in range(total_frames):
        chunk = data[index * frame_bytes:(index + 1) * frame_bytes]
        levels.append(rms_db(chunk))

    # Находим первое и последнее «громкое» окно.
    first = 0
    last = total_frames - 1
    while first < total_frames and levels[first] < threshold_db:
        first += 1
    while last > first and levels[last] < threshold_db:
        last -= 1

    if first >= last:
        return data

    # Оставляем небольшой запас с обеих сторон, чтобы не срезать начало слова.
    first = max(0, first - 2)
    last = min(total_frames - 1, last + 2)
    return data[first * frame_bytes:(last + 1) * frame_bytes]


@lru_cache(maxsize=4)
def _hann_window(size: int):
    """Окно Ханна для спектральной оценки (кэшируется — считается часто)."""
    if not _HAS_NUMPY:
        return None
    return np.hanning(size).astype(np.float32)


def spectral_flatness(data: bytes, rate: int = 16000) -> float:
    """Спектральная плоскостность: 0 — тон/музыка, 1 — шум и речь.

    Помогает отличить речь от музыкального тона: у музыки энергия
    сосредоточена в отдельных частотах, у речи спектр ровнее.
    """
    if not _HAS_NUMPY:
        return 1.0

    array = to_array(data)
    if array is None or array.size < 256:
        return 1.0

    window = _hann_window(min(512, array.size))
    if window is None:
        return 1.0
    segment = array[:window.size] * window

    spectrum = np.abs(np.fft.rfft(segment))
    spectrum = spectrum[1:]  # убираем постоянную составляющую
    if spectrum.size == 0:
        return 1.0

    spectrum = np.maximum(spectrum, 1e-10)
    geometric = float(np.exp(np.mean(np.log(spectrum))))
    arithmetic = float(np.mean(spectrum))
    if arithmetic <= 0:
        return 1.0
    return float(geometric / arithmetic)


def zero_crossing_rate(data: bytes) -> float:
    """Частота переходов через ноль — признак шипящих и свистящих звуков."""
    if not _HAS_NUMPY:
        return 0.0
    array = to_array(data)
    if array is None or array.size < 2:
        return 0.0
    signs = np.signbit(array)
    crossings = np.count_nonzero(signs[1:] != signs[:-1])
    return float(crossings / (array.size - 1))


def concatenate(chunks: list[bytes]) -> bytes:
    """Склеить фрагменты звука."""
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]
    return b"".join(chunks)


def duration_seconds(data: bytes, rate: int = 16000, width: int = 2) -> float:
    """Длительность звука в секундах."""
    if not data:
        return 0.0
    return len(data) / float(rate * width)


def silence(duration: float, rate: int = 16000, width: int = 2) -> bytes:
    """Сгенерировать тишину заданной длительности."""
    return b"\x00" * int(duration * rate) * width

# --- Определение речи -------------------------------------------------------

def speech_probability(data: bytes, rate: int = 16000,
                       noise_floor_db: float = -60.0) -> float:
    """Вероятность того, что в кадре звучит речь.

    Речь отличается от музыки и шума по трём признакам:
      * уровень заметно выше фонового;
      * энергия распределена по спектру, а не собрана в одной частоте;
      * есть характерная смена переходов через ноль (согласные).

    Возвращает число от 0 (тишина или шум) до 1 (уверенная речь).
    """
    if not _HAS_NUMPY:
        return 0.0

    array = to_array(data)
    if array is None or array.size < 64:
        return 0.0

    # --- Признак 1: уровень над фоном ---
    level = rms_db(data)
    margin = level - noise_floor_db
    if margin <= 3.0:
        return 0.0
    level_score = min(1.0, max(0.0, (margin - 3.0) / 15.0))

    # --- Признак 2: спектр не «тоновый» ---
    flatness = spectral_flatness(data, rate)
    # Музыкальный тон даёт плоскостность ниже 0.05, речь — 0.2 и выше.
    tone_score = min(1.0, max(0.0, (flatness - 0.05) / 0.25))

    # --- Признак 3: энергия не в одной узкой частоте ---
    window = _hann_window(min(512, array.size))
    if window is not None and window.size >= 64:
        spectrum = np.abs(np.fft.rfft(array[:window.size] * window))
        spectrum = spectrum[1:]
        if spectrum.size:
            total = float(np.sum(spectrum))
            if total > 0:
                top = float(np.max(spectrum))
                # Если больше половины энергии в одной частоте — это тон.
                concentration = top / total
                tone_score *= min(1.0, max(0.0, (0.5 - concentration) / 0.4))

    # --- Признак 4: переходы через ноль ---
    zcr = zero_crossing_rate(data)
    # Речь даёт 0.02..0.25; крайние значения — шум канала или тишина.
    zcr_score = 1.0 if 0.01 <= zcr <= 0.35 else 0.4

    # Итог: уровень — обязательное условие, «тоновость» работает
    # понижающим множителем. Громкая музыка не должна считаться речью
    # только потому, что она громкая.
    tonal_penalty = 0.25 + 0.75 * tone_score
    probability = level_score * tonal_penalty * (0.9 + 0.1 * zcr_score)
    return float(min(1.0, max(0.0, probability)))
