"""The overview page: totals, a year of speaking, latest result, API usage and milestones.

Typography carries the page: big numbers, small quiet labels, hairlines instead of icons."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .icons import draw_icon
from .stats import TYPING_CHARS_PER_MINUTE, Day, StatsStore
from .ui import ACCENT, INK, MUTED, Glyph, Tween, label, make_button

WEEKDAYS = "一二三四五六日"
SUBTLE = "#8E8E93"
HAIRLINE = "#E8E8ED"
# Empty, then four steps of the accent blue.
HEAT = ("#EDEFF2", "#CFE2FB", "#8EBCF6", "#3E8BEE", "#0A5BD3")
HEAT_STEPS = (1, 300, 1000, 3000)
NUMBER_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI"]

DASHBOARD_STYLE = """
QFrame#panel { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 18px; }
QLabel#panelTitle { font-size: 15px; font-weight: 700; color: #1D1D1F; }
QLabel#panelMeta { font-size: 12px; color: #8E8E93; }
QLabel#statCaption { font-size: 13px; color: #6E6E73; }
QLabel#statSub { font-size: 12px; color: #8E8E93; }
QLabel#latestText { font-size: 15px; color: #1D1D1F; }
QLabel#latestEmpty { font-size: 14px; color: #8E8E93; }
QLabel#rowLabel { color: #6E6E73; font-size: 13px; }
QLabel#rowValue { color: #1D1D1F; font-size: 13px; }
QLabel#trackTitle { color: #3A3A3C; font-size: 13px; }
QLabel#trackValue { color: #1D1D1F; font-size: 13px; font-weight: 700; }
QFrame#vline { background: #EDEDF0; min-width: 1px; max-width: 1px; border: none; }
"""


def number(value: float) -> str:
    """Chinese-style compact numbers: 8,600 → 8,600; 86,000 → 8.6 万."""
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f} 亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f} 万".replace(".0 万", " 万")
    return f"{value:,.0f}"


def duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes >= 60:
        return f"{minutes // 60} 小时 {minutes % 60} 分"
    return f"{minutes} 分钟" if minutes else f"{seconds:.0f} 秒"


def scaled(value: float, unit: str) -> tuple[Callable[[float], str], str]:
    """Big numbers keep only digits large: 73,000 字 → "7.3" + "万字"."""
    if value >= 10_000:
        return (lambda v: f"{v / 10_000:.1f}".removesuffix(".0")), f"万{unit}"
    return (lambda v: f"{v:,.0f}"), unit


class HoverCard(QWidget):
    """A small floating card that follows the pointer, used instead of system tooltips."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title = ""
        self.body = ""
        self.fade = Tween(self, duration=160, on_change=lambda: self.setWindowOpacity(self.fade.value))

    def fonts(self) -> tuple[QFont, QFont]:
        title = QFont(self.font())
        title.setPixelSize(12)
        title.setBold(True)
        body = QFont(self.font())
        body.setPixelSize(12)
        return title, body

    def show_at(self, position: QPoint, title: str, body: str):
        self.title, self.body = title, body
        title_font, body_font = self.fonts()
        width = max(QFontMetrics(title_font).horizontalAdvance(title), QFontMetrics(body_font).horizontalAdvance(body))
        self.resize(width + 28 + 12, 58 + 12)
        target = position + QPoint(14, 16)
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen and target.x() + self.width() > screen.right():
            target.setX(position.x() - self.width() - 8)
        self.move(target)
        if not self.isVisible():
            self.fade.to(0.0, animate=False)  # Fades in once; following the pointer stays instant.
            self.show()
            self.fade.to(1.0)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(6, 4, -6, -8)
        for step in range(5):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 7 - step))
            painter.drawRoundedRect(body.adjusted(-step, -step + 2, step, step + 2), 12 + step, 12 + step)
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#E3E3E8"), 1))
        painter.drawRoundedRect(body, 12, 12)
        title_font, body_font = self.fonts()
        painter.setFont(title_font)
        painter.setPen(QColor(INK))
        painter.drawText(QRectF(body.left() + 14, body.top() + 10, body.width(), 18), Qt.AlignmentFlag.AlignVCenter, self.title)
        painter.setFont(body_font)
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(body.left() + 14, body.top() + 29, body.width(), 18), Qt.AlignmentFlag.AlignVCenter, self.body)


