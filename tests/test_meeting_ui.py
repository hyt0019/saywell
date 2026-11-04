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


def type_in(widget, text):
    """Typed with an input method, as Chinese is (QTest only sends ASCII keys)."""
    from PySide6.QtGui import QInputMethodEvent

    event = QInputMethodEvent()
    event.setCommitString(text)
    QApplication.sendEvent(widget, event)


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
    assert blocks == ["大家好", "好的", "我们开始吧", "第二个议题"]  # Who and when are painted, not text.
    assert [(paragraph.who, paragraph.start) for paragraph in view.shown] == [
        (OTHERS, 1), (ME, 4), (OTHERS, 9), (OTHERS, 80)  # A new turn after mine, and a minute on.
    ]
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
    assert first.text() == "我觉得这个方案的问题在于，它没有考虑老用户。Beta test 先做。"
    view.add(Line(30, 31, OTHERS, "下一件事。"))  # After a long silence: a new paragraph.
    view.add(Line(5, 6, ME, "嗯。"))  # Late, in the middle: the paragraph it lands in splits.
    assert [(paragraph.who, paragraph.start) for paragraph in view.shown] == [(OTHERS, 1), (ME, 5), (OTHERS, 7), (OTHERS, 30)]
    assert view.document().findBlockByNumber(2).text() == "它没有考虑老用户。Beta test 先做。"
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


def test_the_transcript_is_edited_a_paragraph_at_a_time(qt_app):
    from PySide6.QtCore import QMimeData, Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest

    view = TranscriptView()
    lines = [Line(1, 3, OTHERS, "大家好，"), Line(4, 6, OTHERS, "开始吧。"), Line(7, 9, ME, "好的。"),
             Line(10, 40, SKIPPED, ""), Line(41, 42, ME, "继续。")]
    view.load(True, lines)
    assert view.isReadOnly()
    type_in(view, "不")  # Read-only until the meeting is over.
    assert view.take_edits() is None
    view.set_editable(True)

    def at(block, offset=0):
        cursor = QTextCursor(view.document().findBlockByNumber(block))
        cursor.movePosition(QTextCursor.MoveOperation.Right, n=offset)
        view.setTextCursor(cursor)

    at(1)
    QTest.keyClick(view, Qt.Key.Key_Backspace)  # Would join two paragraphs.
    QTest.keyClick(view, Qt.Key.Key_Return)  # Would split one.
    at(0, 3)
    QTest.keyClick(view, Qt.Key.Key_Delete)
    type_in(view, "各位")
    at(2, 2)
    type_in(view, "不")  # The note about a stretch that was not recognized stays.
    at(3, 3)
    pasted = QMimeData()
    pasted.setText("还有\n两件事")
    view.insertFromMimeData(pasted)
    blocks = [view.document().findBlockByNumber(index).text() for index in range(view.document().blockCount())]
    assert blocks == ["大家好各位开始吧。", "好的。", "（约 30 秒没来得及识别）", "继续。还有两件事"]
    assert view.characters() == len("大家好各位开始吧。好的。继续。还有两件事")
    edited = view.take_edits()
    assert edited == [Line(1, 6, OTHERS, "大家好各位开始吧。"), Line(7, 9, ME, "好的。"), Line(10, 40, SKIPPED, ""),
                      Line(41, 42, ME, "继续。还有两件事")]
    assert view.take_edits() is None  # Taken.
    view.selectAll()
    QTest.keyClicks(view, "x")  # Across paragraphs: nothing happens.
    assert view.document().blockCount() == 4
    assert view.plain_text().startswith("[00:01] 其他参会人：大家好各位开始吧。\n\n[00:07] 我：好的。")


