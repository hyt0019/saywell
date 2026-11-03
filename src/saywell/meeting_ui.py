"""The 会议 page: choose what to record, then watch the transcript grow.

MeetingHero takes the dictation card's place while the page is open: what will be recorded, a
meter for each source and the round start button. The page below it holds the live transcript.
"""

from __future__ import annotations

import bisect
import html
import threading
import time
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QFileInfo, QObject, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFileIconProvider,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .audio import AudioError
from .capture import AudioApp, audio_apps, loopback_supported
from .icons import draw_icon
from .meeting import LABELS, ME, OTHERS, ROOM, SKIPPED, Line, MeetingRecorder, MeetingStore, clock
from .minutes import PREPARE_FROM, TEMPLATES, Minutes, MinutesWriter, minutes_record, outline, to_html, to_text
from .models import get_model, models_ready
from .ui import (
    ACCENT,
    DISABLED,
    INK,
    MUTED,
    ComboBox,
    Glyph,
    HeroCard,
    LevelBars,
    MicButton,
    Spinner,
    Switch,
    Tween,
    card,
    label,
    make_button,
    mix,
    run_task,
)

TONES = {"idle": "#AEAEB2", "rec": "#FF3B30", "busy": "#FF9F0A", "done": "#34C759"}
FAINT = "#8E8E93"


def app_picture(path: str, size: int = 32) -> QPixmap | None:
    """The app's own icon, from its executable."""
    if not path:
        return None
    pixmap = QFileIconProvider().icon(QFileInfo(path)).pixmap(size, size)
    return None if pixmap.isNull() else pixmap


def meeting_title(moment: datetime) -> str:
    return f"{moment.month}月{moment.day}日 {moment:%H:%M} 的会议"


def duration_text(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分钟" if hours else f"{minutes} 分钟"


def app_note(app: AudioApp) -> str:
    if app.browser:
        return "浏览器 · 会录下所有标签页的声音"
    if app.playing:
        return "正在播放声音"
    return "会议软件" if app.meeting else "最近播放过声音"


# --- Choosing the app ---------------------------------------------------------------------------


class PickerRow(QAbstractButton):
    """One choice in the picker: icon, name, a grey note, and a tick when it is the current one."""

    HEIGHT = 50

    def __init__(self, title: str, note: str, picture: QPixmap | None = None, glyph: str = "", chosen: bool = False):
        super().__init__()
        self.title, self.note, self.picture, self.glyph, self.chosen = title, note, picture, glyph, chosen
        self.hover = Tween(self, duration=140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self.HEIGHT)

    def sizeHint(self):
        return QSize(320, self.HEIGHT)

    def enterEvent(self, event):
        self.hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0.0, 220)
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.hover.value > 0.01:
            painter.setBrush(mix(QColor("#FFFFFF"), QColor("#F0F0F4"), self.hover.value))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(4, 1, -4, -1), 10, 10)
        icon = QRectF(16, (self.height() - 28) / 2, 28, 28)
        if self.picture:
            painter.drawPixmap(icon, self.picture, QRectF(self.picture.rect()))
        else:
            painter.setBrush(QColor("#F2F2F7"))
            painter.drawRoundedRect(icon, 8, 8)
            if self.glyph:
                draw_icon(painter, self.glyph, icon.adjusted(6, 6, -6, -6), MUTED)
            else:  # An app whose icon Windows would not give: its initial.
                initial = QFont(self.font())
                initial.setPixelSize(14)
                initial.setBold(True)
                painter.setFont(initial)
                painter.setPen(QColor(MUTED))
                painter.drawText(icon, Qt.AlignmentFlag.AlignCenter, self.title[:1].upper())
        text_left = icon.right() + 12
        right = self.width() - (40 if self.chosen else 16)
        title = QFont(self.font())
        title.setPixelSize(14)
        painter.setFont(title)
        painter.setPen(QColor(INK))
        top = 7 if self.note else 0
        height = 20 if self.note else self.height()
        painter.drawText(QRectF(text_left, top, right - text_left, height or self.height()),
                         Qt.AlignmentFlag.AlignVCenter, self.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(right - text_left)))
        if self.note:
            note = QFont(self.font())
            note.setPixelSize(12)
            painter.setFont(note)
            painter.setPen(QColor(FAINT))
            painter.drawText(QRectF(text_left, 26, right - text_left, 18), Qt.AlignmentFlag.AlignVCenter, self.note)
        if self.chosen:
            draw_icon(painter, Glyph.CHECK, QRectF(self.width() - 34, (self.height() - 16) / 2, 16, 16), ACCENT)


