import json
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from saywell import vocabulary
from saywell.core import CleanupResult
from saywell.settings import Settings
from saywell.vocabulary import (
    LexiconError,
    TermIndex,
    Vocabulary,
    delete_lexicons,
    import_lexicon,
    lexicon_index,
    lexicon_path,
    read_lexicon,
)

# A Qwen3-ASR transcript of a synthesized clip: four names misheard as common homophones.
MISHEARD = "下午三点和龚玉晨、欧阳一恒开会，会后把剂量发到灵犀项目群，再请钟立帆核对一下埃克替尼的剂量。"
NAMES = ["龚彧宸", "欧阳翊珩", "灵析", "钟栎帆"]


def write(tmp_path, name, content, encoding="utf-8"):
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding=encoding)
    return path


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("# 注释\n龚彧宸\n\n欧阳翊珩\n", ["龚彧宸", "欧阳翊珩"]),
        ("龚彧宸 5\nDeepSeek 4\nVisual Studio Code 3\n", ["龚彧宸", "DeepSeek", "Visual Studio Code"]),
        ("Windows 11\nDeepSeek\n", ["Windows 11", "DeepSeek"]),  # Not a weighted list.
        ("龚彧宸|4\n钟栎帆|2\n", ["龚彧宸", "钟栎帆"]),
        ("阿替利珠单抗\t1203\n埃克替尼\t88\n", ["阿替利珠单抗", "埃克替尼"]),  # THUOCL
        ("阿替利珠单抗 1203 nz\n埃克替尼 88 n\n", ["阿替利珠单抗", "埃克替尼"]),  # jieba
        ("'a'ti'li'zhu'dan'kang 阿替利珠单抗\na'ke'ti'ni 埃克替尼 1\n", ["阿替利珠单抗", "埃克替尼"]),  # Sogou / QQ
        ("阿替利珠单抗(a|ti|li|zhu|dan|kang) 1\n", ["阿替利珠单抗"]),  # Baidu
    ],
)
def test_text_lists_keep_the_word_column(tmp_path, content, expected):
    assert read_lexicon(write(tmp_path, "words.txt", content)).terms == expected


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_text_lists_in_common_encodings(tmp_path, encoding):
    assert read_lexicon(write(tmp_path, "words.txt", "龚彧宸\n钟栎帆\n", encoding)).terms == ["龚彧宸", "钟栎帆"]


def test_csv_terms_and_wispr_style_corrections(tmp_path):
    parsed = read_lexicon(write(tmp_path, "flow.csv", "word,replacement\n龚彧宸,\n灵犀项目,灵析项目\n埃克替尼,3\n"))
    assert parsed.terms == ["龚彧宸", "埃克替尼"]
    assert parsed.corrections == [("灵犀项目", "灵析项目")]
    assert parsed.count == 3


@pytest.mark.parametrize(
    "data",
    [
        ["龚彧宸", "钟栎帆"],
        [{"text": "龚彧宸", "weight": 4, "lang": "zh"}, {"text": "钟栎帆", "weight": 4}],  # Alibaba Cloud
        {"vocabulary": ["龚彧宸", "钟栎帆"]},
    ],
)
def test_json_hotword_lists(tmp_path, data):
    path = write(tmp_path, "hotwords.json", json.dumps(data, ensure_ascii=False))
    assert read_lexicon(path).terms == ["龚彧宸", "钟栎帆"]


def test_rime_dictionary_reads_the_text_column(tmp_path):
    content = (
        "# Rime dictionary\n---\nname: medical\nversion: \"1\"\nsort: by_weight\ncolumns:\n"
        "  - code\n  - text\n...\n\nati lizhu\t阿替利珠单抗\nake tini\t埃克替尼\n# comment\n"
    )
    parsed = read_lexicon(write(tmp_path, "medical.dict.yaml", content))
    assert parsed.name == "medical"
    assert parsed.terms == ["阿替利珠单抗", "埃克替尼"]


