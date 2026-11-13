from __future__ import annotations

import html
import math
import os
import re
import shutil
import threading
from collections import deque
from itertools import pairwise
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QLibraryInfo,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QTranslator,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QBrush,
    QConicalGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
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
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .api_config import chat_completion_url
from .icons import draw_icon, icon_pixmap
from .models import (
    ALL_MODELS,
    CATALOG,
    ENGINE_DIR,
    GPU_ENGINE_DIR,
    PLAZA,
    PLAZA_CATEGORIES,
    AsrModel,
    discard_partial,
    find_models,
    get_model,
    models_ready,
)
from .settings import Settings, read_key, save_key
from .vocabulary import FILE_FILTER, LexiconError, import_lexicon
from .shortcuts import (
    KEY_NAMES,
    RECORDING_CHOICES,
    SELECTION_CHOICES,
    selection_hint,
    shortcut_label,
    shortcut_name,
    shortcut_problem,
    usage_hint,
)
from .translation import LANGUAGES as TRANSLATION_LANGUAGES
from .translation import SCRIPTS as TRANSLATION_SCRIPTS
from .translation import STYLES as TRANSLATION_STYLES

# The palette grows out of the icon (brand.py): warm paper, ink, one indigo fold and a coral
# breast. Coral marks what is live (listening, recording, the other voices); nothing else is loud.
PAPER = "#F6F1E8"  # The window.
SHEET = "#FFFCF7"  # What lies on it: cards, fields, menus, popups.
LINE = "#E8E0D3"  # Hairlines between things.
INK = "#1D2240"  # Text.
MUTED = "#776F63"  # Quieter text.
ACCENT = "#192457"  # Ink navy: the primary button, the chosen tab, key numbers.
ACCENT_SOFT = "#ECEDF5"
INDIGO = "#3C488E"
CORAL = "#F2755F"
RECORD = "#E5654F"
DISABLED = "#CFC6B8"
SERIF = "Saywell Serif"  # Headings and large numbers (assets/fonts, a cut of Noto Serif SC).
# A voice on the dark recording pill: indigo through a dusty mauve to coral. On paper the bars
# stay in ink, as the card's coral edge already says it is live.
VOICE = ("#3C488E", "#8A6F9E", "#F2755F")

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 14px; color: #1D2240; }
QMainWindow, QDialog { background: #F6F1E8; }
QLabel { background: transparent; }
QLabel#brand { font-family: 'Saywell Serif'; font-weight: 700; color: #192457; font-size: 21px; }
QLabel#brandLatin { font-family: 'Saywell Serif'; font-size: 15px; font-weight: 600; color: #A69D90;
                    padding-bottom: 2px; }
QLabel#title { font-family: 'Saywell Serif'; font-weight: 700; color: #192457; font-size: 26px; }
QLabel#muted { color: #776F63; font-size: 13px; }
QLabel#caption { color: #958C7F; font-size: 12px; }
QLabel#section { font-family: 'Saywell Serif'; font-weight: 700; color: #192457; font-size: 16px; }
QLabel#cardTitle { font-family: 'Saywell Serif'; font-weight: 700; color: #192457; font-size: 17px; }
QLabel#fieldLabel { color: #47413A; font-size: 13px; }
QLabel#count { color: #958C7F; font-size: 12px; }
QLabel#tag { color: #776F63; border: 1px solid #E5DCCD; border-radius: 9px; padding: 1px 8px; font-size: 12px; }
QLabel#tagActive { background: #192457; color: #FFFCF7; border-radius: 9px; padding: 2px 9px; font-size: 12px; }
QLabel#tagGpu { color: #3C488E; border: 1px solid #C3C8E4; border-radius: 9px; padding: 1px 8px; font-size: 12px; }
QLabel#strength { color: #47413A; font-size: 13px; }
QLabel#caveat { color: #958C7F; font-size: 12px; }
QLabel#speed { color: #776F63; font-size: 12px; }
QLabel#notice { background: #FBF2DE; color: #77561F; border: 1px solid #EEDDB4; border-radius: 12px;
                padding: 10px 14px; font-size: 13px; }

QFrame#hero QLabel { color: #1D2240; }
QLabel#status { font-family: 'Saywell Serif'; font-weight: 700; color: #192457; font-size: 28px; }
QLabel#heroHint { color: #776F63; font-size: 14px; }
QLabel#pill { font-size: 13px; color: #776F63; }
QLabel#heroMeta { color: #776F63; font-size: 13px; }
QLabel#keycap { background: #FBF7F0; color: #47413A; border: 1px solid #DCD3C4; border-bottom: 2px solid #D0C5B3;
                border-radius: 6px; padding: 0px 7px; font-size: 12px; }
QFrame#vrule { background: #E2D9CA; min-width: 1px; max-width: 1px; border: none; }
QFrame#hrule { background: #E8E0D3; min-height: 1px; max-height: 1px; border: none; }

QFrame#card { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 18px; }
QFrame#card[accent="true"] { border: 1px solid #D3D7EB; }
QFrame#card QPlainTextEdit { background: transparent; border: none; padding: 2px 12px 12px 12px;
                             font-size: 16px; selection-background-color: #D8DBEE; }
QFrame#sectionCard { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 16px; }
QFrame#modelCard { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 16px; }
QFrame#modelCard[active="true"] { border: 2px solid #192457; }
QFrame#locationBar { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 14px; }
QFrame#footerBar { background: #FFFCF7; border-top: 1px solid #E8E0D3; }
QFrame#divider { background: #F1EBE1; max-height: 1px; min-height: 1px; border: none; }

