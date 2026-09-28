"""Локализация интерфейса и голосовых ответов (русский / английский).

Русский — исходный язык: если перевода нет, возвращается русская строка.
Активный язык хранится в модуле, чтобы обращаться к tr() откуда угодно
без протаскивания объекта настроек.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_lock = threading.RLock()
_current = "ru"

LANGUAGES = {
    "ru": "Русский",
    "en": "English",
}

# Английские переводы. Ключ — русская строка-оригинал.
_EN: dict[str, str] = {
    # --- Приложение ---
    "Голосовой ассистент": "Voice assistant",
    "LuxVoice — голосовой ассистент": "LuxVoice — voice assistant",
    "Готов": "Ready",
    "Готов к работе": "Ready",
    "Слушаю": "Listening",
    "Слушаю…": "Listening…",
    "Не слушаю": "Not listening",
    "Выключен": "Off",
    "Думаю…": "Thinking…",
    "Выполняю…": "Running…",
    "Ошибка": "Error",
    "Внимание": "Attention",
    "Готово": "Done",
    "Отменено": "Cancelled",
    "Отмена": "Cancel",
    "Сохранить": "Save",
    "Сохранить всё": "Save all",
    "Закрыть": "Close",
    "Удалить": "Delete",
    "Добавить": "Add",
    "Изменить": "Edit",
    "Копировать": "Copy",
    "Вставить": "Paste",
    "Клонировать": "Clone",
    "Экспорт": "Export",
    "Импорт": "Import",
    "Обновить": "Refresh",
    "Поиск": "Search",
    "Настройки": "Settings",
    "Дополнения": "Add-ons",
    "Редактор команд": "Command editor",
    "История команд": "Command history",
    "Главная": "Home",
    "Панель управления": "Control panel",
    "Сбросить": "Reset",
    "По умолчанию": "Default",
    "Применить": "Apply",
    "Да": "Yes",
    "Нет": "No",
    "ОК": "OK",
    "Открыть": "Open",
    # --- Голосовой ввод ---
    "Голосовой ввод": "Voice input",
    "Движок распознавания": "Recognition engine",
    "Локальная модель": "Local model",
    "Облако": "Cloud",
    "Язык распознавания речи": "Speech language",
    "Авто": "Auto",
    "Русский": "Russian",
    "Модель микрофона": "Microphone device",
    "Системная по умолчанию": "System default",
    "Чувствительность распознавания": "Recognition sensitivity",
    "Чувствительность префикса": "Prefix sensitivity",
    "Автовключение микрофона": "Start listening on launch",
    "Микрофон включён": "Microphone on",
    "Микрофон выключен": "Microphone off",
    "Нужен префикс": "Prefix required",
    "Команда не найдена": "Command not found",
    "Команда выключена": "Command is disabled",
    "Ничего не расслышал": "Did not catch that",
    # --- Обращение ---
    "Обращение": "Wake word",
    "Префикс": "Prefix",
    "Режим префикса": "Prefix mode",
    "Фразы режимов": "Mode phrases",
    # --- Озвучка ---
    "Озвучка": "Speech output",
    "Голос": "Voice",
    "Громкость": "Volume",
    "Синтез речи": "Text-to-speech",
    "Голосовые ответы": "Voice replies",
    "Характер": "Personality",
    "Дворецкий": "Butler",
    "Дружелюбный": "Friendly",
    "Немногословный": "Terse",
    "Игровой": "Gamer",
    "Спокойный": "Calm",
    "Без характера": "Plain",
    # --- Действия ---
    "Действие": "Action",
    "Действия": "Actions",
    "Запуск": "Launch",
    "Клавиши": "Keys",
    "Мышь": "Mouse",
    "Программа": "Program",
    "Звук": "Sound",
    "Система": "System",
    "Браузер": "Browser",
    "Текст": "Text",
    "Файлы": "Files",
    "Окна": "Windows",
    "Консоль": "Shell",
    "Открыть сайт": "Open website",
    "Запустить файл или программу": "Launch file or program",
    "Нажать клавишу или сочетание": "Press key or shortcut",
    "Ввести текст": "Type text",
    "Нажать Enter": "Press Enter",
    "Пауза": "Pause",
    "Установить громкость": "Set volume",
    "Увеличить громкость": "Volume up",
    "Уменьшить громкость": "Volume down",
    "Мгновенно": "Instantly",
    "Плавно 0,5 секунды": "Smooth 0.5 s",
    "Плавно 1 секунда": "Smooth 1 s",
    "Плавно 1,5 секунды": "Smooth 1.5 s",
    "Плавно 2 секунды": "Smooth 2 s",
    "Заблокировать экран": "Lock screen",
    "Спящий режим": "Sleep",
    "Выключить компьютер": "Shut down",
    "Перезагрузить компьютер": "Restart",
    "Свернуть все окна": "Minimise all windows",
    "Показать рабочий стол": "Show desktop",
    "Создать папку": "Create folder",
    "Записать текст в файл": "Write text to file",
    "Найти в интернете": "Search the web",
    "Открыть первый результат": "Open first result",
    # --- Выполнение ---
    "Выполнение команд": "Command execution",
    "Слова подтверждения": "Confirmation words",
    "Слова отказа": "Rejection words",
    "Слова-связки": "Chain words",
    "Связка команд": "Command chaining",
    "Подтверждать": "Ask confirmation",
    "Включена": "Enabled",
    "Выключена": "Disabled",
    "Порог совпадения": "Match threshold",
    "Учитывать активное окно": "Use active window context",
    "Разрешить консоль": "Allow shell commands",
    "Разрешить запись файлов": "Allow file writing",
    "Безопасный режим": "Safe mode",
    # --- ИИ ---
    "ИИ-провайдер": "AI provider",
    "Ключ API": "API key",
    "Проверить подключение": "Test connection",
    "Где взять ключ": "Where to get a key",
    "Нейросеть": "Neural network",
    "Только ИИ": "AI only",
    "Только управление ПК": "PC control only",
    "Комбинированный": "Combined",
    "Модель": "Model",
    "Системная подсказка": "System prompt",
    "Поиск в интернете": "Web search",
    "Свежий ответ": "Fresh answer",
    "Ключ не задан": "No key configured",
    "Подключение успешно": "Connection successful",
    # --- Telegram и аватар ---
    "Плагины": "Plugins",
    "Токен бота": "Bot token",
    "Telegram ID владельца": "Owner Telegram ID",
    "Управление из Telegram": "Control from Telegram",
    "Аватар": "Avatar",
    "Поверх всех окон": "Always on top",
    "Реагировать на микрофон": "React to microphone",
    # --- Разделы настроек ---
    "Интерфейс": "Interface",
    "Поиск файлов": "File search",
    "Производительность": "Performance",
    "Приватность": "Privacy",
    "Дополнительно": "Advanced",
    "Общие": "General",
    "Язык интерфейса": "Interface language",
    "Тема": "Theme",
    "Тёмная": "Dark",
    "Светлая": "Light",
    "Как в системе": "Follow system",
    "Цвет акцента": "Accent colour",
    "Своё оформление": "Custom theme",
    "Запускать свёрнутым": "Start minimised",
    "Значок в трее": "Tray icon",
    "Закрывать в трей": "Close to tray",
    "Поверх окон": "Always on top",
    "Размер шрифта": "Font scale",
    # --- Редактор ---
    "Коллекция": "Collection",
    "Папка": "Folder",
    "Команда": "Command",
    "Новая команда": "New command",
    "Создать коллекцию": "Create collection",
    "Создать папку": "Create folder",
    "Создать новую команду": "Create new command",
    "Название": "Name",
    "Фразы для активации": "Trigger phrases",
    "Необязательные фразы": "Optional words",
    "Список действий": "Action list",
    "Параметр": "Parameter",
    "Нет действий": "No actions",
    "Фраза не задана": "Phrase not set",
    "Дубликат фразы": "Duplicate phrase",
    # --- Паки ---
    "Паки команд": "Command packs",
    "Установить": "Install",
    "Установлен": "Installed",
    "Категория": "Category",
    "Команд": "Commands",
    # --- История ---
    "Услышано": "Heard",
    "Выполнено": "Executed",
    "Время": "Time",
    "Результат": "Result",
    "Очистить": "Clear",
    # --- Ошибки ---
    "Не удалось": "Failed",
    "Компоненты не установлены": "Components are not installed",
    "Нет доступа к микрофону": "No microphone access",
    "Нет доступа к вводу": "No input injection access",
}


def set_language(code: str) -> None:
    global _current
    code = (code or "ru").lower()[:2]
    if code not in LANGUAGES:
        code = "ru"
    with _lock:
        _current = code


def get_language() -> str:
    with _lock:
        return _current


def tr(text: str, **fmt: object) -> str:
    """Перевести строку. Аргументы подставляются через str.format."""
    with _lock:
        lang = _current
    if lang == "en":
        out = _EN.get(text, text)
    else:
        out = text
    if fmt:
        try:
            out = out.format(**fmt)
        except (KeyError, IndexError, ValueError):
            log.debug("Не удалось подставить параметры в строку %r", text)
    return out


# Короткий псевдоним для частого использования.
_ = tr