def scel_bytes(name: str, groups: list[tuple[list[str], list[str]]], deleted: list[str] = ()) -> bytes:
    """A Sogou .scel file laid out as the ciku / imewlconverter projects document it."""
    data = bytearray(0x1544)
    data[0] = ord("@")
    title = name.encode("utf-16-le")
    data[0x130 : 0x130 + len(title)] = title
    syllables = sorted({s for pinyin, _ in groups for s in pinyin})
    data[0x1540:0x1542] = len(syllables).to_bytes(2, "little")
    for i, syllable in enumerate(syllables):
        encoded = syllable.encode("utf-16-le")
        data += i.to_bytes(2, "little") + len(encoded).to_bytes(2, "little") + encoded
    for pinyin, words in groups:
        code = b"".join(syllables.index(s).to_bytes(2, "little") for s in pinyin)
        data += len(words).to_bytes(2, "little") + len(code).to_bytes(2, "little") + code
        for word in words:
            encoded = word.encode("utf-16-le")
            data += len(encoded).to_bytes(2, "little") + encoded + (10).to_bytes(2, "little") + bytes(10)
    if deleted:
        data += b"DELTBL" + len(deleted).to_bytes(2, "little")
        for word in deleted:
            data += len(word).to_bytes(2, "little") + word.encode("utf-16-le")
    return bytes(data)


def test_sogou_cell_dictionary(tmp_path):
    data = scel_bytes(
        "医学词汇", [(["a", "ke", "ti", "ni"], ["埃克替尼"]), (["ling", "xi"], ["灵析", "灵犀"])], deleted=["废词"]
    )
    parsed = read_lexicon(write(tmp_path, "download.scel", data))
    assert parsed.name == "医学词汇"
    assert parsed.terms == ["埃克替尼", "灵析", "灵犀"]


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("empty.txt", "\n# nothing\n", "没有从文件中读到词语"),
        ("bad.json", "{oops", "JSON 格式有误"),
        ("fake.scel", b"not a sogou file", "搜狗细胞词库"),
    ],
)
def test_unreadable_files_explain_why(tmp_path, name, content, message):
    with pytest.raises(LexiconError, match=message):
        read_lexicon(write(tmp_path, name, content))


def test_large_files_are_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(vocabulary, "LEXICON_LIMIT", 2)
    parsed = read_lexicon(write(tmp_path, "big.txt", "甲乙丙\n丁戊己\n庚辛壬\n"))
    assert parsed.truncated and parsed.terms == ["甲乙丙", "丁戊己"]


def test_misheard_names_are_found_by_sound_including_polyphonic_characters():
    index = TermIndex.build(NAMES + ["阿替利珠单抗", "埃克替尼"])
    # 欧阳翊珩 needs the "heng" reading of 珩; 钟栎帆 needs "li" for 栎. Terms already written
    # correctly (埃克替尼) are not suggested. 灵析 has two characters and is ignored here.
    assert index.lookup(MISHEARD) == ["龚彧宸", "欧阳翊珩", "钟栎帆"]


def test_two_character_imported_words_do_not_match_everyday_speech():
    index = TermIndex.build(["中药", "肺痈", "补虚", "灵析"])
    assert index.lookup("这个功能非常重要，但是费用不能超过六千元") == []


def test_english_terms_match_other_spellings_and_corrections_match_exactly():
    index = TermIndex.build(["DeepSeek", "PyTorch", "API"], [("灵犀项目", "灵析项目")])
    assert index.lookup("记得测试 deep seek 的 api 和 pytorch，发到灵犀项目群") == ["DeepSeek", "PyTorch", "灵析项目"]
    assert index.lookup("记得测试 DeepSeek") == []  # Already written the preferred way.


def test_vocabulary_sends_personal_words_plus_matches_and_picks_terms_to_hear_again():
    words = Vocabulary(["DeepSeek", "灵析"], TermIndex.build(NAMES))
    assert words.lookup(MISHEARD) == ["龚彧宸", "欧阳翊珩", "钟栎帆"]
    assert words.glossary_for(MISHEARD) == ["DeepSeek", "灵析", "龚彧宸", "欧阳翊珩", "钟栎帆"]
    # Personal words take part from two characters (灵犀 → 灵析), imported ones from three.
    assert words.matches(MISHEARD) == ["灵析", "龚彧宸", "欧阳翊珩", "钟栎帆"]
    assert Vocabulary.context(["龚彧宸", "DeepSeek"]) == "龚彧宸 DeepSeek"
    assert Vocabulary().glossary_for(MISHEARD) == [] and Vocabulary().matches(MISHEARD) == []


def test_ordinary_speech_gets_no_asr_context():
    # Any context makes Qwen3-ASR space out English words, so it is only given on a match.
    words = Vocabulary(["DeepSeek", "Windows", "Ctrl", "Shift"], TermIndex.build(NAMES))
    assert words.matches("最后记得测试DeepSeek的API，快捷键改成Ctrl加Shift加空格") == []


def test_matches_are_capped(monkeypatch):
    monkeypatch.setattr(vocabulary, "RETRIEVED_LIMIT", 2)
    assert len(Vocabulary([], TermIndex.build(NAMES)).lookup(MISHEARD)) == 2


