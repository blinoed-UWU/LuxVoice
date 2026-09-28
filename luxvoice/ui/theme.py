"""Оформление интерфейса: темы, цвета и стили.

Тема собирается из базового набора правил и переменных цвета, поэтому
смена акцента или тёмного режима не требует отдельных файлов стилей.
Все размеры заданы в единицах шрифта, чтобы масштабирование из настроек
работало целиком, а не только для текста.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Theme:
    """Набор цветов текущей темы."""

    name: str = "dark"
    background: str = "#1b1d21"
    surface: str = "#232629"
    surface_alt: str = "#2b2f33"
    border: str = "#3a3f45"
    text: str = "#f2f4f7"
    text_dim: str = "#a6adb8"
    text_faint: str = "#767e8a"
    accent: str = "#4f8cff"
    accent_text: str = "#ffffff"
    accent_dim: str = "#3a6cd0"
    success: str = "#3ddc84"
    warning: str = "#ffb020"
    error: str = "#ff5c5c"
    info: str = "#4fc3f7"
    selection: str = "#2f4a7a"

    @property
    def is_dark(self) -> bool:
        return self.name in ("dark", "system_dark")


def build_theme(name: str = "dark", accent: str = "#4f8cff",
                background: str = "") -> Theme:
    """Собрать тему по её имени и цвету акцента."""
    theme = Theme()

    if name == "light":
        theme.name = "light"
        theme.background = "#f5f6f8"
        theme.surface = "#ffffff"
        theme.surface_alt = "#eceef2"
        theme.border = "#d4d8de"
        theme.text = "#1b1d21"
        theme.text_dim = "#5b6270"
        theme.text_faint = "#8c94a1"
        theme.selection = "#cdddfb"
    elif name == "system":
        theme.name = "system_dark"
        # Значение по умолчанию — тёмная: на Linux большинство тем тёмные.
    else:
        theme.name = "dark"

    if accent and accent.startswith("#"):
        theme.accent = accent
        # Затемнённый вариант акцента для нажатых состояний.
        theme.accent_dim = _darken(accent, 0.22)
        # Подбираем цвет текста на акценте по его яркости.
        theme.accent_text = "#10141a" if _is_light(accent) else "#ffffff"

    if background and background.startswith("#"):
        theme.background = background
        # Поверхности слегка светлее фона — так сохраняется глубина.
        theme.surface = _mix(background, theme.text if theme.is_dark else "#ffffff", 0.06)
        theme.surface_alt = _mix(background, theme.text if theme.is_dark else "#ffffff", 0.11)
        theme.border = _mix(background, theme.text if theme.is_dark else "#000000", 0.18)

    return theme


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return 79, 140, 255


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(rgb[0]))),
        max(0, min(255, int(rgb[1]))),
        max(0, min(255, int(rgb[2]))),
    )


def _darken(value: str, amount: float) -> str:
    red, green, blue = _hex_to_rgb(value)
    factor = 1.0 - max(0.0, min(1.0, amount))
    return _rgb_to_hex((red * factor, green * factor, blue * factor))


def _lighten(value: str, amount: float) -> str:
    red, green, blue = _hex_to_rgb(value)
    return _rgb_to_hex((
        red + (255 - red) * amount,
        green + (255 - green) * amount,
        blue + (255 - blue) * amount,
    ))


def _mix(first: str, second: str, ratio: float) -> str:
    a = _hex_to_rgb(first)
    b = _hex_to_rgb(second)
    ratio = max(0.0, min(1.0, ratio))
    return _rgb_to_hex((
        a[0] + (b[0] - a[0]) * ratio,
        a[1] + (b[1] - a[1]) * ratio,
        a[2] + (b[2] - a[2]) * ratio,
    ))


def _is_light(value: str) -> bool:
    red, green, blue = _hex_to_rgb(value)
    # Воспринимаемая яркость по стандартной формуле.
    luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0
    return luminance > 0.6


def stylesheet(theme: Theme, font_scale: int = 100, compact: bool = False,
               animations: bool = True) -> str:
    """Собрать таблицу стилей для всего приложения.

    font_scale задаёт общий масштаб: размеры в pt и px увеличиваются
    вместе с текстом, иначе интерфейс «расплывается».
    """
    scale = max(0.75, min(1.75, font_scale / 100.0))
    base = 10.0 * scale
    radius = 8 if not compact else 6
    pad = 10 if not compact else 6

    return f"""
/* ================= Основы ================= */
* {{
    font-size: {base:.1f}pt;
    outline: none;
}}

QWidget {{
    background-color: {theme.background};
    color: {theme.text};
}}