class AppPicker(QFrame):
    """A popup listing what a meeting can record: meeting apps first, then other apps playing
    sound, and the microphone alone for a meeting in a room."""

    chosen = Signal(object)  # The AudioApp, or None for the microphone alone.

    def __init__(self, apps: list[AudioApp], current: str, supported: bool, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 18)
        panel = QFrame()
        panel.setObjectName("pickerPanel")
        panel.setStyleSheet("QFrame#pickerPanel { background: #FFFFFF; border: 1px solid #E3E3E8; border-radius: 14px; }")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 38))
        panel.setGraphicsEffect(shadow)
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 10, 4, 6)
        layout.setSpacing(0)
        caption = label("会议的声音来自", "caption")
        caption.setContentsMargins(16, 0, 16, 6)
        layout.addWidget(caption)
        if not supported:
            note = label("这台电脑的 Windows 版本较旧，只能录麦克风。单独录制一个应用的声音需要 Windows 10 2004 或更新版本。",
                         "caption", wrap=True)
            note.setContentsMargins(16, 0, 16, 8)
            layout.addWidget(note)
        elif not apps:
            note = label("没有找到能录的应用。打开会议软件、进入会议后再点这里。", "caption", wrap=True)
            note.setContentsMargins(16, 0, 16, 8)
            layout.addWidget(note)
        for app in apps[:9] if supported else []:
            row = PickerRow(app.name, app_note(app), app_picture(app.path, 56), chosen=app.exe.lower() == current.lower())
            row.clicked.connect(lambda _checked=False, app=app: self._choose(app))
            layout.addWidget(row)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background: #EDEDF0; margin: 0 16px;")
        layout.addSpacing(5)
        layout.addWidget(rule)
        layout.addSpacing(5)
        alone = PickerRow("不录应用，只录麦克风", "线下会议：录下会议室里每个人的声音", glyph=Glyph.MIC, chosen=not current)
        alone.clicked.connect(lambda: self._choose(None))
        layout.addWidget(alone)
        self.setFixedWidth(372)

    def _choose(self, app: AudioApp | None):
        self.chosen.emit(app)
        self.close()

    def show_below(self, anchor: QWidget):
        self.adjustSize()
        point = anchor.mapToGlobal(QPoint(-14, anchor.height() - 4))
        screen = (anchor.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        x = max(screen.left(), min(point.x(), screen.right() - self.width()))
        y = point.y() if point.y() + self.height() <= screen.bottom() else anchor.mapToGlobal(QPoint(0, 0)).y() - self.height()
        self.move(x, y)
        self.show()


# --- The transcript -----------------------------------------------------------------------------


class EdgeFade(QWidget):
    """White fading to clear over the edge of a scrolling text, so lines slide under the card's
    title instead of being cut off by it."""

    def __init__(self, parent: QWidget, top: bool):
        super().__init__(parent)
        self.top = top
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        solid, clear = QColor(255, 255, 255, 255), QColor(255, 255, 255, 0)
        gradient.setColorAt(0.0, solid if self.top else clear)
        gradient.setColorAt(1.0, clear if self.top else solid)
        painter.fillRect(self.rect(), gradient)


class TranscriptView(QTextEdit):
    """The meeting's lines in the order they were said, one paragraph each. A small heading with
    who and when starts each turn: a new speaker, or a minute after the last heading. Lines that
    arrive late (the microphone waits for the others to be recognized) go where they belong."""

    TURN = 60.0

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Selectable with the mouse only: keyboard selection would show a blinking caret in it.
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet("QTextEdit { background: transparent; border: none; padding: 0 10px 10px 14px;"
                           " selection-background-color: #CCE0FA; }")
        self.document().setDocumentMargin(4)
        self.lines: list[Line] = []
        self.headed: list[bool] = []
        self.labels = True
        self.fades = (EdgeFade(self, True), EdgeFade(self, False))
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._place_fades)
        bar.rangeChanged.connect(self._place_fades)
        self._place_fades()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_fades()

    def _place_fades(self, *_):
        """A fade only where there is more text past that edge."""
        area = self.viewport().geometry()
        bar = self.verticalScrollBar()
        self.fades[0].setGeometry(area.left(), area.top(), area.width(), 14)
        self.fades[1].setGeometry(area.left(), area.bottom() - 9, area.width(), 10)
        self.fades[0].setVisible(bar.value() > bar.minimum())
        self.fades[1].setVisible(bar.value() < bar.maximum())

    def reset(self, labels: bool, placeholder: str = ""):
        self.clear()
        self.lines, self.headed = [], []
        self.labels = labels
        self.setPlaceholderText(placeholder)

    def characters(self) -> int:
        return sum(len(line.text) for line in self.lines)

    def add(self, line: Line) -> None:
        bar = self.verticalScrollBar()
        following = bar.value() >= bar.maximum() - 4
        index = bisect.bisect_right([(item.start, item.end) for item in self.lines], (line.start, line.end))
        self.lines.insert(index, line)
        self.headed.insert(index, False)
        cursor = QTextCursor(self.document())
        if len(self.lines) == 1:
            cursor.movePosition(QTextCursor.MoveOperation.Start)
        elif index == len(self.lines) - 1:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertBlock()
        else:
            cursor.setPosition(self.document().findBlockByNumber(index).position())
            cursor.insertBlock()
            cursor.movePosition(QTextCursor.MoveOperation.PreviousBlock)
        self._write(cursor, index)
        # A heading further on may now be one too many, or missing.
        for later in range(index + 1, len(self.lines)):
            if self._heading(later) == self.headed[later]:
                continue
            block = self.document().findBlockByNumber(later)
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self._write(cursor, later)
        if following:
            bar.setValue(bar.maximum())

    def _heading(self, index: int) -> bool:
        if index == 0:
            return True
        line, previous = self.lines[index], self.lines[index - 1]
        if line.who != previous.who and self.labels or SKIPPED in (line.who, previous.who):
            return True
        last = next(i for i in range(index - 1, -1, -1) if self.headed[i])
        return line.start - self.lines[last].start >= self.TURN

    def _write(self, cursor: QTextCursor, index: int) -> None:
        line = self.lines[index]
        headed = self.headed[index] = self._heading(index)
        block = QTextBlockFormat()
        block.setTopMargin(14 if headed and index else 2)
        block.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setBlockFormat(block)
        if headed:
            who = LABELS.get(line.who, "") if self.labels else ""
            if who:
                name = QTextCharFormat()
                name.setFontPointSize(9.5)
                name.setFontWeight(QFont.Weight.DemiBold)
                name.setForeground(QColor(ACCENT if line.who == ME else "#3A3A3C"))
                cursor.insertText(who + "   ", name)
            when = QTextCharFormat()
            when.setFontPointSize(9)
            when.setForeground(QColor(FAINT))
            cursor.insertText(clock(line.start), when)
            cursor.insertText(" ", when)
        text = QTextCharFormat()
        text.setFontPointSize(11.5)
        if line.who == SKIPPED:
            text.setForeground(QColor(FAINT))
            text.setFontItalic(True)
            cursor.insertText(f"（约 {max(1, round(line.end - line.start))} 秒没来得及识别）", text)
        else:
            text.setForeground(QColor(INK))
            cursor.insertText(line.text, text)

    def plain_text(self) -> str:
        """The transcript for copying: a line per utterance, with time and speaker."""
        rows = []
        for line in self.lines:
            if line.who == SKIPPED:
                continue
            who = LABELS.get(line.who, "") if self.labels else ""
            rows.append(f"[{clock(line.start)}] {who}：{line.text}" if who else f"[{clock(line.start)}] {line.text}")
        return "\n".join(rows)


