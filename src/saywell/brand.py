"""The app's mark: an origami swallow, ink navy with an indigo fold and a coral breast, on a warm
ivory tile. Drawn from geometry rather than shipped as a bitmap, so every size is crisp and the
small ones can be tuned: the tray, the taskbar, the button beside a selection.

The bird lives in its own units, 100 wide and about 89 tall, rising toward the upper right.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen

INK = "#192457"  # The bird, and the app's primary colour.
INDIGO = "#3C488E"  # The one fold that catches the light.
CORAL = "#F2755F"  # The breast; in the app, what is live.
IVORY_TOP, IVORY_BOTTOM = "#F8F2E8", "#EFE5D6"  # The tile, a sheet of warm paper.
RIM = QColor(120, 96, 62, 46)  # The tile's edge, so it holds on a light taskbar.
# Inside the app's dark theme: a deep ink tile, and the bird in paper with a periwinkle fold.
NIGHT_TOP, NIGHT_BOTTOM = "#2A2F4C", "#1C2037"
NIGHT_RIM = QColor(255, 255, 255, 40)
NIGHT = {INK: "#EDE6D8", INDIGO: "#9DA8EC", CORAL: "#F4826D"}

BIRD_WIDTH, BIRD_HEIGHT = 100.0, 89.2


def _polygon(*points: tuple[float, float]) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    return path


def _head() -> QPainterPath:
    path = QPainterPath(QPointF(70.8, 30.6))
    path.lineTo(81.4, 22.8)
    path.quadTo(83.6, 21.2, 86.4, 21.9)  # The crown, rounded.
    path.lineTo(100.0, 26.5)  # The beak.
    path.lineTo(89.8, 30.3)
    path.lineTo(83.1, 34.6)
    path.lineTo(74.4, 42.8)
    path.lineTo(47.2, 38.7)
    path.closeSubpath()
    return path


def _breast() -> QPainterPath:
    path = QPainterPath(QPointF(89.8, 30.3))
    path.quadTo(86.7, 54.8, 66.3, 60.6)
    path.lineTo(74.4, 42.8)
    path.lineTo(83.1, 34.6)
    path.closeSubpath()
    return path


# Each fold of the paper, back to front, with its colour.
FOLDS = (
    ("wing", _polygon((13.2, 0.0), (56.0, 14.0), (70.8, 30.6), (47.2, 38.7), (30.1, 17.6)), INK),
    ("upper tail", _polygon((0.0, 63.4), (44.0, 44.2), (35.4, 58.8), (26.5, 58.6)), INK),
    ("lower tail", _polygon((35.4, 58.8), (30.4, 72.3), (4.3, 89.2)), INK),
    ("head", _head(), INK),
    ("belly", _polygon((74.4, 42.8), (66.3, 60.6), (38.5, 66.8)), INK),
    ("fold", _polygon((47.2, 38.7), (74.4, 42.8), (30.4, 72.3), (35.4, 58.8)), INDIGO),
    ("breast", _breast(), CORAL),
)


def draw_swallow(painter: QPainter, rect: QRectF, color: str | None = None, night: bool = False) -> None:
    """The bird, as large as fits in rect and centred. One colour (a silhouette) when given; night
    draws it for the dark tile."""
    scale = min(rect.width() / BIRD_WIDTH, rect.height() / BIRD_HEIGHT)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center().x() - BIRD_WIDTH * scale / 2, rect.center().y() - BIRD_HEIGHT * scale / 2)
    painter.scale(scale, scale)
    for _name, path, fill in FOLDS:
        if night:
            fill = NIGHT[fill]
        painter.setBrush(QColor(color or fill))
        # A hairline in the same colour closes the seams antialiasing leaves between folds.
        seam = QPen(QColor(color or fill), 0.6)
        seam.setCosmetic(True)
        seam.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(seam)
        painter.drawPath(path)
    painter.restore()


def squircle(rect: QRectF, exponent: float = 5.0, steps: int = 96) -> QPainterPath:
    """A superellipse: the smooth-cornered square of app icons."""
    cx, cy = rect.center().x(), rect.center().y()
    a, b = rect.width() / 2, rect.height() / 2
    path = QPainterPath()
    for index in range(steps):
        angle = 2 * math.pi * index / steps
        c, s = math.cos(angle), math.sin(angle)
        x = cx + a * math.copysign(abs(c) ** (2 / exponent), c)
        y = cy + b * math.copysign(abs(s) ** (2 / exponent), s)
        if index == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def draw_tile(painter: QPainter, rect: QRectF, rim: int | None = None, night: bool = False) -> None:
    """The ivory tile with a fine edge; light from above. rim sets the edge's alpha; by default
    small tiles get a firmer one, to stand out on a light taskbar. night draws the ink tile."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    shape = squircle(rect)
    paper = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    paper.setColorAt(0.0, QColor(NIGHT_TOP if night else IVORY_TOP))
    paper.setColorAt(1.0, QColor(NIGHT_BOTTOM if night else IVORY_BOTTOM))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(paper)
    painter.drawPath(shape)
    edge = max(1.0, rect.width() / 256)
    colour = QColor(NIGHT_RIM if night else RIM)
    if not night:
        colour.setAlpha(rim if rim is not None else 80 if rect.width() <= 48 else RIM.alpha())
    painter.setPen(QPen(colour, edge))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = edge / 2
    painter.drawPath(squircle(rect.adjusted(inset, inset, -inset, -inset)))
    painter.restore()


