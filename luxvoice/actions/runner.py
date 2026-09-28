"""Исполнитель команд: от распознанной фразы до выполненного действия.

Полный путь одной фразы:
  1. Распознанный текст приходит в handle_text.
  2. Отделяются обращение и слова-связки.
  3. Каждая часть сопоставляется с командой в редакторе.
  4. Опасная команда запрашивает подтверждение голосом.
  5. Действия выполняются по порядку, с паузами и повторами.
  6. Результат уходит в историю, озвучку и уведомления.

Отдельно обрабатываются голосовые команды управления самим ассистентом:
включение и выключение микрофона, переключение режима обращения.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from luxvoice.actions import catalog
from luxvoice.actions.executor import ActionResult, get_executor
from luxvoice.core.events import (
    ACTION_PROGRESS,
    COMMAND_ASK_CONFIRM,
    COMMAND_BLOCKED,
    COMMAND_CONFIRMED,
    COMMAND_FINISHED,
    COMMAND_MATCHED,
    COMMAND_STARTED,
    COMMAND_UNKNOWN,
    MODE_CHANGED,
    NOTIFY,
    STATUS_MESSAGE,
    bus,
)
from luxvoice.core.matcher import get_matcher
from luxvoice.core.model import Action, Command
from luxvoice.core.store import get_store, normalize_phrase

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    """Итог выполнения команды."""

    command: Command | None = None
    phrase: str = ""
    ok: bool = True
    steps: list[tuple[str, bool, str]] = field(default_factory=list)
    output: str = ""
    error: str = ""
    cancelled: bool = False
    elapsed: float = 0.0

    @property
    def summary(self) -> str:
        """Короткий текст результата для истории."""
        if self.cancelled:
            return "отменено"
        if not self.ok:
            return self.error or "ошибка"
        done = sum(1 for _, ok, _ in self.steps if ok)
        return f"выполнено шагов: {done} из {len(self.steps)}"


class CommandRunner:
    """Ведёт выполнение команд и диалог с пользователем."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._store = get_store()
        self._matcher = get_matcher(self._store, settings)
        self._executor = get_executor(settings)

        self._lock = threading.RLock()
        self._run_lock = threading.Lock()     # одна команда за раз
        self._busy = threading.Event()

        # Ожидание голосового ответа.
        self._answer_event = threading.Event()
        self._answer_text = ""
        self._awaiting = threading.Event()

        # Внешние обработчики.
        self._speak_handler: Callable[..., None] | None = None
        self._history_handler: Callable[[RunReport], None] | None = None
        self._confirm_handler: Callable[[Command, float], bool] | None = None

        # Состояние режимов.
        self._silent = False
        self._last_command_time = 0.0
        self._last_command: Command | None = None

        self._executor.on_ask(self._ask_user)
        self._executor.on_run_command(self._run_by_name)

    # --- Внешние зависимости --------------------------------------------

    def set_settings(self, settings) -> None:
        self._settings = settings
        self._matcher.set_settings(settings)
        self._executor.set_settings(settings)

    def on_speak(self, handler: Callable[..., None]) -> None:
        """handler(text, phrase_kind, interrupt)."""
        self._speak_handler = handler
        self._executor.on_speak(handler)

    def on_notify(self, handler: Callable[[str, str], None]) -> None:
        self._executor.on_notify(handler)

    def on_history(self, handler: Callable[[RunReport], None]) -> None:
        self._history_handler = handler

    def refresh(self) -> None:
        """Пересобрать индекс фраз после правки редактора."""
        self._matcher.refresh()

    # --- Приём текста -----------------------------------------------------

    def handle_text(self, text: str) -> RunReport | None:
        """Обработать распознанную фразу."""
        text = (text or "").strip()
        if not text:
            return None

        # 1. Команды управления ассистентом.
        if self._handle_control_phrase(text):
            return None

        # 2. Ожидание ответа на вопрос — отдаём текст диалогу.
        if self._awaiting.is_set():
            self._answer_text = text
            self._answer_event.set()
            return None

        # 3. Поиск команды.
        contexts = self._window_contexts()
        result = self._matcher.match(text, window_titles=contexts)
        
        # Логируем распознанный текст и результат matching
        log.info("Распознано: %r", text)
        if result.found:
            log.info("Найдена команда: %s (score: %.0f%%)", 
                    result.command.title if result.command else "?", result.score)
        else:
            log.info("Команда не найдена: %s", result.reason)

        if not result.found:
            # Сказано только обращение — это вызов ассистента.
            if result.reason == "только обращение":
                log.debug("Услышано обращение")
                self._speak(None, "activate", True)
                return None

            if result.requires_prefix:
                self._speak(None, "need_prefix", False)
                bus.publish(STATUS_MESSAGE, text="Нужен префикс", level="warning")
                return None

            log.info("Команда не найдена: %r (%s)", text, result.reason)
            bus.publish(COMMAND_UNKNOWN, text=text)

            # Пробуем локальный ИИ (LM Studio/Ollama) для сложных фраз.
            if self._settings is not None and self._settings.flag("aichat.route_unknown", True):
                ai_report = self._try_ai_route(text)
                if ai_report is not None:
                    return ai_report

            if self._settings is None or self._settings.flag("app.reply_unknown", True):
                self._speak(None, "not_found", False)
            return None

        bus.publish(COMMAND_MATCHED, command=result.command, phrase=result.phrase,
                    score=result.score)

        # 4. Связки: «открой ютуб и сделай громче».
        if self._settings is not None and self._settings.flag("chain.enabled", True):
            parts = self._matcher.split_chain(text)
            if len(parts) > 1:
                return self._run_chain(parts, text, contexts)

        return self._run_command(result.command, text)

    def _window_contexts(self) -> list[str]:
        """Заголовки окон для контекстного поиска."""
        if self._settings is not None and not self._settings.flag(
                "match.window_context", True):
            return []
        try:
            from luxvoice.sysint.windows import get_windows
            return get_windows().active_contexts()
        except Exception as exc:  # noqa: BLE001
            log.debug("Не удалось получить окна: %s", exc)
            return []

    # --- Управление ассистентом ------------------------------------------

    def _handle_control_phrase(self, text: str) -> bool:
        """Голосовые фразы управления самим ассистентом."""
        if self._settings is None:
            return False

        normalized = normalize_phrase(text)
        # Убираем обращение, если оно есть.
        _, _ = self._matcher.strip_prefix(normalized)
        stripped, _ = self._matcher.strip_prefix(normalized)
        target = stripped or normalized

        # Фразы переключения режимов из настроек.
        raw = self._settings.text("exec.mode_phrases", "")
        phrases: dict[str, str] = {}
        for line in raw.replace(";", "\n").splitlines():
            line = line.strip()
            if not line:
                continue
            lowered = normalize_phrase(line)
            if any(word in lowered for word in ("тихий", "silent")):
                phrases[lowered] = "silent_on"
            elif any(word in lowered for word in ("продолжай", "вернись", "resume")):
                phrases[lowered] = "silent_off"
            elif any(word in lowered for word in ("префикс", "prefix")):
                phrases[lowered] = "prefix_on"
            elif any(word in lowered for word in ("хватит слушать", "выключи микрофон",
                                                  "не слушай")):
                phrases[lowered] = "mic_off"
            elif any(word in lowered for word in ("включи микрофон", "слушай")):
                phrases[lowered] = "mic_on"

        action = phrases.get(target)
        if action is None:
            # Проверяем частичное совпадение.
            for phrase, name in phrases.items():
                if phrase and (phrase in target or target in phrase):
                    action = name
                    break

        if action is None:
            return False

        log.info("Команда управления: %s (%r)", action, text)
        self._apply_mode(action)
        return True

    def _try_ai_route(self, phrase: str) -> RunReport | None:
        """Отправить нераспознанную фразу локальной нейросети.

        Работает только с локальными провайдерами (LM Studio, Ollama)
        и только если нейросеть включена. Если модель просит выполнить
        команду, раннер пытается её запустить; иначе озвучивает ответ.
        """
        if self._settings is None:
            return None
        if not self._settings.flag("ai.enabled", False):
            return None

        provider = ""
        try:
            from luxvoice.ai.client import get_ai_client, provider_info
            client = get_ai_client(self._settings)
            if not client.enabled:
                return None
            provider = client._provider().key
            if not provider_info(provider).local:
                return None
        except Exception:  # noqa: BLE001
            return None

        log.info("Маршрутизация фразы в локальный ИИ: %r", phrase)
        bus.publish(STATUS_MESSAGE, text="Думаю…", level="busy")

        try:
            reply = client.reply(phrase, allow_commands=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("ИИ-маршрутизация не сработала: %s", exc)
            return None

        if reply.error:
            log.debug("ИИ вернул ошибку: %s", reply.error)
            return None

        if reply.command:
            log.info("ИИ предложил команду: %r", reply.command)
            match_result = self._matcher.match(reply.command)
            if match_result.found:
                self._speak("Выполняю", "", True)
                return self._run_command(match_result.command, reply.command)

        if reply.text:
            self._speak(reply.text, "", True)
            report = RunReport(command=None, phrase=phrase, ok=True,
                               output=reply.text)
            self._record(report)
            return report

        return None

    def _apply_mode(self, action: str) -> None:
        """Применить команду режима."""
        from luxvoice.stt.recognizer import get_recognizer

        if action == "silent_on":
            self._silent = True
            self._speak_forced("Тихий режим")
            bus.publish(MODE_CHANGED, prefix_mode=bool(
                self._settings and self._settings.flag("stt.prefix_mode", False)),
                silent=True)
            return

        if action == "silent_off":
            self._silent = False
            self._speak_forced("Продолжаю слушать")
            bus.publish(MODE_CHANGED, prefix_mode=bool(
                self._settings and self._settings.flag("stt.prefix_mode", False)),
                silent=False)
            return

        if action == "prefix_on" and self._settings is not None:
            new_state = not self._settings.flag("stt.prefix_mode", False)
            self._settings.set("stt.prefix_mode", new_state)
            self._speak_forced("Режим обращения включён" if new_state
                               else "Обращение больше не обязательно")
            bus.publish(MODE_CHANGED, prefix_mode=new_state, silent=self._silent)
            return

        if action == "mic_off":
            get_recognizer(self._settings).stop_listening()
            bus.publish(STATUS_MESSAGE, text="Микрофон выключен", level="info")
            return

        if action == "mic_on":
            get_recognizer(self._settings).start_listening()
            bus.publish(STATUS_MESSAGE, text="Микрофон включён", level="info")
            return

    def _speak_forced(self, text: str) -> None:
        """Произнести фразу даже в тихом режиме — ответ на управляющую команду."""
        if self._speak_handler is not None:
            try:
                self._speak_handler(text, "", True)
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("Озвучка не удалась: %s", exc)
        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            get_synthesizer(self._settings).say(text, interrupt=True)
        except Exception:  # noqa: BLE001
            pass

    def _speak(self, text: str | None, phrase_kind: str = "",
               interrupt: bool = True) -> None:
        """Произнести фразу, если тихий режим выключен."""
        if self._silent:
            return

        # Проверяем, включена ли озвучка для этого типа.
        if self._settings is not None and text is None and phrase_kind:
            flags = {
                "ok": "tts.on_success", "error": "tts.on_error",
                "activate": "tts.on_activate", "confirm": "tts.on_confirm",
                "start": "tts.on_start", "not_found": "app.reply_unknown",
            }
            key = flags.get(phrase_kind)
            if key and not self._settings.flag(key, True):
                return

        if self._speak_handler is not None:
            try:
                self._speak_handler(text, phrase_kind, interrupt)
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("Обработчик озвучки упал: %s", exc)

        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            synth = get_synthesizer(self._settings)
            if phrase_kind:
                synth.say_phrase(phrase_kind)
            elif text:
                synth.say(text, interrupt=interrupt)
        except Exception as exc:  # noqa: BLE001
            log.debug("Озвучка недоступна: %s", exc)

    # --- Выполнение -------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def stop_all(self) -> None:
        """Немедленная остановка: прервать команду и диалог."""
        self._executor.stop()
        self._answer_event.set()
        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            get_synthesizer(self._settings).stop()
        except Exception:  # noqa: BLE001
            pass

    def _run_by_name(self, name: str) -> bool:
        """Выполнить команду по названию — для действия «Выполнить другую команду»."""
        candidates = self._store.search(name, limit=5)
        if not candidates:
            # Пробуем сопоставление как фразы.
            result = self._matcher.match(name)
            if result.found:
                candidates = [result.command]
        if not candidates:
            return False

        target = candidates[0]
        self._execute_actions(target, phrase=name)
        return True

    def _run_chain(self, parts: list[str], original: str,
                   contexts: list[str]) -> RunReport:
        """Выполнить несколько команд одной фразой."""
        max_commands = int(self._settings.number("chain.max_commands", 5)) \
            if self._settings else 5
        stop_on_error = self._settings.flag("chain.stop_on_error", False) \
            if self._settings else False

        chain: list[Command] = []
        for part in parts[:max_commands]:
            result = self._matcher.match(part, window_titles=contexts)
            if result.found and result.command.chainable:
                chain.append(result.command)

        if not chain:
            # Ничего не разобрали — пробуем как одну команду.
            single = self._matcher.match(original, window_titles=contexts)
            if single.found:
                return self._run_command(single.command, original)
            bus.publish(COMMAND_UNKNOWN, text=original)
            self._speak(None, "not_found", False)
            return RunReport(ok=False, error="команда не найдена", phrase=original)

        if len(chain) == 1:
            return self._run_command(chain[0], original)

        log.info("Связка из %d команд: %s", len(chain),
                 ", ".join(c.title for c in chain))

        combined = RunReport(phrase=original, ok=True)
        started = time.time()

        for command in chain:
            report = self._run_command(command, original, from_chain=True)
            combined.steps.extend(report.steps)
            if not report.ok:
                combined.ok = False
                combined.error = report.error
                if stop_on_error:
                    break

        combined.elapsed = time.time() - started
        combined.output = "Связка выполнена" if combined.ok else combined.error

        self._speak(combined.output if combined.ok else None,
                    "ok" if combined.ok else "error", interrupt=False)

        if self._history_handler is not None:
            try:
                self._history_handler(combined)
            except Exception as exc:  # noqa: BLE001
                log.debug("История недоступна: %s", exc)

        return combined

    def _run_command(self, command: Command, phrase: str,
                     from_chain: bool = False) -> RunReport:
        """Выполнить одну команду."""
        report = RunReport(command=command, phrase=phrase)

        # Защита от наложения команд.
        block_overlap = self._settings.flag("exec.block_overlap", True) \
            if self._settings else True
        if block_overlap and self.busy and not from_chain:
            report.ok = False
            report.error = "другая команда ещё выполняется"
            log.info("Команда «%s» пропущена: занят", command.title)
            bus.publish(COMMAND_BLOCKED, command=command, reason=report.error)
            return report

        # Проверка частоты запуска.
        if self._store.rate_limit_exceeded(command):
            report.ok = False
            report.error = "команда запускалась слишком часто"
            bus.publish(COMMAND_BLOCKED, command=command, reason=report.error)
            self._speak("Слишком часто", "", False)
            return report

        left = command.cooldown_left()
        if left > 0 and not from_chain:
            report.ok = False
            report.error = f"подождите {left:.0f} с"
            bus.publish(COMMAND_BLOCKED, command=command, reason=report.error)
            return report

        # Подтверждение для опасных команд.
        if command.confirm and not from_chain:
            if not self._confirm(command):
                report.cancelled = True
                report.ok = False
                report.error = "отменено пользователем"
                bus.publish(COMMAND_CONFIRMED, command=command, confirmed=False)
                self._speak(None, "denied", True)
                self._record(report)
                return report
            bus.publish(COMMAND_CONFIRMED, command=command, confirmed=True)

        # Запуск.
        self._busy.set()
        bus.publish(COMMAND_STARTED, command=command)
        bus.publish(STATUS_MESSAGE, text=f"Выполняю: {command.describe()}",
                    level="busy")

        if self._settings is not None and self._settings.flag("exec.voice_feedback", True):
            self._speak(f"Выполняю {command.title}", "", True)

        started = time.time()
        try:
            report = self._execute_actions(command, phrase, report)
        finally:
            report.elapsed = time.time() - started
            self._busy.clear()
            self._store.touch_run(command.id)
            self._store.save_soon()
            bus.publish(COMMAND_FINISHED, command=command, ok=report.ok,
                        report=report.steps)
            bus.publish(STATUS_MESSAGE, text="Готов", level="ok")

        # Итог.
        if report.ok and not report.cancelled:
            if self._settings is None or self._settings.flag("exec.speak_result", True):
                self._speak(None, "ok", interrupt=False)
        elif not report.cancelled:
            self._speak(report.error if report.error else None, "error", interrupt=False)

        self._last_command = command
        self._last_command_time = time.time()
        self._record(report)
        return report

    def _execute_actions(self, command: Command, phrase: str,
                         report: RunReport | None = None) -> RunReport:
        """Выполнить последовательность действий команды."""
        report = report or RunReport(command=command, phrase=phrase)
        self._executor.reset()

        max_actions = int(self._settings.number("match.max_actions", 200)) \
            if self._settings else 200
        timeout = float(self._settings.number("match.command_timeout", 120)) \
            if self._settings else 120.0

        # Контекст для подстановок.
        context: dict[str, Any] = {
            "phrase": phrase,
            "command": command.title,
            "counter": command.run_count + 1,
        }
        deadline = time.time() + timeout

        # Циклы повторов: индекс → сколько раз повторить.
        repeats: dict[int, int] = {}
        index = 0
        executed = 0

        while index < len(command.actions):
            if self._executor._stop_event.is_set():
                report.ok = False
                report.error = "выполнение прервано"
                break

            if executed >= max_actions:
                report.ok = False
                report.error = f"превышен предел шагов ({max_actions})"
                break

            if time.time() > deadline:
                report.ok = False
                report.error = f"превышено время выполнения ({timeout:.0f} с)"
                break

            action = command.actions[index]
            executed += 1

            action_label = catalog.label_for(action.type)
            bus.publish(ACTION_PROGRESS, index=index + 1,
                        total=len(command.actions),
                        title=action_label)

            if self._settings is not None and self._settings.flag("exec.voice_feedback", True):
                self._speak(action_label, "", True)

            # Количество повторов для шага.
            repeat_count = repeats.get(index, 1)
            if repeat_count <= 0:
                repeats.pop(index, None)
                index += 1
                continue

            success = True
            for attempt in range(repeat_count):
                result = self._executor.execute(action, context)
                report.steps.append((
                    catalog.label_for(action.type),
                    result.ok,
                    result.message,
                ))
                if not result.ok and not result.skipped:
                    success = False

                # Управляющие команды.
                if result.control == "stop":
                    report.ok = False
                    report.error = result.message
                    return report

                if result.control.startswith("repeat:"):
                    try:
                        count = int(result.control.split(":")[1])
                    except (IndexError, ValueError):
                        count = 2
                    if index > 0:
                        repeats[index - 1] = count
                        index -= 1
                        break
                    continue

                if result.control.startswith("repeat_block:"):
                    parts = result.control.split(":")
                    try:
                        count = int(parts[1])
                        start = max(0, int(parts[2]) - 1)
                    except (IndexError, ValueError):
                        count, start = 2, 0
                    for offset in range(start, index):
                        repeats[offset] = count
                    index = max(0, start - 1)
                    break

                if result.control == "skip_rest":
                    index = len(command.actions)
                    break

                if result.output:
                    report.output = result.output

            if not success and not action.continue_on_error:
                report.ok = False
                report.error = report.steps[-1][2] if report.steps else "шаг не выполнен"
                return report

            # Уменьшаем счётчик повторов.
            if index in repeats:
                repeats[index] -= 1
            index += 1

        report.ok = report.ok and not report.error
        return report

    def _confirm(self, command: Command) -> bool:
        """Спросить подтверждение перед выполнением опасной команды."""
        from luxvoice.stt.recognizer import get_recognizer

        timeout = float(self._settings.number("confirm.timeout", 30)) \
            if self._settings else 30.0

        bus.publish(COMMAND_ASK_CONFIRM, command=command)

        # Если есть свой обработчик (например, в интерфейсе) — используем его.
        if self._confirm_handler is not None:
            try:
                return bool(self._confirm_handler(command, timeout))
            except Exception as exc:  # noqa: BLE001
                log.debug("Обработчик подтверждения упал: %s", exc)

        recognizer = get_recognizer(self._settings)

        # Произносим вопрос и слушаем ответ.
        dangerous = self._dangerous_description(command)
        text = f"{dangerous}. Подтвердите выполнение"
        self._speak(text, "", True)

        # Ждём, пока ассистент договорит.
        time.sleep(0.4)

        # Если фразы подтверждения отключены в настройках — только вопрос.
        if self._settings is not None and not self._settings.flag(
                "confirm.voice_reply", True):
            return False

        self._answer_event.clear()
        self._awaiting.set()
        was_listening = recognizer.listening
        if not was_listening:
            recognizer.start_listening()

        try:
            got = self._answer_event.wait(timeout=timeout)
        finally:
            self._awaiting.clear()
            self._answer_event.clear()

        if not got:
            log.info("Подтверждение не получено за %.0f с", timeout)
            return False

        answer = self._answer_text
        self._answer_text = ""
        return self._is_yes(answer)

    def _dangerous_description(self, command: Command) -> str:
        """Что именно собирается сделать команда — для вопроса."""
        for action in command.actions:
            spec = catalog.get_spec(action.type)
            if spec is None or not spec.dangerous:
                continue
            descriptions = {
                "shutdown": "Выключить компьютер",
                "reboot": "Перезагрузить компьютер",
                "sleep": "Перевести компьютер в спящий режим",
                "logout": "Выйти из сеанса",
                "close_window": "Закрыть активное окно",
                "close_app": "Закрыть программу",
                "delete_path": "Удалить файл",
                "run_shell": "Выполнить команду консоли",
                "run_script": "Запустить сценарий",
            }
            text = descriptions.get(action.type)
            if text:
                return text
        return f"Выполнить «{command.title}»"

    def _is_yes(self, text: str) -> bool:
        """Считается ли ответ подтверждением."""
        normalized = normalize_phrase(text)
        if not normalized:
            return False

        raw_yes = self._settings.text("confirm.words_yes",
                                      "да,правильно,подтверждаю,верно,ага") \
            if self._settings else "да,правильно,подтверждаю,верно,ага"
        raw_no = self._settings.text("confirm.words_no",
                                     "нет,отмена,стоп,не надо") \
            if self._settings else "нет,отмена,стоп,не надо"

        def matches(raw: str) -> bool:
            for part in raw.replace(";", ",").split(","):
                word = normalize_phrase(part)
                if word and word in normalized:
                    return True
            return False

        # Отказ проверяем первым: «нет, не подтверждаю» не должно считаться «да».
        if matches(raw_no):
            return False
        return matches(raw_yes)

    def _ask_user(self, question: str, timeout: float) -> str | None:
        """Задать вопрос и получить голосовой ответ."""
        from luxvoice.stt.recognizer import get_recognizer

        recognizer = get_recognizer(self._settings)
        self._answer_event.clear()
        self._answer_text = ""
        self._awaiting.set()

        was_listening = recognizer.listening
        if not was_listening:
            recognizer.start_listening()

        try:
            got = self._answer_event.wait(timeout=timeout)
        finally:
            self._awaiting.clear()
            self._answer_event.clear()

        if not got:
            return None
        answer = self._answer_text
        self._answer_text = ""
        return answer

    def _record(self, report: RunReport) -> None:
        """Записать результат в историю и уведомления."""
        if self._history_handler is not None:
            try:
                self._history_handler(report)
            except Exception as exc:  # noqa: BLE001
                log.debug("История недоступна: %s", exc)

        if self._settings is not None and self._settings.flag(
                "ui.notify_on_command", False) and report.command is not None:
            title = report.command.title or "Команда"
            text = report.summary
            bus.publish(NOTIFY, title=title, text=text)

    # --- Диагностика ------------------------------------------------------

    def diagnostics(self) -> dict[str, object]:
        return {
            "busy": self.busy,
            "silent": self._silent,
            "commands": self._store.count(),
            "last_command": self._last_command.title if self._last_command else "",
            "awaiting_answer": self._awaiting.is_set(),
        }


# --- Единственный экземпляр -------------------------------------------------

_runner: CommandRunner | None = None
_lock = threading.Lock()


def get_runner(settings=None) -> CommandRunner:
    global _runner
    with _lock:
        if _runner is None:
            _runner = CommandRunner(settings)
        elif settings is not None:
            _runner.set_settings(settings)
        return _runner