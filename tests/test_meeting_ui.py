import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from saywell.app import MainWindow
from saywell.capture import AudioApp
from saywell.meeting import ME, OTHERS, ROOM, SKIPPED, Line, MeetingRecorder, meetings_dir
from saywell.meeting_ui import TranscriptView, duration_text, meeting_title
from saywell.settings import Settings
from saywell.ui import RECORD, STYLE

WEMEET = AudioApp("腾讯会议", "wemeetapp.exe", "", (4242,), True, True, False)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    yield app
    app.clipboard().clear()


@pytest.fixture
def window(qt_app, monkeypatch):
    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    widget = MainWindow(Settings(), native=False)
    widget.meeting.ready = lambda: (True, "")
    yield widget
    widget.shutdown()
    widget.close()


@pytest.fixture
def no_devices(monkeypatch):
    """Sources that record nothing: the recorder runs, no audio device is opened."""
    added = []
    monkeypatch.setattr(MeetingRecorder, "add_app", lambda self, pids, exe: added.append(("app", pids, exe)))
    monkeypatch.setattr(MeetingRecorder, "add_microphone", lambda self, device, who: added.append(("mic", who)))
    return added


def wait_for(condition, seconds=5.0):
    app = QApplication.instance()
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return condition()


def test_transcript_puts_late_lines_in_order_with_turn_headings(qt_app):
    view = TranscriptView()
    view.reset(True)
    view.add(Line(1, 3, OTHERS, "大家好"))
    view.add(Line(9, 12, OTHERS, "我们开始吧"))
    view.add(Line(4, 6, ME, "好的"))  # Waited for the others, arrives after a later line.
    view.add(Line(80, 82, OTHERS, "第二个议题"))
    assert [line.text for line in view.lines] == ["大家好", "好的", "我们开始吧", "第二个议题"]
    blocks = [view.document().findBlockByNumber(index).text() for index in range(4)]
    assert blocks[0].startswith("其他参会人") and blocks[0].endswith("大家好")
    assert blocks[1].startswith("我") and blocks[2].startswith("其他参会人")  # A new turn after mine.
    assert blocks[3].startswith("其他参会人   01:20")  # More than a minute on: a heading again.
    assert view.plain_text().splitlines()[1] == "[00:04] 我：好的"


def test_transcript_without_labels_and_with_a_dropped_stretch(qt_app):
    view = TranscriptView()
    view.reset(False)
    view.add(Line(1, 3, ROOM, "开始"))
    view.add(Line(5, 65, SKIPPED, ""))
    view.add(Line(70, 72, ROOM, "继续"))
    text = view.toPlainText()
    assert "其他参会人" not in text and "约 60 秒没来得及识别" in text
    assert view.plain_text() == "[00:01] 开始\n[01:10] 继续"
    assert view.characters() == 4


def test_meeting_tab_shows_its_own_card(window):
    assert [button.text() for button in window.page_buttons] == ["概览", "文字", "会议"]
    window.show_page(2)
    assert window.hero_stack.currentWidget() is window.meeting.hero
    window.show_page(0)
    assert window.hero_stack.currentWidget() is window.hero


def test_meeting_records_both_sources_and_keeps_only_text(window, no_devices, monkeypatch):
    page = window.meeting
    page.app = WEMEET
    page.hero.mic_switch.setChecked(True)
    monkeypatch.setattr(window.pipeline, "recognize_clip", lambda settings, clip, vocabulary=None: "好的")
    page.start()
    assert page.state == "recording" and page.recorder is not None
    assert no_devices == [("app", (4242,), "wemeetapp.exe"), ("mic", ME)]
    assert window.page_buttons[2].badge == RECORD
    assert page.hero.button.text() == "结束记录"
    page.recorder._publish(Line(1, 2, OTHERS, "大家好"))  # As the recorder's thread would.
    assert wait_for(lambda: page.transcript.lines)
    page.stop()
    assert page.state == "finishing"
    assert wait_for(lambda: page.state == "done")
    assert window.page_buttons[2].badge == ""
    info = page.store.info()
    assert info["app"] == "腾讯会议" and info["labels"] and info["ended"] >= info["started"]
    assert [line.text for line in page.store.lines()] == ["大家好"]
    assert "会议记好了" == page.hero.status.text()


def test_a_meeting_where_nothing_was_said_leaves_nothing_behind(window, no_devices):
    page = window.meeting
    page.app = None
    page.hero.mic_switch.setChecked(True)
    page.start()
    assert no_devices == [("mic", ROOM)]  # The microphone alone hears the whole room.
    folder = page.store.folder
    page.stop()
    assert wait_for(lambda: page.state == "idle")
    assert not folder.exists()


def test_quitting_mid_meeting_asks_first(window, no_devices, monkeypatch):
    page = window.meeting
    page.app = WEMEET
    page.start()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args) or QMessageBox.StandardButton.Cancel)
    window.quit_app()
    assert asked and page.state == "recording" and not window.quitting
    page.stop()
    wait_for(lambda: page.state != "finishing")


def test_choosing_sources_is_remembered(window, monkeypatch):
    page = window.meeting
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    page.choose_app(WEMEET)
    assert window.settings.meeting_app == "wemeetapp.exe"
    page.hero.mic_switch.setChecked(False)
    page.choose_app(None)  # The microphone alone: the switch comes back on.
    assert window.settings.meeting_app == "-" and page.hero.mic_switch.isChecked()
    assert window.settings.meeting_microphone


def test_nothing_to_record_keeps_the_button_off(window):
    page = window.meeting
    del page.ready  # The real check.
    window.settings.asr_model = window.settings.asr_model
    page.app = None
    page.hero.mic_switch.setChecked(False)
    page.refresh()
    assert not page.hero.button.isEnabled()


def test_narrow_window_keeps_toolbar_icons_only(window):
    window.show()
    window.resize(860, 700)
    QApplication.instance().processEvents()
    assert window.history_button.text() == "" and window.history_button.toolTip() == "历史"
    window.resize(1100, 760)
    QApplication.instance().processEvents()
    assert window.history_button.text() == "历史"


def test_meetings_are_stored_under_the_data_folder():
    assert meetings_dir().parent.name == "Saywell"


def test_titles_and_durations_read_naturally():
    from datetime import datetime

    assert meeting_title(datetime(2026, 3, 5, 9, 7)) == "3月5日 09:07 的会议"
    assert duration_text(20) == "1 分钟" and duration_text(52 * 60) == "52 分钟"
    assert duration_text(3 * 3600 + 5 * 60) == "3 小时 5 分钟"
