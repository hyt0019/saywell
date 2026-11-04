"""Selection translation on screen: the small button that appears after selecting text, the
popup with the translation, and the controller that ties reading, translating, copying and
replacing together.

Both windows never take the focus: the program the text was selected in stays in front with
its selection, so 替换 can type over it and the user can keep working with the popup open.
"""

from __future__ import annotations

import threading
from dataclasses import replace

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .translation import LANGUAGES, STYLES, Translation, target_language
from .ui import ACCENT, INK, Glyph, Spinner, Tween, draw_icon, label, make_button, mix

FAINT = "#8E8E93"
WIDTH = 384
ICON_SECONDS = 4.0  # The button goes away unless it is used or hovered.
NEAR = 120  # How close (physical pixels) the mouse must be to the selection for it to count.


def to_logical(x: int, y: int) -> QPoint:
    """A point in physical pixels (hooks, UI Automation) as Qt places windows. Qt keeps each
    screen's top-left corner in physical pixels and scales from there."""
    for screen in QGuiApplication.screens():
        area, ratio = screen.geometry(), screen.devicePixelRatio()
        left, top = area.x(), area.y()
        if left <= x < left + area.width() * ratio and top <= y < top + area.height() * ratio:
            return QPoint(round(left + (x - left) / ratio), round(top + (y - top) / ratio))
    return QPoint(x, y)


def logical_rect(rect: tuple[int, int, int, int]) -> QRect:
    top_left = to_logical(rect[0], rect[1])
    bottom_right = to_logical(rect[2], rect[3])
    return QRect(top_left, bottom_right)


def near(point: tuple[int, int], area: tuple[int, int, int, int] | None) -> bool:
    if area is None:
        return True  # The program does not say where; trust the gesture.
    x, y = point
    left, top, right, bottom = area
    return left - NEAR <= x <= right + NEAR and top - NEAR <= y <= bottom + NEAR