QMainWindow, QDialog {{
    background-color: {theme.background};
}}

QToolTip {{
    background-color: {theme.surface_alt};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {radius - 2}px;
    padding: 5px 8px;
}}

/* ================= Панели и рамки ================= */
QFrame#Card, QGroupBox {{
    background-color: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    padding: {pad}px;
    margin-top: {base * 1.1:.1f}pt;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: {pad}px;
    padding: 0 5px;
    color: {theme.text_dim};
    font-weight: 600;
}}

QFrame#Sidebar {{
    background-color: {theme.surface};
    border: none;
    border-right: 1px solid {theme.border};
}}

QFrame#Header {{
    background-color: {theme.surface};
    border: none;
    border-bottom: 1px solid {theme.border};
}}

QFrame#StatusBar {{
    background-color: {theme.surface};
    border: none;
    border-top: 1px solid {theme.border};
}}

QFrame#Separator {{
    background-color: {theme.border};
    max-height: 1px;
    border: none;
}}

/* ================= Кнопки ================= */
QPushButton {{
    background-color: {theme.surface_alt};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    padding: {pad * 0.7:.1f}px {pad * 1.4:.1f}px;
    min-height: {base * 1.6:.1f}pt;
}}

QPushButton:hover {{
    background-color: {_lighten(theme.surface_alt, 0.07)};
    border-color: {theme.accent_dim};
}}

QPushButton:pressed {{
    background-color: {_darken(theme.surface_alt, 0.12)};
}}

QPushButton:disabled {{
    color: {theme.text_faint};
    background-color: {theme.surface};
    border-color: {theme.border};
}}

QPushButton#Primary {{
    background-color: {theme.accent};
    color: {theme.accent_text};
    border: 1px solid {theme.accent};
    font-weight: 600;
}}

QPushButton#Primary:hover {{
    background-color: {_lighten(theme.accent, 0.12)};
}}

QPushButton#Primary:pressed {{
    background-color: {theme.accent_dim};
}}

QPushButton#Danger {{
    background-color: transparent;
    color: {theme.error};
    border: 1px solid {theme.error};
}}

QPushButton#Danger:hover {{
    background-color: {_mix(theme.error, theme.background, 0.82)};
}}

QPushButton#Ghost {{
    background-color: transparent;
    border: none;
    color: {theme.text_dim};
    padding: {pad * 0.5:.1f}px;
}}

QPushButton#Ghost:hover {{
    color: {theme.text};
    background-color: {theme.surface_alt};
    border-radius: {radius}px;
}}

QPushButton#IconButton {{
    background-color: transparent;
    border: none;
    border-radius: {radius}px;
    padding: {pad * 0.6:.1f}px;
    min-width: {base * 2.6:.1f}pt;
}}

QPushButton#IconButton:hover {{
    background-color: {theme.surface_alt};
}}

QPushButton#IconButton:checked {{
    background-color: {theme.accent};
    color: {theme.accent_text};
}}

QPushButton#NavItem {{
    background-color: transparent;
    border: none;
    border-radius: {radius}px;
    padding: {pad * 0.9:.1f}px {pad * 1.2:.1f}px;
    text-align: left;
    color: {theme.text_dim};
}}

QPushButton#NavItem:hover {{
    background-color: {theme.surface_alt};
    color: {theme.text};
}}

QPushButton#NavItem:checked {{
    background-color: {theme.accent};
    color: {theme.accent_text};
    font-weight: 600;
}}

/* ================= Поля ввода ================= */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {theme.surface_alt};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {radius - 2}px;
    padding: {pad * 0.6:.1f}px {pad * 0.8:.1f}px;
    selection-background-color: {theme.selection};
    selection-color: {theme.text};
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {theme.accent};
}}

QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled {{
    color: {theme.text_faint};
    background-color: {theme.surface};
}}

QLineEdit[echoMode="2"] {{
    lineedit-password-character: 9679;
}}

/* ================= Списки и таблицы ================= */
QListWidget, QTreeWidget, QTableWidget, QListView, QTreeView, QTableView {{
    background-color: {theme.surface};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    alternate-background-color: {theme.surface_alt};
    selection-background-color: {theme.selection};
    selection-color: {theme.text};
    outline: none;
}}

QListWidget::item, QTreeWidget::item, QTableWidget::item {{
    padding: {pad * 0.5:.1f}px;
    border: none;
}}

QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {{
    background-color: {theme.surface_alt};
}}

QListWidget::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected {{
    background-color: {theme.selection};
    color: {theme.text};
}}

