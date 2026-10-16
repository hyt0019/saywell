"""The overview page: daily speaking heatmap, totals, API usage and achievements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .stats import TYPING_CHARS_PER_MINUTE, Day, StatsStore
from .ui import ACCENT, INK, MUTED, Glyph, glyph_pixmap, icon_font, label, make_button

WEEKDAYS = "一二三四五六日"
# Empty, then four levels of the accent blue.
HEAT = ("#ECEEF2", "#CDE2FC", "#8DBDF8", "#3D8BEF", "#0A5BD3")
HEAT_STEPS = (1, 300, 1000, 3000)

DASHBOARD_STYLE = """
QFrame#tile { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 18px; }
QFrame#panel { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 18px; }
QLabel#tileCaption { color: #6E6E73; font-size: 13px; }
QLabel#tileSub { color: #8E8E93; font-size: 12px; }
QLabel#panelTitle { font-size: 15px; font-weight: 700; }
QLabel#latestText { font-size: 15px; color: #1D1D1F; }
QLabel#latestEmpty { font-size: 14px; color: #8E8E93; }
QLabel#bigNumber { font-size: 30px; font-weight: 700; color: #1D1D1F; }
QLabel#rowLabel { color: #6E6E73; font-size: 13px; }
QLabel#rowValue { color: #1D1D1F; font-size: 13px; font-weight: 700; }
QLabel#providerChip { background: #F2F2F7; color: #6E6E73; border-radius: 9px; padding: 2px 9px; font-size: 12px; }
QProgressBar#milestone { background: #EEF0F4; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar#milestone::chunk { border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:0.6 #8B5CF6, stop:1 #EC4899); }
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


def metric(value: str, unit: str) -> str:
    return (
        f"<span style='font-size:28px; font-weight:700; color:{INK}'>{value}</span>"
        f"<span style='font-size:13px; color:{MUTED}'>&nbsp;{unit}</span>"
    )


def soft_badge(glyph: str, color: str, background: str, size: int = 34) -> QLabel:
    badge = QLabel()
    badge.setFixedSize(size, size)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setPixmap(glyph_pixmap(glyph, color, 16))
    badge.setStyleSheet(f"background: {background}; border-radius: {size // 3}px;")
    return badge


class StatTile(QFrame):
    def __init__(self, caption: str, glyph: str, color: str, background: str):
        super().__init__()
        self.setObjectName("tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(soft_badge(glyph, color, background, 28))
        head.addWidget(label(caption, "tileCaption"))
        head.addStretch()
        layout.addLayout(head)
        self.value = QLabel()
        self.value.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.value)
        self.sub = label("", "tileSub")
        layout.addWidget(self.sub)

    def set(self, value: str, unit: str, sub: str):
        self.value.setText(metric(value, unit))
        self.sub.setText(sub)


