"""The update announcement: what a new version brings, before updating and once after."""

from __future__ import annotations

import datetime
import html
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import styled
from .ui import CheckBox, LogoLabel, ThemedLabel, footer_bar, label, make_button, scroll_body
from .updates import HIGHLIGHT_SECTIONS, Highlight, split_highlight

WIDTH = 580

# Quiet on purpose, like a system settings list: grey section names over white grouped cards,
# hairlines between the points, and only type weight and grey to set titles apart.
STYLE = """
QLabel#announceTitle { font-size: 22px; font-weight: 700; }
QLabel#announceMeta { color: #8C8376; font-size: 13px; }
QLabel#announceSummary { color: #1D2240; font-size: 15px; }
QLabel#announceSection { color: #8C8376; font-size: 13px; font-weight: 600; padding-left: 2px; }
QLabel#announceItemTitle { color: #1D2240; font-size: 14px; font-weight: 600; }
QLabel#announceItem { color: #776F63; font-size: 13px; }
QLabel#announceItemPlain { color: #1D2240; font-size: 14px; }
QLabel#announceNote { color: #8C8376; font-size: 12px; }
QLabel#announceLink { font-size: 13px; padding-left: 2px; }
QFrame#announceGroup { background: #FFFCF7; border: 1px solid #E8E0D3; border-radius: 14px; }
QFrame#announceRule { background: #EEE7DB; min-height: 1px; max-height: 1px; border: none; }
"""


def rich(text: str, bold: str = "#1D2240") -> str:
    """Escaped text with the notes' **bold** kept, set with a little more line spacing."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", rf'<span style="color:{bold}; font-weight:600">\1</span>', text)
    return f'<div style="line-height:150%">{text}</div>'


def release_date(value: str) -> str:
    try:
        day = datetime.date.fromisoformat(value)
    except ValueError:
        return ""
    return f"{day.year}年{day.month}月{day.day}日"


def wrapped(text: str, name: str) -> QLabel:
    widget = ThemedLabel(text, name, wrap=True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def item_row(highlight: Highlight) -> QWidget:
    row = QWidget()
    layout = QVBoxLayout(row)
    # The body's extra line spacing also falls below its last line, so less margin goes there.
    layout.setContentsMargins(18, 13, 18, 9)
    layout.setSpacing(2)
    title, body = split_highlight(highlight.text)
    if title:
        layout.addWidget(wrapped(html.escape(title, quote=False), "announceItemTitle"))
        layout.addWidget(wrapped(rich(body), "announceItem"))
    else:
        layout.addWidget(wrapped(rich(body), "announceItemPlain"))
    return row


def group(items: list[Highlight]) -> QFrame:
    """One section's points in a white card, split by hairlines that stop short of the left edge."""
    card = QFrame()
    card.setObjectName("announceGroup")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(0)
    for index, item in enumerate(items):
        if index:
            rule = QHBoxLayout()
            rule.setContentsMargins(18, 0, 0, 0)
            line = QFrame()
            line.setObjectName("announceRule")
            rule.addWidget(line)
            layout.addLayout(rule)
        layout.addWidget(item_row(item))
    return card


class AnnouncementDialog(QDialog):
    """A release's summary and highlights. Before an update it offers to install it, with a box to
    stop announcing this version; after one it only says what is new."""

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
        self.setWindowTitle("新内容 · 好好说" if after_update else "更新公告 · 好好说")
        styled(self, STYLE)
        self.setMinimumWidth(WIDTH - 60)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        head = QHBoxLayout(header)
        head.setContentsMargins(30, 28, 30, 14)
        head.setSpacing(16)
        head.addWidget(LogoLabel(52), 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addStretch()
        titles.addWidget(label(f"已更新到 {version}" if after_update else f"好好说 {version}", "announceTitle"))
        if after_update:
            meta = "这一版的新内容"
        else:
            parts = ("新版本", release_date(date), f"{size / 1_000_000:.0f} MB" if size else "")
            meta = " · ".join(part for part in parts if part)
        titles.addWidget(label(meta, "announceMeta"))
        titles.addStretch()
        head.addLayout(titles, 1)
        outer.addWidget(header)

        self.area, body = scroll_body(0)
        body.setContentsMargins(30, 4, 30, 22)
        if summary:
            body.addWidget(wrapped(rich(summary), "announceSummary"))
            body.addSpacing(20)
        for name in HIGHLIGHT_SECTIONS:
            items = [item for item in highlights if item.section == name]
            if items:
                body.addWidget(label(name, "announceSection"))
                body.addSpacing(7)
                body.addWidget(group(items))
                body.addSpacing(18)
        # A plain text link, so it lines up with the section names above it.
        more = ThemedLabel(
            f'<a href="{html.escape(changelog)}" style="color:#3C488E; text-decoration:none">查看完整更新日志 ›</a>',
            "announceLink",
        )
        more.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        more.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        body.addWidget(more)
        if not after_update:
            body.addSpacing(8)
            if installable:
                note = "下载后自动安装并重新打开，设置、模型和词库都会保留。"
            else:
                note = "这份程序不是用安装包装的，不能一键更新，请到下载页下载新的安装包。"
            body.addWidget(label(note, "announceNote", wrap=True))
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
            self.skip = CheckBox("这个版本不再提醒")
            self.skip.setToolTip("主窗口顶部的“更新”按钮仍在；以后发布的新版本照常提醒。")
            buttons.addWidget(self.skip)
            buttons.addStretch()
            later = make_button("以后再说")
            later.clicked.connect(self.reject)
            buttons.addWidget(later)
            go = make_button("立即更新" if installable else "前往下载页", None, "primary")
            go.clicked.connect(self.update_now)
            buttons.addWidget(go)
            go.setDefault(True)
        outer.addWidget(footer)
        self._fit()

    def _fit(self):
        """As tall as the content needs, within the screen; longer notes scroll."""
        self.ensurePolished()  # Measure with the style sheet's font sizes, not the defaults.
        # Not laid out until shown, so the text is measured at the width it will have.
        header, footer = (self.layout().itemAt(index).widget() for index in (0, 2))
        needed = self.area.widget().layout().totalHeightForWidth(WIDTH)
        chrome = header.sizeHint().height() + footer.sizeHint().height()
        screen = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(WIDTH, max(340, min(needed + chrome, 760, screen - 80)))

    def update_now(self):
        self.choice = "update"
        self.accept()

    @property
    def skipped(self) -> bool:
        return bool(self.skip and self.skip.isChecked())