def test_context_stays_short(monkeypatch):
    monkeypatch.setattr(vocabulary, "CONTEXT_CHARS", 10)
    assert Vocabulary.context(["一二三四", "五六七八", "九十百千"]) == "一二三四 五六七八"


def test_imported_lexicons_are_indexed_once_and_follow_switches(tmp_path):
    entry, _ = import_lexicon(write(tmp_path, "names.txt", "\n".join(NAMES)))
    assert lexicon_path(entry["id"]).is_file() and entry["count"] == 4 and entry["enabled"]
    first = lexicon_index([entry])
    assert first is lexicon_index([dict(entry)])  # Reused while the files are unchanged.
    assert Vocabulary.for_settings(Settings(glossary=[], lexicons=[entry])).lookup(MISHEARD)
    assert lexicon_index([{**entry, "enabled": False}]) is None
    delete_lexicons([entry["id"]])
    assert not lexicon_path(entry["id"]).exists()
    assert Vocabulary.for_settings(Settings(lexicons=[entry])).lookup(MISHEARD) == []  # Missing file skipped.


def test_delete_lexicons_only_touches_named_files(tmp_path):
    keep, _ = import_lexicon(write(tmp_path, "keep.txt", "龚彧宸\n"))
    drop, _ = import_lexicon(write(tmp_path, "drop.txt", "钟栎帆\n"))
    delete_lexicons([drop["id"]])
    assert lexicon_path(keep["id"]).is_file() and not lexicon_path(drop["id"]).exists()


def test_settings_keep_only_valid_lexicon_entries(tmp_path):
    good = {"id": "a" * 32, "name": "医学", "enabled": True, "count": 3}
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"lexicons": [good, {"id": "../x", "name": "坏", "enabled": True, "count": 1}, "oops"]}),
        encoding="utf-8",
    )
    assert Settings.load(path).lexicons == [good]


class FakeVad:
    """One speech segment covering the whole clip."""

    def __init__(self, config, buffer_size_in_seconds):
        self.items, self.total = [], 0

    def accept_waveform(self, window):
        self.total += len(window)

    def flush(self):
        self.items.append(SimpleNamespace(start=0, samples=np.zeros(self.total)))

    def empty(self):
        return not self.items

    @property
    def front(self):
        return self.items[0]

    def pop(self):
        self.items.pop(0)


@pytest.mark.parametrize("uses_context", [True, False])
def test_qwen3_hears_a_segment_again_with_matched_terms(tmp_path, monkeypatch, uses_context):
    from saywell import asr

    contexts = []

    class Recognizer:
        def create_stream(self):
            return SimpleNamespace(accept_waveform=lambda *_: None, result=SimpleNamespace(text=""))

        def decode_stream(self, stream):
            context = getattr(stream, "context", "")
            contexts.append(context)
            stream.result.text = "下午和龚彧宸开会" if "龚彧宸" in context else "下午和龚玉晨开会"

    monkeypatch.setattr(asr.sherpa_onnx, "VoiceActivityDetector", FakeVad)
    recognizer = asr.LocalRecognizer.__new__(asr.LocalRecognizer)
    recognizer.directory, recognizer.recognizer, recognizer.uses_context = tmp_path, Recognizer(), uses_context
    words = Vocabulary(["DeepSeek"], TermIndex.build(["龚彧宸"]))
    text = recognizer.transcribe(np.full(16000, 0.1, dtype=np.float32), vocabulary=words)
    if uses_context:
        assert contexts == ["", "龚彧宸"] and text == "下午和龚彧宸开会"
    else:  # sherpa-onnx models take no context; the cleanup step gets the matches instead.
        assert contexts == [""] and text == "下午和龚玉晨开会"


def test_llama_request_carries_the_context_as_system_message():
    from saywell.llama import LlamaServerRecognizer

    bodies = []
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": "language Chinese<asr_text>龚彧宸"}}]},
    )
    server = LlamaServerRecognizer.__new__(LlamaServerRecognizer)
    server.client = SimpleNamespace(post=lambda _url, json: bodies.append(json) or response, close=lambda: None)
    for context in ("", "龚彧宸 DeepSeek"):
        stream = server.create_stream()
        stream.accept_waveform(16000, np.zeros(1600, dtype=np.float32))
        stream.context = context
        server.decode_stream(stream)
    assert [message["role"] for message in bodies[0]["messages"]] == ["user"]
    assert bodies[1]["messages"][0] == {"role": "system", "content": "龚彧宸 DeepSeek"}
    assert bodies[1]["messages"][1]["role"] == "user"


