"""The 会议 page: choose what to record, then watch the transcript grow.

MeetingHero takes the dictation card's place while the page is open: what will be recorded, a
meter for each source and the round start button. The page below it holds the live transcript.
"""

from __future__ import annotations

import bisect
import html
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFileInfo, QObject, QPoint, QRectF, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import (
    QActionGroup,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .audio import AudioError
from .capture import AudioApp, audio_apps, loopback_supported
from .export import FORMATS
from .icons import draw_icon
from .meeting import (
    LABELS,
    ME,
    OTHERS,
    ROOM,
    SKIPPED,
    Line,
    MeetingRecorder,
    MeetingStore,
    Paragraph,
    clock,
    list_meetings,
    paragraphs,
    run_on,
)
from .minutes import (
    PREPARE_FROM,
    TEMPLATES,
    Minutes,
    MinutesWriter,
    minutes_from_record,
    minutes_record,
    outline,
    to_text,
    todo_text,
)
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
    dialog_header,
    footer_bar,
    label,
    make_button,
    mix,
    run_task,
)

TONES = {"idle": "#AEAEB2", "rec": "#FF3B30", "busy": "#FF9F0A", "done": "#34C759"}
FAINT = "#8E8E93"
WAITING = "开始记录后，会议里说的话会实时出现在这里。"


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


def meeting_meta(info: dict) -> str:
    """How long, how much was said and what was recorded, for the list of meetings."""
    parts = []
    if info.get("started"):
        moment = datetime.fromtimestamp(info["started"])
        if moment.year != datetime.now().year:
            parts.append(f"{moment.year} 年")
    if info.get("duration"):
        parts.append(duration_text(info["duration"]))
    elif not info.get("ended"):
        parts.append("没有正常结束")
    if info.get("characters") is not None:
        parts.append(f"{info['characters']:,} 字")
    parts.append(info.get("app") or "麦克风")
    return " · ".join(parts)


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


