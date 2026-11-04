import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from saywell import selection
from saywell.selection import MAX_CHARS, MouseWatcher, clip, copy_selection, may_copy


def test_selections_are_tidied_and_long_ones_cut():
    assert clip("  第一行\r\n第二行\r ") == ("第一行\n第二行", False)
    text, cut = clip("字" * (MAX_CHARS + 5))
    assert len(text) == MAX_CHARS and cut


def test_ctrl_c_is_never_sent_to_terminals_or_password_managers(monkeypatch):
    monkeypatch.setattr(selection, "window_class", lambda window: {1: "cascadia_hosting_window_class"}.get(window, "chrome_widgetwin_1"))
    assert "终端" in may_copy(1, "windowsterminal.exe")
    assert "终端" in may_copy(2, "pwsh.exe")
    assert "密码" in may_copy(2, "keepassxc.exe")
    assert may_copy(2, "chrome.exe") == ""


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def watcher(qt_app):
    watcher = MouseWatcher()
    watcher.double = 0.5
    seen = []
    watcher.events.selected.connect(lambda x, y: seen.append((x, y)))
    return watcher, seen


def test_a_drag_and_a_double_click_count_as_selecting_and_a_click_does_not(watcher):
    watcher, seen = watcher
    down, up = selection.WM_LBUTTONDOWN, selection.WM_LBUTTONUP
    watcher.handle(down, 100, 100, 0.0)
    watcher.handle(up, 102, 101, 0.1)  # A click.
    assert seen == []
    watcher.handle(down, 102, 101, 0.3)
    watcher.handle(up, 102, 101, 0.35)  # The second click of a double click: a word.
    assert seen == [(102, 101)]
    watcher.handle(down, 300, 100, 5.0)
    watcher.handle(up, 420, 130, 5.4)  # A drag across text.
    assert seen[-1] == (420, 130)
    watcher.handle(down, 10, 10, 9.0)
    watcher.handle(up, 11, 10, 9.1)
    watcher.handle(down, 300, 300, 9.2)
    watcher.handle(up, 300, 300, 9.3)  # Two clicks far apart are not a double click.
    assert len(seen) == 2


def test_any_button_going_down_is_reported(watcher):
    watcher, _seen = watcher
    pressed = []
    watcher.events.pressed.connect(lambda x, y: pressed.append((x, y)))
    watcher.handle(selection.WM_RBUTTONDOWN, 5, 6, 0.0)
    assert pressed == [(5, 6)]


class Clipboard:
    def __init__(self, text):
        self.value, self.number = text, 1

    def mimeData(self):
        from PySide6.QtCore import QMimeData

        data = QMimeData()
        data.setText(self.value)
        return data

    def setMimeData(self, data):
        self.value, self.number = data.text(), self.number + 1

    def text(self):
        return self.value


def run_copy(clipboard, copied, keys_up_after=0):
    """copy_selection with its waiting done at once, in order."""
    answers, waiting = [], []
    pressed = {"polls": keys_up_after}

    def keys_held():
        pressed["polls"] -= 1
        return pressed["polls"] >= 0

    def send_copy():
        if copied is not None:
            clipboard.value, clipboard.number = copied, clipboard.number + 1
        return True

    copy_selection(clipboard, answers.append, keys_held, send_copy, lambda: clipboard.number,
                   lambda ms, step: waiting.append(step))
    while waiting and not answers:
        waiting.pop(0)()
        if copied is None and len(waiting) > 40:
            break
    return answers


def test_copying_waits_for_the_keys_and_gives_the_clipboard_back(qt_app):
    clipboard = Clipboard("我之前复制的")
    assert run_copy(clipboard, "selected text", keys_up_after=3) == ["selected text"]
    assert clipboard.value == "我之前复制的"


def test_copying_nothing_leaves_the_clipboard_alone(qt_app, monkeypatch):
    clock = iter(float(step) / 10 for step in range(1000))
    monkeypatch.setattr(selection.time, "monotonic", lambda: next(clock))
    clipboard = Clipboard("我之前复制的")
    assert run_copy(clipboard, None) == [""]
    assert clipboard.value == "我之前复制的" and clipboard.number == 1