def test_minutes_are_edited_in_place_and_read_back_as_blocks(qt_app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest

    from saywell.meeting_ui import MinutesView

    view = MinutesView()
    assert view.isReadOnly()
    blocks = [("meta", "2026年11月3日 15:13 · 项目例会"), ("heading", "会议概要"), ("paragraph", "讨论了排期。"),
              ("heading", "章节速览"), ("chapter", ("00:01", "排期", "按两周排。")), ("heading", "决定事项"),
              ("item", "PDF 放到下个版本"), ("heading", "待办"), ("todo", ("发预算明细", "我", "周二"))]
    view.show_blocks(blocks)
    view.set_editable(True)
    document = view.document()

    def end_of(block):
        cursor = QTextCursor(document.findBlockByNumber(block))
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        view.setTextCursor(cursor)

    end_of(0)
    type_in(view, "改")  # The meta line is the program's.
    end_of(7)
    QTest.keyClick(view, Qt.Key.Key_Return)  # A new item in the list.
    type_in(view, "TXT 先做")
    end_of(1)
    QTest.keyClick(view, Qt.Key.Key_Return)  # After a heading: a paragraph.
    type_in(view, "补一句")
    assert view.dirty
    assert view.blocks() == [
        ("heading", "会议概要"), ("paragraph", "补一句"), ("paragraph", "讨论了排期。"), ("heading", "章节速览"),
        ("chapter", ("00:01", "排期", "按两周排。")), ("heading", "决定事项"), ("item", "PDF 放到下个版本"),
        ("item", "TXT 先做"), ("heading", "待办"), ("todo", ("发预算明细（负责人：我，截止：周二）", "", "")),
    ]
    count = document.blockCount()
    view.setTextCursor(QTextCursor(document.findBlockByNumber(1)))
    QTest.keyClick(view, Qt.Key.Key_Backspace)  # Would join the meta line.
    assert document.findBlockByNumber(0).text() == "2026年11月3日 15:13 · 项目例会" and document.blockCount() == count
    view.empty(MinutesView.EMPTY)
    assert view.isReadOnly() and not view.dirty


def test_the_template_picker_explains_and_previews_each_template(window):
    from saywell.meeting_ui import TEMPLATE_SAMPLES, TEMPLATE_USES, template_sample
    from saywell.minutes import TEMPLATES

    assert set(TEMPLATE_SAMPLES) == set(TEMPLATE_USES) == {template.id for template in TEMPLATES}
    for template in TEMPLATES:  # Every section the template writes has an example.
        headings = [value for kind, value in template_sample(template) if kind == "heading"]
        assert headings == [*template.sections, template.todo]
    page = window.meeting
    page.template_box.showPopup()
    picker = QApplication.activePopupWidget()
    assert picker.rows[0].chosen and picker.name.text() == "通用会议"
    picker.rows[4].hovered.emit()
    assert picker.name.text() == "课堂讲座" and "课后任务" in picker.sample.toPlainText()
    assert picker.rows[4].current and not picker.rows[0].current
    assert window.settings.meeting_template == "general"  # Looking changes nothing.
    picker.rows[1].click()
    assert page.template_box.currentData() == "project" and window.settings.meeting_template == "project"
    assert QApplication.activePopupWidget() is None


def test_a_sound_check_listens_to_both_sources_and_says_what_it_heard(window, no_devices, monkeypatch):
    import numpy as np

    from saywell.meeting import SoundCheck

    monkeypatch.setattr(SoundCheck, "add_app", lambda self, pids: self._sink(OTHERS))
    monkeypatch.setattr(SoundCheck, "add_microphone", lambda self, device: self._sink(ME))
    page, hero = window.meeting, window.meeting.hero
    page.app = WEMEET
    hero.mic_chip.setChecked(True)
    window.show()
    page.refresh()
    assert not hero.check_button.isHidden() and hero.check_button.text() == "试音"
    assert hero.app_chip.active and hero.mic_chip.active and hero.app_meter.isHidden()  # Quiet at rest.
    hero.check_button.click()
    check = page.check
    assert check.sources == [OTHERS, ME] and hero.check_button.text() == "停止试音"
    assert not hero.app_meter.isHidden() and not hero.mic_meter.isHidden()
    assert "正在试音" in hero.pill.text() and "腾讯会议里有人说话" in hero.hint.text()
    voice = np.full(1600, 0.05, dtype=np.float32)
    check._sink(OTHERS)(voice)
    page.check_tick()
    assert hero.app_meter.value() == 40 and page.check is check  # Shown a moment longer.
    check.started -= SoundCheck.SECONDS  # Time is up; the microphone heard nothing.
    page.check_tick()
    assert page.check is None and hero.check_button.text() == "试音"
    assert hero.hint.text().startswith("没听到你的声音") and "腾讯会议" not in hero.hint.text()
    assert hero.app_mark.ok and not hero.mic_mark.ok and not hero.mic_mark.isHidden()

    hero.check_button.click()  # Again, and both are heard: done soon after.
    check = page.check
    check._sink(OTHERS)(voice)
    check._sink(ME)(voice)
    page.check_tick()
    check.started -= 2
    page.check_tick()
    assert page.check is None and hero.hint.text() == "试音通过：听到了腾讯会议的声音和你的声音，可以开始记录。"
    hero.mic_chip.setChecked(False)  # Another choice of sources: the result no longer holds.
    assert hero.mic_mark.isHidden() and hero.app_mark.isHidden() and "试音" not in hero.hint.text()
    assert not hero.mic_chip.active and hero.mic_meter.isHidden()
    hero.mic_chip.click()  # The chip itself switches the microphone back on.
    assert hero.mic_chip.isChecked() and window.settings.meeting_microphone

    hero.check_button.click()
    assert page.check is not None
    monkeypatch.setattr(page, "ready", lambda: (True, ""))
    page.start()  # Starting the meeting ends the check first.
    assert page.check is None and page.state == "recording" and hero.check_button.isHidden()
    page.stop()
    assert wait_for(lambda: page.state in ("idle", "done"))


def test_meeting_tab_shows_its_own_card(window):
    assert [button.text() for button in window.page_buttons] == ["概览", "文字", "会议"]
    window.show_page(2)
    assert window.hero_stack.currentWidget() is window.meeting.hero
    window.show_page(0)
    assert window.hero_stack.currentWidget() is window.hero


def test_meeting_records_both_sources_and_keeps_only_text(window, no_devices, monkeypatch):
    page = window.meeting
    page.app = WEMEET
    page.hero.mic_chip.setChecked(True)
    monkeypatch.setattr(window.pipeline, "recognize_clip", lambda settings, clip, vocabulary=None: "好的")
    assert page.hero.wave_column.isHidden()
    page.start()
    assert page.state == "recording" and page.recorder is not None
    assert no_devices == [("app", (4242,), "wemeetapp.exe"), ("mic", ME)]
    assert window.page_buttons[2].badge == RECORD
    assert page.hero.button.text() == "结束记录"
    # The sound as it is heard, colored by who: the others, then the user louder.
    assert not page.hero.wave_column.isHidden() and not page.hero.legend.isHidden()
    page.recorder.level = lambda who: {OTHERS: 0.25, ME: 0.0, ROOM: 0.0}[who]
    page.tick()
    page.recorder.level = lambda who: {OTHERS: 0.1, ME: 0.64, ROOM: 0.0}[who]
    page.tick()
    assert list(page.hero.wave.bars)[-2:] == [(0.5, OTHERS), (0.8, ME)]
    page.hero.wave.grab()  # Paints without trouble.
    page.recorder.recognizing = True  # Words on their way: dots below the transcript.
    page.tick()
    assert page.transcript.pending and page.transcript.pulse.isActive()
    page.recorder.recognizing = False
    page.recorder._publish(Line(1, 2, OTHERS, "大家好"))  # As the recorder's thread would.
    assert wait_for(lambda: page.transcript.lines)
    page.stop()
    assert page.state == "finishing"
    assert wait_for(lambda: page.state == "done")
    assert page.hero.wave_column.isHidden() and not page.transcript.pending
    assert window.page_buttons[2].badge == ""
    info = page.store.info()
    assert info["app"] == "腾讯会议" and info["labels"] and info["ended"] >= info["started"]
    assert [line.text for line in page.store.lines()] == ["大家好"]
    assert "会议记好了" == page.hero.status.text()


def test_a_meeting_where_nothing_was_said_leaves_nothing_behind(window, no_devices):
    page = window.meeting
    page.app = None
    page.hero.mic_chip.setChecked(True)
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
    page.hero.mic_chip.setChecked(False)
    page.choose_app(None)  # The microphone alone: the switch comes back on.
    assert window.settings.meeting_app == "-" and page.hero.mic_chip.isChecked()
    assert window.settings.meeting_microphone


def test_nothing_to_record_keeps_the_button_off(window):
    page = window.meeting
    del page.ready  # The real check.
    window.settings.asr_model = window.settings.asr_model
    page.app = None
    page.hero.mic_chip.setChecked(False)
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

    def panel():
        page.export_button.click()
        return QApplication.activePopupWidget()

    # Before there are minutes: the transcript alone, said so instead of a choice.
    shown = panel()
    assert shown.content.isHidden() and not shown.alone.isHidden() and shown.kind == "docx"
    shown.tiles.buttons()[3].click()  # 纯文本
    shown.button.click()
    assert QApplication.activePopupWidget() is None
    assert asked[0].name == file_name(page.store.info()["title"]) + ".txt"
    assert "那 PDF 先不做了" in (tmp_path / "纪要.txt").read_text(encoding="utf-8-sig")
    assert window.settings.meeting_export_dir == str(tmp_path) and page.export_button.text() == "已导出"
    assert window.settings.meeting_export_format == "txt"

    page.write_minutes()
    assert wait_for(lambda: page.minutes is not None)
    shown = panel()
    assert not shown.content.isHidden() and shown.content.current() == 0
    assert shown.kind == "txt"  # The format used last time.
    shown.tiles.buttons()[2].chosen.emit("md")  # A double click exports right away.
    markdown = (tmp_path / "纪要.md").read_text(encoding="utf-8")
    assert "发预算明细" in markdown and "会议文字" not in markdown
    shown = panel()
    shown.content.buttons[1].click()
    assert window.settings.meeting_export_transcript
    shown.button.click()
    assert "## 会议文字" in (tmp_path / "纪要.md").read_text(encoding="utf-8")
    assert asked[-1].parent == tmp_path  # The folder saved to last time.
    assert panel().content.current() == 1
    QApplication.activePopupWidget().close()

    def fail(path, blocks):
        raise PermissionError("open in Word")

    monkeypatch.setattr("saywell.export.save", fail)
    page.export_meeting("docx")
    assert "没有保存成功" in window.notice.text()


def test_edits_are_saved_used_and_kept_from_being_written_over(window, no_devices, monkeypatch, qt_app):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QTextCursor

    from saywell.minutes import minutes_from_record

    api = MinutesApi()
    page = finished_meeting(window, no_devices, monkeypatch, api)
    assert not page.transcript.isReadOnly()  # The meeting is over.
    page.write_minutes()
    assert wait_for(lambda: page.minutes is not None)
    assert not page.minutes_view.isReadOnly()
    page.transcript.setTextCursor(QTextCursor(page.transcript.document().firstBlock()))
    type_in(page.transcript, "嗯，")
    cursor = QTextCursor(page.minutes_view.document().findBlockByNumber(2))
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    page.minutes_view.setTextCursor(cursor)
    type_in(page.minutes_view, "改过")
    assert page.save_timer.isActive()
    page.copy_all()  # Anything that reads the edits saves them first.
    assert qt_app.clipboard().text() == "[00:01] 其他参会人：嗯，那 PDF 先不做了，放到下个版本。"
    assert [line.text for line in page.store.lines()] == ["嗯，那 PDF 先不做了，放到下个版本。"]
    assert page.progress.text() == "已保存修改" and page.count.text() == f"{len('嗯，那 PDF 先不做了，放到下个版本。')} 字"
    saved = minutes_from_record(page.store.load("minutes"))
    assert ("paragraph", "讨论了排期。改过") in saved.edited and page.minutes_status.text() == "已修改"
    page.copy_minutes()
    assert "讨论了排期。改过" in qt_app.clipboard().text()

    # Writing them again would replace the edits: asked first.
    answers = []

    def answer(button_text):
        box = QApplication.activeModalWidget()
        answers.append(box.text())
        next(button for button in box.buttons() if button.text() == button_text).click()

    QTimer.singleShot(50, lambda: answer("取消"))
    page.write_minutes()
    assert "你修改过" in answers[0] and page.writing is None and len(api.requests) == 1
    QTimer.singleShot(50, lambda: answer("重新生成"))
    page.write_minutes()
    assert wait_for(lambda: page.writing is None) and len(api.requests) == 2
    assert "嗯，那 PDF" in api.requests[1]["messages"][1]["content"]  # The edited transcript.
    assert page.minutes.edited is None and page.minutes_status.text() == "整场会议"
    assert minutes_from_record(page.store.load("minutes")).edited is None


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