QHeaderView::section {{
    background-color: {theme.surface_alt};
    color: {theme.text_dim};
    border: none;
    border-right: 1px solid {theme.border};
    border-bottom: 1px solid {theme.border};
    padding: {pad * 0.5:.1f}px;
    font-weight: 600;
}}

QTreeWidget::branch {{
    background-color: transparent;
}}

QTreeWidget::branch:has-children:closed {{
    image: none;
    border-image: none;
}}

/* ================= Выпадающие списки ================= */
QComboBox {{
    background-color: {theme.surface_alt};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {radius - 2}px;
    padding: {pad * 0.6:.1f}px {pad * 1.2:.1f}px {pad * 0.6:.1f}px {pad * 0.8:.1f}px;
    min-height: {base * 1.4:.1f}pt;
}}

QComboBox:hover {{
    border-color: {theme.accent_dim};
}}

QComboBox:focus {{
    border-color: {theme.accent};
}}

QComboBox::drop-down {{
    border: none;
    width: {base * 1.6:.1f}pt;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {theme.text_dim};
    margin-right: {pad * 0.6:.1f}px;
}}

QComboBox QAbstractItemView {{
    background-color: {theme.surface_alt};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {radius - 2}px;
    selection-background-color: {theme.selection};
    padding: 3px;
    outline: none;
}}

/* ================= Флажки и переключатели ================= */
QCheckBox, QRadioButton {{
    spacing: {pad * 0.7:.1f}px;
    color: {theme.text};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: {base * 1.4:.1f}pt;
    height: {base * 1.4:.1f}pt;
    border: 1px solid {theme.border};
    background-color: {theme.surface_alt};
}}

QCheckBox::indicator {{
    border-radius: 4px;
}}

QRadioButton::indicator {{
    border-radius: {base * 0.7:.1f}pt;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {theme.accent};
}}

QCheckBox::indicator:checked {{
    background-color: {theme.accent};
    border-color: {theme.accent};
    image: none;
}}

QRadioButton::indicator:checked {{
    background-color: {theme.accent};
    border-color: {theme.accent};
}}

/* ================= Ползунки ================= */
QSlider::groove:horizontal {{
    height: 4px;
    background-color: {theme.border};
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background-color: {theme.accent};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background-color: {theme.text};
    border: 2px solid {theme.accent};
    width: {base * 1.0:.1f}pt;
    height: {base * 1.0:.1f}pt;
    margin: -{base * 0.45:.1f}pt 0;
    border-radius: {base * 0.7:.1f}pt;
}}

QSlider::handle:horizontal:hover {{
    background-color: {theme.accent};
}}

/* ================= Вкладки ================= */
QTabWidget::pane {{
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    background-color: {theme.surface};
    top: -1px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {theme.text_dim};
    border: none;
    padding: {pad * 0.8:.1f}px {pad * 1.4:.1f}px;
    margin-right: 2px;
    border-top-left-radius: {radius - 2}px;
    border-top-right-radius: {radius - 2}px;
}}

QTabBar::tab:hover {{
    background-color: {theme.surface_alt};
    color: {theme.text};
}}

QTabBar::tab:selected {{
    background-color: {theme.surface};
    color: {theme.text};
    border-bottom: 2px solid {theme.accent};
    font-weight: 600;
}}

/* ================= Полосы прокрутки ================= */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: {theme.border};
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {theme.text_faint};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background-color: {theme.border};
    border-radius: 5px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {theme.text_faint};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
    border: none;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ================= Меню ================= */
QMenuBar {{
    background-color: {theme.surface};
    color: {theme.text};
    border-bottom: 1px solid {theme.border};
}}

QMenuBar::item {{
    padding: {pad * 0.6:.1f}px {pad * 1.0:.1f}px;
    background: transparent;
    border-radius: {radius - 3}px;
}}

QMenuBar::item:selected {{
    background-color: {theme.surface_alt};
}}

QMenu {{
    background-color: {theme.surface_alt};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    padding: 5px;
}}

QMenu::item {{
    padding: {pad * 0.6:.1f}px {pad * 1.6:.1f}px {pad * 0.6:.1f}px {pad * 1.2:.1f}px;
    border-radius: {radius - 3}px;
}}

QMenu::item:selected {{
    background-color: {theme.selection};
}}

QMenu::item:disabled {{
    color: {theme.text_faint};
}}

QMenu::separator {{
    height: 1px;
    background-color: {theme.border};
    margin: 5px {pad * 0.6:.1f}px;
}}

