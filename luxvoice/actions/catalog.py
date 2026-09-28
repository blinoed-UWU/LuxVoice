"""Каталог действий: описание всего, что умеет команда.

Каждое действие описано один раз: имя, группа, параметры, признак
опасности. Из этого описания строятся:
  * список действий в редакторе команд;
  * поля ввода параметров с подсказками;
  * проверка допустимости и запрос подтверждения;
  * подстановки переменных (громкость, дата, выделенный текст).

Реализация самих действий — в executor.py; здесь только описание
и разбор параметров.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --- Группы действий --------------------------------------------------------

GROUPS: tuple[tuple[str, str, str], ...] = (
    ("launch", "Запуск", "Открывает программы, файлы и сайты"),
    ("keys", "Клавиши", "Нажатия клавиш и ввод текста"),
    ("mouse", "Мышь", "Курсор, клики, прокрутка"),
    ("window", "Окна", "Свернуть, закрыть, переключить"),
    ("sound", "Звук", "Громкость, микшер, воспроизведение файлов"),
    ("speech", "Озвучка", "Голосовые ответы ассистента"),
    ("system", "Система", "Питание, блокировка, монитор"),
    ("files", "Файлы", "Создание папок и запись текста"),
    ("shell", "Консоль", "Команды оболочки и сценарии"),
    ("flow", "Управление", "Паузы, повторы, условия, переменные"),
)

GROUP_LABELS = {key: label for key, label, _ in GROUPS}


# --- Параметр действия ------------------------------------------------------

@dataclass(frozen=True)
class Param:
    """Описание одного параметра действия."""

    key: str = "value"
    label: str = "Параметр"
    kind: str = "str"        # str, text, int, float, choice, bool, keys, path, dir, color
    default: Any = ""
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1.0
    unit: str = ""
    placeholder: str = ""
    required: bool = True
    dynamic: str = ""        # источник списка: mics, sinks, apps, voices, windows


# --- Действие ---------------------------------------------------------------

@dataclass(frozen=True)
class ActionSpec:
    """Описание действия."""

    type: str
    label: str
    group: str
    params: tuple[Param, ...] = ()
    help: str = ""
    dangerous: bool = False       # требует подтверждения по умолчанию
    needs_shell: bool = False     # требует разрешения консоли
    needs_write: bool = False     # требует разрешения записи файлов
    needs_input: bool = False     # требует доступа к виртуальному вводу
    platform_note: str = ""       # особенности на Linux/Wayland


# --- Переменные подстановки -------------------------------------------------

VARIABLES: tuple[tuple[str, str], ...] = (
    ("{volume}", "Текущая громкость системы"),
    ("{date}", "Сегодняшняя дата"),
    ("{time}", "Текущее время"),
    ("{date:%d.%m.%Y}", "Дата в своём формате"),
    ("{time:%H:%M}", "Время в своём формате"),
    ("{user}", "Имя пользователя"),
    ("{home}", "Домашний каталог"),
    ("{hostname}", "Имя компьютера"),
    ("{clipboard}", "Содержимое буфера обмена"),
    ("{selected}", "Выделенный текст в активном окне"),
    ("{active_window}", "Заголовок активного окна"),
    ("{phrase}", "Фраза, которой вызвана команда"),
    ("{random:1-10}", "Случайное число из диапазона"),
    ("{counter}", "Счётчик запусков этой команды"),
)


# --- Сам каталог ------------------------------------------------------------

_ACTIONS: list[ActionSpec] = [

    # ================= ЗАПУСК =================
    ActionSpec("open_url", "Открыть сайт", "launch",
               params=(Param("url", "Адрес сайта", "str", "https://youtube.com",
                             "Полный адрес или название сервиса: «ютуб», «кинопоиск».",
                             placeholder="https://example.com"),
                       Param("browser", "В каком браузере", "str", "",
                             "Пусто — браузер по умолчанию.", required=False)),
               help="Открывает адрес в браузере."),

    ActionSpec("launch_app", "Запустить файл или программу", "launch",
               params=(Param("path", "Программа или файл", "str", "",
                             "Путь к программе, имя команды или название приложения.",
                             placeholder="firefox"),
                       Param("args", "Аргументы", "str", "",
                             "Передаются программе при запуске.", required=False),
                       Param("as_app", "Искать по названию программы", "bool", True,
                             "Включено: «телега» найдёт Telegram. "
                             "Выключено: запускать ровно то, что указано.", required=False)),
               help="Запускает программу или открывает файл."),

    ActionSpec("open_path", "Открыть файл или папку", "launch",
               params=(Param("path", "Путь", "path", "",
                             "Файл или папка для открытия программой по умолчанию."),),
               help="Открывает файл или папку в связанной программе."),

    ActionSpec("focus_app", "Переключиться на программу", "launch",
               params=(Param("title", "Название окна или программы", "str", "",
                             "Если программа уже запущена — переключиться на её окно, "
                             "иначе запустить.", placeholder="discord"),),
               help="Показывает окно уже запущенной программы."),

    ActionSpec("close_app", "Закрыть программу", "launch",
               params=(Param("title", "Название окна или программы", "str", "",
                             "Пусто — закрыть активное окно."),),
               help="Закрывает программу по названию окна.",
               dangerous=True),

    ActionSpec("run_terminal", "Открыть в терминале", "launch",
               params=(Param("command", "Команда", "str", "",
                             "Команда, которая выполнится в новом окне терминала."),),
               help="Открывает команду в отдельном окне терминала.",
               needs_shell=True),

    # ================= КЛАВИШИ =================
    ActionSpec("press_keys", "Нажать клавишу или сочетание", "keys",
               params=(Param("keys", "Сочетание клавиш", "keys", "",
                             "Нажмите сочетание в поле или впишите вручную: "
                             "ctrl+shift+esc, win+d, f5.",
                             placeholder="ctrl+s"),),
               help="Нажимает клавишу или сочетание клавиш.",
               needs_input=True),

    ActionSpec("type_text", "Ввести текст", "keys",
               params=(Param("text", "Текст", "text", "",
                             "Текст на любом языке. Можно использовать переменные "
                             "вроде {date} или {clipboard}."),),
               help="Печатает текст в активном окне.",
               needs_input=True),

    ActionSpec("type_enter", "Нажать Enter", "keys",
               params=(Param("count", "Сколько раз", "int", 1, "",
                             minimum=1, maximum=20, required=False),),
               help="Нажимает Enter заданное число раз.",
               needs_input=True),

    ActionSpec("type_hotkey_text", "Сочетание, затем текст", "keys",
               params=(Param("keys", "Сочетание", "keys", "ctrl+l",
                             "Например, ctrl+l — переход к адресной строке."),
                       Param("text", "Текст", "text", "", ""),
                       Param("enter", "Нажать Enter после", "bool", True, "",
                             required=False)),
               help="Нажимает сочетание и печатает текст — удобно для поиска в браузере.",
               needs_input=True),

    ActionSpec("dictate", "Диктовка Windows-стиля", "keys",
               params=(Param("keys", "Сочетание диктовки", "keys", "meta+h",
                             "В Linux аналог встроенной диктовки отсутствует; "
                             "действие можно переназначить на свою программу."),),
               help="Открывает диктовку системы.",
               needs_input=True,
               platform_note="В Linux нет встроенной диктовки — "
                             "используйте действие «Ввести текст».",),

    # ================= МЫШЬ =================
    ActionSpec("mouse_click", "Клик мышью", "mouse",
               params=(Param("button", "Кнопка", "choice", "left", "",
                             choices=(("left", "Левая"), ("right", "Правая"),
                                      ("middle", "Средняя")), required=False),
                       Param("double", "Двойной клик", "bool", False, "",
                             required=False),
                       Param("count", "Сколько кликов", "int", 1, "",
                             minimum=1, maximum=10, required=False)),
               help="Нажимает кнопку мыши в текущей позиции курсора.",
               needs_input=True),

    ActionSpec("mouse_move", "Сдвинуть курсор", "mouse",
               params=(Param("dx", "По горизонтали", "int", 0,
                             "Положительное — вправо, отрицательное — влево.",
                             required=False),
                       Param("dy", "По вертикали", "int", 0,
                             "Положительное — вниз, отрицательное — вверх.",
                             required=False)),
               help="Смещает курсор относительно текущего положения.",
               needs_input=True),

    ActionSpec("mouse_to", "Переместить курсор в координаты", "mouse",
               params=(Param("x", "X", "int", 0, "", required=False),
                       Param("y", "Y", "int", 0, "", required=False)),
               help="Перемещает курсор в точку на экране.",
               needs_input=True,
               platform_note="В Wayland координаты отсчитываются от верхнего "
                             "левого угла экрана; точность зависит от настроек."),

    ActionSpec("mouse_scroll", "Прокрутить колесо", "mouse",
               params=(Param("amount", "Сколько", "int", 3,
                             "Положительное — вверх, отрицательное — вниз.",
                             required=False),
                       Param("horizontal", "Горизонтально", "bool", False, "",
                             required=False)),
               help="Прокручивает содержимое окна.",
               needs_input=True),

    ActionSpec("mouse_drag", "Перетащить", "mouse",
               params=(Param("dx", "По горизонтали", "int", 0, "", required=False),
                       Param("dy", "По вертикали", "int", 0, "", required=False),
                       Param("button", "Кнопка", "choice", "left", "",
                             choices=(("left", "Левая"), ("right", "Правая")),
                             required=False)),
               help="Зажимает кнопку, перемещает курсор и отпускает.",
               needs_input=True),

    ActionSpec("mouse_hold", "Зажать кнопку мыши", "mouse",
               params=(Param("button", "Кнопка", "choice", "left", "",
                             choices=(("left", "Левая"), ("right", "Правая")),
                             required=False),
                       Param("seconds", "Секунд", "float", 1.0, "",
                             minimum=0.1, maximum=60, unit="с", required=False)),
               help="Удерживает кнопку мыши заданное время.",
               needs_input=True),

    # ================= ОКНА =================
    ActionSpec("show_desktop", "Показать рабочий стол", "window",
               params=(),
               help="Сворачивает все окна и показывает рабочий стол.",
               needs_input=True),

    ActionSpec("minimize_all", "Свернуть все окна", "window",
               params=(),
               help="Сворачивает все открытые окна.",
               needs_input=True),

    ActionSpec("minimize_window", "Свернуть активное окно", "window",
               params=(),
               help="Сворачивает текущее окно.",
               needs_input=True),

    ActionSpec("maximize_window", "Развернуть активное окно", "window",
               params=(),
               help="Разворачивает окно на весь экран.",
               needs_input=True),

    ActionSpec("close_window", "Закрыть активное окно", "window",
               params=(),
               help="Закрывает окно, которое сейчас активно.",
               dangerous=True),

    ActionSpec("switch_window", "Переключить окно", "window",
               params=(Param("title", "Название окна", "str", "", "",
                             required=False),),
               help="Переходит к окну по названию.",
               needs_input=True),

    ActionSpec("next_window", "Следующее окно", "window",
               params=(),
               help="Переключается на следующее окно.",
               needs_input=True),

    ActionSpec("previous_window", "Предыдущее окно", "window",
               params=(),
               help="Переключается на предыдущее окно.",
               needs_input=True),

    ActionSpec("switch_desktop", "Сменить рабочий стол", "window",
               params=(Param("number", "Номер стола", "int", 1, "",
                             minimum=1, maximum=20, required=False),),
               help="Переключает на рабочий стол с указанным номером.",
               needs_input=True),

    # ================= ЗВУК =================
    ActionSpec("set_volume", "Установить громкость", "sound",
               params=(Param("level", "Уровень", "int", 50,
                             "Значение от 0 до 100 процентов.",
                             minimum=0, maximum=100, unit="%"),
                       Param("smooth", "Плавно", "choice", "smooth", "",
                             choices=(("instant", "Мгновенно"),
                                      ("smooth", "Плавно 0,5 секунды"),
                                      ("slow", "Плавно 1 секунда")),
                             required=False)),
               help="Устанавливает системную громкость."),

    ActionSpec("volume_up", "Увеличить громкость", "sound",
               params=(Param("amount", "На сколько", "int", 10, "Процентов.",
                             minimum=1, maximum=100, unit="%", required=False),),
               help="Прибавляет громкость."),

    ActionSpec("volume_down", "Уменьшить громкость", "sound",
               params=(Param("amount", "На сколько", "int", 10, "Процентов.",
                             minimum=1, maximum=100, unit="%", required=False),),
               help="Убавляет громкость."),

    ActionSpec("toggle_mute", "Выключить или включить звук", "sound",
               params=(),
               help="Переключает режим «без звука»."),

    ActionSpec("mute", "Отключить звук", "sound",
               params=(),
               help="Выключает звук в системе."),

    ActionSpec("unmute", "Включить звук", "sound",
               params=(),
               help="Включает звук."),

    ActionSpec("app_volume", "Громкость приложения", "sound",
               params=(Param("app", "Приложение", "str", "",
                             "Имя программы в микшере. Пусто — активное окно.",
                             required=False, dynamic="apps"),
                       Param("action", "Что сделать", "choice", "delta", "",
                             choices=(("delta", "Изменить на"),
                                      ("set", "Установить равной"),
                                      ("mute", "Отключить"),
                                      ("unmute", "Включить")),
                             required=False),
                       Param("value", "Значение", "int", 10,
                             "Проценты. Для «изменить на» может быть отрицательным.",
                             required=False)),
               help="Меняет громкость отдельной программы в микшере, "
                    "не затрагивая остальные."),

    ActionSpec("play_sound", "Воспроизвести файл", "sound",
               params=(Param("path", "Звуковой файл", "path", "",
                             "Поддерживаются WAV, MP3, OGG и другие форматы."),
                       Param("wait", "Дождаться окончания", "bool", False, "",
                             required=False)),
               help="Проигрывает звуковой файл."),

    ActionSpec("stop_sound", "Остановить воспроизведение", "sound",
               params=(),
               help="Прекращает проигрывание, запущенное ассистентом."),

    # ================= ОЗВУЧКА =================
    ActionSpec("speak", "Произнести фразу", "speech",
               params=(Param("text", "Что сказать", "text", "",
                             "Можно использовать переменные и свои фразы."),),
               help="Ассистент произносит фразу выбранным голосом."),

    ActionSpec("speak_phrase", "Произнести готовую фразу", "speech",
               params=(Param("kind", "Какая фраза", "choice", "ok", "",
                             choices=(("ok", "Готово"), ("activate", "Слушаю"),
                                      ("error", "Ошибка"), ("confirm", "Подтверждение"),
                                      ("start", "Приветствие"),
                                      ("thinking", "Секунду"),
                                      ("bye", "Прощание")),
                             required=False),),
               help="Произносит фразу из набора характера ассистента."),

    ActionSpec("speak_random", "Произнести случайную фразу", "speech",
               params=(Param("phrases", "Список фраз", "text", "",
                             "По одной фразе в строке — ассистент выберет случайную."),),
               help="Произносит одну из перечисленных фраз — добавляет разнообразия."),

    ActionSpec("beep", "Звуковой сигнал", "speech",
               params=(Param("kind", "Сигнал", "choice", "soft", "",
                             choices=(("soft", "Мягкий"), ("beep", "Короткий"),
                                      ("ok", "Подтверждение"), ("error", "Ошибка")),
                             required=False),),
               help="Короткий сигнал вместо фразы."),

    ActionSpec("stop_speech", "Прекратить речь ассистента", "speech",
               params=(),
               help="Ассистент замолкает."),

    # ================= СИСТЕМА =================
    ActionSpec("lock_screen", "Заблокировать экран", "system",
               params=(),
               help="Блокирует сеанс — потребуется пароль."),

    ActionSpec("sleep", "Спящий режим", "system",
               params=(),
               help="Переводит компьютер в спящий режим.",
               dangerous=True),

    ActionSpec("shutdown", "Выключить компьютер", "system",
               params=(Param("delay", "Задержка", "int", 0,
                             "Секунд до выключения. 0 — сразу.",
                             minimum=0, maximum=3600, unit="с", required=False),),
               help="Выключает компьютер.",
               dangerous=True),

    ActionSpec("reboot", "Перезагрузить компьютер", "system",
               params=(),
               help="Перезагружает компьютер.",
               dangerous=True),

    ActionSpec("logout", "Выйти из сеанса", "system",
               params=(),
               help="Завершает сеанс пользователя.",
               dangerous=True),

    ActionSpec("screen_off", "Выключить монитор", "system",
               params=(),
               help="Гасит экран без завершения работы."),

    ActionSpec("screen_on", "Включить монитор", "system",
               params=(),
               help="Возвращает экран к работе."),

    ActionSpec("screenshot", "Сделать снимок экрана", "system",
               params=(Param("path", "Куда сохранить", "dir", "",
                             "Пусто — в папку изображений.",
                             required=False),
                       Param("copy", "Скопировать в буфер", "bool", False, "",
                             required=False)),
               help="Сохраняет снимок экрана."),

    ActionSpec("clipboard_copy", "Скопировать текст", "system",
               params=(Param("text", "Текст", "text", "",
                             "Текст, который попадёт в буфер обмена."),),
               help="Помещает текст в буфер обмена."),

    ActionSpec("clipboard_paste", "Вставить из буфера", "system",
               params=(),
               help="Вставляет содержимое буфера в активное окно.",
               needs_input=True),

    ActionSpec("notification", "Показать уведомление", "system",
               params=(Param("text", "Текст", "str", "", ""),
                       Param("title", "Заголовок", "str", "Ассистент", "",
                             required=False)),
               help="Показывает всплывающее уведомление."),

    ActionSpec("open_settings", "Открыть системные настройки", "system",
               params=(Param("page", "Раздел", "str", "",
                             "Например: sound, display, network. Пусто — общие.",
                             required=False),),
               help="Открывает настройки системы."),

    ActionSpec("media_control", "Управление медиа", "system",
               params=(Param("command", "Команда", "choice", "play_pause", "",
                             choices=(("play_pause", "Пауза или продолжить"),
                                      ("next", "Следующий трек"),
                                      ("previous", "Предыдущий трек"),
                                      ("stop", "Остановить")),
                             required=False),),
               help="Управляет текущим проигрывателем.",
               needs_input=True),

    ActionSpec("brightness", "Яркость экрана", "system",
               params=(Param("action", "Что сделать", "choice", "up", "",
                             choices=(("up", "Ярче"), ("down", "Тусклее"),
                                      ("set", "Установить")),
                             required=False),
                       Param("value", "Значение", "int", 10, "Процентов.",
                             minimum=1, maximum=100, unit="%", required=False)),
               help="Меняет яркость экрана."),

    # ================= ФАЙЛЫ =================
    ActionSpec("create_folder", "Создать папку", "files",
               params=(Param("path", "Путь к папке", "dir", "",
                             "Например: ~/Документы/Проекты. "
                             "Промежуточные папки создаются автоматически."),),
               help="Создаёт папку, включая все промежуточные.",
               needs_write=True),

    ActionSpec("write_file", "Записать текст в файл", "files",
               params=(Param("path", "Путь к файлу", "path", "",
                             "Например: ~/Заметки/список.txt."),
                       Param("text", "Текст", "text", "",
                             "Содержимое файла. Можно использовать переменные."),
                       Param("append", "Дописать в конец", "bool", False,
                             "Включено: текст добавится к существующему файлу.",
                             required=False)),
               help="Записывает текст в файл.",
               needs_write=True),

    ActionSpec("delete_path", "Удалить файл или папку", "files",
               params=(Param("path", "Путь", "path", "", ""),
                       Param("permanent", "Удалить навсегда", "bool", False,
                             "Выключено: файл перемещается в корзину — "
                             "это безопаснее.", required=False)),
               help="Удаляет файл или папку.",
               dangerous=True, needs_write=True),

    ActionSpec("copy_path", "Копировать файл", "files",
               params=(Param("source", "Откуда", "path", "", ""),
                       Param("target", "Куда", "str", "", "")),
               help="Копирует файл или папку.",
               needs_write=True),

    ActionSpec("move_path", "Переместить файл", "files",
               params=(Param("source", "Откуда", "path", "", ""),
                       Param("target", "Куда", "str", "", "")),
               help="Перемещает или переименовывает файл.",
               needs_write=True),

    # ================= КОНСОЛЬ =================
    ActionSpec("run_shell", "Выполнить команду консоли", "shell",
               params=(Param("command", "Команда", "text", "",
                             "Команда оболочки. Например: df -h, "
                             "pacman -Qq | wc -l. "
                             "Префикс keep: оставит окно открытым."),
                       Param("keep_open", "Оставить окно открытым", "bool", False,
                             "", required=False),
                       Param("as_user", "От имени пользователя", "bool", True,
                             "Выключено: попытка выполнить с правами администратора.",
                             required=False),
                       Param("show_output", "Показать результат", "bool", False,
                             "Вывести результат голосом или уведомлением.",
                             required=False)),
               help="Выполняет команду в консоли и возвращает результат.",
               needs_shell=True, dangerous=True),

    ActionSpec("run_script", "Запустить сценарий", "shell",
               params=(Param("path", "Путь к сценарию", "path", "",
                             "Файл .sh или другой исполняемый сценарий."),
                       Param("args", "Аргументы", "str", "", "", required=False)),
               help="Запускает сценарий оболочки.",
               needs_shell=True, dangerous=True),

    # ================= УПРАВЛЕНИЕ =================
    ActionSpec("pause", "Пауза", "flow",
               params=(Param("seconds", "Секунд", "float", 1.0,
                             "Сколько ждать перед следующим шагом.",
                             minimum=0.05, maximum=3600, step=0.1, unit="с"),),
               help="Ждёт перед выполнением следующего действия."),

    ActionSpec("pause_ms", "Пауза в миллисекундах", "flow",
               params=(Param("milliseconds", "Миллисекунд", "int", 300,
                             "", minimum=10, maximum=60000, unit="мс"),),
               help="Короткая пауза."),

    ActionSpec("pause_random", "Случайная пауза", "flow",
               params=(Param("minimum", "От", "float", 0.5, "", minimum=0.05,
                             unit="с", required=False),
                       Param("maximum", "До", "float", 1.5, "", minimum=0.05,
                             unit="с", required=False)),
               help="Случайная задержка — делает поведение естественнее."),

    ActionSpec("repeat", "Повторить предыдущее действие", "flow",
               params=(Param("count", "Сколько раз", "int", 2, "",
                             minimum=1, maximum=100, required=False),),
               help="Повторяет предыдущий шаг команды."),

    ActionSpec("repeat_block", "Повторить группу действий", "flow",
               params=(Param("count", "Сколько раз", "int", 3, "",
                             minimum=1, maximum=100, required=False),
                       Param("start", "С какого шага", "int", 1,
                             "Номер шага в команде, с которого начать повтор.",
                             minimum=1, required=False)),
               help="Повторяет несколько шагов подряд."),

    ActionSpec("wait_window", "Ждать появления окна", "flow",
               params=(Param("title", "Название окна", "str", "", ""),
                       Param("timeout", "Ждать не дольше", "float", 10.0, "",
                             minimum=0.5, maximum=300, unit="с", required=False)),
               help="Ждёт, пока появится окно программы — "
                    "удобно после запуска тяжёлых программ."),

    ActionSpec("if_running", "Проверить, запущена ли программа", "flow",
               params=(Param("name", "Программа", "str", "", ""),
                       Param("then", "Если запущена", "choice", "skip", "",
                             choices=(("skip", "Пропустить эту команду"),
                                      ("continue", "Продолжить выполнение")),
                             required=False)),
               help="Условие: если программа уже работает, команда пропускается."),

    ActionSpec("set_variable", "Запомнить значение", "flow",
               params=(Param("name", "Имя переменной", "str", "my_var",
                             "Латинскими буквами, без пробелов.", required=False),
                       Param("value", "Значение", "text", "",
                             "Подставляется позже как {имя}.")),
               help="Сохраняет значение для использования в следующих шагах."),

    ActionSpec("await_voice", "Спросить и ждать ответ", "flow",
               params=(Param("question", "Вопрос", "str", "",
                             "Ассистент произнесёт вопрос и будет ждать голосовой ответ."),
                       Param("timeout", "Ждать не дольше", "float", 15.0, "",
                             minimum=1, maximum=300, unit="с", required=False),
                       Param("save_as", "Сохранить ответ как", "str", "answer",
                             "Ответ можно использовать как {answer}.", required=False)),
               help="Задаёт вопрос голосом и ждёт ответа — "
                    "для сценариев с выбором."),

    ActionSpec("confirm", "Спросить подтверждение", "flow",
               params=(Param("question", "Вопрос", "str", "Выполнить?",
                             "Ассистент спросит и будет ждать «да» или «нет»."),
                       Param("timeout", "Ждать не дольше", "float", 30.0, "",
                             minimum=1, maximum=300, unit="с", required=False)),
               help="Запрашивает подтверждение по ходу команды.",
               dangerous=False),

    ActionSpec("notify_history", "Записать в историю", "flow",
               params=(Param("text", "Заметка", "str", "", ""),),
               help="Добавляет запись в историю команд."),

    ActionSpec("stop_command", "Прервать выполнение", "flow",
               params=(Param("reason", "Причина", "str", "остановлено", "",
                             required=False),),
               help="Немедленно прекращает выполнение команды."),

    ActionSpec("run_command", "Выполнить другую команду", "flow",
               params=(Param("command", "Название или фраза команды", "str", "",
                             "Найдёт и выполнит команду из редактора."),),
               help="Вызывает другую команду из редактора — "
                    "позволяет собирать большие сценарии из готовых частей."),

    ActionSpec("try_block", "Попробовать и продолжить при ошибке", "flow",
               params=(Param("start", "С какого шага", "int", 1, "",
                             minimum=1, required=False),),
               help="Если следующие шаги не сработают, команда продолжится.",
               dangerous=False),
]


ACTIONS_BY_TYPE: dict[str, ActionSpec] = {spec.type: spec for spec in _ACTIONS}
ACTIONS_BY_GROUP: dict[str, list[ActionSpec]] = {}
for _spec in _ACTIONS:
    ACTIONS_BY_GROUP.setdefault(_spec.group, []).append(_spec)


def all_actions() -> tuple[ActionSpec, ...]:
    return tuple(_ACTIONS)


def get_spec(action_type: str) -> ActionSpec | None:
    return ACTIONS_BY_TYPE.get(action_type)


def group_actions(group: str) -> list[ActionSpec]:
    return list(ACTIONS_BY_GROUP.get(group, ()))


def groups() -> tuple[tuple[str, str, str], ...]:
    return GROUPS


def label_for(action_type: str) -> str:
    spec = ACTIONS_BY_TYPE.get(action_type)
    return spec.label if spec else action_type


def is_dangerous(action_type: str) -> bool:
    spec = ACTIONS_BY_TYPE.get(action_type)
    return bool(spec and spec.dangerous)


def needs_input(action_type: str) -> bool:
    spec = ACTIONS_BY_TYPE.get(action_type)
    return bool(spec and spec.needs_input)


def needs_shell(action_type: str) -> bool:
    spec = ACTIONS_BY_TYPE.get(action_type)
    return bool(spec and spec.needs_shell)


def needs_write(action_type: str) -> bool:
    spec = ACTIONS_BY_TYPE.get(action_type)
    return bool(spec and spec.needs_write)


def primary_param(action_type: str) -> Param | None:
    """Основной параметр действия — для компактного отображения."""
    spec = ACTIONS_BY_TYPE.get(action_type)
    if not spec or not spec.params:
        return None
    return spec.params[0]


# --- Подстановка переменных -------------------------------------------------

_VARIABLE_PATTERN = re.compile(r"\{([a-zA-Z_][\w]*(?::[^}]*)?)\}")


def substitute(text: str, context: dict[str, Any] | None = None,
               settings=None) -> str:
    """Заменить переменные вида {date} на их значения.

    Неизвестные переменные остаются как есть — лучше показать {имя},
    чем молча получить пустую строку.
    """
    import datetime
    import os
    import platform
    import random

    if not text or "{" not in text:
        return text

    context = dict(context or {})
    now = datetime.datetime.now()

    def current_volume() -> str:
        try:
            from luxvoice.sysint.audio import get_audio
            value = get_audio().volume()
            return str(value) if value >= 0 else "50"
        except Exception:  # noqa: BLE001
            return "50"

    def clipboard_text() -> str:
        try:
            from luxvoice.sysint.clipboard import get_text
            return get_text() or ""
        except Exception:  # noqa: BLE001
            return ""

    def selected_text() -> str:
        try:
            from luxvoice.sysint.clipboard import get_selected_text
            return get_selected_text() or ""
        except Exception:  # noqa: BLE001
            return ""

    def active_title() -> str:
        try:
            from luxvoice.sysint.windows import get_windows
            return get_windows().active_title() or ""
        except Exception:  # noqa: BLE001
            return ""

    builtins = {
        "volume": current_volume,
        "date": lambda: now.strftime("%d.%m.%Y"),
        "time": lambda: now.strftime("%H:%M:%S"),
        "user": lambda: os.environ.get("USER", "") or os.environ.get("LOGNAME", ""),
        "home": lambda: str(os.path.expanduser("~")),
        "hostname": lambda: platform.node(),
        "clipboard": clipboard_text,
        "selected": selected_text,
        "active_window": active_title,
        "phrase": lambda: str(context.get("phrase", "")),
        "counter": lambda: str(context.get("counter", 1)),
    }

    def replace(match: re.Match[str]) -> str:
        expression = match.group(1)
        name, _, argument = expression.partition(":")

        # Значение из контекста выполнения.
        if name in context:
            return str(context[name])

        # Пользовательская переменная, сохранённая действием.
        variables = context.get("variables")
        if isinstance(variables, dict) and name in variables:
            return str(variables[name])

        # Дата и время с форматом.
        if name == "date" and argument:
            return now.strftime(argument)
        if name == "time" and argument:
            return now.strftime(argument)

        # Случайное число: {random:1-10}.
        if name == "random" and argument:
            bounds = argument.split("-")
            if len(bounds) == 2:
                try:
                    low, high = int(bounds[0]), int(bounds[1])
                    if low > high:
                        low, high = high, low
                    return str(random.randint(low, high))
                except ValueError:
                    pass
            return match.group(0)

        factory = builtins.get(name)
        if factory is not None:
            try:
                return factory()
            except Exception:  # noqa: BLE001
                return match.group(0)

        return match.group(0)

    return _VARIABLE_PATTERN.sub(replace, text)


def has_variables(text: str) -> bool:
    return bool(text and _VARIABLE_PATTERN.search(text))


def list_variables(text: str) -> list[str]:
    if not text:
        return []
    return [match.group(1) for match in _VARIABLE_PATTERN.finditer(text)]