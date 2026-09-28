"""Аватар поверх окон: живой индикатор состояния ассистента.

Аватар — не картинка, а рисунок: концентрические круги, которые
пульсируют в такт голосу. Это значит, что он не требует файлов,
масштабируется без потери качества и подстраивается под цвет темы.

Особенности для Wayland и KDE:
  * окно без рамки и без фокуса, чтобы не мешать работе;
  * «сквозные» клики — мышь проходит насквозь, если так настроено;
  * закрепление в углу экрана с учётом панели задач;
  * анимация останавливается, когда окно скрыто — не тратит ресурсы.
"""

from __future__ import annotations

import logging
import math

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QRadialGradient
from PyQt6.QtWidgets import QWidget

log = logging.getLogger(__name__)


class AvatarWidget(QWidget):
    """Анимированный аватар поверх других окон."""

    clicked = pyqtSignal()
    double_clicked = pyqtSignal()

    def __init__(self, settings=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._level = 0.0          # текущий уровень сигнала
        self._target = 0.0         # цель для плавности
        self._phase = 0.0
        self._state = "idle"       # idle, listening, thinking, speaking, error
        self._accent = QColor("#4f8cff")
        self._text_color = QColor("#f2f4f7")
        self._dismissed = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Аватар не должен получать фокус — иначе он будет забирать
        # нажатия клавиш у активного окна.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._size = 220
        self.resize(self._size, self._size)

        # Анимация: 25 кадров в секунду достаточно для плавной пульсации
        # и почти не заметна по расходу процессора.
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(250)
        self._click_timer.timeout.connect(self._emit_click)

    # --- Настройка --------------------------------------------------------

    def configure(self, size: int = 220, opacity: int = 85,
                  position: str = "bottom-right", click_through: bool = True,
                  accent: str = "#4f8cff") -> None:
        """Применить настройки внешнего вида и поведения."""
        self._size = max(80, min(800, int(size)))
        self.resize(self._size, self._size)
        self.setWindowOpacity(max(0.2, min(1.0, opacity / 100.0)))
        self._accent = QColor(accent if accent.startswith("#") else "#4f8cff")
        self._position = position

        # Сквозные клики: окно не перехватывает мышь.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                           bool(click_through))

        if self.isVisible():
            self._place(position)
        self.update()

    def set_state(self, state: str) -> None:
        """Состояние ассистента: idle, listening, thinking, speaking, error."""
        if state == self._state:
            return
        self._state = state

        colors = {
            "idle": "#4f8cff",
            "listening": "#4f8cff",
            "thinking": "#ffb020",
            "speaking": "#3ddc84",
            "error": "#ff5c5c",
        }
        from luxvoice.ui.theme import build_theme
        theme = build_theme(
            self._settings.text("ui.theme", "dark") if self._settings else "dark",
            self._settings.text("ui.accent", "#4f8cff") if self._settings else "")
        base = colors.get(state, "#4f8cff")
        if state in ("idle", "listening") and self._settings is not None:
            base = self._settings.text("ui.accent", "#4f8cff")
        self._accent = QColor(base)
        self._text_color = QColor(theme.text)

        # Анимация нужна только в активных состояниях.
        if state == "idle":
            self._timer.stop()
            self._level = 0.0
        elif not self._timer.isActive():
            self._timer.start()
        self.update()

    def set_level(self, level: float) -> None:
        """Уровень сигнала: 0 — тишина, 1 — максимум."""
        self._target = max(0.0, min(1.0, level))

    @property
    def level(self) -> float:
        return self._level

    # --- Размещение -------------------------------------------------------

    def _place(self, position: str = "") -> None:
        """Поставить аватар в нужный угол экрана."""
        position = position or getattr(self, "_position", "bottom-right")

        screen = self.screen()
        if screen is None:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
        if screen is None:
            return

        available: QRect = screen.availableGeometry()
        margin = 24
        width, height = self.width(), self.height()

        positions = {
            "top-left": (available.left() + margin, available.top() + margin),
            "top-right": (available.right() - width - margin,
                          available.top() + margin),
            "bottom-left": (available.left() + margin,
                            available.bottom() - height - margin),
            "bottom-right": (available.right() - width - margin,
                             available.bottom() - height - margin),
            "center": (available.center().x() - width // 2,
                       available.center().y() - height // 2),
        }
        x, y = positions.get(position, positions["bottom-right"])
        self.move(int(x), int(y))

    # --- Показ ------------------------------------------------------------

    def show_avatar(self, position: str = "") -> None:
        """Показать аватар на экране."""
        self._dismissed = False
        self._place(position)
        self.show()
        self.raise_()
        self.set_state("idle")
        log.info("Аватар показан")

    def hide_avatar(self) -> None:
        """Скрыть аватар."""
        self._timer.stop()
        self.hide()
        log.info("Аватар скрыт")

    def toggle(self) -> bool:
        """Переключить видимость. Возвращает новое состояние."""
        if self.isVisible():
            self.hide_avatar()
            return False
        self.show_avatar()
        return True

    # --- Анимация ---------------------------------------------------------

    def _tick(self) -> None:
        """Один кадр анимации."""
        # Плавное приближение к целевому уровню — без рывков.
        self._level += (self._target - self._level) * 0.25
        self._phase = (self._phase + 0.06) % (2 * math.pi)

        # В режиме размышления уровень не приходит от микрофона —
        # задаём его сами, чтобы аватар «дышал».
        if self._state == "thinking":
            self._target = 0.35 + 0.25 * (0.5 + 0.5 * math.sin(self._phase))
        elif self._state == "idle":
            self._target = 0.0
        elif self._state == "speaking" and self._target < 0.05:
            # Если уровень не приходит, имитируем ритм речи.
            self._target = 0.3 + 0.3 * abs(math.sin(self._phase * 2))

        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        """Нарисовать аватар: несколько концентрических кругов."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center = self.rect().center()
        base_radius = min(self.width(), self.height()) / 2.0 - 4

        # Уровень влияет на радиус и яркость — получается пульсация.
        level = self._level
        pulse = 1.0 + 0.10 * level

        # --- Внешнее свечение ---
        glow = QRadialGradient(
            float(center.x()), float(center.y()),
            base_radius * pulse * 1.25)
        color = QColor(self._accent)
        color.setAlpha(70 + int(90 * level))
        glow.setColorAt(0.0, color)
        color_transparent = QColor(self._accent)
        color_transparent.setAlpha(0)
        glow.setColorAt(0.7, QColor(color.red(), color.green(), color.blue(), 40))
        glow.setColorAt(1.0, color_transparent)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            QPoint(int(center.x()), int(center.y())),
            int(base_radius * pulse * 1.25), int(base_radius * pulse * 1.25))

        # --- Три кольца ---
        alpha_base = 200 if self._state != "idle" else 130
        for index, factor in enumerate((1.0, 0.82, 0.64)):
            radius = base_radius * pulse * factor
            wave = 0.5 + 0.5 * math.sin(self._phase + index * 1.1)
            ring_color = QColor(self._accent)
            ring_color.setAlpha(int(min(255, alpha_base * (0.55 + 0.45 * wave)
                                        * (0.7 + 0.3 * level))))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen_width = max(1.5, 3.5 * (1.0 - index * 0.2) * (1.0 + level * 0.6))
            from PyQt6.QtGui import QPen
            painter.setPen(QPen(ring_color, pen_width))
            painter.drawEllipse(QPoint(int(center.x()), int(center.y())),
                                int(radius), int(radius))

        # --- Ядро ---
        core_radius = base_radius * (0.42 + 0.10 * level)
        core = QColor(self._accent)
        core.setAlpha(255 if self._state != "idle" else 200)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(QPoint(int(center.x()), int(center.y())),
                            int(core_radius), int(core_radius))

        # --- Внутренний блик ---
        highlight = QColor(255, 255, 255)
        highlight.setAlpha(int(70 + 60 * level))
        painter.setBrush(highlight)
        painter.drawEllipse(
            QPoint(int(center.x() - core_radius * 0.25),
                   int(center.y() - core_radius * 0.3)),
            int(core_radius * 0.32), int(core_radius * 0.32))

        painter.end()

    # --- Мышь -------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            # Различаем одинарный и двойной клик по таймеру.
            if self._click_timer.isActive():
                self._click_timer.stop()
                self.double_clicked.emit()
            else:
                self._click_timer.start()
            self._drag_start = event.globalPosition().toPoint()

    def _emit_click(self) -> None:
        self.clicked.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        """Перетаскивание аватара по экрану."""
        start = getattr(self, "_drag_start", None)
        if start is None:
            return
        current = event.globalPosition().toPoint()
        delta = current - start
        if delta.manhattanLength() > 6:
            self.move(self.pos() + delta)
            self._drag_start = current

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        self._drag_start = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001, N802
        self.double_clicked.emit()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._size = self.width()


class AvatarController:
    """Связывает аватар с событиями ассистента."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._widget: AvatarWidget | None = None
        self._unsubscribers: list = []
        self._enabled = bool(
            settings.flag("ui.show_avatar", False)) if settings else False

    @property
    def visible(self) -> bool:
        return bool(self._widget and self._widget.isVisible())

    def start(self) -> None:
        """Включить аватар и подписаться на события."""
        if not self._enabled:
            return
        if self._settings is not None and self._settings.flag(
                "privacy.offline_only", False):
            pass

        if self._widget is None:
            self._widget = AvatarWidget(self._settings)
            self._widget.configure(
                size=int(self._settings.number("ui.avatar_size", 220)),
                opacity=int(self._settings.number("ui.avatar_opacity", 85)),
                position=self._settings.text("ui.avatar_position", "bottom-right"),
                click_through=self._settings.flag("ui.avatar_click_through", True),
                accent=self._settings.text("ui.accent", "#4f8cff"),
            )
            self._subscribe()

        self._widget.show_avatar(
            self._settings.text("ui.avatar_position", "bottom-right"))

    def stop(self) -> None:
        """Выключить аватар."""
        if self._widget is not None:
            self._widget.hide_avatar()

    def toggle(self) -> bool:
        """Переключить видимость аватара."""
        if self._widget is None:
            self._enabled = True
            self.start()
            return self.visible

        if self._widget.isVisible():
            self._widget.hide_avatar()
            return False
        self._widget.show_avatar(
            self._settings.text("ui.avatar_position", "bottom-right")
            if self._settings else "bottom-right")
        return True

    def _subscribe(self) -> None:
        """Подписка на события, влияющие на вид аватара."""
        from luxvoice.core.events import (
            AI_THINKING,
            COMMAND_FINISHED,
            COMMAND_STARTED,
            STT_ERROR,
            STT_FINAL,
            STT_LEVEL,
            STT_STARTED,
            STT_STOPPED,
            TTS_FINISHED,
            TTS_STARTED,
            bus,
        )

        widget = self._widget
        if widget is None:
            return

        def on_started(**_):
            widget.set_state("listening")

        def on_stopped(**_):
            widget.set_state("idle")

        def on_level(level: float = 0.0, **_):
            # Реагировать на микрофон можно отключить.
            if self._settings is not None and not self._settings.flag(
                    "ui.avatar_react_mic", True):
                return
            widget.set_level(level)

        def on_error(**_):
            widget.set_state("error")

        def on_tts_started(**_):
            if self._settings is not None and not self._settings.flag(
                    "ui.avatar_react_voice", True):
                return
            widget.set_state("speaking")

        def on_tts_finished(**_):
            widget.set_state("listening")

        def on_command_started(**_):
            widget.set_state("thinking")

        def on_command_finished(**_):
            widget.set_state("listening")

        def on_thinking(**_):
            widget.set_state("thinking")

        self._unsubscribers = [
            bus.subscribe(STT_STARTED, on_started),
            bus.subscribe(STT_STOPPED, on_stopped),
            bus.subscribe(STT_LEVEL, on_level),
            bus.subscribe(STT_ERROR, on_error),
            bus.subscribe(TTS_STARTED, on_tts_started),
            bus.subscribe(TTS_FINISHED, on_tts_finished),
            bus.subscribe(COMMAND_STARTED, on_command_started),
            bus.subscribe(COMMAND_FINISHED, on_command_finished),
            bus.subscribe(AI_THINKING, on_thinking),
        ]

    def apply_settings(self) -> None:
        """Применить изменения настроек внешнего вида."""
        if self._settings is None or self._widget is None:
            return
        self._widget.configure(
            size=int(self._settings.number("ui.avatar_size", 220)),
            opacity=int(self._settings.number("ui.avatar_opacity", 85)),
            position=self._settings.text("ui.avatar_position", "bottom-right"),
            click_through=self._settings.flag("ui.avatar_click_through", True),
            accent=self._settings.text("ui.accent", "#4f8cff"),
        )