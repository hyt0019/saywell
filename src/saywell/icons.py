"""The app's own line icons, drawn on a 24-unit grid with one stroke weight and round ends.

Drawing them in code keeps them crisp at every scale and gives the app a consistent,
recognisable set instead of a generic system icon font."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

STROKE = 1.9  # In grid units; about 1.3 px at 16 px.


def _lines(path: QPainterPath, *points: tuple[float, float]) -> None:
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)


def _arc(path: QPainterPath, cx: float, cy: float, r: float, start: float, span: float) -> None:
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path.arcMoveTo(rect, start)
    path.arcTo(rect, start, span)


def _rounded(path: QPainterPath, x: float, y: float, w: float, h: float, r: float) -> None:
    path.addRoundedRect(QRectF(x, y, w, h), r, r)


def _mic(p):
    _rounded(p, 9, 3, 6, 11.5, 3)
    _arc(p, 12, 10.5, 6.5, 180, 180)
    _lines(p, (12, 17), (12, 21))


def _history(p):
    # A clock whose rim runs counter-clockwise into an arrowhead: "back in time".
    _arc(p, 12, 12, 8.5, 160, -300)
    _lines(p, (2.6, 9.4), (3.9, 12.9), (7.2, 11.4))
    _lines(p, (12, 7.5), (12, 12), (15, 14))


def _import(p):
    _lines(p, (12, 3.5), (12, 14.5))
    _lines(p, (8, 10.5), (12, 14.5), (16, 10.5))
    p.moveTo(4, 14)
    p.lineTo(4, 17.5)
    p.quadTo(4, 20, 6.5, 20)
    p.lineTo(17.5, 20)
    p.quadTo(20, 20, 20, 17.5)
    p.lineTo(20, 14)


def _model(p):
    for x, half in ((4.5, 2), (8.25, 5), (12, 8), (15.75, 4.5), (19.5, 1.8)):
        _lines(p, (x, 12 - half), (x, 12 + half))


def _settings(p):
    # Three sliders, each knob cutting its rail.
    for y, knob in ((6.5, 15.5), (12, 8.5), (17.5, 13.5)):
        _lines(p, (3.5, y), (knob - 2.4, y))
        _lines(p, (knob + 2.4, y), (20.5, y))
        p.addEllipse(QPointF(knob, y), 2.4, 2.4)


def _download(p):
    _lines(p, (12, 4), (12, 15))
    _lines(p, (7.5, 10.5), (12, 15), (16.5, 10.5))
    _lines(p, (5, 19.5), (19, 19.5))


def _copy(p):
    _rounded(p, 8.5, 8.5, 11.5, 11.5, 2.6)
    p.moveTo(4, 14.5)
    p.lineTo(4, 6.5)
    p.quadTo(4, 4, 6.5, 4)
    p.lineTo(14.5, 4)


def _insert(p):
    # An arrow running into a text caret: "type it where the cursor is".
    _lines(p, (3.5, 12), (15, 12))
    _lines(p, (11, 8), (15, 12), (11, 16))
    _lines(p, (19.5, 5), (19.5, 19))


def _tidy(p):
    _lines(p, (4, 6.5), (20, 6.5))
    _lines(p, (4, 12), (15, 12))
    _lines(p, (4, 17.5), (10, 17.5))
    _lines(p, (13.5, 19.5), (19.5, 13.5))
    _lines(p, (18, 12), (21, 15))


def _clear(p):
    _lines(p, (7, 7), (17, 17))
    _lines(p, (17, 7), (7, 17))


def _check(p):
    _lines(p, (5, 12.5), (9.8, 17), (19, 7.5))


def _delete(p):
    _lines(p, (4.5, 6.8), (19.5, 6.8))
    _lines(p, (9.5, 6.8), (9.5, 4.5), (14.5, 4.5), (14.5, 6.8))
    p.moveTo(6.5, 6.8)
    p.lineTo(7.3, 18)
    p.quadTo(7.5, 20, 9.5, 20)
    p.lineTo(14.5, 20)
    p.quadTo(16.5, 20, 16.7, 18)
    p.lineTo(17.5, 6.8)


def _folder(p):
    p.moveTo(3.5, 17.5)
    p.lineTo(3.5, 6.5)
    p.quadTo(3.5, 5, 5, 5)
    p.lineTo(9.2, 5)
    p.lineTo(11.2, 7.2)
    p.lineTo(19, 7.2)
    p.quadTo(20.5, 7.2, 20.5, 8.7)
    p.lineTo(20.5, 17.5)
    p.quadTo(20.5, 19.5, 18.5, 19.5)
    p.lineTo(5.5, 19.5)
    p.quadTo(3.5, 19.5, 3.5, 17.5)


def _lock(p):
    _rounded(p, 5, 10.5, 14, 10, 2.6)
    p.moveTo(8, 10.5)
    p.lineTo(8, 8)
    p.arcTo(QRectF(8, 4, 8, 8), 180, -180)
    p.lineTo(16, 10.5)


def _info(p):
    p.addEllipse(QPointF(12, 12), 8.5, 8.5)
    _lines(p, (12, 11), (12, 16))
    _lines(p, (12, 7.9), (12, 8.1))


def _external(p):
    p.moveTo(18.5, 13.5)
    p.lineTo(18.5, 17.5)
    p.quadTo(18.5, 19.5, 16.5, 19.5)
    p.lineTo(6.5, 19.5)
    p.quadTo(4.5, 19.5, 4.5, 17.5)
    p.lineTo(4.5, 7.5)
    p.quadTo(4.5, 5.5, 6.5, 5.5)
    p.lineTo(10.5, 5.5)
    _lines(p, (14, 4.5), (19.5, 4.5), (19.5, 10))
    _lines(p, (19.5, 4.5), (11.5, 12.5))


def _pulse(p):
    _lines(p, (3, 12.5), (7.5, 12.5), (10, 6), (14, 18.5), (16.5, 12.5), (21, 12.5))


def _refresh(p):
    _arc(p, 12, 12, 7.5, 60, 280)
    _lines(p, (19.2, 4.6), (16.2, 5.7), (17.4, 8.7))


def _chevron_down(p):
    _lines(p, (7, 10), (12, 15), (17, 10))


def _chevron_right(p):
    _lines(p, (10, 7), (15, 12), (10, 17))


def _verify(p):
    p.moveTo(12, 3.5)
    p.lineTo(19, 6)
    p.lineTo(19, 11.5)
    p.quadTo(19, 17.2, 12, 20.5)
    p.quadTo(5, 17.2, 5, 11.5)
    p.lineTo(5, 6)
    p.closeSubpath()
    _lines(p, (8.8, 12.2), (11.1, 14.4), (15.4, 9.9))


def _keyboard(p):
    _rounded(p, 3, 6, 18, 12, 2.6)
    for x in (7, 10.3, 13.6, 17):
        _lines(p, (x, 9.6), (x + 0.01, 9.6))
    _lines(p, (8, 14.4), (16, 14.4))


def _translate(p):
    # A globe: rim, one meridian and the equator.
    p.addEllipse(QPointF(12, 12), 8.5, 8.5)
    p.addEllipse(QPointF(12, 12), 3.6, 8.5)
    _lines(p, (3.5, 12), (20.5, 12))


PATHS = {
    "translate": _translate,
    "mic": _mic,
    "history": _history,
    "import": _import,
    "model": _model,
    "settings": _settings,
    "download": _download,
    "copy": _copy,
    "insert": _insert,
    "tidy": _tidy,
    "clear": _clear,
    "check": _check,
    "cancel": _clear,
    "delete": _delete,
    "folder": _folder,
    "lock": _lock,
    "info": _info,
    "external": _external,
    "pulse": _pulse,
    "refresh": _refresh,
    "chevron_down": _chevron_down,
    "chevron_right": _chevron_right,
    "verify": _verify,
    "keyboard": _keyboard,
}


def icon_path(name: str) -> QPainterPath:
    path = QPainterPath()
    PATHS[name](path)
    return path


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor | str, stroke: float = STROKE) -> None:
    scale = min(rect.width(), rect.height()) / 24
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center().x() - 12 * scale, rect.center().y() - 12 * scale)
    painter.scale(scale, scale)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(icon_path(name))
    painter.restore()


def icon_pixmap(name: str, color: QColor | str, size: int = 16, ratio: float = 2.0) -> QPixmap:
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_icon(painter, name, QRectF(0, 0, size, size), color)
    painter.end()
    return pixmap
