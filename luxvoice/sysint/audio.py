"""Звук: системная громкость, микшер приложений, воспроизведение файлов.

Работает через PipeWire (pw-cli/wpctl) с запасным путём на PulseAudio
(pactl) и ALSA (amixer). Громкость отдельного приложения — через
pactl/PipeWire, если поток удаётся сопоставить с программой.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


def _run(cmd: list[str], timeout: float = 6.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout or "", result.stderr or ""
    except FileNotFoundError:
        return 127, "", f"программа не найдена: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "превышено время ожидания"
    except OSError as exc:
        return 1, "", str(exc)


def _has(name: str) -> bool:
    return shutil.which(name) is not None


@dataclass
class AudioDevice:
    """Устройство ввода или вывода."""

    index: int
    name: str
    description: str
    is_default: bool = False
    kind: str = "sink"     # sink — вывод, source — ввод

    @property
    def label(self) -> str:
        return self.description or self.name


class AudioManager:
    """Управление звуком системы."""

    def __init__(self) -> None:
        self._pactl = _has("pactl")
        self._wpctl = _has("wpctl")
        self._amixer = _has("amixer")
        self._pw_play = _has("pw-play")
        self._paplay = _has("paplay")
        self._ffplay = _has("ffplay")

    # --- Системная громкость ---------------------------------------------

    def volume(self) -> int:
        """Текущая громкость основного выхода в процентах."""
        if self._wpctl:
            code, out, _ = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if code == 0:
                match = re.search(r"Volume:\s*([\d.]+)", out)
                if match:
                    return max(0, min(100, round(float(match.group(1)) * 100)))
                if "MUTED" in out:
                    return 0
        if self._pactl:
            code, out, _ = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            if code == 0:
                match = re.search(r"(\d+)%", out)
                if match:
                    return int(match.group(1))
        if self._amixer:
            code, out, _ = _run(["amixer", "get", "Master"])
            if code == 0:
                match = re.search(r"\[(\d+)%\]", out)
                if match:
                    return int(match.group(1))
        return -1

    def muted(self) -> bool:
        if self._wpctl:
            code, out, _ = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if code == 0:
                return "MUTED" in out
        if self._pactl:
            code, out, _ = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            if code == 0:
                return "yes" in out.lower()
        return False

    def set_volume(self, percent: int, smooth: bool = False,
                   duration: float = 0.4) -> bool:
        """Установить громкость.

        smooth=True — плавное изменение: резкий скачок слышен как щелчок,
        поэтому идём к цели небольшими шагами.
        """
        target = max(0, min(100, int(percent)))
        if not smooth:
            return self._set_volume_now(target)

        current = self.volume()
        if current < 0:
            return self._set_volume_now(target)

        steps = max(1, int(duration / 0.04))
        step_delay = duration / steps
        for index in range(1, steps + 1):
            value = current + (target - current) * index / steps
            self._set_volume_now(round(value))
            time.sleep(step_delay)
        return True

    def _set_volume_now(self, percent: int) -> bool:
        percent = max(0, min(100, int(percent)))
        if self._wpctl:
            code, _, _ = _run(["wpctl", "set-volume", "-l", "1.0",
                               "@DEFAULT_AUDIO_SINK@", f"{percent}%"])
            if code == 0:
                return True
        if self._pactl:
            code, _, _ = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@",
                               f"{percent}%"])
            if code == 0:
                return True
        if self._amixer:
            code, _, _ = _run(["amixer", "-q", "set", "Master", f"{percent}%"])
            return code == 0
        return False

    def adjust_volume(self, delta: int, smooth: bool = True) -> bool:
        """Изменить громкость на delta процентов."""
        current = self.volume()
        if current < 0:
            current = 50
        return self.set_volume(current + delta, smooth=smooth)

    def mute(self, state: bool | None = None) -> bool:
        """Выключить или включить звук. state=None — переключить."""
        # Снятие с паузы перед изменением громкости: иначе непонятно, работает ли.
        if self._wpctl:
            if state is None:
                code, _, _ = _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
            else:
                code, _, _ = _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@",
                                   "1" if state else "0"])
            if code == 0:
                return True
        if self._pactl:
            if state is None:
                code, _, _ = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
            else:
                code, _, _ = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@",
                                   "1" if state else "0"])
            if code == 0:
                return True
        if self._amixer:
            arg = "toggle" if state is None else ("mute" if state else "unmute")
            code, _, _ = _run(["amixer", "-q", "set", "Master", arg])
            return code == 0
        return False

    def toggle_mute(self) -> bool:
        """Переключить звук. Возвращает новое состояние «выключен»."""
        self.mute()
        return self.muted()

    # --- Микшер приложений -----------------------------------------------

    def application_volumes(self) -> list[dict[str, object]]:
        """Список приложений, играющих звук, с их громкостью."""
        result: list[dict[str, object]] = []
        if not self._pactl:
            return result

        code, out, _ = _run(["pactl", "list", "sink-inputs"])
        if code != 0:
            return result

        current: dict[str, object] = {}
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("Sink Input #"):
                if current:
                    result.append(current)
                current = {"index": int(stripped.split("#")[1]), "app": "неизвестно",
                           "volume": 100, "muted": False}
            elif "application.name" in stripped and current:
                match = re.search(r'=\s*"([^"]+)"', stripped)
                if match:
                    current["app"] = match.group(1)
            elif stripped.startswith("Volume:") and current:
                match = re.search(r"(\d+)%", stripped)
                if match:
                    current["volume"] = int(match.group(1))
            elif stripped.startswith("Mute:") and current:
                current["muted"] = "yes" in stripped.lower()
        if current:
            result.append(current)
        return result

    def set_app_volume(self, pattern: str, percent: int | None = None,
                       delta: int | None = None, mute: bool | None = None) -> bool:
        """Изменить громкость приложения по имени процесса."""
        needle = pattern.strip().lower()
        applied = False
        for stream in self.application_volumes():
            app = str(stream.get("app", "")).lower()
            if needle and needle not in app:
                continue
            index = int(stream.get("index", 0))
            if mute is not None:
                _run(["pactl", "set-sink-input-mute", str(index), "1" if mute else "0"])
                applied = True
                continue
            if delta is not None:
                _run(["pactl", "set-sink-input-volume", str(index),
                      f"{int(delta):+d}%"])
                applied = True
                continue
            if percent is not None:
                _run(["pactl", "set-sink-input-volume", str(index), f"{percent}%"])
                applied = True
        return applied

    # --- Устройства -------------------------------------------------------

    def devices(self, kind: str = "sink") -> list[AudioDevice]:
        """Список устройств ввода или вывода с человеческими названиями."""
        result: list[AudioDevice] = []
        if not self._pactl:
            return result

        # Полный вывод содержит Description — берём его как основное имя.
        descriptions: dict[int, str] = {}
        code, out, _ = _run(["pactl", "list", kind + "s"])
        if code == 0:
            current_index = -1
            for line in out.splitlines():
                stripped = line.strip()
                if stripped.startswith(("Sink #", "Source #")):
                    digits = re.search(r"#(\d+)", stripped)
                    current_index = int(digits.group(1)) if digits else -1
                elif stripped.startswith("Description:") and current_index >= 0:
                    descriptions[current_index] = stripped.split(":", 1)[1].strip()

        code, out, _ = _run(["pactl", "list", "short", kind + "s"])
        if code != 0:
            return result

        default_index = self._default_index(kind)
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            try:
                index = int(parts[0])
            except ValueError:
                continue
            name = parts[1]
            description = descriptions.get(index) or self._pretty_name(name)
            result.append(AudioDevice(
                index=index, name=name, description=description,
                is_default=index == default_index, kind=kind,
            ))
        return result

    @staticmethod
    def _pretty_name(technical: str) -> str:
        """Превратить техническое имя в читаемое, если описания нет."""
        text = technical
        # Отбрасываем служебные префиксы ALSA.
        for prefix in ("alsa_output.", "alsa_input.", "alsa_card."):
            if text.startswith(prefix):
                text = text[len(prefix):]
        # Убираем идентификаторы шины и модели.
        text = re.sub(r"pci-[0-9a-f]+_[0-9a-f]+_[0-9a-f]+\.[0-9a-f]+\.?", "", text)
        text = re.sub(r"platform-[a-z0-9_]+\.?", "", text)
        text = text.replace("__", " ").replace(".", " ").replace("_", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text or technical

    def _default_index(self, kind: str) -> int:
        if not self._pactl:
            return -1
        target = "@DEFAULT_SINK@" if kind == "sink" else "@DEFAULT_SOURCE@"
        code, out, _ = _run(["pactl", "get-default-" + ("sink" if kind == "sink" else "source")])
        if code != 0:
            return -1
        # Вывод вида: 	Speaker (index 53)
        match = re.search(r"index\s+(\d+)", out)
        return int(match.group(1)) if match else -1

    def set_default_device(self, name: str, kind: str = "sink") -> bool:
        if not self._pactl:
            return False
        command = "set-default-sink" if kind == "sink" else "set-default-source"
        code, _, _ = _run(["pactl", command, name])
        return code == 0

    def microphones(self) -> list[AudioDevice]:
        return self.devices("source")

    def outputs(self) -> list[AudioDevice]:
        return self.devices("sink")

    # --- Воспроизведение файлов ------------------------------------------

    def play_file(self, path: str, volume: float = 1.0, wait: bool = False) -> bool:
        """Проиграть звуковой файл (WAV, MP3, OGG и другие)."""
        from pathlib import Path
        target = Path(path).expanduser()
        if not target.exists():
            log.error("Звуковой файл не найден: %s", target)
            return False

        # Выбор проигрывателя по формату.
        command: list[str] | None = None
        if target.suffix.lower() == ".wav" and self._pw_play:
            command = ["pw-play", str(target)]
        elif self._paplay:
            command = ["paplay", str(target)]
        elif self._pw_play:
            command = ["pw-play", str(target)]
        elif self._ffplay:
            command = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(target)]

        if command is None:
            log.error("Нет доступного проигрывателя звука")
            return False

        # Громкость задаём через системный уровень перед проигрыванием
        # не нужно: pw-play/paplay используют текущий уровень.

        def launch() -> None:
            try:
                subprocess.run(command, capture_output=True, timeout=300)
            except Exception as exc:  # noqa: BLE001
                log.debug("Сбой проигрывания: %s", exc)

        if wait:
            launch()
            return True

        import threading
        thread = threading.Thread(target=launch, daemon=True)
        thread.start()
        return True

    def play_bytes(self, data: bytes, suffix: str = ".wav") -> bool:
        """Проиграть звук из памяти — для озвучки и сигналов."""
        import tempfile
        import threading
        from pathlib import Path

        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(data)
                temp_path = handle.name
        except OSError as exc:
            log.error("Не удалось сохранить звук во временный файл: %s", exc)
            return False

        command: list[str] | None = None
        if self._pw_play:
            command = ["pw-play", temp_path]
        elif self._paplay:
            command = ["paplay", temp_path]
        elif self._ffplay:
            command = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", temp_path]

        if command is None:
            return False

        def run_and_clean() -> None:
            try:
                subprocess.run(command, capture_output=True, timeout=300)
            except Exception as exc:  # noqa: BLE001
                log.debug("Сбой воспроизведения: %s", exc)
            finally:
                try:
                    Path(temp_path).unlink()
                except OSError:
                    pass

        thread = threading.Thread(target=run_and_clean, daemon=True)
        thread.start()
        return True

    def stop_playback(self) -> None:
        """Прекратить воспроизведение, начатое ассистентом."""
        for process in ("pw-play", "paplay", "ffplay"):
            _run(["pkill", "-f", process], timeout=3)


# --- Единственный экземпляр -------------------------------------------------

_audio: AudioManager | None = None


def get_audio() -> AudioManager:
    global _audio
    if _audio is None:
        _audio = AudioManager()
    return _audio