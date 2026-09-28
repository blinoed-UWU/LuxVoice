"""Главное окно: микрофон, история, панель управления, чат с нейросетью.

Слева — навигация, в центре — содержимое раздела, справа при
необходимости — панель управления: кнопка микрофона, индикатор уровня,
режимы, громкость ассистента.

Окно не блокирует работу: все длительные операции (распознавание,
озвучка, выполнение команд) идут в отдельных потоках, а интерфейс
получает результаты через шину событий.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from luxvoice import __version__
from luxvoice.core import paths, schema
from luxvoice.core.events import (
    ACTION_PROGRESS,
    APP_QUIT,
    COMMAND_FINISHED,
    COMMAND_MATCHED,
    COMMAND_UNKNOWN,
    MODE_CHANGED,
    NOTIFY,
    STATUS_MESSAGE,
    STT_ERROR,
    STT_FINAL,
    STT_LEVEL,
    STT_PARTIAL,
    STT_STARTED,
    STT_STOPPED,
    TTS_FINISHED,
    TTS_STARTED,
    bus,
)
from luxvoice.core.history import HistoryEntry, get_history
from luxvoice.core.i18n import get_language, set_language, tr
from luxvoice.core.settings import Settings
from luxvoice.core.store import get_store
from luxvoice.plugins.avatar import AvatarController
from luxvoice.plugins.telegram_bot import get_bot
from luxvoice.ui.command_editor import CommandEditor
from luxvoice.ui.settings_dialog import SettingsDialog
from luxvoice.ui.theme import build_theme, stylesheet

log = logging.getLogger(__name__)


class LevelIndicator(QWidget):
    """Индикатор уровня сигнала микрофона."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LevelBar")
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._level = 0.0
        self._peak = 0.0

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self._peak = max(self._peak * 0.92, self._level)
        self.update()

    def reset(self) -> None:
        self._level = 0.0
        self._peak = 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        from PyQt6.QtGui import QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        theme = getattr(self.window(), "_theme", None)
        accent = QColor(theme.accent if theme else "#4f8cff")
        background = QColor(theme.surface_alt if theme else "#2b2f33")

        painter.fillRect(self.rect(), background)
        if self._level > 0.001:
            width = int(self.width() * self._level)
            painter.fillRect(0, 0, width, self.height(), accent)
        if self._peak > 0.001:
            x = int(self.width() * self._peak)
            painter.fillRect(max(0, x - 2), 0, 2, self.height(),
                             QColor(theme.text if theme else "#ffffff"))
        painter.end()


class ListenButton(QPushButton):
    """Кнопка микрофона с индикацией состояния."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.setCheckable(True)
        self.setFixedSize(52, 52)
        self.setToolTip(f"{tr('Микрофон включён')} / {tr('Микрофон выключен')}")
        self._state = "idle"
        self._pulse = 0.0
        self.setIconSize(self.size() * 0.55)

        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._on_pulse)

    def set_state(self, state: str) -> None:
        self._state = state
        self.setChecked(state in ("listening", "busy"))
        self._refresh()
        if state in ("listening", "busy"):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._pulse = 0.0

    def _on_pulse(self) -> None:
        self._pulse = (self._pulse + 0.08) % 1.0
        self._refresh()

    def _refresh(self) -> None:
        from luxvoice.ui.theme import icon_svg

        theme = getattr(self.window(), "_theme", None)
        accent = theme.accent if theme else "#4f8cff"
        text_color = theme.text if theme else "#ffffff"

        if self._state == "listening":
            color = accent
        elif self._state == "busy":
            color = theme.warning if theme else "#ffb020"
        elif self._state == "error":
            color = theme.error if theme else "#ff5c5c"
        else:
            color = theme.text_faint if theme else "#767e8a"

        name = "mic" if self._state in ("listening", "busy") else "mic_off"
        svg = icon_svg(name, color, 24)
        pixmap = QPixmap()
        pixmap.loadFromData(svg.encode("utf-8"), "SVG")
        self.setIcon(QIcon(pixmap))

        if self._state in ("listening", "busy"):
            import math
            # Лёгкая пульсация вокруг кнопки — видно, что микрофон работает.
            glow = 0.5 + 0.5 * math.sin(self._pulse * 2 * math.pi)
            self.setStyleSheet(
                f"QPushButton#IconButton {{ border: 2px solid "
                f"rgba({_rgb(accent)}, {0.4 + 0.5 * glow:.2f}); "
                f"border-radius: 26px; background: transparent; }}")
        else:
            self.setStyleSheet(
                "QPushButton#IconButton { border: 2px solid "
                f"{theme.border if theme else '#3a3f45'}; border-radius: 26px; "
                "background: transparent; }")


def _rgb(color: str) -> str:
    color = (color or "#4f8cff").lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    try:
        return f"{int(color[0:2], 16)}, {int(color[2:4], 16)}, {int(color[4:6], 16)}"
    except (ValueError, IndexError):
        return "79, 140, 255"


class HistoryPage(QWidget):
    """Страница истории выполненных команд."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._history = get_history(settings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Верхняя панель
        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Поиск по истории…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.reload)
        top.addWidget(self._search, 1)

        refresh = QPushButton(tr("Обновить"))
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)

        export = QPushButton(tr("Экспорт"))
        export.clicked.connect(self._export)
        top.addWidget(export)

        clear = QPushButton(tr("Очистить"))
        clear.setObjectName("Danger")
        clear.clicked.connect(self._clear)
        top.addWidget(clear)
        layout.addLayout(top)

        # Сводка
        self._summary = QLabel("")
        self._summary.setObjectName("Hint")
        layout.addWidget(self._summary)

        # Список
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentRowChanged.connect(self._show_details)
        layout.addWidget(self._list, 2)

        # Подробности
        self._details = QTextBrowser()
        self._details.setMaximumHeight(180)
        self._details.setPlaceholderText(
            "Выберите запись, чтобы увидеть, что именно выполнено")
        layout.addWidget(self._details, 1)

        self.reload()

    def reload(self) -> None:
        """Перечитать историю."""
        query = self._search.text().strip()
        entries = (self._history.search(query, limit=500) if query
                   else self._history.recent(limit=500))

        self._list.clear()
        for entry in entries:
            mark = "✓" if entry.ok and not entry.cancelled else (
                "⊘" if entry.cancelled else "✗")
            score = f" · {entry.score:.0f}%" if entry.score else ""
            text = (f"{mark}  {entry.when}   {entry.phrase or '—'}"
                    f"   →   {entry.command or 'не найдена'}{score}")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            if not entry.ok and not entry.cancelled:
                item.setForeground(QColor(
                    getattr(self.window(), "_theme", None).error
                    if getattr(self.window(), "_theme", None) else "#ff5c5c"))
            elif entry.cancelled:
                item.setForeground(QColor(
                    getattr(self.window(), "_theme", None).text_dim
                    if getattr(self.window(), "_theme", None) else "#a6adb8"))
            self._list.addItem(item)

        stats = self._history.statistics(days=30)
        if stats:
            self._summary.setText(
                f"Записей за 30 дней: {stats.get('total', 0)} · "
                f"успешных: {stats.get('ok', 0)} "
                f"({stats.get('success_rate', 0)}%) · "
                f"среднее время: {stats.get('avg_seconds', 0)} с")
        else:
            self._summary.setText("История пока пуста")

    def _show_details(self, row: int) -> None:
        item = self._list.item(row)
        if item is None:
            self._details.clear()
            return

        entry_id = item.data(Qt.ItemDataRole.UserRole)
        for entry in self._history.recent(limit=500):
            if entry.id != entry_id:
                continue
            lines = [
                f"Когда: {entry.when}",
                f"Услышано: {entry.phrase or '—'}",
                f"Команда: {entry.command or 'не найдена'}",
                f"Совпадение: {entry.score:.0f}%",
                f"Результат: {entry.status}",
                f"Время выполнения: {entry.elapsed:.2f} с",
            ]
            if entry.error:
                lines.append(f"Ошибка: {entry.error}")
            if entry.steps:
                lines.append("")
                lines.append("Шаги:")
                for title, ok, message in entry.steps:
                    mark = "✓" if ok else "✗"
                    lines.append(f"  {mark} {title}" + (f" — {message}" if message else ""))
            self._details.setPlainText("\n".join(lines))
            return

    def _export(self) -> None:
        import json
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить историю",
            str(Path.home() / "luxvoice-история.json"), "JSON (*.json)")
        if not path:
            return
        try:
            data = self._history.export_rows(limit=10000)
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            QMessageBox.information(self, "Экспорт", f"История сохранена:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить: {exc}")

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self, "Очистить историю",
            "Удалить всю историю выполненных команд?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            removed = self._history.clear()
            log.info("История очищена: удалено записей %d", removed)
            self.reload()