# --- The page -----------------------------------------------------------------------------------


def card_column(title: str, accent: bool) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    """A white card with a titled header row, as on the text page; returns the card, its column
    and the header row (for whatever goes on the right of the title)."""
    frame = card(accent)
    column = QVBoxLayout(frame)
    column.setContentsMargins(8, 16, 8, 6)
    column.setSpacing(8)
    head = QHBoxLayout()
    head.setContentsMargins(14, 0, 12, 0)
    head.setSpacing(9)
    dot = QLabel()
    dot.setFixedSize(9, 9)
    dot.setStyleSheet(f"background: {ACCENT if accent else DISABLED}; border-radius: 4px;")
    head.addWidget(dot)
    head.addWidget(label(title, "cardTitle"))
    head.addStretch()
    column.addLayout(head)
    return frame, column, head


class MinutesView(QTextBrowser):
    """The minutes as a document: read-only, selectable, links off."""

    EMPTY = (
        "开会途中或会议结束后，选一个模板，点“生成纪要”。",
        "纪要只根据会议文字来写，没听清、没说定的地方会列进“待确认”。生成时会把会议文字发送给所选 API；"
        "会议超过一小时后，已经说完的部分会每隔约 15 分钟先发去整理，结束时生成得更快。",
    )
    WRITING = ("正在根据会议文字生成纪要，一般要十几秒，会议很长时会久一些。", "可以继续记录，不影响识别。")

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0 10px 10px 14px;"
                           " font-size: 14px; selection-background-color: #CCE0FA; }")
        self.empty(self.EMPTY)

    def empty(self, paragraphs: tuple[str, ...]):
        """Grey explanatory text in place of the minutes."""
        self.setHtml("".join(
            f"<p style='color:{MUTED}; margin:0 0 10px 0; line-height:150%'>{html.escape(text)}</p>"
            for text in paragraphs
        ))