class GuardedEdit(QTextEdit):
    """Text the user may edit within limits: every way of changing it (keys, an input method,
    pasting, the context menu) first asks `may_edit`. The program changes it only inside
    `quietly()`, so that only the user's changes count as edits and undo never takes back the
    program's."""

    edited = Signal()

    def __init__(self):
        super().__init__()
        self.quiet = 0
        self.dirty = False  # Edited since `dirty` was last cleared.
        self.setAcceptDrops(False)  # Dragged text could carry words across what must stay apart.
        self.setTabChangesFocus(True)
        self.document().contentsChange.connect(self._changed)
        self.set_editable(False)

    @contextmanager
    def quietly(self):
        self.quiet += 1
        try:
            yield
        finally:
            self.quiet -= 1
            if not self.quiet:
                self.document().clearUndoRedoStacks()

    def set_editable(self, on: bool) -> None:
        if on == (not self.isReadOnly()):
            return
        self.setReadOnly(not on)
        # Read-only, it is selectable with the mouse only: the keyboard would bring a caret.
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction if on else Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

    def may_edit(self, cursor: QTextCursor, step: int = 0) -> bool:
        """Whether the text may change at the cursor: its selection replaced by what is typed
        (step 0), or the character before (-1) or after (1) it deleted."""
        return True

    def may_break(self, cursor: QTextCursor) -> bool:
        """Whether Enter may start a new paragraph here (as Qt does it)."""
        return False

    def pasted(self, text: str) -> str:
        return text

    def _changed(self, position: int, removed: int, added: int) -> None:
        if self.quiet:
            return
        self.dirty = True
        self.changed_at(position, added)
        self.edited.emit()

    def changed_at(self, position: int, added: int) -> None:
        pass

    def keyPressEvent(self, event):
        if self.isReadOnly() or self._key_allowed(event):
            super().keyPressEvent(event)
        else:
            event.accept()

    def _key_allowed(self, event) -> bool:
        cursor = self.textCursor()
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return self.may_break(cursor)
        if event.matches(QKeySequence.StandardKey.Undo) or event.matches(QKeySequence.StandardKey.Redo):
            return True
        if key == Qt.Key.Key_Backspace:
            return self.may_edit(cursor, 0 if cursor.hasSelection() else -1)
        if key == Qt.Key.Key_Delete and not event.matches(QKeySequence.StandardKey.Cut):
            return self.may_edit(cursor, 0 if cursor.hasSelection() else 1)
        if event.matches(QKeySequence.StandardKey.Cut):
            return cursor.hasSelection() and self.may_edit(cursor)
        if event.matches(QKeySequence.StandardKey.Paste):
            return self.may_edit(cursor)
        if event.text() and event.text().isprintable():
            return self.may_edit(cursor)
        return True

    def inputMethodEvent(self, event):
        if self.isReadOnly():  # Qt would still take what an input method sends.
            event.ignore()
            return
        if event.commitString() or event.preeditString():
            cursor = self.textCursor()
            if not self.may_edit(cursor) and cursor.hasSelection():
                cursor.clearSelection()  # Typing then goes in where the cursor is.
                self.setTextCursor(cursor)
            if not self.may_edit(cursor):
                event.accept()
                return
        super().inputMethodEvent(event)

    def insertFromMimeData(self, source):
        cursor = self.textCursor()
        if not self.isReadOnly() and source.hasText() and self.may_edit(cursor):
            cursor.insertText(self.pasted(source.text()))
            self.ensureCursorVisible()

    def contextMenuEvent(self, event):
        """Qt's own menu is in English: this one in Chinese, offering only what may be done here."""
        cursor = self.textCursor()
        editable = not self.isReadOnly()
        selected = cursor.hasSelection()
        menu = QMenu(self)

        def add(text: str, key, slot, enabled: bool):
            shortcut = QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)
            action = menu.addAction(f"{text}\t{shortcut}" if shortcut else text)
            action.setEnabled(enabled)
            action.triggered.connect(slot)

        if editable:
            add("撤销", QKeySequence.StandardKey.Undo, self.undo, self.document().isUndoAvailable())
            add("重做", QKeySequence.StandardKey.Redo, self.redo, self.document().isRedoAvailable())
            menu.addSeparator()
            add("剪切", QKeySequence.StandardKey.Cut, self.cut, selected and self.may_edit(cursor))
        add("复制", QKeySequence.StandardKey.Copy, self.copy, selected)
        if editable:
            add("粘贴", QKeySequence.StandardKey.Paste, self.paste, self.canPaste() and self.may_edit(cursor))
        menu.addSeparator()
        add("全选", QKeySequence.StandardKey.SelectAll, self.selectAll, not self.document().isEmpty())
        menu.exec(event.globalPos())