def bird_rect(rect: QRectF) -> QRectF:
    """Where the bird sits on a tile: a little larger at small sizes, so it reads."""
    side = rect.width()
    share = 0.84 if side <= 24 else 0.8 if side <= 48 else 0.74
    width = side * share
    bird = QRectF(0, 0, width, width * BIRD_HEIGHT / BIRD_WIDTH)
    bird.moveCenter(QPointF(rect.center().x() + side * 0.01, rect.center().y() - side * 0.012))
    return bird


def soft_shadow(size: int, bird: QRectF, offset: float, blur: int) -> QImage:
    """The bird's shadow on the paper: its silhouette, blurred by shrinking and growing again."""
    small = max(8, size // blur)
    silhouette = QImage(small, small, QImage.Format.Format_ARGB32_Premultiplied)
    silhouette.fill(Qt.GlobalColor.transparent)
    painter = QPainter(silhouette)
    ratio = small / size
    shadow_rect = QRectF(bird.x() * ratio, (bird.y() + offset) * ratio, bird.width() * ratio, bird.height() * ratio)
    draw_swallow(painter, shadow_rect, "#5A3F1E")
    painter.end()
    return silhouette.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def draw_icon(painter: QPainter, rect: QRectF) -> None:
    """The app icon: tile and bird."""
    draw_tile(painter, rect)
    draw_swallow(painter, bird_rect(rect))


def icon_image(size: int, night: bool = False, ratio: float = 1.0) -> QImage:
    """The icon at one size; large ones get the bird's soft shadow on the paper. night is the
    version shown inside the app's dark theme; the program's own icon is always the ivory one.
    ratio draws it that many times finer, for screens with more pixels than points."""
    image = QImage(round(size * ratio), round(size * ratio), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    rect = QRectF(0, 0, size, size)
    draw_tile(painter, rect, night=night)
    bird = bird_rect(rect)
    if size >= 64:
        painter.save()
        painter.setClipPath(squircle(rect.adjusted(1, 1, -1, -1)))
        painter.setOpacity(0.4 if night else 0.16)
        painter.drawImage(0, 0, soft_shadow(size, bird, size * 0.035, 24))
        painter.restore()
    draw_swallow(painter, bird, night=night)
    painter.end()
    return image


def tray_image(size: int, color: str | None = None) -> QImage:
    """The bird alone for the notification area: white on a dark taskbar, in colour on a light one."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    margin = size * 0.02
    draw_swallow(painter, QRectF(margin, margin, size - 2 * margin, size - 2 * margin), color)
    painter.end()
    return image
