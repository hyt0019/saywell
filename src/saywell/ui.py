from __future__ import annotations

import math
import os
import shutil
import threading
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QBrush,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .api_config import chat_completion_url
from .icons import draw_icon, icon_pixmap
from .models import (
    ALL_MODELS,
    CATALOG,
    ENGINE_DIR,
    GPU_ENGINE_DIR,
    PLAZA,
    PLAZA_CATEGORIES,
    SENSE_VOICE,
    AsrModel,
    models_ready,
)
from .settings import Settings, read_key, save_key
from .shortcuts import RECORDING_CHOICES

INK = "#1D1D1F"
MUTED = "#6E6E73"
ACCENT = "#0071E3"
ACCENT_SOFT = "#E8F1FD"
RECORD = "#FF3B30"
DISABLED = "#C7C7CC"
# The one expressive touch in an otherwise quiet interface: an Apple Intelligence
# style blue → violet → pink → amber glow for listening and processing.
AURORA = ("#3B82F6", "#8B5CF6", "#EC4899", "#F59E0B")

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 14px; color: #1D1D1F; }
QMainWindow, QDialog { background: #F5F5F7; }
QLabel { background: transparent; }
QLabel#brand { font-size: 20px; font-weight: 700; }
QLabel#brandLatin { font-size: 15px; font-weight: 600; color: #8E8E93; padding-bottom: 2px; }
QLabel#title { font-size: 24px; font-weight: 700; }
QLabel#muted { color: #6E6E73; font-size: 13px; }
QLabel#caption { color: #8E8E93; font-size: 12px; }
QLabel#section { font-size: 15px; font-weight: 700; }
QLabel#cardTitle { font-size: 15px; font-weight: 700; }
QLabel#fieldLabel { color: #3A3A3C; font-size: 13px; }
QLabel#count { background: #F2F2F7; color: #6E6E73; border-radius: 9px; padding: 2px 9px; font-size: 12px; }
QLabel#tag { color: #6E6E73; border: 1px solid #E0E0E5; border-radius: 9px; padding: 1px 8px; font-size: 12px; }
QLabel#tagActive { background: #0071E3; color: #FFFFFF; border-radius: 9px; padding: 2px 9px; font-size: 12px; }
QLabel#tagGpu { color: #0A5BD3; border: 1px solid #BCD4F5; border-radius: 9px; padding: 1px 8px; font-size: 12px; }
QLabel#strength { color: #3A3A3C; font-size: 13px; }
QLabel#caveat { color: #8E8E93; font-size: 12px; }
QLabel#speed { color: #6E6E73; font-size: 12px; }
QLabel#notice { background: #FFF8E5; color: #7A4F00; border: 1px solid #F9E2A7; border-radius: 12px;
                padding: 10px 14px; font-size: 13px; }

QFrame#hero QLabel { color: #1D1D1F; }
QLabel#status { font-size: 24px; font-weight: 700; color: #1D1D1F; }
QLabel#heroHint { color: #6E6E73; font-size: 14px; }
QLabel#pill { font-size: 13px; color: #6E6E73; }
QLabel#heroMeta { color: #8E8E93; font-size: 13px; }

QFrame#card { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 18px; }
QFrame#card[accent="true"] { border: 1px solid #CFE0F6; }
QFrame#card QPlainTextEdit { background: transparent; border: none; padding: 2px 12px 12px 12px;
                             font-size: 16px; selection-background-color: #CCE0FA; }
QFrame#sectionCard { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 16px; }
QFrame#modelCard { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 16px; }
QFrame#modelCard[active="true"] { border: 2px solid #0071E3; }
QFrame#locationBar { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 14px; }
QFrame#footerBar { background: #FFFFFF; border-top: 1px solid #E5E5EA; }
QFrame#divider { background: #EFEFF4; max-height: 1px; min-height: 1px; border: none; }

