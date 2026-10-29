import json
import threading

import httpx
import pytest

from saywell.cleanup import Cancelled, CleanupError, DeepSeekCleaner
from saywell.core import CleanupPayload, Edit, finish_text, validate_cleanup


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


def capture_transport(payload, seen):
    def handler(request):
        seen.append(json.loads(request.content))
        content = json.dumps(payload, ensure_ascii=False)
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": content}}], "usage": {"total_tokens": 30}},
        )

    return httpx.MockTransport(handler)


def test_spoken_translation_request_types_the_translation_without_chinese_checks():
    seen = []
    payload = {"text": "The plan is not settled yet, don't send it.", "edits": [], "warnings": [], "translated_to": "英语"}
    cleaner = DeepSeekCleaner("test", transport=capture_transport(payload, seen))
    result = cleaner.clean("这个方案还没确定，不要发。翻译成英文。")
    assert result.text == payload["text"] and result.translated_to == "英语"
    assert not result.warnings  # Negation, hedge and length checks compare Chinese wording only.
    assert "唯一的例外是翻译要求" in seen[0]["messages"][0]["content"]


def test_spoken_translation_can_be_turned_off():
    seen = []
    payload = {"text": "这个方案还没确定。", "edits": [], "warnings": [], "translated_to": "英语"}
    cleaner = DeepSeekCleaner("test", transport=capture_transport(payload, seen), voice_translation=False)
    assert cleaner.clean("这个方案还没确定。翻译成英文。").translated_to == ""
    prompt = seen[0]["messages"][0]["content"]
    assert "唯一的例外是翻译要求" not in prompt and "只整理不翻译" in prompt


def test_translate_button_sends_the_text_and_target_language():
    seen = []
    cleaner = DeepSeekCleaner("test", transport=capture_transport({"text": "明日の十時に会議です。"}, seen))
    result = cleaner.translate("明天十点开会。", "日语")
    assert result.text == "明日の十時に会議です。" and result.original == "明天十点开会。"
    assert result.translated_to == "日语" and result.usage["total_tokens"] == 30 and not result.warnings
    system, user = seen[0]["messages"]
    assert "翻译成日语" in system["content"] and json.loads(user["content"]) == {"text": "明天十点开会。"}
    with pytest.raises(CleanupError, match="格式"):
        DeepSeekCleaner("test", transport=capture_transport({"text": ""}, [])).translate("你好", "英语")


def test_unpunctuated_text_asks_the_api_to_drop_modal_particles_too():
    seen = []
    payload = {"text": "好的我们走。", "edits": [], "warnings": []}
    DeepSeekCleaner("test", transport=capture_transport(payload, seen), no_punctuation=True).clean("好的我们走吧。")
    DeepSeekCleaner("test", transport=capture_transport(payload, seen)).clean("好的我们走吧。")
    assert "没有语气词的一整段文字" in seen[0]["messages"][0]["content"]
    assert "没有语气词" not in seen[1]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("text", "spaced", "plain"),
    [
        (
            "我用 Visual Studio Code 写代码，打开 GitHub 看看。",
            "我用Visual Studio Code写代码，打开GitHub看看。",
            "我用Visual Studio Code写代码打开GitHub看看",
        ),
        (
            "下午 3 点开会，用 C++ 写，完成 100% 再说。",
            "下午3点开会，用C++写，完成100%再说。",
            "下午3点开会用C++写完成100%再说",
        ),
        ("（API）和《Python 入门》", "（API）和《Python入门》", "API和Python入门"),
        ("嗯，我觉得可以。好的，呃，额度不够？", "嗯，我觉得可以。好的，呃，额度不够？", "我觉得可以好的额度不够"),
        ("iPhone，iPad 和 Mac。版本 3.5，3～5 天。", "iPhone，iPad和Mac。版本3.5，3～5天。", "iPhone iPad和Mac版本3.5 3～5天"),
        ("口号是 Think different. 大家记住。", "口号是Think different. 大家记住。", "口号是Think different. 大家记住"),
        # Pure English, mostly English with a Chinese name, and Korean keep their spaces.
        ("Hello world, this is fine.", "Hello world, this is fine.", "Hello world, this is fine."),
        ("Please send it to 张三 by Friday.", "Please send it to 张三 by Friday.", "Please send it to 张三 by Friday."),
        ("안녕하세요 API 테스트", "안녕하세요 API 테스트", "안녕하세요 API 테스트"),
    ],
)
def test_results_drop_spaces_around_english_in_chinese_and_can_drop_punctuation(text, spaced, plain):
    assert finish_text(text) == spaced
    assert finish_text(text, no_punctuation=True) == plain
