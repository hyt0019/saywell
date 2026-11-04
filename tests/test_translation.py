import json

import httpx
import pytest

from saywell.cleanup import ApiCleaner, CleanupError
from saywell.translation import is_word, messages, script_of, target_language, terms_in, translate


def test_the_direction_follows_the_language_of_the_selection():
    assert target_language("会议改到周三下午", "简体中文", "英语") == "英语"
    assert target_language("The budget is final.", "简体中文", "英语") == "简体中文"
    assert target_language("会議は水曜日に変更されました", "简体中文", "英语") == "简体中文"  # Kana: Japanese.
    assert target_language("회의가 수요일로 바뀌었습니다", "简体中文", "日语") == "简体中文"
    # Mostly Chinese with an English word or two is still Chinese.
    assert target_language("把 API Key 填到设置里", "简体中文", "英语") == "英语"
    # Someone whose own language is English.
    assert target_language("Meeting moved", "英语", "简体中文") == "简体中文"
    assert target_language("开会", "英语", "简体中文") == "英语"
    assert script_of("1234 !?") == ""


def test_single_words_are_looked_up_and_sentences_translated():
    assert is_word("serendipity") and is_word("state-of-the-art") and is_word("take off")
    assert not is_word("The budget is final.") and not is_word("会议") and not is_word("one two three four")


def test_only_the_glossary_terms_in_the_text_go_along():
    assert terms_in("Saywell 用 DeepSeek 整理", ["DeepSeek", "Windows", "saywell", "DeepSeek"]) == ["DeepSeek", "saywell"]


def test_the_prompt_carries_the_language_style_and_terms():
    system, user = messages("Saywell keeps the text local.", "简体中文", "formal", ["Saywell", "Windows"])
    assert "翻译成简体中文" in system["content"] and "书面、正式" in system["content"]
    assert "换行" in system["content"] and "senses" not in system["content"]
    assert json.loads(user["content"]) == {"text": "Saywell keeps the text local.", "glossary": ["Saywell"]}
    word, _user = messages("ubiquitous", "简体中文", "natural", [])
    assert "senses" in word["content"]


def api(answer: dict, seen: list):
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 9},
        })

    return ApiCleaner("key", transport=httpx.MockTransport(handle))


def test_a_translation_keeps_its_lines_and_a_word_gets_its_senses():
    seen = []
    result = translate(api({"text": "第一段。\n\n第二段。"}, seen), "First.\n\nSecond.", "简体中文")
    assert result.text == "第一段。\n\n第二段。" and result.senses == () and result.usage["completion_tokens"] == 9
    assert seen[0]["response_format"] == {"type": "json_object"}
    word = translate(api({"text": "无处不在的", "senses": [{"pos": "adj.", "meaning": "无处不在的"},
                                                       {"pos": "", "meaning": "普遍存在的"}]}, []), "ubiquitous", "简体中文")
    assert word.senses == (("adj.", "无处不在的"), ("", "普遍存在的"))


def test_a_bad_answer_or_no_key_is_reported():
    with pytest.raises(CleanupError, match="格式"):
        translate(api({"words": "x"}, []), "hello there friend", "简体中文")
    with pytest.raises(CleanupError, match="API Key"):
        translate(ApiCleaner(""), "hello", "简体中文")
    with pytest.raises(CleanupError, match="没有选中"):
        translate(api({"text": "x"}, []), "  ", "简体中文")
