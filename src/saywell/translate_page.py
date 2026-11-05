"""The 翻译 page: a card on how selection translation is used in any program, and long texts
pasted here, translated a few paragraphs at a time so the first ones show while the rest are
on their way. It can be stopped and picked up again where it stopped."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .brand import bird_rect, draw_swallow, draw_tile
from .shortcuts import SOLO_KEYS, shortcut_label
from .stats import count_chars
from .translation import LANGUAGES, SCRIPTS, STYLES, Translation, script_of, target_language
from .ui import (
    ACCENT,
    LINE,
    MUTED,
    SHEET,
    ComboBox,
    Glyph,
    HeroCard,
    Segmented,
    card,
    glyph_label,
    label,
    make_button,
    pill_text,
    run_task,
)

CHUNK = 1500  # Characters asked for at a time: long enough to keep the sense, short enough to come back soon.
LIMIT = 100_000
STYLE_KEYS = tuple(STYLES)
ON, OFF = "#5A9E6F", "#B5AC9F"
SKETCH = "#E2D9CA"  # The grey of the little drawings' lines of text.

PAGE_STYLE = """
QLabel#guideTitle { font-family: 'Saywell Serif'; font-size: 26px; font-weight: 700; color: #192457; }
QLabel#stepCaption { color: #47413A; font-size: 13px; }
QLabel#pageCaption { color: #958C7F; font-size: 12px; }
"""

# A sentence ends at Chinese or Western end punctuation (a full stop only before a space or the end,
# so 3.5 and example.com stay whole), with any closing quote or bracket after it.
SENTENCE = re.compile(r".+?(?:[。！？!?；;…]+[”’\"')）]*|\.(?=\s|$)[”’\"')）]*|$)\s*", re.S)


def pieces(text: str, size: int = CHUNK) -> list[tuple[str, str]]:
    """The text in pieces of at most about size characters, cut between paragraphs (inside a
    longer paragraph, between sentences), each with the whitespace that followed it, so the
    translations join up with the original's breaks."""
    units: list[tuple[str, str]] = []
    parts = re.split(r"(\s*\n\s*)", text.strip())
    for index in range(0, len(parts), 2):
        paragraph = parts[index]
        after = parts[index + 1] if index + 1 < len(parts) else ""
        if len(paragraph) <= size:
            units.append((paragraph, after))
            continue
        sentences = [match.group(0) for match in SENTENCE.finditer(paragraph)]
        for number, sentence in enumerate(sentences):
            body = sentence.rstrip()
            tail = sentence[len(body):] if number < len(sentences) - 1 else after
            while len(body) > size:  # One sentence longer than a piece: cut it.
                units.append((body[:size], ""))
                body = body[size:]
            units.append((body, tail))
    result: list[tuple[str, str]] = []
    current, gap = "", ""
    for body, after in units:
        if current and len(current) + len(gap) + len(body) > size:
            result.append((current, gap))
            current, gap = body, after
        else:
            current = current + gap + body if current else body
            gap = after
    if current:
        result.append((current, gap))
    return result


@dataclass
class Job:
    """One text on its way: what was asked for, its pieces, and the translations so far."""

    text: str
    source: str
    target: str
    style: str
    pieces: list[tuple[str, str]]
    done: list[str] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return len(self.done) == len(self.pieces)


# ---------- the guide ----------


class TextSketch(QWidget):
    """Two lines of text with part of the first selected."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(150, 44)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, width, color in ((10, 26, SKETCH), (40, 64, "#C3C8E4"), (108, 32, SKETCH)):
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(x, 10, width, 8), 4, 4)
        painter.setBrush(QColor(SKETCH))
        painter.drawRoundedRect(QRectF(10, 26, 112, 8), 4, 4)


class SwallowMark(QWidget):
    """The button that appears beside a selection: the swallow on its small tile."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(44, 44)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(3, 2, 38, 38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(120, 96, 62, 22))
        painter.drawRoundedRect(box.translated(0, 1.5), 11, 11)
        draw_tile(painter, box, rim=36)
        draw_swallow(painter, bird_rect(box))


