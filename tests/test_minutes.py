import json
import threading

import httpx
import pytest

from saywell.cleanup import ApiCleaner
from saywell.meeting import ME, OTHERS, SKIPPED, Line
from saywell.minutes import (
    PART_SECONDS,
    TEMPLATES,
    MinutesError,
    MinutesWriter,
    Notes,
    outline,
    split_parts,
    system_prompt,
    to_html,
    to_markdown,
    to_text,
    transcript_text,
)

NOTES = {
    "summary": "讨论了下个版本的排期和预算。",
    "chapters": [{"time": "00:03", "title": "排期", "points": "先做 TXT 和 Word。"}],
    "sections": [
        {"heading": "决定事项", "items": ["PDF 放到下个版本"]},
        {"heading": "要点", "items": ["设计稿已出", " "]},
        {"heading": "多余的小节", "items": ["不该出现"]},
    ],
    "todos": [{"task": "发预算明细", "owner": "我", "due": "周二下班前"}, {"task": " ", "owner": "", "due": ""}],
    "unclear": ["市场部能批的金额是两万还是二十万"],
}


class FakeApi:
    def __init__(self, notes=NOTES):
        self.requests = []
        self.notes = notes
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        with self.lock:
            self.requests.append(body)
        content = json.dumps(self.notes, ensure_ascii=False)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })


def writer(api, key="sk-test", cache=None):
    client = ApiCleaner(key, transport=httpx.MockTransport(api), timeout=5)
    return MinutesWriter(client, cache)


def meeting(minutes: int, every: float = 20.0, size: int = 30):
    return [
        Line(t, t + 5, OTHERS if index % 3 else ME, f"第{index}句" + "内容" * size)
        for index, t in enumerate(x * every for x in range(int(minutes * 60 / every)))
    ]


def test_a_short_meeting_is_written_in_one_request():
    api = FakeApi()
    result = writer(api).write(meeting(20), "general", labels=True)
    assert len(api.requests) == 1
    system, user = api.requests[0]["messages"]
    assert "通用会议" in system["content"] and "“要点”、“决定事项”" in system["content"]
    assert user["content"].startswith("[00:00] 我：第0句") and "\n[00:20] 其他参会人：第1句" in user["content"]
    # The template's sections in its order, empty items and unknown sections dropped.
    assert [(section.heading, section.items) for section in result.notes.sections] == [
        ("要点", ["设计稿已出"]), ("决定事项", ["PDF 放到下个版本"])
    ]
    assert [todo.task for todo in result.notes.todos] == ["发预算明细"]
    assert result.usage["total_tokens"] == 150 and result.lines == 60 and result.through == 1185


def test_a_long_meeting_goes_in_parts_then_merges_and_parts_are_reused():
    api = FakeApi()
    lines = meeting(150, size=60)  # Two and a half hours of steady talk.
    cache = {}
    first = writer(api, cache=cache).write(lines, "project", labels=True)
    parts = split_parts(lines)
    assert len(parts) >= 10 and all(part[-1].start - part[0].start < PART_SECONDS for part in parts)
    assert len(api.requests) == len(parts) + 1 and len(cache) == len(parts)
    # Parts are noted the same way for every template; the merge applies the template.
    assert all("详细笔记" in request["messages"][0]["content"] for request in api.requests[:-1])
    merge = api.requests[-1]["messages"]
    assert "合并成整场会议的纪要" in merge[0]["content"] and "项目例会" in merge[0]["content"]
    assert json.loads(merge[1]["content"])[0]["from"] == "00:00"
    assert first.notes.sections[0].heading == "各项进展"
    # Ten more minutes and another template: only the part still growing and the merge are asked.
    api.requests.clear()
    more = lines + [Line(lines[-1].end + 10 * k, lines[-1].end + 10 * k + 5, ME, "又一句" * 20) for k in range(1, 30)]
    writer(api, cache=cache).write(more, "lecture", labels=True)
    assert 2 <= len(api.requests) <= 3 and "课堂讲座" in api.requests[-1]["messages"][0]["content"]


def test_finished_parts_of_a_long_meeting_are_noted_while_it_goes_on():
    api = FakeApi()
    cache = {}
    # Forty minutes is short enough for one request at the end: nothing is sent early.
    writer(api, cache=cache).prepare(meeting(40), labels=True)
    assert api.requests == []
    lines = meeting(100, size=60)
    usage = writer(api, cache=cache).prepare(lines, labels=True)
    parts = split_parts(lines)
    assert len(api.requests) == len(parts) - 1 == len(cache) and usage["total_tokens"] == 150 * len(cache)
    # Asked again: nothing new until another part is finished; the minutes need only two requests.
    writer(api, cache=cache).prepare(lines, labels=True)
    assert len(api.requests) == len(parts) - 1
    api.requests.clear()
    writer(api, cache=cache).write(lines, "general", labels=True)
    assert len(api.requests) == 2


def test_nothing_to_write_and_no_key_are_explained():
    with pytest.raises(MinutesError, match="还没有会议文字"):
        writer(FakeApi()).write([Line(1, 60, SKIPPED, "")], "general", labels=False)
    with pytest.raises(MinutesError, match="API Key"):
        writer(FakeApi(), key="").write(meeting(1), "general", labels=False)


def test_a_bad_reply_is_an_error_not_empty_minutes():
    api = FakeApi(notes=None)
    api.notes = "不是 JSON"
    with pytest.raises(MinutesError, match="格式不对"):
        writer(api).write(meeting(1), "general", labels=False)


def test_every_template_names_its_sections_and_forbids_inventing():
    for template in TEMPLATES:
        prompt = system_prompt(template)
        assert all(f"“{heading}”" in prompt for heading in template.sections)
        assert "不推测" in prompt and "unclear" in prompt and template.todo in prompt


def test_transcript_marks_what_was_not_recognized():
    text = transcript_text([Line(1, 2, ME, "好"), Line(65, 125, SKIPPED, "")], labels=False)
    assert text == "[00:01] 好\n[01:05] （约 60 秒没有识别出来）"


def test_minutes_read_the_same_in_every_format():
    from saywell.minutes import Minutes, tidy, TEMPLATE_IDS

    notes = tidy(Notes.model_validate(NOTES), TEMPLATE_IDS["general"])
    blocks = outline(Minutes(notes, "general", 3000, 60), {
        "title": "11月3日 15:30 的会议", "started": 1_793_690_000, "duration": 52 * 60, "app": "腾讯会议",
    })
    markdown = to_markdown(blocks)
    assert markdown.startswith("# 11月3日 15:30 的会议\n")
    assert "· 52 分钟 · 腾讯会议" in markdown
    assert "## 会议概要" in markdown and "- **00:03 排期**：先做 TXT 和 Word。" in markdown
    assert "- [ ] 发预算明细（负责人：我，截止：周二下班前）" in markdown
    assert "## 待确认" in markdown
    text = to_text(blocks)
    assert "【待办】" in text and "□ 发预算明细（负责人：我，截止：周二下班前）" in text
    page = to_html(blocks)
    assert "&lt;" not in page and "待确认" in page