QPlainTextEdit { background: #FFFCF7; border: 1px solid #DCD3C4; border-radius: 12px; padding: 10px;
                 selection-background-color: #D8DBEE; }
QPlainTextEdit:focus { border-color: #192457; }
QLineEdit, QComboBox, QSpinBox { background: #FFFCF7; border: 1px solid #DCD3C4; border-radius: 10px;
    padding: 7px 11px; min-height: 22px; selection-background-color: #D8DBEE; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #192457; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #C4BAAA; }
QComboBox { padding-right: 32px; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView { background: #FFFCF7; border: 1px solid #DCD3C4; border-radius: 10px;
    selection-background-color: #ECEDF5; selection-color: #1D2240; outline: none; padding: 4px; }

QPushButton { background: #EEE7DB; border: 1px solid transparent; border-radius: 17px; padding: 8px 18px;
              color: #1D2240; }
QPushButton:hover { background: #E7DFD1; }
QPushButton:pressed { background: #DED5C5; }
QPushButton:focus { border-color: #192457; }
QPushButton:disabled { color: #B5AC9F; background: #F2ECE2; }
QLabel#checkText { color: #776F63; font-size: 13px; }
QLabel#checkText[tone="ok"] { color: #3F7A53; }
QLabel#checkText[tone="fail"] { color: #B94534; }
QLabel#plazaIntro { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 14px; padding: 12px 16px;
    color: #47413A; font-size: 13px; }

QProgressBar { background: #E8E0D3; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #192457; border-radius: 3px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #CFC6B8; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #B5AC9F; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
QListWidget { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 14px; padding: 6px; outline: none; }
QListWidget::item { padding: 10px 12px; border-radius: 9px; color: #1D2240; }
QListWidget::item:hover { background: #F6F1E8; }
QListWidget::item:selected { background: #ECEDF5; color: #1D2240; }
QMenu { background: #FFFCF7; border: 1px solid #E5DCCD; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: #EEE7DB; color: #1D2240; }
QMenu::item:disabled { color: #B5AC9F; }
QMenu::separator { height: 1px; background: #E8E0D3; margin: 5px 8px; }
QWidget#settingsSide { background: #EFE8DC; border-right: 1px solid #E8E0D3; }
QLabel#sideTitle { font-family: 'Saywell Serif'; font-weight: 700; color: #192457; font-size: 24px; padding-left: 10px; }
QLabel#pageTitle { font-family: 'Saywell Serif'; font-weight: 700; color: #192457; font-size: 22px; }
QLabel#settingsGroup { color: #8C8376; font-size: 13px; font-weight: 600; padding-left: 2px; }
QFrame#settingsCard { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 14px; }
QFrame#settingsRule { background: #EEE7DB; min-height: 1px; max-height: 1px; border: none; margin: 0 16px; }
QLabel#rowTitle { color: #1D2240; font-size: 14px; }
QLabel#rowHint { color: #776F63; font-size: 12px; }
QLabel#versionName { color: #1D2240; font-size: 15px; font-weight: 700; }
QFrame#updateNews { background: #ECEDF5; border-radius: 12px; }
QLabel#newsTitle { color: #192457; font-size: 14px; font-weight: 700; }
QLabel#newsText { color: #47413A; font-size: 13px; }
QFrame#updateNews QProgressBar { background: #D9DCEC; }
QSlider { min-height: 24px; }
QSlider::groove:horizontal { height: 4px; background: #E8E0D3; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #192457; border-radius: 2px; }
QSlider::handle:horizontal { background: #FFFCF7; border: 1px solid #C4BAAA; width: 16px; height: 16px;
    margin: -7px 0; border-radius: 9px; }
QSlider::handle:horizontal:hover { border-color: #192457; }
QToolTip { background: #1D2240; color: #FFFCF7; border: none; padding: 6px 9px; border-radius: 6px; }
"""


class Glyph:
    """Names of the app's own line icons (see icons.py)."""

    MIC = "mic"
    SETTINGS = "settings"
    HISTORY = "history"
    IMPORT = "import"
    EXPORT = "export"
    MODEL = "model"
    DOWNLOAD = "download"
    COPY = "copy"
    DELETE = "delete"
    CLEAR = "clear"
    CHECK = "check"
    CHEVRON_DOWN = "chevron_down"
    CHEVRON_RIGHT = "chevron_right"
    CHEVRON_UP = "chevron_up"
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
    SWAP = "swap"
    SPEAKER = "speaker"
    MIC_OFF = "mic_off"
    HEADPHONES = "headphones"
    STOP = "stop"
    PASTE = "paste"
    BOOK = "book"
    CLOUD = "cloud"
    OVERLAY = "overlay"


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


def ease_out(t: float) -> float:
    """Fast start, gentle stop: fades, hovers and anything that should not bounce."""
    return 1 - (1 - t) ** 3


def spring(t: float, damping: float = 0.7) -> float:
    """Step response of a damped spring, settled by t = 1. Below damping 1 it lands with a
    small overshoot, which is what makes switches and thumbs feel physical rather than linear."""
    if t >= 1:
        return 1.0
    omega = 10.0
    if damping >= 1:
        return 1 - math.exp(-omega * t) * (1 + omega * t)
    damped = omega * math.sqrt(1 - damping * damping)
    decay = math.exp(-damping * omega * t)
    return 1 - decay * (math.cos(damped * t) + damping * omega / damped * math.sin(damped * t))


class Tween(QObject):
    """A number that glides to each new target. Retargeting mid-flight starts from where it is,
    so a quick double toggle reverses smoothly instead of jumping."""

    def __init__(self, owner: QObject, value: float = 0.0, duration: int = 240, ease=ease_out, on_change=None):
        super().__init__(owner)
        self.value = self.start = self.target = value
        self.duration = duration
        self.ease = self.current_ease = ease
        self.on_change = on_change or owner.update
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(self._step)

    def to(self, target: float, duration: int | None = None, ease=None, animate: bool = True):
        if not animate:
            self.animation.stop()
            self.value = self.start = self.target = target
            self.on_change()
            return
        if target == self.target and (self.animation.state() == QVariantAnimation.State.Running or self.value == target):
            return
        self.animation.stop()
        self.start, self.target = self.value, target
        self.current_ease = ease or self.ease
        self.animation.setDuration(duration or self.duration)
        self.animation.start()

    def running(self) -> bool:
        return self.animation.state() == QVariantAnimation.State.Running

    def _step(self, t):
        self.value = self.start + (self.target - self.start) * self.current_ease(float(t))
        self.on_change()


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
    "": ButtonLook("#EEE7DB", "#E7DFD1", "#DED5C5", INK),
    "primary": ButtonLook(ACCENT, "#26336E", "#111A44", "#FFFCF7", padding=18, bold=True),
    "toolbar": ButtonLook(CLEAR_COLOR, "#EEE7DB", "#E2D9CA", "#47413A", height=34, padding=12),
    "ghost": ButtonLook(CLEAR_COLOR, "#EEE7DB", "#E2D9CA", "#47413A", height=34, padding=12),
    "danger": ButtonLook(CLEAR_COLOR, "#FBEAE5", "#F6D9D0", "#B94534", height=34, padding=12),
    "link": ButtonLook(CLEAR_COLOR, "#ECEDF5", "#DFE2F1", "#3C488E", height=28, padding=9, size=13),
    "footer": ButtonLook(CLEAR_COLOR, "#ECEDF5", "#DFE2F1", "#3C488E", height=30, padding=10, size=13, bold=True),
    "chip": ButtonLook("#FFFCF7", "#F8F4ED", "#EEE7DB", "#47413A", "#E5DCCD", 30, 13, 13,
                       checked_background=INK, checked_text="#FFFCF7"),
    "check": ButtonLook("#FFFCF7", "#FFFCF7", "#F3F3F8", INK, "#E2D9CA", 30, 13, 13, hover_border=ACCENT),
    "segment": ButtonLook(CLEAR_COLOR, CLEAR_COLOR, CLEAR_COLOR, MUTED, height=30, padding=18, size=13,
                          checked_text=INK),
    "heroChip": ButtonLook(CLEAR_COLOR, "#F1EBE1", "#E8E0D3", MUTED, height=28, padding=10, size=13),
    "heroGhost": ButtonLook("#F2ECE2", "#EEE7DB", "#E2D9CA", INK, height=30, padding=16, size=13),
}


class Button(QPushButton):
    """Pill button drawn by the app: soft hover, a press that gives a little and springs back,
    checked states that fade rather than flip, an optional line icon, and a short "done" flash
    (for example 已复制) after an action."""

    def __init__(self, text: str = "", icon: str | None = None, kind: str | None = None, icon_color: str | None = None):
        super().__init__(text)
        self.kind = kind or ""
        self.look = BUTTON_LOOKS.get(self.kind, BUTTON_LOOKS[""])
        if kind:
            self.setObjectName(kind)
        self.icon_name = icon
        self.icon_color = icon_color
        # An image in place of the line icon (an app's own icon), a chevron after the text for
        # buttons that open a menu, and a small dot beside the text (a meeting still recording).
        self.picture: QPixmap | None = None
        self.trailing: str | None = None
        self.badge = ""
        self.keyboard_focus = False
        self.restore: tuple[str, str | None] | None = None
        self.hover = Tween(self)
        self.press = Tween(self)
        self.check = Tween(self, duration=220)
        self.pressed.connect(lambda: self.press.to(1.0, 90))
        self.released.connect(lambda: self.press.to(0.0, 420, lambda t: spring(t, 0.55)))
        self.toggled.connect(lambda on: self.check.to(float(on), animate=self.isVisible()))
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

    def set_picture(self, picture: QPixmap | None):
        self.picture = picture
        self.updateGeometry()
        self.update()

    def set_trailing(self, glyph: str | None):
        self.trailing = glyph
        self.updateGeometry()
        self.update()

    def set_badge(self, color: str):
        self.badge = color
        self.update()

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
        if self.icon_name or self.picture:
            width += 16 + (6 if text else 0)
        if self.trailing:
            width += 12 + 5
        padding = self.look.padding if text else (self.look.height - 16) // 2
        return QSize(int(width + padding * 2), self.look.height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ---------- interaction ----------
    def enterEvent(self, event):
        self.hover.to(1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0.0, 260)
        super().leaveEvent(event)

    def showEvent(self, event):
        self.check.to(float(self.isChecked()), animate=False)
        super().showEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.press.to(0.0, animate=False)  # Disabled mid-press: no release will come.
        super().changeEvent(event)

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        look = self.look
        if not self.isEnabled():
            painter.setOpacity(0.4)
        enabled = self.isEnabled()
        checked = self.check.value if self.isCheckable() else 0.0
        press = max(0.0, min(1.0, self.press.value)) if enabled else 0.0
        # A press sinks the pill by a few percent; the release spring lets it overshoot back.
        scale = 1 - 0.035 * self.press.value if enabled else 1.0
        if scale != 1.0:
            painter.translate(self.width() / 2, self.height() / 2)
            painter.scale(scale, scale)
            painter.translate(-self.width() / 2, -self.height() / 2)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        background = mix(QColor(look.background), QColor(look.hover), self.hover.value if enabled else 0)
        background = mix(background, QColor(look.pressed), press)
        border = QColor(look.border)
        if look.hover_border and enabled:
            border = mix(border, QColor(look.hover_border), self.hover.value)
        if look.checked_background and checked:
            background = mix(background, QColor(look.checked_background), checked)
            border = mix(border, QColor(look.checked_background), checked)
        painter.setPen(QPen(border, 1) if border.alpha() else Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus() and self.keyboard_focus:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.55)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1.5, radius - 1.5)
        color = QColor(look.text)
        if look.checked_text and checked:
            color = mix(color, QColor(look.checked_text), checked)
        text = self.text().strip()
        font = self.label_font()
        width = QFontMetrics(font).horizontalAdvance(text) if text else 0
        content = width + (16 + (6 if text else 0) if self.icon_name or self.picture else 0)
        content += 12 + 5 if self.trailing else 0
        x = (self.width() - content) / 2
        if self.picture:
            target = QRectF(x, (self.height() - 16) / 2, 16, 16)
            painter.drawPixmap(target, self.picture, QRectF(self.picture.rect()))
            x += 16 + 6
        elif self.icon_name:
            tint = mix(QColor(self.icon_color), color, checked) if self.icon_color else color
            draw_icon(painter, self.icon_name, QRectF(x, (self.height() - 16) / 2, 16, 16), tint)
            x += 16 + 6
        if text:
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(QRectF(x, 0, width + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
        if self.badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.badge))
            painter.drawEllipse(QRectF(x + width + 3, self.height() / 2 - 8, 6, 6))
        if self.trailing:
            draw_icon(painter, self.trailing, QRectF(x + width + 5, (self.height() - 12) / 2, 12, 12), MUTED)


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


OK_GREEN = "#5A9E6F"
FAIL_RED = "#E5654F"


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
        painter.setPen(QPen(QColor("#EEE7DB"), 1.8))
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
        draw_icon(painter, "check" if self.ok else "cancel", rect.adjusted(3, 3, -3, -3), "#FFFCF7", 3.2)


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
        self.home = (0, 0)
        # Floats up a few pixels as it fades in, and drifts down as it fades out.
        self.presence = Tween(self, on_change=self._present)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.presence.to(0.0, 260))
        self.hide()

    def show_text(self, text: str, icon: str = "check", milliseconds: int = 1600):
        self.text, self.icon = text, icon
        font = QFont(self.font())
        font.setPixelSize(13)
        width = QFontMetrics(font).horizontalAdvance(text) + 16 + 8 + 36
        self.resize(width, 38)
        parent = self.parentWidget()
        self.home = ((parent.width() - width) // 2, parent.height() - 38 - 10)
        self.raise_()
        if not self.isVisible():
            self.presence.to(0.0, animate=False)
            self.show()
        self.presence.to(1.0, 420, lambda t: spring(t, 0.8))
        self.timer.start(milliseconds)
        self.update()

    def _present(self):
        value = self.presence.value
        self.effect.setOpacity(max(0.0, min(1.0, value)))
        self.move(self.home[0], round(self.home[1] + (1 - value) * 10))
        if value <= 0.001 and self.presence.target == 0.0:
            self.hide()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(29, 34, 64, 236))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        draw_icon(painter, self.icon, QRectF(18, (self.height() - 16) / 2, 16, 16), "#FFFCF7")
        font = QFont(self.font())
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor("#FFFCF7"))
        painter.drawText(QRectF(18 + 16 + 8, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)


class Segmented(QFrame):
    """Segmented control whose paper thumb glides to the chosen segment and settles with a
    slight spring; the labels cross-fade between grey and black as it passes."""

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
        self.origin = QRectF()
        self.destination = QRectF()
        self.glide = Tween(self, duration=460, ease=lambda t: spring(t, 0.78), on_change=self._moved)
        self.group.idClicked.connect(self._clicked)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def current(self) -> int:
        return self.group.checkedId()

    def set_current(self, index: int, animate: bool = True):
        self.buttons[index].setChecked(True)
        target = QRectF(self.buttons[index].geometry())
        if animate and self.isVisible() and not self.thumb.isNull():
            self.origin, self.destination = QRectF(self.thumb), target
            self.glide.to(0.0, animate=False)
            self.glide.to(1.0)
        else:
            self.glide.animation.stop()
            self.thumb = self.destination = target
            self.update()

    def _clicked(self, index: int):
        self.set_current(index)
        self.changed.emit(index)

    def _moved(self):
        t = self.glide.value
        a, b = self.origin, self.destination
        self.thumb = QRectF(
            a.x() + (b.x() - a.x()) * t,
            a.y() + (b.y() - a.y()) * t,
            a.width() + (b.width() - a.width()) * t,
            a.height() + (b.height() - a.height()) * t,
        )
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current() >= 0 and not self.glide.running():
            self.thumb = QRectF(self.buttons[self.current()].geometry())

    def showEvent(self, event):
        super().showEvent(event)
        if self.current() >= 0:
            self.thumb = QRectF(self.buttons[self.current()].geometry())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#EEE7DB"))
        rect = QRectF(self.rect())
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if not self.thumb.isNull():
            thumb = QRectF(self.thumb)
            painter.setBrush(QColor(0, 0, 0, 16))
            painter.drawRoundedRect(thumb.adjusted(0, 1, 0, 1.5), thumb.height() / 2, thumb.height() / 2)
            painter.setBrush(QColor("#FFFCF7"))
            painter.drawRoundedRect(thumb, thumb.height() / 2, thumb.height() / 2)


class KeyHint(QWidget):
    """A key drawn as a small keycap, then what it does: [右 Ctrl] 按住说话."""

    def __init__(self, key: str = "", action: str = ""):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.key = label(key, "keycap")
        self.action = label(action, "heroMeta")
        row.addWidget(self.key, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_hint(self, key: str, action: str):
        self.key.setText(key)
        self.action.setText(action)

    def text(self) -> str:
        return f"{self.key.text()} {self.action.text()}"


def pill_text(color: str, text: str, live: bool = False) -> str:
    """The small status line above a hero's heading, as rich text: a dot, then the words."""
    words = f"<span style='color:{color}; font-weight:600'>{text}</span>" if live else text
    return f"<span style='color:{color}'>●</span>&nbsp;&nbsp;{words}"


def hrule() -> QFrame:
    """A hairline across, between parts of a page."""
    line = QFrame()
    line.setObjectName("hrule")
    return line


def vrule(height: int = 14) -> QFrame:
    """A short upright hairline between things on one line."""
    line = QFrame()
    line.setObjectName("vrule")
    line.setFixedHeight(height)
    return line


class TabBar(Segmented):
    """The app's pages as plain words; the chosen one is in ink with a short coral line under it,
    and the line glides to the next one. Same API as Segmented."""

    def __init__(self, labels: tuple[str, ...] | list[str], parent=None):
        super().__init__(labels, parent)
        self.layout().setContentsMargins(0, 0, 0, 6)
        self.layout().setSpacing(6)
        for button in self.buttons:
            button.look = replace(BUTTON_LOOKS["segment"], height=34, padding=14, size=15, checked_text=ACCENT)
            button.setMinimumWidth(0)
            button.updateGeometry()

    def paintEvent(self, _event):
        if self.thumb.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CORAL))
        width = min(22.0, self.thumb.width() - 20)
        line = QRectF(self.thumb.center().x() - width / 2, self.thumb.bottom() + 2, width, 2.5)
        painter.drawRoundedRect(line, 1.25, 1.25)


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


TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)
_fonts_loaded: list[bool] = []


def load_fonts() -> None:
    """The serif for headings and large numbers ships with the app (see THIRD_PARTY_NOTICES)."""
    if not _fonts_loaded:
        QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / "SaywellSerif-Bold.ttf"))
        _fonts_loaded.append(True)


_translators: list[QTranslator] = []


def load_translations() -> bool:
    """Qt's own words (the right-click menu's 复制/粘贴, standard buttons) in Chinese.

    PySide6 ships the translations; PyInstaller keeps them under PySide6/translations."""
    if _translators:
        return True
    import PySide6

    folders = (
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
        str(Path(PySide6.__file__).parent / "translations"),
    )
    for folder in folders:
        translator = QTranslator()
        if translator.load("qtbase_zh_CN", folder):
            QCoreApplication.installTranslator(translator)
            _translators.append(translator)
            return True
    return False


_icons: dict[str, QIcon] = {}


def app_icon() -> QIcon:
    """The swallow on its tile, drawn for each size (see brand.py)."""
    if "app" not in _icons:
        from .brand import icon_image

        icon = QIcon()
        for size in ICON_SIZES:
            icon.addPixmap(QPixmap.fromImage(icon_image(size)))
        _icons["app"] = icon
    return _icons["app"]


def tray_image_icon(color: str | None) -> QIcon:
    from .brand import tray_image

    icon = QIcon()
    for size in TRAY_SIZES:
        icon.addPixmap(QPixmap.fromImage(tray_image(size, color)))
    return icon


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
    """The bird alone, as tray icons are: white on a dark taskbar, in its own colours on a light one."""
    return tray_image_icon(None if taskbar_is_light() else "#FFFFFF")


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

    def __init__(self, bars: int = 36, colors: tuple[str, ...] = (INDIGO, ACCENT), rest: str = "#DCD3C4", parent=None):
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


class MicButton(QPushButton):
    """Round record button: a calm solid circle at rest, a breathing red ring while
    recording and a thin rotating arc while processing. objectName "recording"
    switches it to the stop state. Between states the color blends and the microphone
    shrinks away as the stop square grows in, so the button morphs instead of swapping."""

    DIAMETER = 76.0

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.busy = False
        self.phase = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(1400)
        self.animation.setLoopCount(-1)
        self.animation.valueChanged.connect(self._tick)
        self.hover = Tween(self, duration=160)
        self.press = Tween(self)
        self.recording = Tween(self, duration=420, on_change=self._settle)
        self.working = Tween(self, duration=360, on_change=self._settle)
        self.pressed.connect(lambda: self.press.to(1.0, 110))
        self.released.connect(lambda: self.press.to(0.0, 520, lambda t: spring(t, 0.5)))
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

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.press.to(0.0, animate=False)
        super().changeEvent(event)

    def _tick(self, value):
        self.phase = float(value)
        self.update()

    def _settle(self):
        # The ring and arc keep moving while they fade out; the loop stops once both are gone.
        if (
            not self.busy and self.objectName() != "recording"
            and self.recording.value <= 0.001 and self.working.value <= 0.001
        ):
            self.animation.stop()
            self.phase = 0.0
        self.update()

    def set_busy(self, busy: bool):
        self.busy = busy
        self._sync_animation()

    def _sync_animation(self):
        if not hasattr(self, "recording"):  # Qt may name the widget before __init__ finishes.
            return
        recording = self.objectName() == "recording"
        animate = self.isVisible()
        self.recording.to(float(recording), animate=animate)
        self.working.to(float(self.busy), animate=animate)
        if (recording or self.busy) and self.animation.state() != QVariantAnimation.State.Running:
            self.animation.start()
        self._settle()

    def setObjectName(self, name: str):
        super().setObjectName(name)
        self._sync_animation()

    def enterEvent(self, event):
        self.hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        recording = max(0.0, min(1.0, self.recording.value))
        working = max(0.0, min(1.0, self.working.value))
        hover = self.hover.value
        diameter = self.DIAMETER * (1 - 0.06 * self.press.value)
        center_x, center_y = self.width() / 2, 12 + self.DIAMETER / 2
        circle = QRectF(center_x - diameter / 2, center_y - diameter / 2, diameter, diameter)
        if not self.isEnabled() and not self.busy:
            painter.setOpacity(0.4)
        painter.setPen(Qt.PenStyle.NoPen)
        if recording > 0:
            # One soft ring breathing outward.
            radius = diameter / 2 + 2 + self.phase * 11
            ring = QColor(RECORD)
            ring.setAlphaF(0.28 * (1 - self.phase) * recording * (1 - working))
            painter.setBrush(ring)
            painter.drawEllipse(QRectF(center_x - radius, center_y - radius, radius * 2, radius * 2))
        idle_fill = mix(QColor(ACCENT), QColor("#26336E"), hover)
        record_fill = mix(QColor(RECORD), QColor("#EE7A66"), hover)
        # Straight ink → coral goes through mud; a dusty violet and rose keep it clean.
        path = (idle_fill.name(), "#4B3F7A", "#B45E73", record_fill.name())
        fill = mix(blend(path, recording), QColor("#F2ECE2"), working)
        if not self.busy:
            fill = mix(fill, fill.darker(112), max(0.0, min(1.0, self.press.value)))
        painter.setBrush(fill)
        painter.drawEllipse(circle)
        if self.hasFocus() and self.keyboard_focus:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.5)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle.adjusted(-5, -5, 5, 5))
        base = painter.opacity()
        mic = max(0.0, 1 - recording - working)  # Stays hidden while recording hands over to working.
        if mic > 0.01:
            size = 30 * (0.6 + 0.4 * mic)
            painter.setOpacity(base * mic)
            draw_icon(painter, "mic", QRectF(center_x - size / 2, center_y - size / 2, size, size), "#FFFCF7", 2.0)
        stop = recording * (1 - working)
        if stop > 0.01:
            size = 22 * (0.5 + 0.5 * stop)
            painter.setOpacity(base * stop)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFCF7"))
            painter.drawRoundedRect(QRectF(center_x - size / 2, center_y - size / 2, size, size), 5, 5)
        if working > 0.01:
            painter.setOpacity(base * working)
            pen = QPen(QColor(ACCENT), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(circle.adjusted(20, 20, -20, -20), int(-self.phase * 360 * 16), 100 * 16)
        painter.setOpacity(1.0 if self.isEnabled() or self.busy else 0.4)
        font = QFont(self.font())
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor(INK))
        text_rect = QRectF(0, circle.bottom() + 10, self.width(), 22)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())


class ComboBox(QComboBox):
    """QComboBox with a drawn chevron, so no image files are needed for the arrow.

    The wheel scrolls the page instead of changing the choice: scrolling the settings
    past the microphone list used to switch microphones. Choices change by clicking."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # The wheel no longer focuses it either.

    def wheelEvent(self, event):
        event.ignore()  # Passed on to the scroll area.

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        draw_icon(
            painter, "chevron_down", QRectF(self.width() - 30, (self.height() - 14) / 2, 14, 14),
            MUTED if self.isEnabled() else DISABLED,
        )


class SpinBox(QSpinBox):
    """A number field the wheel scrolls past, like ComboBox."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class HeroCard(QFrame):
    """The recording card: plain paper at rest. While listening its edge breathes in coral;
    while processing a single ink light runs along it."""

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
        # The two lights fade into each other rather than switching on and off.
        self.listening = Tween(self, duration=520, on_change=self._settle)
        self.working = Tween(self, duration=520, on_change=self._settle)

    def _tick(self, value):
        self.phase = float(value)
        self.update()

    def _settle(self):
        if self.mode == "idle" and self.listening.value <= 0.001 and self.working.value <= 0.001:
            self.animation.stop()
        self.update()

    def set_mode(self, mode: str):
        self.mode = mode if mode in ("recording", "busy") else "idle"
        animate = self.isVisible()
        self.listening.to(float(self.mode == "recording"), animate=animate)
        self.working.to(float(self.mode == "busy"), animate=animate)
        if self.mode != "idle" and self.animation.state() != QVariantAnimation.State.Running:
            self.animation.start()
        self._settle()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 20
        painter.setPen(QPen(QColor(LINE), 1))
        painter.setBrush(QColor(SHEET))
        painter.drawRoundedRect(rect, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        listening = self.listening.value
        if listening > 0.001:
            breath = 0.5 + 0.5 * math.sin(self.phase * 2 * math.pi * 2)  # Two slow breaths a loop.
            for inset, width, alpha in ((0.75, 1.5, 0.55 + 0.3 * breath), (3.0, 4.0, 0.06 + 0.06 * breath)):
                edge = QColor(CORAL)
                edge.setAlphaF(alpha * listening)
                painter.setPen(QPen(edge, width))
                painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), radius - inset, radius - inset)
        working = self.working.value
        if working > 0.001:
            light = QConicalGradient(rect.center(), -self.phase * 360)
            light.setColorAt(0.0, QColor(25, 36, 87, 200))
            light.setColorAt(0.25, QColor(25, 36, 87, 0))
            light.setColorAt(0.75, QColor(25, 36, 87, 0))
            light.setColorAt(1.0, QColor(25, 36, 87, 200))
            for inset, width, alpha in ((0.75, 1.5, 1.0), (3.0, 4.0, 0.12)):
                painter.setOpacity(alpha * working)
                painter.setPen(QPen(QBrush(light), width))
                painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), radius - inset, radius - inset)
        painter.setOpacity(1.0)


class Switch(QCheckBox):
    """A toggle switch that keeps the QCheckBox API. The knob springs across while the track
    fills with ink; holding it down stretches the knob toward where it is going, as on iOS."""

    TRACK_OFF = "#E8E0D3"
    TRACK_OFF_HOVER = "#E2D9CA"
    TRACK_ON_HOVER = "#26336E"

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.keyboard_focus = False
        self.position = Tween(self, float(self.isChecked()), 520, lambda t: spring(t, 0.62))
        self.fill = Tween(self, float(self.isChecked()), 260)
        self.stretch = Tween(self, duration=160)
        self.hover = Tween(self)
        self.toggled.connect(self._toggled)
        self.pressed.connect(lambda: self.stretch.to(1.0))
        self.released.connect(lambda: self.stretch.to(0.0, 300))

    def _toggled(self, on: bool):
        animate = self.isVisible()
        self.position.to(float(on), animate=animate)
        self.fill.to(float(on), animate=animate)

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QSize(46 + 12 + metrics.horizontalAdvance(self.text()), max(26, metrics.height() + 6))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def enterEvent(self, event):
        self.hover.to(1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0.0, 260)
        super().leaveEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.stretch.to(0.0, animate=False)
        super().changeEvent(event)

    def focusInEvent(self, event):
        # Clicking also focuses a checkbox; only Tab should draw the ring.
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, (self.height() - 24) / 2, 42, 24)
        if not self.isEnabled():
            painter.setOpacity(0.5)
        hover = self.hover.value if self.isEnabled() else 0.0
        off = mix(QColor(self.TRACK_OFF), QColor(self.TRACK_OFF_HOVER), hover)
        on = mix(QColor(ACCENT), QColor(self.TRACK_ON_HOVER), hover)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mix(off, on, max(0.0, min(1.0, self.fill.value))))
        painter.drawRoundedRect(track, 12, 12)
        # The knob is anchored to the side it rests on, so stretching grows it toward the other.
        width = 20 + 5 * self.stretch.value
        left, right = track.left() + 2, track.right() - 2 - width
        knob = QRectF(left + (right - left) * self.position.value, track.top() + 2, width, 20)
        painter.setBrush(QColor(0, 0, 0, 22))
        painter.drawRoundedRect(knob.adjusted(-0.5, 0.5, 0.5, 1.5), 10.5, 10.5)
        painter.setBrush(QColor(0, 0, 0, 12))
        painter.drawRoundedRect(knob.adjusted(-1, 1.5, 1, 3), 11, 11)
        painter.setBrush(QColor("#FFFCF7"))
        painter.drawRoundedRect(knob, 10, 10)
        if self.hasFocus() and self.keyboard_focus:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.55)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-3, -3, 3, 3), 15, 15)
        painter.setPen(QColor(INK))
        painter.setFont(self.font())
        painter.drawText(
            QRectF(track.right() + 12, 0, self.width() - track.right() - 12, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


class CheckBox(QCheckBox):
    """A small square checkbox in the macOS manner: paper with a warm edge, filling with ink as a
    tick draws itself when checked. For options too minor for a switch."""

    BORDER = "#CFC6B8"
    BORDER_HOVER = "#A69D90"
    TICK = ((4.0, 8.4), (6.8, 11.1), (12.2, 5.3))

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.keyboard_focus = False
        self.fill = Tween(self, float(self.isChecked()), 160)
        self.tick = Tween(self, float(self.isChecked()), 240)
        self.hover = Tween(self)
        self.toggled.connect(self._toggled)

    def _toggled(self, on: bool):
        animate = self.isVisible()
        self.fill.to(float(on), animate=animate)
        # The tick draws in after the fill; unchecking simply takes it away.
        self.tick.to(float(on), 240 if on else 120, animate=animate)

    def label_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(13)
        return font

    def sizeHint(self):
        metrics = QFontMetrics(self.label_font())
        return QSize(18 + 8 + metrics.horizontalAdvance(self.text()) + 2, max(24, metrics.height() + 6))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def enterEvent(self, event):
        self.hover.to(1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0.0, 260)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.5)
        box = QRectF(1.5, (self.height() - 16) / 2, 16, 16)
        fill = max(0.0, min(1.0, self.fill.value))
        hover = self.hover.value if self.isEnabled() else 0.0
        edge = mix(QColor(self.BORDER), QColor(self.BORDER_HOVER), hover)
        accent = mix(QColor(ACCENT), QColor("#26336E"), hover)
        painter.setPen(QPen(mix(edge, accent, fill), 1))
        painter.setBrush(mix(QColor("#FFFCF7"), accent, fill))
        painter.drawRoundedRect(box, 4.5, 4.5)
        if self.tick.value > 0.01:
            # Drawn stroke by stroke as far as the animation has got.
            left = sum(math.dist(a, b) for a, b in pairwise(self.TICK)) * min(1.0, self.tick.value)
            path = QPainterPath(box.topLeft() + QPointF(*self.TICK[0]))
            for a, b in pairwise(self.TICK):
                length = math.dist(a, b)
                share = min(1.0, left / length)
                path.lineTo(box.topLeft() + QPointF(a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share))
                left -= length
                if left <= 0:
                    break
            pen = QPen(QColor("#FFFCF7"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        if self.hasFocus() and self.keyboard_focus:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.55)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(-3, -3, 3, 3), 7, 7)
        painter.setPen(QColor("#47413A"))
        painter.setFont(self.label_font())
        painter.drawText(
            QRectF(box.right() + 8, 0, self.width() - box.right() - 8, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


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


CUSTOM_HOTKEY = "custom"
SELECTION_TRIGGERS = ("icon", "direct", "hotkey")


class HotkeyRecorder(QDialog):
    """Captures a key combination for the recording shortcut."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义快捷键 · 好好说")
        self.setMinimumWidth(420)
        self.chosen = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)
        layout.addWidget(label("按下想用的快捷键", "cardTitle"))
        layout.addWidget(
            label(
                "例如 Ctrl+Alt+K、Win+Shift+H 或 F8，按 Esc 取消。"
                "想单独用右 Ctrl、右 Alt 或 CapsLock 录音，请直接在列表里选。",
                "muted",
                wrap=True,
            )
        )
        self.keys = label("等待按键…", "cardTitle")
        layout.addWidget(self.keys)
        self.problem = label("", "caption", wrap=True)
        layout.addWidget(self.problem)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = make_button("取消")
        cancel.clicked.connect(self.reject)
        self.use = make_button("使用这个快捷键", Glyph.CHECK, "primary")
        self.use.setEnabled(False)
        self.use.clicked.connect(self.accept)
        for button in (cancel, self.use):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space and Enter are keys to record here.
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def event(self, event):
        # Tab would otherwise move the focus before keyPressEvent sees it.
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        held = [
            name
            for name, flag in (
                ("Ctrl", Qt.KeyboardModifier.ControlModifier),
                ("Alt", Qt.KeyboardModifier.AltModifier),
                ("Shift", Qt.KeyboardModifier.ShiftModifier),
                ("Win", Qt.KeyboardModifier.MetaModifier),
            )
            if modifiers & flag
        ]
        key = event.key()
        if key == Qt.Key.Key_Escape and not held:
            self.reject()
            return
        pressed = {
            Qt.Key.Key_Control: "Ctrl", Qt.Key.Key_Alt: "Alt", Qt.Key.Key_AltGr: "Alt",
            Qt.Key.Key_Shift: "Shift", Qt.Key.Key_Meta: "Win",
        }.get(key)
        if pressed:
            # Qt flips a modifier's own bit on its key press, so add it explicitly.
            self.keys.setText(shortcut_name({*held, pressed}, "…"))
            return
        key_name = KEY_NAMES.get(event.nativeVirtualKey())
        name = shortcut_name(held, key_name) if key_name else "+".join(held + ["?"])
        problem = shortcut_problem(name) if key_name else "不支持这个键，请换一个。"
        self.keys.setText(name)
        self.problem.setText(problem or "可以使用。")
        self.chosen = "" if problem else name
        self.use.setEnabled(not problem)

    @classmethod
    def ask(cls, parent=None) -> str | None:
        dialog = cls(parent)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.chosen if accepted and dialog.chosen else None


class NavButton(QAbstractButton):
    """A page of the settings sidebar: a line icon and its name, set on a paper pill when chosen."""

    def __init__(self, text: str, glyph: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.glyph = glyph
        self.badge = False
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(text)
        self.setFixedHeight(38)
        self.keyboard_focus = False
        self.hover = Tween(self)
        self.chosen = Tween(self, 0.0, 220)
        self.toggled.connect(lambda on: self.chosen.to(float(on), animate=self.isVisible()))

    def sizeHint(self):
        return QSize(168, 38)

    def set_badge(self, on: bool):
        self.badge = on
        self.setAccessibleDescription("有新版本" if on else "")
        self.update()

    def enterEvent(self, event):
        self.hover.to(1.0, 140)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0.0, 260)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 1.5, -0.5, -1.5)
        chosen = max(0.0, min(1.0, self.chosen.value))
        painter.setPen(Qt.PenStyle.NoPen)
        if self.hover.value > 0 and chosen < 1:
            painter.setBrush(QColor(25, 36, 87, round(14 * self.hover.value * (1 - chosen))))
            painter.drawRoundedRect(box, 9, 9)
        if chosen > 0:
            painter.setBrush(QColor(29, 34, 64, round(14 * chosen)))
            painter.drawRoundedRect(box.adjusted(0, 1, 0, 1), 9, 9)
            sheet = QColor(SHEET)
            sheet.setAlphaF(chosen)
            painter.setBrush(sheet)
            painter.drawRoundedRect(box, 9, 9)
        color = mix(QColor("#5A5248"), QColor(ACCENT), chosen)
        draw_icon(painter, self.glyph, QRectF(12, (self.height() - 18) / 2, 18, 18), color)
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            QRectF(40, 0, self.width() - 64, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )
        if self.badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(RECORD))
            painter.drawEllipse(QRectF(self.width() - 20, self.height() / 2 - 3.5, 7, 7))
        if self.hasFocus() and self.keyboard_focus:
            ring = QColor(ACCENT)
            ring.setAlphaF(0.55)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 8, 8)


class SettingRow(QWidget):
    """A setting's name, an optional line about it, and its control on the right."""

    def __init__(self, title: str, hint: str = "", control: QWidget | QHBoxLayout | None = None):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(20)
        self.text = QVBoxLayout()
        self.text.setSpacing(2)
        layout.addLayout(self.text, 1)  # First, so what goes in belongs to the row at once.
        self.title = label(title, "rowTitle", wrap=True)
        self.text.addWidget(self.title)
        self.hint = label(hint, "rowHint", wrap=True)
        self.text.addWidget(self.hint)
        if not hint:
            # Hidden only once it is inside the row: showing a label that has no parent yet
            # opens it as a window of its own for a moment.
            self.hint.hide()
        if isinstance(control, QHBoxLayout):
            layout.addLayout(control)
        elif control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            if isinstance(control, QAbstractButton) and not control.text():
                control.setAccessibleName(title)
        self.setMinimumHeight(52)


class SettingsGroup(QWidget):
    """Rows on one card under a small grey heading, with hairlines between them."""

    def __init__(self, title: str = ""):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        if title:
            layout.addWidget(label(title, "settingsGroup"))
        self.card = QFrame()
        self.card.setObjectName("settingsCard")
        self.rows = QVBoxLayout(self.card)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        layout.addWidget(self.card)

    def add(self, widget: QWidget) -> QWidget:
        if self.rows.count():
            rule = QFrame()
            rule.setObjectName("settingsRule")
            self.rows.addWidget(rule)
        self.rows.addWidget(widget)
        return widget

    def row(self, title: str, hint: str = "", control: QWidget | QHBoxLayout | None = None) -> SettingRow:
        return self.add(SettingRow(title, hint, control))

    def block(self, *items: QWidget | QHBoxLayout, spacing: int = 10) -> QWidget:
        """Content as wide as the card, such as a text box or a list."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(spacing)
        for item in items:
            if isinstance(item, QHBoxLayout):
                layout.addLayout(item)
            else:
                layout.addWidget(item)
        return self.add(box)


def toggle(checked: bool) -> Switch:
    """A switch with no words of its own; its row names it."""
    switch = Switch()
    switch.setChecked(checked)
    switch.setFixedSize(46, 28)
    return switch


def settings_page(title: str, hint: str) -> tuple[QScrollArea, QVBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(32, 26, 28, 28)
    layout.setSpacing(20)
    head = QVBoxLayout()
    head.setSpacing(3)
    head.addWidget(label(title, "pageTitle"))
    head.addWidget(label(hint, "muted", wrap=True))
    layout.addLayout(head)
    area.setWidget(content)
    return area, layout


# The settings pages, in sidebar order: (key, name, icon).
SETTINGS_PAGES = (
    ("general", "通用", Glyph.SETTINGS),
    ("dictation", "听写", Glyph.MIC),
    ("overlay", "识别框", Glyph.OVERLAY),
    ("translate", "划词翻译", Glyph.TRANSLATE),
    ("words", "词库", Glyph.BOOK),
    ("api", "文字整理服务", Glyph.CLOUD),
    ("privacy", "隐私", Glyph.LOCK),
)


class SettingsDialog(QDialog):
    # A newer release found by 检查更新; the main window shows its 更新 button.
    release_found = Signal(object)
    # 更新到 … was clicked: the main window downloads the release and reports back here.
    update_requested = Signal()

    def __init__(self, settings: Settings, parent=None, page: str = "general"):
        super().__init__(parent)
        self.settings = settings
        self.updated = settings
        self.release = None
        # How far a newer release has got here: "" none, "offered", "downloading" or "ready".
        self.update_state = ""
        self.setWindowTitle("设置 · 好好说")
        self.resize(880, 660)
        self.setMinimumSize(780, 520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        side = QWidget()
        side.setObjectName("settingsSide")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        side.setFixedWidth(196)
        nav = QVBoxLayout(side)
        nav.setContentsMargins(14, 24, 14, 16)
        nav.setSpacing(2)
        heading = label("设置", "sideTitle")
        nav.addWidget(heading)
        nav.addSpacing(12)
        self.stack = QStackedWidget()
        self.nav_buttons: dict[str, NavButton] = {}
        self.pages: dict[str, QWidget] = {}
        for key, name, glyph in SETTINGS_PAGES:
            button = NavButton(name, glyph)
            button.clicked.connect(lambda _=False, key=key: self.show_page(key))
            nav.addWidget(button)
            self.nav_buttons[key] = button
        nav.addStretch()
        body.addWidget(side)
        body.addWidget(self.stack, 1)

        self.build_general()
        self.build_dictation()
        self.build_overlay()
        self.build_translate()
        self.build_words()
        self.build_api()
        self.build_privacy()

        footer, actions = footer_bar()
        actions.addStretch()
        cancel = make_button("取消")
        cancel.clicked.connect(self.reject)
        save = make_button("保存设置", Glyph.CHECK, "primary")
        save.setDefault(True)
        save.clicked.connect(self.save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        outer.addWidget(footer)
        self.show_page(page if page in self.pages else "general", animate=False)

    def add_page(self, key: str, area: QWidget):
        self.pages[key] = area
        self.stack.addWidget(area)

    def show_page(self, key: str, animate: bool = True):
        page = self.pages[key]
        self.nav_buttons[key].setChecked(True)
        if self.stack.currentWidget() is not page:
            self.stack.setCurrentWidget(page)
            if animate:
                fade_in(page.widget(), 180, 0.35)

    def current_page(self) -> str:
        return next(key for key, page in self.pages.items() if page is self.stack.currentWidget())

    # ---- 通用 ----

    def build_general(self):
        from .updates import changelog_page, shipped_notes

        area, layout = settings_page("通用", "版本、启动和提醒。")
        updates = SettingsGroup("软件更新")
        version = QWidget()
        line = QHBoxLayout(version)
        line.setContentsMargins(16, 16, 16, 14)
        line.setSpacing(14)
        mark = QLabel()
        mark.setPixmap(app_icon().pixmap(QSize(44, 44)))
        mark.setFixedSize(44, 44)
        line.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(label(f"好好说 {__version__}", "versionName"))
        summary = shipped_notes()[0]
        link = f'<a href="{changelog_page(__version__)}" style="color:#3C488E; text-decoration:none">更新日志</a>'
        about = QLabel(f"{html.escape(summary)}　{link}" if summary else link)
        about.setObjectName("rowHint")
        about.setTextFormat(Qt.TextFormat.RichText)
        about.setWordWrap(True)
        about.setOpenExternalLinks(True)
        text.addWidget(about)
        self.update_status = CheckStatus()
        self.update_status.hide()
        text.addWidget(self.update_status)
        line.addLayout(text, 1)
        self.check_update_button = check_button("检查更新", Glyph.REFRESH)
        self.check_update_button.clicked.connect(self.check_update)
        line.addWidget(self.check_update_button, 0, Qt.AlignmentFlag.AlignTop)
        updates.add(version)

        # A newer release: what it brings and the button that installs it, or how far the
        # download has got.
        self.update_news_box = QWidget()
        wrap = QVBoxLayout(self.update_news_box)
        wrap.setContentsMargins(16, 0, 16, 16)
        news = QFrame()
        news.setObjectName("updateNews")
        inner = QVBoxLayout(news)
        inner.setContentsMargins(16, 13, 16, 14)
        inner.setSpacing(7)
        self.news_title = QLabel()
        self.news_title.setObjectName("newsTitle")
        self.news_title.setTextFormat(Qt.TextFormat.RichText)
        inner.addWidget(self.news_title)
        self.news_text = label("", "newsText", wrap=True)
        inner.addWidget(self.news_text)
        self.news_progress = QProgressBar()
        self.news_progress.setRange(0, 100)
        self.news_progress.setTextVisible(False)
        self.news_progress.hide()
        inner.addWidget(self.news_progress)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.update_now = make_button("更新", None, "primary")
        self.update_now.clicked.connect(self.request_update)
        actions.addWidget(self.update_now)
        self.update_notes_button = make_button("看看新内容", None, "link")
        self.update_notes_button.clicked.connect(self.show_release_notes)
        actions.addWidget(self.update_notes_button)
        actions.addStretch()
        inner.addLayout(actions)
        wrap.addWidget(news)
        self.update_news_box.hide()
        updates.rows.addWidget(self.update_news_box)  # Part of the version block: no hairline above.
        self.check_updates = toggle(self.settings.check_updates)
        updates.row("每天自动检查新版本", "只读取版本信息，不发送任何数据", self.check_updates)
        layout.addWidget(updates)

        start = SettingsGroup("启动")
        self.startup = toggle(self.settings.start_on_login)
        start.row("开机自启动", "登录 Windows 后在托盘待命，不打开主窗口", self.startup)
        layout.addWidget(start)

        cards = SettingsGroup("提醒")
        self.achievement_popups = toggle(self.settings.achievement_popups)
        cards.row("达成成就时弹出小卡片", "在屏幕右下角停留几秒", self.achievement_popups)
        layout.addWidget(cards)
        layout.addStretch()
        self.add_page("general", area)

    # ---- 听写 ----

    def build_dictation(self):
        settings = self.settings
        area, layout = settings_page("听写", "全局快捷键随时可用，结果直接输入到原来的光标处。")
        recording = SettingsGroup("录音")
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
        self.mic.setFixedWidth(260)
        recording.row("麦克风", "", self.mic)
        self.hotkey = ComboBox()
        for name in RECORDING_CHOICES:
            self.hotkey.addItem(shortcut_label(name), name)
        if settings.hotkey not in RECORDING_CHOICES:
            self.hotkey.addItem(shortcut_label(settings.hotkey), settings.hotkey)
        self.hotkey.addItem("自定义…", CUSTOM_HOTKEY)
        self.hotkey.setCurrentIndex(max(0, self.hotkey.findData(settings.hotkey)))
        self.hotkey_choice = settings.hotkey
        self.hotkey.activated.connect(self.choose_hotkey)
        self.hotkey.setFixedWidth(200)
        recording.row("录音快捷键", "", self.hotkey)
        self.hold = Segmented(("按住说话", "轻按开始/结束"))
        self.hold.set_current(0 if settings.hold_to_talk else 1, animate=False)
        self.hold.changed.connect(self.update_usage_hint)
        self.usage_hint = recording.row("录音方式", " ", self.hold).hint
        self.update_usage_hint()
        layout.addWidget(recording)

        recognition = SettingsGroup("识别")
        self.gpu = toggle(settings.gpu_acceleration)
        recognition.row(
            "GPU 加速", "用显卡运行 Qwen3-ASR，识别更快。支持 NVIDIA、AMD、Intel 显卡，不支持时自动改用 CPU。", self.gpu
        )
        layout.addWidget(recognition)

        typing = SettingsGroup("输入")
        typing.row("输入方式", "直接输入单行文字，支持终端，不自动回车")
        self.keep_in_clipboard = toggle(settings.keep_in_clipboard)
        typing.row(
            "输入后把结果留在剪贴板",
            "适合游戏等打不开输入框的场合，之后按 Ctrl+V 粘贴。关闭时恢复原来的剪贴板。",
            self.keep_in_clipboard,
        )
        layout.addWidget(typing)

        words = SettingsGroup("文字")
        self.no_punctuation = toggle(settings.no_punctuation)
        words.row("去掉中文标点和语气词", "如逗号、句号、问号和“啊、呢、吧”", self.no_punctuation)
        self.no_english_punctuation = toggle(settings.no_english_punctuation)
        words.row("去掉英文标点", "3.5、10:30 和网址里的点、冒号会保留", self.no_english_punctuation)
        self.voice_translation = toggle(settings.voice_translation)
        words.row("语音翻译", "说完加一句“翻译成英文”，直接输入译文", self.voice_translation)
        layout.addWidget(words)
        layout.addStretch()
        self.add_page("dictation", area)

    # ---- 识别框 ----

    def build_overlay(self):
        from .overlay import OverlayPreview

        settings = self.settings
        area, layout = settings_page("识别框", "说话时屏幕上的小框。可以拖到不挡事的地方，位置会记住。")
        look = SettingsGroup("样式")
        self.overlay_style = Segmented(("标准", "极简"))
        self.overlay_style.set_current(1 if settings.overlay_style == "minimal" else 0, animate=False)
        self.overlay_style.changed.connect(self.overlay_look_changed)
        look.row("样式", "标准显示计时和提示；极简只留声波和转圈，要你处理的提示仍会展开成一行字。", self.overlay_style)
        self.overlay_preview = OverlayPreview()
        backdrop = QHBoxLayout()
        backdrop.addWidget(label("预览背景", "rowHint"))
        backdrop.addStretch()
        self.preview_backdrop = Segmented(("浅色", "深色"))
        self.preview_backdrop.set_current(0, animate=False)
        self.preview_backdrop.changed.connect(lambda index: self.overlay_preview.set_dark(index == 1))
        backdrop.addWidget(self.preview_backdrop)
        look.block(self.overlay_preview, backdrop)
        opacity = QHBoxLayout()
        opacity.setSpacing(10)
        self.overlay_opacity = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity.setRange(0, 100)
        self.overlay_opacity.setValue(settings.overlay_opacity)
        self.overlay_opacity.setFixedWidth(180)
        self.overlay_opacity.setAccessibleName("不透明度")
        self.overlay_opacity.valueChanged.connect(self.overlay_look_changed)
        opacity.addWidget(self.overlay_opacity)
        self.overlay_opacity_value = label("", "rowHint")
        self.overlay_opacity_value.setFixedWidth(36)
        self.overlay_opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        opacity.addWidget(self.overlay_opacity_value)
        look.row("不透明度", "只调底色。极简样式拉到 0 几乎完全透明；标准样式的文字需要底色，最低保留 60%。", opacity)
        layout.addWidget(look)

        place = SettingsGroup("位置")
        self.overlay_position = list(settings.overlay_position)
        self.placing = None
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.reset_overlay = make_button("恢复默认", None, "link", ACCENT)
        self.reset_overlay.clicked.connect(self.reset_overlay_position)
        buttons.addWidget(self.reset_overlay, 0, Qt.AlignmentFlag.AlignVCenter)
        self.place_overlay = check_button("在屏幕上调整…", Glyph.OVERLAY)
        self.place_overlay.clicked.connect(self.toggle_placing)
        buttons.addWidget(self.place_overlay, 0, Qt.AlignmentFlag.AlignVCenter)
        self.overlay_place_hint = place.row("位置", " ", buttons).hint
        layout.addWidget(place)
        layout.addStretch()
        self.overlay_look_changed()
        self.update_place_hint()
        self.add_page("overlay", area)

    def overlay_look(self) -> tuple[str, float]:
        return "minimal" if self.overlay_style.current() == 1 else "standard", self.overlay_opacity.value() / 100

    def overlay_look_changed(self, *_):
        look, opacity = self.overlay_look()
        self.overlay_opacity_value.setText(f"{self.overlay_opacity.value()}%")
        self.overlay_preview.set_look(look, opacity)
        if self.placing is not None:
            self.placing.set_look(look, opacity)

    def update_place_hint(self):
        if self.placing is not None:
            text = "拖动屏幕上的识别框，摆好后点“完成”。"
        elif self.overlay_position:
            text = "在你拖到的地方。录音时直接拖动识别框也能换位置。"
        else:
            text = "在屏幕底部正中。录音时直接拖动识别框就能换位置，也可以点右边在屏幕上摆好。"
        self.overlay_place_hint.setText(text)
        self.reset_overlay.setVisible(bool(self.overlay_position) or self.placing is not None)

    def toggle_placing(self):
        """Show the real overlay playing on the screen, to be dragged where it should go."""
        from .overlay import OverlayDemo, RecordingOverlay

        if self.placing is not None:
            self.overlay_position = list(self.placing.position)
            self.stop_placing()
            self.update_place_hint()
            return
        overlay = RecordingOverlay(self)  # Owned by the dialog, so it can be dragged while it is open.
        overlay.position = list(self.overlay_position)
        overlay.set_look(*self.overlay_look())
        self.placing = overlay
        self.placing_demo = OverlayDemo(overlay, vanish=False)
        self.placing_demo.start()
        self.place_overlay.setText("完成")
        self.update_place_hint()

    def stop_placing(self):
        if self.placing is None:
            return
        self.placing_demo.stop()
        self.placing.close()
        self.placing.deleteLater()
        self.placing = None
        self.place_overlay.setText("在屏幕上调整…")

    def reset_overlay_position(self):
        self.overlay_position = []
        if self.placing is not None:
            self.placing.position = []
            self.placing.place()
            self.placing._present()
        self.update_place_hint()

    def done(self, result: int):
        self.stop_placing()
        super().done(result)

    # ---- 划词翻译 ----

    def build_translate(self):
        settings = self.settings
        area, layout = settings_page(
            "划词翻译", "在其他程序里选中文字就能翻译，只有要翻译的文字会发到文字整理服务。"
        )
        switch = SettingsGroup()
        self.selection_on = toggle(settings.selection_translate)
        switch.row("开启划词翻译", "", self.selection_on)
        layout.addWidget(switch)
        self.selection_options = QWidget()
        options = QVBoxLayout(self.selection_options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(20)

        trigger = SettingsGroup("触发")
        self.selection_trigger = Segmented(("显示翻译按钮", "选中即翻译", "只用快捷键"))
        self.selection_trigger.set_current(SELECTION_TRIGGERS.index(settings.selection_trigger), animate=False)
        self.selection_trigger.changed.connect(self.update_selection_hint)
        self.selection_hint = trigger.row("触发方式", " ", self.selection_trigger).hint
        self.selection_hotkey = ComboBox()
        for name in SELECTION_CHOICES:
            self.selection_hotkey.addItem(shortcut_label(name), name)
        if settings.selection_hotkey not in SELECTION_CHOICES:
            self.selection_hotkey.addItem(shortcut_label(settings.selection_hotkey), settings.selection_hotkey)
        self.selection_hotkey.addItem("自定义…", CUSTOM_HOTKEY)
        self.selection_hotkey.setCurrentIndex(max(0, self.selection_hotkey.findData(settings.selection_hotkey)))
        self.selection_hotkey_choice = settings.selection_hotkey
        self.selection_hotkey.activated.connect(self.choose_selection_hotkey)
        self.selection_hotkey.setFixedWidth(200)
        self.selection_key_hint = trigger.row(
            "快捷键", selection_hint(settings.selection_hotkey), self.selection_hotkey
        ).hint
        options.addWidget(trigger)

        languages = SettingsGroup("语言")
        direction = QHBoxLayout()
        direction.setSpacing(8)
        self.selection_native = ComboBox()
        self.selection_foreign = ComboBox()
        for box, value in ((self.selection_native, settings.selection_native), (self.selection_foreign, settings.selection_foreign)):
            for name in TRANSLATION_LANGUAGES:
                box.addItem(name, name)
            box.setCurrentIndex(max(0, box.findData(value)))
            box.setFixedWidth(130)
        direction.addWidget(self.selection_native)
        self.selection_swap = make_button("", Glyph.SWAP, "heroChip")
        self.selection_swap.setFixedWidth(32)
        self.selection_swap.setToolTip("对调两种语言：其他语言的文字会译成左边那一种")
        self.selection_swap.clicked.connect(self.swap_selection_languages)
        direction.addWidget(self.selection_swap)
        direction.addWidget(self.selection_foreign)
        self.selection_back = languages.row("互译语言", " ", direction).hint
        self.selection_native.currentIndexChanged.connect(self.update_selection_hint)
        self.selection_foreign.currentIndexChanged.connect(self.update_selection_hint)
        self.selection_style = Segmented(tuple(name for name, _rule in TRANSLATION_STYLES.values()))
        self.selection_style.set_current(list(TRANSLATION_STYLES).index(settings.selection_style), animate=False)
        languages.row(
            "译文风格", "自然：地道通顺；正式：适合邮件和文档；直译：贴近原文。翻译弹窗里也能换。", self.selection_style
        )
        options.addWidget(languages)

        exceptions = SettingsGroup("例外")
        self.selection_excluded = QLineEdit(", ".join(settings.selection_excluded))
        self.selection_excluded.setPlaceholderText("如 WeChat.exe，用逗号分开")
        self.selection_excluded.setFixedWidth(260)
        exceptions.row(
            "不翻译的程序",
            "这些程序里不弹按钮，也不响应快捷键。密码框和常见的密码管理器从不读取。",
            self.selection_excluded,
        )
        options.addWidget(exceptions)
        layout.addWidget(self.selection_options)
        self.selection_on.toggled.connect(self.selection_options.setEnabled)
        self.selection_options.setEnabled(settings.selection_translate)
        self.update_selection_hint()
        layout.addStretch()
        self.add_page("translate", area)

    # ---- 词库 ----

    def build_words(self):
        area, layout = settings_page("词库", "让识别和整理认得你常用的人名、项目名和术语。")
        personal = SettingsGroup("个人词库")
        self.glossary = QPlainTextEdit("\n".join(self.settings.glossary))
        self.glossary.setFixedHeight(170)
        personal.block(
            label("每行一个，最多 100 个。整理时用来纠正写法，Qwen3-ASR 识别时也会参考。", "rowHint", wrap=True),
            self.glossary,
        )
        layout.addWidget(personal)

        lexicons = SettingsGroup("专业词库")
        # Edited here and saved with the other settings; files of unsaved imports are deleted afterwards.
        self.lexicons = [dict(item) for item in self.settings.lexicons]
        self.imported_ids: set[str] = set()
        self.lexicon_rows = QVBoxLayout()
        self.lexicon_rows.setSpacing(6)
        list_box = QWidget()
        list_box.setLayout(self.lexicon_rows)
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)
        self.import_button = make_button("导入词库…", Glyph.IMPORT, "check", ACCENT)
        self.import_button.clicked.connect(self.import_lexicon)
        actions_row.addWidget(self.import_button, 0, Qt.AlignmentFlag.AlignTop)
        self.lexicon_status = label("支持 txt、csv、json、搜狗 .scel、Rime .dict.yaml", "rowHint", wrap=True)
        actions_row.addWidget(self.lexicon_status, 1)
        lexicons.block(
            label(
                "导入输入法等工具的词库，在本机按读音比对，读音相近的词才用于识别和整理。"
                "两个字的中文词不参与比对，请加到个人词库。",
                "rowHint",
                wrap=True,
            ),
            list_box,
            actions_row,
        )
        layout.addWidget(lexicons)
        self.fill_lexicons()
        layout.addStretch()
        self.add_page("words", area)

    # ---- 文字整理服务 ----

    def build_api(self):
        settings = self.settings
        area, layout = settings_page("文字整理服务", "录音在本机识别，只有文字会发到这里的 API 整理。")
        service = SettingsGroup("连接")
        self.base_url = QLineEdit(settings.api_base_url)
        self.base_url.setPlaceholderText("例如 https://api.example.com/v1")
        self.base_url.setToolTip("填写兼容 Chat Completions 的 API 地址，支持 Base URL 或完整请求地址。")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.update_key_hint()
        self.base_url.textChanged.connect(self.api_address_changed)
        self.model = QLineEdit(settings.model)
        self.model.setPlaceholderText("填写服务商提供的模型名称")
        for field in (self.base_url, self.key, self.model):
            field.setFixedWidth(340)
        service.row("API 地址", "支持 Base URL 或完整请求地址", self.base_url)
        service.row("API Key", "", self.key)
        service.row("模型名称", "", self.model)
        self.timeout = SpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(int(settings.api_timeout))
        self.timeout.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.timeout.setFixedWidth(120)
        service.row("请求超时", "", self.timeout)
        self.test_api = check_button("测试连接", Glyph.CONNECT)
        self.test_api.clicked.connect(self.check_connection)
        test = service.row("连接检查", "用一句话试试地址、密钥和模型能不能用", self.test_api)
        self.api_status = CheckStatus()
        self.api_status.hide()
        test.text.addWidget(self.api_status)
        layout.addWidget(service)
        note = QHBoxLayout()
        note.setSpacing(6)
        note.setContentsMargins(2, 0, 0, 0)
        note.addWidget(glyph_label(Glyph.LOCK, MUTED, 14))
        note.addWidget(label("密钥按 API 地址分别保存在 Windows 凭据管理器，不写进设置文件。", "caption"), 1)
        layout.addLayout(note)
        layout.addStretch()
        self.add_page("api", area)

    # ---- 隐私 ----

    def build_privacy(self):
        area, layout = settings_page("隐私", "录音只在内存中处理，识别后立即释放，不保存录音文件。")
        data = SettingsGroup()
        self.history = toggle(self.settings.save_history)
        data.row("保留最近 5 条文字", "本机加密，最长 7 天", self.history)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.clear_stats = make_button("清空统计", None, "link", ACCENT)
        self.clear_stats.clicked.connect(self.confirm_clear_stats)
        stats.addWidget(self.clear_stats, 0, Qt.AlignmentFlag.AlignVCenter)
        self.collect_stats = toggle(self.settings.collect_stats)
        stats.addWidget(self.collect_stats, 0, Qt.AlignmentFlag.AlignVCenter)
        self.collect_stats.setAccessibleName("记录使用统计")
        data.row("记录使用统计", "只记字数、次数和 tokens，不记内容", stats)
        self.stats_cleared = False
        layout.addWidget(data)
        layout.addStretch()
        self.add_page("privacy", area)

    # ---- 软件更新 ----

    def check_update(self):
        from .checks import CheckResult
        from .updates import UpdateError, fetch_release, is_newer

        self.check_update_button.setEnabled(False)
        self.update_status.show()
        self.update_status.running("正在检查…")

        def work(_report):
            try:
                return fetch_release()
            except UpdateError as exc:
                return CheckResult(False, str(exc))

        def finished(result):
            self.check_update_button.setEnabled(True)
            if isinstance(result, CheckResult):
                self.update_status.finish(False, result.message)
            elif is_newer(result.version):
                self.update_status.finish(True, f"有新版本 {result.version}")
                if self.update_state in ("", "offered"):
                    self.show_release(result)
                self.release_found.emit(result)
            else:
                self.update_status.finish(True, f"已是最新版本（{__version__}）")

        run_task(self, work, finished)

    def show_release(self, release):
        """A newer release is out: say what it brings and offer to install it."""
        from .announcement import release_date

        self.release = release
        self.update_state = "offered"
        meta = " · ".join(part for part in (release_date(release.date), format_bytes(release.size) if release.size else "") if part)
        self.news_title.setText(
            f"有新版本 {html.escape(release.version)}"
            + (f'<span style="color:#776F63; font-weight:400; font-size:12px">　{meta}</span>' if meta else "")
        )
        self.news_text.setText(release.notes)
        self.news_progress.hide()
        self.update_now.setText(f"更新到 {release.version}")
        self.update_now.setEnabled(True)
        self.update_now.show()
        self.update_notes_button.show()
        self.update_news_box.show()
        self.nav_buttons["general"].set_badge(True)

    def show_download(self, percent: int):
        if self.release is None:
            return
        self.news_title.setText(
            f"正在下载 {html.escape(self.release.version)}"
            f'<span style="color:#776F63; font-weight:400; font-size:12px">　{percent}%</span>'
        )
        self.update_state = "downloading"
        self.news_text.setText("可以继续改设置。下载好后，关闭设置就会安装。")
        self.news_progress.setValue(percent)
        self.news_progress.show()
        self.update_now.hide()
        self.update_notes_button.hide()
        self.update_news_box.show()

    def show_downloaded(self):
        if self.release is None:
            return
        self.update_state = "ready"
        self.news_title.setText(f'<span style="color:#3F7A53">✓</span> {html.escape(self.release.version)} 已下载好')
        self.news_text.setText("关闭设置后自动安装，几秒后好好说会重新打开。")
        self.news_progress.hide()
        self.update_now.hide()
        self.update_notes_button.hide()
        self.update_news_box.show()

    def show_update_failed(self, message: str):
        if self.release is None:
            return
        self.show_release(self.release)
        self.news_text.setText(message)
        self.update_now.setText("重试")

    def request_update(self):
        self.update_now.setEnabled(False)
        self.update_requested.emit()
        self.update_now.setEnabled(True)  # Still shown only when nothing started, as from a build copy.

    def show_release_notes(self):
        from .announcement import AnnouncementDialog
        from .updates import changelog_page, installed_here

        release = self.release
        if release is None:
            return
        dialog = AnnouncementDialog(
            release.version,
            release.notes,
            release.highlights,
            changelog=changelog_page(release.version),
            date=release.date,
            size=release.size,
            installable=installed_here(),
            parent=self,
        )
        dialog.exec()
        if dialog.choice == "update":
            self.request_update()

    def reject(self):
        # Closing installs a downloaded update; unsaved changes would be lost with the restart.
        if self.update_state == "ready" and self.has_changes():
            answer = QMessageBox.question(
                self,
                "设置还没保存",
                "关闭设置后会安装更新并重新打开好好说。先保存刚才改的设置吗？",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Save:
                self.save()
                return
            if answer == QMessageBox.StandardButton.Cancel:
                return
        super().reject()

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
        self.api_status.show()
        self.api_status.running("正在连接…")

        def finished(result):
            self.test_api.setEnabled(True)
            self.api_status.finish(result.ok, result.message)

        run_task(self, lambda _report: check_api(base_url, key, model, timeout), finished)

    def choose_hotkey(self, index: int):
        name = self.hotkey.itemData(index)
        if name == CUSTOM_HOTKEY:
            name = HotkeyRecorder.ask(self)
            if not name:
                self.hotkey.setCurrentIndex(self.hotkey.findData(self.hotkey_choice))
                return
            if self.hotkey.findData(name) < 0:
                self.hotkey.insertItem(self.hotkey.count() - 1, shortcut_label(name), name)
            self.hotkey.setCurrentIndex(self.hotkey.findData(name))
        self.hotkey_choice = name
        self.update_usage_hint()

    def choose_selection_hotkey(self, index: int):
        name = self.selection_hotkey.itemData(index)
        if name == CUSTOM_HOTKEY:
            name = HotkeyRecorder.ask(self)
            if not name:
                self.selection_hotkey.setCurrentIndex(self.selection_hotkey.findData(self.selection_hotkey_choice))
                return
            if self.selection_hotkey.findData(name) < 0:
                self.selection_hotkey.insertItem(self.selection_hotkey.count() - 1, shortcut_label(name), name)
            self.selection_hotkey.setCurrentIndex(self.selection_hotkey.findData(name))
        self.selection_hotkey_choice = name
        self.selection_key_hint.setText(selection_hint(name))

    def update_selection_hint(self, *_):
        hints = (
            "用鼠标选中文字后，旁边出现一个小按钮，点一下才翻译。",
            "用鼠标选中文字后马上翻译。每次选中都会把文字发送到 API。",
            "不留意鼠标，只在按快捷键时翻译选中的文字。",
        )
        self.selection_hint.setText(hints[max(0, self.selection_trigger.current())])
        native, foreign = self.selection_native.currentData(), self.selection_foreign.currentData()
        if native == foreign:
            self.selection_back.setText("两种语言相同，请换一种。")
        elif TRANSLATION_SCRIPTS.get(native) == TRANSLATION_SCRIPTS.get(foreign):
            self.selection_back.setText(f"{native}和{foreign}写法相同，分不出选中的是哪种，请把其中一种换成别的语言。")
        else:
            self.selection_back.setText(
                f"选中{native}译成{foreign}，选中{foreign}译成{native}；其他语言都译成{native}。弹窗里可以临时换一种。"
            )

    def swap_selection_languages(self):
        native, foreign = self.selection_native.currentIndex(), self.selection_foreign.currentIndex()
        self.selection_native.setCurrentIndex(foreign)
        self.selection_foreign.setCurrentIndex(native)

    def update_usage_hint(self, *_):
        self.usage_hint.setText(usage_hint(self.hotkey_choice, self.hold.current() == 0))

    def fill_lexicons(self):
        while self.lexicon_rows.count():
            widget = self.lexicon_rows.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        if not self.lexicons:
            self.lexicon_rows.addWidget(label("还没有导入专业词库。", "muted"))
        for lexicon in self.lexicons:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            switch = Switch(f"{lexicon['name']} · {lexicon['count']:,} 个词")
            switch.setChecked(lexicon["enabled"])
            switch.toggled.connect(lambda on, item=lexicon: item.update(enabled=on))
            line.addWidget(switch, 1)
            remove = make_button("删除", Glyph.DELETE, "danger")
            remove.clicked.connect(lambda _=False, item=lexicon: self.remove_lexicon(item))
            line.addWidget(remove)
            self.lexicon_rows.addWidget(row)

    def import_lexicon(self):
        from .checks import CheckResult

        path, _ = QFileDialog.getOpenFileName(self, "导入词库", "", FILE_FILTER)
        if not path:
            return
        self.import_button.setEnabled(False)
        self.lexicon_status.setText("正在读取词库…")

        def work(_report):
            try:
                return import_lexicon(Path(path))
            except LexiconError as exc:
                return CheckResult(False, str(exc))
            except Exception:  # noqa: BLE001 - unreadable or malformed files of any kind
                return CheckResult(False, "无法读取这个文件，请确认是支持的词库格式。")

        def finished(result):
            self.import_button.setEnabled(True)
            if isinstance(result, CheckResult):
                self.lexicon_status.setText("")
                QMessageBox.warning(self, "无法导入词库", result.message)
                return
            entry, parsed = result
            self.lexicons.append(entry)
            self.imported_ids.add(entry["id"])
            self.fill_lexicons()
            more = "，超出 20 万词的部分未导入" if parsed.truncated else ""
            self.lexicon_status.setText(f"已导入“{entry['name']}”，共 {entry['count']:,} 个词{more}。保存设置后生效。")

        run_task(self, work, finished)

    def remove_lexicon(self, lexicon: dict):
        self.lexicons = [item for item in self.lexicons if item["id"] != lexicon["id"]]
        self.fill_lexicons()
        self.lexicon_status.setText(f"已移除“{lexicon['name']}”，保存设置后生效。")

    def pending(self) -> Settings:
        """The settings as the dialog now shows them, before they are checked."""
        words = list(dict.fromkeys(x.strip() for x in self.glossary.toPlainText().splitlines() if x.strip()))
        excluded = []
        for name in re.split(r"[,，;；\s]+", self.selection_excluded.text()):
            name = name.strip().lower()
            if name:
                excluded.append(name if name.endswith(".exe") else name + ".exe")
        return replace(
            self.settings,
            model=self.model.text().strip(),
            api_base_url=self.base_url.text().strip().rstrip("/"),
            api_timeout=float(self.timeout.value()),
            microphone=self.mic.currentData(),
            hotkey=self.hotkey_choice,
            hold_to_talk=self.hold.current() == 0,
            save_history=self.history.isChecked(),
            collect_stats=self.collect_stats.isChecked(),
            achievement_popups=self.achievement_popups.isChecked(),
            start_on_login=self.startup.isChecked(),
            glossary=words,
            lexicons=[dict(item) for item in self.lexicons],
            voice_translation=self.voice_translation.isChecked(),
            no_punctuation=self.no_punctuation.isChecked(),
            no_english_punctuation=self.no_english_punctuation.isChecked(),
            keep_in_clipboard=self.keep_in_clipboard.isChecked(),
            gpu_acceleration=self.gpu.isChecked(),
            check_updates=self.check_updates.isChecked(),
            selection_translate=self.selection_on.isChecked(),
            selection_trigger=SELECTION_TRIGGERS[self.selection_trigger.current()],
            selection_hotkey=self.selection_hotkey_choice,
            selection_native=self.selection_native.currentData(),
            selection_foreign=self.selection_foreign.currentData(),
            selection_style=list(TRANSLATION_STYLES)[self.selection_style.current()],
            selection_excluded=list(dict.fromkeys(excluded))[:100],
            overlay_style=self.overlay_look()[0],
            overlay_opacity=self.overlay_opacity.value(),
            overlay_position=list(self.placing.position if self.placing is not None else self.overlay_position),
        )

    def has_changes(self) -> bool:
        return bool(self.key.text().strip()) or self.pending() != self.settings

    def warn(self, page: str, title: str, text: str):
        """A problem with a setting, shown on the page that holds it."""
        self.show_page(page)
        QMessageBox.warning(self, title, text)

    def save(self):
        new = self.pending()
        if len(new.glossary) > 100 or any(len(word) > 80 for word in new.glossary):
            self.warn("words", "检查词库", "最多添加 100 个词，每个词不超过 80 字。")
            return
        if not new.model:
            self.warn("api", "检查模型", "请填写 API 服务商提供的模型名称。")
            return
        base_url = new.api_base_url
        try:
            endpoint = chat_completion_url(base_url)
        except ValueError as exc:
            self.warn("api", "检查 API 地址", str(exc))
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
            self.warn("api", "检查 API Key", "已更换 API 地址，请填写该服务对应的密钥。")
            return
        if new.selection_translate and new.selection_hotkey == new.hotkey:
            self.warn("translate", "检查快捷键", "划词翻译的快捷键和录音快捷键相同，请换一个。")
            return
        native, foreign = new.selection_native, new.selection_foreign
        if native == foreign:
            self.warn("translate", "检查互译语言", "两种语言相同，请换一种。")
            return
        if new.selection_translate and TRANSLATION_SCRIPTS.get(native) == TRANSLATION_SCRIPTS.get(foreign):
            self.warn(
                "translate", "检查互译语言",
                f"{native}和{foreign}写法相同，程序分不出选中的是哪一种，没法自动互译。请把其中一种换成别的语言。",
            )
            return
        try:
            if self.key.text().strip():
                save_key(self.key.text(), api_base_url=base_url)
        except Exception:
            self.warn("api", "保存失败", "无法保存密钥到 Windows 凭据管理器，请检查系统权限。")
            return
        self.updated = new
        self.accept()

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


def progress_text(verb: str, percent: int) -> str:
    check = "复制时逐个校验" if verb == "导入" else "下载后自动校验完整性"
    return f"正在{verb} {percent}%  ·  {check}"


def delete_model_files(model: AsrModel, directory: Path) -> None:
    discard_partial(model, directory)
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

    def update_state(
        self, *, active: bool, ready: bool, directory: Path, downloading: str | None, percent: int, verb: str = "下载"
    ):
        mine = downloading == self.model.id
        self.check_row.setVisible(ready and not mine)
        self.setProperty("active", active and ready)
        repolish(self)
        self.active_tag.setVisible(active and ready)
        self.progress.setVisible(mine)
        self.progress.setValue(percent if mine else 0)
        self.cancel_button.setVisible(mine)
        self.cancel_button.setText(f"取消{verb}")
        self.delete_button.setVisible(ready and not mine)
        self.action.setVisible(not mine)
        self.action.set_icon(None)
        if mine:
            self.state.setText(progress_text(verb, percent))
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
            self.action.setToolTip(f"请等待当前{verb}完成" if downloading else "")


class ModelDialog(QDialog):
    """Choose, download and switch local speech models."""

    changed = Signal(object)

    def __init__(self, settings: Settings, downloads, apply, parent=None, release=None):
        super().__init__(parent)
        self.settings = settings
        self.downloads = downloads
        self.apply = apply
        # Unloads the model in use before its files are deleted; returns False while it is recognizing.
        self.release = release
        self.setWindowTitle("语音模型 · 好好说")
        available = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(720, max(560, min(860, available - 60)))
        self.setMinimumSize(600, 520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(
            dialog_header(
                "语音模型", "全部在本机识别，录音不会上传。可以随时下载、导入或切换，正在用的模型不会被自动替换。"
            )
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
        self.open_location.setToolTip("从网盘下载的模型文件夹放进这里，也能直接使用")
        self.open_location.clicked.connect(self.open_folder)
        row.addWidget(self.open_location)
        self.import_button = make_button("导入…", Glyph.IMPORT, "toolbar")
        self.import_button.setToolTip("从网盘等处下载的模型包导入，不用联网下载")
        self.import_button.clicked.connect(self.import_models)
        row.addWidget(self.import_button)
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
        if self.downloads.start(model.id, self.settings.model_path(model.id), self.other_folders(model)):
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
            card = self.cards[model.id]
            shown = category == "全部" or model.category == category
            if shown and card.isHidden() and self.isVisible():
                fade_in(card, 260)
            card.setVisible(shown)

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
        busy, verb = self.downloads.active, self.downloads.verb
        self.change_location.setEnabled(busy is None)
        self.change_location.setToolTip(f"{verb}进行中，完成后再更改位置" if busy else "")
        self.import_button.setEnabled(busy is None)
        for model in ALL_MODELS:
            directory = self.settings.model_path(model.id)
            self.cards[model.id].update_state(
                active=model.id == self.settings.asr_model,
                ready=models_ready(directory, model=model),
                directory=directory,
                downloading=busy,
                percent=self.downloads.percent,
                verb=verb,
            )

    def changeEvent(self, event):
        # Back from Explorer, where a model folder may have just been copied in.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.find_dropped_models()
        super().changeEvent(event)

    def find_dropped_models(self):
        """Use model folders copied into the download location by hand, under any folder name."""
        found = self.settings.with_found_models()
        # No warning when saving fails: closing it would activate the window and land here again.
        if found == self.settings or not self.apply(found):
            return
        names = "、".join(
            get_model(model_id).name
            for model_id, path in found.model_paths.items()
            if self.settings.model_paths.get(model_id) != path
        )
        switched = found.asr_model != self.settings.asr_model
        self.settings = found
        self.changed.emit(found)
        self.message.setText(f"在下载位置找到 {names}，" + ("已设为当前模型。" if switched else "可以直接使用。"))
        self.refresh()

    def other_folders(self, model: AsrModel) -> tuple[Path, ...]:
        """Other models' folders, which may already hold a file this model shares, such as the VAD."""
        return tuple(dict.fromkeys(self.settings.model_path(other.id) for other in ALL_MODELS if other is not model))

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
        if self.downloads.start(model.id, directory, self.other_folders(model)):
            self.message.setText(f"开始下载 {model.name}，可以关闭此窗口，下载会在后台继续。")
        self.refresh()

    def import_models(self):
        """Copy the models in an offline pack (for example one shared on a cloud drive) into place."""
        if self.downloads.active:
            return
        chosen = QFileDialog.getExistingDirectory(self, "选择模型包文件夹", str(Path.home() / "Downloads"))
        if not chosen:
            return
        found = find_models(Path(chosen))
        if not found:
            QMessageBox.information(
                self,
                "没有找到模型",
                "这个文件夹里没有完整的模型文件。\n请选择下载好的模型包文件夹，或其中某个模型的文件夹。",
            )
            return
        jobs = [
            (model_id, self.settings.model_path(model_id), (source,))
            for model_id, source in found.items()
            if not models_ready(self.settings.model_path(model_id), model=get_model(model_id))
        ]
        if not jobs:
            names = "、".join(get_model(model_id).name for model_id in found)
            self.message.setText(f"{names} 已在本机，不需要导入。")
            return
        need = sum(get_model(model_id).size for model_id, _, _ in jobs)
        space = free_space(self.settings.download_root)
        if space is not None and space < need * 1.05:
            QMessageBox.warning(
                self,
                "磁盘空间不足",
                f"导入需要约 {format_bytes(need)}，下载位置只剩 {format_bytes(space)}。\n请更改下载位置后重试。",
            )
            return
        if self.downloads.start_many(jobs, "导入"):
            names = "、".join(get_model(model_id).name for model_id, _, _ in jobs)
            self.message.setText(f"正在导入 {names}，可以关闭此窗口，导入会在后台继续。")
        self.refresh()

    def delete_model(self, model: AsrModel):
        directory = self.settings.model_path(model.id)
        active = model.id == self.settings.asr_model
        note = "这是正在使用的模型，删除后要先换用其他模型或重新下载，才能继续录音。\n\n" if active else ""
        answer = QMessageBox.question(
            self,
            "删除模型",
            f"删除 {model.name} 的模型文件（{model.size_label}）？\n{directory}\n\n{note}之后需要时可以重新下载。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        # The model in use may still be loaded; Qwen3-ASR's engine keeps its files open.
        if active and self.release and not self.release():
            QMessageBox.warning(self, "未能删除", "模型正在识别，请稍后重试。")
            return
        try:
            delete_model_files(model, directory)
        except OSError:
            QMessageBox.warning(self, "未能删除", "模型文件正在使用或没有权限，请稍后重试。")
            self.refresh()
            return
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
        if self.commit(self.settings.with_download_root(directory).with_found_models()):
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
            item.state.setText(progress_text(self.downloads.verb, percent))

    def on_finished(self, model_id: str):
        name, verb = get_model(model_id).name, self.downloads.verb
        if model_id == self.settings.asr_model:  # The main window switched to it, as nothing else was usable.
            self.message.setText(f"{name} {verb}完成，已设为当前模型。")
        else:
            self.message.setText(f"{name} {verb}完成，点击“使用此模型”即可切换。")
        self.refresh()

    def on_failed(self, model_id: str, message: str):
        self.message.setText(message or f"已取消{self.downloads.verb}。")
        self.refresh()
