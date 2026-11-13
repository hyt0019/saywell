"""The recording overlay: the small window that says dictation is listening, working or done.

Two looks share the window. "standard" is a dark card with words and a coloured voice level.
"minimal" is a small paper pill: ink lines rising from a flat base while listening, a spinner while
the words are worked on and a tick once they are typed; only a message the user has to act on
opens it into a short card with words. Either can be dragged anywhere and stays there. The
opacity is the background's: lines, spinner and words stay solid, and over a nearly clear
background the ink gets a faint light edge so it still reads on dark windows."""

from __future__ import annotations

import math
import time
from collections import deque
from itertools import pairwise

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSizeF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import is_dark, tone
from .ui import VOICE, LevelBars, Tween, fade_in, mix, spring

STYLES = ("standard", "minimal")
BOTTOM = 14  # Room for the shadow below the box. The middle of the box's bottom edge is its anchor.
SIZES = {"standard": (380, 104), "minimal": (440, 84)}
LIFT = 28  # Default gap between the bottom of the screen's work area and the window.
TONES = {"rec": "#EC7360", "busy": "#E0A040", "done": "#66AE7C", "warn": "#E0A040"}
# The minimal look follows the theme: ink on paper, or paper on the night's ink. The standard
# card is dark in both.


def ink() -> QColor:
    return QColor("#E9E4D8" if is_dark() else "#1D2240")


def muted() -> QColor:
    return QColor("#A39D92" if is_dark() else "#776F63")


def sheet(alpha: int = 255) -> QColor:
    return QColor(27, 31, 53, alpha) if is_dark() else QColor(255, 252, 247, alpha)


PILL = QSizeF(112, 36)  # Listening.
ROUND = QSizeF(36, 36)  # Working, or typed.
BARS = 13
STANDARD_FLOOR = 0.6  # The standard card's light words need a dark background.


def clamp_anchor(point: QPoint, area: QRect) -> QPoint:
    """Keep a dragged box on the screen, with its bottom edge inside the work area."""
    return QPoint(
        min(max(point.x(), area.left() + 70), area.right() - 70),
        min(max(point.y(), area.top() + 70), area.bottom() - 4),
    )


class LevelTrail(QObject):
    """The last few voice levels, newest last, falling back between samples as LevelBars does."""

    def __init__(self, owner: QWidget, count: int = BARS):
        super().__init__(owner)
        self.owner = owner
        self.levels: deque[float] = deque([0.0] * count, maxlen=count)
        self.target = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(45)
        self.timer.timeout.connect(self._advance)

    def set_value(self, value: int):
        value = max(0, min(100, int(value)))
        if value == 0:
            self.target = 0.0
            if not any(self.levels):
                return
        # Speech RMS is compressed; a square root keeps quiet talkers visible.
        self.target = max(self.target, math.sqrt(value / 100))
        if not self.timer.isActive():
            self.timer.start()

    def reset(self):
        self.timer.stop()
        self.target = 0.0
        self.levels.extend([0.0] * self.levels.maxlen)

    def _advance(self):
        self.levels.append(self.target)
        self.target *= 0.55
        if self.target < 0.02:
            self.target = 0.0
            if not any(level > 0.01 for level in self.levels):
                self.reset()
        self.owner.update()


