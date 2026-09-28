"""Автозапуск и системная интеграция: ярлык, значок, правило udev.

Отдельно решается задача доступа к виртуальному вводу: без неё команды
с клавишами и мышью не работают. Правило udev выдаёт права на /dev/uinput
только членам группы input — это безопаснее, чем запуск от root.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

from luxvoice import __app_name__, __version__
from luxvoice.core import paths

log = logging.getLogger(__name__)


def project_root() -> Path:
    """Каталог проекта."""
    return Path(__file__).resolve().parent.parent.parent


def python_executable() -> Path:
    """Python внутри виртуального окружения проекта."""
    root = project_root()
    candidate = root / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(shutil.which("python3") or "python3")


def _run(cmd: list[str], timeout: float = 20) -> tuple[int, str, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout)
        return result.returncode, result.stdout or "", result.stderr or ""
    except FileNotFoundError:
        return 127, "", f"не найдена программа: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "превышено время ожидания"
    except OSError as exc:
        return 1, "", str(exc)


# --- Ярлык рабочего стола ---------------------------------------------------

def desktop_entry_text(portable: bool = False) -> str:
    """Содержимое .desktop-файла."""
    root = project_root()
    python = python_executable()
    icon = root / "luxvoice" / "resources" / "luxvoice.svg"

    if portable:
        exec_line = (f'env LUXVOICE_HOME="{root / "portable-data"}" '
                     f'"{python}" -m luxvoice.main')
    else:
        exec_line = f'"{python}" -m luxvoice.main'

    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name={__app_name__}
GenericName=Голосовой ассистент
Comment=Голосовое управление компьютером: команды, сценарии, программы
Exec={exec_line}
Path={root}
Icon={icon if icon.exists() else "audio-input-microphone"}
Terminal=false
Categories=Utility;Accessibility;AudioVideo;
Keywords=ассистент;голос;команды;управление;voice;assistant;
StartupNotify=true
StartupWMClass=luxvoice
Actions=Headless;Diagnose;

[Desktop Action Headless]
Name=Запустить без интерфейса
Exec={python} -m luxvoice.main --headless

[Desktop Action Diagnose]
Name=Проверить компоненты
Exec={python} -m luxvoice.main --diagnose
"""


def install_desktop_entry(portable: bool = False) -> Path:
    """Установить ярлык в меню приложений."""
    target = Path.home() / ".local" / "share" / "applications" / "luxvoice.desktop"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry_text(portable), encoding="utf-8")
    target.chmod(0o755)

    # Обновляем базу ярлыков, если инструмент есть.
    if shutil.which("update-desktop-database"):
        _run(["update-desktop-database",
              str(target.parent)])

    log.info("Ярлык установлен: %s", target)
    return target


def remove_desktop_entry() -> bool:
    target = Path.home() / ".local" / "share" / "applications" / "luxvoice.desktop"
    try:
        if target.exists():
            target.unlink()
            return True
    except OSError as exc:
        log.warning("Не удалось удалить ярлык: %s", exc)
    return False


# --- Автозапуск -------------------------------------------------------------

def autostart_text() -> str:
    """Содержимое файла автозапуска."""
    root = project_root()
    python = python_executable()
    return f"""[Desktop Entry]
Type=Application
Name={__app_name__}
Comment=Голосовой ассистент
Exec="{python}" -m luxvoice.main --start-hidden
Path={root}
Icon=audio-input-microphone
Terminal=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
StartupNotify=false
"""


def enable_autostart() -> tuple[bool, str]:
    """Включить запуск при входе в систему."""
    target = paths.autostart_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(autostart_text(), encoding="utf-8")
        target.chmod(0o755)
        log.info("Автозапуск включён: %s", target)
        return True, str(target)
    except OSError as exc:
        return False, f"не удалось создать файл автозапуска: {exc}"


def disable_autostart() -> bool:
    """Выключить автозапуск."""
    target = paths.autostart_file()
    try:
        if target.exists():
            target.unlink()
            log.info("Автозапуск выключен")
            return True
    except OSError as exc:
        log.warning("Не удалось выключить автозапуск: %s", exc)
    return False


def autostart_enabled() -> bool:
    return paths.autostart_file().exists()


def sync_autostart(enabled: bool) -> None:
    """Привести автозапуск в соответствие с настройкой."""
    currently = autostart_enabled()
    if enabled and not currently:
        enable_autostart()
    elif not enabled and currently:
        disable_autostart()


# --- Доступ к виртуальному вводу -------------------------------------------

