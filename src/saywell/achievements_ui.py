"""How achievements look: medallions, the 成就 section of the overview, and the card at the
bottom right that says one was just reached.

The emblems are pictures (assets/achievements/<id>.png, drawn with Codex); the medallion
around them is drawn here, so one picture serves every state: an ink ring once reached, a
coral ring while new, a dashed ring and a faint silhouette while not yet reached, and a
question mark for a hidden one."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from .achievements import ACHIEVEMENTS, GROUPS, Achievement, Achievements
from .translation_ui import Floating
from .ui import ACCENT, ASSETS, CORAL, DISABLED, INK, LINE, MUTED, SERIF, SHEET, Glyph, label, make_button

BADGES = ASSETS / "achievements"
FAINT = "#958C7F"
SILHOUETTE = "#DDD5C8"  # An emblem not yet reached.
NEW_FOR = timedelta(days=1)  # How long a reached one keeps its coral ring.

_emblems: dict[tuple[str, bool], QImage | None] = {}


def emblem(achievement_id: str, faded: bool = False) -> QImage | None:
    """The emblem's picture, or its flat silhouette; None if there is no picture."""
    key = (achievement_id, faded)
    if key not in _emblems:
        image = QImage(str(BADGES / f"{achievement_id}.png"))
        if image.isNull():
            _emblems[key] = None
        elif faded:
            image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(image.rect(), QColor(SILHOUETTE))
            painter.end()
            _emblems[key] = image
        else:
            _emblems[key] = image
    return _emblems[key]


def state(achievement: Achievement, tally: Achievements, now: datetime | None = None) -> str:
    """new, reached, locked or secret."""
    when = tally.reached.get(achievement.id)
    if when is None:
        return "secret" if achievement.hidden else "locked"
    try:
        recent = (now or datetime.now()) - datetime.fromisoformat(when) < NEW_FOR
    except ValueError:
        recent = False
    return "new" if recent else "reached"


def draw_medallion(painter: QPainter, rect: QRectF, achievement: Achievement, look: str):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    size = rect.width()
    reached = look in ("new", "reached")
    ring = max(1.5, size * 0.036)
    edge = rect.adjusted(ring / 2, ring / 2, -ring / 2, -ring / 2)
    if reached:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SHEET))
        painter.drawEllipse(rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(CORAL if look == "new" else ACCENT), ring))
        painter.drawEllipse(edge)
        gap = ring + max(1.5, size * 0.035)
        painter.setPen(QPen(QColor(LINE), 1))
        painter.drawEllipse(edge.adjusted(gap, gap, -gap, -gap))
    else:
        pen = QPen(QColor(DISABLED), max(1.1, size * 0.022))
        pen.setDashPattern([2.6, 2.2])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(edge)
    inner = size * 0.58
    target = QRectF(rect.center().x() - inner / 2, rect.center().y() - inner / 2, inner, inner)
    picture = None if look == "secret" else emblem(achievement.id, faded=not reached)
    if picture is not None:
        painter.drawImage(target, picture)
    else:
        font = QFont(SERIF)
        font.setPixelSize(max(9, int(size * (0.42 if look == "secret" else 0.36))))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor(ACCENT if reached else DISABLED))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "?" if look == "secret" else achievement.name[0])
    painter.restore()


def amount(value: float, unit: str) -> str:
    """What is still missing, in words: 2,300 字, 2.7 万字, 24 分钟, 3 天."""
    if unit == "小时":
        return f"{math.ceil(value * 60)} 分钟" if value < 1 else f"{value:.1f}".removesuffix(".0") + " 小时"
    if value >= 10_000:
        return f"{value / 10_000:.1f}".removesuffix(".0") + f" 万{unit}"
    return f"{math.ceil(value):,} {unit}"


def describe(achievement: Achievement, tally: Achievements, facts: dict[str, float]) -> tuple[str, str, str]:
    """Title, condition and a note (the comment, what is missing, or the hint) for the hover card."""
    when = tally.reached.get(achievement.id)
    if when:
        try:
            day = datetime.fromisoformat(when)
            reached = f"{achievement.condition} · {day.month}月{day.day}日"
        except ValueError:
            reached = achievement.condition
        return achievement.name, reached, achievement.comment
    if achievement.hidden:
        return "？？？", "隐藏成就", achievement.hint
    value, goal = tally.progress(achievement, facts)
    missing = f"还差 {amount(goal - value, achievement.unit)}" if goal > 1 else "还没达成"
    return achievement.name, achievement.condition, missing


