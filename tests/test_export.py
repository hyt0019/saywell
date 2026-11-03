import os
import zipfile
from xml.dom import minidom

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from saywell.export import FORMATS, document_blocks, file_name, save
from saywell.meeting import ME, OTHERS, SKIPPED, Line
from saywell.minutes import Minutes, Notes

NOTES = Notes.model_validate({
    "summary": "讨论了排期。",
    "chapters": [{"time": "00:01", "title": "排期", "points": "按两周排。"}],
    "sections": [{"heading": "决定事项", "items": ["PDF 放到下个版本"]}],
    "todos": [{"task": "发预算明细", "owner": "我", "due": "周二"}],
    "unclear": ["预算上限没说定"],
})
INFO = {"title": "11月3日 15:13 的会议", "started": 1_793_690_000, "duration": 1830, "app": "腾讯会议"}
LINES = [
    Line(1, 4, OTHERS, "那 PDF 先不做了。"),
    Line(5, 30, SKIPPED, ""),
    Line(31, 33, ME, "好，<我>按两周排 & 发明细。"),
]


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def blocks(transcript=True, minutes=True):
    return document_blocks(INFO, Minutes(NOTES, "general", 33, 3) if minutes else None, LINES, True, transcript)


def test_every_format_is_written(tmp_path, qt_app):
    for kind in FORMATS:
        path = tmp_path / f"会议.{kind}"
        save(path, blocks())
        assert path.stat().st_size > 0
        assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / "会议.pdf").read_bytes().startswith(b"%PDF")
    with zipfile.ZipFile(tmp_path / "会议.docx") as archive:
        assert archive.testzip() is None
        parts = {name: minidom.parseString(archive.read(name)) for name in archive.namelist()}
    text = "".join(node.data for node in _texts(parts["word/document.xml"]))
    assert "11月3日 15:13 的会议" in text and "发预算明细（负责人：我，截止：周二）" in text
    assert "好，<我>按两周排 & 发明细。" in text and "约 25 秒没来得及识别" in text


def _texts(node):
    for child in node.childNodes:
        if child.nodeType == child.TEXT_NODE:
            yield child
        else:
            yield from _texts(child)


def test_text_and_markdown_read_cleanly(tmp_path):
    save(tmp_path / "a.txt", blocks())
    raw = (tmp_path / "a.txt").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw  # Notepad and Word read it right.
    text = raw.decode("utf-8-sig")
    assert "【待办】" in text and "[00:31] 我：好，<我>按两周排 & 发明细。" in text
    save(tmp_path / "a.md", blocks())
    markdown = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert markdown.startswith("# 11月3日 15:13 的会议\n\n")
    assert "\n\n\n" not in markdown
    assert "- [ ] 发预算明细（负责人：我，截止：周二）" in markdown
    assert "## 会议文字\n\n**00:01 其他参会人**　那 PDF 先不做了。\n\n**00:05**　（约 25 秒没来得及识别）" in markdown


def test_the_transcript_goes_along_only_when_asked_or_when_there_are_no_minutes():
    kinds = [kind for kind, _ in blocks(transcript=False)]
    assert "line" not in kinds and "会议文字" not in [value for _, value in blocks(transcript=False)]
    alone = blocks(transcript=False, minutes=False)
    assert alone[0] == ("title", "11月3日 15:13 的会议") and alone[2] == ("heading", "会议文字")
    assert alone[1][1].endswith(" · 30 分钟 · 腾讯会议") and "通用会议" not in alone[1][1]  # No template: no minutes.
    assert [kind for kind, _ in alone].count("line") == 3


def test_file_names_keep_to_what_windows_allows():
    assert file_name("11月3日 15:13 的会议") == "11月3日 15：13 的会议"
    assert file_name('a/b\\c*?"<>|') == "abc"
    assert file_name(" .. ") == "会议"
