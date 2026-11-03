"""The update announcement: what a new version brings, before updating and once after."""

from __future__ import annotations

import datetime
import html
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .ui import Glyph, Switch, app_icon, footer_bar, label, make_button, scroll_body
from .updates import Highlight, split_highlight

WIDTH = 580
# Each section of the notes gets its own quiet tint: blue for new, green for better, amber for fixed.
TINTS = {"新功能": ("#0066CC", "#E8F1FD"), "改进": ("#1F7A35", "#E6F5EA"), "修复": ("#A15C00", "#FFF2DC")}

STYLE = """
QLabel#announceTitle { font-size: 22px; font-weight: 700; }
QLabel#announceMeta { color: #8E8E93; font-size: 13px; }
QLabel#announceSummary { color: #1D1D1F; font-size: 15px; }
QLabel#announceItemTitle { color: #1D1D1F; font-size: 14px; font-weight: 700; }
QLabel#announceItem { color: #6E6E73; font-size: 13px; }
QLabel#announceItemPlain { color: #1D1D1F; font-size: 14px; }
QLabel#announceNote { color: #8E8E93; font-size: 12px; }
QFrame#announceCard { background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 16px; }
"""


def rich(text: str, bold: str = "#1D1D1F") -> str:
    """Escaped text with the notes' **bold** kept, set with a little more line spacing."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", rf'<span style="color:{bold}; font-weight:700">\1</span>', text)
    return f'<div style="line-height:150%">{text}</div>'


def release_date(value: str) -> str:
    try:
        day = datetime.date.fromisoformat(value)
    except ValueError:
        return ""
    return f"{day.year}年{day.month}月{day.day}日发布"


def wrapped(text: str, name: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.TextFormat.RichText)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def tag(section: str) -> QLabel:
    color, background = TINTS[section]
    widget = QLabel(section)
    # Qt draws square corners once the radius passes half the height, so both are fixed.
    widget.setFixedHeight(22)
    widget.setStyleSheet(
        f"color: {color}; background: {background}; border-radius: 10px; padding: 0 10px;"
        "font-size: 12px; font-weight: 700;"
    )
    return widget


def item_row(highlight: Highlight) -> QWidget:
    color = TINTS[highlight.section][0]
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    # A small dot in the section's colour, level with the first line.
    marker = QWidget()
    marker.setFixedWidth(8)
    marker_layout = QVBoxLayout(marker)
    marker_layout.setContentsMargins(0, 8, 0, 0)
    dot = QLabel()
    dot.setFixedSize(6, 6)
    dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
    marker_layout.addWidget(dot)
    marker_layout.addStretch()
    layout.addWidget(marker)
    text = QVBoxLayout()
    text.setSpacing(3)
    title, body = split_highlight(highlight.text)
    if title:
        text.addWidget(wrapped(html.escape(title, quote=False), "announceItemTitle"))
        text.addWidget(wrapped(rich(body), "announceItem"))
    else:
        text.addWidget(wrapped(rich(body), "announceItemPlain"))
    layout.addLayout(text, 1)
    return row


class AnnouncementDialog(QDialog):
    """A release's summary and highlights. Before an update it offers to install it, with a switch
    to stop announcing this version; after one it only says what is new."""

    def __init__(
        self,
        version: str,
        summary: str,
        highlights: tuple[Highlight, ...],
        *,
        changelog: str,
        date: str = "",
        size: int = 0,
        installable: bool = True,
        after_update: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.choice = "later"
        self.changelog = changelog
        self.setWindowTitle("更新公告 · 好好说" if not after_update else "新内容 · 好好说")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(WIDTH - 60)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        head = QHBoxLayout(header)
        head.setContentsMargins(30, 28, 30, 12)
        head.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(56, 56))
        logo.setFixedSize(56, 56)
        head.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addStretch()
        titles.addWidget(label(f"已更新到 {version}" if after_update else f"好好说 {version} 已发布", "announceTitle"))
        if after_update:
            meta = "看看这一版有什么新变化"
        else:
            meta = " · ".join(
                part for part in (release_date(date), f"约 {size / 1_000_000:.0f} MB" if size else "") if part
            )
        if meta:
            titles.addWidget(label(meta, "announceMeta"))
        titles.addStretch()
        head.addLayout(titles, 1)
        outer.addWidget(header)

        self.area, body = scroll_body(16)
        body.setContentsMargins(30, 6, 26, 20)
        if summary:
            body.addWidget(wrapped(rich(summary), "announceSummary"))
        if highlights:
            body.addWidget(self._card(highlights))
        links = QHBoxLayout()
        links.setContentsMargins(0, 0, 0, 0)
        more = make_button("查看完整更新日志", Glyph.GLOBE, "link")
        more.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.changelog)))
        links.addWidget(more)
        links.addStretch()
        body.addLayout(links)
        body.addStretch()
        outer.addWidget(self.area, 1)

        footer, buttons = footer_bar()
        self.skip = None
        if after_update:
            buttons.addStretch()
            done = make_button("知道了", None, "primary")
            done.clicked.connect(self.accept)
            buttons.addWidget(done)
            done.setDefault(True)
        else:
            if installable:
                note = "下载完自动安装并重新打开，设置、模型和词库都保留。"
            else:
                note = "这份程序不是用安装包装的，不能一键更新，请到下载页下载新的安装包。"
            body.insertWidget(body.count() - 1, label(note, "announceNote", wrap=True))
            self.skip = Switch("这个版本不再提醒")
            self.skip.setToolTip("更新按钮仍在主窗口顶部；以后发布的新版本照常提醒。")
            buttons.addWidget(self.skip)
            buttons.addStretch()
            later = make_button("以后再说")
            later.clicked.connect(self.reject)
            buttons.addWidget(later)
            go = make_button("立即更新" if installable else "前往下载页", Glyph.DOWNLOAD if installable else Glyph.GLOBE, "primary")
            go.clicked.connect(self.update_now)
            buttons.addWidget(go)
            go.setDefault(True)
        outer.addWidget(footer)
        self._fit()

    def _card(self, highlights: tuple[Highlight, ...]) -> QFrame:
        card = QFrame()
        card.setObjectName("announceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(14)
        sections = [name for name in TINTS if any(item.section == name for item in highlights)]
        for index, name in enumerate(sections):
            if index:
                layout.addSpacing(6)
                divider = QFrame()
                divider.setObjectName("divider")
                layout.addWidget(divider)
                layout.addSpacing(6)
            heading = QHBoxLayout()
            heading.addWidget(tag(name))
            heading.addStretch()
            layout.addLayout(heading)
            for item in highlights:
                if item.section == name:
                    layout.addWidget(item_row(item))
        return card

    def _fit(self):
        """As tall as the content needs, within the screen; longer notes scroll."""
        self.ensurePolished()  # Measure with the style sheet's font sizes, not the defaults.
        # Not laid out until shown, so the text is measured at the width it will have.
        header, footer = (self.layout().itemAt(index).widget() for index in (0, 2))
        needed = self.area.widget().layout().totalHeightForWidth(WIDTH)
        chrome = header.sizeHint().height() + footer.sizeHint().height()
        screen = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(WIDTH, max(340, min(needed + chrome, 720, screen - 80)))

    def update_now(self):
        self.choice = "update"
        self.accept()

    @property
    def skipped(self) -> bool:
        return bool(self.skip and self.skip.isChecked())