class TranscriptView(GuardedEdit):
    """The meeting's lines in the order they were said, in paragraphs (meeting.paragraphs). Lines
    that arrive late (the microphone waits for the others to be recognized) go where they belong.
    Each paragraph is a block holding only its words; who and when are painted above it, so that
    editing never touches them. Once the meeting is over the words can be edited a paragraph at a
    time: no paragraph is added, split or joined to another, and none of the notes about speech
    that could not be recognized."""

    HEAD = 21  # Room above a paragraph's words for who and when.
    GAP = 14  # Between one paragraph's words and the next one's head.

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QTextEdit { background: transparent; border: none; padding: 0 10px 10px 14px;"
                           " selection-background-color: #CCE0FA; }")
        self.document().setDocumentMargin(4)
        self.lines: list[Line] = []
        self.shown: list[Paragraph] = []  # What the document holds, a block each.
        self.edits: set[int] = set()  # Paragraphs the user changed, not yet taken by take_edits.
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
        with self.quietly():
            self.clear()
        self.lines, self.shown = [], []
        self.edits.clear()
        self.dirty = False
        self.labels = labels
        self.setPlaceholderText(placeholder)

    def load(self, labels: bool, lines: list[Line], placeholder: str = "") -> None:
        """A saved meeting, all at once."""
        self.reset(labels, placeholder)
        self.lines = sorted(lines, key=lambda line: (line.start, line.end))
        self._render()
        self.verticalScrollBar().setValue(0)

    def characters(self) -> int:
        """What the paragraphs hold, as shown (edits included)."""
        total, block = 0, self.document().begin()
        for paragraph in self.shown:
            if paragraph.who != SKIPPED:
                total += len(block.text())
            block = block.next()
        return total

    def add(self, line: Line) -> None:
        bar = self.verticalScrollBar()
        following = bar.value() >= bar.maximum() - 4
        bisect.insort(self.lines, line, key=lambda item: (item.start, item.end))
        self._render()
        if following:
            bar.setValue(bar.maximum())

    def _render(self) -> None:
        """Bring the document in line with the lines, rewriting only from the first paragraph that
        changed. Mostly the last paragraph just grows: then only its new words are written, so a
        selection in it survives."""
        new = paragraphs(self.lines, self.labels)
        old = self.shown
        same = 0
        while same < min(len(old), len(new)) and old[same] == new[same]:
            same += 1
        with self.quietly():
            cursor = QTextCursor(self.document())
            if same == len(old) - 1 and same < len(new) and self._grows(old[same], new[same]):
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(new[same].text[len(old[same].text):], self._text_format(new[same]))
                same += 1
            elif same < len(old):
                if same:
                    block = self.document().findBlockByNumber(same - 1)
                    cursor.setPosition(block.position() + block.length() - 1)
                cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            else:
                cursor.movePosition(QTextCursor.MoveOperation.End)
            for index in range(same, len(new)):
                if index:
                    cursor.insertBlock()
                self._write(cursor, new[index], index)
        self.shown = new
        self.viewport().update()

    @staticmethod
    def _grows(old: Paragraph, new: Paragraph) -> bool:
        return old.start == new.start and old.who == new.who and new.text.startswith(old.text)

    @staticmethod
    def _text_format(paragraph: Paragraph) -> QTextCharFormat:
        text = QTextCharFormat()
        text.setFontPointSize(11.5)
        text.setForeground(QColor(FAINT if paragraph.who == SKIPPED else INK))
        text.setFontItalic(paragraph.who == SKIPPED)
        return text

    def _write(self, cursor: QTextCursor, paragraph: Paragraph, index: int) -> None:
        if not index:
            # Qt drops the first block's top margin: the room for its head is the frame's.
            frame = self.document().rootFrame()
            spaced = frame.frameFormat()
            spaced.setTopMargin(self.document().documentMargin() + self.HEAD)
            frame.setFrameFormat(spaced)
        block = QTextBlockFormat()
        block.setTopMargin(self.HEAD + self.GAP if index else 0)
        block.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setBlockFormat(block)
        cursor.insertText(paragraph.text, self._text_format(paragraph))

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.shown:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        name_font, time_font = QFont(self.font()), QFont(self.font())
        name_font.setPointSizeF(9.5)
        name_font.setWeight(QFont.Weight.DemiBold)
        time_font.setPointSizeF(9)
        dx, dy = -self.horizontalScrollBar().value(), -self.verticalScrollBar().value()
        area = self.viewport().rect()
        block = self.document().begin()
        for paragraph in self.shown:
            if not block.isValid():
                break
            top = block.layout().position().y() + dy
            if top - self.HEAD > area.bottom():
                break
            if top >= area.top() - 4:
                left = block.layout().position().x() + dx
                head = QRectF(left, top - self.HEAD, area.width(), self.HEAD - 5)
                who = LABELS.get(paragraph.who, "") if self.labels else ""
                if who:
                    painter.setFont(name_font)
                    painter.setPen(QColor(ACCENT if paragraph.who == ME else "#3A3A3C"))
                    painter.drawText(head, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, who)
                    head.setLeft(head.left() + QFontMetrics(name_font).horizontalAdvance(who) + 8)
                painter.setFont(time_font)
                painter.setPen(QColor(FAINT))
                painter.drawText(head, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, clock(paragraph.start))
            block = block.next()

    # Editing

    def may_edit(self, cursor: QTextCursor, step: int = 0) -> bool:
        document = self.document()
        block = document.findBlock(cursor.selectionStart())
        if document.findBlock(cursor.selectionEnd()) != block:
            return False
        index = block.blockNumber()
        if index >= len(self.shown) or self.shown[index].who == SKIPPED:
            return False
        if not cursor.hasSelection():
            inside = cursor.position() - block.position()
            if step < 0 and inside == 0 or step > 0 and inside >= block.length() - 1:
                return False  # It would join two paragraphs.
        return True

    def pasted(self, text: str) -> str:
        """Pasted lines run on in the paragraph."""
        joined = ""
        for part in text.splitlines():
            joined = run_on(joined, part.strip())
        return joined

    def changed_at(self, position: int, added: int) -> None:
        self.edits.add(self.document().findBlock(position).blockNumber())

    def take_edits(self) -> list[Line] | None:
        """The lines with each edited paragraph now one line of its edited words (an emptied one
        holds an empty line, so that the paragraphs stay as they are on the page), or None when
        nothing was edited since the last call."""
        if not self.edits:
            return None
        lines: list[Line] = []
        block = self.document().begin()
        for index, paragraph in enumerate(self.shown):
            held = self.lines[paragraph.first : paragraph.last + 1]
            if index in self.edits and paragraph.who != SKIPPED:
                text = block.text().strip()
                lines.append(Line(held[0].start, max(line.end for line in held), held[0].who, text))
            else:
                lines.extend(held)
            block = block.next()
        self.edits.clear()
        self.lines = lines
        self.shown = paragraphs(lines, self.labels)
        return lines

    def plain_text(self) -> str:
        """The transcript for copying: its paragraphs with time and speaker, a blank line apart."""
        rows = []
        block = self.document().begin()
        for paragraph in self.shown:
            text = block.text().strip()
            block = block.next()
            if paragraph.who == SKIPPED or not text:
                continue
            who = LABELS.get(paragraph.who, "") if self.labels else ""
            when = clock(paragraph.start)
            rows.append(f"[{when}] {who}：{text}" if who else f"[{when}] {text}")
        return "\n\n".join(rows)


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


