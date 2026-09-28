"""PC Control: управление компьютером из Telegram.

Работает через ваш личный бот, созданный в @BotFather. Запросы к Telegram
идут напрямую, без сторонних библиотек — только стандартный HTTP.

Правила безопасности:
  * бот реагирует только на сообщения владельца; чужие игнорируются;
  * опасные команды (выключение, перезагрузка, удаление) требуют
    отдельного разрешения в настройках;
  * команды с подтверждением запускаются только кнопкой и после
    явного «Подтвердить»;
  * токен хранится в файле настроек с правами только для владельца.
"""

from __future__ import annotations

import html
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from luxvoice.core.events import (
    TELEGRAM_STARTED,
    TELEGRAM_STOPPED,
    bus,
)

log = logging.getLogger(__name__)

# Адрес API Telegram. Всегда именно этот: подмена ломает подключение.
TELEGRAM_API = "https://api.telegram.org"


@dataclass
class TelegramUser:
    """Пользователь бота."""

    id: int
    name: str = ""
    username: str = ""


@dataclass
class PendingConfirm:
    """Команда, ожидающая подтверждения."""

    command_id: str
    title: str
    chat_id: int
    created: float = field(default_factory=time.time)


class TelegramBot:
    """Бот для управления командами ассистента."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._token = ""
        self._owner_id = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._offset = 0
        self._lock = threading.RLock()
        self._last_error = ""
        self._messages = 0
        self._commands_run = 0
        self._pending: dict[int, PendingConfirm] = {}
        # Кэш меню: чат → (сообщение, разметка, время).
        self._menus: dict[int, tuple[int, list, float]] = {}

    # --- Настройка --------------------------------------------------------

    def configure(self, token: str = "", owner_id: int | str = 0) -> None:
        """Применить токен и идентификатор владельца."""
        self._token = (token or "").strip()
        try:
            self._owner_id = int(str(owner_id).strip() or 0)
        except (TypeError, ValueError):
            self._owner_id = 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def error(self) -> str:
        return self._last_error

    @property
    def ready(self) -> bool:
        return bool(self._token) and self._owner_id > 0

    # --- Запуск и остановка ----------------------------------------------

    def start(self) -> tuple[bool, str]:
        """Запустить бота. Возвращает (успех, сообщение)."""
        if self._running:
            return True, ""

        if not self._token:
            return False, ("не задан токен бота. Создайте бота в @BotFather "
                           "командой /newbot и вставьте токен")
        if not self._owner_id:
            return False, ("не задан ваш Telegram ID. Узнайте его у @userinfobot "
                           "или напишите боту любое сообщение — он ответит")

        # Проверяем токен до запуска потока: понятная ошибка лучше
        # бесконечных попыток в фоне.
        ok, message = self._check_token()
        if not ok:
            return False, message

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop,
                                        name="telegram-bot", daemon=True)
        self._thread.start()
        self._running = True
        bus.publish(TELEGRAM_STARTED)
        log.info("Бот Telegram запущен (владелец %s)", self._owner_id)

        self._send(self._owner_id,
                   "Ассистент на связи. Отправьте /menu для списка команд "
                   "или напишите команду текстом.")
        return True, ""

    def stop(self) -> None:
        """Остановить бота."""
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        bus.publish(TELEGRAM_STOPPED)
        log.info("Бот Telegram остановлен")

    def _check_token(self) -> tuple[bool, str]:
        """Проверить токен через getMe."""
        import requests

        # Сначала отделяем проблемы сети от неверного токена:
        # это разные причины и исправляются по-разному.
        try:
            requests.get(f"{TELEGRAM_API}/bot{self._token}/getMe", timeout=12)
        except requests.exceptions.SSLError:
            return False, ("ошибка защищённого соединения с Telegram. "
                           "Проверьте системное время и сертификаты")
        except requests.exceptions.ConnectionError:
            return False, ("нет соединения с api.telegram.org. "
                           "Проверьте интернет: возможно, Telegram "
                           "заблокирован у провайдера или нужен VPN")
        except requests.exceptions.Timeout:
            return False, "Telegram не отвечает — превышено время ожидания"
        except Exception as exc:  # noqa: BLE001
            return False, f"не удалось связаться с Telegram: {exc}"

        response = self._api("getMe")
        if response is None:
            return False, "Telegram не ответил на проверку токена"
        if not response.get("ok"):
            description = str(response.get("description", "неизвестная ошибка"))
            if "Unauthorized" in description:
                return False, ("токен неверный. Скопируйте его заново "
                               "из @BotFather — он выглядит как "
                               "1234567890:AAH...")
            return False, f"Telegram ответил ошибкой: {description}"

        username = (response.get("result") or {}).get("username", "")
        log.info("Бот подтверждён: @%s", username)
        return True, ""

    # --- Работа с API -----------------------------------------------------

    def _api(self, method: str, params: dict[str, Any] | None = None,
             timeout: float = 30.0) -> dict[str, Any] | None:
        """Вызвать метод Telegram API."""
        import requests

        if not self._token:
            return None

        try:
            response = requests.post(
                f"{TELEGRAM_API}/bot{self._token}/{method}",
                json=params or {}, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            log.debug("Сбой связи с Telegram: %s", exc)
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        if not data.get("ok"):
            self._last_error = str(data.get("description", ""))
        return data

    def _send(self, chat_id: int, text: str,
              reply_markup: dict | None = None) -> int:
        """Отправить сообщение. Возвращает номер сообщения."""
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            params["reply_markup"] = reply_markup

        result = self._api("sendMessage", params)
        if result and result.get("ok"):
            return int(result["result"]["message_id"])
        return 0

    def _edit(self, chat_id: int, message_id: int, text: str,
              reply_markup: dict | None = None) -> bool:
        """Изменить текст сообщения — для обновления меню."""
        if not message_id:
            return False
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4000],
            "parse_mode": "HTML",
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        result = self._api("editMessageText", params)
        return bool(result and result.get("ok"))

    def _answer_callback(self, callback_id: str, text: str = "") -> None:
        """Ответить на нажатие кнопки — убирает «часики» на кнопке."""
        self._api("answerCallbackQuery",
                  {"callback_query_id": callback_id, "text": text[:200]})

    # --- Основной цикл ----------------------------------------------------

    def _poll_loop(self) -> None:
        """Слушать обновления Telegram."""
        interval = int(self._settings.number("plugins.telegram_poll_interval", 2)) \
            if self._settings else 2

        while not self._stop_event.is_set():
            try:
                result = self._api("getUpdates", {
                    "offset": self._offset,
                    "timeout": 20,
                    "allowed_updates": ["message", "callback_query"],
                }, timeout=30)

                if result and result.get("ok"):
                    for update in result.get("result", []):
                        self._offset = max(self._offset,
                                           int(update.get("update_id", 0)) + 1)
                        try:
                            self._handle_update(update)
                        except Exception as exc:  # noqa: BLE001
                            log.exception("Ошибка обработки обновления: %s", exc)
                    self._last_error = ""
                else:
                    time.sleep(max(1, interval))
            except Exception as exc:  # noqa: BLE001
                log.exception("Сбой цикла опроса Telegram: %s", exc)
                time.sleep(5)

    def _handle_update(self, update: dict[str, Any]) -> None:
        """Обработать одно обновление."""
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return

        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        chat_id = int(message.get("chat", {}).get("id", 0))
        user_id = int(message.get("from", {}).get("id", 0))

        # Чужие сообщения игнорируем — это и есть защита доступа.
        if self._owner_id and user_id != self._owner_id:
            log.warning("Сообщение от постороннего пользователя %s "
                        "(чат %s) проигнорировано", user_id, chat_id)
            self._send(chat_id, "Это личный бот. Доступ закрыт.")
            return

        text = str(message.get("text", "") or "").strip()

        # Голосовое сообщение.
        if "voice" in message or "audio" in message:
            self._handle_voice(chat_id, message)
            return

        if not text:
            return

        self._messages += 1

        if text.startswith("/start"):
            self._send(chat_id,
                       f"<b>LuxVoice</b>\n\n"
                       f"Ваш ID: <code>{user_id}</code>\n"
                       f"Отправьте /menu для списка команд "
                       f"или напишите фразу — например «сделай громче».")
            return

        if text.startswith("/menu") or text.lower() == "меню":
            self._show_menu(chat_id)
            return

        if text.startswith("/status") or text.lower() == "статус":
            self._show_status(chat_id)
            return

        if text.startswith("/stop"):
            self._send(chat_id, "Останавливаю всё")
            self._stop_all()
            return

        if text.startswith("/help"):
            self._send(chat_id, self._help_text())
            return

        # Убираем обращение, если оно есть.
        phrase = self._strip_prefix(text)
        self._run_phrase(chat_id, phrase)

    def _strip_prefix(self, text: str) -> str:
        """Убрать обращение из начала фразы."""
        try:
            from luxvoice.core.matcher import get_matcher
            matcher = get_matcher(settings=self._settings)
            remainder, had = matcher.strip_prefix(text)
            if had and remainder:
                return remainder
        except Exception:  # noqa: BLE001
            pass
        return text

    def _help_text(self) -> str:
        return (
            "<b>Управление ассистентом</b>\n\n"
            "/menu — список команд кнопками\n"
            "/status — состояние ассистента\n"
            "/stop — остановить всё\n"
            "/help — эта справка\n\n"
            "Можно просто написать фразу — например «сделай громче» "
            "или «открой ютуб». Команды берутся из редактора программы.\n\n"
            "Команды с подтверждением запускаются только из меню."
        )

    def _show_status(self, chat_id: int) -> None:
        """Показать состояние ассистента."""
        lines = ["<b>Состояние ассистента</b>", ""]

        try:
            from luxvoice.stt.recognizer import get_recognizer
            diag = get_recognizer(self._settings).diagnostics()
            lines.append(f"Микрофон: "
                         f"{'включён' if diag.get('listening') else 'выключен'}")
            lines.append(f"Распознавание: {diag.get('engine', '—')}"
                         f"{' (готово)' if diag.get('engine_ready') else ''}")
            lines.append(f"Распознано фраз: {diag.get('recognized', 0)}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Распознавание: ошибка ({exc})")

        try:
            from luxvoice.sysint.audio import get_audio
            audio = get_audio()
            lines.append(f"Громкость: {audio.volume()}%"
                         + (" (без звука)" if audio.muted() else ""))
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.sysint.windows import get_windows
            active = get_windows().active_window()
            if active is not None:
                title = html.escape(active.title[:60]) if active.title else active.app
                lines.append(f"Активное окно: {title or '—'}")
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.core.store import get_store
            stats = get_store().statistics()
            lines.append(f"Команд в редакторе: {stats['commands']}")
        except Exception:  # noqa: BLE001
            pass

        lines.append(f"Сообщений получено: {self._messages}")
        lines.append(f"Команд выполнено: {self._commands_run}")

        self._send(chat_id, "\n".join(lines))

    # --- Меню -------------------------------------------------------------

    def _show_menu(self, chat_id: int) -> None:
        """Показать меню команд кнопками."""
        from luxvoice.core.store import get_store

        store = get_store()
        collections = store.collections()

        buttons: list[list[dict[str, str]]] = []

        for collection in collections[:8]:
            count = len(store.commands_in(collection.id))
            if not count:
                continue
            mark = "" if collection.enabled else " (выкл.)"
            buttons.append([{
                "text": f"{collection.title}{mark} · {count}",
                "callback_data": f"c:{collection.id}",
            }])

        buttons.append([
            {"text": "Статус", "callback_data": "status"},
            {"text": "Обновить меню", "callback_data": "refresh"},
        ])

        text = ("<b>Команды ассистента</b>\n\n"
                "Выберите коллекцию, затем команду. "
                "Команды с подтверждением требуют нажатия кнопки.")

        message_id = self._send(chat_id, text,
                                {"inline_keyboard": buttons})
        if message_id:
            self._menus[chat_id] = (message_id, buttons, time.time())

    def _show_collection(self, chat_id: int, collection_id: str,
                         message_id: int) -> None:
        """Показать команды внутри коллекции."""
        from luxvoice.core.store import get_store

        store = get_store()
        node = store.find_node(collection_id)
        if node is None:
            self._edit(chat_id, message_id, "Коллекция не найдена",
                       {"inline_keyboard": [[
                           {"text": "Назад", "callback_data": "menu"}]]})
            return

        commands = [c for c in store.commands_in(collection_id) if c.enabled]

        buttons: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []
        for command in commands[:40]:
            label = command.title or command.primary_phrase() or "Команда"
            if command.confirm:
                label = f"{label} ⚠"
            row.append({"text": label[:40],
                        "callback_data": f"r:{command.id}"})
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        if not buttons:
            buttons.append([{"text": "Здесь нет команд",
                             "callback_data": "menu"}])

        # Подпапки.
        for child in node.children[:6]:
            count = len(store.commands_in(child.id))
            if count:
                buttons.append([{
                    "text": f"📁 {child.title} · {count}",
                    "callback_data": f"c:{child.id}",
                }])

        buttons.append([{"text": "← Назад", "callback_data": "menu"}])

        self._edit(chat_id, message_id,
                   f"<b>{html.escape(node.title)}</b>\n\n"
                   f"Команд: {len(commands)}",
                   {"inline_keyboard": buttons})

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        """Обработать нажатие кнопки."""
        callback_id = str(callback.get("id", ""))
        user_id = int(callback.get("from", {}).get("id", 0))
        chat_id = int(callback.get("message", {}).get("chat", {}).get("id", 0))
        message_id = int(callback.get("message", {}).get("message_id", 0))
        data = str(callback.get("data", ""))

        if self._owner_id and user_id != self._owner_id:
            self._answer_callback(callback_id, "Доступ закрыт")
            return

        self._answer_callback(callback_id)

        if data == "menu" or data == "refresh":
            if data == "refresh":
                self._prepare_menu()
            self._show_menu(chat_id)
            return

        if data == "status":
            self._show_status(chat_id)
            return

        if data.startswith("c:"):
            # Открыть коллекцию или папку.
            self._show_collection(chat_id, data[2:], message_id)
            return

        if data.startswith("r:"):
            # Запустить команду.
            self._run_from_menu(chat_id, data[2:])
            return

        if data.startswith("yes:"):
            # Подтверждение запуска.
            pending = self._pending.pop(chat_id, None)
            if pending is None or pending.command_id != data[4:]:
                self._send(chat_id, "Подтверждение устарело. Откройте меню заново.")
                return
            self._execute(chat_id, data[4:], confirmed=True)
            return

        if data.startswith("no:"):
            self._pending.pop(chat_id, None)
            self._send(chat_id, "Отменено")
            return

    def _prepare_menu(self) -> None:
        """Обновить меню после изменения команд в редакторе."""
        try:
            from luxvoice.actions.runner import get_runner
            get_runner(self._settings).refresh()
        except Exception:  # noqa: BLE001
            pass

    def _run_from_menu(self, chat_id: int, command_id: str) -> None:
        """Запустить команду, выбранную в меню."""
        from luxvoice.core.store import get_store

        command = get_store().get(command_id)
        if command is None:
            self._send(chat_id, "Команда не найдена — возможно, её удалили")
            return

        if not command.enabled:
            self._send(chat_id, f"Команда «{command.title}» выключена "
                                "в редакторе")
            return

        if not command.actions:
            self._send(chat_id, f"В команде «{command.title}» нет действий")
            return

        # Опасные команды из мессенджера — только с явного разрешения.
        from luxvoice.actions import catalog

        if self._settings is not None and not self._settings.flag(
                "plugins.telegram_allow_dangerous", False):
            dangerous = [a for a in command.actions
                         if catalog.is_dangerous(a.type)]
            if dangerous:
                self._send(
                    chat_id,
                    f"⚠ Команда «<b>{html.escape(command.title)}</b>» "
                    "содержит опасные действия и запрещена из Telegram.\n\n"
                    "Разрешить можно в настройках: «Дополнения» → "
                    "«Разрешить опасные команды из Telegram».")
                return

        # Команды с подтверждением — двумя шагами.
        if command.confirm:
            self._pending[chat_id] = PendingConfirm(
                command_id=command.id, title=command.title, chat_id=chat_id)
            self._send(
                chat_id,
                f"Подтвердите выполнение команды "
                f"«<b>{html.escape(command.title)}</b>»",
                {"inline_keyboard": [[
                    {"text": "Подтвердить", "callback_data": f"yes:{command.id}"},
                    {"text": "Отмена", "callback_data": f"no:{command.id}"},
                ]]})
            return

        self._execute(chat_id, command_id, confirmed=False)

    def _execute(self, chat_id: int, command_id: str, confirmed: bool) -> None:
        """Выполнить команду и сообщить результат."""
        from luxvoice.actions.runner import get_runner
        from luxvoice.core.store import get_store

        command = get_store().get(command_id)
        if command is None:
            self._send(chat_id, "Команда не найдена")
            return

        self._send(chat_id, f"Выполняю: <b>{html.escape(command.title)}</b>")

        try:
            runner = get_runner(self._settings)
            report = runner._run_command(
                command,
                command.primary_phrase() or command.title,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка выполнения команды из Telegram")
            self._send(chat_id, f"Ошибка выполнения: {html.escape(str(exc))}")
            return

        self._commands_run += 1

        lines: list[str] = []
        if report.ok:
            lines.append(f"✓ <b>{html.escape(command.title)}</b> — выполнено")
        else:
            lines.append(f"✗ <b>{html.escape(command.title)}</b> — "
                         f"{html.escape(report.error or 'ошибка')}")

        if report.steps:
            lines.append("")
            for title, ok, message in report.steps[:12]:
                mark = "✓" if ok else "✗"
                line = f"{mark} {html.escape(title)}"
                if message:
                    line += f" — {html.escape(message[:80])}"
                lines.append(line)

        lines.append("")
        lines.append(f"Время: {report.elapsed:.1f} с")

        self._send(chat_id, "\n".join(lines))

    # --- Голосовые сообщения ---------------------------------------------

    def _handle_voice(self, chat_id: int, message: dict[str, Any]) -> None:
        """Распознать голосовое сообщение и выполнить как команду."""
        if self._settings is not None and not self._settings.flag(
                "plugins.telegram_voice", False):
            self._send(chat_id,
                       "Голосовые сообщения выключены. Включите их "
                       "в настройках: «Дополнения» → «Принимать голосовые».")
            return

        voice = message.get("voice") or message.get("audio") or {}
        file_id = str(voice.get("file_id", ""))
        if not file_id:
            return

        self._send(chat_id, "Слушаю голосовое…")

        # Получаем путь к файлу.
        result = self._api("getFile", {"file_id": file_id})
        if not result or not result.get("ok"):
            self._send(chat_id, "Не удалось получить файл из Telegram")
            return

        file_path = str(result["result"].get("file_path", ""))
        if not file_path:
            return

        # Скачиваем.
        import tempfile
        from pathlib import Path

        import requests

        suffix = Path(file_path).suffix or ".ogg"
        handle, local_path = tempfile.mkstemp(suffix=suffix)
        import os
        os.close(handle)

        try:
            response = requests.get(
                f"{TELEGRAM_API}/file/bot{self._token}/{file_path}",
                timeout=60)
            if response.status_code != 200:
                self._send(chat_id, "Не удалось скачать голосовое сообщение")
                return
            Path(local_path).write_bytes(response.content)
        except Exception as exc:  # noqa: BLE001
            self._send(chat_id, f"Сбой загрузки: {html.escape(str(exc))}")
            return

        # Распознаём.
        try:
            from luxvoice.stt.recognizer import get_recognizer
            recognizer = get_recognizer(self._settings)
            recognition = recognizer.recognize_file(local_path)

            if recognition.error:
                self._send(chat_id, f"Не удалось распознать: "
                                    f"{html.escape(recognition.error)}")
                return
            if not recognition.text:
                self._send(chat_id, "Речь не распознана")
                return

            text = recognition.text
            self._send(chat_id, f"Услышано: «{html.escape(text)}»")
            self._run_phrase(chat_id, self._strip_prefix(text))
        finally:
            try:
                Path(local_path).unlink()
            except OSError:
                pass

    # --- Выполнение по тексту --------------------------------------------

    def _run_phrase(self, chat_id: int, phrase: str) -> None:
        """Найти и выполнить команду по фразе."""
        from luxvoice.actions.runner import get_runner

        try:
            runner = get_runner(self._settings)
            report = runner.handle_text(phrase)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка выполнения фразы из Telegram")
            self._send(chat_id, f"Ошибка: {html.escape(str(exc))}")
            return

        if report is None:
            # Фраза распознана как служебная (управление режимом).
            self._send(chat_id, "Команда обработана")
            return

        self._commands_run += 1

        if report.ok:
            self._send(chat_id,
                       f"✓ <b>{html.escape(report.command.title if report.command else 'Готово')}</b>")
        else:
            self._send(
                chat_id,
                f"✗ {html.escape(report.error or 'не удалось выполнить')}")

    def _stop_all(self) -> None:
        """Остановить выполнение и речь."""
        try:
            from luxvoice.actions.runner import get_runner
            get_runner(self._settings).stop_all()
        except Exception:  # noqa: BLE001
            pass

    # --- Уведомления ------------------------------------------------------

    def notify(self, title: str, text: str) -> None:
        """Отправить уведомление владельцу."""
        if not self._running or not self._owner_id:
            return
        if self._settings is not None and not self._settings.flag(
                "plugins.telegram_notify", False):
            return
        self._send(self._owner_id,
                   f"<b>{html.escape(title)}</b>\n{html.escape(text)}")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "has_token": bool(self._token),
            "owner_id": self._owner_id,
            "messages": self._messages,
            "commands_run": self._commands_run,
            "error": self._last_error,
        }


# --- Единственный экземпляр -------------------------------------------------

_bot: TelegramBot | None = None
_lock = threading.Lock()


def get_bot(settings=None) -> TelegramBot:
    global _bot
    with _lock:
        if _bot is None:
            _bot = TelegramBot(settings)
        elif settings is not None:
            _bot._settings = settings
        return _bot