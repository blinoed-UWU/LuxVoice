"""Точка входа: запуск программы целиком.

Запуск без графического интерфейса (--headless) полезен для отладки
и для работы в качестве службы. Режим --diagnose печатает сведения
о готовности компонентов и завершает работу — это первое, что стоит
запустить на новой системе.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from luxvoice import __app_name__, __version__
from luxvoice.core import paths


def setup_logging(level: str = "info", console: bool = True) -> None:
    """Настроить журналирование в файл и, при желании, в консоль."""
    paths.ensure_dirs()

    levels = {
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
    }
    numeric = levels.get((level or "info").lower(), logging.INFO)

    from logging.handlers import RotatingFileHandler

    log_file = paths.log_dir() / "luxvoice.log"
    handlers: list[logging.Handler] = []

    try:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)-28s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        handlers.append(file_handler)
    except OSError as exc:
        print(f"Не удалось открыть файл журнала: {exc}", file=sys.stderr)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter(
            "%(levelname)-8s %(message)s"))
        handlers.append(console_handler)

    logging.basicConfig(level=numeric, handlers=handlers, force=True)

    # Сторонние библиотеки шумят — приглушаем.
    for noisy in ("urllib3", "requests", "PIL", "matplotlib", "httpx",
                  "asyncio", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def check_dependencies(verbose: bool = False) -> dict[str, object]:
    """Проверить готовность всех компонентов."""
    report: dict[str, object] = {}

    # --- Окружение ---
    from luxvoice.sysint.windows import desktop_environment, is_kde, is_wayland
    env = desktop_environment()
    report["session"] = f"{env['session']} / {env['desktop'] or 'неизвестно'}"
    report["wayland"] = is_wayland()
    report["kde"] = is_kde()

    # --- Ввод ---
    from luxvoice.sysint.uinput import input_available
    available, reason = input_available()
    report["input"] = available
    report["input_reason"] = reason

    # --- Звук ---
    try:
        from luxvoice.sysint.audio import get_audio
        audio = get_audio()
        report["volume"] = audio.volume()
        report["microphones"] = len(audio.microphones())
    except Exception as exc:  # noqa: BLE001
        report["audio_error"] = str(exc)

    # --- Микрофоны ---
    try:
        from luxvoice.stt.capture import list_microphones
        microphones = list_microphones()
        report["mic_devices"] = len(microphones)
        report["mic_names"] = [d.name for d in microphones[:3]]
    except Exception as exc:  # noqa: BLE001
        report["mic_error"] = str(exc)

    # --- Распознавание ---
    try:
        from luxvoice.stt.engines import available_tools, vosk_model_installed
        report["stt_tools"] = available_tools()
        report["vosk_ru"] = vosk_model_installed("ru")
        report["vosk_en"] = vosk_model_installed("en")
    except Exception as exc:  # noqa: BLE001
        report["stt_error"] = str(exc)

    # --- Синтез ---
    try:
        from luxvoice.tts.synthesizer import SpeechSynthesizer
        synth = SpeechSynthesizer()
        engines = synth.available_engines()
        report["tts_engines"] = engines
        report["tts_voices"] = len(synth.voices())
    except Exception as exc:  # noqa: BLE001
        report["tts_error"] = str(exc)

    # --- Окна ---
    try:
        from luxvoice.sysint.windows import get_windows
        windows = get_windows().windows(use_cache=False)
        report["windows"] = len(windows)
    except Exception as exc:  # noqa: BLE001
        report["windows_error"] = str(exc)

    # --- Программы ---
    try:
        from luxvoice.sysint.launcher import get_locator
        report["apps"] = len(get_locator().entries())
    except Exception as exc:  # noqa: BLE001
        report["apps_error"] = str(exc)

    # --- Данные ---
    try:
        from luxvoice.core.store import get_store
        report["commands"] = get_store().count()
    except Exception as exc:  # noqa: BLE001
        report["commands_error"] = str(exc)

    report["data_dir"] = str(paths.data_dir())
    report["config"] = str(paths.config_file())
    report["log"] = str(paths.log_dir() / "luxvoice.log")
    return report


def print_diagnostics() -> int:
    """Напечатать отчёт о готовности и вернуть код выхода."""
    report = check_dependencies(verbose=True)

    print(f"\n{__app_name__} {__version__} — проверка компонентов\n" + "=" * 58)

    print(f"\nСистема")
    print(f"  сеанс:            {report.get('session')}")
    print(f"  Wayland:          {'да' if report.get('wayland') else 'нет'}")
    print(f"  KDE Plasma:       {'да' if report.get('kde') else 'нет'}")

    print(f"\nУправление компьютером")
    if report.get("input"):
        print("  ввод (клавиши/мышь): доступен")
    else:
        print(f"  ввод:             НЕДОСТУПЕН — {report.get('input_reason')}")
        print("                    исправление: sudo usermod -aG input $USER")
        print("                    затем выйдите из системы и войдите заново")
    print(f"  открытых окон:    {report.get('windows', '—')}")
    print(f"  найдено программ: {report.get('apps', '—')}")

    print(f"\nЗвук и микрофон")
    print(f"  громкость:        {report.get('volume', '—')}%")
    print(f"  микрофонов:       {report.get('mic_devices', '—')}")
    for name in report.get("mic_names", []) or []:
        print(f"                    · {name}")

    print(f"\nРаспознавание речи")
    tools = report.get("stt_tools") or {}
    for key, label in (("vosk", "Vosk"), ("whisper", "Whisper"),
                       ("webrtcvad", "Определение речи")):
        mark = "установлен" if tools.get(key) else "не установлен"
        print(f"  {label:18} {mark}")
    print(f"  модель RU:        {'есть' if report.get('vosk_ru') else 'НЕТ'}")
    print(f"  модель EN:        {'есть' if report.get('vosk_en') else 'НЕТ'}")
    if not report.get("vosk_ru"):
        print("                    загрузка: программа → Настройки → "
              "Голосовой ввод")

    print(f"\nСинтез речи")
    engines = report.get("tts_engines") or {}
    for key, label in (("piper", "Piper"), ("rhvoice", "RHVoice"),
                       ("espeak", "eSpeak NG"), ("cloud", "Облачные голоса")):
        mark = "доступен" if engines.get(key) else "нет"
        print(f"  {label:18} {mark}")
    print(f"  доступно голосов: {report.get('tts_voices', 0)}")

    print(f"\nДанные")
    print(f"  команд в редакторе: {report.get('commands', '—')}")
    print(f"  каталог данных:     {report.get('data_dir')}")
    print(f"  настройки:          {report.get('config')}")
    print(f"  журнал:             {report.get('log')}")

    problems = []
    if not report.get("input"):
        problems.append("нет доступа к виртуальному вводу — "
                        "команды с клавишами и мышью работать не будут")
    if not report.get("vosk_ru"):
        problems.append("не установлена модель распознавания речи")
    if not any(engines.values()):
        problems.append("не найден ни один движок синтеза речи")

    print("\n" + "=" * 58)
    if problems:
        print("Что нужно исправить:")
        for index, problem in enumerate(problems, 1):
            print(f"  {index}. {problem}")
        return 1

    print("Все основные компоненты готовы. Можно запускать программу.")
    return 0


def install_model(language: str = "ru") -> int:
    """Загрузить модель распознавания из командной строки."""
    from luxvoice.stt.engines import download_vosk_model

    print(f"Загрузка модели распознавания ({language})…")
    last = [-1]

    def progress(state) -> None:
        percent = state.percent
        if percent != last[0]:
            last[0] = percent
            bar = "█" * (percent // 4) + "░" * (25 - percent // 4)
            print(f"\r  [{bar}] {percent:3d}%  "
                  f"{state.downloaded // 1048576} МБ", end="", flush=True)

    ok, message = download_vosk_model(language, progress=progress)
    print()
    print(("Готово: " if ok else "Ошибка: ") + message)
    return 0 if ok else 1


def run_headless() -> int:
    """Работа без графического интерфейса: только слушать и выполнять."""
    from luxvoice.actions.runner import get_runner
    from luxvoice.core.history import HistoryEntry, get_history
    from luxvoice.core.settings import Settings
    from luxvoice.stt.recognizer import get_recognizer
    from luxvoice.tts.synthesizer import get_synthesizer

    paths.ensure_dirs()
    settings = Settings()
    settings.load()

    recognizer = get_recognizer(settings)
    runner = get_runner(settings)
    synth = get_synthesizer(settings)

    recognizer.on_text(lambda result: runner.handle_text(result.text))

    def speak(text, phrase_kind, interrupt):
        if phrase_kind:
            synth.say_phrase(phrase_kind)
        elif text:
            synth.say(text, interrupt=interrupt)

    runner.on_speak(speak)

    if settings.flag("exec.hold_mic_while_speaking", True):
        synth.on_speaking_changed(
            lambda speaking: (recognizer.pause() if speaking
                              else recognizer.resume()))

    history = get_history(settings)

    def record(report) -> None:
        import time as _time
        history.add(HistoryEntry(
            timestamp=_time.time(),
            phrase=report.phrase,
            command_id=report.command.id if report.command else "",
            command=report.command.title if report.command else "",
            ok=report.ok,
            cancelled=report.cancelled,
            elapsed=report.elapsed,
            steps=report.steps,
            error=report.error,
        ))
        status = "OK" if report.ok else f"ОШИБКА: {report.error}"
        print(f"[{_time.strftime('%H:%M:%S')}] {report.phrase!r} → {status}")

    runner.on_history(record)

    ok, message = recognizer.start_listening()
    if not ok:
        print(f"Не удалось включить микрофон: {message}", file=sys.stderr)
        return 2

    print(f"{__app_name__} слушает. Нажмите Ctrl+C для выхода.")
    stop = threading.Event()

    def handler(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    try:
        while not stop.is_set():
            stop.wait(0.5)
    finally:
        print("\nОстановка…")
        recognizer.stop_listening()
        synth.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    """Основная точка входа."""
    parser = argparse.ArgumentParser(
        prog="luxvoice",
        description=f"{__app_name__} — голосовой ассистент для Linux",
    )
    parser.add_argument("--version", action="version",
                        version=f"{__app_name__} {__version__}")
    parser.add_argument("--diagnose", action="store_true",
                        help="проверить компоненты и выйти")
    parser.add_argument("--headless", action="store_true",
                        help="работать без графического интерфейса")
    parser.add_argument("--install-model", metavar="ЯЗЫК", nargs="?",
                        const="ru", help="загрузить модель распознавания (ru или en)")
    parser.add_argument("--log-level", default="",
                        help="уровень журнала: error, warning, info, debug")
    parser.add_argument("--portable", action="store_true",
                        help="хранить данные рядом с программой")
    parser.add_argument("--command", metavar="ФРАЗА",
                        help="выполнить одну команду и выйти")
    parser.add_argument("--start-hidden", action="store_true",
                        help="запустить свёрнутым в трей (для автозапуска)")

    args = parser.parse_args(argv)

    if args.portable:
        os.environ["LUXVOICE_HOME"] = str(Path(__file__).resolve().parent.parent
                                          / "portable-data")

    paths.ensure_dirs()

    # Уровень журнала из настроек, если не задан в командной строке.
    level = args.log_level
    if not level:
        try:
            from luxvoice.core.settings import Settings
            probe = Settings()
            probe.load()
            level = probe.text("privacy.log_level", "info")
        except Exception:  # noqa: BLE001
            level = "info"

    setup_logging(level, console=args.diagnose or args.headless)

    if args.diagnose:
        return print_diagnostics()

    if args.install_model:
        return install_model(args.install_model)

    if args.command:
        return run_single_command(args.command)

    if args.headless:
        return run_headless()

    # --- Графический режим ---
    return run_gui(start_hidden=args.start_hidden)


def run_single_command(phrase: str) -> int:
    """Выполнить одну команду без интерфейса — удобно для проверки."""
    from luxvoice.actions.runner import get_runner
    from luxvoice.core.settings import Settings

    settings = Settings()
    settings.load()
    runner = get_runner(settings)
    runner.refresh()

    report = runner.handle_text(phrase)
    if report is None:
        print("Команда не найдена или обработана как служебная")
        return 1

    print(f"Команда: {report.command.title if report.command else '—'}")
    for title, ok, message in report.steps:
        print(f"  {'✓' if ok else '✗'} {title}" + (f" — {message}" if message else ""))
    print(f"Итог: {report.summary}")
    return 0 if report.ok else 2


def run_gui(start_hidden: bool = False) -> int:
    """Запустить графический интерфейс."""
    # Проверяем наличие PyQt6 заранее: иначе ошибка будет непонятной.
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: F401
    except ImportError:
        print("Не установлен PyQt6 — графический интерфейс недоступен.\n"
              "Установите: sudo pacman -S python-pyqt6\n"
              "Программа может работать без интерфейса: luxvoice --headless",
              file=sys.stderr)
        return 3

    # Проверяем дисплей.
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("Не найден графический сеанс. "
              "Для работы без интерфейса используйте --headless.",
              file=sys.stderr)
        return 4

    from luxvoice.ui.main_window import AssistantApp

    errors: list[str] = []

    def report_exception(exc_type, exc_value, exc_traceback) -> None:
        """Перехватываем необработанные ошибки, чтобы программа не падала молча."""
        import traceback
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value,
                                                  exc_traceback))
        logging.getLogger("luxvoice").error("Необработанная ошибка:\n%s", text)
        errors.append(text)

    sys.excepthook = report_exception

    try:
        app = AssistantApp(sys.argv, start_hidden=start_hidden)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"\nНе удалось запустить программу: {exc}", file=sys.stderr)
        print(f"Подробности в журнале: {paths.log_dir() / 'luxvoice.log'}",
              file=sys.stderr)
        return 5

    return app.run()


if __name__ == "__main__":
    sys.exit(main())