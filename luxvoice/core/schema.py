"""Декларативная схема настроек.

Каждая настройка описана один раз: тип, значение по умолчанию, подпись,
подсказка, допустимый диапазон, уровень важности. Из этой схемы строятся:
  * значения по умолчанию и валидация;
  * окно настроек целиком (вкладки, поля, подсказки);
  * миграции и проверка конфигурационного файла.

Добавить настройку = добавить одну запись сюда. Логику менять не нужно.

Уровни важности (level):
  basic     — видно сразу, обычные настройки
  advanced  — под общей кнопкой «Показать дополнительные»
  expert    — для тонкой отладки, спрятано глубже
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Типы значений ----------------------------------------------------------

BOOL = "bool"
INT = "int"
FLOAT = "float"
STR = "str"
TEXT = "text"          # многострочный текст
CHOICE = "choice"      # один вариант из списка
MULTI = "multi"        # несколько вариантов из списка
COLOR = "color"        # цвет в формате #rrggbb
PATH = "path"          # путь к файлу или каталогу
DIR = "dir"            # путь к каталогу
FILE = "file"          # путь к файлу
SECRET = "secret"      # ключ API: скрыт в интерфейсе
LIST = "list"          # список строк
KEYSEQ = "keyseq"      # сочетание клавиш


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    default: Any
    kind: str = STR
    section: str = "general"
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1.0
    unit: str = ""
    level: str = "basic"
    restart: bool = False       # требуется перезапуск
    dynamic: bool = False       # список вариантов берётся во время работы
    placeholder: str = ""
    depends_on: tuple[str, str] | None = None   # (ключ, значение) для показа поля
    provider_field: bool = False  # поле ввода ключа ИИ-провайдера

    @property
    def hidden_by_level(self) -> str:
        return self.level


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    icon: str = ""
    description: str = ""
    order: int = 0


# --- Разделы настроек -------------------------------------------------------

SECTIONS: tuple[Section, ...] = (
    Section("general", "Общие", "settings", "Поведение ассистента при запуске", 10),
    Section("voice_input", "Голосовой ввод", "mic", "Микрофон, распознавание, обращение", 20),
    Section("execution", "Выполнение команд", "play", "Сопоставление фраз, подтверждения, связки", 30),
    Section("speech_output", "Озвучка", "speaker", "Голоса, громкость, характер ответов", 40),
    Section("ai", "ИИ-провайдер", "cpu", "Нейросеть для вопросов и умных действий", 50),
    Section("ai_chat", "Нейросеть", "chat", "Режим работы чата и его возможности", 55),
    Section("interface", "Интерфейс", "window", "Язык, тема, окно, трей", 60),
    Section("search", "Поиск файлов", "folder", "Где искать программы и документы", 70),
    Section("actions", "Действия и безопасность", "shield", "Разрешения на опасные операции", 80),
    Section("plugins", "Дополнения", "plug", "Telegram, аватар, внешние модули", 90),
    Section("performance", "Производительность", "gauge", "Потоки, кэш, энергосбережение", 100),
    Section("privacy", "Приватность", "lock", "История, логи, телеметрия", 110),
    Section("updates", "Обновления", "refresh", "Проверка новых версий", 120),
    Section("advanced", "Дополнительно", "tools", "Резервные копии, отладка, портативный режим", 130),
)


# --- Настройки --------------------------------------------------------------

_SETTINGS: list[Setting] = [

    # ================= ОБЩИЕ =================
    Setting("app.autostart", "Запускать при входе в систему", False, BOOL, "general",
            "Добавляет ассистента в автозагрузку KDE.", level="basic"),
    Setting("app.start_minimized", "Запускать свёрнутым в трей", False, BOOL, "general",
            "Программа начнёт работать молча, окно не откроется."),
    Setting("app.start_listening", "Сразу включать микрофон", True, BOOL, "general",
            "Ассистент начинает слушать сразу после запуска."),
    Setting("app.single_instance", "Одна копия программы", True, BOOL, "general",
            "Повторный запуск разворачивает уже работающее окно.", level="advanced"),
    Setting("app.close_to_tray", "Кнопка закрытия прячет в трей", True, BOOL, "general",
            "Выключить: закрытие окна завершает программу."),
    Setting("app.confirm_exit", "Спрашивать при выходе", False, BOOL, "general",
            "Подтверждение перед завершением работы."),
    Setting("app.reply_unknown", "Отвечать, если команда не найдена", True, BOOL, "general",
            "Короткая фраза голосом, когда фраза не распознана."),
    Setting("app.unknown_phrase", "Фраза «не понял»", "Команда не найдена", STR, "general",
            "Что произносить, если команда не найдена.", level="advanced"),
    Setting("app.silent_start", "Тихий старт", False, BOOL, "general",
            "Не здороваться голосом при запуске."),
    Setting("app.greeting", "Приветствие при старте", "Ассистент готов к работе", STR, "general",
            "Фраза при запуске. Пусто — не здороваться.", level="advanced"),
    Setting("app.pin_window", "Окно поверх других", False, BOOL, "general",
            "Главное окно всегда сверху."),
    Setting("app.remember_window", "Запоминать размер и положение окна", True, BOOL, "general",
            None, level="advanced"),

    # ================= ГОЛОСОВОЙ ВВОД =================
    Setting("stt.engine", "Движок распознавания", "vosk", CHOICE, "voice_input",
            "Vosk — быстрый и лёгкий, идеален для команд. Whisper — точнее на свободной речи, "
            "тяжелее. Облако — нужен ключ и интернет.",
            choices=(("vosk", "Vosk (офлайн, команды)"),
                     ("whisper", "Whisper (офлайн, точнее)"),
                     ("cloud", "Облако (по ключу ИИ)"))),
    Setting("stt.assistant_engine", "Движок для длинной речи", "whisper", CHOICE, "voice_input",
            "Используется для диктовки и запросов к нейросети, когда основной движок — Vosk.",
            choices=(("whisper", "Whisper"), ("vosk", "Vosk"), ("cloud", "Облако")),
            level="advanced"),
    Setting("stt.language", "Язык распознавания речи", "ru", CHOICE, "voice_input",
            "Автоопределение даёт лучший результат, если вы говорите на двух языках.",
            choices=(("ru", "Русский"), ("en", "English"), ("auto", "Авто"))),
    Setting("stt.mic_device", "Модель микрофона", "", CHOICE, "voice_input",
            "Какое устройство слушать. Список подставляется из PipeWire.",
            dynamic=True, level="basic"),
    Setting("stt.auto_mic_on_launch", "Автовключение микрофона", True, BOOL, "voice_input",
            "Дублирует общую настройку запуска — удобно настраивать в одном месте."),
    Setting("stt.sensitivity", "Чувствительность распознавания", 50, INT, "voice_input",
            "Меньше — реже реагирует на посторонние звуки. Больше — слышит тихую речь.",
            minimum=0, maximum=100, unit="%"),
    Setting("stt.prefix_sensitivity", "Чувствительность префикса", 55, INT, "voice_input",
            "Порог срабатывания обращения. Ниже — ловит обращение издалека, но чаще ошибается.",
            minimum=0, maximum=100, unit="%"),
    Setting("stt.prefix_mode", "Режим префикса", False, BOOL, "voice_input",
            "Включён: команды выполняются только после обращения. Выключен: обращение не обязательно."),
    Setting("stt.prefix", "Обращение", "джарвис", STR, "voice_input",
            "Слово-обращение. Можно несколько вариантов через запятую: «джарвис, ассистент»."),
    Setting("stt.prefix_aliases", "Похожие обращения", "джарвис,jarvis,жарвис,джарвин,чарвис",
            STR, "voice_input",
            "Варианты, которые распознавание часто выдаёт вместо обращения.", level="advanced"),
    Setting("stt.wake_sound", "Звук отклика на обращение", "soft", CHOICE, "voice_input",
            "Короткий сигнал, подтверждающий, что ассистент услышал обращение.",
            choices=(("off", "Без звука"), ("soft", "Мягкий"), ("beep", "Короткий сигнал")),
            level="basic"),
    Setting("stt.wake_sound_volume", "Громкость сигнала отклика", 60, INT, "voice_input",
            None, minimum=0, maximum=100, unit="%", level="advanced"),
    Setting("stt.live_partial", "Показывать текст по мере распознавания", True, BOOL, "voice_input",
            "Промежуточный текст видно в окне до завершения фразы."),
    Setting("stt.vad_enabled", "Определять речь в шуме (VAD)", True, BOOL, "voice_input",
            "Отсекает фоновый шум и музыку, снижая ложные срабатывания."),
    Setting("stt.vad_aggressiveness", "Агрессивность фильтра шума", 2, INT, "voice_input",
            "0 — мягко, 3 — отсекает почти всё, включая тихую речь.",
            minimum=0, maximum=3, level="advanced"),
    Setting("stt.silence_timeout", "Пауза в конце фразы", 0.9, FLOAT, "voice_input",
            "Сколько тишины ждать, считая фразу законченной.",
            minimum=0.3, maximum=5.0, step=0.1, unit="с", level="advanced"),
    Setting("stt.max_phrase_seconds", "Максимальная длина фразы", 15.0, FLOAT, "voice_input",
            "Предохранитель от бесконечной записи.",
            minimum=3.0, maximum=120.0, step=1.0, unit="с", level="advanced"),
    Setting("stt.noise_gate", "Порог тишины микрофона", -45, INT, "voice_input",
            "Уровень в дБ, ниже которого звук считается тишиной.",
            minimum=-80, maximum=-10, unit="дБ", level="expert"),
    Setting("stt.input_gain", "Усиление входа", 1.0, FLOAT, "voice_input",
            "Умножает сигнал микрофона. Поднимайте, если ассистент плохо слышит.",
            minimum=0.1, maximum=8.0, step=0.1, unit="×", level="expert"),
    Setting("stt.num_threads", "Потоков распознавания", 0, INT, "voice_input",
            "0 — подобрать автоматически по числу ядер.",
            minimum=0, maximum=16, level="expert"),
    Setting("stt.vosk_model_path", "Путь к модели Vosk", "", DIR, "voice_input",
            "Пусто — используется модель из каталога данных программы.", level="advanced"),
    Setting("stt.whisper_model", "Модель Whisper", "small", CHOICE, "voice_input",
            "Больше — точнее и медленнее. tiny/base — для слабых машин, "
            "small — баланс, medium/large — максимальная точность.",
            choices=(("tiny", "tiny (самая быстрая)"), ("base", "base"),
                     ("small", "small (рекомендуется)"), ("medium", "medium"),
                     ("large-v3", "large-v3 (самая точная)")),
            level="basic"),
    Setting("stt.whisper_device", "Устройство для Whisper", "auto", CHOICE, "voice_input",
            "CUDA задействует видеокарту NVIDIA — значительно быстрее.",
            choices=(("auto", "Авто"), ("cpu", "Процессор"), ("cuda", "Видеокарта NVIDIA")),
            level="advanced"),
    Setting("stt.whisper_compute", "Точность вычислений Whisper", "int8", CHOICE, "voice_input",
            "int8 — быстрее и легче, float16 — точнее, но требует больше памяти.",
            choices=(("int8", "int8 (быстро)"), ("float16", "float16 (точно)"),
                     ("float32", "float32 (максимум)")),
            level="expert"),
    Setting("stt.whisper_preload", "Загружать Whisper заранее", False, BOOL, "voice_input",
            "Старт медленнее, зато первая фраза распознаётся мгновенно.", level="advanced"),
    Setting("stt.cloud_provider", "Облачный движок", "openai", CHOICE, "voice_input",
            "Какой сервис использовать для распознавания в облаке.",
            choices=(("openai", "OpenAI (Whisper API)"), ("gemini", "Google Gemini"),
                     ("deepseek", "DeepSeek"), ("custom", "Свой OpenAI-совместимый сервер")),
            depends_on=("stt.engine", "cloud"), level="advanced"),

    # ================= ВЫПОЛНЕНИЕ КОМАНД =================
    Setting("match.threshold", "Порог совпадения фраз", 72, INT, "execution",
            "Насколько похожей должна быть фраза. Ниже — понимает неточную речь, но путает команды.",
            minimum=40, maximum=100, unit="%"),
    Setting("match.prefix_threshold", "Порог совпадения с обращением", 70, INT, "execution",
            None, minimum=40, maximum=100, unit="%", level="advanced"),
    Setting("match.fuzzy", "Понимать неточную речь", True, BOOL, "execution",
            "Прощает ошибки распознавания и окончания слов."),
    Setting("match.fuzzy_distance", "Терпимость к ошибкам", 2, INT, "execution",
            "Сколько символов можно перепутать в слове.",
            minimum=0, maximum=5, level="advanced"),
    Setting("match.normalize_numbers", "Понимать числа словами", True, BOOL, "execution",
            "«громкость пятьдесят» и «громкость 50» — одно и то же."),
    Setting("match.window_context", "Учитывать активное окно", True, BOOL, "execution",
            "Короткие фразы вроде «пауза» срабатывают, когда открыто окно нужного сервиса."),
    Setting("match.context_bonus", "Преимущество командам активного окна", 25, INT, "execution",
            "Насколько поднять оценку команды, подходящей текущему окну.",
            minimum=0, maximum=100, unit="%", level="advanced"),
    Setting("match.optional_words", "Игнорировать служебные слова", True, BOOL, "execution",
            "«пожалуйста», «давай», «быстро» можно добавлять к любой команде.",
            level="basic"),
    Setting("match.extra_filler", "Свои служебные слова", "пожалуйста,давай,быстро,ну,ок",
            STR, "execution",
            "Через запятую. Эти слова не мешают поиску команды.", level="advanced"),
    Setting("match.command_timeout", "Максимальное время команды", 120, INT, "execution",
            "Предохранитель: дольше этого срока выполнение прерывается.",
            minimum=5, maximum=3600, unit="с", level="advanced"),
    Setting("match.max_actions", "Ограничение действий в команде", 200, INT, "execution",
            "Защита от случайного зацикливания.",
            minimum=1, maximum=5000, level="expert"),
    Setting("confirm.enabled", "Подтверждение опасных команд", True, BOOL, "execution",
            "Перед выключением, перезагрузкой и другими опасными действиями "
            "ассистент спросит голосом."),
    Setting("confirm.words_yes", "Слова подтверждения", "да,правильно,подтверждаю,верно,ага",
            STR, "execution",
            "Через запятую. Слова, которыми можно подтвердить выполнение."),
    Setting("confirm.words_no", "Слова отказа", "нет,отмена,стоп,не надо,отменить",
            STR, "execution",
            "Через запятую. Слова, которыми можно отменить команду."),
    Setting("confirm.timeout", "Ожидание ответа", 30, INT, "execution",
            "Сколько секунд ждать подтверждения. По истечении команда отменяется.",
            minimum=5, maximum=300, unit="с", level="advanced"),
    Setting("confirm.voice_reply", "Подтверждать голосом", True, BOOL, "execution",
            "Ответ на подтверждение можно произнести, а не искать окно.", level="advanced"),
    Setting("chain.enabled", "Связка команд", True, BOOL, "execution",
            "Несколько команд одной фразой: «открой ютуб и сделай громче»."),
    Setting("chain.words", "Слова-связки", "и,затем,потом,после этого,а также", STR, "execution",
            "Через запятую. По этим словам фраза делится на отдельные команды."),
    Setting("chain.max_commands", "Максимум команд в связке", 5, INT, "execution",
            None, minimum=2, maximum=20, level="advanced"),
    Setting("chain.stop_on_error", "Прерывать связку при ошибке", False, BOOL, "execution",
            "Выключено: остальные команды выполнятся даже при сбое одной.", level="advanced"),
    Setting("exec.speak_result", "Озвучивать результат команды", True, BOOL, "execution",
            "Короткое подтверждение голосом после выполнения."),
    Setting("exec.voice_feedback", "Голосовая обратная связь", True, BOOL, "execution",
            "Ассистент произносит слова подтверждения и ошибки."),
    Setting("exec.hold_mic_while_speaking", "Не слушать себя во время ответа", True, BOOL, "execution",
            "Микрофон приостанавливается, пока ассистент говорит — исключает самозацикливание.",
            level="advanced"),
    Setting("exec.block_overlap", "Не запускать команду поверх другой", True, BOOL, "execution",
            "Новая команда ждёт завершения текущей.", level="advanced"),
    Setting("exec.hotkey_listen", "Клавиша включения микрофона", "Meta+Shift+V", KEYSEQ, "execution",
            "Сочетание, включающее и выключающее прослушивание."),
    Setting("exec.hotkey_panic", "Клавиша мгновенной остановки", "Meta+Shift+X", KEYSEQ, "execution",
            "Немедленно прекращает прослушивание и выполнение команд."),
    Setting("exec.mode_phrases", "Фразы переключения режимов", "перейди в режим префикса,"
            "перейди в тихий режим,хватит слушать,продолжай слушать,выключи микрофон,включи микрофон",
            TEXT, "execution",
            "Голосовые фразы управления самим ассистентом. Одна фраза в строке.",
            level="advanced"),
    Setting("exec.history_size", "Сколько команд хранить в истории", 2000, INT, "execution",
            None, minimum=0, maximum=100000, unit="записей", level="advanced"),

    # ================= ОЗВУЧКА =================
    Setting("tts.enabled", "Голосовые ответы", True, BOOL, "speech_output",
            "Ассистент отвечает вслух. Выключите, если нужен только текстовый режим."),
    Setting("tts.engine", "Движок синтеза", "auto", CHOICE, "speech_output",
            "Piper — самые естественные офлайн-голоса. RHVoice — хороший русский, "
            "есть в репозитории. eSpeak — всегда доступен, звучит роботизированно.",
            choices=(("auto", "Автоматически (лучший из доступных)"),
                     ("piper", "Piper"), ("rhvoice", "RHVoice"),
                     ("espeak", "eSpeak NG"), ("cloud", "Облако (по ключу)"))),
    Setting("tts.voice", "Голос", "", CHOICE, "speech_output",
            "Список голосов зависит от выбранного движка.", dynamic=True),
    Setting("tts.volume", "Громкость ассистента", 80, INT, "speech_output",
            "Отдельная громкость — не влияет на музыку и системные звуки.",
            minimum=0, maximum=100, unit="%"),
    Setting("tts.rate", "Скорость речи", 0, INT, "speech_output",
            "Сдвиг от естественной скорости голоса: отрицательное — медленнее, "
            "положительное — быстрее.", minimum=-50, maximum=100, unit="%"),
    Setting("tts.pitch", "Высота голоса", 0, INT, "speech_output",
            "Сдвиг тона: выше или ниже.", minimum=-50, maximum=50, unit="%"),
    Setting("tts.personality", "Характер ответов", "butler", CHOICE, "speech_output",
            "Готовые наборы фраз. Ваши правки в «Фразах» имеют приоритет.",
            choices=(("butler", "Дворецкий (сдержанный)"),
                     ("friendly", "Дружелюбный"),
                     ("terse", "Немногословный"),
                     ("gamer", "Игровой"),
                     ("calm", "Спокойный"),
                     ("none", "Без характера"))),
    Setting("tts.custom_phrases", "Свои фразы озвучки", "", TEXT, "speech_output",
            "Формат: ключ=фраза, по одной в строке. Например: "
            "ok=Слушаюсь, mylord.", level="advanced"),
    Setting("tts.on_activate", "Отвечать при обращении", True, BOOL, "speech_output",
            "Ассистент голосом подтверждает, что услышал обращение."),
    Setting("tts.on_activate_phrase", "Фраза при обращении", "Слушаю", STR, "speech_output",
            None, level="advanced"),
    Setting("tts.on_success", "Отвечать после успеха", True, BOOL, "speech_output",
            "Например: «Готово»."),
    Setting("tts.on_success_phrase", "Фраза успеха", "Готово", STR, "speech_output",
            None, level="advanced"),
    Setting("tts.on_error", "Отвечать при ошибке", True, BOOL, "speech_output",
            "Например: «Не удалось»."),
    Setting("tts.on_error_phrase", "Фраза ошибки", "Не удалось", STR, "speech_output",
            None, level="advanced"),
    Setting("tts.on_confirm", "Отвечать перед подтверждением", True, BOOL, "speech_output",
            "Ассистент повторяет, что собирается сделать, и ждёт ответа.", level="advanced"),
    Setting("tts.on_start", "Приветствие при запуске", True, BOOL, "speech_output",
            "Голосом при старте программы."),
    Setting("tts.dictation_voice", "Голос для диктовки текста", "", CHOICE, "speech_output",
            "Каким голосом читать вслух большие тексты. Пусто — основной голос.",
            dynamic=True, level="advanced"),
    Setting("tts.max_length", "Максимальная длина озвучки", 400, INT, "speech_output",
            "Длинные ответы обрезаются, чтобы не читать вслух целую статью.",
            minimum=20, maximum=5000, unit="симв.", level="advanced"),
    Setting("tts.queue_max", "Ограничение очереди фраз", 3, INT, "speech_output",
            "Сколько фраз может ждать произнесения.",
            minimum=1, maximum=20, level="advanced"),
    Setting("tts.interrupt", "Прерывать ответ новой командой", True, BOOL, "speech_output",
            "Сказали «стоп» или новую команду — ассистент замолкает.", level="advanced"),
    Setting("tts.streaming", "Произносить по мере поступления текста", True, BOOL, "speech_output",
            "Ответ нейросети озвучивается, не дожидаясь конца.", level="advanced"),
    Setting("tts.cloud_provider", "Облачный голос", "openai", CHOICE, "speech_output",
            "Сервис для облачной озвучки, когда выбран облачный движок.",
            choices=(("openai", "OpenAI"), ("elevenlabs", "ElevenLabs"),
                     ("custom", "Свой OpenAI-совместимый сервер")),
            depends_on=("tts.engine", "cloud"), level="advanced"),
    Setting("tts.cloud_voice", "Название облачного голоса", "alloy", STR, "speech_output",
            "Например: alloy, nova, shimmer.", depends_on=("tts.engine", "cloud"),
            level="advanced"),
    Setting("tts.cache_enabled", "Кэшировать озвучку", True, BOOL, "speech_output",
            "Часто повторяемые фразы произносятся мгновенно.", level="advanced"),

    # ================= ИИ-ПРОВАЙДЕР =================
    Setting("ai.enabled", "Включить нейросеть", False, BOOL, "ai",
            "Голосовой и текстовый чат, умные ответы, выполнение команд по смыслу."),
    Setting("ai.provider", "Провайдер", "openai", CHOICE, "ai",
            "Ключ вводится для выбранного сервиса. Локальные модели работают без интернета.",
            choices=(
                ("openai", "OpenAI"), ("anthropic", "Anthropic (Claude)"),
                ("gemini", "Google Gemini (есть бесплатный лимит)"),
                ("grok", "xAI (Grok)"), ("deepseek", "DeepSeek"),
                ("yandexgpt", "YandexGPT"), ("gigachat", "GigaChat (есть бесплатный лимит)"),
                ("openrouter", "OpenRouter"), ("proxyapi", "ProxyAPI (₽, без VPN)"),
                ("aitunnel", "AITunnel (₽, без VPN)"),
                ("ollama", "Ollama (локально, бесплатно)"),
                ("lmstudio", "LM Studio (локально, бесплатно)"),
                ("custom", "Свой OpenAI-совместимый сервер"),
            )),
    Setting("ai.api_key", "Ключ API", "", SECRET, "ai",
            "Хранится только на этом компьютере, файл доступен лишь вашей учётной записи.",
            provider_field=True),
    Setting("ai.model", "Модель", "", STR, "ai",
            "Имя модели у провайдера. Пусто — модель по умолчанию для сервиса.",
            placeholder="например: gpt-4o-mini"),
    Setting("ai.base_url", "Адрес сервера", "", STR, "ai",
            "Для Ollama, LM Studio и своего сервера. Пусто — адрес по умолчанию.",
            placeholder="http://localhost:11434/v1",
            depends_on=("ai.provider", "custom")),
    Setting("ai.temperature", "Творческость ответов", 0.3, FLOAT, "ai",
            "Меньше — точнее и предсказуемее, больше — свободнее формулировки.",
            minimum=0.0, maximum=2.0, step=0.1),
    Setting("ai.max_tokens", "Максимум длины ответа", 1024, INT, "ai",
            None, minimum=64, maximum=32768, unit="токенов", level="advanced"),
    Setting("ai.timeout", "Таймаут запроса", 60, INT, "ai",
            None, minimum=5, maximum=600, unit="с", level="advanced"),
    Setting("ai.system_prompt", "Системная подсказка", "", TEXT, "ai",
            "Характер и правила нейросети. Пусто — готовая подсказка ассистента.",
            level="advanced"),
    Setting("ai.keys", "Сохранённые ключи сервисов", {}, "dict", "ai",
            "Внутреннее хранилище: ключ для каждого провайдера.", level="expert"),
    Setting("ai.retries", "Повторы при сбое запроса", 2, INT, "ai",
            None, minimum=0, maximum=10, level="expert"),
    Setting("ai.fallback_provider", "Резервный провайдер", "", CHOICE, "ai",
            "Используется, если основной сервис недоступен.",
            choices=(("", "Не использовать"), ("openai", "OpenAI"), ("anthropic", "Anthropic"),
                     ("gemini", "Google Gemini"), ("deepseek", "DeepSeek"),
                     ("openrouter", "OpenRouter"), ("ollama", "Ollama"),
                     ("lmstudio", "LM Studio")),
            level="advanced"),

    # ================= НЕЙРОСЕТЬ (чат) =================
    Setting("aichat.mode", "Режим работы", "combined", CHOICE, "ai_chat",
            "Комбинированный: отвечает на вопросы и может выполнять команды на компьютере.",
            choices=(("ai", "Только ИИ"), ("pc", "Только управление ПК"),
                     ("combined", "Комбинированный"))),
    Setting("aichat.voice_activation", "Активация голосом по обращению", True, BOOL, "ai_chat",
            "«Джарвис, расскажи про Марс» уходит в нейросеть, "
            "«Джарвис, открой Telegram» выполняется как команда."),
    Setting("aichat.route_unknown", "Маршрутизировать неизвестные фразы через ИИ", True, BOOL, "ai_chat",
            "Если фраза не похожа на команду, локальная нейросеть попытается понять запрос "
            "или выполнить команду по смыслу.", level="advanced"),
    Setting("aichat.continue_without_prefix", "Продолжать разговор без обращения", True,
            BOOL, "ai_chat",
            "Несколько секунд после ответа можно говорить без обращения."),
    Setting("aichat.continue_window", "Окно продолжения разговора", 8, INT, "ai_chat",
            None, minimum=1, maximum=120, unit="с", level="advanced"),
    Setting("aichat.web_search", "Разрешить поиск в интернете", True, BOOL, "ai_chat",
            "Свежие данные со ссылками на источники."),
    Setting("aichat.search_provider", "Поисковик", "duckduckgo", CHOICE, "ai_chat",
            "Чем искать в интернете.", level="advanced",
            choices=(("duckduckgo", "DuckDuckGo (без ключа)"),
                     ("searxng", "SearXNG"), ("brave", "Brave Search"),
                     ("tavily", "Tavily"), ("yandex", "Яндекс"),
                     ("google", "Google (нужен ключ)"), ("none", "Не использовать"))),
    Setting("aichat.search_api_key", "Ключ поисковика", "", SECRET, "ai_chat",
            "Нужен не для всех поисковиков — DuckDuckGo работает без ключа.",
            level="advanced"),
    Setting("aichat.max_search_results", "Сколько источников читать", 5, INT, "ai_chat",
            None, minimum=1, maximum=20, level="advanced"),
    Setting("aichat.images", "Создавать изображения", True, BOOL, "ai_chat",
            "Нарисовать картинку по описанию словами."),
    Setting("aichat.image_provider", "Художник", "openai", CHOICE, "ai_chat",
            "Какой сервис рисует изображения.", level="advanced",
            choices=(("openai", "OpenAI"), ("gemini", "Google Gemini"),
                     ("grok", "xAI Grok"), ("comfyui", "ComfyUI (локально)"),
                     ("custom", "Свой сервер"))),
    Setting("aichat.image_size", "Размер изображения", "1024x1024", STR, "ai_chat",
            "Формат ШИРИНАxВЫСОТА.", level="advanced"),
    Setting("aichat.save_images", "Куда сохранять картинки", "", DIR, "ai_chat",
            "Пусто — в подпапку «Изображения» каталога загрузок.", level="advanced"),
    Setting("aichat.files", "Создавать файлы", True, BOOL, "ai_chat",
            "Excel, Word, CSV, JSON и текстовые файлы одной просьбой."),
    Setting("aichat.files_dir", "Куда сохранять файлы", "", DIR, "ai_chat",
            "Пусто — в каталог документов.", level="advanced"),
    Setting("aichat.screenshot", "Уметь делать снимок экрана", True, BOOL, "ai_chat",
            "Ассистент может посмотреть на экран и ответить, что на нём."),
    Setting("aichat.screen_read", "Читать содержимое экрана", False, BOOL, "ai_chat",
            "Распознавание текста на снимке экрана. Включено: ассистент видит, что написано в окнах."),
    Setting("aichat.execute_commands", "Выполнять команды по смыслу", True, BOOL, "ai_chat",
            "Нейросеть может предложить и запустить подходящую команду из редактора."),
    Setting("aichat.confirm_ai_actions", "Подтверждать действия от нейросети", True, BOOL, "ai_chat",
            "Опасные операции из чата требуют подтверждения."),
    Setting("aichat.history_limit", "Помнить сообщений", 20, INT, "ai_chat",
            "Сколько предыдущих сообщений учитывать в разговоре.",
            minimum=0, maximum=200, level="advanced"),
    Setting("aichat.memory_enabled", "Помнить факты о вас", False, BOOL, "ai_chat",
            "Ассистент сохраняет полезные сведения между сессиями.", level="advanced"),
    Setting("aichat.memory_file", "Файл памяти", "", FILE, "ai_chat",
            "Пусто — стандартный файл в каталоге данных.", level="advanced"),
    Setting("aichat.stream", "Печатать ответ по мере готовности", True, BOOL, "ai_chat",
            "Ответ появляется постепенно, как в чате.", level="advanced"),
    Setting("aichat.speak_stream", "Озвучивать ответ по частям", True, BOOL, "ai_chat",
            "Ассистент начинает говорить, не дожидаясь конца ответа.", level="advanced"),
    Setting("aichat.persona", "Роль ассистента", "Умный и краткий голосовой помощник, "
            "который управляет этим компьютером", TEXT, "ai_chat",
            "Описание роли для нейросети.", level="advanced"),
    Setting("aichat.locale", "Язык ответов", "auto", CHOICE, "ai_chat",
            "На каком языке отвечать.", level="advanced",
            choices=(("auto", "Как спрашивают"), ("ru", "Русский"), ("en", "English"))),

    # ================= ИНТЕРФЕЙС =================
    Setting("ui.language", "Язык интерфейса", "ru", CHOICE, "interface",
            "Русский или английский.", choices=(("ru", "Русский"), ("en", "English"))),
    Setting("ui.theme", "Тема", "dark", CHOICE, "interface",
            "Цветовая схема окна.",
            choices=(("dark", "Тёмная"), ("light", "Светлая"), ("system", "Как в системе"))),
    Setting("ui.accent", "Цвет акцента", "#4f8cff", COLOR, "interface",
            "Основной цвет кнопок, выделений и аватара."),
    Setting("ui.background", "Свой цвет фона", "", COLOR, "interface",
            "Пусто — цвет темы.", level="advanced"),
    Setting("ui.font_size", "Размер шрифта", 100, INT, "interface",
            "Масштаб текста в процентах.", minimum=75, maximum=175, unit="%"),
    Setting("ui.tray", "Значок в области уведомлений", True, BOOL, "interface",
            "Быстрый доступ к микрофону, режимам и выходу."),
    Setting("ui.tray_notifications", "Уведомления системы", True, BOOL, "interface",
            "Показывать всплывающие сообщения о выполненных командах.", level="advanced"),
    Setting("ui.notify_on_command", "Уведомлять о выполнении", False, BOOL, "interface",
            "Всплывающее сообщение при каждой выполненной команде.", level="advanced"),
    Setting("ui.show_avatar", "Показывать аватар", False, BOOL, "interface",
            "Анимированный круг на экране, реагирующий на голос."),
    Setting("ui.avatar_size", "Размер аватара", 220, INT, "interface",
            None, minimum=80, maximum=800, unit="пикселей",
            depends_on=("ui.show_avatar", "true")),
    Setting("ui.avatar_position", "Положение аватара", "bottom-right", CHOICE, "interface",
            "Где держать аватар на экране.",
            choices=(("top-left", "Сверху слева"), ("top-right", "Сверху справа"),
                     ("bottom-left", "Снизу слева"), ("bottom-right", "Снизу справа"),
                     ("center", "По центру")),
            depends_on=("ui.show_avatar", "true"), level="advanced"),
    Setting("ui.avatar_opacity", "Прозрачность аватара", 85, INT, "interface",
            None, minimum=20, maximum=100, unit="%",
            depends_on=("ui.show_avatar", "true"), level="advanced"),
    Setting("ui.avatar_react_mic", "Аватар реагирует на микрофон", True, BOOL, "interface",
            None, depends_on=("ui.show_avatar", "true"), level="advanced"),
    Setting("ui.avatar_react_voice", "Аватар реагирует на голос ассистента", True, BOOL,
            "interface", None, depends_on=("ui.show_avatar", "true"), level="advanced"),
    Setting("ui.avatar_click_through", "Аватар не мешает кликам", True, BOOL, "interface",
            None, depends_on=("ui.show_avatar", "true"), level="advanced"),
    Setting("ui.history_visible", "Показывать историю в главном окне", True, BOOL, "interface",
            "Список распознанных фраз и выполненных команд на главном экране."),
    Setting("ui.history_rows", "Строк истории на экране", 50, INT, "interface",
            None, minimum=5, maximum=1000, level="advanced"),
    Setting("ui.show_level", "Показывать уровень микрофона", True, BOOL, "interface",
            "Индикатор громкости входящего звука.", level="advanced"),
    Setting("ui.compact", "Компактный режим", False, BOOL, "interface",
            "Уменьшенные отступы — помещается больше информации.", level="advanced"),
    Setting("ui.confirm_delete", "Спрашивать при удалении", True, BOOL, "interface",
            "Подтверждение перед удалением команд и паков.", level="advanced"),
    Setting("ui.autosave", "Автосохранение редактора", True, BOOL, "interface",
            "Изменения записываются сразу, без кнопки «Сохранить»."),
    Setting("ui.grid_density", "Плотность списка команд", "normal", CHOICE, "interface",
            None, level="advanced",
            choices=(("compact", "Плотно"), ("normal", "Обычно"), ("roomy", "Просторно"))),
    Setting("ui.animations", "Анимации интерфейса", True, BOOL, "interface",
            "Отключите, если важна максимальная отзывчивость.", level="advanced"),
    Setting("ui.remember_tab", "Открывать последнюю вкладку", True, BOOL, "interface",
            None, level="advanced"),

    # ================= ПОИСК ФАЙЛОВ =================
    Setting("files.search_dirs", "Где искать программы и файлы",
            "~/bin:~/.local/bin:/usr/bin:/usr/local/bin:/opt:"
            "~/.local/share/applications:/usr/share/applications",
            LIST, "search",
            "Каталоги для поиска по фразам «открой …» и «запусти …». Через двоеточие."),
    Setting("files.user_dirs", "Личные папки для поиска", "~/Загрузки:~/Документы:~",
            LIST, "search",
            "Где искать документы и файлы, названные голосом."),
    Setting("files.max_depth", "Глубина поиска", 4, INT, "search",
            None, minimum=1, maximum=12, level="advanced"),
    Setting("files.index_refresh_minutes", "Обновлять индекс программ", 30, INT, "search",
            "Как часто пересканировать установленные программы.",
            minimum=0, maximum=1440, unit="мин", level="advanced"),
    Setting("files.use_desktop_entries", "Искать среди ярлыков программ", True, BOOL, "search",
            "Находит программы по названию ярлыка, а не только по имени файла.",
            level="advanced"),
    Setting("files.search_documents", "Искать среди документов", True, BOOL, "search",
            None, level="advanced"),
    Setting("files.fuzzy_files", "Прощать неточные названия файлов", True, BOOL, "search",
            None, level="advanced"),
    Setting("files.max_results", "Сколько вариантов предлагать", 8, INT, "search",
            None, minimum=1, maximum=50, level="advanced"),
    Setting("files.browser", "Браузер по умолчанию", "", STR, "search",
            "Команда или путь к браузеру. Пусто — системный по умолчанию."),
    Setting("files.search_template", "Поисковик для фразы «найти в интернете»",
            "https://duckduckgo.com/?q={query}", STR, "search",
            "{query} заменяется на поисковый запрос.", level="advanced"),
    Setting("files.open_with", "Чем открывать файлы", "xdg-open", STR, "search",
            None, level="advanced"),
    Setting("files.trash_instead_delete", "Удалять в корзину", True, BOOL, "search",
            "Файлы перемещаются в корзину, а не удаляются навсегда.", level="advanced"),

    # ================= ДЕЙСТВИЯ И БЕЗОПАСНОСТЬ =================
    Setting("safety.allow_shell", "Разрешить команды консоли", True, BOOL, "actions",
            "Действия «Выполнить команду CMD» и «PowerShell» могут менять систему. "
            "Выключите, если не пользуетесь."),
    Setting("safety.allow_powershell", "Разрешить PowerShell", True, BOOL, "actions",
            "Отдельное разрешение для сценариев PowerShell.", level="advanced"),
    Setting("safety.allow_file_write", "Разрешить запись файлов", True, BOOL, "actions",
            "Команды, создающие файлы и папки."),
    Setting("safety.allow_scripts", "Разрешить запуск скриптов", False, BOOL, "actions",
            "Запуск .sh и других сценариев, записанных командами. По умолчанию выключено."),
    Setting("safety.allow_shutdown", "Разрешить выключение и перезагрузку", True, BOOL, "actions",
            "Действия управления питанием."),
    Setting("safety.allow_mouse", "Разрешить управление мышью", True, BOOL, "actions",
            "Перемещение курсора, клики, прокрутка."),
    Setting("safety.allow_keyboard", "Разрешить нажатия клавиш", True, BOOL, "actions",
            "Действия с клавиатурой и вводом текста.", level="advanced"),
    Setting("safety.allow_close_apps", "Разрешить закрывать программы", True, BOOL, "actions",
            "Команды закрытия окон и приложений.", level="advanced"),
    Setting("safety.confirm_always", "Всегда подтверждать опасные действия", True, BOOL, "actions",
            "Выключение, перезагрузка, закрытие окон, удаление файлов.", level="advanced"),
    Setting("safety.confirm_list", "Что всегда требует подтверждения",
            "shutdown,reboot,suspend,logout,run_shell,run_powershell,delete_file,close_app",
            LIST, "actions",
            "Внутренние имена опасных действий.", level="expert"),
    Setting("safety.blocked_commands", "Запрещённые консольные команды",
            "rm -rf /,mkfs,dd if=,shutdown -h now,:(){,fork bomb",
            LIST, "actions",
            "Ассистент откажется их выполнять независимо от подтверждения.",
            level="advanced"),
    Setting("safety.shell_timeout", "Максимальное время консольной команды", 60, INT, "actions",
            None, minimum=1, maximum=3600, unit="с", level="advanced"),
    Setting("safety.sandbox_paths", "Запрещённые пути для записи",
            "/etc:/usr:/boot:/sys:/proc:/dev:/var/lib",
            LIST, "actions",
            "Каталоги, куда команды не могут писать файлы.", level="advanced"),
    Setting("safety.max_text_input", "Максимальная длина вводимого текста", 5000, INT, "actions",
            "Предохранитель для действия «Ввести текст».",
            minimum=10, maximum=100000, unit="симв.", level="expert"),
    Setting("safety.panic_key", "Клавиша аварийной остановки работает", True, BOOL, "actions",
            "Мгновенно останавливает всё выполнение.", level="advanced"),
    Setting("safety.log_actions", "Записывать все действия в журнал", True, BOOL, "actions",
            "Полный протокол для разбора инцидентов.", level="advanced"),

    # ================= ДОПОЛНЕНИЯ =================
    Setting("plugins.telegram_enabled", "Управление из Telegram", False, BOOL, "plugins",
            "Свои команды через личного бота. Нужен токен от BotFather."),
    Setting("plugins.telegram_token", "Токен бота (BotFather)", "", SECRET, "plugins",
            "Создайте бота командой /newbot и вставьте полученный токен.",
            depends_on=("plugins.telegram_enabled", "true")),
    Setting("plugins.telegram_owner_id", "Ваш Telegram ID", "", STR, "plugins",
            "Только этому пользователю бот отвечает. Узнайте у @userinfobot.",
            depends_on=("plugins.telegram_enabled", "true")),
    Setting("plugins.telegram_menu_refresh", "Обновлять меню автоматически", True, BOOL, "plugins",
            "Меню команд в боте обновляется при правке редактора.",
            depends_on=("plugins.telegram_enabled", "true"), level="advanced"),
    Setting("plugins.telegram_notify", "Сообщать о выполнении в Telegram", False, BOOL, "plugins",
            "Отчёт о командах приходит в чат с ботом.",
            depends_on=("plugins.telegram_enabled", "true"), level="advanced"),
    Setting("plugins.telegram_voice", "Принимать голосовые сообщения", False, BOOL, "plugins",
            "Голосовые из Telegram распознаются и выполняются как команды.",
            depends_on=("plugins.telegram_enabled", "true"), level="advanced"),
    Setting("plugins.telegram_allow_dangerous", "Разрешить опасные команды из Telegram",
            False, BOOL, "plugins",
            "Выключение и перезагрузка по сообщению в мессенджере. По умолчанию запрещено.",
            depends_on=("plugins.telegram_enabled", "true")),
    Setting("plugins.telegram_poll_interval", "Частота проверки сообщений", 2, INT, "plugins",
            None, minimum=1, maximum=60, unit="с",
            depends_on=("plugins.telegram_enabled", "true"), level="expert"),
    Setting("plugins.avatar_plugin", "Плагин аватара", True, BOOL, "plugins",
            "Анимированный аватар поверх окон.", level="advanced"),
    Setting("plugins.scenario_pack", "Готовые сценарии", True, BOOL, "plugins",
            "Наборы «Доброе утро», «Режим кино», «Пора спать» и другие.", level="advanced"),
    Setting("plugins.custom_dir", "Каталог пользовательских плагинов", "", DIR, "plugins",
            "Пусто — стандартный каталог данных.", level="advanced"),
    Setting("plugins.autoload", "Загружать плагины при запуске", True, BOOL, "plugins",
            None, level="advanced"),
    Setting("plugins.notify_errors", "Показывать ошибки плагинов", True, BOOL, "plugins",
            None, level="expert"),

    # ================= ПРОИЗВОДИТЕЛЬНОСТЬ =================
    Setting("perf.priority", "Приоритет процесса", "normal", CHOICE, "performance",
            "Повышенный приоритет — отзывчивее распознавание, но выше расход батареи.",
            choices=(("low", "Низкий"), ("normal", "Обычный"), ("high", "Повышенный"))),
    Setting("perf.cpu_limit", "Ограничение нагрузки на процессор", 0, INT, "performance",
            "0 — без ограничений. Меньше — тише и экономнее, но распознавание медленнее.",
            minimum=0, maximum=100, unit="%", level="advanced"),
    Setting("perf.idle_sleep", "Засыпать при простое", False, BOOL, "performance",
            "Останавливать распознавание, если команд не было давно.", level="advanced"),
    Setting("perf.idle_minutes", "Простой до сна", 30, INT, "performance",
            None, minimum=1, maximum=1440, unit="мин",
            depends_on=("perf.idle_sleep", "true"), level="advanced"),
    Setting("perf.cache_size_mb", "Размер кэша озвучки", 200, INT, "performance",
            None, minimum=0, maximum=5000, unit="МБ", level="advanced"),
    Setting("perf.history_days", "Хранить историю", 30, INT, "performance",
            "Старые записи удаляются автоматически.",
            minimum=0, maximum=3650, unit="дней"),
    Setting("perf.prewarm_models", "Прогревать модели при старте", False, BOOL, "performance",
            "Первая команда распознаётся быстрее, запуск дольше.", level="advanced"),
    Setting("perf.gpu_accel", "Использовать видеокарту", "auto", CHOICE, "performance",
            "Ускорение распознавания на NVIDIA, если доступно.",
            choices=(("auto", "Авто"), ("off", "Выключено")), level="advanced"),
    Setting("perf.memory_limit_mb", "Ограничение памяти", 0, INT, "performance",
            "0 — без ограничений.", minimum=0, maximum=32768, unit="МБ", level="expert"),
    Setting("perf.audio_buffer_ms", "Буфер аудио", 100, INT, "performance",
            "Меньше — быстрее реакция, больше — устойчивее к сбоям звука.",
            minimum=20, maximum=1000, unit="мс", level="expert"),

    # ================= ПРИВАТНОСТЬ =================
    Setting("privacy.save_history", "Хранить историю команд", True, BOOL, "privacy",
            "Список распознанных фраз и результатов. Удобно для разбора ошибок."),
    Setting("privacy.history_audio", "Сохранять записи голоса", False, BOOL, "privacy",
            "Аудио фраз сохраняется рядом с историей для ручного разбора."),
    Setting("privacy.offline_only", "Только офлайн-функции", False, BOOL, "privacy",
            "Полностью запрещает любые обращения в интернет, включая нейросеть."),
    Setting("privacy.log_level", "Подробность журнала", "info", CHOICE, "privacy",
            "Уровень детализации файлов журнала.",
            choices=(("error", "Только ошибки"), ("warning", "Предупреждения"),
                     ("info", "Обычный"), ("debug", "Подробный (для отладки)"))),
    Setting("privacy.log_days", "Хранить журналы", 14, INT, "privacy",
            None, minimum=0, maximum=365, unit="дней", level="advanced"),
    Setting("privacy.log_recognized", "Записывать распознанный текст в журнал", False, BOOL,
            "privacy",
            "По умолчанию услышанные фразы в журнал не попадают.", level="advanced"),
    Setting("privacy.hash_device_id", "Обезличивать идентификатор устройства", True, BOOL,
            "privacy", None, level="expert"),
    Setting("privacy.clear_on_exit", "Очищать историю при выходе", False, BOOL, "privacy",
            None, level="advanced"),

    # ================= ОБНОВЛЕНИЯ =================
    Setting("updates.check_enabled", "Проверять обновления", True, BOOL, "updates",
            "Программа сообщит о новой версии, но ничего не установит без вашего согласия."),
    Setting("updates.channel", "Канал обновлений", "stable", CHOICE, "updates",
            "Стабильный — проверенные версии, тестовый — новинки раньше всех.",
            choices=(("stable", "Стабильный"), ("beta", "Тестовый"))),
    Setting("updates.check_interval_hours", "Как часто проверять", 24, INT, "updates",
            None, minimum=1, maximum=720, unit="ч", level="advanced"),
    Setting("updates.url", "Адрес обновлений", "", STR, "updates",
            "Пусто — встроенный адрес.", level="expert"),
    Setting("updates.notify_only", "Только уведомлять", True, BOOL, "updates",
            "Обновление ставится вами вручную.", level="advanced"),

    # ================= ДОПОЛНИТЕЛЬНО =================
    Setting("adv.autobackup", "Резервные копии настроек и команд", True, BOOL, "advanced",
            "Копия создаётся перед каждым изменением конфигурации."),
    Setting("adv.backup_count", "Сколько копий хранить", 10, INT, "advanced",
            None, minimum=1, maximum=200, level="advanced"),
    Setting("adv.backup_on_start", "Копия при каждом запуске", True, BOOL, "advanced",
            "Отдельная ежедневная копия.", level="advanced"),
    Setting("adv.diagnostics", "Собирать диагностику для поддержки", True, BOOL, "advanced",
            "Технические сведения о системе без личных данных.", level="advanced"),
    Setting("adv.debug_panel", "Показывать отладочную панель", False, BOOL, "advanced",
            "Живой журнал событий в отдельном окне.", level="expert"),
    Setting("adv.simulate_actions", "Пробный прогон без выполнения", False, BOOL, "advanced",
            "Команды только показывают, что сделали бы, ничего не выполняя.", level="expert"),
    Setting("adv.export_all", "Экспортировать вместе с ключами", False, BOOL, "advanced",
            "По умолчанию при экспорте ключи API не выгружаются.", level="expert"),
    Setting("adv.portable", "Портативный режим", False, BOOL, "advanced",
            "Хранить все данные рядом с программой. Требуется перезапуск.",
            restart=True, level="advanced"),
    Setting("adv.check_deps_on_start", "Проверять компоненты при запуске", True, BOOL, "advanced",
            "Сообщать о недостающих моделях и программах.", level="advanced"),
    Setting("adv.autoinstall_deps", "Предлагать установку компонентов", True, BOOL, "advanced",
            "Показывать кнопку загрузки моделей распознавания и голосов.",
            level="advanced"),
]


# --- Производные структуры --------------------------------------------------

SETTINGS_BY_KEY: dict[str, Setting] = {s.key: s for s in _SETTINGS}
SETTINGS_BY_SECTION: dict[str, list[Setting]] = {}
for _s in _SETTINGS:
    SETTINGS_BY_SECTION.setdefault(_s.section, []).append(_s)

SECTION_BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}


def defaults() -> dict[str, Any]:
    """Полный набор значений по умолчанию (плоский словарь «ключ → значение»)."""
    out: dict[str, Any] = {}
    for setting in _SETTINGS:
        value = setting.default
        # Изменяемые значения копируем, чтобы не делили состояние.
        if isinstance(value, (dict, list)):
            value = value.copy()
        out[setting.key] = value
    return out


def all_settings() -> tuple[Setting, ...]:
    return tuple(_SETTINGS)


def get_setting(key: str) -> Setting | None:
    return SETTINGS_BY_KEY.get(key)


def sections() -> tuple[Section, ...]:
    return tuple(sorted(SECTIONS, key=lambda s: s.order))


def settings_in(section: str) -> list[Setting]:
    return list(SETTINGS_BY_SECTION.get(section, ()))


def keys_with_prefix(prefix: str) -> list[str]:
    return [s.key for s in _SETTINGS if s.key.startswith(prefix)]


def coerce(setting: Setting, value: Any) -> Any:
    """Привести значение к типу настройки. Неверные значения заменяются
    значением по умолчанию — конфигурация не должна ронять программу."""
    try:
        if setting.kind == BOOL:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "да", "on", "вкл")
            return bool(value)

        if setting.kind == INT:
            number = int(float(value))
            if setting.minimum is not None:
                number = max(int(setting.minimum), number)
            if setting.maximum is not None:
                number = min(int(setting.maximum), number)
            return number

        if setting.kind == FLOAT:
            number = float(value)
            if setting.minimum is not None:
                number = max(float(setting.minimum), number)
            if setting.maximum is not None:
                number = min(float(setting.maximum), number)
            return number

        if setting.kind == CHOICE:
            if setting.dynamic or not setting.choices:
                return str(value)
            allowed = {c[0] for c in setting.choices}
            text = str(value)
            return text if text in allowed else setting.default

        if setting.kind == LIST:
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            if isinstance(value, str):
                # Список хранится либо строкой с разделителями, либо как есть.
                if ":" in value and setting.key != "match.extra_filler":
                    return [p.strip() for p in value.split(":") if p.strip()]
                return [p.strip() for p in value.replace("\n", ",").split(",") if p.strip()]
            return list(setting.default)

        if setting.kind == MULTI:
            if isinstance(value, (list, tuple)):
                allowed = {c[0] for c in setting.choices} if setting.choices else None
                items = [str(v) for v in value]
                return [v for v in items if allowed is None or v in allowed]
            return list(setting.default)

        if setting.kind in ("dict",):
            return value if isinstance(value, dict) else dict(setting.default)

        # Строки и пути.
        text = "" if value is None else str(value)
        if setting.kind == COLOR and text and not text.startswith("#"):
            return setting.default
        return text

    except (TypeError, ValueError):
        return setting.default