def test_pipeline_sends_personal_words_and_matched_terms_to_cleanup(tmp_path, monkeypatch):
    from saywell.pipeline import Pipeline

    entry, _ = import_lexicon(write(tmp_path, "names.txt", "\n".join(NAMES)))
    settings = Settings(glossary=["DeepSeek"], lexicons=[entry])
    seen = {}

    def transcribe(samples, cancel, partial, words):
        seen["matches"] = words.matches(MISHEARD)
        return MISHEARD

    def clean(text, glossary, cancel):
        seen["glossary"] = glossary
        return CleanupResult(text, text)

    emitted = []
    events = SimpleNamespace(**{
        name: SimpleNamespace(emit=lambda *args, name=name: emitted.append((name, args)))
        for name in ("stage", "transcript", "completed", "failed")
    })
    pipeline = Pipeline(events)
    pipeline.recognizer = SimpleNamespace(
        directory=settings.models, model=settings.asr_model, gpu=False, transcribe=transcribe
    )
    monkeypatch.setattr("saywell.pipeline.read_key", lambda **kwargs: "key")
    monkeypatch.setattr("saywell.pipeline.ApiCleaner", lambda *args, **kwargs: SimpleNamespace(clean=clean))
    pipeline._run(1, threading.Event(), settings, SimpleNamespace(size=16000), "audio")
    assert seen == {
        "matches": ["龚彧宸", "欧阳翊珩", "钟栎帆"],
        "glossary": ["DeepSeek", "龚彧宸", "欧阳翊珩", "钟栎帆"],
    }
    assert emitted[-1][0] == "completed"


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def test_settings_dialog_imports_switches_and_removes_lexicons(qt_app, tmp_path, monkeypatch):
    from saywell import ui
    from saywell.ui import SettingsDialog

    old = {"id": "b" * 32, "name": "旧词库", "enabled": True, "count": 5}
    source = write(tmp_path, "医学.txt", "阿替利珠单抗\n埃克替尼\n")
    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    monkeypatch.setattr(ui, "run_task", lambda owner, work, done: done(work(lambda _text: None)))
    dialog = SettingsDialog(Settings(lexicons=[old]))
    dialog.import_button.click()
    added = dialog.lexicons[-1]
    assert added["name"] == "医学" and added["count"] == 2 and dialog.imported_ids == {added["id"]}
    assert "已导入“医学”，共 2 个词" in dialog.lexicon_status.text()
    dialog.remove_lexicon(old)
    dialog.lexicons[0]["enabled"] = False
    dialog.save()
    assert dialog.updated.lexicons == [{**added, "enabled": False}]
    dialog.close()


def test_settings_dialog_reports_unsupported_files(qt_app, tmp_path, monkeypatch):
    from saywell import ui
    from saywell.ui import SettingsDialog

    warnings = []
    source = write(tmp_path, "empty.txt", "\n")
    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    monkeypatch.setattr(ui, "run_task", lambda owner, work, done: done(work(lambda _text: None)))
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog = SettingsDialog(Settings())
    dialog.import_button.click()
    assert dialog.lexicons == [] and "没有从文件中读到词语" in warnings[0]
    dialog.close()


@pytest.mark.parametrize("accepted", [True, False])
def test_unsaved_imports_and_removed_lexicons_leave_no_files(qt_app, tmp_path, monkeypatch, accepted):
    from saywell.app import MainWindow

    kept, _ = import_lexicon(write(tmp_path, "kept.txt", "龚彧宸\n"))
    removed, _ = import_lexicon(write(tmp_path, "removed.txt", "钟栎帆\n"))
    new, _ = import_lexicon(write(tmp_path, "new.txt", "欧阳翊珩\n"))

    class Dialog:
        release_found = SimpleNamespace(connect=lambda slot: None)

        def __init__(self, settings, parent):
            self.imported_ids = {new["id"]}
            self.updated = replace(settings, lexicons=[kept, new])

        def exec(self):
            return 1 if accepted else 0

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    monkeypatch.setattr("saywell.app.SettingsDialog", Dialog)
    monkeypatch.setattr("saywell.app.startup_enabled", lambda: False)
    monkeypatch.setattr("saywell.app.save_settings_with_startup", lambda settings: None)
    window = MainWindow(Settings(lexicons=[kept, removed]), native=False)
    try:
        window.open_settings()
        assert lexicon_path(kept["id"]).is_file()
        assert lexicon_path(new["id"]).is_file() == accepted
        assert lexicon_path(removed["id"]).is_file() == (not accepted)
    finally:
        window.shutdown()
        window.close()
