"""Паки команд: готовые наборы для программ, сайтов и сервисов.

Пак — это набор папок с командами и фразами. После установки пак
становится обычной коллекцией в редакторе: его можно менять, выключать
и дополнять. Изменённые пользователем команды помечаются и не
перезаписываются при обновлении пака.

Паки описываются данными, а не кодом, поэтому новый пак добавляется
одним словарём без правки логики.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from luxvoice.core import paths
from luxvoice.core.events import PACKS_CHANGED, bus
from luxvoice.core.model import Action, Command, Node, KIND_COLLECTION, KIND_FOLDER
from luxvoice.core.store import CommandStore, get_store

log = logging.getLogger(__name__)

PACK_FORMAT = 1


@dataclass
class PackCommand:
    """Команда внутри пака."""

    title: str
    phrases: list[str]
    actions: list[tuple[str, dict[str, Any]]]
    optional: list[str] = field(default_factory=list)
    confirm: bool = False
    contexts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    cooldown: float = 0.0
    note: str = ""


@dataclass
class Pack:
    """Готовый набор команд."""

    key: str
    title: str
    category: str
    description: str = ""
    version: str = "1.0"
    # Команда → требования: приложение должно быть установлено.
    requires_app: str = ""
    url: str = ""
    folders: dict[str, list[PackCommand]] = field(default_factory=dict)
    author: str = "LuxVoice"
    free: bool = True


# --- Вспомогательные сокращения ---------------------------------------------

def C(title: str, phrases: list[str], actions: list[tuple[str, dict[str, Any]]],
      **kwargs: Any) -> PackCommand:
    """Короткая запись команды пака."""
    return PackCommand(title=title, phrases=phrases, actions=actions, **kwargs)


# --- Паки -------------------------------------------------------------------

PACKS: list[Pack] = [

    # =================== ОБЩИЕ: СИСТЕМА ===================
    Pack(
        key="system", title="Система", category="Основное",
        description="Окна, звук, питание, рабочий стол — базовые команды "
                    "для повседневной работы.",
        requires_app="", version="1.0",
        folders={
            "Окна": [
                C("Свернуть все окна", ["сверни все окна", "свернуть все окна",
                                        "покажи рабочий стол", "рабочий стол"],
                  [("show_desktop", {})]),
                C("Свернуть окно", ["сверни окно", "свернуть это окно"],
                  [("minimize_window", {})]),
                C("Развернуть окно", ["разверни окно", "на весь экран"],
                  [("maximize_window", {})]),
                C("Закрыть окно", ["закрой окно", "закрыть это окно"],
                  [("close_window", {})], confirm=True),
                C("Следующее окно", ["следующее окно", "переключи окно", "переключись"],
                  [("next_window", {})]),
                C("Предыдущее окно", ["предыдущее окно", "вернись назад"],
                  [("previous_window", {})]),
                C("Рабочий стол 1", ["первый рабочий стол", "рабочий стол один",
                                     "перейди на первый стол"],
                  [("switch_desktop", {"number": 1})]),
                C("Рабочий стол 2", ["второй рабочий стол", "рабочий стол два"],
                  [("switch_desktop", {"number": 2})]),
            ],
            "Звук": [
                C("Громче", ["сделай громче", "громче", "прибавь звук",
                             "увеличь громкость"],
                  [("volume_up", {"amount": 10})], optional=["пожалуйста"],
                  tags=["звук"]),
                C("Тише", ["сделай тише", "тише", "убавь звук",
                           "уменьши громкость", "потише"],
                  [("volume_down", {"amount": 10})], tags=["звук"]),
                C("Громкость 100", ["громкость на максимум", "максимальная громкость",
                                    "на всю громкость"],
                  [("set_volume", {"level": 100})]),
                C("Громкость 50", ["громкость пятьдесят", "средняя громкость",
                                   "громкость наполовину"],
                  [("set_volume", {"level": 50})]),
                C("Громкость 0", ["выключи звук", "без звука", "громкость ноль"],
                  [("set_volume", {"level": 0})]),
                C("Без звука", ["выключи звук совсем", "режим без звука", "мьют"],
                  [("toggle_mute", {})]),
                C("Тихий режим", ["тихий режим на десять"],
                  [("volume_up", {"amount": 10})]),
            ],
            "Монитор и питание": [
                C("Выключить монитор", ["выключи монитор", "погаси экран",
                                        "выключи экран"],
                  [("screen_off", {})]),
                C("Включить монитор", ["включи монитор", "включи экран"],
                  [("screen_on", {})]),
                C("Заблокировать", ["заблокируй экран", "заблокируй компьютер",
                                    "блокировка"],
                  [("lock_screen", {})]),
                C("Спящий режим", ["спящий режим", "усни", "режим сна"],
                  [("sleep", {})], confirm=True),
                C("Выключить компьютер", ["выключи компьютер", "выключение",
                                          "завершение работы"],
                  [("speak", {"text": "Выключаюсь"}), ("pause", {"seconds": 1.5}),
                   ("shutdown", {})], confirm=True),
                C("Перезагрузить компьютер", ["перезагрузи компьютер",
                                              "перезагрузка"],
                  [("speak", {"text": "Перезагружаюсь"}), ("pause", {"seconds": 1.5}),
                   ("reboot", {})], confirm=True),
                C("Снимок экрана", ["сделай снимок экрана", "скриншот",
                                    "снимок экрана"],
                  [("screenshot", {}), ("speak", {"text": "Снимок готов"})]),
            ],
            "Медиа": [
                C("Пауза или продолжить", ["пауза", "продолжи воспроизведение",
                                           "играй дальше"],
                  [("media_control", {"command": "play_pause"})],
                  contexts=["youtube", "spotify", "vlc", "музыка"]),
                C("Следующий трек", ["следующий трек", "следующая песня",
                                     "переключи трек"],
                  [("media_control", {"command": "next"})]),
                C("Предыдущий трек", ["предыдущий трек", "верни трек"],
                  [("media_control", {"command": "previous"})]),
            ],
        },
    ),

    # =================== СИСТЕМА: ФАЙЛЫ И КОНСОЛЬ ===================
    Pack(
        key="productivity", title="Работа с файлами", category="Основное",
        description="Создание папок и заметок, поиск файлов, быстрые "
                    "консольные проверки.",
        folders={
            "Заметки": [
                C("Быстрая заметка", ["сделай заметку", "запиши заметку"],
                  [("await_voice", {"question": "Что записать?", "timeout": 12,
                                    "save_as": "заметка"}),
                   ("write_file", {"path": "~/Заметки/заметки.txt",
                                   "text": "{заметка}\n", "append": True}),
                   ("speak", {"text": "Записал"})]),
                C("Список дел", ["покажи список дел", "открой список дел"],
                  [("open_path", {"path": "~/Заметки/заметки.txt"})]),
            ],
            "Консоль": [
                C("Сколько места на диске", ["сколько места на диске",
                                             "свободное место"],
                  [("run_shell", {"command": "df -h / /home | tail -3",
                                  "show_output": True}),
                   ("speak", {"text": "Проверил диски"})]),
                C("Загрузка процессора", ["загрузка процессора", "нагрузка на процессор"],
                  [("run_shell", {"command": "uptime", "show_output": True})]),
                C("Память", ["сколько оперативной памяти", "свободная память"],
                  [("run_shell", {"command": "free -h", "show_output": True})]),
                C("Список процессов", ["тяжёлые процессы", "что нагружает компьютер"],
                  [("run_shell", {"command": "ps aux --sort=-%cpu | head -8",
                                  "show_output": True})]),
                C("Открыть терминал", ["открой терминал", "запусти консоль"],
                  [("launch_app", {"path": "konsole"})]),
            ],
        },
    ),

    # =================== БРАУЗЕРЫ ===================
    Pack(
        key="browser", title="Браузеры", category="Интернет",
        description="Вкладки, адресная строка, поиск и навигация для "
                    "любого браузера.",
        folders={
            "Навигация": [
                C("Новая вкладка", ["новая вкладка", "открой вкладку"],
                  [("press_keys", {"keys": "ctrl+t"})],
                  contexts=["firefox", "chrome", "browser", "opera", "brave"]),
                C("Закрыть вкладку", ["закрой вкладку", "закрыть эту вкладку"],
                  [("press_keys", {"keys": "ctrl+w"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Вернуть вкладку", ["верни вкладку", "восстанови вкладку"],
                  [("press_keys", {"keys": "ctrl+shift+t"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Следующая вкладка", ["следующая вкладка", "вкладка вперёд"],
                  [("press_keys", {"keys": "ctrl+tab"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Предыдущая вкладка", ["предыдущая вкладка", "вкладка назад"],
                  [("press_keys", {"keys": "ctrl+shift+tab"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Обновить страницу", ["обнови страницу", "перезагрузи страницу"],
                  [("press_keys", {"keys": "f5"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Адресная строка", ["адресная строка", "перейди в строку адреса"],
                  [("press_keys", {"keys": "ctrl+l"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Назад", ["назад по истории", "вернись на страницу назад"],
                  [("press_keys", {"keys": "alt+left"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Вперёд", ["вперёд по истории"],
                  [("press_keys", {"keys": "alt+right"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Закрыть браузер", ["закрой браузер", "закрой все вкладки"],
                  [("close_app", {"title": "firefox"})], confirm=True),
            ],
            "Поиск": [
                C("Найти в интернете", ["найди в интернете", "поищи в интернете"],
                  [("await_voice", {"question": "Что найти?", "timeout": 12,
                                    "save_as": "запрос"}),
                   ("type_hotkey_text", {"keys": "ctrl+l",
                                         "text": "{запрос}", "enter": True}),
                   ("speak", {"text": "Ищу"})],
                  contexts=["firefox", "chrome", "browser"]),
                C("Гуглить", ["загугли", "погугли"],
                  [("await_voice", {"question": "Что найти?", "timeout": 12,
                                    "save_as": "запрос"}),
                   ("open_url", {"url": "https://www.google.com/search?q={запрос}"})]),
            ],
        },
    ),

    # =================== САЙТЫ ===================
    Pack(
        key="sites", title="Популярные сайты", category="Интернет",
        description="Быстрое открытие сервисов одной фразой.",
        folders={
            "Сервисы": [
                C("Ютуб", ["открой ютуб", "ютуб", "запусти youtube"],
                  [("open_url", {"url": "https://youtube.com"})],
                  contexts=["youtube"], tags=["сайт"]),
                C("ВКонтакте", ["открой вконтакте", "вк", "открой вк"],
                  [("open_url", {"url": "https://vk.com"})], tags=["сайт"]),
                C("Telegram веб", ["открой телеграм веб", "телеграм веб"],
                  [("open_url", {"url": "https://web.telegram.org"})],
                  tags=["сайт"]),
                C("Почта", ["открой почту", "проверь почту"],
                  [("open_url", {"url": "https://mail.google.com"})], tags=["сайт"]),
                C("Кинопоиск", ["открой кинопоиск", "кинопоиск"],
                  [("open_url", {"url": "https://www.kinopoisk.ru"})], tags=["сайт"]),
                C("Википедия", ["открой википедию", "википедия"],
                  [("open_url", {"url": "https://ru.wikipedia.org"})], tags=["сайт"]),
                C("GitHub", ["открой гитхаб", "гитхаб"],
                  [("open_url", {"url": "https://github.com"})], tags=["сайт"]),
                C("Twitch", ["открой твич", "твич"],
                  [("open_url", {"url": "https://www.twitch.tv"})], tags=["сайт"]),
                C("Яндекс", ["открой яндекс", "яндекс"],
                  [("open_url", {"url": "https://yandex.ru"})], tags=["сайт"]),
                C("Карты", ["открой карты", "яндекс карты"],
                  [("open_url", {"url": "https://yandex.ru/maps"})], tags=["сайт"]),
                C("Погода", ["какая погода", "покажи погоду", "погода"],
                  [("open_url", {"url": "https://yandex.ru/pogoda"})]),
            ],
        },
    ),

    # =================== YOUTUBE ===================
    Pack(
        key="youtube", title="YouTube", category="Медиа",
        description="Управление видео: пауза, звук, полноэкранный режим, "
                    "перемотка.",
        url="https://youtube.com", requires_app="",
        folders={
            "Воспроизведение": [
                C("Пауза", ["пауза", "останови видео", "пауза на ютубе"],
                  [("press_keys", {"keys": "k"})], contexts=["youtube"]),
                C("Продолжить", ["продолжи", "играй видео", "воспроизведи"],
                  [("press_keys", {"keys": "k"})], contexts=["youtube"]),
                C("Громче", ["сделай громче на ютубе"],
                  [("volume_up", {"amount": 10})], contexts=["youtube"]),
                C("Полный экран", ["на весь экран видео", "полноэкранный режим"],
                  [("press_keys", {"keys": "f"})], contexts=["youtube"]),
                C("На десять секунд вперёд", ["перемотай вперёд"],
                  [("press_keys", {"keys": "l"})], contexts=["youtube"]),
                C("На десять секунд назад", ["перемотай назад"],
                  [("press_keys", {"keys": "j"})], contexts=["youtube"]),
                C("Следующее видео", ["следующее видео", "включи следующее"],
                  [("press_keys", {"keys": "shift+n"})], contexts=["youtube"]),
                C("Кинотеатральный режим", ["режим кино на ютубе", "широкий экран"],
                  [("press_keys", {"keys": "t"})], contexts=["youtube"]),
                C("Субтитры", ["включи субтитры", "выключи субтитры"],
                  [("press_keys", {"keys": "c"})], contexts=["youtube"]),
                C("Промотать до конца", ["промотай до конца видео"],
                  [("press_keys", {"keys": "end"})], contexts=["youtube"]),
            ],
        },
    ),

    # =================== TELEGRAM ===================
    Pack(
        key="telegram", title="Telegram", category="Общение",
        description="Чаты, поиск, отправка сообщений.",
        folders={
            "Чаты": [
                C("Поиск в Telegram", ["найди в телеграме", "поиск в телеграме"],
                  [("press_keys", {"keys": "ctrl+k"})], contexts=["telegram", "ayugram"]),
                C("Новое сообщение", ["новое сообщение в телеграме", "новый чат",
                                    "напиши сообщение"],
                  [("press_keys", {"keys": "ctrl+n"})], contexts=["telegram"]),
                C("Следующий чат", ["следующий чат", "чат вниз"],
                  [("press_keys", {"keys": "ctrl+down"})], contexts=["telegram"]),
                C("Предыдущий чат", ["предыдущий чат", "чат вверх"],
                  [("press_keys", {"keys": "ctrl+up"})], contexts=["telegram"]),
                C("Закрыть чат", ["закрой чат"],
                  [("press_keys", {"keys": "escape"})], contexts=["telegram"]),
                C("Отправить", ["отправь сообщение"],
                  [("press_keys", {"keys": "enter"})], contexts=["telegram"]),
                C("Открыть Telegram", ["открой телеграм", "запусти телеграм"],
                  [("focus_app", {"title": "telegram"})]),
            ],
        },
    ),

    # =================== SPOTIFY И МУЗЫКА ===================
    Pack(
        key="spotify", title="Музыка", category="Медиа",
        description="Управление плеером: пауза, треки, громкость.",
        folders={
            "Управление": [
                C("Пауза Spotify", ["пауза в спотифае", "останови музыку"],
                  [("media_control", {"command": "play_pause"})],
                  contexts=["spotify", "музыка"]),
                C("Следующий трек", ["следующий трек", "переключи песню"],
                  [("media_control", {"command": "next"})]),
                C("Предыдущий трек", ["предыдущий трек", "верни песню"],
                  [("media_control", {"command": "previous"})]),
                C("Прибавить громкость", ["сделай музыку громче"],
                  [("app_volume", {"app": "spotify", "action": "delta", "value": 10})],
                  contexts=["spotify"]),
                C("Убавить громкость", ["сделай музыку тише"],
                  [("app_volume", {"app": "spotify", "action": "delta", "value": -10})],
                  contexts=["spotify"]),
                C("Открыть Spotify", ["открой спотифай", "запусти музыку"],
                  [("focus_app", {"title": "spotify"})]),
                C("Открыть Яндекс Музыку", ["открой яндекс музыку", "включи музыку"],
                  [("launch_app", {"path": "яндекс музыка"})]),
            ],
        },
    ),

    # =================== ИГРЫ ===================
    Pack(
        key="games", title="Игры", category="Игры",
        description="Быстрые нажатия, запуск игр, игровой режим. "
                    "Сочетания соответствуют управлению по умолчанию.",
        folders={
            "Игровой режим": [
                C("Игровой режим", ["игровой режим", "включи игровой режим"],
                  [("launch_app", {"path": "steam"}),
                   ("pause", {"seconds": 3}),
                   ("launch_app", {"path": "discord"}),
                   ("pause", {"seconds": 2}),
                   ("set_volume", {"level": 80}),
                   ("speak", {"text": "Игровой режим включён"})],
                  cooldown=10),
                C("Свернуть игру", ["сверни игру", "выйди в винду"],
                  [("press_keys", {"keys": "alt+tab"})]),
            ],
            "Steam": [
                C("Открыть Steam", ["открой стим", "запусти steam"],
                  [("focus_app", {"title": "steam"})]),
                C("Скриншот в Steam", ["сделай скриншот в стиме", "снимок в стиме"],
                  [("press_keys", {"keys": "f12"})], contexts=["steam"]),
                C("Оверлей Steam", ["открой оверлей стима", "оверлей стима"],
                  [("press_keys", {"keys": "shift+tab"})], contexts=["steam"]),
            ],
            "Minecraft": [
                C("Открыть Minecraft", ["открой майнкрафт", "запусти майн"],
                  [("launch_app", {"path": "minecraft"})]),
                C("Инвентарь", ["открой инвентарь", "инвентарь"],
                  [("press_keys", {"keys": "e"})], contexts=["minecraft", "майн"]),
                C("Меню Minecraft", ["меню майнкрафта", "пауза в майне"],
                  [("press_keys", {"keys": "escape"})], contexts=["minecraft"]),
                C("Третье лицо", ["вид от третьего лица", "третье лицо"],
                  [("press_keys", {"keys": "f5"})], contexts=["minecraft"]),
                C("Координаты", ["покажи координаты", "координаты"],
                  [("press_keys", {"keys": "f3"})], contexts=["minecraft"]),
                C("Крадёжка", ["режим крадёжки", "крадёжка"],
                  [("press_keys", {"keys": "shift"})], contexts=["minecraft"]),
            ],
            "Counter-Strike": [
                C("Открыть CS", ["открой кс", "запусти cs"],
                  [("launch_app", {"path": "steam steam://rungameid/730"})]),
                C("Скриншот в игре", ["скриншот в игре", "снимок в игре"],
                  [("press_keys", {"keys": "f5"})], contexts=["counter-strike", "cs2"]),
                C("Консоль игры", ["открой консоль игры", "консоль в игре"],
                  [("press_keys", {"keys": "grave"})], contexts=["counter-strike", "cs2"]),
            ],
            "Dota 2": [
                C("Открыть Dota", ["открой доту", "запусти dota"],
                  [("launch_app", {"path": "steam steam://rungameid/570"})]),
            ],
        },
    ),

    # =================== РАЗРАБОТКА ===================
    Pack(
        key="dev", title="Разработка", category="Работа",
        description="Редактор кода, терминал, Git — для программистов.",
        folders={
            "Редактор": [
                C("Открыть редактор", ["открой редактор кода", "запусти код"],
                  [("focus_app", {"title": "code"})]),
                C("Палитра команд", ["палитра команд", "команды редактора"],
                  [("press_keys", {"keys": "ctrl+shift+p"})],
                  contexts=["visual studio code", "code"]),
                C("Быстрое открытие файла", ["открой файл в редакторе"],
                  [("press_keys", {"keys": "ctrl+p"})],
                  contexts=["visual studio code", "code"]),
                C("Терминал в редакторе", ["терминал в редакторе"],
                  [("press_keys", {"keys": "ctrl+grave"})],
                  contexts=["visual studio code", "code"]),
                C("Форматировать файл", ["отформатируй файл", "форматирование"],
                  [("press_keys", {"keys": "ctrl+shift+i"})],
                  contexts=["visual studio code", "code"]),
                C("Найти в файле", ["найди в файле"],
                  [("press_keys", {"keys": "ctrl+f"})],
                  contexts=["visual studio code", "code", "kate"]),
                C("Сохранить файл", ["сохрани файл"],
                  [("press_keys", {"keys": "ctrl+s"})]),
            ],
        },
    ),

    # =================== ДОКУМЕНТЫ ===================
    Pack(
        key="office", title="Документы", category="Работа",
        description="Текстовые документы, таблицы, презентации.",
        folders={
            "Документы": [
                C("Открыть Word", ["открой ворд", "запусти word"],
                  [("launch_app", {"path": "writer"})]),
                C("Открыть Excel", ["открой эксель", "запусти таблицы"],
                  [("launch_app", {"path": "calc"})]),
                C("Открыть презентации", ["открой презентации", "запусти impress"],
                  [("launch_app", {"path": "impress"})]),
                C("Сохранить документ", ["сохрани документ"],
                  [("press_keys", {"keys": "ctrl+s"})],
                  contexts=["writer", "word", "calc", "excel"]),
            ],
        },
    ),

    # =================== СЦЕНАРИИ ===================
    Pack(
        key="scenarios", title="Готовые сценарии", category="Сценарии",
        description="Наборы действий из одной фразы: утро, кино, сон, "
                    "рабочий режим.",
        folders={
            "Режимы дня": [
                C("Доброе утро", ["доброе утро", "режим доброе утро", "утро"],
                  [("speak", {"text": "Доброе утро"}),
                   ("set_volume", {"level": 30}),
                   ("open_url", {"url": "https://yandex.ru/pogoda"}),
                   ("pause", {"seconds": 2}),
                   ("launch_app", {"path": "telegram"}),
                   ("speak", {"text": "Хорошего дня"})],
                  cooldown=30),
                C("Рабочий режим", ["рабочий режим", "режим работы", "включи работу"],
                  [("launch_app", {"path": "telegram"}),
                   ("pause", {"seconds": 1}),
                   ("launch_app", {"path": "code"}),
                   ("pause", {"seconds": 1}),
                   ("set_volume", {"level": 30}),
                   ("speak", {"text": "Все системы готовы"})],
                  cooldown=20),
                C("Режим кино", ["режим кино", "кино", "смотрим фильм"],
                  [("set_volume", {"level": 60}),
                   ("minimize_all", {}),
                   ("pause", {"seconds": 1}),
                   ("open_url", {"url": "https://www.kinopoisk.ru"}),
                   ("speak", {"text": "Приятного просмотра"})],
                  cooldown=20),
                C("Пора спать", ["пора спать", "спокойной ночи", "режим сна"],
                  [("speak", {"text": "Спокойной ночи"}),
                   ("pause", {"seconds": 2}),
                   ("set_volume", {"level": 0}),
                   ("pause", {"seconds": 1}),
                   ("sleep", {})], confirm=True, cooldown=30),
                C("Протокол у нас гости", ["протокол у нас гости", "у нас гости"],
                  [("press_keys", {"keys": "meta+d"}),
                   ("set_volume", {"level": 0}),
                   ("open_url", {"url": "https://www.kinopoisk.ru"}),
                   ("speak", {"text": "Протокол выполнен"})]),
                C("Убрать со стола", ["убери со стола", "закрой всё лишнее"],
                  [("minimize_all", {}),
                   ("open_path", {"path": "~/.local/share/Trash/files"}),
                   ("speak", {"text": "Готово"})]),
                C("Фокус", ["режим фокуса", "не беспокоить", "сосредоточиться"],
                  [("set_volume", {"level": 0}),
                   ("minimize_all", {}),
                   ("launch_app", {"path": "konsole"}),
                   ("speak", {"text": "Режим концентрации включён"})],
                  cooldown=20),
                C("Отдых от экрана", ["отдохни от экрана", "перерыв"],
                  [("speak", {"text": "Сделайте перерыв на пять минут"}),
                   ("pause", {"seconds": 2}),
                   ("screen_off", {})]),
            ],
        },
    ),

    # =================== ТЕКСТ И ВВОД ===================
    Pack(
        key="text", title="Ввод и диктовка", category="Работа",
        description="Быстрый ввод текста, шаблоны, работа с буфером обмена.",
        folders={
            "Ввод": [
                C("Надиктовать текст", ["надиктуй текст", "запиши что скажу"],
                  [("await_voice", {"question": "Говорите", "timeout": 30,
                                    "save_as": "текст"}),
                   ("type_text", {"text": "{текст}"}),
                   ("speak", {"text": "Готово"})]),
                C("Вставить дату", ["вставь дату", "напиши дату"],
                  [("type_text", {"text": "{date}"})]),
                C("Вставить время", ["вставь время", "напиши время"],
                  [("type_text", {"text": "{time}"})]),
                C("Копировать выделенное", ["скопируй выделенное", "копировать"],
                  [("press_keys", {"keys": "ctrl+c"})]),
                C("Вставить из буфера", ["вставь из буфера", "вставить"],
                  [("press_keys", {"keys": "ctrl+v"})]),
                C("Вырезать", ["вырежи выделенное"],
                  [("press_keys", {"keys": "ctrl+x"})]),
                C("Отменить действие", ["отмени действие", "верни как было"],
                  [("press_keys", {"keys": "ctrl+z"})]),
                C("Повторить действие", ["повтори действие", "верни обратно"],
                  [("press_keys", {"keys": "ctrl+shift+z"})]),
            ],
        },
    ),
]


PACKS_BY_KEY: dict[str, Pack] = {pack.key: pack for pack in PACKS}


def categories() -> list[str]:
    """Список категорий паков."""
    result: list[str] = []
    for pack in PACKS:
        if pack.category not in result:
            result.append(pack.category)
    return result


def find_packs(query: str = "", category: str = "") -> list[Pack]:
    """Найти паки по запросу и категории."""
    result = list(PACKS)
    if category:
        result = [p for p in result if p.category == category]
    if query:
        from luxvoice.core.store import normalize_phrase
        needle = normalize_phrase(query)
        filtered: list[Pack] = []
        for pack in result:
            haystack = normalize_phrase(
                f"{pack.title} {pack.description} {pack.category} {pack.key}")
            if needle in haystack:
                filtered.append(pack)
                continue
            # Ищем и по названиям команд внутри пака.
            for commands in pack.folders.values():
                if any(needle in normalize_phrase(c.title) for c in commands):
                    filtered.append(pack)
                    break
        result = filtered
    return result


def pack_command_count(pack: Pack) -> int:
    return sum(len(commands) for commands in pack.folders.values())


def pack_options(pack: Pack, languages: tuple[str, ...] = ("ru",)) -> list[str]:
    """Языки, доступные для пака."""
    return ["ru"]


# --- Установка --------------------------------------------------------------

class PackManager:
    """Установка и удаление паков команд."""

    def __init__(self, store: CommandStore | None = None) -> None:
        self._store = store or get_store()
        self._state_path = paths.packs_dir() / "installed.json"
        self._installed: dict[str, dict[str, Any]] = {}
        self._load_state()

    # --- Состояние --------------------------------------------------------

    def _load_state(self) -> None:
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._installed = data
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Не удалось прочитать состояние паков: %s", exc)
            self._installed = {}

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(self._installed, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as exc:
            log.error("Не удалось сохранить состояние паков: %s", exc)

    def is_installed(self, key: str) -> bool:
        return key in self._installed

    def installed_version(self, key: str) -> str:
        info = self._installed.get(key, {})
        return str(info.get("version", ""))

    def installed_packs(self) -> dict[str, dict[str, Any]]:
        return dict(self._installed)

    def needs_update(self, pack: Pack) -> bool:
        """Установлен ли пак и требуется ли обновление."""
        if not self.is_installed(pack.key):
            return False
        return self.installed_version(pack.key) != pack.version

    # --- Установка --------------------------------------------------------

    def install(self, pack: Pack, folders: list[str] | None = None,
                node_id: str | None = None) -> dict[str, int]:
        """Установить пак.

        folders — какие папки пака ставить (пусто — все).
        node_id — куда положить (пусто — создать коллекцию с именем пака).
        """
        store = self._store
        store.start_batch()
        stats = {"added": 0, "skipped": 0, "folders": 0}

        try:
            # Определяем, куда ставить.
            if node_id:
                target = store.find_node(node_id)
                if target is None:
                    target = store.create_collection(pack.title)
                    stats["folders"] += 1
            else:
                target = self._collection_for(pack)
                stats["folders"] += 1

            for folder_name, commands in pack.folders.items():
                if folders and folder_name not in folders:
                    continue

                folder = self._folder_for(target, folder_name)

                for spec in commands:
                    existing = self._find_existing(spec)
                    if existing is not None:
                        stats["skipped"] += 1
                        continue

                    command = self._build_command(spec, pack)
                    store._commands[command.id] = command
                    store._links[command.id] = folder
                    stats["added"] += 1

            store._reindex()
            store._dirty = True
        finally:
            store.end_batch(save=True)

        # Запоминаем установку.
        self._installed[pack.key] = {
            "version": pack.version,
            "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
            "added": stats["added"],
            "node": target.id if target else "",
            "folders": sorted(pack.folders.keys()),
        }
        self._save_state()

        bus.publish(PACKS_CHANGED)
        log.info("Пак «%s» установлен: добавлено команд %d, пропущено %d",
                 pack.title, stats["added"], stats["skipped"])
        return stats

    def _collection_for(self, pack: Pack) -> Node:
        """Найти коллекцию пака или создать её."""
        store = self._store
        for node in store.collections():
            if node.kind == KIND_COLLECTION and node.title == pack.title:
                return node
        # Ищем по сохранённому состоянию.
        info = self._installed.get(pack.key)
        if info:
            node = store.find_node(str(info.get("node", "")))
            if node is not None:
                return node
        return store.create_collection(pack.title)

    def _folder_for(self, parent: Node, title: str) -> Node:
        """Найти папку внутри коллекции или создать её."""
        for child in parent.children:
            if child.title == title and child.kind in (KIND_FOLDER, KIND_COLLECTION):
                return child
        return self._store.create_folder(parent.id, title) or parent

    def _find_existing(self, spec: PackCommand) -> Command | None:
        """Уже есть команда с такой фразой?"""
        for phrase in spec.phrases:
            owner = self._store.phrase_owner(phrase)
            if owner is not None:
                return owner
        return None

    def _build_command(self, spec: PackCommand, pack: Pack) -> Command:
        """Собрать команду из описания пака."""
        command = Command(
            title=spec.title,
            phrases=list(spec.phrases),
            optional=list(spec.optional),
            confirm=spec.confirm,
            contexts=list(spec.contexts),
            tags=list(spec.tags),
            cooldown=spec.cooldown,
            pack=pack.key,
            pack_version=pack.version,
        )
        for action_type, params in spec.actions:
            command.add_action(Action(type=action_type, params=dict(params)))
        return command

    # --- Обновление и удаление -------------------------------------------

    def update(self, pack: Pack) -> dict[str, int]:
        """Обновить пак, не трогая изменённые пользователем команды."""
        store = self._store
        store.start_batch()
        stats = {"added": 0, "updated": 0, "kept": 0}

        try:
            info = self._installed.get(pack.key, {})
            target = store.find_node(str(info.get("node", "")))
            if target is None:
                target = self._collection_for(pack)

            for folder_name, commands in pack.folders.items():
                folder = self._folder_for(target, folder_name)
                for spec in commands:
                    existing = self._find_existing(spec)
                    if existing is None:
                        command = self._build_command(spec, pack)
                        store._commands[command.id] = command
                        store._links[command.id] = folder
                        stats["added"] += 1
                        continue

                    if existing.user_modified:
                        # Пользователь правил команду — оставляем как есть.
                        stats["kept"] += 1
                        continue

                    # Обновляем, сохраняя идентификатор и статистику.
                    fresh = self._build_command(spec, pack)
                    fresh.id = existing.id
                    fresh.run_count = existing.run_count
                    fresh.last_run = existing.last_run
                    fresh.enabled = existing.enabled
                    store._commands[fresh.id] = fresh
                    stats["updated"] += 1

            store._reindex()
            store._dirty = True
        finally:
            store.end_batch(save=True)

        info = self._installed.get(pack.key, {})
        info.update({
            "version": pack.version,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._installed[pack.key] = info
        self._save_state()

        bus.publish(PACKS_CHANGED)
        return stats

    def uninstall(self, pack: Pack, keep_modified: bool = True) -> int:
        """Удалить пак. Возвращает число удалённых команд."""
        store = self._store
        removed = 0

        store.start_batch()
        try:
            for command in list(store.commands()):
                if command.pack != pack.key:
                    continue
                if keep_modified and command.user_modified:
                    # Изменённые команды остаются, но перестают быть частью пака.
                    command.pack = ""
                    command.pack_version = ""
                    continue
                store._commands.pop(command.id, None)
                store._links.pop(command.id, None)
                removed += 1

            # Удаляем пустые папки пака.
            info = self._installed.get(pack.key, {})
            node = store.find_node(str(info.get("node", "")))
            if node is not None and not store.commands_in(node.id):
                store._nodes = [n for n in store._nodes if n.id != node.id]

            store._reindex()
            store._dirty = True
        finally:
            store.end_batch(save=True)

        self._installed.pop(pack.key, None)
        self._save_state()
        bus.publish(PACKS_CHANGED)
        log.info("Пак «%s» удалён: убрано команд %d", pack.title, removed)
        return removed

    def remove_missing(self) -> None:
        """Убрать из состояния паки, команды которых больше не существуют."""
        store = self._store
        active = {c.pack for c in store.commands() if c.pack}
        for key in list(self._installed.keys()):
            if key not in active:
                self._installed.pop(key, None)
        self._save_state()

    # --- Экспорт и импорт пака -------------------------------------------

    def export_pack(self, pack: Pack) -> dict[str, Any]:
        """Сохранить пак в файл."""
        return {
            "format": "luxvoice.pack",
            "version": PACK_FORMAT,
            "key": pack.key,
            "title": pack.title,
            "category": pack.category,
            "description": pack.description,
            "pack_version": pack.version,
            "folders": {
                name: [
                    {
                        "title": c.title,
                        "phrases": c.phrases,
                        "optional": c.optional,
                        "actions": [{"type": t, "params": p} for t, p in c.actions],
                        "confirm": c.confirm,
                        "contexts": c.contexts,
                        "tags": c.tags,
                        "cooldown": c.cooldown,
                    }
                    for c in commands
                ]
                for name, commands in pack.folders.items()
            },
        }

    def import_pack(self, data: dict[str, Any]) -> Pack | None:
        """Прочитать пак из файла."""
        if not isinstance(data, dict):
            return None
        folders_raw = data.get("folders")
        if not isinstance(folders_raw, dict):
            return None

        folders: dict[str, list[PackCommand]] = {}
        for folder_name, commands in folders_raw.items():
            items: list[PackCommand] = []
            for item in commands if isinstance(commands, list) else []:
                if not isinstance(item, dict):
                    continue
                actions = []
                for action in item.get("actions", []):
                    if isinstance(action, dict) and action.get("type"):
                        actions.append((str(action["type"]),
                                        dict(action.get("params") or {})))
                phrases = item.get("phrases") or []
                if isinstance(phrases, str):
                    phrases = [phrases]
                items.append(PackCommand(
                    title=str(item.get("title") or "Без названия"),
                    phrases=[str(p) for p in phrases],
                    actions=actions,
                    optional=[str(o) for o in (item.get("optional") or [])],
                    confirm=bool(item.get("confirm", False)),
                    contexts=[str(c) for c in (item.get("contexts") or [])],
                    tags=[str(t) for t in (item.get("tags") or [])],
                    cooldown=float(item.get("cooldown", 0) or 0),
                ))
            if items:
                folders[str(folder_name)] = items

        if not folders:
            return None

        return Pack(
            key=str(data.get("key") or f"custom-{int(time.time())}"),
            title=str(data.get("title") or "Свой пак"),
            category=str(data.get("category") or "Свои паки"),
            description=str(data.get("description") or ""),
            version=str(data.get("pack_version") or "1.0"),
            folders=folders,
            author=str(data.get("author") or "пользователь"),
        )


# --- Одиночный экземпляр ----------------------------------------------------

_manager: PackManager | None = None
_lock = __import__("threading").Lock()


def get_pack_manager(store: CommandStore | None = None) -> PackManager:
    global _manager
    with _lock:
        if _manager is None:
            _manager = PackManager(store)
        elif store is not None:
            _manager._store = store
        return _manager