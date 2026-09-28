"""Абстрактный интерфейс для платформо-зависимых операций.

Все системные функции (управление окнами, громкостью, клавиатурой)
реализуются через этот интерфейс. Для каждой платформы (Linux, Windows, macOS)
создаётся своя реализация.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class WindowInfo:
    """Информация об окне."""
    id: str
    title: str
    process: str = ""
    geometry: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h
    
    def context(self) -> str:
        """Контекст для matching (title + process)."""
        return f"{self.title} {self.process}".strip()


class PlatformInterface(ABC):
    """Абстрактный интерфейс для платформо-зависимых операций."""
    
    # --- Управление окнами ---
    
    @abstractmethod
    def get_windows(self) -> list[WindowInfo]:
        """Получить список всех окон."""
        pass
    
    @abstractmethod
    def get_active_window(self) -> Optional[WindowInfo]:
        """Получить активное окно."""
        pass
    
    @abstractmethod
    def close_window(self, window_id: str = "") -> bool:
        """Закрыть окно (текущее, если window_id пустой)."""
        pass
    
    @abstractmethod
    def minimize_window(self, window_id: str = "") -> bool:
        """Свернуть окно."""
        pass
    
    @abstractmethod
    def maximize_window(self, window_id: str = "") -> bool:
        """Развернуть окно."""
        pass
    
    @abstractmethod
    def minimize_all(self) -> bool:
        """Свернуть все окна."""
        pass
    
    @abstractmethod
    def show_desktop(self) -> bool:
        """Показать рабочий стол (свернуть все)."""
        pass
    
    # --- Управление громкостью ---
    
    @abstractmethod
    def get_volume(self) -> int:
        """Получить текущую громкость (0-100)."""
        pass
    
    @abstractmethod
    def set_volume(self, level: int) -> bool:
        """Установить громкость (0-100)."""
        pass
    
    @abstractmethod
    def volume_up(self, amount: int = 10) -> int:
        """Увеличить громкость. Возвращает новый уровень."""
        pass
    
    @abstractmethod
    def volume_down(self, amount: int = 10) -> int:
        """Уменьшить громкость. Возвращает новый уровень."""
        pass
    
    @abstractmethod
    def is_muted(self) -> bool:
        """Проверить, выключен ли звук."""
        pass
    
    @abstractmethod
    def toggle_mute(self) -> bool:
        """Переключить mute. Возвращает новое состояние."""
        pass
    
    # --- Управление клавиатурой/мышью ---
    
    @abstractmethod
    def press_key(self, key: str) -> bool:
        """Нажать клавишу."""
        pass
    
    @abstractmethod
    def hotkey(self, *keys: str) -> bool:
        """Нажать комбинацию клавиш."""
        pass
    
    @abstractmethod
    def type_text(self, text: str) -> bool:
        """Напечатать текст."""
        pass
    
    # --- Буфер обмена ---
    
    @abstractmethod
    def get_clipboard(self) -> str:
        """Получить текст из буфера обмена."""
        pass
    
    @abstractmethod
    def set_clipboard(self, text: str) -> bool:
        """Установить текст в буфер обмена."""
        pass
    
    # --- Системные операции ---
    
    @abstractmethod
    def screenshot(self, save_path: str = "") -> Optional[str]:
        """Сделать скриншот. Возвращает путь к файлу."""
        pass
    
    @abstractmethod
    def lock_screen(self) -> bool:
        """Заблокировать экран."""
        pass
    
    @abstractmethod
    def sleep_display(self) -> bool:
        """Выключить монитор."""
        pass
    
    # --- Запуск программ ---
    
    @abstractmethod
    def launch_app(self, name: str) -> bool:
        """Запустить приложение по имени."""
        pass
    
    @abstractmethod
    def find_executable(self, name: str) -> Optional[str]:
        """Найти исполняемый файл по имени."""
        pass
    
    # --- Информация о системе ---
    
    @abstractmethod
    def get_platform_name(self) -> str:
        """Название платформы (linux/windows/macos)."""
        pass
    
    @abstractmethod
    def is_wayland(self) -> bool:
        """Проверить, используется ли Wayland (только Linux)."""
        pass


def get_platform_interface() -> PlatformInterface:
    """Получить интерфейс для текущей платформы."""
    import sys
    
    if sys.platform == "win32":
        from luxvoice.sysint.windows_platform import WindowsPlatform
        return WindowsPlatform()
    elif sys.platform == "linux":
        from luxvoice.sysint.linux_platform import LinuxPlatform
        return LinuxPlatform()
    elif sys.platform == "darwin":
        from luxvoice.sysint.macos_platform import MacOSPlatform
        return MacOSPlatform()
    else:
        raise RuntimeError(f"Неподдерживаемая платформа: {sys.platform}")


# Глобальный экземпляр
_platform: Optional[PlatformInterface] = None


def platform() -> PlatformInterface:
    """Получить глобальный интерфейс платформы."""
    global _platform
    if _platform is None:
        _platform = get_platform_interface()
    return _platform
