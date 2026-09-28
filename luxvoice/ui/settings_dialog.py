"""Окно настроек, которое строится автоматически из схемы.

Ключевое решение: поля не описываются вручную. Схема настроек
(core/schema.py) содержит тип, подпись, подсказку и допустимые значения
каждого параметра — из неё и собирается этот интерфейс. Добавление
настройки в схему сразу даёт рабочее поле в окне.

Дополнительно: поиск по настройкам, переключатель уровня детализации
(обычные / дополнительные / экспертные), сброс раздела и экспорт.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from luxvoice.core import schema
from luxvoice.core.i18n import tr
from luxvoice.core.settings import Settings

log = logging.getLogger(__name__)


class SettingRow(QWidget):
    """Одна строка настройки: подпись, поле, подсказка."""

    changed = pyqtSignal(str, object)

    def __init__(self, setting: schema.Setting, settings: Settings,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setting = setting
        self._settings = settings
        self._editor: QWidget | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(10)

        label = QLabel(setting.label)
        label.setWordWrap(True)
        label.setMinimumWidth(220)
        top.addWidget(label, 1)

        self._editor = self._make_editor()
        if self._editor is not None:
            self._editor.setMinimumWidth(240)
            top.addWidget(self._editor, 1)

        layout.addLayout(top)

        if setting.help:
            hint = QLabel(setting.help)
            hint.setObjectName("Hint")
            hint.setWordWrap(True)
            hint.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(hint)

        if setting.restart:
            restart_hint = QLabel("Изменение применится после перезапуска программы")
            restart_hint.setObjectName("Warning")
            restart_hint.setWordWrap(True)
            layout.addWidget(restart_hint)

    # --- Создание поля ----------------------------------------------------

    def _make_editor(self) -> QWidget | None:
        setting = self.setting
        value = self._settings.get(setting.key, setting.default)
        kind = setting.kind

        if kind == schema.BOOL:
            editor = QCheckBox()
            editor.setChecked(bool(value))
            editor.toggled.connect(
                lambda state: self._emit(self.setting.key, bool(state)))
            return editor

        if kind in (schema.INT, schema.FLOAT):
            return self._make_number_editor(value, kind)

        if kind == schema.CHOICE:
            editor = QComboBox()
            if setting.dynamic or not setting.choices:
                editor.setEditable(True)
                editor.addItem(str(value))
                if setting.key == "stt.mic_device":
                    self._fill_microphones(editor, str(value))
                elif setting.key == "tts.voice":
                    self._fill_voices(editor, str(value))
                elif setting.key == "tts.dictation_voice":
                    self._fill_voices(editor, str(value), add_default=True)
            else:
                for key, label in setting.choices:
                    editor.addItem(tr(label), key)
                index = editor.findData(value)
                if index >= 0:
                    editor.setCurrentIndex(index)
            editor.currentIndexChanged.connect(
                lambda _index: self._emit_combo(editor))
            if editor.isEditable():
                editor.editTextChanged.connect(
                    lambda text: self._emit(self.setting.key, text))
            return editor

        if kind == schema.SECRET:
            editor = QLineEdit(str(value))
            editor.setEchoMode(QLineEdit.EchoMode.Password)
            editor.setPlaceholderText(setting.placeholder or "вставьте ключ")
            editor.setClearButtonEnabled(True)
            editor.editingFinished.connect(
                lambda: self._emit(self.setting.key, editor.text()))
            self._add_reveal_button(editor)
            return editor

        if kind == schema.TEXT:
            editor = QTextEdit()
            editor.setPlainText(str(value))
            editor.setMaximumHeight(90)
            editor.setPlaceholderText(setting.placeholder)
            editor.textChanged.connect(
                lambda: self._emit(self.setting.key, editor.toPlainText()))
            return editor

        if kind == schema.COLOR:
            return self._make_color_editor(str(value))

        if kind in (schema.PATH, schema.DIR, schema.FILE):
            return self._make_path_editor(str(value), kind)

        if kind in (schema.LIST, schema.MULTI):
            editor = QLineEdit(self._format_list(value))
            editor.setPlaceholderText(setting.placeholder or "через двоеточие")
            editor.editingFinished.connect(
                lambda: self._emit_list(self.setting.key, editor.text()))
            return editor

        if kind == schema.KEYSEQ:
            editor = QLineEdit(str(value))
            editor.setPlaceholderText("ctrl+shift+k")
            editor.editingFinished.connect(
                lambda: self._emit(self.setting.key, editor.text()))
            return editor

        # Строка по умолчанию.
        editor = QLineEdit(str(value))
        editor.setPlaceholderText(setting.placeholder)
        editor.setClearButtonEnabled(True)
        editor.editingFinished.connect(
            lambda: self._emit(self.setting.key, editor.text()))
        return editor

    def _make_number_editor(self, value: Any, kind: str) -> QWidget:
        setting = self.setting
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        is_float = kind == schema.FLOAT
        if is_float:
            spin: QSpinBox | QDoubleSpinBox = QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setSingleStep(setting.step or 0.1)
        else:
            spin = QSpinBox()
            spin.setSingleStep(int(setting.step) or 1)

        if setting.minimum is not None:
            spin.setMinimum(int(setting.minimum) if not is_float
                            else float(setting.minimum))
        else:
            spin.setMinimum(-1000000)
        if setting.maximum is not None:
            spin.setMaximum(int(setting.maximum) if not is_float
                            else float(setting.maximum))
        else:
            spin.setMaximum(1000000)

        spin.setValue(float(value) if is_float else int(float(value)))
        if setting.unit:
            spin.setSuffix(f" {setting.unit}")
        layout.addWidget(spin)

        # Ползунок: удобен для процентов и небольших диапазонов.
        slider_ok = (
            setting.minimum is not None and setting.maximum is not None
            and (setting.maximum - setting.minimum) <= 1000
            and not is_float
        )
        if slider_ok:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimum(int(setting.minimum))
            slider.setMaximum(int(setting.maximum))
            slider.setValue(int(float(value)))
            slider.setFixedWidth(160)
            layout.addWidget(slider)

            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)

        spin.valueChanged.connect(
            lambda number: self._emit(self.setting.key, number))
        container.setLayout(layout)
        return container

    def _make_color_editor(self, value: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        button = QPushButton(value or "выбрать цвет")
        button.setObjectName("Ghost")

        def update_preview(color: str) -> None:
            if color:
                button.setText(color)
                button.setStyleSheet(
                    f"background-color: {color}; "
                    f"color: {'#10141a' if _is_light(color) else '#ffffff'}; "
                    "border-radius: 6px; padding: 4px 10px;")
            else:
                button.setText("выбрать цвет")
                button.setStyleSheet("")

        update_preview(value)

        def pick() -> None:
            current = self._settings.get(self.setting.key, "") or "#4f8cff"
            chosen = QColorDialog.getColor(QColor(current), self,
                                           "Выберите цвет")
            if chosen.isValid():
                hex_value = chosen.name()
                update_preview(hex_value)
                self._emit(self.setting.key, hex_value)

        button.clicked.connect(pick)
        layout.addWidget(button)

        clear = QPushButton("Сбросить")
        clear.setObjectName("Ghost")
        clear.setToolTip("Вернуть цвет по умолчанию")
        clear.clicked.connect(lambda: (update_preview(""),
                                       self._emit(self.setting.key, "")))
        layout.addWidget(clear)
        return container

    def _make_path_editor(self, value: str, kind: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        editor = QLineEdit(value)
        editor.setPlaceholderText("путь")
        editor.editingFinished.connect(
            lambda: self._emit(self.setting.key, editor.text()))
        layout.addWidget(editor, 1)

        button = QPushButton("…")
        button.setFixedWidth(34)
        button.setToolTip("Выбрать")

        def browse() -> None:
            if kind == schema.DIR:
                chosen = QFileDialog.getExistingDirectory(
                    self, "Выберите каталог", editor.text() or "")
            else:
                chosen, _ = QFileDialog.getOpenFileName(
                    self, "Выберите файл", editor.text() or "")
            if chosen:
                editor.setText(chosen)
                self._emit(self.setting.key, chosen)

        button.clicked.connect(browse)
        layout.addWidget(button)
        return container

    def _add_reveal_button(self, editor: QLineEdit) -> None:
        """Кнопка показа секрета — чтобы проверить, что ключ вставлен верно."""
        action = editor.addAction(
            QIcon(), QLineEdit.ActionPosition.TrailingPosition)
        action.setText("👁")
        action.setToolTip("Показать или скрыть ключ")

        def toggle() -> None:
            if editor.echoMode() == QLineEdit.EchoMode.Password:
                editor.setEchoMode(QLineEdit.EchoMode.Normal)
            else:
                editor.setEchoMode(QLineEdit.EchoMode.Password)

        action.triggered.connect(toggle)

    # --- Динамические списки ---------------------------------------------

    def _fill_microphones(self, editor: QComboBox, value: str) -> None:
        """Заполнить список микрофонами из системы."""
        try:
            from luxvoice.stt.capture import list_microphones
            editor.clear()
            editor.addItem(tr("Системная по умолчанию"), "")
            for device in list_microphones():
                label = device.name + (" (по умолчанию)" if device.is_default else "")
                editor.addItem(label, str(device.index))
            index = editor.findData(value)
            editor.setCurrentIndex(index if index >= 0 else 0)
        except Exception as exc:  # noqa: BLE001
            log.debug("Не удалось получить список микрофонов: %s", exc)

    def _fill_voices(self, editor: QComboBox, value: str,
                     add_default: bool = False) -> None:
        """Заполнить список голосами доступных движков."""
        try:
            from luxvoice.tts.synthesizer import get_synthesizer
            synth = get_synthesizer(self._settings)
            editor.clear()
            if add_default:
                editor.addItem("Основной голос", "")
            for engine in ("piper", "rhvoice", "espeak", "cloud"):
                voices = synth.voices(engine)
                if not voices:
                    continue
                for voice in voices:
                    editor.addItem(f"{voice.label} · {engine}", voice.id)
            index = editor.findData(value)
            if index >= 0:
                editor.setCurrentIndex(index)
            elif value:
                editor.setEditText(value)
        except Exception as exc:  # noqa: BLE001
            log.debug("Не удалось получить список голосов: %s", exc)

    # --- Отправка значения -----------------------------------------------

    def _emit(self, key: str, value: Any) -> None:
        self.changed.emit(key, value)

    def _emit_combo(self, editor: QComboBox) -> None:
        data = editor.currentData()
        if data is None:
            data = editor.currentText()
        self.changed.emit(self.setting.key, data)

    def _emit_list(self, key: str, text: str) -> None:
        """Список храним строкой после приведения к типу настройки."""
        self.changed.emit(key, text)

    @staticmethod
    def _format_list(value: Any) -> str:
        if isinstance(value, (list, tuple)):
            return ":".join(str(v) for v in value)
        return str(value or "")

    def refresh(self) -> None:
        """Перечитать значение из настроек — после внешнего изменения."""
        value = self._settings.get(self.setting.key, self.setting.default)
        editor = self._editor
        if editor is None:
            return

        editor.blockSignals(True)
        try:
            if isinstance(editor, QCheckBox):
                editor.setChecked(bool(value))
            elif isinstance(editor, QComboBox):
                index = editor.findData(value)
                if index >= 0:
                    editor.setCurrentIndex(index)
                elif editor.isEditable():
                    editor.setEditText(str(value))
            elif isinstance(editor, QLineEdit):
                editor.setText(str(value))
            elif isinstance(editor, QTextEdit):
                editor.setPlainText(str(value))
        finally:
            editor.blockSignals(False)


class SettingsDialog(QDialog):
    """Окно настроек со всеми разделами."""

    settings_applied = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None,
                 initial_section: str = "") -> None:
        super().__init__(parent)
        self._settings = settings
        self._rows: list[SettingRow] = []
        self._section_buttons: dict[str, QPushButton] = {}
        self._current_section = initial_section or schema.sections()[0].key
        self._level = "basic"
        self._changed_keys: set[str] = set()
        self._api_key_row: SettingRow | None = None

        self.setWindowTitle(f"{tr('Настройки')} — LuxVoice")
        self.setMinimumSize(860, 640)
        self.resize(940, 700)

        self._build_ui()
        self._show_section(self._current_section)

    # --- Построение -------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Левая колонка: разделы ---
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 12, 10, 12)
        side_layout.setSpacing(4)

        title = QLabel(tr("Настройки"))
        title.setObjectName("Title")
        side_layout.addWidget(title)
        side_layout.addSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText(f"{tr('Поиск')}…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        side_layout.addWidget(self._search)
        side_layout.addSpacing(6)

        for section in schema.sections():
            button = QPushButton(tr(section.label))
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setToolTip(section.description)
            button.clicked.connect(
                lambda _checked, key=section.key: self._show_section(key))
            side_layout.addWidget(button)
            self._section_buttons[section.key] = button

        side_layout.addStretch(1)

        self._level_button = QPushButton("Показать дополнительные")
        self._level_button.setObjectName("Ghost")
        self._level_button.setCheckable(True)
        self._level_button.toggled.connect(self._on_level_toggle)
        side_layout.addWidget(self._level_button)

        root.addWidget(sidebar)

        # --- Правая часть: содержимое ---
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)

        self._section_title = QLabel("")
        self._section_title.setObjectName("SectionTitle")
        header_layout.addWidget(self._section_title)
        header_layout.addStretch(1)

        self._reset_button = QPushButton("Сбросить раздел")
        self._reset_button.setObjectName("Ghost")
        self._reset_button.setToolTip("Вернуть значения по умолчанию "
                                      "для всех настроек этого раздела")
        self._reset_button.clicked.connect(self._reset_section)
        header_layout.addWidget(self._reset_button)

        right.addWidget(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._form = QVBoxLayout(self._container)
        self._form.setContentsMargins(18, 14, 18, 14)
        self._form.setSpacing(2)
        self._scroll.setWidget(self._container)
        right.addWidget(self._scroll, 1)

        # --- Нижняя панель ---
        buttons = QDialogButtonBox()
        save = buttons.addButton(tr("Сохранить"), QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("Primary")
        buttons.addButton(tr("Закрыть"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        footer = QFrame()
        footer.setObjectName("StatusBar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 8, 16, 8)
        footer_layout.addStretch(1)
        footer_layout.addWidget(buttons)
        right.addWidget(footer)

        root.addLayout(right, 1)

    # --- Разделы ----------------------------------------------------------

    def _show_section(self, key: str) -> None:
        self._current_section = key
        for section_key, button in self._section_buttons.items():
            button.setChecked(section_key == key)

        section = schema.SECTION_BY_KEY.get(key)
        self._section_title.setText(tr(section.label) if section else key)

        # Очищаем содержимое.
        while self._form.count():
            item = self._form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                child_layout.deleteLater()
        self._rows.clear()

        settings = self._settings.visible_settings(key, self._level)
        if not settings:
            empty = QLabel("В этом разделе нет настроек на выбранном уровне")
            empty.setObjectName("Hint")
            self._form.addWidget(empty)
            self._form.addStretch(1)
            return

        if section and section.description:
            description = QLabel(tr(section.description))
            description.setObjectName("Hint")
            description.setWordWrap(True)
            self._form.addWidget(description)
            self._form.addSpacing(8)

        # Группируем по префиксу подраздела — так длинные разделы читаются.
        last_prefix = ""
        for setting in settings:
            prefix = setting.key.split(".")[0]
            if prefix != last_prefix and prefix:
                if self._form.count() > 0:
                    line = QFrame()
                    line.setObjectName("Separator")
                    line.setFrameShape(QFrame.Shape.HLine)
                    self._form.addSpacing(6)
                    self._form.addWidget(line)
                    self._form.addSpacing(6)
                last_prefix = prefix

            row = SettingRow(setting, self._settings)
            row.changed.connect(self._on_setting_changed)

            # Специальная логика для поля ключа API: скрываем для локальных провайдеров.
            if setting.key == "ai.api_key":
                self._api_key_row = row
                provider = self._settings.text("ai.provider", "openai")
                from luxvoice.ai.client import provider_info
                is_local = provider_info(provider).local
                row.setVisible(not is_local)

            # Специальная логика для выбора провайдера: обновляем видимость ключа.
            if setting.key == "ai.provider":
                row.changed.connect(self._on_provider_changed)

            self._form.addWidget(row)
            self._rows.append(row)

        # Специальные кнопки для раздела ИИ-провайдер.
        if key == "ai":
            self._form.addSpacing(12)
            test_button = QPushButton("Проверить подключение")
            test_button.setToolTip("Отправляет тестовый запрос к выбранному провайдеру")
            test_button.clicked.connect(self._test_ai_connection)
            self._form.addWidget(test_button)

            self._test_result = QLabel("")
            self._test_result.setWordWrap(True)
            self._form.addWidget(self._test_result)

        self._form.addStretch(1)

    def _on_level_toggle(self, advanced: bool) -> None:
        self._level = "expert" if advanced else "basic"
        self._level_button.setText(
            "Скрыть дополнительные" if advanced else "Показать дополнительные")
        self._show_section(self._current_section)

    def _on_search(self, text: str) -> None:
        """Поиск по всем настройкам, независимо от раздела."""
        needle = text.strip().lower()
        if not needle:
            self._show_section(self._current_section)
            return

        while self._form.count():
            item = self._form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                child_layout.deleteLater()
        self._rows.clear()

        found: list[schema.Setting] = []
        for setting in schema.all_settings():
            haystack = f"{setting.label} {setting.help} {setting.key}".lower()
            if needle in haystack:
                found.append(setting)

        self._section_title.setText(f"Найдено: {len(found)}")

        if not found:
            empty = QLabel("Ничего не найдено. Попробуйте другое слово.")
            empty.setObjectName("Hint")
            self._form.addWidget(empty)
            self._form.addStretch(1)
            return

        for setting in found[:80]:
            section = schema.SECTION_BY_KEY.get(setting.section)
            caption = QLabel(f"{tr(setting.label)}  ·  "
                             f"{tr(section.label) if section else setting.section}")
            caption.setObjectName("Hint")
            self._form.addWidget(caption)
            row = SettingRow(setting, self._settings)
            row.changed.connect(self._on_setting_changed)
            self._form.addWidget(row)
            self._rows.append(row)

        self._form.addStretch(1)

    # --- Изменения --------------------------------------------------------

    def _on_setting_changed(self, key: str, value: Any) -> None:
        """Применить значение сразу — настройки нужны работающими без перезапуска."""
        self._settings.set(key, value, save=False)
        self._changed_keys.add(key)

        # Некоторые настройки требуют немедленной реакции.
        self._apply_live(key, value)

    def _apply_live(self, key: str, value: Any) -> None:
        """Передать изменение работающим подсистемам."""
        try:
            if key.startswith("stt."):
                from luxvoice.stt.recognizer import get_recognizer
                recognizer = get_recognizer(self._settings)
                recognizer.set_settings(self._settings)
                if key in ("stt.engine", "stt.mic_device", "stt.language",
                           "stt.sensitivity", "stt.vad_enabled", "stt.vad_aggressiveness"):
                    if key in ("stt.engine", "stt.mic_device"):
                        recognizer.reload_engine()
                        recognizer.restart_listening()
                    else:
                        recognizer._apply_capture_settings()

            if key == "tts.engine":
                # Смена движка не требует перезапуска: движок выбирается
                # при каждом произнесении.
                pass

            if key.startswith("match.") or key.startswith("chain."):
                from luxvoice.actions.runner import get_runner
                runner = get_runner(self._settings)
                runner.set_settings(self._settings)
                runner.refresh()

            if key == "ui.theme" or key == "ui.accent" or key.startswith("ui.font"):
                self.settings_applied.emit()

            if key == "stt.prefix" or key == "stt.prefix_aliases":
                from luxvoice.actions.runner import get_runner
                get_runner(self._settings).refresh()

        except Exception as exc:  # noqa: BLE001
            log.debug("Живое применение настройки %s не удалось: %s", key, exc)

    def _reset_section(self) -> None:
        if self._current_section not in schema.SECTION_BY_KEY:
            return
        answer = QMessageBox.question(
            self, "Сбросить раздел",
            "Вернуть все настройки этого раздела к значениям по умолчанию?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        changed = self._settings.reset_section(self._current_section, save=False)
        self._changed_keys.update(changed)
        self._show_section(self._current_section)

    def _test_ai_connection(self) -> None:
        """Проверить подключение к ИИ-провайдеру."""
        self._test_result.setText("Проверяю подключение...")
        self._test_result.setStyleSheet("color: gray;")
        QApplication.processEvents()

        try:
            from luxvoice.ai.client import get_ai_client
            client = get_ai_client(self._settings)

            # Временно включаем нейросеть для проверки.
            was_enabled = self._settings.flag("ai.enabled", False)
            if not was_enabled:
                self._settings.set("ai.enabled", True, save=False)

            ok, message = client.test_connection()

            if ok:
                # Если проверка прошла — включаем нейросеть автоматически.
                if not was_enabled:
                    self._settings.set("ai.enabled", True, save=True)
                    self._changed_keys.add("ai.enabled")
                    # Обновляем чекбокс в интерфейсе.
                    for row in self._rows:
                        if row.setting.key == "ai.enabled":
                            row.update_value(True)
                            break
                self._test_result.setText(
                    f"✓ {message}\n\nНейросеть включена и готова к работе.")
                self._test_result.setStyleSheet("color: green;")
            else:
                # Возвращаем прежнее состояние.
                if not was_enabled:
                    self._settings.set("ai.enabled", was_enabled, save=False)
                self._test_result.setText(f"✗ {message}")
                self._test_result.setStyleSheet("color: red;")

        except Exception as exc:  # noqa: BLE001
            self._test_result.setText(f"✗ Ошибка: {exc}")
            self._test_result.setStyleSheet("color: red;")

    def _on_provider_changed(self, key: str, value: Any) -> None:
        """Обновить видимость поля ключа API при смене провайдера."""
        if key != "ai.provider":
            return

        api_key_row = getattr(self, "_api_key_row", None)
        if api_key_row is None:
            return

        from luxvoice.ai.client import provider_info
        provider = provider_info(str(value))
        api_key_row.setVisible(not provider.local)

        # Показываем подсказку для локальных провайдеров.
        if provider.local:
            hint_text = f"Для {provider.title} ключ не нужен. Убедитесь, что сервер запущен: {provider.note}"
        else:
            hint_text = f"Получите ключ: {provider.key_url}" if provider.key_url else ""

        # Ищем или создаем label с подсказкой.
        hint_label = api_key_row.findChild(QLabel, "ProviderHint")
        if hint_label is None:
            hint_label = QLabel(hint_text)
            hint_label.setObjectName("ProviderHint")
            hint_label.setStyleSheet("color: #666; font-style: italic;")
            hint_label.setWordWrap(True)
            api_key_row.layout().addWidget(hint_label)
        else:
            hint_label.setText(hint_text)

        hint_label.setVisible(bool(hint_text))
        self.settings_applied.emit()

    def _on_save(self) -> None:
        if self._settings.save():
            log.info("Настройки сохранены (%d изменённых полей)",
                     len(self._changed_keys))
        self.settings_applied.emit()
        self.accept()


def _is_light(color: str) -> bool:
    """Светлый ли цвет — для выбора контрастного текста на нём."""
    color = (color or "").lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    try:
        red = int(color[0:2], 16)
        green = int(color[2:4], 16)
        blue = int(color[4:6], 16)
    except (ValueError, IndexError):
        return False
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0 > 0.6