class Shelf(QWidget):
    """Medallions in rows with their names under them; the one under the pointer tells more.
    With `one_row`, only as many as fit on one line."""

    CELL, ROW = 88, 90

    def __init__(self, hovercard, size: int = 52, one_row: bool = False):
        super().__init__()
        self.hovercard = hovercard
        self.size = size
        self.one_row = one_row
        self.items: list[Achievement] = []
        self.tally: Achievements | None = None
        self.facts: dict[str, float] = {}
        self.hovered = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.ROW)

    def set_items(self, items: list[Achievement], tally: Achievements, facts: dict[str, float]):
        self.items, self.tally, self.facts = items, tally, facts
        self._fit()
        self.update()

    def columns(self) -> int:
        return max(1, self.width() // self.CELL)

    def shown(self) -> list[Achievement]:
        return self.items[: self.columns()] if self.one_row else self.items

    def _fit(self):
        rows = 1 if self.one_row else max(1, math.ceil(len(self.items) / self.columns()))
        if self.height() != rows * self.ROW:
            self.setFixedHeight(rows * self.ROW)

    def resizeEvent(self, event):
        self._fit()
        super().resizeEvent(event)

    def cell(self, index: int) -> QRectF:
        columns = self.columns()
        return QRectF(index % columns * self.CELL, index // columns * self.ROW, self.CELL, self.ROW)

    def paintEvent(self, _event):
        if self.tally is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(12)
        now = datetime.now()
        for index, achievement in enumerate(self.shown()):
            cell = self.cell(index)
            look = state(achievement, self.tally, now)
            top = cell.top() + 4
            medal = QRectF(cell.center().x() - self.size / 2, top, self.size, self.size)
            if index == self.hovered and look in ("new", "reached"):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 0, 0, 10))
                painter.drawEllipse(medal.adjusted(-3, -2, 3, 4))
            draw_medallion(painter, medal, achievement, look)
            painter.setFont(font)
            painter.setPen(QColor(INK if look in ("new", "reached") else FAINT))
            name = "？？？" if look == "secret" else achievement.name
            painter.drawText(QRectF(cell.left(), medal.bottom() + 6, cell.width(), 18),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, name)

    def index_at(self, x: float, y: float) -> int:
        columns = self.columns()
        column, row = int(x // self.CELL), int(y // self.ROW)
        index = row * columns + column
        return index if 0 <= column < columns and 0 <= index < len(self.shown()) else -1

    def mouseMoveEvent(self, event):
        index = self.index_at(event.position().x(), event.position().y())
        if index != self.hovered:
            self.hovered = index
            self.update()
        if index < 0 or self.tally is None:
            self.hovercard.hide()
            return
        title, body, note = describe(self.shown()[index], self.tally, self.facts)
        self.hovercard.show_at(event.globalPosition().toPoint(), title, body, note)

    def leaveEvent(self, event):
        self.hovered = -1
        self.hovercard.hide()
        self.update()
        super().leaveEvent(event)


class NextGoal(QWidget):
    """The visible achievement closest to being reached, and how far there is to go."""

    def __init__(self):
        super().__init__()
        self.achievement: Achievement | None = None
        self.value = self.goal = 0.0
        self.setFixedSize(300, 76)

    def set_goal(self, achievement: Achievement | None, value: float, goal: float):
        self.achievement, self.value, self.goal = achievement, value, goal
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = QFont(self.font())
        small.setPixelSize(12)
        if self.achievement is None:
            painter.setFont(small)
            painter.setPen(QColor(FAINT))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, "看得见的成就都达成了，还有几个藏着。")
            return
        draw_medallion(painter, QRectF(0, 10, 52, 52), self.achievement, "locked")
        left = 68
        painter.setFont(small)
        painter.setPen(QColor(FAINT))
        painter.drawText(QRectF(left, 4, 200, 16), Qt.AlignmentFlag.AlignVCenter, "下一个")
        title = QFont(SERIF)
        title.setPixelSize(16)
        title.setWeight(QFont.Weight.Bold)
        painter.setFont(title)
        painter.setPen(QColor(ACCENT))
        painter.drawText(QRectF(left, 20, 220, 22), Qt.AlignmentFlag.AlignVCenter, self.achievement.name)
        track = QRectF(left, 48, self.width() - left - 8, 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#EFE9DE"))
        painter.drawRoundedRect(track, 2, 2)
        share = max(0.0, min(1.0, self.value / self.goal)) if self.goal else 0.0
        if share > 0:
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(QRectF(track.left(), track.top(), max(4.0, track.width() * share), 4), 2, 2)
        painter.setFont(small)
        painter.setPen(QColor(FAINT))
        missing = f"还差 {amount(self.goal - self.value, self.achievement.unit)} · {self.achievement.condition}"
        painter.drawText(QRectF(left, 56, self.width() - left, 18), Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(small).elidedText(missing, Qt.TextElideMode.ElideRight, self.width() - left))


class AchievementsPanel(QFrame):
    """The 成就 section of the overview: folded to the latest few and the next one, or opened
    to every achievement by group."""

    def __init__(self, hovercard):
        super().__init__()
        self.setObjectName("panel")
        self.opened = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 16)
        layout.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(label("成就", "panelTitle"))
        self.count = label("", "panelMeta")
        head.addWidget(self.count)
        head.addStretch()
        self.toggle = make_button("展开全部", None, "link")
        self.toggle.set_trailing(Glyph.CHEVRON_DOWN)
        self.toggle.clicked.connect(lambda: self.set_open(not self.opened))
        head.addWidget(self.toggle)
        layout.addLayout(head)

        self.folded = QWidget()
        row = QHBoxLayout(self.folded)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        self.latest = Shelf(hovercard, one_row=True)
        row.addWidget(self.latest, 1)
        self.empty = label("说出第一句话，就能点亮第一个成就。", "statSub")
        row.addWidget(self.empty, 1)
        line = QFrame()
        line.setObjectName("vline")
        line.setFixedHeight(56)
        row.addWidget(line, 0, Qt.AlignmentFlag.AlignVCenter)
        self.next = NextGoal()
        row.addWidget(self.next, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.folded)

        self.all = QWidget()
        groups = QVBoxLayout(self.all)
        groups.setContentsMargins(0, 0, 0, 0)
        groups.setSpacing(0)
        self.shelves: list[Shelf] = []
        for index, group in enumerate(GROUPS):
            if index:
                rule = QFrame()
                rule.setObjectName("groupRule")
                groups.addWidget(rule)
            line = QHBoxLayout()
            line.setContentsMargins(0, 10, 0, 6)
            line.setSpacing(8)
            title = label(group, "groupTitle")
            title.setFixedWidth(64)
            line.addWidget(title, 0, Qt.AlignmentFlag.AlignTop)
            shelf = Shelf(hovercard, size=50)
            self.shelves.append(shelf)
            line.addWidget(shelf, 1)
            groups.addLayout(line)
        self.all.hide()
        layout.addWidget(self.all)
        self.off = label("使用统计已关闭，成就也随之暂停。可在设置中开启。", "statSub")
        self.off.hide()
        layout.addWidget(self.off)

    def set_open(self, opened: bool):
        self.opened = opened
        self.all.setVisible(opened)
        self.folded.setVisible(not opened and not self.off.isVisible())
        self.toggle.setText("收起" if opened else "展开全部")
        self.toggle.set_trailing(Glyph.CHEVRON_UP if opened else Glyph.CHEVRON_DOWN)

    def refresh(self, tally: Achievements | None, stats, settings):
        enabled = tally is not None and stats is not None
        self.off.setVisible(not enabled)
        self.toggle.setVisible(enabled)
        if not enabled:
            self.count.setText("")
            self.folded.hide()
            self.all.hide()
            return
        facts = tally.facts(stats, settings)
        self.count.setText(f"{len(tally.reached)} / {len(ACHIEVEMENTS)}")
        latest = tally.latest(12)
        self.latest.set_items(latest, tally, facts)
        self.latest.setVisible(bool(latest))
        self.empty.setVisible(not latest)
        upcoming = tally.upcoming(facts)
        value, goal = tally.progress(upcoming, facts) if upcoming else (0, 0)
        self.next.set_goal(upcoming, value, goal)
        for shelf, group in zip(self.shelves, GROUPS, strict=True):
            shelf.set_items([a for a in ACHIEVEMENTS if a.group == group], tally, facts)
        self.set_open(self.opened)