class MinutesView(GuardedEdit):
    """The minutes as a document the user may edit once they are written. Each block keeps what
    kind of block it is (KIND, outline's kinds plus "points" under a chapter), so that the edited
    minutes read back as outline's blocks for copying and export; Enter makes a block of the
    kind that follows. The line with the date and the template stays as the program wrote it."""

    KIND = QTextFormat.Property.UserProperty + 1
    EMPTY = (
        "开会途中或会议结束后，选一个模板，点“生成纪要”。",
        "纪要只根据会议文字来写，没听清、没说定的地方会列进“待确认”。生成时会把会议文字发送给所选 API；"
        "会议超过一小时后，已经说完的部分会每隔约 15 分钟先发去整理，结束时生成得更快。",
    )
    WRITING = ("正在根据会议文字生成纪要，一般要十几秒，会议很长时会久一些。", "可以继续记录，不影响识别。")

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QTextEdit { background: transparent; border: none; padding: 0 10px 10px 14px;"
                           " font-size: 14px; selection-background-color: #CCE0FA; }")
        self.document().setIndentWidth(16)
        self.showing = False  # Minutes, not an explanation in their place.
        self.empty(self.EMPTY)

    def empty(self, paragraphs: tuple[str, ...]):
        """Grey explanatory text in place of the minutes."""
        with self.quietly():
            self.setHtml("".join(
                f"<p style='color:{MUTED}; margin:0 0 10px 0; line-height:150%'>{html.escape(text)}</p>"
                for text in paragraphs
            ))
        self.showing = False
        self.dirty = False
        self.set_editable(False)

    def show_blocks(self, blocks: list[tuple[str, object]]) -> None:
        """outline's blocks after the title."""
        with self.quietly():
            self.clear()
            cursor = QTextCursor(self.document())
            first = True
            for kind, value in blocks:
                if kind == "chapter":
                    when, title, points = value
                    self._block(cursor, "chapter", first)
                    cursor.insertText(when, self._char("time"))
                    cursor.insertText("  " + title if when else title, self._char("bold"))
                    if points:
                        self._block(cursor, "points", False)
                        cursor.insertText(points, self._char("points"))
                elif kind in ("meta", "heading", "paragraph", "item", "todo"):
                    self._block(cursor, kind, first)
                    text = todo_text(*value) if kind == "todo" else value
                    cursor.insertText(text, self._char(kind))
                else:
                    continue
                first = False
            self.verticalScrollBar().setValue(0)
        self.showing = True
        self.dirty = False

    def _block(self, cursor: QTextCursor, kind: str, first: bool) -> None:
        if not first:
            cursor.insertBlock()
        cursor.setBlockFormat(self._format(kind))
        if kind in ("item", "todo"):
            previous = cursor.block().previous()
            listed = previous.textList() if previous.isValid() else None
            if listed is not None and previous.blockFormat().property(self.KIND) == kind:
                listed.add(cursor.block())
            else:
                style = QTextListFormat()
                style.setStyle(QTextListFormat.Style.ListDisc)
                style.setIndent(1)
                cursor.createList(style)

    def _format(self, kind: str) -> QTextBlockFormat:
        block = QTextBlockFormat()
        block.setProperty(self.KIND, kind)
        top, bottom = {
            "meta": (0, 6), "heading": (16, 4), "paragraph": (0, 2), "chapter": (4, 0), "points": (0, 4),
            "item": (2, 2), "todo": (2, 2),
        }[kind]
        block.setTopMargin(top)
        block.setBottomMargin(bottom)
        if kind != "meta":
            block.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        if kind == "todo":
            block.setMarker(QTextBlockFormat.MarkerType.Unchecked)
        return block

    @staticmethod
    def _char(kind: str) -> QTextCharFormat:
        text = QTextCharFormat()
        size, color, weight = {
            "meta": (12, FAINT, QFont.Weight.Normal),
            "heading": (14, INK, QFont.Weight.Bold),
            "time": (14, ACCENT, QFont.Weight.DemiBold),
            "bold": (14, INK, QFont.Weight.Bold),
            "points": (14, "#3A3A3C", QFont.Weight.Normal),
        }.get(kind, (14, INK, QFont.Weight.Normal))
        text.setProperty(QTextFormat.Property.FontPixelSize, size)
        text.setForeground(QColor(color))
        text.setFontWeight(weight)
        return text

    def blocks(self) -> list[tuple[str, object]]:
        """What the document holds, as outline's blocks (without the title and the meta line)."""
        out: list[tuple[str, object]] = []
        block = self.document().begin()
        while block.isValid():
            kind = block.blockFormat().property(self.KIND) or "paragraph"
            text = block.text().replace("\u2028", " ").strip()
            if block.textList() is not None:
                marked = block.blockFormat().marker() != QTextBlockFormat.MarkerType.NoMarker
                kind = "todo" if marked else "item"
            elif kind in ("item", "todo"):
                kind = "paragraph"  # Taken out of its list.
            block = block.next()
            if not text or kind == "meta":
                continue
            if kind == "chapter":
                found = re.match(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*(.*)", text)
                out.append(("chapter", (found.group(1), found.group(2), "") if found else ("", text, "")))
            elif kind == "points" and out and out[-1][0] == "chapter" and not out[-1][1][2]:
                when, title, _ = out[-1][1]
                out[-1] = ("chapter", (when, title, text))
            elif kind == "todo":
                out.append(("todo", (text, "", "")))
            else:
                out.append(("paragraph" if kind == "points" else kind, text))
        return out

    # Editing

    def may_edit(self, cursor: QTextCursor, step: int = 0) -> bool:
        if not self.showing:
            return False
        meta = self.document().begin()
        if meta.blockFormat().property(self.KIND) != "meta":
            return True
        after = meta.position() + meta.length()  # Where the block after the meta line starts.
        start = cursor.selectionStart()
        return start >= after and not (step < 0 and not cursor.hasSelection() and start == after)

    def may_break(self, cursor: QTextCursor) -> bool:
        """After a heading comes a paragraph and after a chapter its points, not another of the
        same; elsewhere Enter does what it does in Qt (in a list, a new item)."""
        if not self.may_edit(cursor):
            return False
        kind = cursor.block().blockFormat().property(self.KIND)
        if cursor.block().textList() is None and kind in ("heading", "chapter") and cursor.atBlockEnd():
            following = "paragraph" if kind == "heading" else "points"
            cursor.insertBlock(self._format(following), self._char(following))
            self.setTextCursor(cursor)
            return False
        return True


class StayOpenMenu(QMenu):
    """A menu whose check boxes can be ticked without it closing."""

    def mouseReleaseEvent(self, event):
        action = self.activeAction()
        if action is not None and action.isCheckable() and action.isEnabled():
            action.trigger()
            return
        super().mouseReleaseEvent(event)


class MeetingRow(QWidget):
    """One meeting in the list: its title, below it how long and how much, and whether it has
    minutes."""

    def __init__(self, info: dict, minutes: bool):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # Clicks select the row.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(10)
        column = QVBoxLayout()
        column.setSpacing(3)
        title = label(info.get("title") or "会议")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {INK};")
        column.addWidget(title)
        column.addWidget(label(meeting_meta(info), "caption"))
        layout.addLayout(column, 1)
        if minutes:
            layout.addWidget(label("已有纪要", "tag"), 0, Qt.AlignmentFlag.AlignVCenter)


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
        self.opened = ""  # The title of a meeting opened from the list (not just recorded).
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
        self.transcript.reset(True, WAITING)
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
        self.history_button = make_button("全部会议", Glyph.HISTORY, "ghost")
        self.history_button.setToolTip("打开以前记录的会议")
        self.history_button.clicked.connect(self.open_meetings)
        actions.addWidget(self.history_button)
        self.copy_button = make_button("复制全文", Glyph.COPY, "ghost")
        self.copy_button.clicked.connect(self.copy_all)
        actions.addWidget(self.copy_button)
        self.export_button = make_button("导出", Glyph.EXPORT, "ghost")
        self.export_button.set_trailing(Glyph.CHEVRON_DOWN)
        self.export_button.setToolTip("把纪要和会议文字存成 Word、PDF、Markdown 或纯文本文件")
        self.export_menu = StayOpenMenu(self.export_button)
        self.export_menu.aboutToShow.connect(self.fill_export_menu)
        self.export_button.setMenu(self.export_menu)
        actions.addWidget(self.export_button)
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
        # Edits are saved once the user pauses typing, and before anything reads them.
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(800)
        self.save_timer.timeout.connect(self.save_edits)
        self.saved_timer = QTimer(self)
        self.saved_timer.setSingleShot(True)
        self.saved_timer.setInterval(2000)
        self.saved_timer.timeout.connect(lambda: self.progress.setText(""))
        self.transcript.edited.connect(self.transcript_edited)
        self.minutes_view.edited.connect(self.save_timer.start)
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
        self.save_edits()
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
        self.opened = ""
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
        self.save_edits()
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
        self.transcript.set_editable(self.state == "done" and bool(self.transcript.lines))
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
                size = f"共 {duration_text(self.duration)}，{self.transcript.characters():,} 字。"
                if self.opened:
                    hero.status.setText(self.opened)
                    hero.hint.setText(size + "文字和纪要都可以直接修改，也可以导出成文件。")
                else:
                    hero.status.setText("会议记好了")
                    hero.hint.setText(size + "文字已加密保存在本机，可以直接修改。")
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
        self.save_edits()  # The transcript as edited goes in; edits to the minutes count as made.
        if self.minutes is not None and self.minutes.edited is not None and not self.confirm_rewrite():
            return
        from .cleanup import CleanupError

        lines = [line for line in self.transcript.lines if line.text.strip() or line.who == SKIPPED]
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

    def confirm_rewrite(self) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, "重新生成纪要", "这份纪要你修改过。重新生成会按会议文字重写，你的修改会被替换。", parent=self)
        again = box.addButton("重新生成", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(box.addButton("取消", QMessageBox.ButtonRole.RejectRole))
        box.exec()
        return box.clickedButton() is again

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
            self.minutes_view.show_blocks([block for block in outline(minutes, info) if block[0] != "title"])
        self.refresh_minutes()

    def refresh_minutes(self):
        busy = self.writing is not None
        self.write_button.setEnabled(not busy and bool(self.transcript.lines) and self.store is not None)
        self.write_button.setText("生成中…" if busy else ("重新生成" if self.minutes else "生成纪要"))
        self.template_box.setEnabled(not busy)
        self.copy_minutes_button.setEnabled(self.minutes is not None)
        self.export_button.setEnabled(bool(self.transcript.lines) and self.store is not None)
        self.history_button.setEnabled(not self.recording and not busy)
        # Not while new minutes are on their way: they would replace what is typed.
        self.minutes_view.set_editable(self.minutes is not None and not busy)
        if busy:
            return
        minutes = self.minutes
        if minutes is None:
            self.minutes_status.setText("")
        elif minutes.edited is not None:
            self.minutes_status.setText("已修改")
        elif minutes.lines < len(self.transcript.lines):
            self.minutes_status.setText(f"截至 {clock(minutes.through)} · 之后的话还没写进去")
        else:
            self.minutes_status.setText(f"截至 {clock(minutes.through)}" if self.recording else "整场会议")

    # Editing

    def transcript_edited(self):
        self.save_timer.start()
        self.count.setText(f"{self.transcript.characters():,} 字")

    def save_edits(self):
        """What the user changed in either panel, into the meeting's files."""
        self.save_timer.stop()
        store = self.store
        if store is None:
            return
        try:
            lines = self.transcript.take_edits()
            if lines is not None:
                store.replace_lines([line for line in lines if line.text.strip() or line.who == SKIPPED])
                info = store.info()
                info["characters"] = self.transcript.characters()
                store.save_info(info)
                self.refresh()  # The count of characters above the panels.
                self.progress.setText("已保存修改")
                self.saved_timer.start()
            if self.minutes is not None and self.minutes_view.dirty:
                self.minutes_view.dirty = False
                self.minutes.edited = self.minutes_view.blocks()
                store.save("minutes", minutes_record(self.minutes))
                self.refresh_minutes()
        except OSError:
            self.main.set_notice("修改没能保存，请检查磁盘空间和权限。")

    def copy_minutes(self):
        self.save_edits()
        if self.minutes is None:
            return
        info = self.store.info() if self.store else {}
        QApplication.clipboard().setText(to_text(outline(self.minutes, info)))
        self.copy_minutes_button.flash("已复制")

    def copy_all(self):
        self.save_edits()
        text = self.transcript.plain_text()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_button.flash("已复制")

    # Saving as a file

    def fill_export_menu(self):
        """What goes in the file (minutes alone, or with the transcript), then the formats."""
        menu = self.export_menu
        menu.clear()
        if self.minutes is None:
            menu.addAction("还没有纪要，将只导出会议文字").setEnabled(False)
        else:
            menu.addAction("内容").setEnabled(False)
            group = QActionGroup(menu)
            for text, transcript in (("只要纪要", False), ("纪要和会议文字", True)):
                action = menu.addAction(text)
                action.setCheckable(True)
                action.setChecked(self.main.settings.meeting_export_transcript == transcript)
                action.setActionGroup(group)
                action.triggered.connect(
                    lambda _checked=False, transcript=transcript: self._save(meeting_export_transcript=transcript)
                )
        menu.addSeparator()
        menu.addAction("导出为").setEnabled(False)
        for kind, (name, _filter) in FORMATS.items():
            action = menu.addAction(f"{name}	.{kind}")
            action.triggered.connect(lambda _checked=False, kind=kind: self.export_meeting(kind))

    def export_meeting(self, kind: str):
        """The minutes, with the transcript if asked for (or alone, before there are minutes)."""
        if self.store is None or not self.transcript.lines:
            return
        self.save_edits()
        from .export import document_blocks, file_name, save

        settings = self.main.settings
        info = self.store.info()
        if self.recorder is not None:
            info.pop("duration", None)
        folder = settings.meeting_export_dir
        if not folder or not Path(folder).is_dir():
            folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        suggested = Path(folder) / f"{file_name(info.get('title') or '会议')}.{kind}"
        chosen, _ = QFileDialog.getSaveFileName(self, "导出会议", str(suggested), FORMATS[kind][1])
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != f".{kind}":
            path = path.with_name(f"{path.name}.{kind}")
        transcript = settings.meeting_export_transcript or self.minutes is None
        lines = [line for line in self.transcript.lines if line.text.strip() or line.who == SKIPPED]
        blocks = document_blocks(info, self.minutes, lines, self.transcript.labels, transcript)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            save(path, blocks)
        except OSError:
            self.main.set_notice("文件没有保存成功。如果同名文件正在 Word 或其他程序里打开，请先关掉它，或换个位置再试。")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._save(meeting_export_dir=str(path.parent))
        self.main.set_notice("")
        self.export_button.flash("已导出")

    # Earlier meetings

    def open_meetings(self):
        if self.recording or self.writing is not None:
            return
        stores = list_meetings()
        dialog = QDialog(self)
        dialog.setWindowTitle("全部会议 · 好好说")
        dialog.resize(600, 520)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(dialog_header("全部会议", "会议文字和纪要加密保存在本机，只有当前 Windows 用户能打开。"))
        body = QVBoxLayout()
        body.setContentsMargins(30, 6, 30, 22)
        listing = QListWidget()
        listing.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        listing.setStyleSheet("QListWidget::item { padding: 0; }")  # MeetingRow has its own.
        for store in stores:
            row = MeetingRow(store.info(), (store.folder / "minutes.dat").is_file())
            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            listing.addItem(item)
            listing.setItemWidget(item, row)
        shown = next((i for i, store in enumerate(stores) if self.store and store.folder == self.store.folder), 0)
        if stores:
            listing.setCurrentRow(shown)
        empty = label("还没有记录过会议。", "muted")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(listing, 1)
        body.addWidget(empty, 1)
        layout.addLayout(body, 1)
        footer, actions = footer_bar()
        delete_button = make_button("删除", Glyph.DELETE, "danger")
        open_button = make_button("打开", None, "primary")
        actions.addWidget(delete_button)
        actions.addStretch()
        actions.addWidget(open_button)
        layout.addWidget(footer)

        def update():
            listing.setVisible(bool(stores))
            empty.setVisible(not stores)
            delete_button.setEnabled(listing.currentRow() >= 0)
            open_button.setEnabled(listing.currentRow() >= 0)

        def open_chosen():
            index = listing.currentRow()
            if 0 <= index < len(stores):
                dialog.accept()
                self.open_meeting(stores[index])

        def delete_chosen():
            index = listing.currentRow()
            if not 0 <= index < len(stores):
                return
            title = stores[index].info().get("title") or "这场会议"
            answer = QMessageBox.question(
                dialog, "删除会议", f"删除“{title}”？\n\n会议文字和纪要都会从本机删除，无法恢复。"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            if not self.delete_meeting(stores[index]):
                QMessageBox.warning(dialog, "未能删除", "会议文件正在使用或没有权限，请稍后重试。")
                return
            stores.pop(index)
            listing.takeItem(index)
            update()

        listing.currentRowChanged.connect(lambda _row: update())
        listing.itemDoubleClicked.connect(lambda _item: open_chosen())
        open_button.clicked.connect(open_chosen)
        delete_button.clicked.connect(delete_chosen)
        update()
        dialog.exec()

    def open_meeting(self, store: MeetingStore):
        """A meeting from the list, as it was left: its transcript, minutes and notes."""
        if self.recording or self.writing is not None:
            return
        self.save_edits()
        info = store.info()
        lines = store.lines()
        self.store = store
        self.opened = info.get("title") or "会议"
        self.transcript.load(bool(info.get("labels")), lines, "这场会议没有记下文字。")
        self.duration = info.get("duration") or (lines[-1].end if lines else 0.0)
        parts = store.load("parts")
        self.parts = parts if isinstance(parts, dict) else {}
        minutes = minutes_from_record(store.load("minutes"))
        index = next((i for i, t in enumerate(TEMPLATES) if minutes and t.id == minutes.template), None)
        if index is not None:
            # The meeting's own template for writing it again; the remembered choice stays.
            self.template_box.blockSignals(True)
            self.template_box.setCurrentIndex(index)
            self.template_box.blockSignals(False)
        self.state = "done"
        self.main.set_notice("")
        self.show_minutes(minutes)
        self.refresh()

    def delete_meeting(self, store: MeetingStore) -> bool:
        import shutil

        try:
            shutil.rmtree(store.folder)
        except OSError:
            return False
        if self.store is not None and self.store.folder == store.folder:
            self.save_timer.stop()
            self.store = None
            self.parts = {}
            self.opened = ""
            self.state = "idle"
            self.duration = 0.0
            self.transcript.reset(True, WAITING)
            self.show_minutes(None)
            self.refresh()
        return True