class RecordingOverlay(QWidget):
    # The new anchor (a QPoint on the desktop) after the box was dragged somewhere else.
    moved = Signal(object)

    def __init__(self, parent: QWidget | None = None, embedded: bool = False):
        """A window of its own, or with embedded a plain child widget, as in the settings' preview."""
        flags = (
            Qt.WindowType.Widget
            if embedded
            else Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(parent, flags)
        self.embedded = embedded
        if not embedded:
            # Clicking it never takes the focus from the text box the words go to.
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.fade = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self.fade)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.look = "standard"
        self.opacity = 1.0
        self.position: list[int] = []  # Where it was dragged to; [] for the bottom middle.
        self.fixed_anchor: QPoint | None = None  # Set by the preview, which places it itself.
        self.home: tuple[int, int] | None = None
        self.press: QPoint | None = None
        self.drag_from: QPoint | None = None
        self.showing_message = False
        self.tone = "rec"
        self.color = QColor(TONES["rec"])
        self.previous = QColor(self.color)
        self.phase = 0.0
        self.kind = "rec"  # What the minimal pill shows: rec, busy, done or message.
        self.message = ("", "")
        self.box_from = self.box_to = QSizeF(PILL)
        # Rises a little into place when it appears and sinks away when dismissed.
        self.presence = Tween(self, on_change=self._present)
        self.shade = Tween(self, 1.0, 320)
        self.morph = Tween(self, 1.0, 420, lambda t: spring(t, 0.82))
        self.content = Tween(self, 1.0, 240)
        self.tick = Tween(self, 1.0, 320)
        self.trail = LevelTrail(self)
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self.dismiss)
        self.pulse = QVariantAnimation(self)
        self.pulse.setStartValue(0.0)
        self.pulse.setEndValue(1.0)
        self.pulse.setDuration(1200)
        self.pulse.setLoopCount(-1)
        self.pulse.valueChanged.connect(self._pulse)

        # The standard card's words and level; the minimal pill draws everything itself.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.words = QWidget()
        outer.addWidget(self.words)
        layout = QHBoxLayout(self.words)
        layout.setContentsMargins(52, 22, 26, 22)
        layout.setSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.label = QLabel("正在听  00:00")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setStyleSheet("color: #FFFCF7; font-size: 15px; font-weight: 700; background: transparent;")
        self.hint = QLabel("松开快捷键结束，Esc 取消")
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setStyleSheet("color: #9D9487; font-size: 12px; background: transparent;")
        text.addWidget(self.label)
        text.addWidget(self.hint)
        layout.addLayout(text, 1)
        self.level = LevelBars(14, VOICE, "#534C43", themed=False)  # On the dark card in both themes.
        self.level.setFixedWidth(104)
        layout.addWidget(self.level)
        self.setFixedSize(*SIZES["standard"])

    # ---- look and place ----

    def apply(self, settings):
        self.position = list(settings.overlay_position)
        self.set_look(settings.overlay_style, settings.overlay_opacity / 100)

    def set_look(self, look: str, opacity: float):
        look = look if look in STYLES else "standard"
        self.opacity = max(0.0, min(1.0, opacity))
        if look != self.look:
            anchor = self.anchor() if self.home else None
            self.look = look
            self.words.setVisible(look == "standard")
            self.setFixedSize(*SIZES[look])
            self.box_from = self.box_to = self.box_size(self.kind)
            if anchor is not None:
                self.home = self.home_for(anchor)
                self._present()
        self.update()

    def anchor(self) -> QPoint:
        if self.home is None:
            self.place()
        return QPoint(self.home[0] + self.width() // 2, self.home[1] + self.height() - BOTTOM)

    def home_for(self, anchor: QPoint) -> tuple[int, int]:
        return anchor.x() - self.width() // 2, anchor.y() - self.height() + BOTTOM

    def default_anchor(self) -> QPoint:
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        return QPoint(area.center().x(), area.bottom() - LIFT - BOTTOM)

    def place(self):
        """Where it shows: the preview's spot, where it was dragged to if that is still on a
        screen, or the bottom middle of the screen."""
        if self.fixed_anchor is not None:
            self.home = self.home_for(self.fixed_anchor)
            return
        anchor = None
        if len(self.position) == 2:
            point = QPoint(*self.position)
            screen = QGuiApplication.screenAt(point)
            if screen is not None:
                anchor = clamp_anchor(point, screen.availableGeometry())
        self.home = self.home_for(anchor or self.default_anchor())

    # ---- what it says ----

    def listen(self, hint: str):
        self.showing_message = False
        self.label.setText("正在听  00:00")
        self.hint.setText(hint)
        self.trail.reset()
        self.level.setValue(0)
        self.level.show()
        self.set_tone("rec")
        self.set_kind("rec")
        self.show_near_bottom()

    def set_elapsed(self, seconds: int):
        self.label.setText(f"正在听  {seconds // 60:02}:{seconds % 60:02}")

    def set_level(self, value: int):
        if self.look == "minimal":
            self.trail.set_value(value)
        else:
            self.level.setValue(value)

    def working(self, text: str, hint: str):
        self.showing_message = False
        self.label.setText(text)
        self.hint.setText(hint)
        self.set_tone("busy")
        self.set_kind("busy")

    def set_stage(self, text: str):
        self.label.setText(text)

    def show_message(self, title: str, hint: str, milliseconds: int = 4500, tone: str = "done", brief: bool = False):
        """A result or a problem. A brief one (the words were typed) is only a tick on the
        minimal pill; anything to act on opens it into a card with the words."""
        changed = self.isVisible() and self.label.text() != title
        self.label.setText(title)
        self.hint.setText(hint)
        self.level.hide()
        self.message = (title, hint)
        self.showing_message = True
        self.set_tone(tone)
        self.set_kind("done" if brief else "message")
        self.show_near_bottom()
        if changed and self.look == "standard":
            fade_in(self.label, 260, 0.15)
            fade_in(self.hint, 320, 0.0)
        self.dismiss_timer.start(milliseconds)

    def set_tone(self, tone: str):
        tone = tone if tone in TONES else "rec"
        if tone != self.tone:
            # The status dot shifts hue (red → orange → green) instead of swapping.
            self.previous = mix(self.previous, self.color, max(0.0, min(1.0, self.shade.value)))
            self.color = QColor(TONES[tone])
            self.shade.to(0.0, animate=False)
            self.shade.to(1.0, animate=self.isVisible())
        self.tone = tone
        if self.tone in ("rec", "busy"):
            if self.pulse.state() != QVariantAnimation.State.Running:
                self.pulse.start()
        else:
            self.pulse.stop()
            self.phase = 0.0
        self.update()

    def box_size(self, kind: str) -> QSizeF:
        if kind == "rec":
            return QSizeF(PILL)
        if kind in ("busy", "done"):
            return QSizeF(ROUND)
        title, hint = self.message
        wide = max(QFontMetrics(self.title_font()).horizontalAdvance(title),
                   QFontMetrics(self.hint_font()).horizontalAdvance(hint))
        return QSizeF(min(self.width() - 24, wide + 52), 54)

    def set_kind(self, kind: str):
        target = self.box_size(kind)
        if kind == self.kind and target == self.box_to and self.isVisible():
            return
        animate = self.look == "minimal" and self.isVisible() and self.presence.value > 0.05
        self.box_from = self.current_box() if animate else target
        self.box_to = target
        self.kind = kind
        self.morph.to(0.0, animate=False)
        self.morph.to(1.0, animate=animate)
        self.content.to(0.0, animate=False)
        self.content.to(1.0, animate=animate)
        if kind == "done":
            self.tick.to(0.0, animate=False)
            self.tick.to(1.0, 360 if animate else 320, animate=self.look == "minimal")

    def current_box(self) -> QSizeF:
        t = max(0.0, min(1.08, self.morph.value))
        return QSizeF(
            self.box_from.width() + (self.box_to.width() - self.box_from.width()) * t,
            self.box_from.height() + (self.box_to.height() - self.box_from.height()) * t,
        )

    # ---- showing and hiding ----

    def show_near_bottom(self):
        self.dismiss_timer.stop()
        if self.drag_from is None:
            self.place()
        if not self.isVisible():
            self.presence.to(0.0, animate=False)
            self.show()
        self.presence.to(1.0, 460, lambda t: spring(t, 0.8))

    def dismiss(self):
        """Fade out and sink a few pixels, then hide. Showing again midway turns it back."""
        self.dismiss_timer.stop()
        if self.drag_from is not None:
            return  # Held by the hand; it goes once let go.
        if self.isVisible():
            self.presence.to(0.0, 240)

    def vanish(self):
        self.dismiss_timer.stop()
        self.presence.to(0.0, animate=False)

    def _present(self):
        value = self.presence.value
        if self.embedded:
            self.fade.setOpacity(max(0.0, min(1.0, value)))
        else:
            self.setWindowOpacity(max(0.0, min(1.0, value)))
        if self.home and self.drag_from is None:
            # Past 1 the spring overshoots, so it lifts a pixel above its spot and settles.
            rise = 16 if self.look == "standard" else 10
            self.move(self.home[0], round(self.home[1] + (1 - value) * rise))
        if value <= 0.001 and self.presence.target == 0.0 and self.isVisible():
            self.hide()

    def _pulse(self, value):
        self.phase = float(value)
        self.update()

    def hideEvent(self, event):
        self.pulse.stop()
        self.trail.reset()
        super().hideEvent(event)

    # ---- dragging ----

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.press = event.globalPosition().toPoint()
        # From where it settles, not where the rising animation has got to.
        self.drag_from = self.press - (QPoint(*self.home) if self.home else self.pos())
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag_from is None:
            return super().mouseMoveEvent(event)
        top_left = event.globalPosition().toPoint() - self.drag_from
        self.home = (top_left.x(), top_left.y())
        self.move(top_left)

    def mouseReleaseEvent(self, event):
        if self.drag_from is None:
            return super().mouseReleaseEvent(event)
        moved = (event.globalPosition().toPoint() - self.press).manhattanLength() > 3
        self.drag_from = self.press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if moved:
            anchor = self.anchor()
            screen = QGuiApplication.screenAt(anchor) or self.screen()
            kept = clamp_anchor(anchor, screen.availableGeometry())
            if kept != anchor:
                self.home = self.home_for(kept)
                self.move(*self.home)
            self.position = [kept.x(), kept.y()]
            self.moved.emit(kept)
        if self.showing_message:
            self.dismiss_timer.start(2000)

    # ---- drawing ----

    def title_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def hint_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(12)
        return font

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.look == "minimal":
            self.paint_minimal(painter)
        else:
            self.paint_standard(painter)

    def dot_color(self) -> QColor:
        return mix(self.previous, self.color, max(0.0, min(1.0, self.shade.value)))

    def paint_standard(self, painter: QPainter):
        alpha = max(self.opacity, STANDARD_FLOOR)
        body = QRectF(self.rect()).adjusted(12, 10, -12, -BOTTOM)
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(8):
            painter.setBrush(QColor(0, 0, 0, round((10 - step) * alpha)))
            painter.drawRoundedRect(body.adjusted(-step, -step + 3, step, step + 3), 22 + step, 22 + step)
        gradient = QLinearGradient(body.topLeft(), body.bottomLeft())
        gradient.setColorAt(0, QColor(36, 43, 82, round(245 * alpha)))
        gradient.setColorAt(1, QColor(23, 28, 58, round(245 * alpha)))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(255, 255, 255, round(34 * alpha)), 1))
        painter.drawRoundedRect(body, 22, 22)
        color = self.dot_color()
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

    def paint_minimal(self, painter: QPainter):
        size = self.current_box()
        bottom = self.height() - BOTTOM
        body = QRectF(self.width() / 2 - size.width() / 2, bottom - size.height(), size.width(), size.height())
        radius = min(size.height() / 2, 18)
        # A message keeps a background whatever the opacity: its words must be read.
        message = self.content.value if self.kind == "message" else 0.0
        background = self.opacity + (max(self.opacity, 0.92) - self.opacity) * message
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(6):
            painter.setBrush(QColor(20, 24, 50, round((8 - step) * background)))
            painter.drawRoundedRect(body.adjusted(-step, -step + 2, step, step + 2), radius + step, radius + step)
        painter.setBrush(sheet(round(255 * background)))
        edge = QColor(255, 255, 255) if is_dark() else ink()
        edge.setAlphaF((0.1 if is_dark() else 0.13) * background)
        painter.setPen(QPen(edge, 1))
        painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        # A light edge around the ink, strongest where the background has gone.
        halo = 0.85 * (1 - background)
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, self.content.value)))
        middle = body.center().y()
        if self.kind == "rec":
            self.paint_dot(painter, QPointF(body.left() + 17, middle), pulse=True)
            self.paint_bars(painter, body.left() + 31, middle + 6.5, min(66.0, body.width() - 46), halo)
        elif self.kind == "busy":
            self.paint_spinner(painter, body.center(), halo)
        elif self.kind == "done":
            self.paint_tick(painter, body.center(), halo)
        else:
            painter.setClipRect(body)
            self.paint_dot(painter, QPointF(body.left() + 18, body.top() + 18))
            title, hint = self.message
            painter.setPen(ink())
            painter.setFont(self.title_font())
            painter.drawText(QRectF(body.left() + 32, body.top() + 8, body.width() - 44, 20),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            painter.setPen(muted())
            painter.setFont(self.hint_font())
            painter.drawText(QRectF(body.left() + 32, body.top() + 27, body.width() - 44, 18),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, hint)
        painter.restore()

    def paint_dot(self, painter: QPainter, center: QPointF, pulse: bool = False):
        color = self.dot_color()
        painter.setPen(Qt.PenStyle.NoPen)
        if pulse and self.tone in ("rec", "busy"):
            ring = 4 + self.phase * 5.5
            halo = QColor(color)
            halo.setAlphaF(0.45 * (1 - self.phase))
            painter.setBrush(halo)
            painter.drawEllipse(center, ring, ring)
        painter.setBrush(color)
        painter.drawEllipse(center, 4, 4)

    def paint_bars(self, painter: QPainter, left: float, base: float, width: float, halo: float):
        """Thin ink lines standing on one flat base, newest on the right, older ones a little paler."""
        count = len(self.trail.levels)
        gap = (width - count * 2) / (count - 1)
        lines = [
            (left + 1 + index * (2 + gap), 1.5 + level * 12.5, 0.5 + 0.5 * (index + 1) / count)
            for index, level in enumerate(self.trail.levels)
        ]
        for wide, strong in ((4.5, False), (2.0, True)):
            if not strong and halo < 0.02:
                continue
            for x, length, strength in lines:
                color = ink() if strong else sheet()
                color.setAlphaF(strength if strong else halo)
                pen = QPen(color, wide)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(x, base), QPointF(x, base - length))

    def paint_spinner(self, painter: QPainter, center: QPointF, halo: float):
        box = QRectF(center.x() - 7.5, center.y() - 7.5, 15, 15)
        start = -((time.monotonic() % 0.9) / 0.9) * 360
        painter.setBrush(Qt.BrushStyle.NoBrush)
        track = ink()
        track.setAlphaF(0.14)
        pen = QPen(track, 2)
        painter.setPen(pen)
        painter.drawEllipse(box)
        for wide, color in ((4.5, sheet(round(255 * halo))), (2.0, ink())):
            if wide > 2 and halo < 0.02:
                continue
            pen = QPen(color, wide)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(box, round(start * 16), -135 * 16)

    def paint_tick(self, painter: QPainter, center: QPointF, halo: float):
        points = [QPointF(-5.5, 0.5), QPointF(-1.5, 4.5), QPointF(5.5, -4)]
        lengths = [math.dist((points[0].x(), points[0].y()), (points[1].x(), points[1].y())),
                   math.dist((points[1].x(), points[1].y()), (points[2].x(), points[2].y()))]
        left = sum(lengths) * max(0.0, min(1.0, self.tick.value))
        path = QPainterPath(center + points[0])
        for (a, b), length in zip(pairwise(points), lengths, strict=True):
            share = min(1.0, left / length)
            path.lineTo(center + a + (b - a) * share)
            left -= length
            if left <= 0:
                break
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for wide, color in ((4.6, sheet(round(255 * halo))), (2.2, tone("#5A9E6F"))):
            if wide > 3 and halo < 0.02:
                continue
            pen = QPen(color, wide)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)