class Floating(QWidget):
    """A window that stays on top and is never activated, so clicks on it leave the focus
    where it was."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fade = Tween(self, duration=160, on_change=self._faded)

    def appear(self):
        if not self.isVisible():
            self.fade.to(0.0, animate=False)
            self.setWindowOpacity(0.0)
            self.show()
        self.fade.to(1.0)

    def vanish(self):
        if self.isVisible():
            self.fade.to(0.0, 140)

    def _faded(self):
        self.setWindowOpacity(max(0.0, min(1.0, self.fade.value)))
        if self.fade.value <= 0.001 and self.fade.target == 0.0 and self.isVisible():
            self.hide()

    def contains_physical(self, x: int, y: int) -> bool:
        return self.isVisible() and self.frameGeometry().contains(to_logical(x, y))


class SelectionIcon(Floating):
    """A small round-cornered button beside the selection: click it to translate."""

    clicked = Signal()
    SIZE = 30
    MARGIN = 8  # Room for the shadow.

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE + 2 * self.MARGIN, self.SIZE + 2 * self.MARGIN)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("翻译选中的文字")
        self.hover = Tween(self, duration=120)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.vanish)

    def show_at(self, point: QPoint):
        """Just below and right of where the mouse let go, inside the screen."""
        screen = (QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()).availableGeometry()
        x = min(point.x() + 6, screen.right() - self.width())
        y = point.y() + 10
        if y + self.height() > screen.bottom():
            y = point.y() - self.height() - 6
        self.move(x, y)
        self.appear()
        self.timer.start(int(ICON_SECONDS * 1000))

    def enterEvent(self, event):
        self.hover.to(1.0)
        self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0.0, 200)
        self.timer.start(1500)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.timer.stop()
            self.hide()
            self.clicked.emit()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.MARGIN, self.MARGIN, self.SIZE, self.SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((5, 10), (3, 16), (1.5, 24)):  # A soft shadow, a little lower.
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(box.adjusted(-spread, -spread + 2, spread, spread + 2), 9 + spread, 9 + spread)
        painter.setBrush(mix(QColor("#FFFFFF"), QColor("#F0F5FE"), self.hover.value))
        painter.drawRoundedRect(box, 9, 9)
        draw_icon(painter, Glyph.TRANSLATE, box.adjusted(7, 7, -7, -7), ACCENT, 1.6)


class TranslationPopup(Floating):
    """The translation beside the selection: where it goes, the original in brief, the
    translation (with senses for a single word), and copy and replace."""

    copy = Signal()
    replace_selection = Signal()
    language = Signal(str)  # Another target language was chosen.
    retry = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedWidth(WIDTH + 28)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 14, 20)
        self.panel = QFrame()
        self.panel.setObjectName("translationPanel")
        self.panel.setStyleSheet(
            "QFrame#translationPanel { background: #FFFFFF; border: 1px solid #E3E3E8; border-radius: 14px; }"
        )
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 40))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(16, 10, 10, 12)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.route = make_button("", None, "heroChip")
        self.route.set_trailing(Glyph.CHEVRON_DOWN)
        self.route.setToolTip("换一种目标语言")
        self.route.clicked.connect(self.toggle_languages)
        head.addWidget(self.route)
        head.addStretch()
        self.close_button = make_button("", Glyph.CANCEL, "heroChip")
        self.close_button.setFixedWidth(30)
        self.close_button.setToolTip("关闭（Esc）")
        self.close_button.clicked.connect(self.dismiss)
        head.addWidget(self.close_button)
        layout.addLayout(head)

        self.languages = QWidget()
        grid = QGridLayout(self.languages)
        grid.setContentsMargins(0, 6, 6, 4)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        self.language_buttons = {}
        for index, name in enumerate(LANGUAGES):
            button = make_button(name, None, "chip")
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, name=name: self._choose(name))
            grid.addWidget(button, index // 3, index % 3)
            self.language_buttons[name] = button
        self.languages.hide()
        layout.addWidget(self.languages)

        layout.addSpacing(6)
        self.original = label("", "translationOriginal", wrap=True)
        self.original.setStyleSheet(f"color: {FAINT}; font-size: 12px;")
        layout.addWidget(self.original)
        layout.addSpacing(8)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 6, 0)
        body.setSpacing(8)
        self.result = QLabel()
        self.result.setTextFormat(Qt.TextFormat.PlainText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result.setCursor(Qt.CursorShape.IBeamCursor)
        self.result.setStyleSheet(f"color: {INK}; font-size: 15px;")
        self.result.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.result.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        body.addWidget(self.result)
        self.senses = QLabel()
        self.senses.setTextFormat(Qt.TextFormat.RichText)
        self.senses.setWordWrap(True)
        self.senses.setStyleSheet(f"color: {INK}; font-size: 13px;")
        self.senses.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        body.addWidget(self.senses)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        layout.addWidget(self.scroll)

        self.waiting = QWidget()
        row = QHBoxLayout(self.waiting)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)
        self.spinner = Spinner(15)
        row.addWidget(self.spinner)
        self.waiting_text = label("正在翻译…", "muted")
        row.addWidget(self.waiting_text)
        row.addStretch()
        layout.addWidget(self.waiting)

        self.problem = QWidget()
        trouble = QHBoxLayout(self.problem)
        trouble.setContentsMargins(0, 0, 6, 0)
        trouble.setSpacing(10)
        self.problem_text = label("", "checkText", wrap=True)
        self.problem_text.setProperty("tone", "fail")
        trouble.addWidget(self.problem_text, 1)
        self.retry_button = make_button("重试", Glyph.REFRESH, "heroGhost")
        self.retry_button.clicked.connect(self.retry.emit)
        trouble.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.problem)

        layout.addSpacing(10)
        foot = QHBoxLayout()
        foot.setSpacing(4)
        self.copy_button = make_button("复制", Glyph.COPY, "heroChip")
        self.copy_button.clicked.connect(self.copy.emit)
        foot.addWidget(self.copy_button)
        self.replace_button = make_button("替换原文", Glyph.SEND, "heroChip")
        self.replace_button.setToolTip("用译文替换选中的文字")
        self.replace_button.clicked.connect(self.replace_selection.emit)
        foot.addWidget(self.replace_button)
        foot.addStretch()
        self.note = label("", "caption")
        foot.addWidget(self.note)
        foot.addSpacing(6)
        layout.addLayout(foot)
        self.target = ""
        self.anchor: QRect | None = None
        self.dragging: QPoint | None = None

    # What it shows

    def show_waiting(self, original: str, target: str, editable: bool | None, cut: bool):
        self.target = target
        self.route.setText(f"译成{target}")
        for name, button in self.language_buttons.items():
            button.setChecked(name == target)
        # Two lines of it at most: enough to see what is being translated.
        brief = " ".join(original.split())
        metrics = QFontMetrics(self.original.font())
        self.original.setText(metrics.elidedText(brief, Qt.TextElideMode.ElideRight, 2 * (WIDTH - 40) - 30))
        self.result.clear()
        self.senses.clear()
        self.senses.hide()
        self.scroll.hide()
        self.problem.hide()
        self.waiting.show()
        self.spinner.start()
        self.copy_button.setEnabled(False)
        self.replace_button.setVisible(editable is not False)
        self.replace_button.setEnabled(False)
        self.note.setText(f"只译了前 {len(original):,} 字" if cut else "")
        self._fit()

    def show_translation(self, translation: Translation, style: str):
        self.spinner.stop()
        self.waiting.hide()
        self.problem.hide()
        self.result.setText(translation.text)
        if translation.senses:
            rows = "".join(
                f"<tr><td style='color:{FAINT}; padding-right:8px'>{pos}</td><td>{meaning}</td></tr>"
                for pos, meaning in ((self._escape(p), self._escape(m)) for p, m in translation.senses)
            )
            self.senses.setText(f"<table cellspacing='0' cellpadding='1'>{rows}</table>")
            self.senses.show()
        self.scroll.show()
        self.copy_button.setEnabled(True)
        self.replace_button.setEnabled(True)
        if not self.note.text():
            self.note.setText(STYLES.get(style, STYLES["natural"])[0])
        self._fit()

    def show_problem(self, message: str, can_retry: bool = True):
        self.spinner.stop()
        self.waiting.hide()
        self.scroll.hide()
        self.problem_text.setText(message)
        self.retry_button.setVisible(can_retry)
        self.problem.show()
        self.copy_button.setEnabled(False)
        self.replace_button.setEnabled(False)
        self._fit()

    @staticmethod
    def _escape(text: str) -> str:
        import html

        return html.escape(text)

    def toggle_languages(self):
        self.languages.setVisible(not self.languages.isVisible())
        self._fit()

    def _choose(self, name: str):
        self.languages.hide()
        if name != self.target:
            self.language.emit(name)
        else:
            self.language_buttons[name].setChecked(True)
            self._fit()

    def _fit(self):
        """Grow with the translation up to about half the screen, then scroll."""
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        if self.scroll.isVisible():
            room = max(160, screen.height() // 2 - 160)
            width = WIDTH - 32
            needed = self._height(width - 6)
            if needed > room:  # Scrolling: the bar takes some of the width.
                width -= 8
                needed = self._height(width - 6)
            self.body.setFixedWidth(width)
            self.scroll.setFixedHeight(min(needed, room))
        self.adjustSize()
        if self.anchor is not None and self.isVisible():
            self._place()

    def _height(self, width: int) -> int:
        """What the translation and its senses take at this width; each label is given its
        own height (a word-wrapped label left to itself asks for more and centers its text)."""
        box = QRect(0, 0, width, 100_000)
        flags = int(Qt.TextFlag.TextWordWrap)
        text = QFontMetrics(self.result.font()).boundingRect(box, flags, self.result.text()).height() + 4
        self.result.setFixedHeight(text)
        height = text
        if self.senses.isVisible():
            senses = self.senses.heightForWidth(width) + 2
            self.senses.setFixedHeight(senses)
            height += 8 + senses
        return height

    # Where it goes

    def show_near(self, anchor: QRect):
        """Below the selection, or above it when there is no room; inside the screen."""
        self.anchor = anchor
        self.adjustSize()
        self._place()
        self.appear()

    def _place(self):
        anchor = self.anchor
        screen = (QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()).availableGeometry()
        x = max(screen.left(), min(anchor.left() - 14, screen.right() - self.width()))
        y = anchor.bottom() + 4
        if y + self.height() > screen.bottom():
            y = max(screen.top(), anchor.top() - self.height() + 4)
        self.move(x, y)

    def dismiss(self):
        self.spinner.stop()
        self.vanish()
        self.closed.emit()

    # Moved by its empty parts

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.dragging is not None:
            self.anchor = None  # Placed by hand: it stays where it was put.
            self.move(event.globalPosition().toPoint() - self.dragging)

    def mouseReleaseEvent(self, event):
        self.dragging = None


class SelectionTranslate:
    """Watches for selections (as the settings say), reads them, and shows and fills the icon
    and the popup. The window owns the hotkeys: it calls hotkey() and escape()."""

    HOTKEY, ESCAPE = 4, 5

    def __init__(self, window):
        from .selection import MouseWatcher, SelectionReader

        self.window = window
        self.reader = SelectionReader()
        self.reader.read.connect(self._read)
        self.watcher: MouseWatcher | None = None
        self.make_watcher = MouseWatcher
        self.icon = SelectionIcon()
        self.icon.clicked.connect(self._icon_clicked)
        self.popup = TranslationPopup()
        self.popup.copy.connect(self.copy)
        self.popup.replace_selection.connect(self.replace)
        self.popup.language.connect(self._retarget)
        self.popup.retry.connect(lambda: self.translate(self.target_language))
        self.popup.closed.connect(self._closed)
        self.pending = None  # The Selection shown in the icon, waiting for a click.
        self.current = None  # The Selection in the popup.
        self.target = None  # Where it was selected, for 替换.
        self.target_language = ""
        self.translation: Translation | None = None
        self.generation = 0
        self.cancel: threading.Event | None = None
        self.mouse_timer = QTimer()
        self.mouse_timer.setSingleShot(True)
        self.mouse_timer.setInterval(120)  # Let the program settle its selection first.
        self.mouse_timer.timeout.connect(lambda: self.reader.ask(("mouse", self.mouse_at)))
        self.mouse_at = (0, 0)
        self.enabled = False

    @property
    def settings(self):
        return self.window.settings

    # Turning it on and off

    def configure(self) -> str:
        """Follow the settings; returns a problem to show, or ""."""
        from .shortcuts import parse_shortcut

        settings = self.settings
        hotkeys = self.window.hotkeys
        self.enabled = settings.selection_translate
        problem = ""
        if hotkeys:
            hotkeys.unregister(self.HOTKEY)
            if self.enabled:
                shortcut = parse_shortcut(settings.selection_hotkey)
                if shortcut is None or shortcut.solo or not hotkeys.register(self.HOTKEY, shortcut.modifiers, shortcut.key):
                    problem = f"划词翻译的快捷键 {settings.selection_hotkey} 被其他程序占用，请在设置中更换。"
        self._watch(self.enabled and settings.selection_trigger in ("icon", "direct"))
        if not self.enabled:
            self.icon.hide()
            if self.popup.isVisible():
                self.popup.hide()
                self._closed()  # Esc must not stay taken from every program.
        return problem

    def _watch(self, on: bool):
        if on and self.watcher is None:
            watcher = self.make_watcher()
            watcher.events.selected.connect(self._mouse_selected)
            watcher.events.pressed.connect(self._mouse_pressed)
            if watcher.start():
                self.watcher = watcher
        elif not on and self.watcher is not None:
            self.watcher.stop()
            self.watcher = None

    def shutdown(self):
        self._watch(False)
        self.reader.stop()
        if self.cancel:
            self.cancel.set()
        self.icon.hide()
        self.popup.hide()

    # Where a selection comes from

    def _mouse_selected(self, x: int, y: int):
        if not self.enabled or self.settings.selection_trigger == "hotkey":
            return
        if self.popup.contains_physical(x, y) or self.icon.contains_physical(x, y):
            return  # Selecting inside the popup itself.
        self.mouse_at = (x, y)
        self.mouse_timer.start()

    def _mouse_pressed(self, x: int, y: int):
        if self.icon.isVisible() and not self.icon.contains_physical(x, y):
            self.icon.vanish()
        if self.popup.isVisible() and not self.popup.contains_physical(x, y):
            self.popup.dismiss()

    def hotkey(self):
        if not self.enabled:
            return
        from .selection import cursor

        self.icon.hide()
        self.reader.ask(("hotkey", cursor()))

    def escape(self):
        self.icon.hide()
        if self.popup.isVisible():
            self.popup.dismiss()

    def _read(self, request, found):
        from .windows import current_target

        kind, point = request
        if found is None or found.exe in self.settings.selection_excluded:
            if kind == "hotkey" and found is not None:
                self._message(point, "这个程序在设置里排除了，不做划词翻译。")
            return
        if kind == "mouse":
            if not found.text or found.blocked or not near(point, found.area):
                return
            if self.popup.isVisible() and self.current and found.text == self.current.text:
                return  # The same selection again.
            self.target = current_target()
            if self.settings.selection_trigger == "direct":
                self.open(found, point)
            else:
                self.pending = (found, point)
                self.icon.show_at(to_logical(*point))
            return
        if found.blocked:
            self._message(point, "密码框和密码管理器里的文字不会读取。")
            return
        self.target = current_target()
        if found.text:
            self.open(found, point)
            return
        self._copy_fallback(found, point)

    def _copy_fallback(self, found, point):
        """The program offers no text: copy it, if that is safe here."""
        from .selection import clip, may_copy
        from .windows import is_password, modifiers_held, send_keys, user32

        why = may_copy(found.window, found.exe)
        if not why and self.target is not None and is_password(self.target):
            why = "密码框和密码管理器里的文字不会读取。"
        if why:
            self._message(point, why)
            return
        from .selection import copy_selection

        def copied(text: str):
            text, cut = clip(text)
            if not text:
                self._message(point, "没有读到选中的文字。先选中文字，再按快捷键。")
                return
            self.open(replace(found, text=text, cut=cut, via="clipboard"), point)

        copy_selection(
            QApplication.clipboard(), copied, modifiers_held,
            lambda: send_keys((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2)),
            user32.GetClipboardSequenceNumber, QTimer.singleShot,
        )

    def _icon_clicked(self):
        if self.pending:
            found, point = self.pending
            self.pending = None
            self.open(found, point)

    # The popup

    def open(self, found, point):
        self.current = found
        self.target_language = target_language(found.text, self.settings.selection_native, self.settings.selection_foreign)
        anchor = logical_rect(found.rect) if found.rect and near(point, found.area) else None
        if anchor is None:
            spot = to_logical(*point)
            anchor = QRect(spot.x(), spot.y() - 10, 1, 20)
        self.popup.show_waiting(found.text, self.target_language, found.editable, found.cut)
        self.popup.show_near(anchor)
        self._register_escape(True)
        self._watch_for_outside_clicks()
        self.translate(self.target_language)

    def _message(self, point, text: str):
        spot = to_logical(*point)
        self.current = None
        self.popup.show_waiting("", self.target_language or self.settings.selection_foreign, False, False)
        self.popup.show_problem(text, can_retry=False)
        self.popup.route.hide()
        self.popup.show_near(QRect(spot.x(), spot.y() - 10, 1, 20))
        self._register_escape(True)
        self._watch_for_outside_clicks()
        QTimer.singleShot(3500, lambda: self._expire(text))

    def _expire(self, shown: str):
        if self.current is None and self.popup.isVisible() and self.popup.problem_text.text() == shown:
            self.popup.dismiss()

    def _retarget(self, language: str):
        self.target_language = language
        if self.current:
            self.popup.show_waiting(self.current.text, language, self.current.editable, self.current.cut)
            self.translate(language)

    def translate(self, language: str):
        from .cleanup import ApiCleaner, CleanupError
        from .settings import read_key
        from .ui import run_task
        from .vocabulary import Vocabulary

        if self.current is None:
            return
        from . import translation as translating

        self.popup.route.show()
        self.generation += 1
        generation, text = self.generation, self.current.text
        if self.cancel:
            self.cancel.set()
        cancel = self.cancel = threading.Event()
        settings = self.settings
        style = settings.selection_style
        self.translation = None

        def work(_report):
            from .checks import CheckResult

            try:
                cleaner = ApiCleaner(read_key(api_base_url=settings.api_base_url), settings.model,
                                     settings.api_timeout, base_url=settings.api_base_url)
                glossary = Vocabulary.for_settings(settings).glossary_for(text)
                return translating.translate(cleaner, text, language, style, glossary, cancel)
            except CleanupError as error:
                return CheckResult(False, str(error))

        def done(value):
            if generation != self.generation or cancel.is_set():
                return
            if isinstance(value, Translation):
                self.translation = value
                self.popup.show_translation(value, style)
                self.window.record_api_usage(value.usage)
            else:
                self.popup.show_problem(getattr(value, "message", "翻译没有完成，请重试。"))

        run_task(self.window, work, done)

    def copy(self):
        if self.translation:
            QApplication.clipboard().setText(self.translation.text)
            self.popup.copy_button.flash("已复制")

    def replace(self):
        from .windows import PasteError, paste_text

        if not self.translation or self.target is None:
            return
        try:
            paste_text(self.translation.text, self.target, QApplication.clipboard(),
                       keep_result=self.settings.keep_in_clipboard, single_line=False)
        except PasteError as error:
            self.popup.show_problem(str(error), can_retry=False)
            return
        self.popup.dismiss()

    def _closed(self):
        if self.cancel:
            self.cancel.set()
        self.current = None
        self._register_escape(False)
        if self.settings.selection_trigger == "hotkey":
            self._watch(False)

    def _register_escape(self, on: bool):
        hotkeys = self.window.hotkeys
        if not hotkeys:
            return
        if on:
            if self.ESCAPE not in hotkeys.registered:
                hotkeys.register(self.ESCAPE, 0, 0x1B)
        else:
            hotkeys.unregister(self.ESCAPE)

    def _watch_for_outside_clicks(self):
        """With the shortcut alone the mouse is not watched, except while the popup is open, so a
        click elsewhere can close it."""
        if self.watcher is None and self.enabled:
            self._watch(True)

