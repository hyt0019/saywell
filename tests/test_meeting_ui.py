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
    assert view.plain_text().split("\n\n")[1] == "[00:04] 我：好的"


def test_transcript_runs_one_speaker_on_in_a_paragraph(qt_app):
    view = TranscriptView()
    view.reset(True)
    view.add(Line(1, 3, OTHERS, "我觉得这个方案的问题在于，"))
    first = view.document().firstBlock()  # It grows in place: what is there is not rewritten.
    view.add(Line(7, 9, OTHERS, "它没有考虑老用户。"))
    view.add(Line(10, 11, OTHERS, "Beta"))
    view.add(Line(12, 13, OTHERS, "test 先做。"))
    assert view.document().blockCount() == 1 and view.document().firstBlock() == first
    assert first.text() == "其他参会人   00:01 我觉得这个方案的问题在于，它没有考虑老用户。Beta test 先做。"
    view.add(Line(30, 31, OTHERS, "下一件事。"))  # After a long silence: a new paragraph.
    view.add(Line(5, 6, ME, "嗯。"))  # Late, in the middle: the paragraph it lands in splits.
    assert [view.document().findBlockByNumber(index).text().split(" ")[0] for index in range(4)] == [
        "其他参会人", "我", "其他参会人", "其他参会人"
    ]
    assert view.document().findBlockByNumber(2).text().endswith("00:07 它没有考虑老用户。Beta test 先做。")
    again = TranscriptView()
    again.load(True, list(view.lines))
    assert again.toPlainText() == view.toPlainText()


def test_transcript_without_labels_and_with_a_dropped_stretch(qt_app):
    view = TranscriptView()
    view.reset(False)
    view.add(Line(1, 3, ROOM, "开始"))
    view.add(Line(5, 65, SKIPPED, ""))
    view.add(Line(70, 72, ROOM, "继续"))
    text = view.toPlainText()
    assert "其他参会人" not in text and "约 60 秒没来得及识别" in text
    assert view.plain_text() == "[00:01] 开始\n\n[01:10] 继续"
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


class MinutesApi:
    """A chat API answering every request with the same minutes."""

    def __init__(self, status=200):
        self.status = status
        self.requests = []

    def __call__(self, request):
        import json

        import httpx

        self.requests.append(json.loads(request.content))
        notes = {
            "summary": "讨论了排期。", "chapters": [{"time": "00:01", "title": "排期", "points": "按两周排。"}],
            "sections": [{"heading": "决定事项", "items": ["PDF 放到下个版本"]}],
            "todos": [{"task": "发预算明细", "owner": "我", "due": "周二"}], "unclear": [],
        }
        return httpx.Response(self.status, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(notes, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 120, "total_tokens": 420},
        })


def finished_meeting(window, no_devices, monkeypatch, api):
    import httpx

    from saywell.cleanup import ApiCleaner
    from saywell.minutes import MinutesWriter

    page = window.meeting
    page.app = WEMEET
    monkeypatch.setattr(
        page, "_writer",
        lambda cache, lines: MinutesWriter(ApiCleaner("sk-test", transport=httpx.MockTransport(api)), cache),
    )
    page.start()
    page.recorder._publish(Line(1, 2, OTHERS, "那 PDF 先不做了，放到下个版本。"))
    assert wait_for(lambda: page.transcript.lines)
    page.stop()
    assert wait_for(lambda: page.state == "done")
    return page


def test_minutes_are_written_shown_saved_and_counted(window, no_devices, monkeypatch, qt_app, tmp_path):
    from saywell.minutes import minutes_from_record
    from saywell.stats import StatsStore

    window.usage = StatsStore(tmp_path / "stats.json")
    api = MinutesApi()
    page = finished_meeting(window, no_devices, monkeypatch, api)
    assert page.write_button.isEnabled() and not page.copy_minutes_button.isEnabled()
    page.template_box.setCurrentIndex(3)  # 讨论决策, remembered for next time.
    assert window.settings.meeting_template == "decision"
    page.write_minutes()
    assert page.write_button.text() == "生成中…" and not page.template_box.isEnabled()
    assert wait_for(lambda: page.minutes is not None)
    assert "讨论决策" in api.requests[0]["messages"][0]["content"]
    shown = page.minutes_view.toPlainText()
    assert "会议概要" in shown and "00:01  排期" in shown and "发预算明细（负责人：我，截止：周二）" in shown
    assert page.write_button.text() == "重新生成" and page.minutes_status.text() == "整场会议"
    saved = minutes_from_record(page.store.load("minutes"))
    assert saved.template == "decision" and saved.notes.todos[0].task == "发预算明细"
    assert window.usage.total().requests == 1 and window.usage.total().prompt_tokens == 300
    page.copy_minutes()
    assert qt_app.clipboard().text().startswith(page.store.info()["title"])