def demo_voice(t: float) -> float:
    """A made-up voice: phrases of a few syllables with short pauses between them."""
    cycle = t % 2.6
    if cycle > 1.9:
        return 0.02
    syllable = max(0.0, math.sin(cycle * math.pi * 3.6 + math.sin(t * 1.7) * 1.4))
    swell = 0.6 + 0.4 * math.sin(t * 0.9 + 1.2)
    grain = 0.5 + 0.5 * math.sin(t * 31.7) * math.sin(t * 17.3)
    return min(1.0, 0.05 + syllable * swell * (0.75 + 0.35 * grain))


class OverlayDemo(QObject):
    """Plays dictation on an overlay over and over: listening, working, then typed, and every
    other round a problem to act on. Without vanishing it stays in sight, for placing it."""

    def __init__(self, overlay: RecordingOverlay, vanish: bool = True):
        super().__init__(overlay)
        self.overlay = overlay
        self.vanish = vanish
        self.steps = (
            (("listen", 4.0), ("busy", 1.6), ("done", 1.5), ("gap", 0.8),
             ("listen", 3.4), ("busy", 1.4), ("problem", 3.6), ("gap", 0.8))
            if vanish
            else (("listen", 4.0), ("busy", 1.6), ("done", 1.6))
        )
        self.index = -1
        self.started = self.step_started = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    def start(self):
        if self.timer.isActive():
            return
        self.started = time.monotonic()
        self.index = -1
        self._next()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.overlay.vanish()

    def _next(self):
        self.index = (self.index + 1) % len(self.steps)
        self.step_started = time.monotonic()
        step = self.steps[self.index][0]
        overlay = self.overlay
        if step == "listen":
            overlay.listen("松开快捷键结束，Esc 取消")
        elif step == "busy":
            overlay.working("正在识别和整理…", "Esc 取消本次处理")
        elif step == "done":
            overlay.show_message("已输入", "可以继续按快捷键说下一段", 1300 if self.vanish else 600_000, brief=True)
        elif step == "problem":
            overlay.show_message("未能自动输入，结果已复制", "打开输入框后按 Ctrl+V 粘贴", 3000, "warn")

    def _tick(self):
        now = time.monotonic()
        step, length = self.steps[self.index]
        if step == "listen":
            self.overlay.set_level(round(demo_voice(now - self.started) ** 2 * 100))
            self.overlay.set_elapsed(int(now - self.step_started))
        if now - self.step_started >= length:
            self._next()