class CountLabel(QLabel):
    """A big number with a small unit; changes roll from the old value to the new one."""

    def __init__(self, size: int = 34):
        super().__init__()
        self.size = size
        self.value = 0.0
        self.formatter: Callable[[float], str] = number
        self.unit = ""
        self.setTextFormat(Qt.TextFormat.RichText)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(650)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._render)

    def set_value(self, value: float, unit: str, formatter: Callable[[float], str] = number):
        self.unit, self.formatter = unit, formatter
        if self.isVisible() and value != self.value:
            self.animation.stop()
            self.animation.setStartValue(float(self.value))
            self.animation.setEndValue(float(value))
            self.animation.start()
        else:
            self._render(value)
        self.value = value

    def _render(self, value):
        families = ", ".join(f"'{family}'" for family in NUMBER_FAMILIES)
        self.setText(
            f"<span style=\"font-family:{families}; font-size:{self.size}px; font-weight:600; color:{INK}\">"
            f"{self.formatter(float(value))}</span>"
            f"<span style='font-size:13px; color:{MUTED}'>&nbsp;{self.unit}</span>"
        )


class Stat(QWidget):
    def __init__(self, caption: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(4)
        layout.addWidget(label(caption, "statCaption"))
        self.value = CountLabel()
        layout.addWidget(self.value)
        self.sub = label("", "statSub")
        layout.addWidget(self.sub)

    def set(self, value: float, unit: str, sub: str, formatter: Callable[[float], str] = number):
        self.value.set_value(value, unit, formatter)
        self.sub.setText(sub)


def panel(title: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)
    head = QHBoxLayout()
    head.setSpacing(10)
    head.addWidget(label(title, "panelTitle"))
    head.addStretch()
    layout.addLayout(head)
    return frame, layout, head


def vline() -> QFrame:
    line = QFrame()
    line.setObjectName("vline")
    return line


class Heatmap(QWidget):
    """GitHub-style year of days; darker means more characters spoken."""

    WEEKS = 53
    TOP = 20
    LEFT = 22
    GAP = 3

    def __init__(self, hover: HoverCard):
        super().__init__()
        self.hovercard = hover
        self.days: dict[str, Day] = {}
        self.today = date.today()
        self.hovered: date | None = None
        self.setMouseTracking(True)
        self.setFixedHeight(int(self.TOP + 7 * (15 + self.GAP)))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, days: dict[str, Day], today: date):
        self.days, self.today = days, today
        self.update()

    @property
    def start(self) -> date:
        # Columns are weeks starting on Monday; the last column holds today.
        return self.today - timedelta(days=self.today.weekday()) - timedelta(weeks=self.WEEKS - 1)

    def geometry_for(self) -> tuple[float, float]:
        step = min(15 + self.GAP, (self.width() - self.LEFT) / self.WEEKS)
        offset = self.LEFT + max(0.0, (self.width() - self.LEFT - step * self.WEEKS) / 2)
        return step, offset

    @staticmethod
    def level(chars: int) -> int:
        return sum(chars >= step for step in HEAT_STEPS)

    def cell_rect(self, day: date) -> QRectF:
        step, offset = self.geometry_for()
        week = (day - self.start).days // 7
        return QRectF(offset + week * step, self.TOP + day.weekday() * step, step - self.GAP, step - self.GAP)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        step, offset = self.geometry_for()
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor(SUBTLE))
        for row in (0, 2, 4):
            painter.drawText(QRectF(0, self.TOP + row * step, self.LEFT - 7, step - self.GAP),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, WEEKDAYS[row])
        start = self.start
        last_month = None
        for week in range(self.WEEKS):
            first = start + timedelta(weeks=week)
            if first.month != last_month and week < self.WEEKS - 1:
                if last_month is not None or first.day <= 7:
                    painter.setPen(QColor(SUBTLE))
                    painter.drawText(QRectF(offset + week * step, 0, 40, self.TOP - 5),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{first.month}月")
                last_month = first.month
            for row in range(7):
                day = first + timedelta(days=row)
                if day > self.today:
                    continue
                stats = self.days.get(day.isoformat())
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(HEAT[self.level(stats.chars if stats else 0)]))
                painter.drawRoundedRect(self.cell_rect(day), 2.5, 2.5)
        for day, color in ((self.today, INK), (self.hovered, ACCENT)):
            if day is not None:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(color), 1.5))
                painter.drawRoundedRect(self.cell_rect(day).adjusted(-1.5, -1.5, 1.5, 1.5), 3.5, 3.5)

    def day_at(self, x: float, y: float) -> date | None:
        step, offset = self.geometry_for()
        week, row = int((x - offset) // step), int((y - self.TOP) // step)
        if not (0 <= week < self.WEEKS and 0 <= row < 7):
            return None
        day = self.start + timedelta(weeks=week, days=row)
        return day if day <= self.today else None

    def mouseMoveEvent(self, event):
        day = self.day_at(event.position().x(), event.position().y())
        if day != self.hovered:
            self.hovered = day
            self.update()
        if day is None:
            self.hovercard.hide()
            return
        stats = self.days.get(day.isoformat())
        title = f"{day.month}月{day.day}日 周{WEEKDAYS[day.weekday()]}"
        body = f"{stats.chars:,} 字 · {stats.dictations} 次" if stats and stats.dictations else "没有说话"
        self.hovercard.show_at(event.globalPosition().toPoint(), title, body)

    def leaveEvent(self, event):
        self.hovered = None
        self.hovercard.hide()
        self.update()
        super().leaveEvent(event)


class Legend(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(label("少", "statSub"))
        for color in HEAT:
            swatch = QLabel()
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(f"background: {color}; border-radius: 2px;")
            layout.addWidget(swatch)
        layout.addWidget(label("多", "statSub"))


class MiniBars(QWidget):
    """Tokens per day for the last two weeks; the bar under the pointer lights up."""

    def __init__(self, hover: HoverCard):
        super().__init__()
        self.hovercard = hover
        self.values: list[tuple[date, int]] = []
        self.hovered = -1
        self.setMinimumHeight(64)
        self.setMaximumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_data(self, values: list[tuple[date, int]]):
        self.values = values
        self.update()

    def paintEvent(self, _event):
        if not self.values:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width() / len(self.values)
        peak = max(value for _, value in self.values) or 1
        for index, (_day, value) in enumerate(self.values):
            height = max(3.0, (self.height() - 4) * value / peak) if value else 3.0
            rect = QRectF(index * width + width * 0.2, self.height() - height, width * 0.6, height)
            if not value:
                color = QColor("#EDEFF2")
            elif index == self.hovered or (self.hovered < 0 and index == len(self.values) - 1):
                color = QColor(ACCENT)
            else:
                color = QColor("#B9D5F8")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 2.5, 2.5)

    def mouseMoveEvent(self, event):
        if not self.values:
            return
        index = int(event.position().x() // (self.width() / len(self.values)))
        if not 0 <= index < len(self.values):
            return
        if index != self.hovered:
            self.hovered = index
            self.update()
        day, value = self.values[index]
        self.hovercard.show_at(event.globalPosition().toPoint(), f"{day.month}月{day.day}日", f"{value:,} tokens")

    def leaveEvent(self, event):
        self.hovered = -1
        self.hovercard.hide()
        self.update()
        super().leaveEvent(event)


class MilestoneTrack(QWidget):
    """A progress line with milestone nodes; reached nodes are filled."""

    def __init__(self, hover: HoverCard, steps: tuple[int, ...], names: tuple[str, ...], unit: str):
        super().__init__()
        self.hovercard = hover
        self.steps, self.names, self.unit = steps, names, unit
        self.value = 0.0
        self.shown = 0.0
        self.setFixedHeight(46)
        self.setMouseTracking(True)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(800)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._progress)

    def reached(self) -> list[str]:
        return [name for step, name in zip(self.steps, self.names, strict=True) if self.value >= step]

    def set_value(self, value: float):
        target = self.position(value)
        self.value = value
        if self.isVisible():
            self.animation.stop()
            self.animation.setStartValue(self.shown)
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.shown = target
            self.update()

    def _progress(self, value):
        self.shown = float(value)
        self.update()

    def position(self, value: float) -> float:
        """0..1 along the line: value 0 at the left end, node k at (k + 1) / n."""
        count = len(self.steps)
        if value <= 0:
            return 0.0
        if value < self.steps[0]:
            return value / self.steps[0] / count
        for index in range(count - 1):
            low, high = self.steps[index], self.steps[index + 1]
            if value < high:
                return (index + 1 + (value - low) / (high - low)) / count
        return 1.0

    LEFT, RIGHT = 8.0, 30.0

    def node_x(self, index: int) -> float:
        return self.LEFT + (self.width() - self.LEFT - self.RIGHT) * (index + 1) / len(self.steps)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y, left, right = 10.0, self.LEFT, self.width() - self.RIGHT
        track = QPen(QColor(HAIRLINE), 3)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawLine(QRectF(left, y, right - left, 0).topLeft(), QRectF(left, y, right - left, 0).topRight())
        if self.shown > 0:
            fill = QPen(QColor(ACCENT), 3)
            fill.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(fill)
            end = left + (right - left) * self.shown
            painter.drawLine(QRectF(left, y, end - left, 0).topLeft(), QRectF(left, y, end - left, 0).topRight())
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        for index, name in enumerate(self.names):
            x = self.node_x(index)
            reached = self.shown >= (index + 1) / len(self.steps) - 1e-6
            node = QRectF(x - 7, y - 7, 14, 14)
            if reached:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ACCENT))
                painter.drawEllipse(node)
                draw_icon(painter, "check", node.adjusted(3, 3, -3, -3), "#FFFFFF", 3.4)
            else:
                painter.setPen(QPen(QColor("#D1D1D6"), 1.5))
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawEllipse(node)
            painter.setPen(QColor(INK if reached else SUBTLE))
            painter.drawText(QRectF(x - 40, y + 12, 80, 18), Qt.AlignmentFlag.AlignCenter, name)

    def mouseMoveEvent(self, event):
        x = event.position().x()
        for index, (step, name) in enumerate(zip(self.steps, self.names, strict=True)):
            if abs(x - self.node_x(index)) < 18:
                status = "已达成" if self.value >= step else f"还差 {number(step - self.value)} {self.unit}"
                self.hovercard.show_at(event.globalPosition().toPoint(), name, status)
                return
        self.hovercard.hide()

    def leaveEvent(self, event):
        self.hovercard.hide()
        super().leaveEvent(event)


