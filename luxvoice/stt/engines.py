"""Распознавание речи: локальные движки и облако.

Три движка на выбор:
  * Vosk — быстрый, лёгкий (около 50 МБ на язык), мгновенный отклик,
    распознаёт потоково. Основной выбор для команд.
  * Whisper (faster-whisper) — точнее на свободной речи, но тяжелее;
    применяется для диктовки и запросов к нейросети.
  * Облако — через API выбранного сервиса, если есть ключ и интернет.

Все движки приводятся к одному интерфейсу: accept(кадр) → текст,
или recognize(звук целиком) → текст. Это позволяет менять движок
в настройках без переделки остальной программы.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from luxvoice.core import paths
from luxvoice.stt.capture import SAMPLE_RATE, pcm_to_wav

log = logging.getLogger(__name__)


# --- Результат распознавания ------------------------------------------------

@dataclass
class Recognition:
    """Результат распознавания одной фразы."""

    text: str = ""
    confidence: float = 0.0
    engine: str = ""
    duration: float = 0.0
    elapsed: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


# --- Базовый движок ---------------------------------------------------------

class SpeechEngine:
    """Общий интерфейс движка распознавания."""

    name = "base"
    supports_streaming = False

    def __init__(self, language: str = "ru") -> None:
        self.language = language
        self._ready = False
        self._error = ""

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> str:
        return self._error

    def prepare(self) -> bool:
        """Подготовить движок к работе (загрузить модель)."""
        raise NotImplementedError

    def recognize(self, pcm: bytes) -> Recognition:
        """Распознать фразу целиком."""
        raise NotImplementedError

    def start_stream(self, on_partial: Callable[[str], None] | None = None) -> bool:
        """Начать потоковое распознавание (если поддерживается)."""
        return False

    def feed(self, pcm: bytes) -> str:
        """Подать кадр в поток. Возвращает промежуточный текст."""
        return ""

    def finish_stream(self) -> Recognition:
        """Завершить поток и получить итоговый текст."""
        return Recognition(engine=self.name)

    def close(self) -> None:
        """Освободить ресурсы."""


# --- Vosk -------------------------------------------------------------------

# Адреса моделей Vosk. Небольшие модели для команд — оптимальный выбор.
VOSK_MODELS = {
    "ru": {
        "name": "vosk-model-small-ru-0.22",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
        "size": "45 МБ",
    },
    "en": {
        "name": "vosk-model-small-en-us-0.15",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "size": "40 МБ",
    },
    "ru-large": {
        "name": "vosk-model-ru-0.42",
        "url": "https://alphacephei.com/vosk/models/vosk-model-ru-0.42.zip",
        "size": "1.8 ГБ",
    },
}


class VoskEngine(SpeechEngine):
    """Распознавание через Vosk. Работает офлайн, распознаёт потоково."""

    name = "vosk"
    supports_streaming = True

    def __init__(self, language: str = "ru", model_path: str = "",
                 sensitivity: int = 50) -> None:
        super().__init__(language)
        self._model = None
        self._recognizer = None
        self._model_path = model_path
        self._sensitivity = sensitivity
        self._lock = threading.RLock()
        self._partial: list[str] = []

    # --- Поиск и загрузка модели -----------------------------------------

    def resolve_model_path(self) -> str:
        """Найти каталог модели: из настроек, потом в данных программы."""
        if self._model_path:
            candidate = Path(self._model_path).expanduser()
            if candidate.exists():
                return str(candidate)

        models_root = paths.models_dir()
        wanted = VOSK_MODELS.get(self.language, VOSK_MODELS["ru"])["name"]

        # Ищем точное совпадение, затем любую подходящую модель языка.
        exact = models_root / wanted
        if exact.exists():
            return str(exact)

        aliases = {"ru": ("ru", "russian"), "en": ("en", "english", "en-us")}
        markers = aliases.get(self.language, ("ru",))

        try:
            for folder in sorted(models_root.iterdir()):
                if not folder.is_dir() or not folder.name.startswith("vosk-model"):
                    continue
                lowered = folder.name.lower()
                if any(marker in lowered for marker in markers):
                    return str(folder)
        except OSError:
            pass
        return ""

    def prepare(self) -> bool:
        if self._ready:
            return True

        try:
            import vosk
        except ImportError:
            self._error = ("модуль Vosk не установлен. Установите: "
                           "pip install vosk (в окружении программы)")
            log.error(self._error)
            return False

        model_path = self.resolve_model_path()
        if not model_path:
            self._error = ("модель Vosk не найдена. Загрузите её в настройках "
                           "(раздел «Голосовой ввод», кнопка загрузки модели).")
            log.warning(self._error)
            return False

        try:
            # Vosk печатает много служебного в stderr — приглушаем.
            vosk.SetLogLevel(-1)
            started = time.time()
            self._model = vosk.Model(model_path)
            log.info("Модель Vosk загружена за %.1f с: %s",
                     time.time() - started, model_path)
        except Exception as exc:  # noqa: BLE001
            self._error = f"не удалось загрузить модель Vosk: {exc}"
            log.error(self._error)
            return False

        self._ready = True
        return True

    # --- Распознавание ----------------------------------------------------

    def _make_recognizer(self):
        import vosk
        # Без грамматики Vosk распознаёт свободную речь — это нужно,
        # чтобы понимать произвольные команды пользователя.
        return vosk.KaldiRecognizer(self._model, SAMPLE_RATE)

    def start_stream(self, on_partial: Callable[[str], None] | None = None) -> bool:
        if not self.prepare():
            return False
        with self._lock:
            try:
                self._recognizer = self._make_recognizer()
                self._recognizer.SetWords(False)
            except Exception as exc:  # noqa: BLE001
                self._error = f"не удалось создать распознаватель: {exc}"
                log.error(self._error)
                return False
            self._partial = []
        return True

    def feed(self, pcm: bytes) -> str:
        """Подать кадр. Возвращает промежуточный текст, если он появился."""
        with self._lock:
            if self._recognizer is None:
                return ""
            try:
                if self._recognizer.AcceptWaveform(pcm):
                    # Фраза завершена — забираем итог.
                    result = json.loads(self._recognizer.Result())
                    return result.get("text", "")
                partial = json.loads(self._recognizer.PartialResult())
                return partial.get("partial", "")
            except Exception as exc:  # noqa: BLE001
                log.debug("Сбой потокового распознавания: %s", exc)
                return ""

    def finish_stream(self) -> Recognition:
        started = time.time()
        with self._lock:
            if self._recognizer is None:
                return Recognition(engine=self.name, error="распознавание не запущено")
            try:
                result = json.loads(self._recognizer.FinalResult())
            except Exception as exc:  # noqa: BLE001
                return Recognition(engine=self.name, error=str(exc))
            finally:
                self._recognizer = None

        text = (result.get("text") or "").strip()
        return Recognition(text=text, engine=self.name,
                           confidence=0.9 if text else 0.0,
                           elapsed=time.time() - started)

    def recognize(self, pcm: bytes) -> Recognition:
        """Распознать фразу целиком."""
        started = time.time()
        if not self.prepare():
            return Recognition(engine=self.name, error=self._error)

        with self._lock:
            try:
                import vosk
                recognizer = self._make_recognizer()
                recognizer.AcceptWaveform(pcm)
                result = json.loads(recognizer.FinalResult())
            except Exception as exc:  # noqa: BLE001
                return Recognition(engine=self.name, error=str(exc),
                                   elapsed=time.time() - started)

        text = (result.get("text") or "").strip()
        return Recognition(
            text=text,
            engine=self.name,
            confidence=0.9 if text else 0.0,
            duration=len(pcm) / (SAMPLE_RATE * 2),
            elapsed=time.time() - started,
        )

    def close(self) -> None:
        with self._lock:
            self._recognizer = None
            self._model = None
            self._ready = False


# --- Whisper ----------------------------------------------------------------

# Размеры моделей faster-whisper и их примерная память.
WHISPER_MODELS = {
    "tiny": "~40 МБ",
    "base": "~75 МБ",
    "small": "~250 МБ",
    "medium": "~800 МБ",
    "large-v3": "~1,6 ГБ",
}


class WhisperEngine(SpeechEngine):
    """Распознавание через faster-whisper. Точнее, но тяжелее."""

    name = "whisper"
    supports_streaming = False

    def __init__(self, language: str = "ru", model_size: str = "small",
                 device: str = "auto", compute_type: str = "int8") -> None:
        super().__init__(language)
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None
        self._lock = threading.RLock()

    @property
    def model_size(self) -> str:
        return self._model_size

    def prepare(self) -> bool:
        if self._ready:
            return True

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            self._error = ("модуль faster-whisper не установлен. Установите: "
                           "pip install faster-whisper")
            log.error(self._error)
            return False

        device = self._device
        compute = self._compute_type
        if device == "auto":
            device = self._pick_device()
        # На процессоре float16 не поддерживается.
        if device == "cpu" and compute in ("float16",):
            compute = "int8"

        try:
            started = time.time()
            self._model = WhisperModel(
                self._model_size, device=device, compute_type=compute)
            log.info("Модель Whisper (%s) загружена за %.1f с на %s",
                     self._model_size, time.time() - started, device)
        except Exception as exc:  # noqa: BLE001
            # Откат на процессор: видеокарта может не иметь нужных библиотек.
            if device != "cpu":
                log.warning("Не удалось использовать %s (%s), перехожу на процессор",
                            device, exc)
                try:
                    self._model = WhisperModel(self._model_size, device="cpu",
                                               compute_type="int8")
                    device = "cpu"
                except Exception as second:  # noqa: BLE001
                    self._error = f"не удалось загрузить Whisper: {second}"
                    log.error(self._error)
                    return False
            else:
                self._error = f"не удалось загрузить Whisper: {exc}"
                log.error(self._error)
                return False

        self._device_used = device
        self._ready = True
        return True

    def _pick_device(self) -> str:
        """Выбрать устройство: видеокарта NVIDIA, если доступна."""
        try:
            import ctranslate2
            if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
                return "cuda"
        except Exception:  # noqa: BLE001
            pass
        return "cpu"

    def recognize(self, pcm: bytes) -> Recognition:
        started = time.time()
        if not self.prepare():
            return Recognition(engine=self.name, error=self._error)

        wav_bytes = pcm_to_wav(pcm, SAMPLE_RATE)
        return self._recognize_wav(wav_bytes, started)

    def recognize_file(self, path: str) -> Recognition:
        """Распознать звуковой файл — для диктовки и голосовых сообщений."""
        started = time.time()
        if not self.prepare():
            return Recognition(engine=self.name, error=self._error)
        target = Path(path).expanduser()
        if not target.exists():
            return Recognition(engine=self.name, error=f"файл не найден: {target}")
        return self._recognize_wav(str(target), started)

    def _recognize_wav(self, source, started: float) -> Recognition:
        """Общая часть: распознать файл или поток байтов."""
        import io

        prepared = source
        if isinstance(source, bytes):
            prepared = io.BytesIO(source)

        language = self.language if self.language in ("ru", "en") else None

        with self._lock:
            try:
                segments, info = self._model.transcribe(
                    prepared,
                    language=language,
                    beam_size=3,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 400},
                    condition_on_previous_text=False,
                )
                parts = [segment.text for segment in segments]
            except Exception as exc:  # noqa: BLE001
                return Recognition(engine=self.name, error=str(exc),
                                   elapsed=time.time() - started)

        text = " ".join(part.strip() for part in parts).strip()
        confidence = getattr(info, "language_probability", 0.0) if info else 0.0
        return Recognition(
            text=text,
            engine=self.name,
            confidence=float(confidence or 0.0),
            elapsed=time.time() - started,
        )

    def close(self) -> None:
        with self._lock:
            self._model = None
            self._ready = False


# --- Облачное распознавание -------------------------------------------------

class CloudEngine(SpeechEngine):
    """Распознавание через API провайдера. Требует ключ и интернет."""

    name = "cloud"
    supports_streaming = False

    def __init__(self, language: str = "ru", provider: str = "openai",
                 api_key: str = "", base_url: str = "") -> None:
        super().__init__(language)
        self._provider = provider
        self._api_key = api_key
        self._base_url = base_url

    def configure(self, provider: str, api_key: str, base_url: str = "") -> None:
        self._provider = provider
        self._api_key = api_key
        self._base_url = base_url

    def prepare(self) -> bool:
        if not self._api_key:
            self._error = "не задан ключ API для облачного распознавания"
            return False
        try:
            import requests  # noqa: F401
        except ImportError:
            self._error = "модуль requests не установлен"
            return False
        self._ready = True
        return True

    def recognize(self, pcm: bytes) -> Recognition:
        started = time.time()
        if not self.prepare():
            return Recognition(engine=self.name, error=self._error)

        wav_bytes = pcm_to_wav(pcm, SAMPLE_RATE)

        if self._provider == "openai":
            return self._recognize_openai(wav_bytes, started)
        if self._provider == "gemini":
            return self._recognize_gemini(wav_bytes, started)
        if self._provider == "deepseek":
            return Recognition(
                engine=self.name,
                error="DeepSeek не предоставляет распознавание речи. "
                      "Выберите OpenAI, Gemini или локальную модель.",
            )
        return self._recognize_custom(wav_bytes, started)

    def _recognize_openai(self, wav_bytes: bytes, started: float) -> Recognition:
        import requests

        url = (self._base_url.rstrip("/") if self._base_url
               else "https://api.openai.com/v1") + "/audio/transcriptions"
        language = self.language if self.language in ("ru", "en") else None

        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"model": "whisper-1", **({"language": language} if language else {})},
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            return Recognition(engine=self.name, error=f"сеть недоступна: {exc}",
                               elapsed=time.time() - started)

        if response.status_code != 200:
            return Recognition(
                engine=self.name,
                error=f"сервис вернул ошибку {response.status_code}: "
                      f"{response.text[:200]}",
                elapsed=time.time() - started,
            )

        try:
            text = (response.json().get("text") or "").strip()
        except ValueError:
            return Recognition(engine=self.name, error="неожиданный ответ сервиса",
                               elapsed=time.time() - started)

        return Recognition(text=text, engine="openai-whisper", confidence=0.9,
                           elapsed=time.time() - started)

    def _recognize_gemini(self, wav_bytes: bytes, started: float) -> Recognition:
        import base64

        import requests

        model = "gemini-2.0-flash"
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={self._api_key}")

        payload = {
            "contents": [{
                "parts": [
                    {"text": "Transcribe this audio. Return only the text, "
                             "without comments."},
                    {"inline_data": {
                        "mime_type": "audio/wav",
                        "data": base64.b64encode(wav_bytes).decode(),
                    }},
                ],
            }],
        }

        try:
            response = requests.post(url, json=payload, timeout=60)
        except Exception as exc:  # noqa: BLE001
            return Recognition(engine=self.name, error=f"сеть недоступна: {exc}",
                               elapsed=time.time() - started)

        if response.status_code != 200:
            return Recognition(engine=self.name,
                               error=f"сервис вернул ошибку {response.status_code}",
                               elapsed=time.time() - started)
        try:
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, ValueError, TypeError):
            return Recognition(engine=self.name, error="неожиданный ответ сервиса",
                               elapsed=time.time() - started)
        return Recognition(text=text, engine="gemini", confidence=0.85,
                           elapsed=time.time() - started)

    def _recognize_custom(self, wav_bytes: bytes, started: float) -> Recognition:
        """Свой сервер, совместимый с OpenAI."""
        if not self._base_url:
            return Recognition(engine=self.name,
                               error="не задан адрес сервера распознавания")
        return self._recognize_openai(wav_bytes, started)


# --- Загрузка моделей -------------------------------------------------------

@dataclass
class DownloadProgress:
    """Ход загрузки модели."""

    name: str = ""
    downloaded: int = 0
    total: int = 0
    done: bool = False
    error: str = ""

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.downloaded * 100 / self.total))


def download_vosk_model(language: str = "ru",
                        progress: Callable[[DownloadProgress], None] | None = None,
                        stop_event: threading.Event | None = None) -> tuple[bool, str]:
    """Загрузить и расписать модель Vosk.

    Возвращает (успех, сообщение). Загрузка идёт в отдельном потоке,
    если вызвана из интерфейса.
    """
    info = VOSK_MODELS.get(language)
    if info is None:
        return False, f"нет модели для языка {language}"

    try:
        import requests
    except ImportError:
        return False, "модуль requests не установлен"

    import zipfile

    paths.ensure_dirs()
    target_dir = paths.models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / f"{info['name']}.zip"

    status = DownloadProgress(name=info["name"])
    if progress:
        progress(status)

    # --- Скачивание ---
    try:
        with requests.get(info["url"], stream=True, timeout=30) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0) or 0)
            status.total = total
            downloaded = 0

            with open(archive, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if stop_event is not None and stop_event.is_set():
                        handle.close()
                        archive.unlink(missing_ok=True)
                        return False, "загрузка отменена"
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    status.downloaded = downloaded
                    if progress:
                        progress(status)
    except Exception as exc:  # noqa: BLE001
        status.error = str(exc)
        if progress:
            progress(status)
        return False, f"не удалось скачать модель: {exc}"

    # --- Распаковка ---
    try:
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(target_dir)
    except Exception as exc:  # noqa: BLE001
        status.error = str(exc)
        if progress:
            progress(status)
        return False, f"не удалось распаковать модель: {exc}"

    try:
        archive.unlink()
    except OSError:
        pass

    status.done = True
    if progress:
        progress(status)
    return True, f"модель {info['name']} установлена"


def vosk_model_installed(language: str = "ru") -> bool:
    """Проверить, установлена ли модель Vosk для языка."""
    engine = VoskEngine(language)
    return bool(engine.resolve_model_path())


def available_tools() -> dict[str, bool]:
    """Какие движки распознавания доступны в системе."""
    result = {"vosk": False, "whisper": False, "webrtcvad": False}
    try:
        import vosk  # noqa: F401
        result["vosk"] = True
    except ImportError:
        pass
    try:
        import faster_whisper  # noqa: F401
        result["whisper"] = True
    except ImportError:
        pass
    try:
        # webrtcvad несовместим с Python 3.13 и новее (требует pkg_resources),
        # поэтому проверяем не только наличие, но и работоспособность.
        import webrtcvad  # noqa: F401
        webrtcvad.Vad(2)
        result["webrtcvad"] = True
    except Exception:  # noqa: BLE001
        # Определение речи всё равно работает: применяется собственная
        # спектральная оценка на numpy.
        result["webrtcvad"] = False
    return result