class OverlayPreview(QWidget):
    """The overlay as it will look, playing over a chat window or a dark code editor."""

    CODE = (
        ("def ", "#C792EA"), ("polish", "#82AAFF"), ("(text):", "#C9CCD6"), None,
        ("    # 去掉口癖，保留原意", "#676E85"), None,
        ("    words = ", "#C9CCD6"), ("split", "#82AAFF"), ("(text)", "#C9CCD6"), None,
        ("    return ", "#C792EA"), ("clean", "#82AAFF"), ("(words, ", "#C9CCD6"), ('"zh"', "#C3E88D"), (")", "#C9CCD6"),
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.dark = False
        self.setFixedHeight(158)
        self.overlay = RecordingOverlay(self, embedded=True)
        self.demo = OverlayDemo(self.overlay)

    def set_look(self, look: str, opacity: float):
        self.overlay.set_look(look, opacity)
        self.settle()

    def set_dark(self, dark: bool):
        self.dark = dark
        self.update()

    def settle(self):
        self.overlay.fixed_anchor = QPoint(self.width() // 2, self.height() - 20)
        self.overlay.place()
        self.overlay._present()

    def resizeEvent(self, event):
        self.settle()
        super().resizeEvent(event)

    def showEvent(self, event):
        self.settle()
        self.demo.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.demo.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor("#2C2E36" if self.dark else "#E6E1D8"), 1))
        painter.setBrush(QColor("#1E1F25" if self.dark else "#FFFFFF"))
        painter.drawRoundedRect(frame, 10, 10)
        if self.dark:
            font = QFont("Consolas")
            font.setPixelSize(12)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            x, y = 18.0, 26.0
            for part in self.CODE:
                if part is None:
                    x, y = 18.0, y + 21
                    continue
                text, color = part
                painter.setPen(QColor(color))
                painter.drawText(QPointF(x, y), text)
                x += metrics.horizontalAdvance(text)
            return
        painter.setPen(Qt.PenStyle.NoPen)
        width = self.width()
        for top, share, mine in ((16, 0.46, False), (34, 0.3, True), (52, 0.4, False), (70, 0.22, True)):
            painter.setBrush(QColor("#D7E8D2" if mine else "#ECE9E3"))
            length = width * share
            painter.drawRoundedRect(QRectF(width - 16 - length if mine else 16, top, length, 10), 5, 5)
        box = QRectF(12, self.height() - 42, width - 24, 30)
        painter.setPen(QPen(QColor("#E2DDD4"), 1))
        painter.setBrush(QColor("#FAF8F4"))
        painter.drawRoundedRect(box, 8, 8)
        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#A49C8F"))
        painter.drawText(box.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "输入消息…")