def test_a_failed_request_keeps_the_minutes_there_were(window, no_devices, monkeypatch):
    api = MinutesApi(status=401)
    page = finished_meeting(window, no_devices, monkeypatch, api)
    page.write_minutes()
    assert wait_for(lambda: page.writing is None)
    assert page.minutes is None and "API 密钥无效" in window.notice.text()
    assert page.write_button.isEnabled() and page.write_button.text() == "生成纪要"
    assert page.store.load("minutes") is None


def test_short_meetings_send_nothing_before_minutes_are_asked_for(window, no_devices, monkeypatch):
    api = MinutesApi()
    page = finished_meeting(window, no_devices, monkeypatch, api)
    page.prepare_minutes()
    assert not page.preparing and api.requests == []


def test_exporting_saves_the_minutes_and_remembers_the_choices(window, no_devices, monkeypatch, tmp_path):
    from pathlib import Path

    from PySide6.QtWidgets import QFileDialog

    from saywell.export import file_name

    page = finished_meeting(window, no_devices, monkeypatch, MinutesApi())
    asked = []

    def choose(parent, caption, suggested, kinds):
        asked.append(Path(suggested))
        return str(tmp_path / "纪要"), kinds

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    page.fill_export_menu()
    assert page.export_menu.actions()[0].text() == "还没有纪要，将只导出会议文字"
    page.export_meeting("txt")
    assert asked[0].name == file_name(page.store.info()["title"]) + ".txt"
    assert "那 PDF 先不做了" in (tmp_path / "纪要.txt").read_text(encoding="utf-8-sig")
    assert window.settings.meeting_export_dir == str(tmp_path) and page.export_button.text() == "已导出"

    page.write_minutes()
    assert wait_for(lambda: page.minutes is not None)
    page.fill_export_menu()
    actions = {action.text(): action for action in page.export_menu.actions()}
    assert actions["只要纪要"].isChecked() and not actions["纪要和会议文字"].isChecked()
    page.export_meeting("md")
    markdown = (tmp_path / "纪要.md").read_text(encoding="utf-8")
    assert "发预算明细" in markdown and "会议文字" not in markdown
    actions["纪要和会议文字"].trigger()
    assert window.settings.meeting_export_transcript
    page.export_meeting("md")
    assert "## 会议文字" in (tmp_path / "纪要.md").read_text(encoding="utf-8")
    assert asked[-1].parent == tmp_path  # The folder saved to last time.

    def fail(path, blocks):
        raise PermissionError("open in Word")

    monkeypatch.setattr("saywell.export.save", fail)
    page.export_meeting("docx")
    assert "没有保存成功" in window.notice.text()


def test_earlier_meetings_open_as_they_were_left_and_can_be_deleted(window, no_devices, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QListWidget

    from saywell.meeting import list_meetings
    from saywell.ui import Button

    page = finished_meeting(window, no_devices, monkeypatch, MinutesApi())
    page.template_box.setCurrentIndex(1)  # 项目例会
    page.write_minutes()
    assert wait_for(lambda: page.minutes is not None)
    first = page.store
    page.template_box.setCurrentIndex(0)
    page.start()
    assert not page.history_button.isEnabled()  # Not while recording.
    page.recorder._publish(Line(3, 4, OTHERS, "第二场会议"))
    assert wait_for(lambda: page.transcript.lines)
    page.stop()
    assert wait_for(lambda: page.state == "done")
    assert [store.folder for store in list_meetings()] == [page.store.folder, first.folder]  # Newest first.

    page.open_meeting(list_meetings()[1])
    assert page.store.folder == first.folder and page.state == "done"
    assert [line.text for line in page.transcript.lines] == ["那 PDF 先不做了，放到下个版本。"]
    assert page.minutes is not None and page.write_button.text() == "重新生成"
    assert page.template_box.currentData() == "project" and window.settings.meeting_template == "general"
    assert page.hero.status.text() == first.info()["title"] and page.history_button.isEnabled()

    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    seen = []

    def use_dialog():
        try:
            dialog = QApplication.activeModalWidget()
            listing = dialog.findChild(QListWidget)
            seen.append((listing.count(), listing.currentRow()))  # The meeting shown is the one chosen.
            next(button for button in dialog.findChildren(Button) if button.text() == "删除").click()
            seen.append(listing.count())
        finally:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QDialog) and widget.isVisible():
                    widget.reject()

    QTimer.singleShot(50, use_dialog)
    page.open_meetings()
    assert seen == [(2, 1), 1]
    assert not first.folder.exists() and page.store is None and page.state == "idle"
    assert page.transcript.lines == [] and page.minutes is None and not page.export_button.isEnabled()