class MeetingEvents(QObject):
    """Brings the recorder's callbacks, made on its threads, to the interface thread."""

    line = Signal(object)
    problem = Signal(str)
    finished = Signal()


class MeetingHero(HeroCard):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 18, 22, 16)
        layout.setSpacing(20)
        column = QVBoxLayout()
        column.setSpacing(6)
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        self.pill.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.pill)
        self.status = label("记录一场会议", "status")
        column.addWidget(self.status)
        self.hint = label("", "heroHint", wrap=True)
        column.addWidget(self.hint)
        column.addSpacing(6)
        sources = QWidget()
        sources.setFixedHeight(34)
        row = QHBoxLayout(sources)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.app_chip = make_button("选择会议软件", None, "heroChip")
        self.app_chip.set_trailing(Glyph.CHEVRON_DOWN)
        self.app_chip.setToolTip("选择要记录哪个应用的声音")
        row.addWidget(self.app_chip)
        self.app_meter = LevelBars(7)
        self.app_meter.setFixedSize(46, 22)
        self.app_meter.setMinimumHeight(22)
        row.addWidget(self.app_meter)
        row.addSpacing(6)
        self.dot = label("·", "heroMeta")
        row.addWidget(self.dot)
        row.addSpacing(6)
        self.mic_switch = Switch("我的麦克风")
        self.mic_switch.setToolTip("同时记录你自己说的话")
        row.addWidget(self.mic_switch)
        self.mic_meter = LevelBars(7)
        self.mic_meter.setFixedSize(46, 22)
        self.mic_meter.setMinimumHeight(22)
        row.addWidget(self.mic_meter)
        row.addStretch()
        column.addWidget(sources)
        layout.addLayout(column, 1)
        controls = QVBoxLayout()
        controls.setSpacing(4)
        controls.addStretch()
        self.button = MicButton("开始记录")
        self.button.setObjectName("primary")
        controls.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        # The same height as the dictation card's cancel button, so the two cards match.
        spacer = QWidget()
        spacer.setFixedHeight(30)
        controls.addWidget(spacer)
        controls.addStretch()
        layout.addLayout(controls)

    def set_pill(self, tone: str, text: str):
        self.pill.setText(f"<span style='color:{TONES[tone]}'>●</span>&nbsp;&nbsp;{text}")