UDEV_RULE_NAME = "60-luxvoice-uinput.rules"

UDEV_RULE = """# Доступ к виртуальному устройству ввода для голосового ассистента.
# Позволяет членам группы input создавать виртуальную клавиатуру и мышь,
# что необходимо для команд с нажатиями клавиш в Wayland.
KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
"""


def udev_rule_path() -> Path:
    return Path("/etc/udev/rules.d") / UDEV_RULE_NAME


def input_access_available() -> bool:
    """Есть ли доступ к /dev/uinput прямо сейчас."""
    from luxvoice.sysint.uinput import UInputDevice
    return UInputDevice.available().uinput


def in_input_group() -> bool:
    """Состоит ли пользователь в группе input."""
    try:
        import grp
        group = grp.getgrnam("input")
        return os.getuid() in group.gr_mem or False or (
            os.getgid() == group.gr_gid)
    except (KeyError, OSError):
        return False


def install_udev_rule(sudo_password: str = "") -> tuple[bool, str]:
    """Установить правило udev. Требует прав администратора."""
    rule_path = udev_rule_path()
    import tempfile

    try:
        handle, temp_path = tempfile.mkstemp(suffix=".rules")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(UDEV_RULE)
    except OSError as exc:
        return False, f"не удалось создать временный файл: {exc}"

    # Копируем правило и перечитываем настройки.
    commands = [
        ["sudo", "-n", "cp", temp_path, str(rule_path)],
        ["sudo", "-n", "usermod", "-aG", "input", os.environ.get("USER", "")],
    ]

    # Если есть пароль — используем его через stdin.
    if sudo_password:
        commands = [
            ["sudo", "-S", "-p", "", "cp", temp_path, str(rule_path)],
            ["sudo", "-S", "-p", "", "usermod", "-aG", "input",
             os.environ.get("USER", "")],
        ]

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                input=(sudo_password + "\n") if sudo_password else None,
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                error = (result.stderr or "").strip()
                if "password" in error.lower() or "пароль" in error.lower():
                    return False, ("требуется пароль администратора. "
                                   "Выполните вручную команды ниже")
                return False, f"команда не выполнена: {error[:200]}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)

    # Перечитываем правила и настройки.
    for cmd in (["sudo", "-n", "udevadm", "control", "--reload-rules"],
                ["sudo", "-n", "udevadm", "trigger"]):
        _run(cmd, timeout=20)

    try:
        Path(temp_path).unlink()
    except OSError:
        pass

    return True, "правило установлено. Требуется выйти из системы и войти заново"


def check_input_setup() -> dict[str, object]:
    """Проверить состояние доступа к вводу и что нужно сделать."""
    from luxvoice.sysint.uinput import UInputDevice

    caps = UInputDevice.available()
    visible = Path("/dev/uinput").exists()
    readable = os.access("/dev/uinput", os.W_OK) if visible else False
    group = in_input_group()

    if caps.uinput:
        return {
            "ok": True,
            "message": "Доступ к виртуальному вводу есть — "
                       "команды с клавишами и мышью работают",
            "action": "",
        }

    return {
        "ok": False,
        "message": caps.reason,
        "action": "sudo usermod -aG input $USER",
        "rule_exists": udev_rule_path().exists(),
        "in_group": group,
        "device_exists": visible,
        "writable": readable,
    }


# --- Проверка компонентов системы -------------------------------------------

def system_packages() -> dict[str, str]:
    """Проверить наличие нужных системных программ."""
    required = {
        "pipewire": "звуковая подсистема",
        "wpctl": "управление громкостью",
        "pactl": "микшер приложений",
        "ffmpeg": "чтение звуковых файлов",
        "notify-send": "уведомления",
        "xdg-open": "открытие файлов и ссылок",
    }
    optional = {
        "spectacle": "снимки экрана (KDE)",
        "grim": "снимки экрана (Wayland)",
        "wl-copy": "буфер обмена (Wayland)",
        "xclip": "буфер обмена (X11)",
        "brightnessctl": "яркость экрана",
        "konsole": "терминал",
        "qt6ct": "настройка вида Qt-приложений",
    }

    result: dict[str, str] = {}
    for name, description in required.items():
        result[name] = f"{description} — " + (
            "есть" if shutil.which(name) else "НЕ НАЙДЕНО")
    for name, description in optional.items():
        if shutil.which(name):
            result[name] = f"{description} — есть"
    return result


