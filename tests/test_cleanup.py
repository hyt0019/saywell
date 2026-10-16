import json
import threading

import httpx
import pytest

from saywell.cleanup import Cancelled, CleanupError, DeepSeekCleaner
from saywell.core import CleanupPayload, Edit, validate_cleanup


def client_response(payload=None, status=200, finish="stop"):
    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert "tools" not in body
        return httpx.Response(
            status,
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {
                            "content": json.dumps(
                                payload or {"text": "我想明天去。", "edits": [], "warnings": []}
                            )
                        },
                    }
                ],
                "usage": {"total_tokens": 20},
            },
        )

    return httpx.MockTransport(handler)


def test_clean_preserves_original_and_usage():
    result = DeepSeekCleaner("test", transport=client_response()).clean("我我我想明天去")
    assert result.original == "我我我想明天去"
    assert result.text == "我想明天去。"
    assert result.usage["total_tokens"] == 20
    assert not result.needs_review


@pytest.mark.parametrize("status,expected", [(401, "密钥"), (402, "余额"), (404, "模型"), (403, "HTTP 403")])
def test_errors_are_safe(status, expected):
    with pytest.raises(CleanupError, match=expected) as caught:
        DeepSeekCleaner("secret-test", transport=client_response(status=status)).clean("你好")
    assert "secret-test" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"text": ""},
        {"text": 42},
        {"text": "你好", "unknown": "x"},
        {"text": "你好", "edits": [{"source": "x"}]},
    ],
)
def test_invalid_output_is_not_used(payload):
    with pytest.raises(CleanupError):
        DeepSeekCleaner("test", transport=client_response(payload)).clean("你好")


def test_truncated_result_is_not_used():
    with pytest.raises(CleanupError, match="不完整"):
        DeepSeekCleaner("test", transport=client_response(finish="length")).clean("你好")


def test_cancel_before_request():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        DeepSeekCleaner("test", transport=client_response()).clean("你好", cancel=cancel)


def test_cancel_after_response():
    cancel = threading.Event()

    def handler(request):
        cancel.set()
        return httpx.Response(200, json={})

    with pytest.raises(Cancelled):
        DeepSeekCleaner("test", transport=httpx.MockTransport(handler)).clean("你好", cancel=cancel)


def test_transient_failure_retries_once(monkeypatch):
    monkeypatch.setattr("saywell.cleanup.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(CleanupError):
        DeepSeekCleaner("test", transport=httpx.MockTransport(handler)).clean("你好")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "source,target,fragment",
    [
        ("预算5000元", "预算6000元。", "数字"),
        ("不要删除这个文件", "删除这个文件。", "否定"),
        ("我可能会晚到", "我会晚到。", "可能"),
        ("周三或者周四，还没确定", "周三。", "或者"),
        ("费用不能超过六千元", "费用六千元。", "不能超过"),
        ("暂时就先这样", "就这样。", "暂时"),
    ],
)
def test_meaning_changes_require_review(source, target, fragment):
    assert any(fragment in message for message in validate_cleanup(source, CleanupPayload(text=target)))


@pytest.mark.parametrize(
    "source,target",
    [
        ("我我我想明天去", "我想明天去。"),
        ("预算五千元", "预算5000元。"),
        ("预算两千五百元", "预算2500元。"),
        ("这个非常非常重要", "这个非常非常重要。"),
        ("我不是不想去，是没时间", "我不是不想去，是没时间。"),
        ("他说不对然后重新算了", "他说“不对”，然后重新算了。"),
    ],
)
def test_faithful_changes_pass(source, target):
    assert not validate_cleanup(source, CleanupPayload(text=target))


def test_grounded_correction():
    source = "明天下午三点开会，不对，四点。"
    payload = CleanupPayload(
        text="明天下午四点开会。",
        edits=[Edit(source="下午三点", replacement="下午四点", evidence="三点开会，不对，四点")],
    )
    assert not validate_cleanup(source, payload)


def test_fabricated_evidence_requires_review():
    payload = CleanupPayload(
        text="预算五千元。", edits=[Edit(source="三千", replacement="五千", evidence="不对五千")]
    )
    assert validate_cleanup("预算三千元", payload)


def test_missing_key_and_blank_input_never_call_api():
    with pytest.raises(CleanupError, match="Key"):
        DeepSeekCleaner("").clean("你好")
    with pytest.raises(CleanupError, match="没有"):
        DeepSeekCleaner("test").clean(" ")


def test_unrelated_correction_cannot_hide_lost_negation():
    source = "三点，不对，四点开会。不要删除文件。"
    payload = CleanupPayload(
        text="四点开会。删除文件。",
        edits=[Edit(source="三点", replacement="四点", evidence="三点，不对，四点")],
    )
    assert any("否定" in warning for warning in validate_cleanup(source, payload))


def test_ambiguous_number_negation_requires_review():
    assert any(
        "不是＋数字" in warning
        for warning in validate_cleanup("预算3000元，不是5000元", CleanupPayload(text="预算5000元。"))
    )