class PacksPage(QWidget):
    """Страница дополнений: паки команд, плагины, модели."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._store = get_store()

        from luxvoice.packs.manager import get_pack_manager
        self._manager = get_pack_manager(self._store)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel(tr("Паки команд"))
        title.setObjectName("Title")
        layout.addWidget(title)

        hint = QLabel(
            "Готовые наборы команд для программ, сайтов и сервисов. "
            "После установки пак становится обычной коллекцией — "
            "его можно менять и выключать.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Поиск по пакам…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self.reload)
        top.addWidget(self._search, 1)

        self._category = QComboBox()
        self._category.addItem("Все категории", "")
        from luxvoice.packs.manager import categories
        for category in categories():
            self._category.addItem(category, category)
        self._category.currentIndexChanged.connect(self.reload)
        top.addWidget(self._category)
        layout.addLayout(top)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list, 1)

        buttons = QHBoxLayout()
        install = QPushButton(tr("Установить"))
        install.setObjectName("Primary")
        install.clicked.connect(self._install)
        buttons.addWidget(install)

        update = QPushButton("Обновить пак")
        update.clicked.connect(self._update)
        buttons.addWidget(update)

        remove = QPushButton("Удалить пак")
        remove.setObjectName("Danger")
        remove.clicked.connect(self._uninstall)
        buttons.addWidget(remove)

        buttons.addStretch(1)

        import_button = QPushButton("Загрузить свой пак")
        import_button.setToolTip("Установить пак из файла JSON")
        import_button.clicked.connect(self._import_pack)
        buttons.addWidget(import_button)
        layout.addLayout(buttons)

        self._status = QLabel("")
        self._status.setObjectName("Hint")
        layout.addWidget(self._status)

        self.reload()

    def reload(self) -> None:
        from luxvoice.packs.manager import (find_packs, pack_command_count,
                                            pack_options)

        self._list.clear()
        packs = find_packs(self._search.text(),
                           str(self._category.currentData() or ""))

        for pack in packs:
            count = pack_command_count(pack)
            installed = self._manager.is_installed(pack.key)
            needs_update = self._manager.needs_update(pack)

            if needs_update:
                state = "⟳ доступно обновление"
            elif installed:
                state = "✓ установлен"
            else:
                state = "· не установлен"

            text = (f"{pack.title}   [{pack.category}]   "
                    f"{count} команд   {state}")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, pack.key)
            tooltip = pack.description
            if pack.requires_app:
                tooltip += f"\n\nТребуется: {pack.requires_app}"
            item.setToolTip(tooltip)
            self._list.addItem(item)

        total = sum(pack_command_count(p) for p in packs)
        self._status.setText(
            f"Паков: {len(packs)} · команд в них: {total} · "
            f"языки: русский и английский")

    def _selected_pack(self):
        from luxvoice.packs.manager import PACKS_BY_KEY

        item = self._list.currentItem()
        if item is None:
            return None
        return PACKS_BY_KEY.get(str(item.data(Qt.ItemDataRole.UserRole)))

    def _install(self) -> None:
        pack = self._selected_pack()
        if pack is None:
            QMessageBox.information(self, "Установка",
                                    "Выберите пак из списка.")
            return

        if self._manager.is_installed(pack.key):
            answer = QMessageBox.question(
                self, "Пак уже установлен",
                f"Пак «{pack.title}» уже установлен.\n\n"
                "Установить заново? Существующие команды не дублируются.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        stats = self._manager.install(pack)
        self._store.save()
        self.reload()
        QMessageBox.information(
            self, "Пак установлен",
            f"«{pack.title}»\n\n"
            f"Добавлено команд: {stats['added']}\n"
            f"Пропущено (уже есть): {stats['skipped']}\n\n"
            "Команды появятся в редакторе — их можно менять.")

    def _update(self) -> None:
        pack = self._selected_pack()
        if pack is None or not self._manager.is_installed(pack.key):
            QMessageBox.information(self, "Обновление",
                                    "Выберите установленный пак.")
            return
        stats = self._manager.update(pack)
        self._store.save()
        self.reload()
        QMessageBox.information(
            self, "Пак обновлён",
            f"Добавлено: {stats['added']}\n"
            f"Обновлено: {stats['updated']}\n"
            f"Сохранено ваших правок: {stats['kept']}")

    def _uninstall(self) -> None:
        pack = self._selected_pack()
        if pack is None or not self._manager.is_installed(pack.key):
            QMessageBox.information(self, "Удаление",
                                    "Выберите установленный пак.")
            return

        answer = QMessageBox.question(
            self, "Удалить пак",
            f"Удалить пак «{pack.title}» вместе с его командами?\n\n"
            "Команды, которые вы изменяли вручную, останутся.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        removed = self._manager.uninstall(pack)
        self._store.save()
        self.reload()
        QMessageBox.information(self, "Пак удалён",
                                f"Удалено команд: {removed}")

    def _import_pack(self) -> None:
        import json
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл пака", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать: {exc}")
            return

        pack = self._manager.import_pack(data)
        if pack is None:
            QMessageBox.warning(self, "Ошибка",
                                "Файл не похож на пак команд LuxVoice.")
            return

        stats = self._manager.install(pack)
        self._store.save()
        self.reload()
        QMessageBox.information(self, "Пак установлен",
                                f"«{pack.title}»: добавлено {stats['added']} команд")


class ChatPage(QWidget):
    """Чат с нейросетью и командная строка."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._history: list[tuple[str, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(tr("Нейросеть"))
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)

        self._mode = QComboBox()
        for key, label in (("ai", tr("Только ИИ")),
                           ("pc", tr("Только управление ПК")),
                           ("combined", tr("Комбинированный"))):
            self._mode.addItem(label, key)
        index = self._mode.findData(self._settings.text("aichat.mode", "combined"))
        self._mode.setCurrentIndex(max(0, index))
        self._mode.setToolTip("Как реагировать на запросы: отвечать словами "
                              "или выполнять команды на компьютере")
        self._mode.currentIndexChanged.connect(
            lambda: self._settings.set("aichat.mode", self._mode.currentData()))
        header.addWidget(self._mode)

        self._provider_label = QLabel("")
        self._provider_label.setObjectName("Hint")
        header.addWidget(self._provider_label)
        layout.addLayout(header)

        self._chat = QTextBrowser()
        self._chat.setObjectName("Chat")
        self._chat.setOpenExternalLinks(True)
        layout.addWidget(self._chat, 1)

        input_row = QHBoxLayout()
        self._input = QTextEdit()
        self._input.setObjectName("ChatInput")
        self._input.setPlaceholderText(
            "Спросите что угодно или напишите команду…")
        self._input.setMaximumHeight(80)
        input_row.addWidget(self._input, 1)

        send = QPushButton(tr("Отправить"))
        send.setObjectName("Primary")
        send.clicked.connect(self._send)
        input_row.addWidget(send)
        layout.addLayout(input_row)

        hint = QLabel(
            "Текстовый чат работает так же, как голосовой: можно спрашивать "
            "и отдавать команды. Нужен ключ провайдера в настройках "
            "или локальная модель.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._refresh_provider()
        self._welcome()

    def _refresh_provider(self) -> None:
        provider = self._settings.text("ai.provider", "openai")
        model = self._settings.text("ai.model", "")
        enabled = self._settings.flag("ai.enabled", False)

        if not enabled:
            self._provider_label.setText("нейросеть выключена")
            return

        keys = self._settings.get("ai.keys") or {}
        key = ""
        if isinstance(keys, dict):
            key = str(keys.get(provider, "") or "")
        if not key and provider in ("ollama", "lmstudio"):
            key = "локальная"

        self._provider_label.setText(
            f"{provider}" + (f" · {model}" if model else "")
            + ("" if key else " · нет ключа"))

    def _welcome(self) -> None:
        self._chat.clear()
        self._append("Ассистент",
                     "Чат готов. Спросите что-нибудь или напишите команду — "
                     "например «открой ютуб».")

    def _append(self, who: str, text: str) -> None:
        theme = getattr(self.window(), "_theme", None)
        accent = theme.accent if theme else "#4f8cff"
        dim = theme.text_dim if theme else "#a6adb8"
        color = accent if who == "Вы" else dim
        safe = (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))
        self._chat.append(
            f'<p style="color:{color}; margin:6px 0 2px 0;"><b>{who}</b></p>'
            f'<p style="margin:0 0 10px 0;">{safe}</p>')
        self._chat.verticalScrollBar().setValue(
            self._chat.verticalScrollBar().maximum())

    def _send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self._append("Вы", text)
        self._history.append(("user", text))

        mode = self._settings.text("aichat.mode", "combined")

        # В режиме управления ПК сначала пробуем найти команду.
        if mode in ("pc", "combined"):
            try:
                from luxvoice.actions.runner import get_runner
                runner = get_runner(self._settings)
                result = runner.handle_text(text)
                if result is not None:
                    status = "выполнено" if result.ok else f"ошибка: {result.error}"
                    self._append("Ассистент",
                                 f"{result.command.title if result.command else 'команда'}: {status}")
                    self._history.append(("assistant", status))
                    return
            except Exception as exc:  # noqa: BLE001
                log.debug("Обработка команды из чата не удалась: %s", exc)

        if mode == "pc":
            self._append("Ассистент", "Команда не найдена.")
            return

        self._ask_ai(text)

    def _ask_ai(self, text: str) -> None:
        if not self._settings.flag("ai.enabled", False):
            self._append("Ассистент",
                         "Нейросеть выключена. Включите её в настройках: "
                         "«ИИ-провайдер» → «Включить нейросеть».")
            return

        from PyQt6.QtCore import QThread

        self._append("Ассистент", "Думаю…")

        settings = self._settings

        class Worker(QThread):
            done = pyqtSignal(str)

            def run(self) -> None:  # noqa: D102
                try:
                    from luxvoice.ai.client import get_ai_client
                    client = get_ai_client(settings)
                    reply = client.ask(text)
                    self.done.emit(reply or "Пустой ответ")
                except Exception as exc:  # noqa: BLE001
                    self.done.emit(f"Ошибка: {exc}")

        worker = Worker(self)
        worker.done.connect(self._on_ai_reply)
        self._worker = worker
        worker.start()

    def _on_ai_reply(self, text: str) -> None:
        # Убираем строку «Думаю…».
        content = self._chat.toPlainText()
        if content.endswith("Думаю…"):
            self._chat.clear()
            for who, message in self._history:
                self._append("Вы" if who == "user" else "Ассистент", message)
        self._append("Ассистент", text)
        self._history.append(("assistant", text))