def value_row(name: str) -> tuple[QHBoxLayout, QLabel]:
    row = QHBoxLayout()
    row.addWidget(label(name, "rowLabel"))
    row.addStretch()
    value = label("", "rowValue")
    row.addWidget(value)
    return row, value


class Overview(QWidget):
    """Everything on the overview page; refresh() redraws it from the stats store."""

    open_text = Signal()
    copy_latest = Signal()
    insert_latest = Signal()
    refresh_balance = Signal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet(DASHBOARD_STYLE)
        self.hovercard = HoverCard()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        summary = QFrame()
        summary.setObjectName("panel")
        stats = QHBoxLayout(summary)
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(0)
        self.today = Stat("今天")
        self.streak = Stat("连续使用")
        self.total = Stat("累计")
        self.saved = Stat("省下的打字时间")
        for index, stat in enumerate((self.today, self.streak, self.total, self.saved)):
            if index:
                line = vline()
                line.setFixedHeight(56)
                stats.addWidget(line, 0, Qt.AlignmentFlag.AlignVCenter)
            stats.addWidget(stat, 1)
        layout.addWidget(summary)

        heat, heat_layout, heat_head = panel("说话热力图")
        self.heat_summary = label("", "panelMeta")
        heat_head.addWidget(self.heat_summary)
        self.heatmap = Heatmap(self.hovercard)
        heat_layout.addWidget(self.heatmap)
        foot = QHBoxLayout()
        foot.addWidget(label("每格一天，颜色越深说得越多", "statSub"))
        foot.addStretch()
        foot.addWidget(Legend())
        heat_layout.addLayout(foot)
        layout.addWidget(heat)

        row = QHBoxLayout()
        row.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        latest, latest_layout, latest_head = panel("最近一次")
        self.latest_meta = label("", "panelMeta")
        latest_head.addWidget(self.latest_meta)
        self.latest_text = label("", "latestText", wrap=True)
        self.latest_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.latest_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.latest_text.setMinimumHeight(44)
        latest_layout.addWidget(self.latest_text, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_button = make_button("复制", Glyph.COPY)
        self.copy_button.clicked.connect(self.copy_latest)
        self.insert_button = make_button("输入到其他窗口", Glyph.SEND, "primary")
        self.insert_button.clicked.connect(self.insert_latest)
        compare = make_button("原文对照", Glyph.CHEVRON_RIGHT, "link")
        compare.clicked.connect(self.open_text)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.insert_button)
        actions.addStretch()
        actions.addWidget(compare)
        latest_layout.addLayout(actions)
        left.addWidget(latest, 1)

        milestones, milestone_layout, milestone_head = panel("里程碑")
        self.best = label("", "panelMeta")
        milestone_head.addWidget(self.best)
        self.chars_value = label("", "trackValue")
        self.streak_value = label("", "trackValue")
        self.milestones = [
            MilestoneTrack(
                self.hovercard, (1_000, 10_000, 100_000, 1_000_000), ("1 千字", "1 万字", "10 万字", "100 万字"), "字"
            ),
            MilestoneTrack(self.hovercard, (3, 7, 30, 100), ("3 天", "7 天", "30 天", "100 天"), "天"),
        ]
        for title, value, track in zip(
            ("累计字数", "最长连续使用"), (self.chars_value, self.streak_value), self.milestones, strict=True
        ):
            head = QHBoxLayout()
            head.addWidget(label(title, "trackTitle"))
            head.addStretch()
            head.addWidget(value)
            milestone_layout.addLayout(head)
            milestone_layout.addWidget(track)
        left.addWidget(milestones)
        row.addLayout(left, 3)

        usage, usage_layout, usage_head = panel("API 用量")
        self.provider = label("", "panelMeta")
        usage_head.addWidget(self.provider)
        self.balance = QLabel()
        families = ", ".join(f"'{family}'" for family in NUMBER_FAMILIES)
        self.balance.setStyleSheet(f"color: {INK}; font-family: {families}; font-size: 30px; font-weight: 600;")
        balance_row = QHBoxLayout()
        figures = QVBoxLayout()
        figures.setSpacing(2)
        figures.addWidget(self.balance)
        self.balance_caption = label("", "statSub")
        figures.addWidget(self.balance_caption)
        balance_row.addLayout(figures, 1)
        self.refresh_button = make_button("刷新", Glyph.REFRESH, "link")
        self.refresh_button.clicked.connect(self.refresh_balance)
        balance_row.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignTop)
        usage_layout.addLayout(balance_row)
        rows = QVBoxLayout()
        rows.setSpacing(8)
        line, self.requests = value_row("本月请求")
        rows.addLayout(line)
        line, self.tokens = value_row("本月 tokens")
        rows.addLayout(line)
        line, self.cost = value_row("本月估算花费")
        rows.addLayout(line)
        line, self.today_tokens = value_row("今天 tokens")
        rows.addLayout(line)
        line, self.average = value_row("平均每次")
        rows.addLayout(line)
        usage_layout.addLayout(rows)
        usage_layout.addStretch()
        self.bars = MiniBars(self.hovercard)
        usage_layout.addWidget(self.bars, 1)
        usage_layout.addWidget(label("最近 14 天每天的 tokens", "statSub"))
        row.addWidget(usage, 2)
        layout.addLayout(row)
        self.set_latest("", "")

    def hideEvent(self, event):
        self.hovercard.hide()
        super().hideEvent(event)

    # ---------- data ----------
    def set_latest(self, text: str, meta: str):
        if text.strip():
            shown = text if len(text) <= 220 else text[:220].rstrip() + "…"
            self.latest_text.setObjectName("latestText")
            self.latest_text.setText(shown)
        else:
            self.latest_text.setObjectName("latestEmpty")
            self.latest_text.setText("按住快捷键说一句话，整理好的文字会出现在这里，并直接输入到光标处。")
        self.latest_text.style().unpolish(self.latest_text)
        self.latest_text.style().polish(self.latest_text)
        self.latest_meta.setText(meta)
        self.copy_button.setEnabled(bool(text.strip()))
        self.insert_button.setEnabled(bool(text.strip()))

    def set_balance(self, balance: str | None, provider: str, updated: str = ""):
        self.provider.setText(provider)
        if balance:
            self.balance.setText(balance)
            self.balance_caption.setText(f"账户余额{(' · ' + updated) if updated else ''}")
        else:
            self.balance.setText("—")
            self.balance_caption.setText("这个服务不提供余额查询" if provider and provider != "DeepSeek" else "暂未获取余额")
        self.refresh_button.setVisible(provider == "DeepSeek")

    def refresh(self, store: StatsStore | None, today: date, deepseek: bool, enabled: bool = True):
        store = store or StatsStore.empty()
        day = store.day(today)
        formatter, unit = scaled(day.chars, "字")
        self.today.set(day.chars, unit, f"{day.dictations} 次 · {duration(day.seconds)}语音", formatter)
        self.streak.set(store.streak(today), "天", f"最长连续 {store.longest_streak()} 天")
        total = store.total()
        formatter, unit = scaled(total.chars, "字")
        self.total.set(total.chars, unit, f"{total.dictations:,} 次 · 活跃 {store.active_days()} 天", formatter)
        minutes = store.saved_minutes()
        if minutes >= 60:
            self.saved.set(minutes / 60, "小时", f"按每分钟打 {TYPING_CHARS_PER_MINUTE} 字估算", lambda v: f"{v:.1f}")
        else:
            self.saved.set(minutes, "分钟", f"按每分钟打 {TYPING_CHARS_PER_MINUTE} 字估算", lambda v: f"{v:.0f}")

        self.heatmap.set_data(store.days, today)
        year_start = today - timedelta(days=365)
        year = store.total(year_start)
        active = sum(1 for key, value in store.days.items() if value.dictations and date.fromisoformat(key) > year_start)
        self.heat_summary.setText(
            f"过去一年说了 {number(year.chars)} 字 · 活跃 {active} 天" if enabled else "使用统计已关闭，可在设置中开启"
        )

        month = store.total(today.replace(day=1))
        self.requests.setText(f"{month.requests:,} 次")
        self.tokens.setText(number(month.prompt_tokens + month.completion_tokens))
        self.tokens.setToolTip(f"输入 {month.prompt_tokens:,} · 输出 {month.completion_tokens:,}")
        self.cost.setText(f"约 ¥{month.cost:.2f}" if deepseek else "—")
        self.cost.setToolTip("按 DeepSeek 官方价格和请求时段估算，以账单为准" if deepseek else "该服务的价格未知")
        self.today_tokens.setText(number(day.prompt_tokens + day.completion_tokens))
        self.average.setText(
            f"{(month.prompt_tokens + month.completion_tokens) / month.requests:,.0f} tokens" if month.requests else "—"
        )
        recent = [today - timedelta(days=offset) for offset in range(13, -1, -1)]
        self.bars.set_data([(d, store.day(d).prompt_tokens + store.day(d).completion_tokens) for d in recent])

        best_day, best_chars = store.best_day()
        if best_chars:
            when = date.fromisoformat(best_day)
            self.best.setText(f"最多的一天 {when.month}月{when.day}日 · {best_chars:,} 字")
        else:
            self.best.setText("")
        self.chars_value.setText(f"{number(total.chars)} 字")
        self.streak_value.setText(f"{store.longest_streak()} 天")
        self.milestones[0].set_value(total.chars)
        self.milestones[1].set_value(store.longest_streak())
