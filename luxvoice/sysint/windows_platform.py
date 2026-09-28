"""Реализация PlatformInterface для Windows."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from luxvoice.sysint.base import PlatformInterface, WindowInfo

log = logging.getLogger(__name__)


class WindowsPlatform(PlatformInterface):
    """Windows-специфичные операции через pyautogui, pygetwindow, pycaw."""
    
    def __init__(self):
        self._check_dependencies()
    
    def _check_dependencies(self):
        """Проверить наличие Windows-библиотек."""
        missing = []
        try:
            import pygetwindow  # noqa: F401
        except ImportError:
            missing.append("pygetwindow")
        
        try:
            import pyautogui  # noqa: F401
        except ImportError:
            missing.append("pyautogui")
        
        try:
            from pycaw.pycaw import AudioUtilities  # noqa: F401
        except ImportError:
            missing.append("pycaw")
        
        try:
            import pyperclip  # noqa: F401
        except ImportError:
            missing.append("pyperclip")
        
        if missing:
            log.warning(
                "Не установлены Windows-библиотеки: %s. "
                "Установите: pip install %s",
                ", ".join(missing), " ".join(missing)
            )
    
    # --- Окна ---
    
    def get_windows(self) -> list[WindowInfo]:
        try:
            import pygetwindow as gw
            windows = []
            for win in gw.getAllWindows():
                if win.title and not win.isMinimized:
                    windows.append(WindowInfo(
                        id=str(win._hWnd),
                        title=win.title,
                        geometry=(win.left, win.top, win.width, win.height)
                    ))
            return windows
        except Exception as exc:
            log.debug("Не удалось получить окна: %s", exc)
            return []
    
    def get_active_window(self) -> Optional[WindowInfo]:
        try:
            import pygetwindow as gw
            win = gw.getActiveWindow()
            if win and win.title:
                return WindowInfo(
                    id=str(win._hWnd),
                    title=win.title,
                    geometry=(win.left, win.top, win.width, win.height)
                )
        except Exception as exc:
            log.debug("Не удалось получить активное окно: %s", exc)
        return None
    
    def close_window(self, window_id: str = "") -> bool:
        try:
            import pygetwindow as gw
            if window_id:
                # Найти окно по ID
                for win in gw.getAllWindows():
                    if str(win._hWnd) == window_id:
                        win.close()
                        return True
            else:
                win = gw.getActiveWindow()
                if win:
                    win.close()
                    return True
        except Exception as exc:
            log.debug("Не удалось закрыть окно: %s", exc)
        return False
    
    def minimize_window(self, window_id: str = "") -> bool:
        try:
            import pygetwindow as gw
            if window_id:
                for win in gw.getAllWindows():
                    if str(win._hWnd) == window_id:
                        win.minimize()
                        return True
            else:
                win = gw.getActiveWindow()
                if win:
                    win.minimize()
                    return True
        except Exception as exc:
            log.debug("Не удалось свернуть окно: %s", exc)
        return False
    
    def maximize_window(self, window_id: str = "") -> bool:
        try:
            import pygetwindow as gw
            if window_id:
                for win in gw.getAllWindows():
                    if str(win._hWnd) == window_id:
                        win.maximize()
                        return True
            else:
                win = gw.getActiveWindow()
                if win:
                    win.maximize()
                    return True
        except Exception as exc:
            log.debug("Не удалось развернуть окно: %s", exc)
        return False
    
    def minimize_all(self) -> bool:
        try:
            import pyautogui
            pyautogui.hotkey("win", "d")
            return True
        except Exception as exc:
            log.debug("Не удалось свернуть все: %s", exc)
            return False
    
    def show_desktop(self) -> bool:
        return self.minimize_all()
    
    # --- Громкость ---
    
    def get_volume(self) -> int:
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            current = volume.GetMasterVolumeLevelScalar()
            return int(current * 100)
        except Exception as exc:
            log.debug("Не удалось получить громкость: %s", exc)
            return 50
    
    def set_volume(self, level: int) -> bool:
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            
            level = max(0, min(100, level))
            scalar = level / 100.0
            
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMasterVolumeLevelScalar(scalar, None)
            return True
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
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            return bool(volume.GetMute())
        except Exception as exc:
            log.debug("Не удалось проверить mute: %s", exc)
            return False
    
    def toggle_mute(self) -> bool:
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            current_mute = volume.GetMute()
            volume.SetMute(not current_mute, None)
            return True
        except Exception as exc:
            log.debug("Не удалось переключить mute: %s", exc)
            return False
    
    # --- Клавиатура ---
    
    def press_key(self, key: str) -> bool:
        try:
            import pyautogui
            pyautogui.press(key)
            return True
        except Exception as exc:
            log.debug("Не удалось нажать клавишу: %s", exc)
            return False
    
    def hotkey(self, *keys: str) -> bool:
        try:
            import pyautogui
            pyautogui.hotkey(*keys)
            return True
        except Exception as exc:
            log.debug("Не удалось нажать комбинацию: %s", exc)
            return False
    
    def type_text(self, text: str) -> bool:
        try:
            import pyautogui
            pyautogui.write(text)
            return True
        except Exception as exc:
            log.debug("Не удалось напечатать текст: %s", exc)
            return False
    
    # --- Буфер обмена ---
    
    def get_clipboard(self) -> str:
        try:
            import pyperclip
            return pyperclip.paste()
        except Exception as exc:
            log.debug("Не удалось получить буфер: %s", exc)
            return ""
    
    def set_clipboard(self, text: str) -> bool:
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception as exc:
            log.debug("Не удалось установить буфер: %s", exc)
            return False
    
    # --- Скриншоты ---
    
    def screenshot(self, save_path: str = "") -> Optional[str]:
        if not save_path:
            save_path = str(Path(tempfile.gettempdir()) / "luxvoice_screenshot.png")
        
        try:
            import pyautogui
            screenshot = pyautogui.screenshot()
            screenshot.save(save_path)
            return save_path
        except Exception as exc:
            log.debug("Не удалось сделать скриншот: %s", exc)
            return None
    
    def lock_screen(self) -> bool:
        try:
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=True)
            return True
        except Exception as exc:
            log.debug("Не удалось заблокировать экран: %s", exc)
            return False
    
    def sleep_display(self) -> bool:
        try:
            # Выключить монитор через PowerShell
            subprocess.run([
                "powershell", "-Command",
                "(Add-Type '[DllImport(\"user32.dll\")]^public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);' -Name a -Passthru)::SendMessage(-1,0x0112,0xF170,2)"
            ], check=True)
            return True
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
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
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
        
        # Поиск в Program Files
        program_files = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", r"C:\Users\%USERNAME%\AppData\Local"),
        ]
        
        for base_dir in program_files:
            if not os.path.exists(base_dir):
                continue
            
            # Ищем папки с похожим именем
            try:
                for item in os.listdir(base_dir):
                    if name.lower() in item.lower():
                        item_path = os.path.join(base_dir, item)
                        if os.path.isdir(item_path):
                            # Ищем .exe внутри
                            for root, dirs, files in os.walk(item_path):
                                for file in files:
                                    if file.lower().endswith(".exe"):
                                        if name.lower() in file.lower():
                                            return os.path.join(root, file)
            except Exception:
                continue
        
        return None
    
    # --- Информация ---
    
    def get_platform_name(self) -> str:
        return "windows"
    
    def is_wayland(self) -> bool:
        return False  # Wayland только на Linux