class MainWindow(QMainWindow):
    """Главное окно программы."""

    def __init__(self, settings: Settings, app: QApplication) -> None:
        super().__init__()
        self._settings = settings
        self._app = app
        self._store = get_store()
        self._theme = build_theme(settings.text("ui.theme", "dark"),
                                  settings.text("ui.accent", "#4f8cff"),
                                  settings.text("ui.background", ""))
        self._busy = False

        self.setWindowTitle(f"LuxVoice {__version__} — {tr('голосовой ассистент')}")
        self.setMinimumSize(980, 640)
        self.resize(1180, 760)

        self._build_ui()
        self._apply_theme()
        self._connect_events()
        self._setup_tray()
        self._restore_geometry()

        # Дополнения: аватар и Telegram-бот.
        self._avatar = AvatarController(settings)
        self._avatar.start()
        self._telegram = get_bot(settings)
        self._start_telegram_if_needed()

        # Периодическое обновление состояния.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

    # --- Построение -------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Боковая навигация ---
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 14, 10, 14)
        side.setSpacing(4)

        logo = QLabel("LuxVoice")
        logo.setObjectName("Title")
        side.addWidget(logo)

        version_label = QLabel(f"версия {__version__}")
        version_label.setObjectName("Hint")
        side.addWidget(version_label)
        side.addSpacing(12)

        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label, icon in (
            ("home", tr("Главная"), "home"),
            ("editor", tr("Редактор команд"), "edit"),
            ("history", tr("История команд"), "history"),
            ("chat", tr("Нейросеть"), "chat"),
            ("packs", tr("Дополнения"), "plug"),
            ("settings", tr("Настройки"), "settings"),
        ):
            button = QPushButton(label)
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, k=key: self._show_page(k))
            side.addWidget(button)
            self._nav_buttons[key] = button

        side.addStretch(1)

        # Быстрые действия
        quick_label = QLabel("Быстрые действия")
        quick_label.setObjectName("Hint")
        side.addWidget(quick_label)

        for label, slot in (
            ("Стоп", self._panic_stop),
            ("Проверить микрофон", self._test_microphone),
        ):
            button = QPushButton(label)
            button.setObjectName("Ghost")
            button.clicked.connect(slot)
            side.addWidget(button)

        root.addWidget(sidebar)

        # --- Содержимое ---
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        # Шапка
        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.setSpacing(10)

        self._page_title = QLabel(tr("Главная"))
        self._page_title.setObjectName("SectionTitle")
        header_layout.addWidget(self._page_title)
        header_layout.addStretch(1)

        # Режим префикса
        self._prefix_check = QCheckBox("Режим обращения")
        self._prefix_check.setToolTip(
            "Включено: команда выполняется только после обращения по имени")
        self._prefix_check.setChecked(self._settings.flag("stt.prefix_mode", False))
        self._prefix_check.toggled.connect(
            lambda state: self._settings.set("stt.prefix_mode", state))
        header_layout.addWidget(self._prefix_check)

        # Громкость ассистента
        volume_label = QLabel("Громкость:")
        volume_label.setObjectName("Hint")
        header_layout.addWidget(volume_label)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(int(self._settings.number("tts.volume", 80)))
        self._volume_slider.setFixedWidth(110)
        self._volume_slider.valueChanged.connect(
            lambda value: self._settings.set("tts.volume", value, save=False))
        header_layout.addWidget(self._volume_slider)

        # Кнопка микрофона
        self._listen_button = ListenButton()
        self._listen_button.clicked.connect(self._toggle_listening)
        header_layout.addWidget(self._listen_button)

        right.addWidget(header)

        # Страницы
        self._pages = QStackedWidget()
        self._home_page = self._build_home_page()
        self._pages.addWidget(self._home_page)

        self._editor_page = CommandEditor(self._store, self._settings)
        self._editor_page.test_requested.connect(self._run_command_by_id)
        self._pages.addWidget(self._editor_page)

        self._history_page = HistoryPage(self._settings)
        self._pages.addWidget(self._history_page)

        self._chat_page = ChatPage(self._settings)
        self._pages.addWidget(self._chat_page)

        self._packs_page = PacksPage(self._settings)
        self._pages.addWidget(self._packs_page)

        self._settings_page = self._build_settings_page()
        self._pages.addWidget(self._settings_page)

        right.addWidget(self._pages, 1)

        # Строка состояния
        status_bar = QFrame()
        status_bar.setObjectName("StatusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(16, 6, 16, 6)
        status_layout.setSpacing(10)

        self._status_label = QLabel(tr("Готов"))
        status_layout.addWidget(self._status_label)
        status_layout.addStretch(1)

        self._engine_label = QLabel("")
        self._engine_label.setObjectName("Hint")
        status_layout.addWidget(self._engine_label)

        right.addWidget(status_bar)

        root.addLayout(right, 1)

    def _build_home_page(self) -> QWidget:
        """Главная страница: индикатор, история, подсказки."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # Карточка состояния
        card = QFrame()
        card.setObjectName("Card")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(18)

        # Кнопка микрофона крупно
        self._big_listen = QPushButton()
        self._big_listen.setObjectName("IconButton")
        self._big_listen.setCheckable(True)
        self._big_listen.setFixedSize(84, 84)
        self._big_listen.clicked.connect(self._toggle_listening)
        card_layout.addWidget(self._big_listen)

        info = QVBoxLayout()
        info.setSpacing(4)

        self._state_label = QLabel(tr("Не слушаю"))
        self._state_label.setObjectName("Title")
        info.addWidget(self._state_label)

        self._heard_label = QLabel("Скажите команду — например «сделай громче»")
        self._heard_label.setObjectName("Subtitle")
        self._heard_label.setWordWrap(True)
        info.addWidget(self._heard_label)

        info.addSpacing(6)
        self._level_indicator = LevelIndicator()
        info.addWidget(self._level_indicator)

        self._diagnostics_label = QLabel("")
        self._diagnostics_label.setObjectName("Hint")
        self._diagnostics_label.setWordWrap(True)
        info.addWidget(self._diagnostics_label)

        card_layout.addLayout(info, 1)
        layout.addWidget(card)

        # Последние команды
        recent_label = QLabel("Последние команды")
        recent_label.setObjectName("SectionTitle")
        layout.addWidget(recent_label)

        self._recent_list = QListWidget()
        self._recent_list.setAlternatingRowColors(True)
        layout.addWidget(self._recent_list, 1)

        # Подсказки
        tips = QLabel(
            "Подсказки: скажите «сверни все окна», «сделай тише», "
            "«открой ютуб», «сколько места на диске». "
            "Свои команды создаются в редакторе — "
            "там можно собрать сценарий из десятка действий.")
        tips.setObjectName("Hint")
        tips.setWordWrap(True)
        layout.addWidget(tips)

        return page

    def _build_settings_page(self) -> QWidget:
        """Страница настроек внутри главного окна."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel(tr("Настройки"))
        title.setObjectName("Title")
        layout.addWidget(title)

        hint = QLabel(
            f"Всего доступно {len(schema.all_settings())} настроек "
            f"в {len(schema.sections())} разделах. Обычные настройки видны сразу, "
            "дополнительные и экспертные открываются кнопкой в окне настроек.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        open_button = QPushButton("Открыть окно настроек")
        open_button.setObjectName("Primary")
        open_button.clicked.connect(self._open_settings_dialog)
        layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignLeft)

        # Быстрые настройки прямо здесь
        quick = QFrame()
        quick.setObjectName("Card")
        quick_layout = QVBoxLayout(quick)
        quick_layout.setContentsMargins(14, 12, 14, 12)
        quick_layout.setSpacing(8)

        quick_title = QLabel("Часто используемое")
        quick_title.setObjectName("SectionTitle")
        quick_layout.addWidget(quick_title)

        for key in ("app.autostart", "app.start_listening", "tts.enabled",
                    "match.window_context", "app.reply_unknown",
                    "privacy.save_history"):
            setting = schema.get_setting(key)
            if setting is None:
                continue
            check = QCheckBox(tr(setting.label))
            check.setChecked(self._settings.flag(key, bool(setting.default)))
            if setting.help:
                check.setToolTip(setting.help)
            check.toggled.connect(
                lambda state, k=key: self._settings.set(k, state))
            quick_layout.addWidget(check)

        layout.addWidget(quick)

        # Диагностика
        diag_card = QFrame()
        diag_card.setObjectName("Card")
        diag_layout = QVBoxLayout(diag_card)
        diag_layout.setContentsMargins(14, 12, 14, 12)
        diag_layout.setSpacing(6)

        diag_title = QLabel("Состояние системы")
        diag_title.setObjectName("SectionTitle")
        diag_layout.addWidget(diag_title)

        self._diag_text = QLabel("")
        self._diag_text.setObjectName("Hint")
        self._diag_text.setWordWrap(True)
        self._diag_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        diag_layout.addWidget(self._diag_text)

        check_button = QPushButton("Обновить сведения")
        check_button.clicked.connect(self._update_diagnostics)
        diag_layout.addWidget(check_button, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(diag_card)

        backends = QFrame()
        backends.setObjectName("Card")
        backends_layout = QVBoxLayout(backends)
        backends_layout.setContentsMargins(14, 12, 14, 12)
        backends_layout.setSpacing(6)

        backends_title = QLabel("Готовность компонентов")
        backends_title.setObjectName("SectionTitle")
        backends_layout.addWidget(backends_title)

        self._backends_text = QLabel("")
        self._backends_text.setObjectName("Hint")
        self._backends_text.setWordWrap(True)
        backends_layout.addWidget(self._backends_text)

        layout.addWidget(backends)

        layout.addStretch(1)
        self._update_diagnostics()
        return page

    # --- Тема -------------------------------------------------------------

    def _apply_theme(self) -> None:
        self._theme = build_theme(self._settings.text("ui.theme", "dark"),
                                  self._settings.text("ui.accent", "#4f8cff"),
                                  self._settings.text("ui.background", ""))
        self._app.setStyleSheet(stylesheet(
            self._theme,
            int(self._settings.number("ui.font_size", 100)),
            self._settings.flag("ui.compact", False),
            self._settings.flag("ui.animations", True),
        ))
        self._refresh_listen_icons()

        # Аватар подстраивается под тему и акцент.
        avatar = getattr(self, "_avatar", None)
        if avatar is not None:
            avatar.apply_settings()

    def _refresh_listen_icons(self) -> None:
        from luxvoice.ui.theme import icon_svg

        state = getattr(self, "_listen_state", "idle")
        name = "mic" if state in ("listening", "busy") else "mic_off"
        color = (self._theme.accent if state == "listening"
                 else self._theme.warning if state == "busy"
                 else self._theme.text_faint)

        for button in (getattr(self, "_listen_button", None),
                       getattr(self, "_big_listen", None)):
            if button is None:
                continue
            pixmap = QPixmap()
            pixmap.loadFromData(icon_svg(name, color, 48).encode("utf-8"), "SVG")
            button.setIcon(QIcon(pixmap))
            button.setIconSize(button.size() * 0.5)

    # --- Страницы ---------------------------------------------------------

    def _show_page(self, key: str) -> None:
        index = {
            "home": 0, "editor": 1, "history": 2,
            "chat": 3, "packs": 4, "settings": 5,
        }.get(key, 0)

        self._pages.setCurrentIndex(index)
        for name, button in self._nav_buttons.items():
            button.setChecked(name == key)

        titles = {
            "home": tr("Главная"), "editor": tr("Редактор команд"),
            "history": tr("История команд"), "chat": tr("Нейросеть"),
            "packs": tr("Дополнения"), "settings": tr("Настройки"),
        }
        self._page_title.setText(titles.get(key, ""))

        if key == "history":
            self._history_page.reload()
        elif key == "packs":
            self._packs_page.reload()
        elif key == "settings":
            self._update_diagnostics()
            self._chat_page._refresh_provider()

        if key == "settings" and self._settings.flag("ui.remember_tab", True):
            self._settings.set("ui.last_page", key, save=False)

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        dialog.settings_applied.connect(self._apply_theme)
        dialog.exec()
        self._apply_theme()
        self._update_diagnostics()

    # --- События ----------------------------------------------------------

    def _connect_events(self) -> None:
        """Подписка на события подсистем."""
        self._unsubscribers = [
            bus.subscribe(STT_STARTED, self._on_stt_started),
            bus.subscribe(STT_STOPPED, self._on_stt_stopped),
            bus.subscribe(STT_FINAL, self._on_stt_final),
            bus.subscribe(STT_PARTIAL, self._on_stt_partial),
            bus.subscribe(STT_ERROR, self._on_stt_error),
            bus.subscribe(STT_LEVEL, self._on_level),
            bus.subscribe(COMMAND_MATCHED, self._on_matched),
            bus.subscribe(COMMAND_UNKNOWN, self._on_unknown),
            bus.subscribe(COMMAND_FINISHED, self._on_finished),
            bus.subscribe(ACTION_PROGRESS, self._on_progress),
            bus.subscribe(TTS_STARTED, self._on_tts_started),
            bus.subscribe(TTS_FINISHED, self._on_tts_finished),
            bus.subscribe(STATUS_MESSAGE, self._on_status),
            bus.subscribe(NOTIFY, self._on_notify),
            bus.subscribe(MODE_CHANGED, self._on_mode_changed),
        ]

    def _on_stt_started(self, **_: Any) -> None:
        self._listen_state = "listening"
        self._state_label.setText(tr("Слушаю"))
        self._listen_button.set_state("listening")
        self._refresh_listen_icons()

    def _on_stt_stopped(self, **_: Any) -> None:
        self._listen_state = "idle"
        self._state_label.setText(tr("Не слушаю"))
        self._listen_button.set_state("idle")
        self._level_indicator.reset()
        self._refresh_listen_icons()

    def _on_stt_final(self, text: str = "", **_: Any) -> None:
        self._heard_label.setText(f"Услышано: «{text}»")
        self._add_recent(f"Услышано: {text}", "")

    def _on_stt_partial(self, text: str = "", **_: Any) -> None:
        if text:
            self._heard_label.setText(f"Слышу: «{text}»")

    def _on_stt_error(self, message: str = "", **_: Any) -> None:
        self._listen_state = "error"
        self._state_label.setText(tr("Ошибка"))
        self._heard_label.setText(message)
        self._listen_button.set_state("error")
        self._refresh_listen_icons()

    def _on_level(self, level: float = 0.0, **_: Any) -> None:
        self._level_indicator.set_level(level)

    def _on_matched(self, command: Any = None, phrase: str = "",
                    score: float = 0.0, **_: Any) -> None:
        if command is None:
            return
        self._state_label.setText(tr("Выполняю…"))
        self._add_recent(f"→ {command.title}", f"{score:.0f}% · «{phrase}»")

    def _on_unknown(self, text: str = "", **_: Any) -> None:
        self._add_recent(f"Не понял: {text}", "команда не найдена")
        self._heard_label.setText(f"Команда не найдена: «{text}»")

    def _on_finished(self, command: Any = None, ok: bool = True, **_: Any) -> None:
        self._state_label.setText(tr("Слушаю") if self._is_listening()
                                  else tr("Готов"))
        if command is not None:
            mark = "выполнено" if ok else "ошибка"
            self._add_recent(f"{'✓' if ok else '✗'} {command.title}", mark)
        if self._pages.currentIndex() == 2:
            self._history_page.reload()

    def _on_progress(self, index: int = 0, total: int = 0,
                     title: str = "", **_: Any) -> None:
        if total:
            self._status_label.setText(f"Шаг {index} из {total}: {title}")

    def _on_tts_started(self, text: str = "", **_: Any) -> None:
        self._status_label.setText(f"Говорю: {text[:60]}")

    def _on_tts_finished(self, **_: Any) -> None:
        self._status_label.setText(tr("Готов"))

    def _on_status(self, text: str = "", level: str = "info", **_: Any) -> None:
        self._status_label.setText(text)

    def _on_notify(self, title: str = "", text: str = "", **_: Any) -> None:
        """Показать системное уведомление, если оно включено."""
        if not self._settings.flag("ui.tray_notifications", True):
            return
        tray = getattr(self, "_tray", None)
        if tray is not None and tray.isVisible():
            from PyQt6.QtWidgets import QSystemTrayIcon
            tray.showMessage(title or "LuxVoice", text,
                             QSystemTrayIcon.MessageIcon.Information, 4000)

    def _on_mode_changed(self, prefix_mode: bool = False, silent: bool = False,
                         **_: Any) -> None:
        self._prefix_check.blockSignals(True)
        self._prefix_check.setChecked(prefix_mode)
        self._prefix_check.blockSignals(False)

    # --- Действия ---------------------------------------------------------

    def _add_recent(self, text: str, detail: str) -> None:
        """Добавить строку в список последних событий."""
        row = f"{text}" + (f"   ·   {detail}" if detail else "")
        item = QListWidgetItem(row)
        self._recent_list.insertItem(0, item)

        limit = int(self._settings.number("ui.history_rows", 50))
        while self._recent_list.count() > limit:
            self._recent_list.takeItem(self._recent_list.count() - 1)

    def _is_listening(self) -> bool:
        try:
            from luxvoice.stt.recognizer import get_recognizer
            return get_recognizer(self._settings).listening
        except Exception:  # noqa: BLE001
            return False

    def _toggle_listening(self) -> None:
        """Включить или выключить микрофон."""
        try:
            from luxvoice.stt.recognizer import get_recognizer
            recognizer = get_recognizer(self._settings)
            if recognizer.listening:
                recognizer.stop_listening()
            else:
                ok, message = recognizer.start_listening()
                if not ok:
                    QMessageBox.warning(
                        self, "Микрофон",
                        f"Не удалось включить микрофон:\n{message}\n\n"
                        "Проверьте устройство в настройках "
                        "(раздел «Голосовой ввод»).")
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка переключения микрофона: %s", exc)
            QMessageBox.warning(self, "Микрофон", f"Ошибка: {exc}")

    def _panic_stop(self) -> None:
        """Немедленно всё остановить."""
        try:
            from luxvoice.actions.runner import get_runner
            get_runner(self._settings).stop_all()
        except Exception:  # noqa: BLE001
            pass
        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            get_synthesizer(self._settings).stop()
        except Exception:  # noqa: BLE001
            pass
        self._status_label.setText("Остановлено")

    def _test_microphone(self) -> None:
        """Быстрая проверка микрофона."""
        try:
            from luxvoice.stt.capture import list_microphones
            from luxvoice.stt.recognizer import get_recognizer

            microphones = list_microphones()
            recognizer = get_recognizer(self._settings)
            diag = recognizer.diagnostics()

            lines = [f"Найдено микрофонов: {len(microphones)}"]
            for device in microphones[:4]:
                mark = " (по умолчанию)" if device.is_default else ""
                lines.append(f"  · {device.name}{mark}")

            lines.append("")
            lines.append(f"Состояние прослушивания: "
                         f"{'включено' if diag.get('listening') else 'выключено'}")
            lines.append(f"Движок распознавания: {diag.get('engine', '—')}"
                         f"{' (готов)' if diag.get('engine_ready') else ''}")
            if diag.get("engine_error"):
                lines.append(f"Ошибка движка: {diag['engine_error']}")
            lines.append(f"Распознано фраз: {diag.get('recognized', 0)}")

            QMessageBox.information(self, "Проверка микрофона", "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Проверка микрофона", f"Ошибка: {exc}")

    def _run_command_by_id(self, command_id: str) -> None:
        """Выполнить команду из редактора (кнопка «Проверить»)."""
        command = self._store.get(command_id)
        if command is None:
            return
        if not command.actions:
            QMessageBox.information(self, "Проверка команды",
                                    "В команде нет действий — нечего выполнять.")
            return

        # Озвучка приостанавливается: ассистент не должен слышать себя.
        try:
            from luxvoice.actions.runner import get_runner
            runner = get_runner(self._settings)
            self._status_label.setText(f"Выполняю: {command.title}")
            report = runner._run_command(command, command.primary_phrase())
            if report.ok:
                QMessageBox.information(
                    self, "Команда выполнена",
                    f"«{command.title}»\n\n{report.summary}")
            else:
                QMessageBox.warning(
                    self, "Команда не выполнена",
                    f"«{command.title}»\n\n{report.error}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Ошибка выполнения", str(exc))

    # --- Состояние и трей -------------------------------------------------

    def _start_telegram_if_needed(self) -> None:
        """Запустить Telegram-бота, если он включён в настройках."""
        if not self._settings.flag("plugins.telegram_enabled", False):
            return

        self._telegram.configure(
            token=self._settings.text("plugins.telegram_token", ""),
            owner_id=self._settings.text("plugins.telegram_owner_id", ""),
        )

        def start() -> None:
            ok, message = self._telegram.start()
            if not ok:
                log.warning("Telegram-бот не запущен: %s", message)
                self._status_label.setText(f"Telegram: {message}")

        # Запуск откладываем: окно должно появиться раньше.
        QTimer.singleShot(2500, start)

    def _refresh_status(self) -> None:
        """Обновить сведения в строке состояния."""
        try:
            from luxvoice.stt.recognizer import get_recognizer
            diag = get_recognizer(self._settings).diagnostics()
            engine = diag.get("engine", "")
            ready = "готов" if diag.get("engine_ready") else "не готов"
            self._engine_label.setText(f"Распознавание: {engine} ({ready})")

            if diag.get("listening") and self._pages.currentIndex() == 0:
                level = diag.get("queue", 0)
                if level:
                    self._status_label.setText(f"В очереди фраз: {level}")
        except Exception:  # noqa: BLE001
            pass

    def _update_diagnostics(self) -> None:
        """Показать сведения о системе и компонентах."""
        lines: list[str] = []

        try:
            from luxvoice.sysint.windows import desktop_environment
            env = desktop_environment()
            lines.append(f"Сеанс: {env['session']} · рабочий стол: {env['desktop']}")
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.sysint.uinput import input_available
            available, reason = input_available()
            lines.append("Ввод (клавиши и мышь): "
                         + ("доступен" if available else f"недоступен — {reason}"))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Ввод: ошибка проверки ({exc})")

        try:
            from luxvoice.sysint.audio import get_audio
            audio = get_audio()
            volume = audio.volume()
            lines.append(f"Звук: громкость {volume}%"
                         + (", без звука" if audio.muted() else ""))
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.stt.engines import available_tools
            tools = available_tools()
            lines.append("Распознавание: "
                         + ", ".join(k for k, v in tools.items() if v))
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            engines = get_synthesizer(self._settings).available_engines()
            ready = [k for k, v in engines.items() if v]
            lines.append("Синтез речи: " + (", ".join(ready) if ready
                                            else "не найден ни один движок"))
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.core.history import get_history
            lines.append(f"История: записей {get_history(self._settings).count()}")
        except Exception:  # noqa: BLE001
            pass

        lines.append(f"Данные: {paths.data_dir()}")
        lines.append(f"Настройки: {paths.config_file()}")

        self._diag_text.setText("\n".join(lines))

        # Готовность компонентов
        checks: list[str] = []
        try:
            from luxvoice.stt.engines import vosk_model_installed
            for language, label in (("ru", "русская"), ("en", "английская")):
                mark = "✓" if vosk_model_installed(language) else "✗"
                checks.append(f"{mark} Модель Vosk ({label})")
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.stt.capture import list_microphones
            count = len(list_microphones())
            checks.append(f"{'✓' if count else '✗'} Микрофоны найдено: {count}")
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            voices = get_synthesizer(self._settings).voices()
            checks.append(f"{'✓' if voices else '✗'} Голоса синтеза: {len(voices)}")
        except Exception:  # noqa: BLE001
            pass

        stats = self._store.statistics()
        checks.append(f"· Команд в редакторе: {stats['commands']} "
                      f"(включено {stats['enabled']})")

        self._backends_text.setText("\n".join(checks))

    def _setup_tray(self) -> None:
        """Значок в области уведомлений."""
        if not self._settings.flag("ui.tray", True):
            return

        from PyQt6.QtWidgets import QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.debug("Область уведомлений недоступна")
            return

        from luxvoice.ui.theme import icon_svg

        tray = QSystemTrayIcon(self)
        pixmap = QPixmap()
        pixmap.loadFromData(
            icon_svg("mic", self._theme.accent, 64).encode("utf-8"), "SVG")
        tray.setIcon(QIcon(pixmap))
        tray.setToolTip(f"LuxVoice {__version__}")

        menu = QMenu()
        menu.addAction("Открыть окно", self._restore_window)
        menu.addSeparator()

        self._tray_listen = menu.addAction("Включить микрофон")
        self._tray_listen.triggered.connect(self._toggle_listening)

        self._tray_prefix = menu.addAction("Режим обращения")
        self._tray_prefix.setCheckable(True)
        self._tray_prefix.setChecked(self._settings.flag("stt.prefix_mode", False))
        self._tray_prefix.triggered.connect(
            lambda checked: self._settings.set("stt.prefix_mode", checked))

        menu.addSeparator()
        menu.addAction("Настройки", self._open_settings_dialog)
        menu.addAction("Редактор команд", lambda: self._show_page("editor"))
        menu.addSeparator()
        menu.addAction("Стоп", self._panic_stop)
        menu.addAction("Выход", self._force_quit)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()

        self._tray = tray
        self._tray_menu = menu

    def _on_tray_activated(self, reason: Any) -> None:
        from PyQt6.QtWidgets import QSystemTrayIcon

        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_window()

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # --- Геометрия и закрытие --------------------------------------------

    def _restore_geometry(self) -> None:
        """Восстановить размер и положение окна."""
        if not self._settings.flag("app.remember_window", True):
            return
        geometry = self._settings.get("ui.window_geometry")
        if isinstance(geometry, list) and len(geometry) == 4:
            try:
                self.setGeometry(*(int(v) for v in geometry))
            except (TypeError, ValueError):
                pass

    def _save_geometry(self) -> None:
        if not self._settings.flag("app.remember_window", True):
            return
        rect = self.geometry()
        self._settings.set(
            "ui.window_geometry",
            [rect.x(), rect.y(), rect.width(), rect.height()],
            save=False)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        QTimer.singleShot(500, self._save_geometry)

    def moveEvent(self, event) -> None:  # noqa: ANN001, N802
        super().moveEvent(event)
        QTimer.singleShot(500, self._save_geometry)

    def _force_quit(self) -> None:
        """Полностью закрыть приложение, минуя сворачивание в трей."""
        self._force_closing = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        """Закрытие окна."""
        # Если запрошен принудительный выход — закрываем сразу.
        if getattr(self, "_force_closing", False):
            self._shutdown()
            event.accept()
            QApplication.quit()
            return

        if (self._settings.flag("app.close_to_tray", True)
                and getattr(self, "_tray", None) is not None):
            event.ignore()
            self.hide()
            if self._settings.flag("ui.tray_notifications", True):
                self._tray.showMessage(
                    "LuxVoice работает", "Программа продолжает слушать "
                    "микрофон. Выход — через значок в области уведомлений.",
                    self._tray.icon(), 3000)
            return

        if self._settings.flag("app.confirm_exit", False):
            answer = QMessageBox.question(
                self, "Выход", "Завершить работу ассистента?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._shutdown()
        event.accept()
        QApplication.quit()

    def _shutdown(self) -> None:
        """Аккуратно завершить работу всех подсистем."""
        log.info("Завершение работы программы")
        self._save_geometry()

        if self._settings.flag("privacy.clear_on_exit", False):
            try:
                get_history(self._settings).clear()
            except Exception:  # noqa: BLE001
                pass

        for unsubscribe in getattr(self, "_unsubscribers", []):
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001
                pass

        try:
            from luxvoice.stt.recognizer import get_recognizer
            get_recognizer(self._settings).close()
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            get_synthesizer(self._settings).close()
        except Exception:  # noqa: BLE001
            pass

        try:
            from luxvoice.sysint.uinput import release_device
            release_device()
        except Exception:  # noqa: BLE001
            pass

        try:
            self._telegram.stop()
        except Exception:  # noqa: BLE001
            pass

        try:
            self._avatar.stop()
        except Exception:  # noqa: BLE001
            pass

        try:
            self._store.save(force=True)
            self._settings.save()
        except Exception:  # noqa: BLE001
            pass

        bus.publish(APP_QUIT)


class AssistantApp:
    """Приложение целиком: настройки, окно, подсистемы."""

    def __init__(self, argv: list[str] | None = None,
                 start_hidden: bool = False) -> None:
        self._app = QApplication(argv or [])
        self._start_hidden = start_hidden
        self._app.setApplicationName("LuxVoice")
        self._app.setApplicationDisplayName("LuxVoice")
        self._app.setOrganizationName("LuxVoice")
        self._app.setDesktopFileName("luxvoice")
        # Закрытие последнего окна не завершает программу: она живёт в трее.
        self._app.setQuitOnLastWindowClosed(False)

        paths.ensure_dirs()

        self._settings = Settings()
        self._settings.load()
        set_language(self._settings.text("ui.language", "ru"))

        self._store = get_store()
        self._store.ensure_defaults()

        self._window = MainWindow(self._settings, self._app)

        # Мгновенный выход из редактора команд нужно зафиксировать на диске.
        self._app.aboutToQuit.connect(self._window._shutdown)

        self._connect_subsystems()
        self._start_listening_if_needed()

    def _connect_subsystems(self) -> None:
        """Связать распознавание, выполнение команд и озвучку."""
        settings = self._settings

        from luxvoice.actions.runner import get_runner
        from luxvoice.core.history import HistoryEntry, get_history
        from luxvoice.stt.recognizer import get_recognizer
        from luxvoice.tts.synthesizer import get_synthesizer

        recognizer = get_recognizer(settings)
        runner = get_runner(settings)
        synth = get_synthesizer(settings)
        history = get_history(settings)

        # Распознанный текст → исполнитель команд.
        recognizer.on_text(lambda result: runner.handle_text(result.text))

        # Озвучка ассистента.
        def speak(text: str | None, phrase_kind: str, interrupt: bool) -> None:
            if phrase_kind:
                synth.say_phrase(phrase_kind)
            elif text:
                synth.say(text, interrupt=interrupt)

        runner.on_speak(speak)

        # Пауза микрофона, пока ассистент говорит: иначе он услышит сам себя.
        if settings.flag("exec.hold_mic_while_speaking", True):
            def on_speaking(speaking: bool) -> None:
                if speaking:
                    recognizer.pause()
                else:
                    recognizer.resume()

            synth.on_speaking_changed(on_speaking)

        # История.
        def record(report) -> None:
            if not settings.flag("privacy.save_history", True):
                return
            entry = HistoryEntry(
                timestamp=__import__("time").time(),
                phrase=report.phrase,
                command_id=report.command.id if report.command else "",
                command=report.command.title if report.command else "",
                ok=report.ok,
                cancelled=report.cancelled,
                score=0.0,
                elapsed=report.elapsed,
                steps=report.steps,
                output=report.output,
                error=report.error,
            )
            history.add(entry)

        runner.on_history(record)

        # Уведомления.
        runner.on_notify(self._notify)

        # Изменения в редакторе → обновить индекс фраз.
        from luxvoice.core.events import COMMANDS_CHANGED, bus
        bus.subscribe(COMMANDS_CHANGED, runner.refresh)

        # Приветствие при запуске.
        if (settings.flag("tts.on_start", True)
                and not settings.flag("app.silent_start", False)):
            greeting = settings.text("app.greeting", "")
            if greeting:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(1200, lambda: synth.say(greeting, interrupt=False))

    def _notify(self, title: str, text: str) -> None:
        """Показать уведомление."""
        bus.publish(NOTIFY, title=title, text=text)

    def _start_listening_if_needed(self) -> None:
        """Включить микрофон при старте, если так настроено."""
        if not self._settings.flag("app.start_listening", True):
            return

        from luxvoice.stt.recognizer import get_recognizer

        recognizer = get_recognizer(self._settings)

        # Запуск отложен: окно должно появиться раньше, чем начнётся
        # загрузка модели распознавания — она занимает время.
        def start() -> None:
            ok, message = recognizer.start_listening()
            if not ok:
                log.warning("Микрофон не включён автоматически: %s", message)

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(800, start)

    def run(self) -> int:
        # При автозапуске окно не показываем: программа работает в трее.
        hidden = (self._start_hidden
                  or self._settings.flag("app.start_minimized", False))
        if hidden:
            log.info("Запуск свёрнутым в трей")
            self._window._status_label.setText(
                "Ассистент работает в фоне — значок в области уведомлений")
        else:
            self._window.show()
        return self._app.exec()