/* ================= Прогресс ================= */
QProgressBar {{
    background-color: {theme.surface_alt};
    border: 1px solid {theme.border};
    border-radius: {radius - 3}px;
    text-align: center;
    color: {theme.text};
    height: {base * 1.2:.1f}pt;
}}

QProgressBar::chunk {{
    background-color: {theme.accent};
    border-radius: {radius - 4}px;
}}

/* ================= Прочее ================= */
QLabel#Title {{
    font-size: {base * 1.6:.1f}pt;
    font-weight: 700;
    color: {theme.text};
}}

QLabel#Subtitle {{
    font-size: {base * 1.15:.1f}pt;
    color: {theme.text_dim};
}}

QLabel#Hint {{
    color: {theme.text_faint};
    font-size: {base * 0.9:.1f}pt;
}}

QLabel#SectionTitle {{
    font-size: {base * 1.2:.1f}pt;
    font-weight: 600;
    color: {theme.text};
}}

QLabel#Success {{ color: {theme.success}; }}
QLabel#Warning {{ color: {theme.warning}; }}
QLabel#Error {{ color: {theme.error}; }}
QLabel#Accent {{ color: {theme.accent}; }}

QStatusBar {{
    background-color: {theme.surface};
    color: {theme.text_dim};
    border-top: 1px solid {theme.border};
}}

QStatusBar::item {{
    border: none;
}}

QSplitter::handle {{
    background-color: {theme.border};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

QToolBar {{
    background-color: {theme.surface};
    border: none;
    border-bottom: 1px solid {theme.border};
    spacing: 4px;
    padding: 4px;
}}

QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: {radius - 2}px;
    padding: {pad * 0.5:.1f}px;
    color: {theme.text};
}}

QToolButton:hover {{
    background-color: {theme.surface_alt};
}}

QToolButton:checked {{
    background-color: {theme.accent};
    color: {theme.accent_text};
}}

QDialogButtonBox QPushButton {{
    min-width: {base * 6:.1f}pt;
}}

QCalendarWidget QWidget {{
    alternate-background-color: {theme.surface_alt};
}}

QCalendarWidget QAbstractItemView:enabled {{
    background-color: {theme.surface};
    color: {theme.text};
    selection-background-color: {theme.accent};
    selection-color: {theme.accent_text};
}}

/* Индикатор «слушаю»: пульсация через изменение прозрачности. */
QWidget#ListenIndicator {{
    background-color: {theme.accent};
    border-radius: {base * 3:.1f}pt;
    min-width: {base * 6:.1f}pt;
    min-height: {base * 6:.1f}pt;
}}

QWidget#ListenIndicator[state="idle"] {{
    background-color: {theme.surface_alt};
    border: 2px solid {theme.border};
}}

QWidget#ListenIndicator[state="listening"] {{
    background-color: {theme.accent};
}}

QWidget#ListenIndicator[state="busy"] {{
    background-color: {theme.warning};
}}

QWidget#ListenIndicator[state="error"] {{
    background-color: {theme.error};
}}

QWidget#LevelBar {{
    background-color: {theme.surface_alt};
    border-radius: 3px;
}}

QWidget#LevelBarChunk {{
    background-color: {theme.accent};
    border-radius: 3px;
}}

QWidget#LevelBarChunk[state="loud"] {{
    background-color: {theme.warning};
}}

QTextBrowser {{
    background-color: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: {radius}px;
    padding: {pad * 0.6:.1f}px;
}}

QTextBrowser#Chat {{
    background-color: {theme.surface};
}}

QTextEdit#ChatInput {{
    background-color: {theme.surface_alt};
    border-radius: {radius}px;
    padding: {pad * 0.8:.1f}px;
}}

QLabel#Bubble {{
    background-color: {theme.surface_alt};
    border-radius: {radius}px;
    padding: {pad * 0.8:.1f}px;
}}

