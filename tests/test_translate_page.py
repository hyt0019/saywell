import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from saywell import translation
from saywell.achievements import Achievements
from saywell.cleanup import CleanupError
from saywell.settings import Settings
from saywell.stats import count_chars
from saywell.translate_page import TranslateGuide, TranslatePage, pieces
from saywell.translation import Translation
from saywell.ui import STYLE


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    return app


def wait_for(condition, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class Toast:
    def __init__(self):
        self.texts = []

    def show_text(self, text):
        self.texts.append(text)


class Main(QWidget):
    """What the page needs of the main window."""

    def __init__(self, **settings):
        super().__init__()
        self.settings = replace(Settings(), **settings)
        self.usage, self.notices, self.toast = [], [], Toast()
        self.tally = Achievements(Path(tempfile.mkdtemp()) / "achievements.json")

    def record_api_usage(self, usage):
        self.usage.append(usage)

    def achieve(self, record=None):
        if record:
            record(self.tally)

    def set_notice(self, text=""):
        self.notices.append(text)


@pytest.fixture
def page(qt_app, monkeypatch):
    asked, failing = [], set()

    def translate(cleaner, text, language, style="natural", glossary=None, cancel=None, source=""):
        asked.append((text, language, style, source))
        if text in failing:
            failing.discard(text)
            raise CleanupError("请求超时。")
        return Translation(text, f"<{text[:6]}>", language, usage={"total_tokens": 7})

    monkeypatch.setattr(translation, "translate", translate)
    monkeypatch.setattr("saywell.settings.read_key", lambda **_: "key")
    main = Main()
    view = TranslatePage(main)
    view.asked, view.failing = asked, failing
    yield view
    view.shutdown()


def test_long_text_is_cut_between_paragraphs_and_sentences_and_joins_back():
    text = "第一段。\n\n" + "这是一句很长的话，" * 30 + "到这里结束。还有一句。\n第三段 3.5 分。 Next one. Done."
    parts = pieces(text, 120)
    assert "".join(piece + after for piece, after in parts) == text
    assert all(len(piece) <= 120 for piece, _after in parts) and len(parts) > 3
    assert parts[0] == ("第一段。", "\n\n")
    assert not any(piece.endswith("3.") for piece, _after in parts)  # 3.5 is not a sentence end.
    assert pieces("  只有一句  ") == [("只有一句", "")]
    assert pieces("x" * 250, 100) == [("x" * 100, ""), ("x" * 100, ""), ("x" * 50, "")]


def test_a_long_text_is_translated_piece_by_piece_into_the_pair_language(page):
    text = "\n\n".join(f"第{n}段：" + "内容。" * 300 for n in range(1, 4))
    page.original.setPlainText(text)
    page._follow_text()
    assert page.target.currentData() == "英语"  # Chinese goes to the other language of the pair.
    page.translate_button.click()
    assert page.running and page.translate_button.text() == "停止"
    assert wait_for(lambda: not page.running)
    assert len(page.asked) == 3 and all(language == "英语" for _t, language, _s, _src in page.asked)
    assert page.result.toPlainText() == "<第1段：内容>\n\n<第2段：内容>\n\n<第3段：内容>"
    assert len(page.main.usage) == 3 and page.main.toast.texts == ["译完了，共 3 段"]
    assert page.main.tally.counts["page_chars"] == count_chars(text) and "selections" not in page.main.tally.counts
    assert page.translate_button.text() == "翻译" and not page.bar.isVisible()
    page.copy_button.click()
    assert QApplication.clipboard().text() == page.result.toPlainText()


def test_a_failed_piece_is_retried_from_where_it_stopped(page):
    text = "\n\n".join(f"第{n}段：" + "内容。" * 300 for n in range(1, 4))
    page.original.setPlainText(text)
    page.failing.add(pieces(text)[1][0])
    page.translate_button.click()
    assert wait_for(lambda: not page.running)
    assert "第 2 段没有翻译成功：请求超时。" in page.main.notices[-1]
    assert page.translate_button.text() == "继续翻译" and page.progress.text() == "已译 1 / 3 段"
    page.translate_button.click()
    assert wait_for(lambda: not page.running)
    assert [text[:4] for text, *_rest in page.asked] == ["第1段：", "第2段：", "第2段：", "第3段："]
    assert page.result.toPlainText().count("<第") == 3
    page.original.setPlainText(text + "\n\n多一段。")
    assert page.translate_button.text() == "翻译"


def test_the_target_follows_the_text_until_chosen_and_swaps_with_the_source(page):
    page.original.setPlainText("The budget is final.")
    page._follow_text()
    assert page.target.currentData() == "简体中文"
    page.target.setCurrentIndex(page.target.findData("日语"))
    page._pin(0)
    page.original.setPlainText("预算定了。")
    page._follow_text()
    assert page.target.currentData() == "日语"  # Chosen by hand: it stays.
    page.swap()
    assert (page.source.currentData(), page.target.currentData()) == ("日语", "简体中文")
    page.translate_button.click()
    assert wait_for(lambda: not page.running)
    assert page.asked[-1][3] == "日语"  # The source is told to the model.
    page.clear()
    assert not page.pinned and page.result.toPlainText() == ""


def test_the_style_is_shared_with_selection_translation(page):
    page.styles.buttons[2].click()  # 直译
    assert page.main.settings.selection_style == "literal"
    page.original.setPlainText("A short line.")
    page.translate_button.click()
    assert wait_for(lambda: not page.running)
    assert page.asked[-1][2] == "literal"


def test_too_long_a_text_is_refused_with_a_notice(page):
    page.original.setPlainText("字" * 100_001)
    page.translate_button.click()
    assert not page.running and "一次最多翻译 10 万字" in page.main.notices[-1]


def test_the_guide_follows_the_trigger_and_the_shortcut(qt_app):
    guide = TranslateGuide()
    guide.refresh(replace(Settings(), selection_translate=True))
    assert "已开启" in guide.state.text() and guide.on_button.isHidden()
    assert guide.trigger_caption.text() == "点燕子按钮，或轻按右 Alt" and guide.key.text() == "右 Alt"
    guide.refresh(replace(Settings(), selection_trigger="hotkey", selection_hotkey="F9"))
    assert "未开启" in guide.state.text() and not guide.on_button.isHidden()
    assert guide.trigger_caption.text() == "按一下 F9" and guide.mark.isHidden()
    guide.refresh(replace(Settings(), selection_trigger="direct"))
    assert guide.trigger_caption.text() == "选中就翻译，也可以轻按右 Alt"
