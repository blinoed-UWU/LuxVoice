"""Буфер обмена и получение выделенного текста.

Кроссплатформенная реализация через платформенный интерфейс.
"""

from __future__ import annotations

import logging

from luxvoice.sysint.base import platform

log = logging.getLogger(__name__)


def get_text() -> str:
    """Прочитать текст из буфера обмена."""
    return platform().get_clipboard()


def set_text(text: str) -> bool:
    """Записать текст в буфер обмена."""
    return platform().set_clipboard(text)
    if _has("xclip"):
        candidates.append(["xclip", "-selection", "clipboard", "-o"])
    if _has("xsel"):
        candidates.append(["xsel", "--clipboard", "--output"])
    if _has("wl-paste"):
        candidates.append(["wl-paste", "--no-newline"])

    for cmd in candidates:
        code, out = _run(cmd)
        if code == 0:
            return out
    return ""


def set_text(text: str) -> bool:
    """Положить текст в буфер обмена."""
    payload = text.encode("utf-8")

    candidates: list[list[str]] = []
    if _is_wayland() and _has("wl-copy"):
        candidates.append(["wl-copy"])
    if _has("xclip"):
        candidates.append(["xclip", "-selection", "clipboard"])
    if _has("xsel"):
        candidates.append(["xsel", "--clipboard", "--input"])
    if _has("wl-copy"):
        candidates.append(["wl-copy"])

    for cmd in candidates:
        code, _ = _run(cmd, data=payload)
        if code == 0:
            return True

    # Последний путь: Klipper через D-Bus.
    if _has("qdbus6") or _has("qdbus"):
        binary = "qdbus6" if _has("qdbus6") else "qdbus"
        code, _ = _run([
            binary, "org.kde.klipper", "/klipper",
            "org.kde.klipper.klipper.setClipboardContents", text,
        ])
        if code == 0:
            return True

    log.warning("Не удалось записать текст в буфер обмена — "
                "установите wl-clipboard (Wayland) или xclip (X11)")
    return False


def clear() -> bool:
    """Очистить буфер обмена."""
    return set_text("")


def get_selected_text(timeout: float = 0.6) -> str:
    """Получить выделенный текст из активного окна.

    Работает так: сохраняем буфер, отправляем Ctrl+C, читаем буфер,
    возвращаем прежнее содержимое. Это единственный способ получить
    выделение без интеграции с приложением.
    """
    from luxvoice.sysint.uinput import press_key

    backup = get_text()

    if not press_key("ctrl+c"):
        return ""

    # Приложению нужно время обработать нажатие.
    import time
    time.sleep(timeout)

    selected = get_text()

    # Возвращаем прежнее содержимое буфера, если выделение реально получено.
    if selected and selected != backup:
        set_text(backup)
        return selected

    return ""


def available() -> dict[str, bool]:
    """Какие инструменты работы с буфером доступны."""
    return {
        "wl-clipboard": _has("wl-copy") and _has("wl-paste"),
        "xclip": _has("xclip"),
        "xsel": _has("xsel"),
        "wayland": _is_wayland(),
    }


def copy_file_to_clipboard(path: str) -> bool:
    """Поместить файл в буфер обмена (для вставки в файловом менеджере)."""
    target = Path(path).expanduser()
    if not target.exists():
        return False

    if _has("wl-copy") and target.is_file():
        # wl-copy поддерживает передачу типа содержимого.
        try:
            subprocess.run(
                ["wl-copy", "--type", "text/uri-list"],
                input=f"file://{target}\n".encode("utf-8"),
                capture_output=True, timeout=5,
            )
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    if _has("xclip") and target.is_file():
        code, _ = _run(
            ["xclip", "-selection", "clipboard", "-t", "text/uri-list"],
            data=f"file://{target}\n".encode("utf-8"))
        return code == 0

    # Запасной вариант — положить путь как текст.
    return set_text(str(target))