QLabel#BubbleUser {{
    background-color: {theme.selection};
    border-radius: {radius}px;
    padding: {pad * 0.8:.1f}px;
}}
"""


# --- Иконки -----------------------------------------------------------------

# Простые векторные значки: не требуют внешних файлов и масштабируются.
ICONS: dict[str, str] = {
    "home": "M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z",
    "mic": "M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3m-7 9a7 7 0 0 0 14 0h2a9 9 0 0 1-8 8.9V23h-2v-2.1A9 9 0 0 1 3 12z",
    "mic_off": "M3 3l18 18-1.4 1.4L17 18.9A9 9 0 0 1 13 20.9V23h-2v-2.1A9 9 0 0 1 3 12h2a7 7 0 0 0 10.6 6L12 14.4A3 3 0 0 1 9 12V11L3 5z",
    "play": "M8 5v14l11-7z",
    "edit": "M3 17.25V21h3.75L17.8 9.94l-3.75-3.75zM20.7 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75z",
    "settings": "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8m9.4 4a7.6 7.6 0 0 1-.1 1.2l2 1.6-2 3.4-2.4-1a7.5 7.5 0 0 1-2 1.2l-.4 2.6h-4l-.4-2.6a7.5 7.5 0 0 1-2-1.2l-2.4 1-2-3.4 2-1.6a7.6 7.6 0 0 1 0-2.4l-2-1.6 2-3.4 2.4 1a7.5 7.5 0 0 1 2-1.2L10.9 2h4l.4 2.6a7.5 7.5 0 0 1 2 1.2l2.4-1 2 3.4-2 1.6c.1.4.1.8.1 1.2z",
    "plug": "M7 2v6H5v2h4V2h2v6h1v2h-3v4a3 3 0 0 0 3 3h4v2h-4a5 5 0 0 1-5-5v-4H4V8h2V2z",
    "history": "M13 3a9 9 0 0 1 0 18 9 9 0 0 1-8.5-6h2.2A7 7 0 1 0 13 5a7 7 0 0 0-4.5 1.6L11 9H4V2l2.4 2.4A9 9 0 0 1 13 3m-1 5v5l4 2-1 1.7-5-2.5V8z",
    "chat": "M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2z",
    "folder": "M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8z",
    "speaker": "M3 9v6h4l5 5V4L7 9zm13.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4m-2.5 8.8a9 9 0 0 0 0-17.6v2.1a7 7 0 0 1 0 13.4z",
    "cpu": "M9 2v2H7a3 3 0 0 0-3 3v2H2v2h2v2H2v2h2v2a3 3 0 0 0 3 3h2v2h2v-2h2v2h2v-2h2a3 3 0 0 0 3-3v-2h2v-2h-2v-2h2V9h-2V7a3 3 0 0 0-3-3h-2V2h-2v2h-2V2zm0 5h6a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z",
    "add": "M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z",
    "remove": "M5 11h14v2H5z",
    "close": "M18.3 5.7 12 12l6.3 6.3-1.4 1.4L10.6 13.4 4.3 19.7 2.9 18.3 9.2 12 2.9 5.7l1.4-1.4 6.3 6.3 6.3-6.3z",
    "check": "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
    "warning": "M12 2 1 21h22zm0 6 6.5 11h-13zM11 10v4h2v-4zm0 6v2h2v-2z",
    "refresh": "M17.6 6.4A8 8 0 1 0 20 12h-2a6 6 0 1 1-1.8-4.3L13 11h7V4z",
    "save": "M4 4h12l4 4v12H4zm4 2v4h8V6zm-1 8h10v4H7z",
    "search": "M15.5 14h-.8l-.3-.3a6.5 6.5 0 1 0-.7.7l.3.3v.8l5 5 1.5-1.5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z",
    "export": "M12 3v10l3.5-3.5 1.4 1.4L12 16.8 7.1 10.9l1.4-1.4L12 13V3zM5 18h14v2H5z",
    "import": "M12 16V6l-3.5 3.5-1.4-1.4L12 3.2l4.9 4.9-1.4 1.4L12 6v10zM5 18h14v2H5z",
    "arrow_up": "M12 4l7 7-1.4 1.4L13 7.8V20h-2V7.8L6.4 12.4 5 11z",
    "arrow_down": "M12 20l-7-7 1.4-1.4L11 16.2V4h2v12.2l4.6-4.6L19 13z",
    "copy": "M16 1H4a2 2 0 0 0-2 2v14h2V3h12zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2z",
    "star": "M12 17.3 5.8 21l1.6-7.1L2 9.2l7.2-.6L12 2l2.8 6.6 7.2.6-5.4 4.7L18.2 21z",
    "info": "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20m1 15h-2v-6h2zm0-8h-2V7h2z",
    "send": "M2 21 23 12 2 3v7l15 2-15 2z",
    "stop": "M6 6h12v12H6z",
    "bell": "M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2m6-6V11a6 6 0 0 0-5-5.9V4a1 1 0 0 0-2 0v1.1A6 6 0 0 0 6 11v5l-2 2v1h16v-1z",
}


def icon_svg(name: str, color: str = "#ffffff", size: int = 24) -> str:
    """Собрать SVG-иконку по имени."""
    path = ICONS.get(name)
    if not path:
        path = ICONS["info"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}">'
        f'<path fill="{color}" d="{path}"/></svg>'
    )