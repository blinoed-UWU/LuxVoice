"""Синтез речи: локальные голоса, облачные голоса и характер ответов.

Движки:
  * Piper — нейросетевые голоса, звучат естественно, работают офлайн;
  * RHVoice — хороший русский синтез, есть в репозиториях Linux;
  * eSpeak NG — есть почти везде, звучит машинно, но всегда доступен;
  * облако — OpenAI, ElevenLabs и совместимые серверы, по ключу.

Характер — это набор фраз для типовых ситуаций: обращение услышано,
команда выполнена, произошла ошибка, нужно подтверждение. Наборы
задаются готовыми пресетами, а свои фразы всегда имеют приоритет.

Голоса персонажей из фильмов и игр не входят в поставку: это чужие
защищённые образы. Вместо них — свободные голоса с настройками
высоты, скорости и тембра, из которых собирается нужный характер.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from luxvoice.core import paths
from luxvoice.core.events import TTS_ERROR, TTS_FINISHED, TTS_STARTED, bus
from luxvoice.sysint.audio import get_audio

log = logging.getLogger(__name__)


# --- Характеры --------------------------------------------------------------

# Наборы фраз. Ключи: activate, ok, error, confirm, start, stop, thinking,
# not_found, need_prefix, bye, plus варианты для разнообразия.
PERSONALITIES: dict[str, dict[str, list[str]]] = {
    "butler": {
        "activate": ["Слушаю", "К вашим услугам", "Слушаю вас"],
        "ok": ["Готово", "Выполнено", "Сделано", "Слушаюсь"],
        "error": ["Не удалось", "Прошу прощения, не вышло", "Не получилось"],
        "confirm": ["Подтвердите", "Требуется подтверждение"],
        "start": ["Системы готовы", "Ассистент к работе готов", "Все системы в норме"],
        "thinking": ["Секунду", "Одну минуту"],
        "not_found": ["Не понял команду", "Такой команды нет", "Уточните, пожалуйста"],
        "need_prefix": ["Нужно обращение", "Обратитесь ко мне"],
        "denied": ["Отменено", "Как пожелаете"],
        "bye": ["До встречи", "Всего доброго"],
    },
    "friendly": {
        "activate": ["Слушаю!", "Да?", "Я здесь", "Что нужно?"],
        "ok": ["Готово!", "Сделал!", "Есть!", "Всё сделано"],
        "error": ["Ой, не получилось", "Что-то пошло не так", "Не вышло, извини"],
        "confirm": ["Точно делаем?", "Подтверди, пожалуйста"],
        "start": ["Привет! Я на месте", "Привет, я готов помогать"],
        "thinking": ["Думаю…", "Сейчас посмотрю"],
        "not_found": ["Не понял, повтори", "Хм, такой команды нет"],
        "need_prefix": ["Позови меня по имени", "Скажи обращение"],
        "denied": ["Ладно, отменил", "Хорошо, не делаю"],
        "bye": ["Пока!", "До связи"],
    },
    "terse": {
        "activate": ["Да", "Слушаю"],
        "ok": ["Готово", "Есть"],
        "error": ["Ошибка", "Не вышло"],
        "confirm": ["Подтвердить?"],
        "start": ["Готов"],
        "thinking": ["Секунду"],
        "not_found": ["Не найдено"],
        "need_prefix": ["Скажите обращение"],
        "denied": ["Отменено"],
        "bye": ["Выключаюсь"],
    },
    "gamer": {
        "activate": ["На связи", "Готов к бою", "Слушаю, командир"],
        "ok": ["Сделано, командир", "Задача выполнена", "Есть!"],
        "error": ["Провал задачи", "Не вышло, командир"],
        "confirm": ["Подтверждаешь?", "Выполняем?"],
        "start": ["Система в сети", "Загружаюсь, всё в норме"],
        "thinking": ["Считаю", "Секунду, обрабатываю"],
        "not_found": ["Неизвестная команда", "Нет такой директивы"],
        "need_prefix": ["Нужен позывной", "Активируй обращение"],
        "denied": ["Отменено, стоим"],
        "bye": ["Отключаюсь"],
    },
    "calm": {
        "activate": ["Слушаю", "Я здесь"],
        "ok": ["Сделано", "Готово"],
        "error": ["Не удалось выполнить", "Возникла сложность"],
        "confirm": ["Пожалуйста, подтвердите"],
        "start": ["Ассистент готов", "Всё готово к работе"],
        "thinking": ["Обрабатываю запрос"],
        "not_found": ["Команда не распознана"],
        "need_prefix": ["Требуется обращение"],
        "denied": ["Действие отменено"],
        "bye": ["До встречи"],
    },
    "none": {
        "activate": ["Слушаю"],
        "ok": ["Готово"],
        "error": ["Не удалось"],
        "confirm": ["Подтвердите"],
        "start": ["Ассистент готов к работе"],
        "thinking": ["Секунду"],
        "not_found": ["Команда не найдена"],
        "need_prefix": ["Нужен префикс"],
        "denied": ["Отменено"],
        "bye": ["До встречи"],
    },
}


# --- Голоса -----------------------------------------------------------------

@dataclass
class Voice:
    """Доступный голос синтеза."""

    id: str
    name: str
    engine: str
    language: str = "ru"
    gender: str = ""
    path: str = ""          # путь к модели, если есть
    size: str = ""
    quality: str = ""

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.gender:
            parts.append("мужской" if self.gender == "male" else "женский")
        if self.language:
            parts.append(self.language.upper())
        return " — ".join(parts)


@dataclass
class SpeakRequest:
    """Запрос на произнесение."""

    text: str
    interrupt: bool = True
    volume: float | None = None
    voice: str = ""
    priority: int = 0


class SpeechSynthesizer:
    """Произнесение фраз: очередь, прерывание, разные движки."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._queue: list[SpeakRequest] = []
        self._event = threading.Event()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current: SpeakRequest | None = None
        self._current_process: subprocess.Popen | None = None
        self._speaking = threading.Event()
        self._engine_used = ""
        self._error = ""
        self._cache_lock = threading.RLock()
        # Загруженные голоса Piper держим в памяти: загрузка модели
        # занимает около секунды, а фразы идут часто.
        self._piper_cache: dict[str, object] = {}

        # Наблюдатели состояния «говорит».
        self._listeners: list[Callable[[bool], None]] = []

        self._start_worker()

    # --- Настройка --------------------------------------------------------

    def set_settings(self, settings) -> None:
        self._settings = settings

    def on_speaking_changed(self, handler: Callable[[bool], None]) -> None:
        """Оповещать о начале и конце речи — нужно для паузы микрофона."""
        self._listeners.append(handler)

    def _notify_speaking(self, speaking: bool) -> None:
        for handler in list(self._listeners):
            try:
                handler(speaking)
            except Exception as exc:  # noqa: BLE001
                log.debug("Обработчик состояния речи упал: %s", exc)

    # --- Движки -----------------------------------------------------------

    def available_engines(self) -> dict[str, bool]:
        """Какие движки синтеза есть в системе."""
        result = {
            "piper": False,
            "rhvoice": False,
            "espeak": False,
            "cloud": False,
        }
        # Piper может быть как программа, так и модуль Python.
        if shutil.which("piper") or shutil.which("piper-tts"):
            result["piper"] = True
        else:
            try:
                import piper  # noqa: F401
                result["piper"] = True
            except ImportError:
                pass

        if shutil.which("RHVoice-test") or shutil.which("rhvoice-client"):
            result["rhvoice"] = True

        if shutil.which("espeak-ng") or shutil.which("espeak"):
            result["espeak"] = True

        if self._settings is not None and self._cloud_key():
            result["cloud"] = True

        return result

    def _cloud_key(self) -> str:
        """Ключ для облачной озвучки."""
        if self._settings is None:
            return ""
        provider = self._settings.text("tts.cloud_provider", "openai")
        keys = self._settings.get("ai.keys") or {}
        if isinstance(keys, dict):
            key = str(keys.get("openai", "") or "") if provider == "openai" else ""
            if key:
                return key
            # ElevenLabs хранится отдельным ключом.
            key = str(keys.get(provider, "") or "")
            if key:
                return key
        return self._settings.text("ai.api_key", "")

    def _pick_engine(self) -> str:
        """Выбрать движок с учётом настроек и доступности."""
        wanted = self._settings.text("tts.engine", "auto") if self._settings else "auto"
        engines = self.available_engines()

        if wanted == "auto":
            for candidate in ("rhvoice", "piper", "espeak"):
                if engines.get(candidate):
                    return candidate
            return ""

        # Если выбран облачный, но ключа нет — переходим на локальный.
        if wanted == "cloud" and not engines.get("cloud"):
            log.warning("Облачная озвучка недоступна без ключа — использую локальный голос")
            for candidate in ("rhvoice", "piper", "espeak"):
                if engines.get(candidate):
                    return candidate
            return ""

        # Выбранный движок недоступен — берём любой доступный.
        if not engines.get(wanted):
            for candidate in ("rhvoice", "piper", "espeak", "cloud"):
                if engines.get(candidate):
                    return candidate
            return ""

        return wanted

    def voices(self, engine: str = "") -> list[Voice]:
        """Список голосов выбранного движка."""
        engine = engine or self._pick_engine()
        if not engine:
            return []
        if engine == "rhvoice":
            return self._rhvoice_voices()
        if engine == "piper":
            return self._piper_voices()
        if engine == "espeak":
            return self._espeak_voices()
        if engine == "cloud":
            return self._cloud_voices()
        return []

    def _rhvoice_voices(self) -> list[Voice]:
        """Голоса RHVoice — спрашиваем у самого синтезатора."""
        binary = shutil.which("RHVoice-test") or shutil.which("rhvoice-client")
        if not binary:
            return []

        voices: list[Voice] = []
        try:
            completed = subprocess.run([binary, "--voices"], capture_output=True,
                                       text=True, timeout=10)
            if completed.returncode == 0:
                for line in completed.stdout.splitlines():
                    text = line.strip()
                    if not text:
                        continue
                    # Формат: "aleksandr - Russian male voice"
                    match = re.match(r"^([\w-]+)\s*-\s*(.+)$", text)
                    if not match:
                        continue
                    voice_id, description = match.group(1), match.group(2)
                    lowered = description.lower()
                    language = "ru" if "russian" in lowered else (
                        "en" if "english" in lowered else "")
                    gender = "male" if "male" in lowered and "female" not in lowered else (
                        "female" if "female" in lowered else "")
                    voices.append(Voice(
                        id=voice_id, name=voice_id.capitalize(), engine="rhvoice",
                        language=language, gender=gender, quality=description,
                    ))
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("Не удалось получить голоса RHVoice: %s", exc)

        if not voices:
            # Запасной список типовых голосов RHVoice.
            for voice_id, gender in (("aleksandr", "male"), ("elena", "female"),
                                     ("irina", "female"), ("artemiy", "male"),
                                     ("mikhail", "male"), ("tatiana", "female")):
                voices.append(Voice(id=voice_id, name=voice_id.capitalize(),
                                    engine="rhvoice", language="ru", gender=gender))
        return voices

    def _piper_voices(self) -> list[Voice]:
        """Голоса Piper, найденные в каталоге данных."""
        voices: list[Voice] = []
        directory = paths.voices_dir()
        try:
            for model in sorted(directory.glob("*.onnx")):
                name = model.stem
                # Имена вида ru_RU-irina-medium.
                parts = name.split("-")
                language = parts[0].split("_")[0].lower() if parts else "ru"
                speaker = parts[1] if len(parts) > 1 else name
                quality = parts[2] if len(parts) > 2 else ""
                voices.append(Voice(
                    id=name, name=speaker.replace("_", " ").capitalize(),
                    engine="piper", language=language,
                    path=str(model), quality=quality,
                ))
        except OSError:
            pass
        return voices

    def _espeak_voices(self) -> list[Voice]:
        """Голоса eSpeak NG."""
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            return []

        voices: list[Voice] = []
        try:
            completed = subprocess.run([binary, "--voices"], capture_output=True,
                                       text=True, timeout=10)
            if completed.returncode == 0:
                for line in completed.stdout.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    language, gender, _age, name = parts[0], parts[1], parts[2], parts[3]
                    if not language.startswith(("ru", "en")):
                        continue
                    voices.append(Voice(
                        id=f"{language}+{name}", name=f"{name} ({language})",
                        engine="espeak", language=language[:2],
                        gender="male" if gender == "M" else "female",
                    ))
        except (OSError, subprocess.TimeoutExpired):
            pass

        if not voices:
            voices = [
                Voice(id="ru", name="Русский", engine="espeak", language="ru"),
                Voice(id="en", name="English", engine="espeak", language="en"),
            ]
        return voices

    def _cloud_voices(self) -> list[Voice]:
        """Голоса облачных сервисов."""
        provider = self._settings.text("tts.cloud_provider", "openai") \
            if self._settings else "openai"
        if provider == "elevenlabs":
            return [
                Voice(id="Rachel", name="Rachel", engine="cloud", language="en"),
                Voice(id="Adam", name="Adam", engine="cloud", language="en"),
                Voice(id="Bella", name="Bella", engine="cloud", language="en"),
                Voice(id="Antoni", name="Antoni", engine="cloud", language="en"),
            ]
        return [
            Voice(id="alloy", name="Alloy", engine="cloud"),
            Voice(id="echo", name="Echo", engine="cloud"),
            Voice(id="fable", name="Fable", engine="cloud"),
            Voice(id="onyx", name="Onyx", engine="cloud"),
            Voice(id="nova", name="Nova", engine="cloud"),
            Voice(id="shimmer", name="Shimmer", engine="cloud"),
        ]

    # --- Характер ---------------------------------------------------------

    def phrase(self, kind: str, default: str = "") -> str:
        """Взять фразу нужного типа с учётом характера и своих правок."""
        # 1. Свои фразы из настроек — самый высокий приоритет.
        if self._settings is not None:
            custom = self._settings.text("tts.custom_phrases", "")
            if custom:
                parsed = self._parse_custom(custom)
                if kind in parsed:
                    return parsed[kind]

            # 2. Отдельные поля настроек.
            field_map = {
                "activate": "tts.on_activate_phrase",
                "ok": "tts.on_success_phrase",
                "error": "tts.on_error_phrase",
                "not_found": "app.unknown_phrase",
                "start": "app.greeting",
            }
            key = field_map.get(kind)
            if key:
                value = self._settings.text(key, "")
                if value:
                    return value

        # 3. Готовый набор характера.
        personality = self._settings.text("tts.personality", "butler") \
            if self._settings else "butler"
        variants = PERSONALITIES.get(personality, PERSONALITIES["butler"])
        options = variants.get(kind) or PERSONALITIES["none"].get(kind)
        if options:
            # Разнообразие: выбираем вариант по кругу, чтобы не повторяться.
            with self._lock:
                counters = getattr(self, "_counters", {})
                index = counters.get(kind, 0)
                counters[kind] = index + 1
                self._counters = counters
            return options[index % len(options)]

        return default

    @staticmethod
    def _parse_custom(text: str) -> dict[str, str]:
        """Разобрать «ключ=фраза» из настроек."""
        result: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().lower()
            value = value.strip()
            if key and value:
                result[key] = value
        return result

    # --- Произнесение -----------------------------------------------------

    def say(self, text: str, interrupt: bool = True, priority: int = 0,
            voice: str = "") -> None:
        """Поставить фразу в очередь на произнесение."""
        text = (text or "").strip()
        if not text:
            return
        if self._settings is not None and not self._settings.flag("tts.enabled", True):
            return

        # Ограничение длины: не читаем вслух целые статьи.
        limit = int(self._settings.number("tts.max_length", 400)) \
            if self._settings else 400
        if limit > 0 and len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0] + "…"

        request = SpeakRequest(text=text, interrupt=interrupt, priority=priority,
                               voice=voice)

        with self._lock:
            if interrupt and self._speaking.is_set():
                self._kill_current()
                # Прерывание: очищаем очередь, кроме приоритетных.
                self._queue = [r for r in self._queue if r.priority > priority]

            max_queue = int(self._settings.number("tts.queue_max", 3)) \
                if self._settings else 3
            if len(self._queue) >= max_queue:
                # Выбрасываем самую старую фразу с наименьшим приоритетом.
                self._queue.sort(key=lambda r: r.priority)
                self._queue.pop(0)
            self._queue.append(request)
            self._queue.sort(key=lambda r: -r.priority)

        self._event.set()

    def say_phrase(self, kind: str, default: str = "") -> None:
        """Произнести готовую фразу характера."""
        text = self.phrase(kind, default)
        if text:
            self.say(text)

    def stop(self, clear_queue: bool = True) -> None:
        """Прекратить речь."""
        with self._lock:
            if clear_queue:
                self._queue.clear()
            self._kill_current()
        self._event.set()

    def _kill_current(self) -> None:
        """Остановить текущее произнесение."""
        process = self._current_process
        if process is not None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
            except OSError:
                pass
            self._current_process = None

        # Останавливаем проигрывание файлов, если звук идёт через него.
        get_audio().stop_playback()

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Дождаться окончания речи."""
        deadline = time.time() + timeout if timeout else None
        while self._speaking.is_set():
            if deadline and time.time() > deadline:
                return False
            time.sleep(0.05)
        return True

    def _start_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._work_loop, name="tts-worker",
                                        daemon=True)
        self._worker.start()

    def _work_loop(self) -> None:
        """Поток озвучки: берёт фразы из очереди и произносит."""
        while not self._stop_event.is_set():
            request: SpeakRequest | None = None
            with self._lock:
                if self._queue:
                    request = self._queue.pop(0)

            if request is None:
                self._event.wait(timeout=0.3)
                self._event.clear()
                continue

            try:
                self._speak(request)
            except Exception as exc:  # noqa: BLE001
                log.exception("Ошибка озвучки: %s", exc)
                self._error = str(exc)
                bus.publish(TTS_ERROR, message=str(exc))

    def _speak(self, request: SpeakRequest) -> None:
        """Произнести одну фразу выбранным движком."""
        self._current = request
        self._speaking.set()
        self._notify_speaking(True)
        bus.publish(TTS_STARTED, text=request.text)

        try:
            engine = self._pick_engine()
            self._engine_used = engine

            if not engine:
                # Нет ни одного синтезатора — сообщаем один раз.
                message = ("Не найден ни один движок синтеза речи. "
                           "Установите RHVoice, Piper или eSpeak NG.")
                if self._error != message:
                    self._error = message
                    log.error(message)
                    bus.publish(TTS_ERROR, message=message)
                return

            volume = request.volume
            if volume is None and self._settings is not None:
                volume = self._settings.number("tts.volume", 80) / 100.0

            if engine == "cloud":
                self._speak_cloud(request, volume or 0.8)
            elif engine == "rhvoice":
                self._speak_rhvoice(request, volume or 0.8)
            elif engine == "piper":
                self._speak_piper(request, volume or 0.8)
            elif engine == "espeak":
                self._speak_espeak(request, volume or 0.8)

        finally:
            self._current = None
            self._speaking.clear()
            self._notify_speaking(False)
            bus.publish(TTS_FINISHED, text=request.text)

    # --- Реализации движков ----------------------------------------------

    def _rate_params(self) -> tuple[int, int]:
        """Скорость и тон из настроек."""
        rate = int(self._settings.number("tts.rate", 0)) if self._settings else 0
        pitch = int(self._settings.number("tts.pitch", 0)) if self._settings else 0
        return rate, pitch

    def _voice_id(self, request: SpeakRequest, engine: str) -> str:
        """Выбранный голос."""
        if request.voice:
            return request.voice
        if self._settings is None:
            return ""
        voice = self._settings.text("tts.voice", "")
        if voice:
            return voice
        voices = self.voices(engine)
        return voices[0].id if voices else ""

    def _speak_rhvoice(self, request: SpeakRequest, volume: float) -> None:
        """Озвучка через RHVoice."""
        binary = shutil.which("RHVoice-test") or shutil.which("rhvoice-client")
        if not binary:
            return

        voice = self._voice_id(request, "rhvoice")
        rate, pitch = self._rate_params()

        args = [binary]
        if voice:
            args.extend(["-v", voice])
        # Громкость RHVoice задаётся в процентах.
        args.extend(["-p", str(max(0, min(100, int(volume * 100))))])
        if rate:
            # Скорость: базовое значение 0, допустимы отрицательные.
            args.extend(["-r", str(rate)])
        args.extend(["-t", "text", "-"])

        self._run_with_stdin(args, request.text.encode("utf-8"))

    def _speak_piper(self, request: SpeakRequest, volume: float) -> None:
        """Озвучка через Piper."""
        voice_id = self._voice_id(request, "piper")
        model_path = ""

        voices = {voice.id: voice for voice in self.voices("piper")}
        if voice_id in voices:
            model_path = voices[voice_id].path
        if not model_path:
            # Ищем хоть какую-нибудь модель.
            found = list(paths.voices_dir().glob("*.onnx"))
            if not found:
                log.debug("Модель Piper не найдена — перехожу на другой движок")
                self._speak_espeak(request, volume)
                return
            model_path = str(found[0])

        rate, pitch = self._rate_params()
        # Piper: длина фразы задаётся параметром length_scale
        # (больше — медленнее).
        length_scale = max(0.5, min(2.0, 1.0 - rate / 150.0))

        binary = shutil.which("piper") or shutil.which("piper-tts")
        wav_path = self._temp_wav("piper")

        # Вариативность голоса: выше тон — чуть больше «выразительности».
        noise_scale = max(0.0, min(1.5, 0.667 + pitch / 200))

        if binary:
            args = [binary, "--model", model_path, "--output_file", wav_path,
                    "--length_scale", f"{length_scale:.2f}",
                    "--noise_scale", f"{noise_scale:.3f}"]
            ok = self._run_with_stdin(args, request.text.encode("utf-8"))
        else:
            ok = self._piper_python(model_path, request.text, wav_path,
                                    length_scale, noise_scale)

        if ok:
            self._play_wav(wav_path, volume)
        self._cleanup(wav_path)

    def _piper_python(self, model_path: str, text: str, output: str,
                      length_scale: float = 1.0,
                      noise_scale: float = 0.667) -> bool:
        """Piper как модуль Python, если программа не установлена.

        Поддерживаются обе версии API: новая (synthesize_wav с
        SynthesisConfig) и старая (synthesize с записью в файл).
        """
        try:
            from piper import PiperVoice
        except ImportError:
            return False

        import wave

        # Голос из кэша — загрузка модели стоит около секунды.
        with self._cache_lock:
            voice = self._piper_cache.get(model_path)

        if voice is None:
            try:
                voice = PiperVoice.load(model_path)
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось загрузить голос Piper (%s): %s", model_path, exc)
                return False
            with self._cache_lock:
                # Держим не больше трёх голосов: каждая модель — десятки мегабайт.
                if len(self._piper_cache) >= 3:
                    self._piper_cache.pop(next(iter(self._piper_cache)))
                self._piper_cache[model_path] = voice

        # Настройки синтеза: темп и вариативность.
        config = None
        try:
            from piper.config import SynthesisConfig
            config = SynthesisConfig(
                length_scale=max(0.3, min(3.0, length_scale)),
                noise_scale=max(0.0, min(1.5, noise_scale)),
                normalize_audio=True,
            )
        except (ImportError, TypeError, ValueError):
            config = None

        # --- Новый API ---
        if hasattr(voice, "synthesize_wav"):
            try:
                with wave.open(output, "wb") as handle:
                    voice.synthesize_wav(text, handle, syn_config=config,
                                         set_wav_format=True)
                if os.path.getsize(output) > 44:  # больше пустого заголовка
                    return True
            except Exception as exc:  # noqa: BLE001
                log.debug("Новый API Piper не сработал: %s", exc)

        # --- Старый API ---
        try:
            with wave.open(output, "wb") as handle:
                voice.synthesize(text, handle)
            return os.path.getsize(output) > 44
        except TypeError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("Старый API Piper не сработал: %s", exc)

        # --- Совсем старый API: синтез в файл напрямую ---
        try:
            with wave.open(output, "wb") as handle:
                for chunk in voice.synthesize(text, syn_config=config):
                    if hasattr(chunk, "audio_int16_bytes"):
                        handle.writeframes(chunk.audio_int16_bytes)
            return os.path.getsize(output) > 44
        except Exception as exc:  # noqa: BLE001
            log.warning("Синтез Piper не удался: %s", exc)
            return False

    def _speak_espeak(self, request: SpeakRequest, volume: float) -> None:
        """Озвучка через eSpeak NG."""
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            return

        voice = self._voice_id(request, "espeak")
        rate, pitch = self._rate_params()

        args = [binary, "--stdin"]
        if voice and "+" in voice:
            args.extend(["-v", voice])
        args.extend(["-a", str(max(0, min(200, int(volume * 200))))])
        # Базовая скорость eSpeak — 175 слов в минуту.
        args.extend(["-s", str(max(80, min(450, 175 + rate * 2)))])
        if pitch:
            args.extend(["-p", str(max(0, min(99, 50 + pitch)))])

        self._run_with_stdin(args, request.text.encode("utf-8"))

    def _speak_cloud(self, request: SpeakRequest, volume: float) -> None:
        """Озвучка через облачный сервис."""
        key = self._cloud_key()
        if not key:
            self._speak_espeak(request, volume)
            return

        provider = self._settings.text("tts.cloud_provider", "openai") \
            if self._settings else "openai"
        voice = self._voice_id(request, "cloud") or (
            self._settings.text("tts.cloud_voice", "alloy")
            if self._settings else "alloy")

        wav_path = self._temp_wav("cloud")
        try:
            if provider == "elevenlabs":
                ok = self._cloud_elevenlabs(key, voice, request.text, wav_path)
            else:
                ok = self._cloud_openai(key, voice, request.text, wav_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Облачная озвучка не удалась (%s), использую локальную", exc)
            ok = False

        if ok:
            self._play_wav(wav_path, volume)
        else:
            self._speak_espeak(request, volume)
        self._cleanup(wav_path)

    def _cloud_openai(self, key: str, voice: str, text: str, output: str) -> bool:
        import requests

        base = "https://api.openai.com/v1"
        if self._settings is not None:
            custom = self._settings.text("ai.base_url", "")
            if custom and self._settings.text("tts.cloud_provider", "") == "custom":
                base = custom.rstrip("/")
        model = "tts-1"
        if self._settings is not None and self._settings.text("tts.cloud_provider", "") == "custom":
            model = "tts-1"

        response = requests.post(
            f"{base}/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "voice": voice or "alloy", "input": text,
                  "response_format": "wav"},
            timeout=60,
        )
        if response.status_code != 200:
            log.warning("Сервис озвучки вернул ошибку %s", response.status_code)
            return False
        Path(output).write_bytes(response.content)
        return True

    def _cloud_elevenlabs(self, key: str, voice: str, text: str, output: str) -> bool:
        import requests

        voice_id = voice or "Rachel"
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "Accept": "audio/mpeg"},
            json={"text": text, "model_id": "eleven_multilingual_v2"},
            timeout=60,
        )
        if response.status_code != 200:
            return False
        Path(output).write_bytes(response.content)
        return True

    # --- Утилиты ----------------------------------------------------------

    def _run_with_stdin(self, args: list[str], data: bytes) -> bool:
        """Запустить синтезатор, передав текст в стандартный ввод."""
        try:
            process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_process = process
            try:
                process.communicate(input=data, timeout=120)
            except subprocess.TimeoutExpired:
                process.kill()
                return False
            return process.returncode == 0
        except FileNotFoundError:
            return False
        except OSError as exc:
            log.debug("Сбой запуска синтезатора: %s", exc)
            return False
        finally:
            self._current_process = None

    def _temp_wav(self, prefix: str) -> str:
        """Путь для временного файла озвучки."""
        directory = paths.tts_cache_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            directory = Path(tempfile.gettempdir())
        handle, name = tempfile.mkstemp(prefix=f"{prefix}-", suffix=".wav",
                                        dir=str(directory))
        os.close(handle)
        return name

    def _play_wav(self, path: str, volume: float) -> None:
        """Проиграть файл с заданной громкостью."""
        audio = get_audio()
        # Громкость применяем к самому файлу, если он в формате WAV,
        # иначе используем системный уровень плеера.
        audio.play_file(path, volume=volume, wait=True)

    @staticmethod
    def _cleanup(path: str) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def diagnostics(self) -> dict[str, object]:
        engines = self.available_engines()
        return {
            "engine": self._engine_used or self._pick_engine(),
            "available": engines,
            "speaking": self.speaking,
            "queued": len(self._queue),
            "voice": self._settings.text("tts.voice", "") if self._settings else "",
            "error": self._error,
        }

    def close(self) -> None:
        self._stop_event.set()
        self._event.set()
        self.stop()


# --- Одиночный экземпляр ----------------------------------------------------

_synth: SpeechSynthesizer | None = None
_lock = threading.Lock()


def get_synthesizer(settings=None) -> SpeechSynthesizer:
    global _synth
    with _lock:
        if _synth is None:
            _synth = SpeechSynthesizer(settings)
        elif settings is not None:
            _synth.set_settings(settings)
        return _synth