QPlainTextEdit { background: #FFFFFF; border: 1px solid #D2D2D7; border-radius: 12px; padding: 10px;
                 selection-background-color: #CCE0FA; }
QPlainTextEdit:focus { border-color: #0071E3; }
QLineEdit, QComboBox, QSpinBox { background: #FFFFFF; border: 1px solid #D2D2D7; border-radius: 10px;
    padding: 7px 11px; min-height: 22px; selection-background-color: #CCE0FA; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #0071E3; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #B8B8BF; }
QComboBox { padding-right: 32px; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView { background: #FFFFFF; border: 1px solid #D2D2D7; border-radius: 10px;
    selection-background-color: #E8F1FD; selection-color: #1D1D1F; outline: none; padding: 4px; }

QPushButton { background: #E9E9EE; border: 1px solid transparent; border-radius: 17px; padding: 8px 18px;
              color: #1D1D1F; }
QPushButton:hover { background: #E1E1E7; }
QPushButton:pressed { background: #D6D6DD; }
QPushButton:focus { border-color: #0071E3; }
QPushButton:disabled { color: #AEAEB2; background: #F2F2F5; }
QLabel#checkText { color: #6E6E73; font-size: 13px; }
QLabel#checkText[tone="ok"] { color: #1F7A35; }
QLabel#checkText[tone="fail"] { color: #D70015; }
QLabel#plazaIntro { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 14px; padding: 12px 16px;
    color: #3A3A3C; font-size: 13px; }

QProgressBar { background: #E5E5EA; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #0071E3; border-radius: 3px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #C7C7CC; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #AEAEB2; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
QListWidget { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 14px; padding: 6px; outline: none; }
QListWidget::item { padding: 10px 12px; border-radius: 9px; color: #1D1D1F; }
QListWidget::item:hover { background: #F5F5F7; }
QListWidget::item:selected { background: #E8F1FD; color: #1D1D1F; }
QMenu { background: #FFFFFF; border: 1px solid #E0E0E5; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: #0071E3; color: #FFFFFF; }
QToolTip { background: #1D1D1F; color: #FFFFFF; border: none; padding: 6px 9px; border-radius: 6px; }
"""


class Glyph:
    """Names of the app's own line icons (see icons.py)."""

    MIC = "mic"
    SETTINGS = "settings"
    HISTORY = "history"
    IMPORT = "import"
    MODEL = "model"
    DOWNLOAD = "download"
    COPY = "copy"
    DELETE = "delete"
    CLEAR = "clear"
    CHECK = "check"
    CHEVRON_DOWN = "chevron_down"
    CHEVRON_RIGHT = "chevron_right"
    INFO = "info"
    FOLDER = "folder"
    LOCK = "lock"
    EDIT = "tidy"
    SEND = "insert"
    KEYBOARD = "keyboard"
    GLOBE = "external"
    CANCEL = "cancel"
    CONNECT = "pulse"
    REPAIR = "refresh"
    REFRESH = "refresh"
    VERIFY = "verify"
    TRANSLATE = "translate"


def glyph_pixmap(glyph: str, color: str, size: int = 16) -> QPixmap:
    return icon_pixmap(glyph, color, size)


def glyph_icon(glyph: str, color: str = INK, size: int = 16, disabled: str = DISABLED) -> QIcon:
    icon = QIcon(glyph_pixmap(glyph, color, size))
    icon.addPixmap(glyph_pixmap(glyph, disabled, size), QIcon.Mode.Disabled)
    return icon


def icon_text(text: str) -> str:
    """Kept for callers that pad labels next to icons; Button lays text out itself."""
    return f" {text}"


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """Blend two colors; a transparent end keeps the other end's hue so fades stay clean."""
    if a.alpha() == 0:
        a = QColor(b.red(), b.green(), b.blue(), 0)
    if b.alpha() == 0:
        b = QColor(a.red(), a.green(), a.blue(), 0)
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


CLEAR_COLOR = "#00000000"


@dataclass(frozen=True)
class ButtonLook:
    background: str
    hover: str
    pressed: str
    text: str
    border: str = CLEAR_COLOR
    height: int = 36
    padding: int = 16
    size: int = 14
    bold: bool = False
    checked_background: str = ""
    checked_text: str = ""
    hover_border: str = ""


BUTTON_LOOKS = {
    "": ButtonLook("#ECECF0", "#E2E2E7", "#D6D6DC", INK),
    "primary": ButtonLook(ACCENT, "#1A80EC", "#0062C4", "#FFFFFF", padding=18, bold=True),
    "toolbar": ButtonLook(CLEAR_COLOR, "#E8E8ED", "#DCDCE2", "#3A3A3C", height=34, padding=12),
    "ghost": ButtonLook(CLEAR_COLOR, "#E8E8ED", "#DCDCE2", "#3A3A3C", height=34, padding=12),
    "danger": ButtonLook(CLEAR_COLOR, "#FFEDEC", "#FFDCD9", "#D70015", height=34, padding=12),
    "link": ButtonLook(CLEAR_COLOR, "#EAF2FD", "#D9E8FB", "#0066CC", height=28, padding=9, size=13),
    "chip": ButtonLook("#FFFFFF", "#F6F6F8", "#EDEDF0", "#3A3A3C", "#E0E0E5", 30, 13, 13,
                       checked_background=INK, checked_text="#FFFFFF"),
    "check": ButtonLook("#FFFFFF", "#FFFFFF", "#F2F6FC", INK, "#D8D8DD", 30, 13, 13, hover_border=ACCENT),
    "segment": ButtonLook(CLEAR_COLOR, CLEAR_COLOR, CLEAR_COLOR, MUTED, height=30, padding=18, size=13,
                          checked_text=INK),
    "heroChip": ButtonLook(CLEAR_COLOR, "#EFEFF3", "#E4E4E9", MUTED, height=28, padding=10, size=13),
    "heroGhost": ButtonLook("#F2F2F5", "#E8E8ED", "#DCDCE2", INK, height=30, padding=16, size=13),
}


class Button(QPushButton):
    """Pill button drawn by the app: soft hover and press transitions, an optional line icon,
    and a short "done" flash (for example 已复制) after an action."""

    def __init__(self, text: str = "", icon: str | None = None, kind: str | None = None, icon_color: str | None = None):
        super().__init__(text)
        self.kind = kind or ""
        self.look = BUTTON_LOOKS.get(self.kind, BUTTON_LOOKS[""])
        if kind:
            self.setObjectName(kind)
        self.icon_name = icon
        self.icon_color = icon_color
        self.hover = 0.0
        self.keyboard_focus = False
        self.restore: tuple[str, str | None] | None = None
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._hovered)
        self.flash_timer = QTimer(self)
        self.flash_timer.setSingleShot(True)
        self.flash_timer.timeout.connect(self._end_flash)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ---------- content ----------
    def set_icon(self, name: str | None):
        self.icon_name = name
        self.updateGeometry()
        self.update()

    def setText(self, text: str):
        super().setText(text)
        self.updateGeometry()

    def flash(self, text: str, icon: str = "check", milliseconds: int = 1400):
        """Show a brief confirmation in place of the label, then restore it."""
        if self.restore is None:
            self.restore = (self.text(), self.icon_name)
        self.setText(text)
        self.set_icon(icon)
        self.flash_timer.start(milliseconds)

    def _end_flash(self):
        if self.restore:
            text, icon = self.restore
            self.restore = None
            self.setText(text)
            self.set_icon(icon)

    def label_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(self.look.size)
        font.setBold(self.look.bold)
        return font

    def sizeHint(self) -> QSize:
        text = self.text().strip()
        width = QFontMetrics(self.label_font()).horizontalAdvance(text) if text else 0
        if self.icon_name:
            width += 16 + (6 if text else 0)
        padding = self.look.padding if text else (self.look.height - 16) // 2
        return QSize(int(width + padding * 2), self.look.height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ---------- interaction ----------
    def _hovered(self, value):
        self.hover = float(value)
        self.update()

    def _animate(self, target: float, duration: int):
        self.animation.stop()
        self.animation.setDuration(duration)
        self.animation.setStartValue(self.hover)
        self.animation.setEndValue(target)
        self.animation.start()

    def enterEvent(self, event):
        self._animate(1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate(0.0, 220)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        look = self.look
        if not self.isEnabled():
            painter.setOpacity(0.4)
        checked = self.isCheckable() and self.isChecked()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        background = QColor(look.checked_background) if checked and look.checked_background else mix(
            QColor(look.background), QColor(look.hover), self.hover if self.isEnabled() else 0
        )
        if self.isDown():
            background = QColor(look.pressed)
        border = QColor(look.border)
        if look.hover_border and self.isEnabled():
            border = mix(border, QColor(look.hover_border), self.hover)
        if checked and look.checked_background:
            border = QColor(look.checked_background)
        painter.setPen(QPen(border, 1) if border.alpha() else Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus() and self.keyboard_focus:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.55)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1.5, radius - 1.5)
        color = QColor(look.checked_text if checked and look.checked_text else look.text)
        text = self.text().strip()
        font = self.label_font()
        width = QFontMetrics(font).horizontalAdvance(text) if text else 0
        content = width + (16 + (6 if text else 0) if self.icon_name else 0)
        x = (self.width() - content) / 2
        if self.icon_name:
            tint = QColor(self.icon_color) if self.icon_color and not checked else color
            draw_icon(painter, self.icon_name, QRectF(x, (self.height() - 16) / 2, 16, 16), tint)
            x += 16 + 6
        if text:
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(QRectF(x, 0, width + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, text)


def make_button(text: str, glyph: str | None = None, kind: str | None = None, icon_color: str | None = None):
    return Button(text, glyph, kind, icon_color)


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


class ElidedLabel(QLabel):
    """Single-line label that shortens long paths in the middle instead of widening the window."""

    def __init__(self, text: str = "", name: str | None = None):
        super().__init__()
        self.full = ""
        if name:
            self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(60)
        self.setText(text)

    def setText(self, text: str):
        self.full = text
        super().setText(text)
        self.setToolTip(text if self.fontMetrics().horizontalAdvance(text) > self.width() else "")

    def text(self) -> str:
        return self.full

    def resizeEvent(self, event):
        self.setToolTip(self.full if self.fontMetrics().horizontalAdvance(self.full) > self.width() else "")
        super().resizeEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        rect = self.contentsRect()
        elided = self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideMiddle, rect.width())
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)


def glyph_label(glyph: str, color: str, size: int = 16) -> QLabel:
    widget = QLabel()
    widget.setPixmap(glyph_pixmap(glyph, color, size))
    widget.setFixedSize(size + 4, size + 4)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return widget


def repolish(widget: QWidget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


OK_GREEN = "#34C759"
FAIL_RED = "#FF3B30"


def fade_in(widget: QWidget, duration: int = 200, start: float = 0.0):
    """Fade a widget in; the effect is removed afterwards so it costs nothing at rest."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class Spinner(QWidget):
    """Thin rotating arc shown while a check runs."""

    def __init__(self, size: int = 16, color: str = ACCENT):
        super().__init__()
        self.setFixedSize(size, size)
        self.color = QColor(color)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def start(self):
        self.timer.start(16)
        self.show()

    def stop(self):
        self.timer.stop()
        self.hide()

    def tick(self):
        self.angle = (self.angle + 8) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor("#E8E8ED"), 1.8))
        painter.drawEllipse(rect)
        pen = QPen(self.color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, -self.angle * 16, 90 * 16)


class ResultMark(QWidget):
    """Green tick or red cross that pops in with a small overshoot."""

    def __init__(self, size: int = 16):
        super().__init__()
        self.setFixedSize(size, size)
        self.ok = True
        self.scale = 1.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(360)
        self.animation.setStartValue(0.4)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutBack)
        self.animation.valueChanged.connect(self._scaled)

    def _scaled(self, value):
        self.scale = float(value)
        self.update()

    def play(self, ok: bool):
        self.ok = ok
        self.show()
        self.animation.start()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = self.width() * self.scale
        rect = QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(OK_GREEN if self.ok else FAIL_RED))
        painter.drawEllipse(rect)
        draw_icon(painter, "check" if self.ok else "cancel", rect.adjusted(3, 3, -3, -3), "#FFFFFF", 3.2)


class CheckStatus(QWidget):
    """Running / passed / failed line next to a check button."""

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.spinner = Spinner(16)
        self.spinner.hide()
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignTop)
        self.icon = ResultMark(16)
        self.icon.hide()
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.text = label("", "checkText", wrap=True)
        layout.addWidget(self.text, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def running(self, text: str):
        self.icon.hide()
        self.spinner.start()
        self._set(text, "")

    def finish(self, ok: bool, text: str):
        self.spinner.stop()
        self.icon.play(ok)
        self._set(text, "ok" if ok else "fail")
        fade_in(self.text, 260)

    def clear(self):
        self.spinner.stop()
        self.icon.hide()
        self._set("", "")

    def _set(self, text: str, tone: str):
        self.text.setText(text)
        self.text.setProperty("tone", tone)
        repolish(self.text)


class Toast(QWidget):
    """A short dark capsule near the bottom of a window: 已复制, 设置已保存 and so on."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text = ""
        self.icon = "check"
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.finished.connect(self._faded)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._fade_to(0.0, 240))
        self.hide()

    def show_text(self, text: str, icon: str = "check", milliseconds: int = 1600):
        self.text, self.icon = text, icon
        font = QFont(self.font())
        font.setPixelSize(13)
        width = QFontMetrics(font).horizontalAdvance(text) + 16 + 8 + 36
        self.resize(width, 38)
        parent = self.parentWidget()
        self.move((parent.width() - width) // 2, parent.height() - 38 - 10)
        self.raise_()
        self.show()
        self._fade_to(1.0, 180)
        self.timer.start(milliseconds)
        self.update()

    def _fade_to(self, value: float, duration: int):
        self.fade.stop()
        self.fade.setDuration(duration)
        self.fade.setStartValue(self.effect.opacity())
        self.fade.setEndValue(value)
        self.fade.start()

    def _faded(self):
        if self.effect.opacity() < 0.01:
            self.hide()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(29, 29, 31, 236))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        draw_icon(painter, self.icon, QRectF(18, (self.height() - 16) / 2, 16, 16), "#FFFFFF")
        font = QFont(self.font())
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRectF(18 + 16 + 8, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)


class Segmented(QFrame):
    """Segmented control whose white thumb slides to the chosen segment."""

    changed = Signal(int)

    def __init__(self, labels: tuple[str, ...] | list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons: list[Button] = []
        for index, text in enumerate(labels):
            button = Button(text, None, "segment")
            button.setCheckable(True)
            button.setMinimumWidth(72)
            self.group.addButton(button, index)
            self.buttons.append(button)
            layout.addWidget(button)
        self.thumb = QRectF()
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(240)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._moved)
        self.group.idClicked.connect(self._clicked)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def current(self) -> int:
        return self.group.checkedId()

    def set_current(self, index: int, animate: bool = True):
        self.buttons[index].setChecked(True)
        target = QRectF(self.buttons[index].geometry())
        if animate and self.isVisible() and not self.thumb.isNull():
            self.animation.stop()
            self.animation.setStartValue(self.thumb)
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.thumb = target
            self.update()

    def _clicked(self, index: int):
        self.set_current(index)
        self.changed.emit(index)

    def _moved(self, value):
        self.thumb = QRectF(value)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current() >= 0 and self.animation.state() != QVariantAnimation.State.Running:
            self.thumb = QRectF(self.buttons[self.current()].geometry())

    def showEvent(self, event):
        super().showEvent(event)
        if self.current() >= 0:
            self.thumb = QRectF(self.buttons[self.current()].geometry())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#E8E8ED"))
        rect = QRectF(self.rect())
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if not self.thumb.isNull():
            thumb = self.thumb.adjusted(0, 0, 0, 0)
            painter.setBrush(QColor(0, 0, 0, 16))
            painter.drawRoundedRect(thumb.adjusted(0, 1, 0, 1.5), thumb.height() / 2, thumb.height() / 2)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(thumb, thumb.height() / 2, thumb.height() / 2)


class Task(QObject):
    progress = Signal(str)
    done = Signal(object)


def run_task(owner: QObject, work, on_done, on_progress=None) -> Task:
    """Run work(report) on a thread; results come back on the GUI thread.

    work raises ModelError (or returns a CheckResult); anything else becomes a generic failure."""
    from .checks import CheckResult
    from .models import ModelError

    task = Task(owner)
    task.done.connect(on_done)
    task.done.connect(task.deleteLater)
    if on_progress:
        task.progress.connect(on_progress)

    def emit(signal, value):
        try:
            signal.emit(value)
        except RuntimeError:  # The window closed while the check was running.
            pass

    def target():
        try:
            result = work(lambda text: emit(task.progress, text))
        except ModelError as exc:
            result = CheckResult(False, str(exc))
        except Exception:  # noqa: BLE001 - shown to the user as a failed check
            result = CheckResult(False, "检查没有完成，请稍后重试")
        emit(task.done, result)

    threading.Thread(target=target, daemon=True).start()
    return task


def check_button(text: str, glyph: str) -> QPushButton:
    button = make_button(text, glyph, "check", ACCENT)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


ASSETS = Path(__file__).resolve().parent / "assets"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def scaled_image(image: QImage, size: int) -> QImage:
    """Halve step by step before the final resize so small icons stay crisp instead of aliased."""
    while image.width() >= size * 4:
        image = image.scaled(
            image.width() // 2, image.height() // 2, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def image_icon(name: str) -> QIcon:
    source = QImage(str(ASSETS / name))
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(scaled_image(source, size)))
    return icon


def app_icon() -> QIcon:
    return image_icon("icon.png")


def taskbar_is_light() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def tray_icon() -> QIcon:
    """White glyph on a dark taskbar like other tray icons; the colored icon stays visible on a light one."""
    return app_icon() if taskbar_is_light() else image_icon("tray-white.png")


def blend(colors: tuple[str, ...], t: float) -> QColor:
    """Color at position t (0..1) along a multi-stop gradient."""
    t = max(0.0, min(1.0, t)) * (len(colors) - 1)
    index = min(int(t), len(colors) - 2)
    a, b, f = QColor(colors[index]), QColor(colors[index + 1]), t - index
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * f,
        a.greenF() + (b.greenF() - a.greenF()) * f,
        a.blueF() + (b.blueF() - a.blueF()) * f,
    )


class LevelBars(QWidget):
    """A scrolling voice-level visualizer with a QProgressBar-like setValue API."""

    def __init__(self, bars: int = 36, colors: tuple[str, ...] = AURORA[:3], rest: str = "#D1D1D6", parent=None):
        super().__init__(parent)
        self.levels = deque([0.0] * bars, maxlen=bars)
        self.colors = colors
        self.rest = QColor(rest)
        self.target = 0.0
        self._value = 0
        self.timer = QTimer(self)
        self.timer.setInterval(45)
        self.timer.timeout.connect(self._advance)
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setValue(self, value: int):
        self._value = max(0, min(100, int(value)))
        if self._value == 0:
            self.target = 0.0
            if not any(self.levels):
                return
        # Speech RMS is compressed; a square root keeps quiet talkers visible.
        self.target = max(self.target, math.sqrt(self._value / 100))
        if not self.timer.isActive():
            self.timer.start()

    def value(self) -> int:
        return self._value

    def _advance(self):
        self.levels.append(self.target)
        self.target *= 0.55
        if self.target < 0.02:
            self.target = 0.0
            if not any(level > 0.01 for level in self.levels):
                self.levels.extend([0.0] * self.levels.maxlen)
                self.timer.stop()
        self.update()

    def hideEvent(self, event):
        self.timer.stop()
        self.target = 0.0
        self.levels.extend([0.0] * self.levels.maxlen)
        super().hideEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        count = self.levels.maxlen
        gap = 4
        width = max(2.0, (self.width() - gap * (count - 1)) / count)
        width = min(width, 5.0)
        x = 0.0
        middle = self.height() / 2
        for index, level in enumerate(self.levels):
            height = max(width, level * (self.height() - 2))
            if level > 0.01:
                color = blend(self.colors, index / max(1, count - 1))
                color.setAlphaF(0.55 + 0.45 * (index + 1) / count)
            else:
                color = QColor(self.rest)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x, middle - height / 2, width, height), width / 2, width / 2)
            x += width + gap


def aurora_gradient(center, angle: float) -> QConicalGradient:
    gradient = QConicalGradient(center, angle)
    for index, color in enumerate((*AURORA, AURORA[0])):
        gradient.setColorAt(index / len(AURORA), QColor(color))
    return gradient


class MicButton(QPushButton):
    """Round record button: a calm solid circle at rest, a breathing red ring while
    recording and a thin rotating arc while processing. objectName "recording"
    switches it to the stop state."""

    DIAMETER = 76.0

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.busy = False
        self.phase = 0.0
        self.hover = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(1400)
        self.animation.setLoopCount(-1)
        self.animation.valueChanged.connect(self._tick)
        self.hover_animation = QVariantAnimation(self)
        self.hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hover_animation.valueChanged.connect(self._hovered)
        self.keyboard_focus = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(132, 126)

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
        )
        super().focusInEvent(event)

    def _tick(self, value):
        self.phase = float(value)
        self.update()

    def _hovered(self, value):
        self.hover = float(value)
        self.update()

    def set_busy(self, busy: bool):
        self.busy = busy
        self._sync_animation()

    def _sync_animation(self):
        animate = self.busy or self.objectName() == "recording"
        if animate and self.animation.state() != QVariantAnimation.State.Running:
            self.animation.start()
        elif not animate and self.animation.state() == QVariantAnimation.State.Running:
            self.animation.stop()
            self.phase = 0.0
        self.update()

    def setObjectName(self, name: str):
        super().setObjectName(name)
        self._sync_animation()

    def _hover_to(self, target: float):
        self.hover_animation.stop()
        self.hover_animation.setDuration(160)
        self.hover_animation.setStartValue(self.hover)
        self.hover_animation.setEndValue(target)
        self.hover_animation.start()

    def enterEvent(self, event):
        self._hover_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        recording = self.objectName() == "recording"
        diameter = self.DIAMETER
        center_x, center_y = self.width() / 2, 12 + diameter / 2
        circle = QRectF(center_x - diameter / 2, center_y - diameter / 2, diameter, diameter)
        if not self.isEnabled() and not self.busy:
            painter.setOpacity(0.4)
        painter.setPen(Qt.PenStyle.NoPen)
        if recording:
            # One soft ring breathing outward.
            radius = diameter / 2 + 2 + self.phase * 11
            ring = QColor(RECORD)
            ring.setAlphaF(0.28 * (1 - self.phase))
            painter.setBrush(ring)
            painter.drawEllipse(QRectF(center_x - radius, center_y - radius, radius * 2, radius * 2))
            fill = mix(QColor(RECORD), QColor("#FF5A50"), self.hover)
        elif self.busy:
            fill = QColor("#F2F2F5")
        else:
            fill = mix(QColor(ACCENT), QColor("#1A80EC"), self.hover)
        if self.isDown() and not self.busy:
            fill = fill.darker(112)
        painter.setBrush(fill)
        painter.drawEllipse(circle)
        if self.hasFocus() and self.keyboard_focus:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.5)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle.adjusted(-5, -5, 5, 5))
        if recording:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(QRectF(center_x - 11, center_y - 11, 22, 22), 5, 5)
        elif self.busy:
            pen = QPen(QColor(ACCENT), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(circle.adjusted(20, 20, -20, -20), int(-self.phase * 360 * 16), 100 * 16)
        else:
            draw_icon(painter, "mic", QRectF(center_x - 15, center_y - 15, 30, 30), "#FFFFFF", 2.0)
        painter.setOpacity(1.0 if self.isEnabled() or self.busy else 0.4)
        font = QFont(self.font())
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor(INK))
        text_rect = QRectF(0, circle.bottom() + 10, self.width(), 22)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())


class ComboBox(QComboBox):
    """QComboBox with a drawn chevron, so no image files are needed for the arrow."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        draw_icon(
            painter, "chevron_down", QRectF(self.width() - 30, (self.height() - 14) / 2, 14, 14),
            MUTED if self.isEnabled() else DISABLED,
        )


class HeroCard(QFrame):
    """The recording card: plain white at rest. While listening a slow aurora light runs
    along its edge; while processing a single blue light does."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hero")
        self.mode = "idle"
        self.phase = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(4200)
        self.animation.setLoopCount(-1)
        self.animation.valueChanged.connect(self._tick)

    def _tick(self, value):
        self.phase = float(value)
        self.update()

    def set_mode(self, mode: str):
        self.mode = mode if mode in ("recording", "busy") else "idle"
        if self.mode == "idle":
            self.animation.stop()
            self.phase = 0.0
        elif self.animation.state() != QVariantAnimation.State.Running:
            self.animation.start()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 20
        painter.setPen(QPen(QColor("#E5E5EA"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, radius, radius)
        if self.mode == "idle":
            return
        if self.mode == "recording":
            gradient = aurora_gradient(rect.center(), -self.phase * 360)
        else:
            gradient = QConicalGradient(rect.center(), -self.phase * 360)
            gradient.setColorAt(0.0, QColor(0, 113, 227, 220))
            gradient.setColorAt(0.25, QColor(0, 113, 227, 0))
            gradient.setColorAt(0.75, QColor(0, 113, 227, 0))
            gradient.setColorAt(1.0, QColor(0, 113, 227, 220))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for inset, width, alpha in ((1.0, 2.0, 1.0), (3.5, 4, 0.16)):
            painter.setOpacity(alpha)
            painter.setPen(QPen(QBrush(gradient), width))
            painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), radius - inset, radius - inset)
        painter.setOpacity(1.0)


class Switch(QCheckBox):
    """A toggle switch that keeps the QCheckBox API."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QSize(46 + 12 + metrics.horizontalAdvance(self.text()), max(26, metrics.height() + 6))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, (self.height() - 24) / 2, 42, 24)
        on = self.isChecked()
        if not self.isEnabled():
            painter.setOpacity(0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT) if on else QColor("#E5E5EA"))
        painter.drawRoundedRect(track, 12, 12)
        knob = QRectF(track.right() - 22 if on else track.left() + 2, track.top() + 2, 20, 20)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawEllipse(knob.adjusted(0, 1, 0, 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(knob)
        if self.hasFocus():
            painter.setPen(QPen(QColor(ACCENT), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-2.5, -2.5, 2.5, 2.5), 14, 14)
        painter.setPen(QColor(INK))
        painter.setFont(self.font())
        painter.drawText(
            QRectF(track.right() + 12, 0, self.width() - track.right() - 12, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


class RecordingOverlay(QWidget):
    TONES = {"rec": "#FF453A", "busy": "#FF9F0A", "done": "#30D158", "warn": "#FF9F0A"}

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tone = "rec"
        self.phase = 0.0
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self.hide)
        self.pulse = QVariantAnimation(self)
        self.pulse.setStartValue(0.0)
        self.pulse.setEndValue(1.0)
        self.pulse.setDuration(1200)
        self.pulse.setLoopCount(-1)
        self.pulse.valueChanged.connect(self._pulse)
        self.setFixedSize(380, 104)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(52, 22, 26, 22)
        layout.setSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.label = QLabel("正在听  00:00")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setStyleSheet("color: #FFFFFF; font-size: 15px; font-weight: 700; background: transparent;")
        self.hint = QLabel("松开快捷键结束，Esc 取消")
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setStyleSheet("color: #98989D; font-size: 12px; background: transparent;")
        text.addWidget(self.label)
        text.addWidget(self.hint)
        layout.addLayout(text, 1)
        self.level = LevelBars(14, AURORA[:3], "#48484A")
        self.level.setFixedWidth(104)
        layout.addWidget(self.level)

    def _pulse(self, value):
        self.phase = float(value)
        self.update()

    def set_tone(self, tone: str):
        self.tone = tone if tone in self.TONES else "rec"
        if self.tone in ("rec", "busy"):
            if self.pulse.state() != QVariantAnimation.State.Running:
                self.pulse.start()
        else:
            self.pulse.stop()
            self.phase = 0.0
        self.update()

    def show_near_bottom(self):
        self.dismiss_timer.stop()
        area = self.screen().availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.bottom() - self.height() - 28)
        self.show()

    def show_message(self, title: str, hint: str, milliseconds: int = 4500, tone: str = "done"):
        self.label.setText(title)
        self.hint.setText(hint)
        self.level.hide()
        self.set_tone(tone)
        self.show_near_bottom()
        self.dismiss_timer.start(milliseconds)

    def hideEvent(self, event):
        self.pulse.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(12, 10, -12, -14)
        for step in range(8):
            shadow = QColor(0, 0, 0, 10 - step)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawRoundedRect(body.adjusted(-step, -step + 3, step, step + 3), 22 + step, 22 + step)
        gradient = QLinearGradient(body.topLeft(), body.bottomLeft())
        gradient.setColorAt(0, QColor(44, 44, 46, 245))
        gradient.setColorAt(1, QColor(28, 28, 30, 245))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1))
        painter.drawRoundedRect(body, 22, 22)
        color = QColor(self.TONES.get(self.tone, RECORD))
        center = body.left() + 22, body.center().y()
        if self.tone in ("rec", "busy"):
            ring = 6 + self.phase * 9
            halo = QColor(color)
            halo.setAlphaF(0.5 * (1 - self.phase))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(center[0] - ring, center[1] - ring, ring * 2, ring * 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(center[0] - 6, center[1] - 6, 12, 12))


def card(accent: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    if accent:
        frame.setProperty("accent", True)
    return frame


def text_column(title: str, hint: str, accent: bool = False) -> tuple[QWidget, QPlainTextEdit, QLabel]:
    widget = card(accent)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(8, 16, 8, 6)
    layout.setSpacing(8)
    row = QHBoxLayout()
    row.setContentsMargins(14, 0, 12, 0)
    row.setSpacing(9)
    dot = QLabel()
    dot.setFixedSize(9, 9)
    dot.setStyleSheet(f"background: {ACCENT if accent else DISABLED}; border-radius: 4px;")
    row.addWidget(dot)
    row.addWidget(label(title, "cardTitle"))
    row.addStretch()
    count = label("0 字", "count")
    row.addWidget(count)
    layout.addLayout(row)
    editor = QPlainTextEdit()
    editor.setPlaceholderText(hint)
    editor.textChanged.connect(lambda: count.setText(f"{len(editor.toPlainText())} 字"))
    layout.addWidget(editor, 1)
    return widget, editor, count


class Section(QFrame):
    def __init__(self, title: str, description: str, glyph: str):
        super().__init__()
        self.setObjectName("sectionCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 22)
        layout.setSpacing(16)
        head = QHBoxLayout()
        head.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "section"))
        text.addWidget(label(description, "muted", wrap=True))
        head.addLayout(text, 1)
        layout.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        layout.addLayout(self.body)

    def form(self) -> QFormLayout:
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.body.addLayout(form)
        return form


def field_label(text: str) -> QLabel:
    widget = label(text, "fieldLabel")
    widget.setMinimumWidth(76)
    return widget


def dialog_header(title: str, subtitle: str) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(30, 26, 30, 10)
    layout.setSpacing(4)
    layout.addWidget(label(title, "title"))
    layout.addWidget(label(subtitle, "muted", wrap=True))
    return widget


def scroll_body(spacing: int = 14) -> tuple[QScrollArea, QVBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(30, 8, 26, 22)
    layout.setSpacing(spacing)
    area.setWidget(content)
    return area, layout


def footer_bar() -> tuple[QFrame, QHBoxLayout]:
    frame = QFrame()
    frame.setObjectName("footerBar")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(30, 14, 30, 14)
    layout.setSpacing(10)
    return frame, layout


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.updated = settings
        self.setWindowTitle("设置 · 好好说")
        self.resize(640, 780)
        self.setMinimumSize(560, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(dialog_header("设置", "调整文字整理服务、录音方式和个人词库。保存后立即生效。"))
        area, layout = scroll_body()
        outer.addWidget(area, 1)

        api = Section("文字整理服务", "录音在本机识别后，只把文字发送到这里填写的 API 进行整理。", Glyph.GLOBE)
        form = api.form()
        self.base_url = QLineEdit(settings.api_base_url)
        self.base_url.setPlaceholderText("例如 https://api.example.com/v1")
        self.base_url.setToolTip("填写兼容 Chat Completions 的 API 地址，支持 Base URL 或完整请求地址。")
        form.addRow(field_label("API 地址"), self.base_url)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.update_key_hint()
        self.base_url.textChanged.connect(self.api_address_changed)
        form.addRow(field_label("API Key"), self.key)
        self.model = QLineEdit(settings.model)
        self.model.setPlaceholderText("填写服务商提供的模型名称")
        form.addRow(field_label("模型名称"), self.model)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(int(settings.api_timeout))
        self.timeout.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.timeout.setMaximumWidth(140)
        form.addRow(field_label("请求超时"), self.timeout)
        test = QHBoxLayout()
        test.setSpacing(12)
        self.test_api = check_button("测试连接", Glyph.CONNECT)
        self.test_api.clicked.connect(self.check_connection)
        test.addWidget(self.test_api, 0, Qt.AlignmentFlag.AlignTop)
        self.api_status = CheckStatus()
        test.addWidget(self.api_status, 1)
        form.addRow(field_label("连接检查"), test)
        layout.addWidget(api)

        recording = Section("录音与输入", "全局快捷键随时可用，结果直接输入到原来的光标处。", Glyph.MIC)
        form = recording.form()
        self.mic = ComboBox()
        self.mic.addItem("系统默认麦克风", None)
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            for index, device in enumerate(devices):
                if device["max_input_channels"] > 0 and (
                    device["hostapi"] == 0 or index == settings.microphone
                ):
                    self.mic.addItem(device["name"], index)
            self.mic.setCurrentIndex(max(0, self.mic.findData(settings.microphone)))
        except Exception:
            self.mic.setToolTip("未能列出麦克风，请检查音频设备。")
        form.addRow(field_label("麦克风"), self.mic)
        self.hotkey = ComboBox()
        self.hotkey.addItems(RECORDING_CHOICES)
        if settings.hotkey not in RECORDING_CHOICES:
            self.hotkey.addItem(settings.hotkey)
        self.hotkey.setCurrentText(settings.hotkey)
        self.hotkey.setMaximumWidth(200)
        form.addRow(field_label("录音快捷键"), self.hotkey)
        self.hold = Switch("按住快捷键说话，松开结束")
        self.hold.setChecked(settings.hold_to_talk)
        form.addRow(field_label("录音方式"), self.hold)
        form.addRow(field_label("输入方式"), label("直接输入单行文字，支持终端，不自动回车", "muted", wrap=True))
        self.voice_translation = Switch("说完加一句“翻译成英文”，直接输入译文")
        self.voice_translation.setChecked(settings.voice_translation)
        form.addRow(field_label("语音翻译"), self.voice_translation)
        gpu = QVBoxLayout()
        gpu.setSpacing(4)
        self.gpu = Switch("用显卡运行 Qwen3-ASR，识别更快")
        self.gpu.setChecked(settings.gpu_acceleration)
        gpu.addWidget(self.gpu)
        gpu.addWidget(label("支持 NVIDIA、AMD、Intel 显卡；显卡不支持时自动改用 CPU。", "muted", wrap=True))
        form.addRow(field_label("GPU 加速"), gpu)
        layout.addWidget(recording)

        privacy = Section("隐私与启动", "录音只在内存中处理，识别后立即释放，不保存录音文件。", Glyph.LOCK)
        self.history = Switch("保留最近 5 条文字（本机加密，最长 7 天）")
        self.history.setChecked(settings.save_history)
        privacy.body.addWidget(self.history)
        stats_row = QHBoxLayout()
        self.collect_stats = Switch("记录使用统计（只记字数、次数和 tokens，不记内容）")
        self.collect_stats.setChecked(settings.collect_stats)
        stats_row.addWidget(self.collect_stats, 1)
        self.clear_stats = make_button("清空统计", None, "link", ACCENT)
        self.clear_stats.clicked.connect(self.confirm_clear_stats)
        stats_row.addWidget(self.clear_stats)
        privacy.body.addLayout(stats_row)
        self.stats_cleared = False
        self.startup = Switch("开机自启动，登录 Windows 后在托盘运行")
        self.startup.setChecked(settings.start_on_login)
        privacy.body.addWidget(self.startup)
        layout.addWidget(privacy)

        words = Section(
            "个人词库",
            "每行一个常用人名、项目名或英文术语，最多 100 个，整理时用于纠正拼写。",
            Glyph.EDIT,
        )
        self.glossary = QPlainTextEdit("\n".join(settings.glossary))
        self.glossary.setMinimumHeight(130)
        self.glossary.setMaximumHeight(180)
        words.body.addWidget(self.glossary)
        layout.addWidget(words)
        layout.addStretch()

        footer, actions = footer_bar()
        actions.addWidget(glyph_label(Glyph.LOCK, MUTED, 14))
        actions.addWidget(label("密钥按 API 地址分别保存在 Windows 凭据管理器", "caption"))
        actions.addStretch()
        cancel = make_button("取消")
        cancel.clicked.connect(self.reject)
        save = make_button("保存设置", Glyph.CHECK, "primary")
        save.setDefault(True)
        save.clicked.connect(self.save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        outer.addWidget(footer)

    def update_key_hint(self):
        found = bool(read_key(api_base_url=self.base_url.text()))
        self.key.setPlaceholderText("该地址已有密钥；留空保留" if found else "填写此 API 地址对应的密钥")

    def api_address_changed(self):
        self.key.clear()
        self.update_key_hint()

    def confirm_clear_stats(self):
        answer = QMessageBox.question(self, "清空统计", "清空所有使用统计？热力图、累计字数和成就会从零开始。")
        if answer == QMessageBox.StandardButton.Yes:
            self.stats_cleared = True
            self.clear_stats.setText("已清空")
            self.clear_stats.setEnabled(False)

    def check_connection(self):
        from .checks import check_api

        base_url = self.base_url.text().strip().rstrip("/")
        try:
            key = self.key.text().strip() or read_key(api_base_url=base_url) or ""
        except Exception:
            key = self.key.text().strip()
        model, timeout = self.model.text(), float(self.timeout.value())
        self.test_api.setEnabled(False)
        self.api_status.running("正在连接…")

        def finished(result):
            self.test_api.setEnabled(True)
            self.api_status.finish(result.ok, result.message)

        run_task(self, lambda _report: check_api(base_url, key, model, timeout), finished)

    def save(self):
        words = list(dict.fromkeys(x.strip() for x in self.glossary.toPlainText().splitlines() if x.strip()))
        if len(words) > 100 or any(len(word) > 80 for word in words):
            QMessageBox.warning(self, "检查词库", "最多添加 100 个词，每个词不超过 80 字。")
            return
        if not self.model.text().strip():
            QMessageBox.warning(self, "检查模型", "请填写 API 服务商提供的模型名称。")
            return
        base_url = self.base_url.text().strip().rstrip("/")
        try:
            endpoint = chat_completion_url(base_url)
        except ValueError as exc:
            QMessageBox.warning(self, "检查 API 地址", str(exc))
            return
        try:
            previous_endpoint = chat_completion_url(self.settings.api_base_url)
        except ValueError:
            previous_endpoint = ""
        if (
            endpoint != previous_endpoint
            and not self.key.text().strip()
            and not read_key(api_base_url=base_url)
        ):
            QMessageBox.warning(self, "检查 API Key", "已更换 API 地址，请填写该服务对应的密钥。")
            return
        try:
            if self.key.text().strip():
                save_key(self.key.text(), api_base_url=base_url)
            self.updated = replace(
                self.settings,
                model=self.model.text().strip(),
                api_base_url=base_url,
                api_timeout=float(self.timeout.value()),
                microphone=self.mic.currentData(),
                hotkey=self.hotkey.currentText(),
                hold_to_talk=self.hold.isChecked(),
                save_history=self.history.isChecked(),
                collect_stats=self.collect_stats.isChecked(),
                start_on_login=self.startup.isChecked(),
                glossary=words,
                voice_translation=self.voice_translation.isChecked(),
                gpu_acceleration=self.gpu.isChecked(),
            )
            self.accept()
        except Exception:
            QMessageBox.warning(self, "保存失败", "无法保存密钥到 Windows 凭据管理器，请检查系统权限。")


def free_space(path: Path) -> int | None:
    for candidate in (path, *path.parents):
        try:
            if candidate.exists():
                return shutil.disk_usage(candidate).free
        except OSError:
            return None
    return None


def format_bytes(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


def delete_model_files(model: AsrModel, directory: Path) -> None:
    for spec in model.files:
        (directory / spec.name).unlink(missing_ok=True)
    if model.kind == "llama_server":
        # Unpacked from the downloaded archives.
        shutil.rmtree(directory / ENGINE_DIR, ignore_errors=True)
        shutil.rmtree(directory / GPU_ENGINE_DIR, ignore_errors=True)
    folders = sorted({(directory / spec.name).parent for spec in model.files}, key=lambda p: len(p.parts))
    for folder in reversed(folders):
        if folder != directory or model.folder:
            try:
                folder.rmdir()  # Only removes folders that are now empty.
            except OSError:
                pass


class ModelCard(QFrame):
    def __init__(self, model: AsrModel):
        super().__init__()
        self.model = model
        self.setObjectName("modelCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 14)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(label(model.name, "cardTitle"))
        self.tag = label(model.tagline, "tag")
        head.addWidget(self.tag)
        if model.gpu:
            gpu = label("支持 GPU 加速", "tagGpu")
            gpu.setToolTip("在“设置 → GPU 加速”中开启后用显卡识别，速度更快")
            head.addWidget(gpu)
        self.active_tag = label("使用中", "tagActive")
        head.addWidget(self.active_tag)
        head.addStretch()
        head.addWidget(label(f"{model.languages}  ·  {model.size_label}", "muted"))
        layout.addLayout(head)
        if model.homepage:
            meta = QHBoxLayout()
            meta.setSpacing(8)
            meta.addWidget(label(model.speed_label, "speed"))
            meta.addStretch()
            self.homepage = make_button("模型主页", Glyph.GLOBE, "link")
            self.homepage.setToolTip(model.homepage)
            self.homepage.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(model.homepage)))
            meta.addWidget(self.homepage)
            layout.addLayout(meta)
        strengths = QVBoxLayout()
        strengths.setSpacing(4)
        for text in model.strengths:
            strengths.addWidget(label(f"·  {text}", "strength"))
        layout.addLayout(strengths)
        caveat = label(model.caveat, "caveat", wrap=True)
        layout.addWidget(caveat)
        self.check_row = QWidget()
        checks = QHBoxLayout(self.check_row)
        checks.setContentsMargins(0, 4, 0, 0)
        checks.setSpacing(8)
        self.verify_button = check_button("检查文件", Glyph.VERIFY)
        self.verify_button.setToolTip("逐个校验模型文件是否完整")
        self.try_button = check_button("试一试", Glyph.MIC)
        self.try_button.setToolTip("最长录 10 秒，用这个模型当场识别")
        self.stop_recording = None  # Set while "试一试" is listening; clicking again finishes early.
        self.repair_button = check_button("修复", Glyph.REPAIR)
        self.repair_button.setToolTip("只重新下载不完整的文件")
        self.repair_button.hide()
        for button in (self.verify_button, self.try_button, self.repair_button):
            checks.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        self.check_status = CheckStatus()
        checks.addSpacing(4)
        checks.addWidget(self.check_status, 1)
        layout.addWidget(self.check_row)
        divider = QFrame()
        divider.setObjectName("divider")
        layout.addWidget(divider)
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.state = ElidedLabel("", "muted")
        bottom.addWidget(self.state, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(160)
        bottom.addWidget(self.progress)
        self.delete_button = make_button("删除", Glyph.DELETE, "danger")
        bottom.addWidget(self.delete_button)
        self.cancel_button = make_button("取消下载")
        bottom.addWidget(self.cancel_button)
        self.action = make_button("", None, "primary")
        self.action.setMinimumWidth(128)
        bottom.addWidget(self.action)
        layout.addLayout(bottom)

    def set_checking(self, checking: bool):
        for button in (self.verify_button, self.try_button, self.repair_button):
            button.setEnabled(not checking)

    def update_state(self, *, active: bool, ready: bool, directory: Path, downloading: str | None, percent: int):
        mine = downloading == self.model.id
        self.check_row.setVisible(ready and not mine)
        self.setProperty("active", active and ready)
        repolish(self)
        self.active_tag.setVisible(active and ready)
        self.progress.setVisible(mine)
        self.progress.setValue(percent if mine else 0)
        self.cancel_button.setVisible(mine)
        self.delete_button.setVisible(ready and not active and not mine and self.model.id != SENSE_VOICE.id)
        self.action.setVisible(not mine)
        self.action.set_icon(None)
        if mine:
            self.state.setText(f"正在下载 {percent}%  ·  下载后自动校验完整性")
        elif ready:
            self.state.setText(f"已下载  ·  {directory}")
        else:
            self.state.setText("未下载" + ("  ·  当前选择，需下载后才能识别" if active else ""))
        if ready and active:
            self.action.setText("正在使用")
            self.action.setEnabled(False)
        elif ready:
            self.action.setText("使用此模型")
            self.action.setEnabled(True)
        else:
            self.action.setText(icon_text(f"下载 · {self.model.size_label.removeprefix('约 ')}"))
            self.action.set_icon(Glyph.DOWNLOAD)
            self.action.setEnabled(downloading is None)
            self.action.setToolTip("请等待当前下载完成" if downloading else "")


class ModelDialog(QDialog):
    """Choose, download and switch local speech models."""

    changed = Signal(object)

    def __init__(self, settings: Settings, downloads, apply, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.downloads = downloads
        self.apply = apply
        self.setWindowTitle("语音模型 · 好好说")
        available = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(720, max(560, min(860, available - 60)))
        self.setMinimumSize(600, 520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(
            dialog_header("语音模型", "全部在本机识别，录音不会上传。按需下载，随时切换；当前模型保持不变，直到你选择其他模型。")
        )
        tabs = QHBoxLayout()
        tabs.setContentsMargins(30, 6, 30, 10)
        self.tabs = Segmented(("精选模型", f"模型广场 · {len(PLAZA)}"))
        self.tab_group, self.tab_buttons = self.tabs.group, self.tabs.buttons
        tabs.addWidget(self.tabs)
        tabs.addStretch()
        outer.addLayout(tabs)

        location = QFrame()
        location.setObjectName("locationBar")
        row = QHBoxLayout(location)
        row.setContentsMargins(16, 12, 12, 12)
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label("下载位置", "fieldLabel"))
        self.location = ElidedLabel("", "strength")
        text.addWidget(self.location)
        row.addLayout(text, 1)
        self.space = label("", "caption")
        row.addWidget(self.space)
        self.change_location = make_button("更改…", None, "toolbar")
        self.change_location.clicked.connect(self.choose_location)
        row.addWidget(self.change_location)
        self.open_location = make_button("打开", Glyph.FOLDER, "toolbar")
        self.open_location.clicked.connect(self.open_folder)
        row.addWidget(self.open_location)
        location_row = QHBoxLayout()
        location_row.setContentsMargins(30, 0, 26, 4)
        location_row.addWidget(location)
        outer.addLayout(location_row)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.cards: dict[str, ModelCard] = {}

        area, layout = scroll_body(12)
        self.pages.addWidget(area)
        for model in CATALOG:
            layout.addWidget(self.make_card(model))
        note = label(
            "准确率来自各模型公开测试和本机示例录音，实际效果因口音、麦克风和环境而异。"
            "速度为参考值，实际随电脑而异；切换后首次识别需要几秒加载模型。",
            "caption",
            wrap=True,
        )
        layout.addWidget(note)
        layout.addStretch()

        area, layout = scroll_body(12)
        self.pages.addWidget(area)
        intro = label(
            "精选模型已覆盖绝大多数场景。有特殊需要时，可以在这里挑选更多开源模型，"
            "比如更高的中文准确率、方言或其他语言。",
            "plazaIntro",
            wrap=True,
        )
        layout.addWidget(intro)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_group = QButtonGroup(self)
        self.chips: dict[str, QPushButton] = {}
        for category in ("全部", *PLAZA_CATEGORIES):
            count = len(PLAZA) if category == "全部" else sum(m.category == category for m in PLAZA)
            chip = make_button(f"{category} {count}", None, "chip")
            chip.setCheckable(True)
            chip.clicked.connect(lambda _=False, c=category: self.filter_plaza(c))
            self.chip_group.addButton(chip)
            self.chips[category] = chip
            chips.addWidget(chip)
        chips.addStretch()
        layout.addLayout(chips)
        for model in PLAZA:
            layout.addWidget(self.make_card(model))
        layout.addWidget(
            label(
                "速度为参考值，实际随电脑而异。各模型的许可协议见模型主页。",
                "caption",
                wrap=True,
            )
        )
        layout.addStretch()
        self.tabs.changed.connect(self.show_page)
        start = 1 if settings.asr_model in {m.id for m in PLAZA} else 0
        self.tabs.set_current(start, animate=False)
        self.pages.setCurrentIndex(start)
        self.chips["全部"].setChecked(True)

        footer, actions = footer_bar()
        self.message = label("", "muted")
        actions.addWidget(self.message, 1)
        close = make_button("完成", None, "primary")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        outer.addWidget(footer)

        downloads.progress.connect(self.on_progress)
        downloads.finished.connect(self.on_finished)
        downloads.failed.connect(self.on_failed)
        self.refresh()

    def make_card(self, model: AsrModel) -> ModelCard:
        item = ModelCard(model)
        item.action.clicked.connect(lambda _=False, m=model: self.primary_action(m))
        item.cancel_button.clicked.connect(lambda _=False: self.downloads.cancel())
        item.delete_button.clicked.connect(lambda _=False, m=model: self.delete_model(m))
        item.verify_button.clicked.connect(lambda _=False, m=model: self.verify_model(m))
        item.try_button.clicked.connect(lambda _=False, m=model: self.try_model(m))
        item.repair_button.clicked.connect(lambda _=False, m=model: self.repair_model(m))
        self.cards[model.id] = item
        return item

    def verify_model(self, model: AsrModel):
        from .checks import check_files

        card, directory = self.cards[model.id], self.settings.model_path(model.id)
        card.set_checking(True)
        card.check_status.running(f"正在校验 {len(model.files)} 个文件…")

        def finished(result):
            card.set_checking(False)
            card.check_status.finish(result.ok, result.message)
            card.repair_button.setVisible(not result.ok)

        run_task(self, lambda _report: check_files(model, directory), finished)

    def try_model(self, model: AsrModel):
        from .checks import TRY_SECONDS, CheckResult, record_clip, try_model

        card, directory, microphone = self.cards[model.id], self.settings.model_path(model.id), self.settings.microphone
        if card.stop_recording is not None:  # "说完了"
            card.stop_recording.set()
            return
        stop = card.stop_recording = threading.Event()
        card.set_checking(True)
        card.try_button.setText(icon_text("说完了"))
        card.try_button.setEnabled(True)
        card.check_status.running(f"请对着麦克风说话，最长 {TRY_SECONDS} 秒…")
        recognizing = f"正在用 {model.name} 识别…"

        def work(report):
            try:
                samples = record_clip(
                    TRY_SECONDS, microphone, stop, lambda left: report(f"正在听… 还剩 {left} 秒，说完点“说完了”")
                )
            except Exception:  # noqa: BLE001 - PortAudio errors vary by device
                return CheckResult(False, "无法打开麦克风，请在“设置”里检查麦克风")
            report(recognizing)
            return try_model(model, directory, samples, self.settings.gpu_acceleration)

        def listening_done():
            card.stop_recording = None
            card.try_button.setText(icon_text("试一试"))

        def progress(text):
            card.check_status.running(text)
            if text == recognizing:
                listening_done()
                card.try_button.setEnabled(False)

        def finished(result):
            listening_done()
            card.set_checking(False)
            card.check_status.finish(result.ok, result.message)

        run_task(self, work, finished, progress)

    def repair_model(self, model: AsrModel):
        card = self.cards[model.id]
        if self.downloads.start(model.id, self.settings.model_path(model.id)):
            card.repair_button.hide()
            card.check_status.clear()
            self.message.setText(f"正在重新下载 {model.name} 不完整的文件，完整的文件不会重复下载。")
        self.refresh()

    def show_page(self, index: int):
        self.pages.setCurrentIndex(index)
        fade_in(self.pages.currentWidget(), 180)

    def filter_plaza(self, category: str):
        self.chips[category].setChecked(True)
        for model in PLAZA:
            self.cards[model.id].setVisible(category == "全部" or model.category == category)

    def done(self, result):
        for signal, slot in (
            (self.downloads.progress, self.on_progress),
            (self.downloads.finished, self.on_finished),
            (self.downloads.failed, self.on_failed),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

    def refresh(self):
        root = self.settings.download_root
        space = free_space(root)
        self.location.setText(str(root))
        self.space.setText(f"剩余 {format_bytes(space)}" if space is not None else "")
        busy = self.downloads.active
        self.change_location.setEnabled(busy is None)
        self.change_location.setToolTip("下载进行中，完成后再更改位置" if busy else "")
        for model in ALL_MODELS:
            directory = self.settings.model_path(model.id)
            self.cards[model.id].update_state(
                active=model.id == self.settings.asr_model,
                ready=models_ready(directory, model=model),
                directory=directory,
                downloading=busy,
                percent=self.downloads.percent,
            )

    def commit(self, settings: Settings) -> bool:
        if not self.apply(settings):
            QMessageBox.warning(self, "无法保存", "无法写入本地设置，请检查用户目录权限。")
            return False
        self.settings = settings
        self.changed.emit(settings)
        self.refresh()
        return True

    def primary_action(self, model: AsrModel):
        directory = self.settings.model_path(model.id)
        if models_ready(directory, model=model):
            if self.commit(replace(self.settings, asr_model=model.id)):
                self.message.setText(f"已切换到 {model.name}，下次录音生效。")
            return
        space = free_space(directory)
        if space is not None and space < model.size * 1.05:
            QMessageBox.warning(
                self,
                "磁盘空间不足",
                f"{model.name} 需要约 {format_bytes(model.size)}，所选位置只剩 {format_bytes(space)}。\n请更改下载位置后重试。",
            )
            return
        if self.downloads.start(model.id, directory):
            self.message.setText(f"开始下载 {model.name}，可以关闭此窗口，下载会在后台继续。")
        self.refresh()

    def delete_model(self, model: AsrModel):
        directory = self.settings.model_path(model.id)
        answer = QMessageBox.question(
            self,
            "删除模型",
            f"删除 {model.name} 的模型文件（{model.size_label}）？\n{directory}\n\n之后需要时可以重新下载。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_model_files(model, directory)
        except OSError:
            QMessageBox.warning(self, "未能删除", "模型文件正在使用或没有权限，请稍后重试。")
        paths = {key: value for key, value in self.settings.model_paths.items() if key != model.id}
        if paths != self.settings.model_paths:
            self.commit(replace(self.settings, model_paths=paths))
        self.message.setText(f"已删除 {model.name}。")
        self.refresh()

    def choose_location(self):
        if self.downloads.active:
            return
        chosen = QFileDialog.getExistingDirectory(self, "选择模型下载位置", str(self.settings.download_root))
        if not chosen:
            return
        directory = Path(chosen)
        try:
            probe = directory / f".write-test-{os.getpid()}"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            QMessageBox.warning(self, "无法使用此位置", "没有写入权限，请选择其他文件夹。")
            return
        if self.commit(self.settings.with_download_root(directory)):
            self.message.setText("新下载将保存到此位置，已下载的模型保留在原处。")

    def open_folder(self):
        root = self.settings.download_root
        try:
            root.mkdir(parents=True, exist_ok=True)
            os.startfile(root)  # type: ignore[attr-defined]
        except OSError:
            self.message.setText("无法打开文件夹，请检查路径是否存在。")

    def on_progress(self, model_id: str, percent: int):
        item = self.cards.get(model_id)
        if item:
            item.progress.setValue(percent)
            item.state.setText(f"正在下载 {percent}%  ·  下载后自动校验完整性")

    def on_finished(self, model_id: str):
        self.message.setText("下载完成，点击“使用此模型”即可切换。")
        self.refresh()

    def on_failed(self, model_id: str, message: str):
        self.message.setText(message or "已取消下载。")
        self.refresh()