class MeetingPage(QWidget):
    """The transcript below MeetingHero, and the meeting itself: sources, recorder and state.
    `state` is idle, recording, finishing (recognizing what was heard after stopping) or done."""

    recording_changed = Signal(bool)

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.hero = MeetingHero()
        self.events = MeetingEvents()
        self.events.line.connect(self.on_line)
        self.events.problem.connect(self.on_problem)
        self.events.finished.connect(self.on_finished)
        self.recorder: MeetingRecorder | None = None
        self.store: MeetingStore | None = None
        self.state = "idle"
        self.app: AudioApp | None = None
        self.chose = False  # The user picked a source on this page (not just the remembered one).
        self.ended = 0.0
        self.duration = 0.0
        self.silent_notice = ""
        self.supported = loopback_supported()
        self.minutes: Minutes | None = None
        self.writing: threading.Event | None = None  # Set to cancel the minutes being written.
        self.preparing = False  # Notes on a long meeting's finished parts are being written.
        self.parts: dict = {}  # Those notes, for the meeting in self.store (saved as parts.dat).
        self.stopping = threading.Event()  # Set on quitting, to stop writing notes.
        self.next_prepare = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(16)
        splitter.setChildrenCollapsible(False)
        frame, column, head = card_column("会议文字", False)
        self.progress = label("", "caption")
        head.addWidget(self.progress)
        self.count = label("0 字", "count")
        head.addWidget(self.count)
        self.transcript = TranscriptView()
        self.transcript.reset(True, "开始记录后，会议里说的话会实时出现在这里。")
        column.addWidget(self.transcript, 1)
        splitter.addWidget(frame)
        frame, column, head = card_column("会议纪要", True)
        self.spinner = Spinner(14)
        self.spinner.hide()
        head.addWidget(self.spinner)
        self.minutes_status = label("", "caption")
        head.addWidget(self.minutes_status)
        self.minutes_view = MinutesView()
        column.addWidget(self.minutes_view, 1)
        splitter.addWidget(frame)
        splitter.setSizes([560, 460])
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_button = make_button("复制全文", Glyph.COPY, "ghost")
        self.copy_button.clicked.connect(self.copy_all)
        actions.addWidget(self.copy_button)
        actions.addStretch()
        self.copy_minutes_button = make_button("复制纪要", Glyph.COPY, "ghost")
        self.copy_minutes_button.clicked.connect(self.copy_minutes)
        actions.addWidget(self.copy_minutes_button)
        self.template_box = ComboBox()
        for template in TEMPLATES:
            self.template_box.addItem(template.name, template.id)
        chosen = next((i for i, t in enumerate(TEMPLATES) if t.id == main.settings.meeting_template), 0)
        self.template_box.setCurrentIndex(chosen)
        self.template_box.setToolTip("纪要的写法：通用会议、项目例会、客户沟通、讨论决策或课堂讲座")
        self.template_box.currentIndexChanged.connect(
            lambda _index: self._save(meeting_template=self.template_box.currentData())
        )
        actions.addWidget(self.template_box)
        self.write_button = make_button("生成纪要", Glyph.EDIT, "primary")
        self.write_button.clicked.connect(self.write_minutes)
        actions.addWidget(self.write_button)
        layout.addLayout(actions)

        self.hero.app_chip.clicked.connect(self.pick_app)
        self.hero.mic_switch.setChecked(main.settings.meeting_microphone)
        self.hero.mic_switch.toggled.connect(self.mic_toggled)
        self.hero.button.clicked.connect(self.toggle)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.tick)
        self.refresh()

    # Sources

    def remember_app(self):
        """Pick the remembered app if it runs, else a meeting app in a call, else leave it open."""
        if self.state != "idle" or self.chose or not self.supported:
            return
        apps = audio_apps()
        saved = self.main.settings.meeting_app.lower()
        found = next((app for app in apps if saved and app.exe.lower() == saved), None)
        if found is None and saved != "-":
            found = next((app for app in apps if app.meeting and app.playing), None)
        self.app = found
        self.refresh()

    def pick_app(self):
        apps = audio_apps() if self.supported else []
        current = self.app.exe if self.app else ""
        picker = AppPicker(apps, current, self.supported, self)
        picker.chosen.connect(self.choose_app)
        picker.show_below(self.hero.app_chip)

    def choose_app(self, app: AudioApp | None):
        self.app = app
        self.chose = True
        if app is None and not self.hero.mic_switch.isChecked():
            self.hero.mic_switch.setChecked(True)  # The microphone alone was chosen.
        self._save(meeting_app=app.exe if app else "-")
        self.refresh()

    def mic_toggled(self, on: bool):
        self._save(meeting_microphone=on)
        self.refresh()

    def _save(self, **changes):
        window = self.main
        window.settings = replace(window.settings, **changes)
        try:
            window.settings.save()
        except OSError:
            pass

    # Recording

    def ready(self) -> tuple[bool, str]:
        settings = self.main.settings
        if not models_ready(settings.models, model=settings.asr_model):
            return False, f"先在“语音模型”里下载 {get_model(settings.asr_model).name}，会议文字在本机识别。"
        if self.app is None and not self.hero.mic_switch.isChecked():
            return False, "先选择会议软件，或打开“我的麦克风”。"
        return True, ""

    def toggle(self):
        if self.state == "recording":
            self.stop()
        elif self.state in ("idle", "done"):
            self.start()

    def start(self):
        ok, why = self.ready()
        if not ok:
            self.main.set_notice(why)
            return
        from .vocabulary import Vocabulary

        window = self.main
        mic = self.hero.mic_switch.isChecked()
        app = self.app
        labels = bool(app and mic)
        settings = window.settings
        vocabulary = Vocabulary.for_settings(settings)
        started = time.time()
        info = {
            "started": started,
            "title": meeting_title(datetime.fromtimestamp(started)),
            "app": app.name if app else "",
            "exe": app.exe if app else "",
            "microphone": mic,
            "labels": labels,
            "model": get_model(settings.asr_model).name,
        }
        try:
            self.store = MeetingStore.create(info)
        except OSError:
            window.set_notice("无法创建会议记录文件夹，请检查磁盘空间和权限。")
            return
        recorder = MeetingRecorder(
            self.store,
            settings.models,
            lambda clip: window.pipeline.recognize_clip(window.settings, clip, vocabulary),
            self.events.line.emit,
            self.events.problem.emit,
            self.events.finished.emit,
        )
        try:
            if app:
                recorder.add_app(app.pids, app.exe)
            if mic:
                recorder.add_microphone(settings.microphone, ME if app else ROOM)
        except AudioError as error:
            recorder.close(0)
            self._discard_store()
            window.set_notice(str(error))
            return
        recorder.start()
        self.recorder = recorder
        self.ended = self.duration = 0.0
        self.silent_notice = ""
        self.parts = {}
        self.next_prepare = time.monotonic() + 60
        self.show_minutes(None)
        self.state = "recording"
        window.set_notice("")
        self.transcript.reset(labels, "正在听。有人说话时，文字会在几秒内出现在这里。")
        self.timer.start()
        self.refresh()
        self.recording_changed.emit(True)

    def _discard_store(self):
        import shutil

        if self.store is not None:
            shutil.rmtree(self.store.folder, ignore_errors=True)
            self.store = None

    def stop(self):
        if self.recorder is None or self.state != "recording":
            return
        self.recorder.stop()
        self.ended = time.time()
        self.state = "finishing"
        self.refresh()

    def on_line(self, line: Line):
        self.transcript.add(line)
        self.refresh_count()

    def on_problem(self, message: str):
        self.main.set_notice(message)

    def on_finished(self):
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        if self.store is not None:
            info = self.store.info()
            ended = self.ended or time.time()
            self.duration = ended - info.get("started", ended)
            info.update(ended=ended, duration=self.duration, characters=self.transcript.characters())
            try:
                self.store.save_info(info)
            except OSError:
                pass
            if not self.transcript.lines:
                self._discard_store()  # Nothing was said: no empty meeting in the history.
        self.state = "done" if self.transcript.lines else "idle"
        self.timer.stop()
        self.hero.app_meter.setValue(0)
        self.hero.mic_meter.setValue(0)
        if not self.transcript.lines:
            self.transcript.reset(True, "这次没有记下文字。检查所选应用是否在播放声音、麦克风是否打开。")
        self.refresh()
        self.recording_changed.emit(False)

    def shutdown(self):
        """Quitting: stop now; lines already heard are on disk, speech still waiting is dropped."""
        self.stopping.set()
        if self.writing is not None:
            self.writing.set()
        if self.recorder is not None:
            self.ended = self.ended or time.time()
            recorder = self.recorder
            recorder.close()
            if self.recorder is not None:  # on_finished did not run: the threads are gone.
                self.on_finished()

    @property
    def recording(self) -> bool:
        return self.state in ("recording", "finishing")

    # Display

    def tick(self):
        recorder = self.recorder
        if recorder is None:
            return
        self.hero.app_meter.setValue(int(recorder.level(OTHERS) * 100))
        self.hero.mic_meter.setValue(int(max(recorder.level(ME), recorder.level(ROOM)) * 100))
        if self.state == "recording":
            self.hero.set_pill("rec", f"记录中&nbsp;&nbsp;{clock(recorder.elapsed)}")
        backlog = recorder.backlog
        self.progress.setText(f"识别中，还有 {round(backlog)} 秒的话" if backlog >= 15 else "")
        if self.state == "recording" and time.monotonic() >= self.next_prepare:
            self.next_prepare = time.monotonic() + 60
            self.prepare_minutes()
        # Windows records an app after its volume-mixer slider: muted there, it records nothing.
        if self.app and self.state == "recording":
            silent = recorder.elapsed > 40 and not recorder.heard(OTHERS)
            if silent and not self.silent_notice:
                self.silent_notice = (
                    f"还没听到{self.app.name}的声音。如果在 Windows 音量合成器里把它调成了静音或很小的音量，"
                    "这里也会听不到，请调回来。"
                )
                self.main.set_notice(self.silent_notice)
            elif not silent and self.silent_notice:
                self.silent_notice = ""
                self.main.set_notice("")

    def refresh_count(self):
        characters = self.transcript.characters()
        self.count.setText(f"{characters:,} 字")
        self.copy_button.setEnabled(bool(self.transcript.lines))
        self.refresh_minutes()

    def refresh(self):
        hero = self.hero
        mic = hero.mic_switch.isChecked()
        app = self.app
        if app:
            hero.app_chip.setText(app.name)
            hero.app_chip.set_picture(app_picture(app.path, 32))
        else:
            hero.app_chip.setText("选择会议软件" if self.supported else "只能录麦克风")
            hero.app_chip.set_picture(None)
        editable = self.state in ("idle", "done")
        # While recording the sources stay as they look, as a statement of what is recorded, but
        # cannot be changed.
        hero.app_chip.setEnabled(self.supported)
        for widget in (hero.app_chip, hero.mic_switch):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not editable)
            widget.setFocusPolicy(Qt.FocusPolicy.TabFocus if editable else Qt.FocusPolicy.NoFocus)
        hero.app_meter.setVisible(app is not None)
        hero.mic_meter.setVisible(mic)
        ok, why = self.ready()
        hero.button.setEnabled(self.state == "recording" or (editable and ok))
        hero.button.setText("结束记录" if self.state == "recording" else "开始记录")
        hero.button.setObjectName("recording" if self.state == "recording" else "primary")
        hero.button.set_busy(self.state == "finishing")
        hero.set_mode({"recording": "recording", "finishing": "busy"}.get(self.state, "idle"))
        sources = "和你的麦克风" if mic else ""
        if self.state == "recording":
            hero.status.setText("正在记录")
            if app and app.browser:
                hero.hint.setText(f"正在录下 {app.name} 所有标签页的声音{sources}。请先暂停其他标签页里的视频。")
            elif app:
                hero.hint.setText(f"正在录下{app.name}的声音{sources}。说的话会在几秒内出现在下方。")
            else:
                hero.hint.setText("正在录下麦克风听到的声音。说的话会在几秒内出现在下方。")
        elif self.state == "finishing":
            hero.set_pill("busy", "正在识别最后几句")
            hero.status.setText("马上就好")
            hero.hint.setText("已经停止录音，正在把刚才听到的话转成文字。")
        else:
            if self.state == "done":
                hero.set_pill("done", "已结束")
                hero.status.setText("会议记好了")
                hero.hint.setText(
                    f"共 {duration_text(self.duration)}，{self.transcript.characters():,} 字。"
                    "文字已加密保存在本机。"
                )
            else:
                hero.set_pill("idle", "未开始")
                hero.status.setText("记录一场会议")
                if not ok:
                    hero.hint.setText(why)
                elif app:
                    hero.hint.setText(f"录下{app.name}的声音{sources}，实时转成文字。不保存录音；开始前请告知参会人。")
                else:
                    hero.hint.setText("录下麦克风听到的声音，适合线下会议。不保存录音；开始前请告知参会人。")
        self.refresh_count()

    # Minutes

    def write_minutes(self):
        """Minutes of what has been said so far, with the chosen template, on a worker thread."""
        if self.writing is not None or self.store is None or not self.transcript.lines:
            return
        from .cleanup import CleanupError

        lines = list(self.transcript.lines)
        labels = self.transcript.labels
        template = self.template_box.currentData()
        store, cache = self.store, self.parts
        try:
            writer = self._writer(cache, lines)
        except CleanupError as error:
            self.main.set_notice(str(error))
            return
        cancel = self.writing = threading.Event()

        def work(report):
            try:
                return writer.write(lines, template, labels, cancel, report)
            except Exception as error:  # Shown in the notice; MinutesError says what to do.
                return error

        def done(result):
            self.writing = None
            self.spinner.stop()
            self.spinner.hide()
            if not isinstance(result, Exception):
                self.main.record_minutes_usage(result.usage)
                try:
                    store.save("parts", cache)
                    store.save("minutes", minutes_record(result))
                except OSError:
                    pass
            if cancel.is_set() or store is not self.store:
                self.refresh_minutes()
                return
            if isinstance(result, Exception):
                self.main.set_notice(str(result) if isinstance(result, CleanupError) else "纪要没有生成，请稍后重试。")
                self.show_minutes(self.minutes)
            else:
                self.show_minutes(result)

        self.main.set_notice("")
        self.spinner.show()
        self.spinner.start()
        self.minutes_status.setText("正在生成纪要…")
        if self.minutes is None:
            self.minutes_view.empty(MinutesView.WRITING)
        run_task(self, work, done, lambda text: self.writing is cancel and self.minutes_status.setText(text))
        self.refresh_minutes()

    def _writer(self, cache: dict, lines: list[Line]) -> MinutesWriter:
        from .cleanup import ApiCleaner
        from .settings import read_key
        from .vocabulary import Vocabulary

        settings = self.main.settings
        client = ApiCleaner(
            read_key(api_base_url=settings.api_base_url), settings.model, max(settings.api_timeout, 180),
            base_url=settings.api_base_url,
        )
        glossary = Vocabulary.for_settings(settings).glossary_for(" ".join(line.text for line in lines[-200:]))
        return MinutesWriter(client, cache, glossary)

    def prepare_minutes(self):
        """Every minute of a long meeting: note the parts already finished, in the background."""
        if self.preparing or self.writing is not None or self.store is None:
            return
        if self.transcript.characters() < PREPARE_FROM:
            return
        from .cleanup import CleanupError

        lines = list(self.transcript.lines)
        labels = self.transcript.labels
        store, cache = self.store, self.parts
        try:
            writer = self._writer(cache, lines)
        except CleanupError:
            return
        if not writer.client.key:
            return
        self.preparing = True

        def work(_report):
            try:
                return writer.prepare(lines, labels, self.stopping)
            except Exception:  # Tried again in a minute; the minutes button says what is wrong.
                return {}

        def done(usage):
            self.preparing = False
            if usage:
                self.main.record_minutes_usage(usage)
                try:
                    store.save("parts", cache)
                except OSError:
                    pass

        run_task(self, work, done)

    def show_minutes(self, minutes: Minutes | None):
        self.minutes = minutes
        if minutes is None:
            self.minutes_view.empty(MinutesView.EMPTY)
        else:
            info = self.store.info() if self.store else {}
            if self.recorder is not None:
                info.pop("duration", None)  # Still going: the length so far says little.
            blocks = [block for block in outline(minutes, info) if block[0] != "title"]
            self.minutes_view.setHtml(to_html(blocks))
        self.refresh_minutes()

    def refresh_minutes(self):
        busy = self.writing is not None
        self.write_button.setEnabled(not busy and bool(self.transcript.lines) and self.store is not None)
        self.write_button.setText("生成中…" if busy else ("重新生成" if self.minutes else "生成纪要"))
        self.template_box.setEnabled(not busy)
        self.copy_minutes_button.setEnabled(self.minutes is not None)
        if busy:
            return
        minutes = self.minutes
        if minutes is None:
            self.minutes_status.setText("")
        elif minutes.lines < len(self.transcript.lines):
            self.minutes_status.setText(f"截至 {clock(minutes.through)} · 之后的话还没写进去")
        else:
            self.minutes_status.setText(f"截至 {clock(minutes.through)}" if self.recording else "整场会议")

    def copy_minutes(self):
        if self.minutes is None:
            return
        info = self.store.info() if self.store else {}
        QApplication.clipboard().setText(to_text(outline(self.minutes, info)))
        self.copy_minutes_button.flash("已复制")

    def copy_all(self):
        text = self.transcript.plain_text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_button.flash("已复制")
