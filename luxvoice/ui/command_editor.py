"""Редактор команд: дерево, карточка команды и список действий.

Слева — дерево «коллекция → папка → команда» с включением, поиском
и перетаскиванием. Справа — карточка выбранной команды: фразы, действия,
подтверждение, метки и привязка к окнам.

Действия собираются мышью, как в исходной программе: выбираете группу,
затем действие, задаёте параметры. Все поля строятся из каталога
действий, поэтому новый тип действия появляется в редакторе сам.
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from luxvoice.actions import catalog
from luxvoice.core.events import COMMANDS_CHANGED, bus
from luxvoice.core.i18n import tr
from luxvoice.core.model import (
    KIND_COLLECTION,
    KIND_COMMAND,
    KIND_FOLDER,
    Action,
    Command,
    Node,
)
from luxvoice.core.settings import Settings
from luxvoice.core.store import CommandStore

log = logging.getLogger(__name__)

# Роль элемента дерева: id узла или команды.
ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_KIND = Qt.ItemDataRole.UserRole + 1


class CommandEditor(QWidget):
    """Полноценный редактор команд."""

    command_saved = pyqtSignal(str)
    test_requested = pyqtSignal(str)

    def __init__(self, store: CommandStore, settings: Settings,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._settings = settings
        self._current_command: Command | None = None
        self._current_node_id = ""
        self._dirty = False

        self._build_ui()
        self.refresh_tree()

        self._unsubscribe = bus.subscribe(COMMANDS_CHANGED, self._on_external_change)

    # --- Построение -------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # --- Панель инструментов ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText(f"{tr('Поиск')} по командам и фразам…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search, 2)

        for label, slot, tip in (
            ("+ Коллекция", self._create_collection, "Создать коллекцию верхнего уровня"),
            ("+ Папка", self._create_folder, "Создать папку внутри выбранного"),
            ("+ Команда", self._create_command, "Создать команду в выбранной папке"),
        ):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            toolbar.addWidget(button)

        root.addLayout(toolbar)

        # --- Основная область: дерево и карточка ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Дерево
        tree_container = QFrame()
        tree_container.setObjectName("Card")
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(6, 6, 6, 6)
        tree_layout.setSpacing(4)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Команды", "Фраза"])
        self._tree.setColumnWidth(0, 260)
        self._tree.setAlternatingRowColors(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_menu)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        tree_layout.addWidget(self._tree, 1)

        self._stats_label = QLabel("")
        self._stats_label.setObjectName("Hint")
        tree_layout.addWidget(self._stats_label)

        splitter.addWidget(tree_container)

        # Карточка команды
        card_container = QFrame()
        card_container.setObjectName("Card")
        card_layout = QVBoxLayout(card_container)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)

        # Заголовок
        title_row = QHBoxLayout()
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText(tr("Название"))
        self._title_edit.setObjectName("Title")
        self._title_edit.textChanged.connect(self._mark_dirty)
        title_row.addWidget(self._title_edit, 1)

        self._enabled_check = QCheckBox(tr("Включена"))
        self._enabled_check.setToolTip("Выключенная команда не выполняется, "
                                       "но остаётся в дереве")
        self._enabled_check.toggled.connect(self._mark_dirty)
        title_row.addWidget(self._enabled_check)
        card_layout.addLayout(title_row)

        hint = QLabel("Изменения сохраняются автоматически")
        hint.setObjectName("Hint")
        card_layout.addWidget(hint)

        # Область прокрутки карточки
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 6, 0)
        inner_layout.setSpacing(10)

        # --- Фразы ---
        phrases_group = QFrame()
        phrases_group.setObjectName("Card")
        phrases_layout = QVBoxLayout(phrases_group)
        phrases_layout.setContentsMargins(10, 8, 10, 8)
        phrases_layout.setSpacing(6)

        phrases_title = QLabel(tr("Фразы для активации"))
        phrases_title.setObjectName("SectionTitle")
        phrases_layout.addWidget(phrases_title)

        phrases_hint = QLabel(
            "Что нужно сказать, чтобы команда сработала. "
            "Добавьте несколько вариантов — ассистент поймёт любой из них.")
        phrases_hint.setObjectName("Hint")
        phrases_hint.setWordWrap(True)
        phrases_layout.addWidget(phrases_hint)

        self._phrases_list = QListWidget()
        self._phrases_list.setMaximumHeight(130)
        phrases_layout.addWidget(self._phrases_list)

        phrase_row = QHBoxLayout()
        self._phrase_input = QLineEdit()
        self._phrase_input.setPlaceholderText("новая фраза, например: сделай громче")
        self._phrase_input.returnPressed.connect(self._add_phrase)
        phrase_row.addWidget(self._phrase_input, 1)

        add_phrase = QPushButton(tr("Добавить"))
        add_phrase.clicked.connect(self._add_phrase)
        phrase_row.addWidget(add_phrase)

        remove_phrase = QPushButton(tr("Удалить"))
        remove_phrase.setObjectName("Ghost")
        remove_phrase.clicked.connect(self._remove_phrase)
        phrase_row.addWidget(remove_phrase)
        phrases_layout.addLayout(phrase_row)

        # Необязательные слова
        optional_row = QHBoxLayout()
        optional_label = QLabel("Необязательные слова:")
        optional_label.setObjectName("Hint")
        optional_row.addWidget(optional_label)
        self._optional_edit = QLineEdit()
        self._optional_edit.setPlaceholderText("пожалуйста, давай, быстро")
        self._optional_edit.setToolTip(
            "Эти слова можно сказать до или после фразы — команда всё равно сработает")
        self._optional_edit.textChanged.connect(self._mark_dirty)
        optional_row.addWidget(self._optional_edit, 1)
        phrases_layout.addLayout(optional_row)

        inner_layout.addWidget(phrases_group)

        # --- Действия ---
        actions_group = QFrame()
        actions_group.setObjectName("Card")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setContentsMargins(10, 8, 10, 8)
        actions_layout.setSpacing(6)

        actions_header = QHBoxLayout()
        actions_title = QLabel(tr("Действия"))
        actions_title.setObjectName("SectionTitle")
        actions_header.addWidget(actions_title)
        actions_header.addStretch(1)

        self._actions_count = QLabel("")
        self._actions_count.setObjectName("Hint")
        actions_header.addWidget(self._actions_count)
        actions_layout.addLayout(actions_header)

        actions_hint = QLabel(
            "Шаги выполняются по порядку сверху вниз. "
            "Порядок меняется стрелками, действие настраивается ниже.")
        actions_hint.setObjectName("Hint")
        actions_hint.setWordWrap(True)
        actions_layout.addWidget(actions_hint)

        self._actions_list = QListWidget()
        self._actions_list.setMinimumHeight(140)
        self._actions_list.currentRowChanged.connect(self._on_action_selected)
        actions_layout.addWidget(self._actions_list)

        # Кнопки управления списком действий
        action_buttons = QHBoxLayout()
        action_buttons.setSpacing(4)

        for label, slot, tip in (
            ("↑", lambda: self._move_action(-1), "Поднять действие"),
            ("↓", lambda: self._move_action(1), "Опустить действие"),
            ("Дублировать", self._duplicate_action, "Скопировать действие"),
            ("Удалить", self._remove_action, "Удалить действие"),
        ):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.setObjectName("Ghost" if len(label) > 2 else "IconButton")
            button.clicked.connect(slot)
            action_buttons.addWidget(button)
        action_buttons.addStretch(1)
        actions_layout.addLayout(action_buttons)

        # --- Выбор действия ---
        pick_row = QHBoxLayout()
        pick_row.setSpacing(6)

        self._group_combo = QComboBox()
        for key, label, _description in catalog.groups():
            self._group_combo.addItem(tr(label), key)
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)
        pick_row.addWidget(self._group_combo, 1)

        self._action_combo = QComboBox()
        self._action_combo.currentIndexChanged.connect(self._on_action_type_changed)
        pick_row.addWidget(self._action_combo, 2)

        insert_button = QPushButton("Вставить")
        insert_button.setObjectName("Primary")
        insert_button.clicked.connect(self._insert_action)
        pick_row.addWidget(insert_button)
        actions_layout.addLayout(pick_row)

        self._action_help = QLabel("")
        self._action_help.setObjectName("Hint")
        self._action_help.setWordWrap(True)
        actions_layout.addWidget(self._action_help)

        # --- Параметры действия ---
        self._params_frame = QFrame()
        self._params_frame.setObjectName("Card")
        self._params_layout = QFormLayout(self._params_frame)
        self._params_layout.setContentsMargins(10, 8, 10, 8)
        self._params_layout.setSpacing(6)
        actions_layout.addWidget(self._params_frame)

        inner_layout.addWidget(actions_group)

        # --- Свойства команды ---
        options_group = QFrame()
        options_group.setObjectName("Card")
        options_layout = QFormLayout(options_group)
        options_layout.setContentsMargins(10, 8, 10, 8)
        options_layout.setSpacing(6)

        options_title = QLabel("Настройки команды")
        options_title.setObjectName("SectionTitle")
        options_layout.addRow(options_title)

        self._confirm_check = QCheckBox("Спрашивать подтверждение перед выполнением")
        self._confirm_check.setToolTip(
            "Ассистент произнесёт вопрос и будет ждать ответа «да» или «нет». "
            "Обязательно для выключения компьютера и удаления файлов.")
        self._confirm_check.toggled.connect(self._mark_dirty)
        options_layout.addRow(self._confirm_check)

        self._chain_check = QCheckBox("Можно связывать с другими командами")
        self._chain_check.setToolTip(
            "Позволяет сказать «открой ютуб и сделай громче» одной фразой")
        self._chain_check.toggled.connect(self._mark_dirty)
        options_layout.addRow(self._chain_check)

        self._contexts_edit = QLineEdit()
        self._contexts_edit.setPlaceholderText("youtube, spotify, minecraft")
        self._contexts_edit.setToolTip(
            "Команда будет срабатывать точнее, когда активно окно с этим названием. "
            "Полезно для коротких фраз вроде «пауза». Через запятую.")
        self._contexts_edit.textChanged.connect(self._mark_dirty)
        options_layout.addRow("Учитывать активное окно:", self._contexts_edit)

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("звук, медиа, работа")
        self._tags_edit.setToolTip("Метки для поиска и группировки")
        self._tags_edit.textChanged.connect(self._mark_dirty)
        options_layout.addRow("Метки:", self._tags_edit)

        self._weight_spin = QSpinBox()
        self._weight_spin.setRange(-100, 100)
        self._weight_spin.setToolTip(
            "Приоритет при одинаковой оценке совпадения. "
            "Больше — команда выбирается чаще.")
        self._weight_spin.valueChanged.connect(self._mark_dirty)
        options_layout.addRow("Приоритет:", self._weight_spin)

        self._cooldown_spin = QSpinBox()
        self._cooldown_spin.setRange(0, 3600)
        self._cooldown_spin.setSuffix(" с")
        self._cooldown_spin.setToolTip(
            "Защита от повторного запуска: сколько секунд ждать")
        self._cooldown_spin.valueChanged.connect(self._mark_dirty)
        options_layout.addRow("Пауза между запусками:", self._cooldown_spin)

        self._rate_spin = QSpinBox()
        self._rate_spin.setRange(0, 100)
        self._rate_spin.setSuffix(" раз/мин")
        self._rate_spin.setToolTip("0 — без ограничения")
        self._rate_spin.valueChanged.connect(self._mark_dirty)
        options_layout.addRow("Ограничение частоты:", self._rate_spin)

        self._run_info = QLabel("")
        self._run_info.setObjectName("Hint")
        options_layout.addRow(self._run_info)

        inner_layout.addWidget(options_group)

        # --- Кнопки карточки ---
        card_buttons = QHBoxLayout()
        test_button = QPushButton("Проверить команду")
        test_button.setToolTip("Выполнить команду прямо сейчас")
        test_button.clicked.connect(self._test_command)
        card_buttons.addWidget(test_button)

        clone_button = QPushButton(tr("Клонировать"))
        clone_button.clicked.connect(self._clone_command)
        card_buttons.addWidget(clone_button)

        export_button = QPushButton(tr("Экспорт"))
        export_button.clicked.connect(self._export_command)
        card_buttons.addWidget(export_button)

        card_buttons.addStretch(1)

        delete_button = QPushButton(tr("Удалить"))
        delete_button.setObjectName("Danger")
        delete_button.clicked.connect(self._delete_command)
        card_buttons.addWidget(delete_button)
        inner_layout.addLayout(card_buttons)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        card_layout.addWidget(scroll, 1)

        splitter.addWidget(card_container)
        splitter.setSizes([340, 620])
        root.addWidget(splitter, 1)

        # Заполняем список действий первой группы.
        self._on_group_changed(0)
        self._set_card_enabled(False)

    # --- Дерево -----------------------------------------------------------

    def refresh_tree(self, select_id: str = "") -> None:
        """Перестроить дерево команд."""
        self._tree.blockSignals(True)
        self._tree.clear()

        for node in self._store.collections():
            item = self._make_item(node)
            self._tree.addTopLevelItem(item)

        self._tree.blockSignals(False)
        self._tree.expandAll()

        if select_id:
            self._select_item(select_id)

        stats = self._store.statistics()
        self._stats_label.setText(
            f"Всего команд: {stats['commands']} "
            f"(включено {stats['enabled']}), "
            f"действий: {stats['actions']}, "
            f"фраз: {stats['phrases']}")

    def _make_item(self, node: Node) -> QTreeWidgetItem:
        """Создать элемент дерева для узла."""
        item = QTreeWidgetItem()
        item.setText(0, node.title or "Без названия")
        item.setData(0, ROLE_ID, node.id)
        item.setData(0, ROLE_KIND, node.kind)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                      | Qt.ItemFlag.ItemIsDragEnabled
                      | Qt.ItemFlag.ItemIsDropEnabled
                      | Qt.ItemFlag.ItemIsEditable)
        item.setCheckState(0, Qt.CheckState.Checked if node.enabled
                           else Qt.CheckState.Unchecked)

        if node.kind == KIND_COLLECTION:
            item.setToolTip(0, "Коллекция — верхний уровень дерева")
        else:
            item.setToolTip(0, "Папка внутри коллекции")

        # Команды внутри узла.
        for command in sorted(self._store.commands_in(node.id, recursive=False),
                              key=lambda c: c.title.lower()):
            child = QTreeWidgetItem()
            child.setText(0, command.title or "Без названия")
            child.setText(1, command.primary_phrase())
            child.setData(0, ROLE_ID, command.id)
            child.setData(0, ROLE_KIND, KIND_COMMAND)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable
                           | Qt.ItemFlag.ItemIsDragEnabled
                           | Qt.ItemFlag.ItemIsEditable)
            child.setCheckState(0, Qt.CheckState.Checked if command.enabled
                                else Qt.CheckState.Unchecked)

            tooltip = "\n".join(command.all_phrases()[:6]) or "фразы не заданы"
            if command.confirm:
                tooltip += "\n\nТребует подтверждения"
            if command.pack:
                tooltip += f"\n\nИз пака: {command.pack}"
            if command.user_modified:
                tooltip += " (изменена вами)"
            child.setToolTip(0, tooltip)
            item.addChild(child)

        # Подпапки.
        for child_node in sorted(node.children, key=lambda n: n.title.lower()):
            item.addChild(self._make_item(child_node))

        item.setExpanded(node.expanded)
        return item

    def _select_item(self, target_id: str) -> None:
        """Выделить элемент по идентификатору."""
        iterator = self._tree.findItems(
            "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive, 0)
        candidates = list(iterator)
        for item in candidates:
            if item.data(0, ROLE_ID) == target_id:
                self._tree.setCurrentItem(item)
                self._tree.scrollToItem(item)
                return

    def _on_tree_selection(self) -> None:
        """Выбрана команда или узел."""
        item = self._tree.currentItem()
        if item is None:
            return

        kind = item.data(0, ROLE_KIND)
        target_id = item.data(0, ROLE_ID)

        if kind == KIND_COMMAND:
            command = self._store.get(str(target_id))
            if command is not None:
                self._load_command(command)
            return

        # Выбран узел — показываем карточку узла (только название).
        self._current_node_id = str(target_id)
        self._current_command = None
        self._set_card_enabled(False)
        node = self._store.find_node(str(target_id))
        if node is not None:
            self._title_edit.setText(node.title)
            self._enabled_check.setChecked(node.enabled)
            self._phrases_list.clear()
            self._actions_list.clear()
            self._actions_count.setText("Выберите команду, чтобы изменить её шаги")

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Изменён флажок или название в дереве."""
        kind = item.data(0, ROLE_KIND)
        target_id = str(item.data(0, ROLE_ID) or "")
        if not target_id:
            return

        # Включение и выключение.
        if column == 0:
            wanted = item.checkState(0) == Qt.CheckState.Checked
            if kind == KIND_COMMAND:
                command = self._store.get(target_id)
                if command is not None and command.enabled != wanted:
                    command.enabled = wanted
                    command.user_modified = True
                    self._store.update_command(command)
                    self._store.save_soon()
            else:
                node = self._store.find_node(target_id)
                if node is not None and node.enabled != wanted:
                    self._store.set_enabled(target_id, wanted)
                    self._store.save_soon()
                elif node is not None:
                    # Название могло измениться вручную.
                    new_title = item.text(0).strip()
                    if new_title and new_title != node.title:
                        self._store.rename_node(target_id, new_title)
                        node.user_modified = True
                        self._store.save_soon()

    def _show_tree_menu(self, position: Any) -> None:
        """Контекстное меню дерева."""
        item = self._tree.itemAt(position)
        menu = QMenu(self)

        if item is not None:
            kind = item.data(0, ROLE_KIND)
            target_id = str(item.data(0, ROLE_ID) or "")

            if kind == KIND_COMMAND:
                menu.addAction("Проверить", lambda: self._test_command())
                menu.addAction("Клонировать", lambda: self._clone_command())
                menu.addAction("Экспорт", lambda: self._export_command())
                menu.addSeparator()

            if kind in (KIND_COLLECTION, KIND_FOLDER):
                menu.addAction("Создать папку", lambda: self._create_folder())
                menu.addAction("Создать команду", lambda: self._create_command())
                menu.addSeparator()
                menu.addAction("Переименовать", lambda: self._rename_node(target_id))
                menu.addAction("Экспорт коллекции", lambda: self._export_node(target_id))
                menu.addSeparator()

            enabled = item.checkState(0) == Qt.CheckState.Checked
            menu.addAction("Включить" if not enabled else "Выключить",
                           lambda: self._toggle_item(item))
            menu.addSeparator()
            menu.addAction("Удалить", lambda: self._delete_node_or_command(
                target_id, kind))
        else:
            menu.addAction("Создать коллекцию", self._create_collection)
            menu.addSeparator()
            menu.addAction("Развернуть всё", self._tree.expandAll)
            menu.addAction("Свернуть всё", self._tree.collapseAll)

        menu.addSeparator()
        menu.addAction("Развернуть всё", self._tree.expandAll)
        menu.addAction("Свернуть всё", self._tree.collapseAll)
        menu.exec(self._tree.viewport().mapToGlobal(position))

    def _toggle_item(self, item: QTreeWidgetItem) -> None:
        state = item.checkState(0)
        item.setCheckState(0, Qt.CheckState.Unchecked
                           if state == Qt.CheckState.Checked
                           else Qt.CheckState.Checked)

    def _on_external_change(self) -> None:
        """Дерево изменилось извне — обновляем отображение."""
        current = ""
        item = self._tree.currentItem()
        if item is not None:
            current = str(item.data(0, ROLE_ID) or "")
        self.refresh_tree(current)

    def _on_search(self, text: str) -> None:
        """Поиск по дереву."""
        needle = text.strip().lower()
        if not needle:
            self.refresh_tree(
                str(self._tree.currentItem().data(0, ROLE_ID))
                if self._tree.currentItem() else "")
            return

        self._tree.blockSignals(True)
        self._tree.clear()

        found = self._store.search(text, limit=300)
        for command in found:
            owner = self._store.node_of(command.id)
            path = ""
            if owner is not None:
                path = " → ".join(node.title for node in
                                  self._store.path_of(owner.id))

            item = QTreeWidgetItem()
            item.setText(0, command.title or "Без названия")
            item.setText(1, f"{path}  ·  {command.primary_phrase()}")
            item.setData(0, ROLE_ID, command.id)
            item.setData(0, ROLE_KIND, KIND_COMMAND)
            item.setCheckState(0, Qt.CheckState.Checked if command.enabled
                               else Qt.CheckState.Unchecked)
            self._tree.addTopLevelItem(item)

        self._tree.blockSignals(False)
        self._stats_label.setText(f"Найдено команд: {len(found)}")

    # --- Создание и удаление ---------------------------------------------

    def _create_collection(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Создать коллекцию"),
                                        "Название коллекции:")
        if ok and name.strip():
            node = self._store.create_collection(name.strip())
            self._store.save()
            self.refresh_tree(node.id)

    def _create_folder(self) -> None:
        item = self._tree.currentItem()
        if item is None:
            QMessageBox.information(self, "Папка",
                                    "Сначала выберите коллекцию или папку.")
            return

        kind = item.data(0, ROLE_KIND)
        if kind == KIND_COMMAND:
            QMessageBox.information(
                self, "Папка",
                "Папку можно создать только внутри коллекции или папки.")
            return

        parent_id = str(item.data(0, ROLE_ID) or "")
        name, ok = QInputDialog.getText(self, tr("Создать папку"),
                                        "Название папки:")
        if ok and name.strip():
            node = self._store.create_folder(parent_id, name.strip())
            self._store.save()
            self.refresh_tree(node.id if node else "")

    def _create_command(self) -> None:
        item = self._tree.currentItem()
        if item is None:
            QMessageBox.information(
                self, "Команда",
                "Сначала выберите папку или коллекцию, где создать команду.")
            return

        if item.data(0, ROLE_KIND) == KIND_COMMAND:
            item = item.parent()
            if item is None:
                return

        node_id = str(item.data(0, ROLE_ID) or "")
        command = self._store.create_command(node_id, tr("Новая команда"))
        if command is None:
            return

        self._store.save()
        self.refresh_tree(command.id)
        self._title_edit.setFocus()
        self._title_edit.selectAll()

    def _rename_node(self, node_id: str) -> None:
        node = self._store.find_node(node_id)
        if node is None:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Новое название:",
                                        text=node.title)
        if ok and name.strip():
            self._store.rename_node(node_id, name.strip())
            self._store.save()
            self.refresh_tree(node_id)

    def _delete_node_or_command(self, target_id: str, kind: str) -> None:
        if kind == KIND_COMMAND:
            command = self._store.get(target_id)
            if command is None:
                return
            if self._settings.flag("ui.confirm_delete", True):
                answer = QMessageBox.question(
                    self, "Удалить команду",
                    f"Удалить команду «{command.title}»?\n\n"
                    f"Фраз: {len(command.phrases)}, действий: {len(command.actions)}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self._store.remove_command(target_id)
        else:
            commands = self._store.commands_in(target_id)
            node = self._store.find_node(target_id)
            title = node.title if node else "узел"

            if self._settings.flag("ui.confirm_delete", True):
                answer = QMessageBox.question(
                    self, "Удалить",
                    f"Удалить «{title}» вместе с командой внутри?\n\n"
                    f"Будет удалено команд: {len(commands)}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self._store.remove_node(target_id)

        self._store.save()
        self._current_command = None
        self._set_card_enabled(False)
        self.refresh_tree()

    def _delete_command(self) -> None:
        if self._current_command is None:
            return
        self._delete_node_or_command(self._current_command.id, KIND_COMMAND)

    # --- Карточка команды -------------------------------------------------

    def _set_card_enabled(self, enabled: bool) -> None:
        """Включить или заблокировать поля карточки."""
        for widget in (self._title_edit, self._enabled_check, self._phrase_input,
                       self._phrases_list, self._actions_list, self._group_combo,
                       self._action_combo, self._optional_edit, self._confirm_check,
                       self._chain_check, self._contexts_edit, self._tags_edit,
                       self._weight_spin, self._cooldown_spin, self._rate_spin):
            widget.setEnabled(enabled)

    def _load_command(self, command: Command) -> None:
        """Показать команду в карточке."""
        self._current_command = command

        for widget in (self._title_edit, self._optional_edit, self._contexts_edit,
                       self._tags_edit):
            widget.blockSignals(True)
        for widget in (self._enabled_check, self._confirm_check, self._chain_check):
            widget.blockSignals(True)
        for widget in (self._weight_spin, self._cooldown_spin, self._rate_spin):
            widget.blockSignals(True)

        try:
            self._title_edit.setText(command.title)
            self._enabled_check.setChecked(command.enabled)
            self._optional_edit.setText(", ".join(command.optional))
            self._contexts_edit.setText(", ".join(command.contexts))
            self._tags_edit.setText(", ".join(command.tags))
            self._confirm_check.setChecked(command.confirm)
            self._chain_check.setChecked(command.chainable)
            self._weight_spin.setValue(command.weight)
            self._cooldown_spin.setValue(int(command.cooldown))
            self._rate_spin.setValue(command.rate_limit)
        finally:
            for widget in (self._title_edit, self._optional_edit, self._contexts_edit,
                           self._tags_edit, self._enabled_check, self._confirm_check,
                           self._chain_check, self._weight_spin, self._cooldown_spin,
                           self._rate_spin):
                widget.blockSignals(False)

        self._refresh_phrases()
        self._refresh_actions()

        if command.run_count:
            import time as _time
            when = (_time.strftime("%d.%m.%Y %H:%M",
                                   _time.localtime(command.last_run))
                    if command.last_run else "неизвестно")
            self._run_info.setText(
                f"Запусков: {command.run_count}, последний: {when}")
        else:
            self._run_info.setText("Команда ещё не запускалась")

        self._set_card_enabled(True)
        self._dirty = False

    def _mark_dirty(self) -> None:
        """Отметить изменения и сохранить их."""
        if self._current_command is None:
            # Правки в узле — сохраняем название.
            if self._current_node_id:
                item = self._tree.currentItem()
                if item is not None and item.data(0, ROLE_KIND) != KIND_COMMAND:
                    node = self._store.find_node(self._current_node_id)
                    if node is not None:
                        new_title = self._title_edit.text().strip()
                        if new_title and new_title != node.title:
                            self._store.rename_node(self._current_node_id, new_title)
                            self._store.save_soon()
                            self.refresh_tree(self._current_node_id)
            return

        command = self._current_command
        command.title = self._title_edit.text()
        command.enabled = self._enabled_check.isChecked()
        command.confirm = self._confirm_check.isChecked()
        command.chainable = self._chain_check.isChecked()
        command.weight = self._weight_spin.value()
        command.cooldown = float(self._cooldown_spin.value())
        command.rate_limit = self._rate_spin.value()

        command.optional = [word.strip().lower()
                            for word in self._optional_edit.text().replace(";", ",").split(",")
                            if word.strip()]
        command.contexts = [item.strip() for item
                            in self._contexts_edit.text().replace(";", ",").split(",")
                            if item.strip()]
        command.tags = [item.strip() for item
                        in self._tags_edit.text().replace(";", ",").split(",")
                        if item.strip()]

        command.user_modified = bool(command.pack) or command.user_modified
        self._store.update_command(command)
        self._store.save_soon()
        self.command_saved.emit(command.id)

        # Обновляем строку в дереве без полной перестройки.
        item = self._tree.currentItem()
        if item is not None and item.data(0, ROLE_KIND) == KIND_COMMAND:
            item.setText(0, command.title or "Без названия")
            item.setText(1, command.primary_phrase())

    # --- Фразы ------------------------------------------------------------

    def _refresh_phrases(self) -> None:
        self._phrases_list.clear()
        if self._current_command is None:
            return
        for phrase in self._current_command.phrases:
            item = QListWidgetItem(phrase)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._phrases_list.addItem(item)

    def _add_phrase(self) -> None:
        if self._current_command is None:
            return
        text = self._phrase_input.text().strip()
        if not text:
            return

        # Предупреждаем о дубликате: две команды с одной фразой
        # будут конкурировать за срабатывание.
        owner = self._store.phrase_owner(text)
        if owner is not None and owner.id != self._current_command.id:
            answer = QMessageBox.question(
                self, "Фраза уже используется",
                f"Фраза «{text}» уже есть у команды «{owner.title}».\n\n"
                "Обе команды будут конкурировать. Всё равно добавить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        if self._current_command.add_phrase(text):
            self._phrase_input.clear()
            self._mark_dirty()
            self._refresh_phrases()

    def _remove_phrase(self) -> None:
        if self._current_command is None:
            return
        row = self._phrases_list.currentRow()
        if 0 <= row < len(self._current_command.phrases):
            del self._current_command.phrases[row]
            self._mark_dirty()
            self._refresh_phrases()

    # --- Действия ---------------------------------------------------------

    def _refresh_actions(self) -> None:
        """Перерисовать список действий команды."""
        self._actions_list.clear()
        if self._current_command is None:
            self._actions_count.setText("")
            return

        for index, action in enumerate(self._current_command.actions, 1):
            text = self._describe_action(action, index)
            item = QListWidgetItem(text)
            item.setData(ROLE_ID, action.id)
            if not action.enabled:
                item.setForeground(Qt.GlobalColor.gray)
                item.setText(f"{text}  (выключено)")
            spec = catalog.get_spec(action.type)
            if spec is not None and spec.dangerous:
                item.setToolTip("Опасное действие — проверьте настройки "
                                "подтверждения")
            self._actions_list.addItem(item)

        count = len(self._current_command.actions)
        self._actions_count.setText(f"Шагов: {count}")

        if count and self._actions_list.currentRow() < 0:
            self._actions_list.setCurrentRow(0)

    def _describe_action(self, action: Action, index: int) -> str:
        """Человекочитаемое описание шага."""
        label = catalog.label_for(action.type)
        value = action.value
        if value:
            short = value.replace("\n", " ")
            if len(short) > 60:
                short = short[:57] + "…"
            return f"{index}. {label}: {short}"
        return f"{index}. {label}"

    def _on_group_changed(self, _index: int) -> None:
        """Сменилась группа действий — обновляем список."""
        group = self._group_combo.currentData()
        self._action_combo.clear()
        for spec in catalog.group_actions(str(group)):
            self._action_combo.addItem(tr(spec.label), spec.type)
        self._on_action_type_changed(0)

    def _on_action_type_changed(self, _index: int) -> None:
        """Сменился тип действия — показываем подсказку."""
        spec = catalog.get_spec(str(self._action_combo.currentData() or ""))
        if spec is None:
            self._action_help.setText("")
            return
        text = spec.help
        if spec.dangerous:
            text += "  ⚠ Опасное действие: рекомендуется включить подтверждение."
        if spec.platform_note:
            text += f"  ({spec.platform_note})"
        self._action_help.setText(text)

    def _insert_action(self) -> None:
        if self._current_command is None:
            QMessageBox.information(self, "Действие",
                                    "Сначала выберите команду в дереве.")
            return

        action_type = str(self._action_combo.currentData() or "")
        spec = catalog.get_spec(action_type)
        if spec is None:
            return

        params: dict[str, Any] = {}
        for param in spec.params:
            if param.required or param.default not in ("", None):
                params[param.key] = param.default

        action = Action(type=action_type, params=params)
        insert_at = self._actions_list.currentRow()
        if insert_at < 0:
            self._current_command.add_action(action)
            row = len(self._current_command.actions) - 1
        else:
            self._current_command.add_action(action, insert_at + 1)
            row = insert_at + 1

        self._mark_dirty()
        self._refresh_actions()
        self._actions_list.setCurrentRow(row)

    def _move_action(self, offset: int) -> None:
        if self._current_command is None:
            return
        row = self._actions_list.currentRow()
        if self._current_command.move_action(row, offset):
            self._mark_dirty()
            self._refresh_actions()
            self._actions_list.setCurrentRow(row + offset)

    def _duplicate_action(self) -> None:
        if self._current_command is None:
            return
        row = self._actions_list.currentRow()
        if not 0 <= row < len(self._current_command.actions):
            return
        original = self._current_command.actions[row]
        clone = Action.from_dict(original.to_dict())
        clone.id = Action().id
        self._current_command.add_action(clone, row + 1)
        self._mark_dirty()
        self._refresh_actions()
        self._actions_list.setCurrentRow(row + 1)

    def _remove_action(self) -> None:
        if self._current_command is None:
            return
        row = self._actions_list.currentRow()
        if self._current_command.remove_action(row):
            self._mark_dirty()
            self._refresh_actions()

    def _on_action_selected(self, row: int) -> None:
        """Показать параметры выбранного действия."""
        # Полностью очищаем форму параметров. У QFormLayout строки
        # удаляются только через removeRow: takeAt снимает элементы,
        # но строки остаются зарегистрированными и поля накапливаются.
        self._clear_form(self._params_layout)

        if self._current_command is None or not 0 <= row < len(
                self._current_command.actions):
            self._params_frame.setVisible(False)
            return

        action = self._current_command.actions[row]
        spec = catalog.get_spec(action.type)
        if spec is None:
            self._params_frame.setVisible(False)
            return

        self._params_frame.setVisible(True)

        title = QLabel(f"{tr('Параметр')}: {tr(spec.label)}")
        title.setObjectName("SectionTitle")
        self._params_layout.addRow(title)

        for param in spec.params:
            editor = self._make_param_editor(action, param)
            if editor is None:
                continue
            label = QLabel(tr(param.label))
            if param.help:
                label.setToolTip(param.help)
            self._params_layout.addRow(label, editor)

        # Общие свойства шага.
        self._params_layout.addRow(self._make_step_options(action))

    @staticmethod
    def _clear_form(layout: QFormLayout) -> None:
        """Полностью очистить форму: удаляются и элементы, и строки."""
        while layout.rowCount():
            layout.removeRow(0)

    def _make_param_editor(self, action: Action, param: catalog.Param) -> QWidget | None:
        """Создать поле ввода для параметра действия."""
        value = action.params.get(param.key, param.default)
        kind = param.kind

        def store(new_value: Any) -> None:
            action.params[param.key] = new_value
            if self._current_command is not None:
                self._mark_dirty()
                row = self._actions_list.currentRow()
                if 0 <= row < len(self._current_command.actions):
                    self._actions_list.item(row).setText(
                        self._describe_action(action, row + 1))

        if kind == "bool":
            editor = QCheckBox()
            editor.setChecked(bool(value))
            editor.toggled.connect(store)
            return editor

        if kind == "int":
            editor = QSpinBox()
            editor.setRange(int(param.minimum if param.minimum is not None else -100000),
                            int(param.maximum if param.maximum is not None else 100000))
            editor.setValue(int(float(value or 0)))
            if param.unit:
                editor.setSuffix(f" {param.unit}")
            editor.valueChanged.connect(store)
            return editor

        if kind == "float":
            from PyQt6.QtWidgets import QDoubleSpinBox
            editor = QDoubleSpinBox()
            editor.setDecimals(2)
            editor.setRange(float(param.minimum if param.minimum is not None else -100000),
                            float(param.maximum if param.maximum is not None else 100000))
            editor.setSingleStep(param.step or 0.1)
            editor.setValue(float(value or 0))
            if param.unit:
                editor.setSuffix(f" {param.unit}")
            editor.valueChanged.connect(store)
            return editor

        if kind == "choice":
            editor = QComboBox()
            for key, label in param.choices:
                editor.addItem(tr(label), key)
            index = editor.findData(value)
            if index >= 0:
                editor.setCurrentIndex(index)
            editor.currentIndexChanged.connect(
                lambda _i: store(editor.currentData()))
            return editor

        if kind == "text":
            editor = QTextEdit()
            editor.setPlainText(str(value or ""))
            editor.setMaximumHeight(80)
            editor.setPlaceholderText(param.placeholder)
            editor.textChanged.connect(lambda: store(editor.toPlainText()))
            return editor

        if kind in ("path", "dir", "file"):
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            editor = QLineEdit(str(value or ""))
            editor.setPlaceholderText(param.placeholder)
            editor.textChanged.connect(store)
            layout.addWidget(editor, 1)

            browse = QPushButton("…")
            browse.setFixedWidth(32)

            def choose() -> None:
                if kind == "dir":
                    chosen = QFileDialog.getExistingDirectory(self, "Выберите каталог")
                else:
                    chosen, _ = QFileDialog.getOpenFileName(self, "Выберите файл")
                if chosen:
                    editor.setText(chosen)

            browse.clicked.connect(choose)
            layout.addWidget(browse)
            return container

        if kind == "keys":
            from luxvoice.sysint.uinput import parse_keys, describe_keys
            editor = QLineEdit(str(value or ""))
            editor.setPlaceholderText("например: ctrl+shift+s")
            editor.setToolTip("Напечатайте сочетание вручную или введите "
                              "названия клавиш через плюс")

            preview = QLabel("")
            preview.setObjectName("Hint")

            def update(text: str) -> None:
                store(text)
                if text.strip():
                    codes = parse_keys(text)
                    preview.setText(describe_keys(text) if codes
                                    else "сочетание не распознано")
                else:
                    preview.setText("")

            editor.textChanged.connect(update)
            update(str(value or ""))

            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            layout.addWidget(editor)
            layout.addWidget(preview)
            return container

        if param.dynamic == "apps":
            editor = QComboBox()
            editor.setEditable(True)
            editor.addItem("активное окно", "")
            try:
                from luxvoice.sysint.audio import get_audio
                for stream in get_audio().application_volumes():
                    editor.addItem(str(stream.get("app", "")),
                                   str(stream.get("app", "")))
            except Exception:  # noqa: BLE001
                pass
            editor.setCurrentText(str(value or ""))
            editor.currentTextChanged.connect(store)
            return editor

        # Строка по умолчанию.
        editor = QLineEdit(str(value or ""))
        editor.setPlaceholderText(param.placeholder)
        editor.textChanged.connect(store)
        return editor

    def _make_step_options(self, action: Action) -> QWidget:
        """Общие свойства шага: паузы, повторы, поведение при ошибке."""
        container = QWidget()
        layout = QFormLayout(container)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(4)

        enabled = QCheckBox("Выполнять этот шаг")
        enabled.setChecked(action.enabled)
        enabled.toggled.connect(lambda state: (setattr(action, "enabled", state),
                                               self._mark_dirty()))
        layout.addRow(enabled)

        continue_check = QCheckBox("Продолжать, если шаг не удался")
        continue_check.setChecked(action.continue_on_error)
        continue_check.setToolTip("Иначе команда остановится на ошибке")
        continue_check.toggled.connect(
            lambda state: (setattr(action, "continue_on_error", state),
                           self._mark_dirty()))
        layout.addRow(continue_check)

        before = QSpinBox()
        before.setRange(0, 600)
        before.setSuffix(" с")
        before.setValue(int(action.delay_before))
        before.setToolTip("Подождать перед этим шагом")
        before.valueChanged.connect(
            lambda value: (setattr(action, "delay_before", float(value)),
                           self._mark_dirty()))
        layout.addRow("Пауза перед шагом:", before)

        after = QSpinBox()
        after.setRange(0, 600)
        after.setSuffix(" с")
        after.setValue(int(action.delay_after))
        after.setToolTip("Подождать после этого шага — нужно после запуска программ")
        after.valueChanged.connect(
            lambda value: (setattr(action, "delay_after", float(value)),
                           self._mark_dirty()))
        layout.addRow("Пауза после шага:", after)

        retries = QSpinBox()
        retries.setRange(0, 10)
        retries.setValue(action.retries)
        retries.setToolTip("Сколько раз повторить шаг при неудаче")
        retries.valueChanged.connect(
            lambda value: (setattr(action, "retries", value), self._mark_dirty()))
        layout.addRow("Повторов при ошибке:", retries)

        return container

    # --- Действия над командой -------------------------------------------

    def _test_command(self) -> None:
        if self._current_command is None:
            return
        self.test_requested.emit(self._current_command.id)

    def _clone_command(self) -> None:
        if self._current_command is None:
            return
        clone = self._current_command.duplicate(keep_phrases=True)
        owner = self._store.node_of(self._current_command.id)
        if owner is None:
            return
        self._store.add_command(clone, owner.id)
        self._store.save()
        self.refresh_tree(clone.id)

    def _export_command(self) -> None:
        if self._current_command is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт команды",
            f"{self._current_command.title}.json", "JSON (*.json)")
        if not path:
            return

        import json
        from pathlib import Path
        try:
            data = self._store.export_many([self._current_command.id])
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Экспорт",
                                    f"Команда сохранена в файл:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить: {exc}")

    def _export_node(self, node_id: str) -> None:
        import json
        from pathlib import Path

        node = self._store.find_node(node_id)
        if node is None:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт коллекции", f"{node.title}.json", "JSON (*.json)")
        if not path:
            return

        try:
            data = self._store.export_tree(node_id)
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Экспорт", f"Сохранено в файл:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить: {exc}")

    def import_file(self, path: str) -> None:
        """Импортировать команды из файла (в том числе перетаскиванием)."""
        import json
        from pathlib import Path

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Импорт",
                                f"Не удалось прочитать файл: {exc}")
            return

        item = self._tree.currentItem()
        target_id = ""
        if item is not None and item.data(0, ROLE_KIND) != KIND_COMMAND:
            target_id = str(item.data(0, ROLE_ID) or "")

        stats = self._store.import_data(data, target_id or None)
        self._store.save()
        self.refresh_tree()

        QMessageBox.information(
            self, "Импорт завершён",
            f"Добавлено команд: {stats['added']}\n"
            f"Обновлено: {stats['updated']}\n"
            f"Пропущено (уже есть): {stats['skipped']}\n"
            f"Создано папок: {stats['folders']}")

    def save_now(self) -> None:
        """Сохранить дерево немедленно."""
        self._store.save(force=True)