class PopupSketch(QWidget):
    """The translation popup in miniature: two lines and its two buttons."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(150, 50)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(6.5, 2.5, 137, 44)
        painter.setPen(QPen(QColor("#DCD3C4"), 1))
        painter.setBrush(QColor(SHEET))
        painter.drawRoundedRect(box, 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#C3C8E4"))
        painter.drawRoundedRect(QRectF(16, 11, 96, 6), 3, 3)
        painter.setBrush(QColor(SKETCH))
        painter.drawRoundedRect(QRectF(16, 22, 62, 6), 3, 3)
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(0, 30, box.right() - 8, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "复制   替换")


def step(picture: QWidget, caption: QWidget) -> QVBoxLayout:
    column = QVBoxLayout()
    column.setSpacing(8)
    column.addWidget(picture, 0, Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(caption, 0, Qt.AlignmentFlag.AlignHCenter)
    return column


class TranslateGuide(HeroCard):
    """In place of the recording card on the 翻译 page: whether selection translation is on, and
    how it is used, in three small pictures that follow the trigger and the shortcut chosen."""

    open_settings = Signal()
    turn_on = Signal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet(PAGE_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 22, 18)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(label("划词翻译", "guideTitle"))
        self.state = label("", "pill")
        self.state.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(self.state, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch()
        self.on_button = make_button("开启划词翻译", None, "primary")
        self.on_button.clicked.connect(self.turn_on)
        head.addWidget(self.on_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.settings_button = make_button("设置", Glyph.SETTINGS, "link")
        self.settings_button.setToolTip("触发方式、快捷键、互译语言和不翻译的程序")
        self.settings_button.clicked.connect(self.open_settings)
        head.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        steps = QHBoxLayout()
        steps.setSpacing(10)
        self.select_caption = label("在任何程序里选中文字", "stepCaption")
        steps.addStretch(1)
        steps.addLayout(step(TextSketch(), self.select_caption))
        steps.addStretch(1)
        steps.addWidget(glyph_label(Glyph.CHEVRON_RIGHT, "#B5AC9F", 16), 0, Qt.AlignmentFlag.AlignVCenter)
        steps.addStretch(1)
        trigger = QWidget()
        trigger.setFixedHeight(44)
        row = QHBoxLayout(trigger)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.mark = SwallowMark()
        row.addWidget(self.mark)
        self.key = label("", "keycap")
        row.addWidget(self.key, 0, Qt.AlignmentFlag.AlignVCenter)
        self.trigger_caption = label("", "stepCaption")
        steps.addLayout(step(trigger, self.trigger_caption))
        steps.addStretch(1)
        steps.addWidget(glyph_label(Glyph.CHEVRON_RIGHT, "#B5AC9F", 16), 0, Qt.AlignmentFlag.AlignVCenter)
        steps.addStretch(1)
        steps.addLayout(step(PopupSketch(), label("复制译文，或直接替换原文", "stepCaption")))
        steps.addStretch(1)
        layout.addLayout(steps)

    def refresh(self, settings):
        on = settings.selection_translate
        self.state.setText(pill_text(ON if on else OFF, "已开启" if on else "未开启"))
        self.on_button.setVisible(not on)
        key = shortcut_label(settings.selection_hotkey)
        press = "轻按" if settings.selection_hotkey in SOLO_KEYS else "按"
        gap = " " if key[0].isascii() else ""
        self.key.setText(key)
        self.mark.setVisible(settings.selection_trigger != "hotkey")
        self.trigger_caption.setText({
            "icon": f"点燕子按钮，或{press}{gap}{key}",
            "direct": f"选中就翻译，也可以{press}{gap}{key}",
            "hotkey": f"{press}一下{gap}{key}",
        }.get(settings.selection_trigger, f"点燕子按钮，或{press}{gap}{key}"))


# ---------- the page ----------


class ProgressLine(QWidget):
    """A thin ink line under the translation: how much of a long text is done."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(3)
        self.fraction = 0.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_fraction(self, fraction: float):
        self.fraction = max(0.0, min(1.0, fraction))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(LINE))
        painter.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        if self.fraction > 0:
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(QRectF(0, 0, self.width() * self.fraction, self.height()), 1.5, 1.5)