def create_icon() -> Path:
    """Создать значок программы в каталоге ресурсов."""
    target = project_root() / "luxvoice" / "resources" / "luxvoice.svg"
    target.parent.mkdir(parents=True, exist_ok=True)

    svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#4f8cff"/>
      <stop offset="100%" stop-color="#2d5fd0"/>
    </linearGradient>
  </defs>
  <circle cx="64" cy="64" r="58" fill="url(#bg)"/>
  <circle cx="64" cy="64" r="46" fill="none" stroke="#ffffff" stroke-opacity="0.25" stroke-width="2"/>
  <circle cx="64" cy="64" r="34" fill="none" stroke="#ffffff" stroke-opacity="0.45" stroke-width="3"/>
  <rect x="55" y="30" width="18" height="38" rx="9" fill="#ffffff"/>
  <path d="M42 62 a22 22 0 0 0 44 0" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <line x1="64" y1="84" x2="64" y2="96" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <line x1="50" y1="98" x2="78" y2="98" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
</svg>
"""
    target.write_text(svg, encoding="utf-8")
    return target


# --- Установка целиком ------------------------------------------------------

def install_shortcuts() -> dict[str, object]:
    """Установить ярлык и создать значок."""
    result: dict[str, object] = {}

    try:
        result["desktop"] = str(install_desktop_entry())
    except OSError as exc:
        result["desktop_error"] = str(exc)

    try:
        result["icon"] = str(create_icon())
    except OSError as exc:
        result["icon_error"] = str(exc)

    # Копируем значок в общий каталог, чтобы он был виден системе.
    try:
        icon_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
        icon_dir.mkdir(parents=True, exist_ok=True)
        target = icon_dir / "luxvoice.svg"
        shutil.copy2(project_root() / "luxvoice" / "resources" / "luxvoice.svg",
                     target)
        result["system_icon"] = str(target)
    except OSError as exc:
        result["system_icon_error"] = str(exc)

    return result


def setup_report() -> str:
    """Текстовый отчёт о состоянии интеграции."""
    lines = ["Интеграция с системой", "=" * 44, ""]

    lines.append(f"Ярлык в меню: "
                 + ("установлен" if (Path.home() / ".local" / "share"
                                     / "applications" / "luxvoice.desktop").exists()
                    else "не установлен"))
    lines.append(f"Автозапуск: "
                 + ("включён" if autostart_enabled() else "выключен"))

    check = check_input_setup()
    lines.append("")
    lines.append("Доступ к управлению компьютером:")
    lines.append(f"  {check['message']}")
    if not check.get("ok"):
        lines.append(f"  Что сделать: {check.get('action')}")
        lines.append("  Затем выйдите из системы и войдите заново.")

    lines.append("")
    lines.append("Системные программы:")
    for name, status in system_packages().items():
        lines.append(f"  {name}: {status}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Установка интеграции из командной строки."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="luxvoice-setup",
        description="Установка интеграции LuxVoice с системой")
    parser.add_argument("--install", action="store_true",
                        help="установить ярлык и значок")
    parser.add_argument("--autostart", choices=("on", "off"),
                        help="включить или выключить автозапуск")
    parser.add_argument("--input-access", action="store_true",
                        help="настроить доступ к виртуальному вводу")
    parser.add_argument("--check", action="store_true",
                        help="показать отчёт")
    parser.add_argument("--remove", action="store_true",
                        help="удалить ярлык и автозапуск")

    args = parser.parse_args(argv)

    if args.install:
        result = install_shortcuts()
        print("Установка завершена:")
        for key, value in result.items():
            print(f"  {key}: {value}")

    if args.autostart:
        if args.autostart == "on":
            ok, message = enable_autostart()
            print(("Автозапуск включён: " if ok else "Ошибка: ") + message)
        else:
            print("Автозапуск выключен" if disable_autostart()
                  else "Автозапуск и так был выключен")

    if args.input_access:
        ok, message = install_udev_rule()
        print(("Готово: " if ok else "Не удалось: ") + message)
        if not ok:
            print("\nВыполните вручную:")
            print(f"  echo '{UDEV_RULE.strip()}' | sudo tee {udev_rule_path()}")
            print("  sudo usermod -aG input $USER")
            print("  sudo udevadm control --reload-rules && sudo udevadm trigger")
            print("  Затем выйдите из системы и войдите заново.")

    if args.remove:
        print("Ярлык удалён" if remove_desktop_entry() else "Ярлыка не было")
        disable_autostart()

    if args.check or not any((args.install, args.autostart, args.input_access,
                              args.remove)):
        print(setup_report())

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())