class Heatmap(QWidget):
    """GitHub-style year of days; darker means more characters spoken."""

    WEEKS = 53
    TOP = 20
    LEFT = 22
    GAP = 3

    def __init__(self):
        super().__init__()
        self.days: dict[str, Day] = {}
        self.today = date.today()
        self.cell = 13.0
        self.setMouseTracking(True)
        self.setMinimumHeight(self.TOP + 7 * (11 + self.GAP))
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

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        step, offset = self.geometry_for()
        cell = step - self.GAP
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#8E8E93"))
        for row in (0, 2, 4):
            y = self.TOP + row * step
            painter.drawText(QRectF(0, y, self.LEFT - 6, cell), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             WEEKDAYS[row])
        start = self.start
        last_month = None
        for week in range(self.WEEKS):
            first = start + timedelta(weeks=week)
            if first.month != last_month and week < self.WEEKS - 2:
                if last_month is not None or first.day <= 7:
                    painter.setPen(QColor("#8E8E93"))
                    painter.drawText(QRectF(offset + week * step, 0, 40, self.TOP - 4),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{first.month}月")
                last_month = first.month
            for row in range(7):
                day = first + timedelta(days=row)
                if day > self.today:
                    continue
                stats = self.days.get(day.isoformat())
                level = self.level(stats.chars if stats else 0)
                rect = QRectF(offset + week * step, self.TOP + row * step, cell, cell)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(HEAT[level]))
                painter.drawRoundedRect(rect, 3, 3)
                if day == self.today:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor(INK), 1.4))
                    painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 3.5, 3.5)

    def day_at(self, x: float, y: float) -> date | None:
        step, offset = self.geometry_for()
        week, row = int((x - offset) // step), int((y - self.TOP) // step)
        if not (0 <= week < self.WEEKS and 0 <= row < 7):
            return None
        day = self.start + timedelta(weeks=week, days=row)
        return day if day <= self.today else None

    def mouseMoveEvent(self, event):
        day = self.day_at(event.position().x(), event.position().y())
        if day is None:
            QToolTip.hideText()
            return
        stats = self.days.get(day.isoformat())
        when = f"{day.month}月{day.day}日 周{WEEKDAYS[day.weekday()]}"
        text = f"{when} · {stats.chars:,} 字 · {stats.dictations} 次" if stats and stats.dictations else f"{when} · 没有说话"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


class Legend(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(label("少", "tileSub"))
        for color in HEAT:
            swatch = QLabel()
            swatch.setFixedSize(11, 11)
            swatch.setStyleSheet(f"background: {color}; border-radius: 3px;")
            layout.addWidget(swatch)
        layout.addWidget(label("多", "tileSub"))


class MiniBars(QWidget):
    """Tokens per day for the last two weeks."""

    def __init__(self, days: int = 14):
        super().__init__()
        self.count = days
        self.values: list[tuple[date, int]] = []
        self.setFixedHeight(56)
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
            rect = QRectF(index * width + width * 0.18, self.height() - height, width * 0.64, height)
            last = index == len(self.values) - 1
            color = QColor(ACCENT if last else "#9CC3F5") if value else QColor("#ECEEF2")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 3, 3)

    def mouseMoveEvent(self, event):
        if not self.values:
            return
        index = int(event.position().x() // (self.width() / len(self.values)))
        if 0 <= index < len(self.values):
            day, value = self.values[index]
            QToolTip.showText(event.globalPosition().toPoint(), f"{day.month}月{day.day}日 · {value:,} tokens", self)


@dataclass(frozen=True)
class Achievement:
    title: str
    hint: str
    glyph: str
    colors: tuple[str, str]
    goal: Callable[[StatsStore], tuple[float, float]]  # (current, target)


ACHIEVEMENTS = (
    Achievement("开口", "第一次用语音输入", Glyph.MIC, ("#5AC8FA", "#0071E3"), lambda s: (s.total().dictations, 1)),
    Achievement("千字", "累计说了 1,000 字", "", ("#64D2FF", "#3B82F6"), lambda s: (s.total().chars, 1_000)),
    Achievement("连续 3 天", "连续 3 天使用", "", ("#FFD60A", "#FF9500"), lambda s: (s.longest_streak(), 3)),
    Achievement("单日千字", "一天说了 1,000 字", "", ("#FF9F0A", "#FF375F"), lambda s: (s.best_day()[1], 1_000)),
    Achievement("万字", "累计说了 1 万字", "", ("#A78BFA", "#8B5CF6"), lambda s: (s.total().chars, 10_000)),
    Achievement("连续 7 天", "连续 7 天使用", "", ("#FF9F0A", "#FF3B30"), lambda s: (s.longest_streak(), 7)),
    Achievement("十万字", "累计说了 10 万字", "", ("#F472B6", "#EC4899"), lambda s: (s.total().chars, 100_000)),
    Achievement("连续 30 天", "连续 30 天使用", "", ("#34D399", "#059669"), lambda s: (s.longest_streak(), 30)),
)


class Badge(QWidget):
    def __init__(self, achievement: Achievement):
        super().__init__()
        self.achievement = achievement
        self.earned = False
        self.progress = 0.0
        self.setFixedSize(64, 74)

    def set_progress(self, current: float, target: float):
        self.earned = current >= target
        self.progress = min(1.0, current / target if target else 0)
        left = max(0, target - current)
        status = "已达成" if self.earned else f"进度 {self.progress:.0%}，还差 {left:,.0f}"
        self.setToolTip(f"{self.achievement.hint} · {status}")
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QRectF(10, 2, 44, 44)
        if self.earned:
            gradient = QLinearGradient(circle.topLeft(), circle.bottomRight())
            gradient.setColorAt(0, QColor(self.achievement.colors[0]))
            gradient.setColorAt(1, QColor(self.achievement.colors[1]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(circle)
            glyph_color = QColor("#FFFFFF")
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F2F2F7"))
            painter.drawEllipse(circle)
            if self.progress > 0:
                pen = QPen(QColor(self.achievement.colors[1]), 3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawArc(circle.adjusted(1.5, 1.5, -1.5, -1.5), 90 * 16, int(-360 * 16 * self.progress))
            glyph_color = QColor("#AEAEB2")
        painter.setPen(glyph_color)
        painter.setFont(icon_font(18))
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, self.achievement.glyph)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(self.earned)
        painter.setFont(font)
        painter.setPen(QColor(INK if self.earned else "#8E8E93"))
        painter.drawText(QRectF(0, 50, self.width(), 20), Qt.AlignmentFlag.AlignCenter, self.achievement.title)


def panel(title: str, glyph: str, color: str, background: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 18, 22, 18)
    layout.setSpacing(12)
    head = QHBoxLayout()
    head.setSpacing(10)
    head.addWidget(soft_badge(glyph, color, background, 30))
    head.addWidget(label(title, "panelTitle"))
    head.addStretch()
    layout.addLayout(head)
    return frame, layout, head


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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.today = StatTile("今天", Glyph.MIC, ACCENT, "#E8F1FD")
        self.streak = StatTile("连续使用", "", "#FF9500", "#FFF4E5")
        self.total = StatTile("累计", "", "#8B5CF6", "#F3EEFF")
        self.saved = StatTile("省下的打字时间", "", "#28A745", "#E9F9EE")
        for tile in (self.today, self.streak, self.total, self.saved):
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)

        heat, heat_layout, heat_head = panel("说话热力图", "", ACCENT, "#E8F1FD")
        self.heat_summary = label("", "tileSub")
        heat_head.addWidget(self.heat_summary)
        self.heatmap = Heatmap()
        heat_layout.addWidget(self.heatmap)
        foot = QHBoxLayout()
        foot.addWidget(label("每个格子是一天，悬停查看当天说了多少字", "tileSub"))
        foot.addStretch()
        foot.addWidget(Legend())
        heat_layout.addLayout(foot)
        layout.addWidget(heat)

        row = QHBoxLayout()
        row.setSpacing(14)
        latest, latest_layout, latest_head = panel("最近一次", Glyph.EDIT, "#0071E3", "#E8F1FD")
        self.latest_meta = label("", "tileSub")
        latest_head.addWidget(self.latest_meta)
        self.latest_text = label("", "latestText", wrap=True)
        self.latest_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.latest_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.latest_text.setMinimumHeight(48)
        latest_layout.addWidget(self.latest_text, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_button = make_button("复制", Glyph.COPY)
        self.copy_button.clicked.connect(self.copy_latest)
        self.insert_button = make_button("输入到其他窗口", Glyph.SEND, "primary")
        self.insert_button.clicked.connect(self.insert_latest)
        compare = make_button("原文对照", None, "link", ACCENT)
        compare.clicked.connect(self.open_text)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.insert_button)
        actions.addStretch()
        actions.addWidget(compare)
        latest_layout.addLayout(actions)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(latest, 1)
        row.addLayout(left, 3)

        usage, usage_layout, usage_head = panel("API 用量", "", "#28A745", "#E9F9EE")
        self.provider = label("", "providerChip")
        self.provider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        usage_head.addWidget(self.provider, 0, Qt.AlignmentFlag.AlignVCenter)
        self.balance = label("", "bigNumber")
        self.balance_caption = label("", "tileSub")
        top = QHBoxLayout()
        figures = QVBoxLayout()
        figures.setSpacing(0)
        figures.addWidget(self.balance)
        figures.addWidget(self.balance_caption)
        top.addLayout(figures, 1)
        self.refresh_button = make_button("刷新", None, "link", ACCENT)
        self.refresh_button.clicked.connect(self.refresh_balance)
        top.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignTop)
        usage_layout.addLayout(top)
        rows = QVBoxLayout()
        rows.setSpacing(7)
        line, self.requests = value_row("本月请求")
        rows.addLayout(line)
        line, self.tokens = value_row("本月 tokens")
        rows.addLayout(line)
        line, self.cost = value_row("本月估算花费")
        self.cost_row = line
        rows.addLayout(line)
        usage_layout.addLayout(rows)
        self.bars = MiniBars()
        usage_layout.addWidget(self.bars)
        usage_layout.addWidget(label("最近 14 天每天的 tokens", "tileSub"))
        row.addWidget(usage, 2)
        layout.addLayout(row)

        achievements, achievement_layout, achievement_head = panel("成就", "", "#EC4899", "#FDEEF6")
        self.milestone_text = label("", "tileSub")
        achievement_head.addWidget(self.milestone_text)
        self.milestone = QProgressBar()
        self.milestone.setObjectName("milestone")
        self.milestone.setTextVisible(False)
        self.milestone.setRange(0, 1000)
        achievement_layout.addWidget(self.milestone)
        badges = QGridLayout()
        badges.setHorizontalSpacing(0)
        self.badges = [Badge(item) for item in ACHIEVEMENTS]
        for index, badge in enumerate(self.badges):
            badges.addWidget(badge, 0, index, Qt.AlignmentFlag.AlignCenter)
        achievement_layout.addLayout(badges)
        left.addWidget(achievements)
        self.set_latest("", "")

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
        self.provider.setVisible(bool(provider))
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
        self.today.set(number(day.chars), "字", f"{day.dictations} 次 · {duration(day.seconds)}语音")
        streak = store.streak(today)
        self.streak.set(str(streak), "天", f"最长连续 {store.longest_streak()} 天")
        total = store.total()
        self.total.set(number(total.chars), "字", f"{total.dictations:,} 次 · 活跃 {store.active_days()} 天")
        minutes = store.saved_minutes()
        hours = minutes / 60
        if hours >= 1:
            self.saved.set(f"{hours:.1f}", "小时", f"按每分钟打 {TYPING_CHARS_PER_MINUTE} 字估算")
        else:
            self.saved.set(f"{minutes:.0f}", "分钟", f"按每分钟打 {TYPING_CHARS_PER_MINUTE} 字估算")

        self.heatmap.set_data(store.days, today)
        year = store.total(today - timedelta(days=365))
        active = sum(
            1 for key, value in store.days.items() if value.dictations and date.fromisoformat(key) > today - timedelta(days=365)
        )
        self.heat_summary.setText(
            f"过去一年说了 {number(year.chars)} 字 · 活跃 {active} 天" if enabled else "使用统计已关闭，可在设置中开启"
        )

        month = store.total(today.replace(day=1))
        self.requests.setText(f"{month.requests:,} 次")
        self.tokens.setText(number(month.prompt_tokens + month.completion_tokens))
        self.tokens.setToolTip(f"输入 {month.prompt_tokens:,} · 输出 {month.completion_tokens:,}")
        self.cost.setText(f"约 ¥{month.cost:.2f}" if deepseek else "—")
        self.cost.setToolTip("按 DeepSeek 官方价格和请求时段估算，以账单为准" if deepseek else "该服务的价格未知")
        recent = [today - timedelta(days=offset) for offset in range(13, -1, -1)]
        self.bars.set_data([(d, store.day(d).prompt_tokens + store.day(d).completion_tokens) for d in recent])

        reached, upcoming = store.next_milestone()
        if upcoming:
            self.milestone.setValue(int(1000 * (total.chars - reached) / (upcoming - reached)))
            self.milestone_text.setText(f"距离累计 {number(upcoming)} 字还差 {number(upcoming - total.chars)} 字")
        else:
            self.milestone.setValue(1000)
            self.milestone_text.setText("所有字数里程碑都达成了")
        for badge in self.badges:
            badge.set_progress(*badge.achievement.goal(store))

