"""Реализация PlatformInterface для Linux (X11/Wayland)."""

import logging
import os
import subprocess
from typing import Optional

from luxvoice.sysint.base import PlatformInterface, WindowInfo

log = logging.getLogger(__name__)


class LinuxPlatform(PlatformInterface):
    """Linux-специфичные операции через wmctrl, xdotool, pactl."""
    
    def __init__(self):
        self._is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"
    
    # --- Окна ---
    
    def get_windows(self) -> list[WindowInfo]:
        try:
            result = subprocess.run(
                ["wmctrl", "-l", "-p"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode != 0:
                return []
            
            windows = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(None, 4)
                if len(parts) >= 5:
                    wid, _, pid, _, title = parts[0], parts[1], parts[2], parts[3], parts[4]
                    windows.append(WindowInfo(
                        id=wid,
                        title=title,
                        process=pid
                    ))
            return windows
        except Exception as exc:
            log.debug("Не удалось получить окна: %s", exc)
            return []
    
    def get_active_window(self) -> Optional[WindowInfo]:
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                title = result.stdout.strip()
                wid_result = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True, text=True, timeout=2
                )
                wid = wid_result.stdout.strip() if wid_result.returncode == 0 else ""
                return WindowInfo(id=wid, title=title)
        except Exception as exc:
            log.debug("Не удалось получить активное окно: %s", exc)
        return None
    
    def close_window(self, window_id: str = "") -> bool:
        try:
            cmd = ["xdotool", "windowclose"]
            if window_id:
                cmd.append(window_id)
            else:
                cmd.append("getactivewindow")
            result = subprocess.run(cmd, capture_output=True, timeout=2)
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось закрыть окно: %s", exc)
            return False
    
    def minimize_window(self, window_id: str = "") -> bool:
        try:
            cmd = ["xdotool", "windowminimize"]
            if window_id:
                cmd.append(window_id)
            else:
                cmd.append("getactivewindow")
            result = subprocess.run(cmd, capture_output=True, timeout=2)
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось свернуть окно: %s", exc)
            return False
    
    def maximize_window(self, window_id: str = "") -> bool:
        try:
            cmd = ["xdotool", "windowsize"]
            if window_id:
                cmd.append(window_id)
            else:
                cmd.append("getactivewindow")
            cmd.extend(["100%", "100%"])
            result = subprocess.run(cmd, capture_output=True, timeout=2)
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось развернуть окно: %s", exc)
            return False
    
    def minimize_all(self) -> bool:
        try:
            result = subprocess.run(
                ["wmctrl", "-k", "on"],
                capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось свернуть все: %s", exc)
            return False
    
    def show_desktop(self) -> bool:
        return self.minimize_all()
    
    # --- Громкость ---
    
    def get_volume(self) -> int:
        try:
            result = subprocess.run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                import re
                match = re.search(r"(\d+)%", result.stdout)
                if match:
                    return min(100, int(match.group(1)))
        except Exception as exc:
            log.debug("Не удалось получить громкость: %s", exc)
        return 50
    
    def set_volume(self, level: int) -> bool:
        try:
            level = max(0, min(100, level))
            result = subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось установить громкость: %s", exc)
            return False
    
    def volume_up(self, amount: int = 10) -> int:
        current = self.get_volume()
        new_level = min(100, current + amount)
        self.set_volume(new_level)
        return new_level
    
    def volume_down(self, amount: int = 10) -> int:
        current = self.get_volume()
        new_level = max(0, current - amount)
        self.set_volume(new_level)
        return new_level
    
    def is_muted(self) -> bool:
        try:
            result = subprocess.run(
                ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=2
            )
            return "yes" in result.stdout.lower()
        except Exception as exc:
            log.debug("Не удалось проверить mute: %s", exc)
            return False
    
    def toggle_mute(self) -> bool:
        try:
            result = subprocess.run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось переключить mute: %s", exc)
            return False
    
    # --- Клавиатура ---
    
    def press_key(self, key: str) -> bool:
        try:
            result = subprocess.run(
                ["xdotool", "key", key],
                capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось нажать клавишу: %s", exc)
            return False
    
    def hotkey(self, *keys: str) -> bool:
        combo = "+".join(keys)
        return self.press_key(combo)
    
    def type_text(self, text: str) -> bool:
        try:
            result = subprocess.run(
                ["xdotool", "type", "--clearmodifiers", text],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось напечатать текст: %s", exc)
            return False
    
    # --- Буфер обмена ---
    
    def get_clipboard(self) -> str:
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as exc:
            log.debug("Не удалось получить буфер: %s", exc)
        return ""
    
    def set_clipboard(self, text: str) -> bool:
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text, text=True, capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось установить буфер: %s", exc)
            return False
    
    # --- Скриншоты ---
    
    def screenshot(self, save_path: str = "") -> Optional[str]:
        if not save_path:
            import tempfile
            from pathlib import Path
            save_path = str(Path(tempfile.gettempdir()) / "luxvoice_screenshot.png")
        
        try:
            result = subprocess.run(
                ["scrot", save_path],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return save_path
        except Exception as exc:
            log.debug("Не удалось сделать скриншот: %s", exc)
        return None
    
    def lock_screen(self) -> bool:
        try:
            # Попробуем разные команды
            for cmd in [["loginctl", "lock-session"], ["xdg-screensaver", "lock"]]:
                result = subprocess.run(cmd, capture_output=True, timeout=3)
                if result.returncode == 0:
                    return True
        except Exception as exc:
            log.debug("Не удалось заблокировать экран: %s", exc)
        return False
    
    def sleep_display(self) -> bool:
        try:
            result = subprocess.run(
                ["xdg-screensaver", "reset"],
                capture_output=True, timeout=2
            )
            return result.returncode == 0
        except Exception as exc:
            log.debug("Не удалось выключить монитор: %s", exc)
            return False
    
    # --- Запуск программ ---
    
    def launch_app(self, name: str) -> bool:
        exe = self.find_executable(name)
        if not exe:
            return False
        try:
            subprocess.Popen(
                [exe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            return True
        except Exception as exc:
            log.debug("Не удалось запустить %s: %s", name, exc)
            return False
    
    def find_executable(self, name: str) -> Optional[str]:
        import shutil
        # Прямое совпадение
        path = shutil.which(name)
        if path:
            return path
        
        # Поиск в /usr/share/applications
        try:
            result = subprocess.run(
                ["grep", "-l", f"Name=.*{name}", "/usr/share/applications/*.desktop"],
                capture_output=True, text=True, timeout=2, shell=True
            )
            if result.returncode == 0:
                desktop_file = result.stdout.strip().split("\n")[0]
                if desktop_file:
                    exec_result = subprocess.run(
                        ["grep", "^Exec=", desktop_file],
                        capture_output=True, text=True, timeout=2
                    )
                    if exec_result.returncode == 0:
                        exec_line = exec_result.stdout.strip().split("=", 1)[1]
                        return exec_line.split()[0]
        except Exception as exc:
            log.debug("Не удалось найти %s: %s", name, exc)
        return None
    
    # --- Информация ---
    
    def get_platform_name(self) -> str:
        return "linux"
    
    def is_wayland(self) -> bool:
        return self._is_wayland