class AchievementToast(Floating):
    """A card at the bottom right of the screen, one achievement at a time, for a few seconds.
    It waits while `ready()` says no (recording), and never takes the focus. Clicked, it opens
    the 成就 section."""

    clicked = Signal()
    WIDTH, MARGIN, SECONDS = 420, 14, 4.5

    def __init__(self):
        super().__init__()
        self.queue: list[tuple[str, str, str, Achievement]] = []
        self.current: tuple[str, str, str, Achievement] | None = None
        self.ready = lambda: True
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.vanish)
        self.waiting = QTimer(self)
        self.waiting.setSingleShot(True)
        self.waiting.timeout.connect(self._next)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def push(self, achievement: Achievement):
        self.queue.append(("达成成就", achievement.name, achievement.comment, achievement))
        self._next()

    def push_summary(self, reached: list[Achievement]):
        """Reached at once from what was used before: said once, not one by one."""
        if len(reached) == 1:
            self.push(reached[0])
            return
        self.queue.append(("成就", f"点亮了 {len(reached)} 个成就", "按以前的使用记录补上的，去概览里看看吧。", reached[-1]))
        self._next()

    def _next(self):
        if self.isVisible() or not self.queue:
            return
        if not self.ready():
            self.waiting.start(1500)
            return
        self.current = self.queue.pop(0)
        self.resize(self.WIDTH + 2 * self.MARGIN, self._card_height() + 2 * self.MARGIN)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 8, screen.bottom() - self.height() - 8)
        self.update()
        self.appear()
        self.timer.start(int(self.SECONDS * 1000))

    def fonts(self) -> tuple[QFont, QFont, QFont]:
        caption = QFont(self.font())
        caption.setPixelSize(12)
        title = QFont(SERIF)
        title.setPixelSize(17)
        title.setWeight(QFont.Weight.Bold)
        body = QFont(self.font())
        body.setPixelSize(13)
        return caption, title, body

    def _text_width(self) -> int:
        return self.WIDTH - 16 - 56 - 14 - 18

    def _card_height(self) -> int:
        body = self.fonts()[2]
        comment = self.current[2] if self.current else ""
        lines = QFontMetrics(body).boundingRect(0, 0, self._text_width(), 400, Qt.TextFlag.TextWordWrap, comment)
        return max(88, 14 + 16 + 2 + 24 + 4 + lines.height() + 16)

    def paintEvent(self, _event):
        if self.current is None:
            return
        caption_text, title_text, comment, achievement = self.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(self.MARGIN, self.MARGIN - 4, -self.MARGIN, -self.MARGIN - 4)
        for step in range(6):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 24, 50, 9 - step))
            painter.drawRoundedRect(card.adjusted(-step, -step + 3, step, step + 3), 14 + step, 14 + step)
        painter.setBrush(QColor(SHEET))
        painter.setPen(QPen(QColor(LINE), 1))
        painter.drawRoundedRect(card, 14, 14)
        draw_medallion(painter, QRectF(card.left() + 16, card.center().y() - 28, 56, 56), achievement, "new")
        caption, title, body = self.fonts()
        left = card.left() + 16 + 56 + 14
        top = card.top() + 14
        painter.setFont(caption)
        painter.setPen(QColor(CORAL))
        painter.drawText(QRectF(left, top, self._text_width(), 16), Qt.AlignmentFlag.AlignVCenter, caption_text)
        painter.setFont(title)
        painter.setPen(QColor(ACCENT))
        painter.drawText(QRectF(left, top + 18, self._text_width(), 24), Qt.AlignmentFlag.AlignVCenter, title_text)
        painter.setFont(body)
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(left, top + 46, self._text_width(), card.bottom() - top - 46),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, comment)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.current = None
        QTimer.singleShot(400, self._next)

    def enterEvent(self, event):
        self.timer.stop()  # Being read: stay.
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isVisible():
            self.timer.start(1800)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            self.queue.clear()
            self.vanish()