def text_card(title: str, hint: str, accent: bool = False):
    """A card with a serif title, a quiet figure on the right of it and a text box."""
    frame = card(accent)
    column = QVBoxLayout(frame)
    column.setContentsMargins(8, 16, 8, 6)
    column.setSpacing(8)
    head = QHBoxLayout()
    head.setContentsMargins(14, 0, 12, 0)
    head.addWidget(label(title, "cardTitle"))
    head.addStretch()
    figure = label("", "count")
    head.addWidget(figure)
    column.addLayout(head)
    editor = QPlainTextEdit()
    editor.setPlaceholderText(hint)
    column.addWidget(editor, 1)
    return frame, column, editor, figure


class TranslatePage(QWidget):
    """Long texts: languages and style on top, the original and the translation side by side,
    and the actions below. main is the window: settings, the notice, the toast, usage."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setStyleSheet(PAGE_STYLE)
        self.guide = TranslateGuide()
        self.job: Job | None = None
        self.running = False
        self.cancel: threading.Event | None = None
        self.generation = 0
        self.pinned = False  # The target was chosen by hand; otherwise it follows the text.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.source = ComboBox()
        self.source.addItem("自动检测", "")
        for name in LANGUAGES:
            self.source.addItem(name, name)
        self.source.setToolTip("原文的语言；自动检测时由模型判断")
        self.source.setMinimumWidth(120)
        self.source.activated.connect(lambda _index: self._follow_text())
        controls.addWidget(self.source)
        self.swap_button = make_button("", Glyph.SWAP, "heroChip")
        self.swap_button.setFixedWidth(34)
        self.swap_button.setToolTip("对调原文和译文的语言")
        self.swap_button.clicked.connect(self.swap)
        controls.addWidget(self.swap_button)
        self.target = ComboBox()
        for name in LANGUAGES:
            self.target.addItem(name, name)
        self.target.setToolTip("译成哪种语言")
        self.target.setMinimumWidth(120)
        self.target.activated.connect(self._pin)
        controls.addWidget(self.target)
        controls.addSpacing(10)
        self.styles = Segmented(tuple(name for name, _rule in STYLES.values()))
        self.styles.setToolTip("自然：地道通顺；正式：适合邮件和文档；直译：贴近原文。和划词翻译用同一种")
        self.styles.changed.connect(self._restyle)
        controls.addWidget(self.styles, 0, Qt.AlignmentFlag.AlignVCenter)
        controls.addStretch()
        controls.addWidget(label("长文会分段翻译，译好一段显示一段。", "pageCaption"))
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(16)
        splitter.setChildrenCollapsible(False)
        left, _column, self.original, self.original_count = text_card(
            "原文", "把要翻译的文字粘贴到这里，最多 10 万字。\n\n长文会按段落分成几份依次翻译，可以随时停下，再接着翻。"
        )
        right, column, self.result, self.progress = text_card(
            "译文", "译文会出现在这里，译完可以直接修改。", accent=True
        )
        self.result.setReadOnly(True)
        self.bar = ProgressLine()
        self.bar.hide()
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(14, 0, 14, 8)
        bar_row.addWidget(self.bar)
        column.addLayout(bar_row)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, 1)
        self.follow_timer = QTimer(self)
        self.follow_timer.setSingleShot(True)
        self.follow_timer.setInterval(300)
        self.follow_timer.timeout.connect(self._follow_text)
        self.original.textChanged.connect(self._original_changed)
        self.result.textChanged.connect(self._sync)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.paste_button = make_button("粘贴", Glyph.PASTE)
        self.paste_button.clicked.connect(self.paste)
        self.clear_button = make_button("清空", Glyph.CLEAR, "ghost")
        self.clear_button.clicked.connect(self.clear)
        actions.addWidget(self.paste_button)
        actions.addWidget(self.clear_button)
        actions.addStretch()
        self.copy_button = make_button("复制译文", Glyph.COPY)
        self.copy_button.clicked.connect(self.copy)
        self.translate_button = make_button("翻译", Glyph.TRANSLATE, "primary")
        self.translate_button.clicked.connect(self.primary)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.translate_button)
        layout.addLayout(actions)
        self.refresh()

    # ---------- following the settings and the text ----------

    @property
    def settings(self):
        return self.main.settings

    def refresh(self):
        """Settings may have changed: the guide, the style, and the target if it follows the text."""
        self.guide.refresh(self.settings)
        style = self.settings.selection_style
        self.styles.set_current(STYLE_KEYS.index(style) if style in STYLES else 0, animate=self.isVisible())
        self._follow_text()
        self._sync()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()  # The style may have been changed in the popup meanwhile.

    def _original_changed(self):
        text = self.original.toPlainText()
        self.original_count.setText(f"{len(text.strip()):,} 字" if text.strip() else "")
        self.follow_timer.start()
        self._sync()

    def _follow_text(self):
        """Unless the target was picked by hand, it follows the 互译 pair: text in the user's
        own language goes to the other one, anything else comes home."""
        if self.pinned or self.running:
            return
        native, foreign = self.settings.selection_native, self.settings.selection_foreign
        source = self.source.currentData()
        if source:
            target = foreign if source == native else native
        else:
            text = self.original.toPlainText()[:4000]
            target = target_language(text, native, foreign) if text.strip() else foreign
        self.target.setCurrentIndex(max(0, self.target.findData(target)))
        self._sync()

    def _pin(self, _index: int):
        self.pinned = True
        self._sync()

    def _guess_source(self) -> str:
        """The language the text seems to be in, for swapping: one of the pair if its script says so."""
        script = script_of(self.original.toPlainText()[:4000])
        for name in (self.settings.selection_native, self.settings.selection_foreign):
            if SCRIPTS.get(name) == script:
                return name
        return ""

    def swap(self):
        source = self.source.currentData() or self._guess_source()
        target = self.target.currentData()
        if not source or source == target:
            return
        self.source.setCurrentIndex(max(0, self.source.findData(target)))
        self.target.setCurrentIndex(max(0, self.target.findData(source)))
        self.pinned = True
        self._sync()

    def _restyle(self, index: int):
        from dataclasses import replace

        style = STYLE_KEYS[index]
        if style != self.settings.selection_style:
            self.main.settings = replace(self.settings, selection_style=style)
            try:
                self.main.settings.save()
            except OSError:
                pass
        self._sync()

    # ---------- the actions ----------

    def _wanted(self) -> tuple[str, str, str, str]:
        return (self.original.toPlainText().strip(), self.source.currentData(), self.target.currentData(),
                STYLE_KEYS[self.styles.current()])

    def _resumable(self) -> bool:
        job = self.job
        return bool(job and job.done and not job.finished and (job.text, job.source, job.target, job.style) == self._wanted())

    def _sync(self, *_):
        """Buttons, the figure over the translation and the line under it, from the state."""
        text = self.original.toPlainText().strip()
        if self.running:
            self.translate_button.setText("停止")
            self.translate_button.set_icon(Glyph.STOP)
        else:
            self.translate_button.setText("继续翻译" if self._resumable() else "翻译")
            self.translate_button.set_icon(Glyph.TRANSLATE)
        self.translate_button.setEnabled(self.running or bool(text))
        self.copy_button.setEnabled(bool(self.result.toPlainText().strip()))
        self.paste_button.setEnabled(not self.running)
        for widget in (self.source, self.target, self.swap_button, self.styles):
            widget.setEnabled(not self.running)
        self.original.setReadOnly(self.running)
        self.result.setReadOnly(self.running or not self.result.toPlainText())
        job = self.job
        count = len(job.pieces) if job else 0
        if job and self.running:
            self.progress.setText(f"第 {min(len(job.done) + 1, count)} / {count} 段" if count > 1 else "正在翻译…")
        elif job and not job.finished:
            self.progress.setText(f"已译 {len(job.done)} / {count} 段")
        else:
            result = self.result.toPlainText().strip()
            self.progress.setText(f"{len(result):,} 字" if result else "")
        self.bar.setVisible(bool(job) and count > 1 and (self.running or not job.finished))
        if job and count:
            self.bar.set_fraction(len(job.done) / count)

    def primary(self):
        if self.running:
            self.stop()
            return
        text, source, target, style = self._wanted()
        if not text:
            return
        if len(text) > LIMIT:
            self.main.set_notice(f"一次最多翻译 {LIMIT // 10000} 万字，这段有 {len(text):,} 字，请分几次粘贴。")
            return
        self.main.set_notice()
        if not self._resumable():
            self.job = Job(text, source, target, style, pieces(text))
            self.result.clear()
        self.running = True
        self.cancel = threading.Event()
        self._sync()
        self._next()

    def stop(self):
        if self.cancel:
            self.cancel.set()
        self.running = False
        self.generation += 1
        self._sync()

    def _next(self):
        from .cleanup import ApiCleaner, CleanupError
        from .settings import read_key
        from .vocabulary import Vocabulary

        from . import translation as translating

        job, cancel, generation = self.job, self.cancel, self.generation
        index = len(job.done)
        if index >= len(job.pieces):
            self.running = False
            self._sync()
            if len(job.pieces) > 1:
                self.main.toast.show_text(f"译完了，共 {len(job.pieces)} 段")
            chars = count_chars(job.text)
            self.main.achieve(lambda tally: tally.translated(job.target, job.style, datetime.now(), False, chars))
            return
        piece = job.pieces[index][0]
        settings = self.settings

        def work(_report):
            from .checks import CheckResult

            try:
                cleaner = ApiCleaner(read_key(api_base_url=settings.api_base_url), settings.model,
                                     settings.api_timeout, base_url=settings.api_base_url)
                glossary = Vocabulary.for_settings(settings).glossary_for(piece)
                return translating.translate(cleaner, piece, job.target, job.style, glossary, cancel, source=job.source)
            except CleanupError as error:
                return CheckResult(False, str(error))

        def done(value):
            if generation != self.generation or job is not self.job or cancel.is_set():
                return  # Stopped, cleared or started over meanwhile.
            if isinstance(value, Translation):
                job.done.append(value.text)
                self._append(job, index, value.text)
                self.main.record_api_usage(value.usage)
                self._next()
            else:
                self.running = False
                message = getattr(value, "message", "翻译没有完成。")
                where = f"第 {index + 1} 段" if len(job.pieces) > 1 else "这段文字"
                self.main.set_notice(f"{where}没有翻译成功：{message} 点“继续翻译”可以从这里接着翻。"
                                     if job.done else f"{where}没有翻译成功：{message}")
                self._sync()

        run_task(self.main, work, done)

    def _append(self, job: Job, index: int, text: str):
        """Add a piece's translation at the end without moving the view: the reader may be
        reading the first ones while the rest arrive."""
        cursor = QTextCursor(self.result.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText((job.pieces[index - 1][1] if index else "") + text)

    def paste(self):
        text = QApplication.clipboard().text()
        if not text.strip():
            self.paste_button.flash("剪贴板是空的", Glyph.INFO)
            return
        self.original.setFocus()
        self.original.insertPlainText(text)

    def clear(self):
        self.stop()
        self.job = None
        self.pinned = False
        self.original.clear()
        self.result.clear()
        self.main.set_notice()
        self._follow_text()

    def copy(self):
        text = self.result.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_button.flash("已复制")

    def shutdown(self):
        if self.cancel:
            self.cancel.set()

