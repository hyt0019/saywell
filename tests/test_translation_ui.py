import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from saywell import translation
from saywell.selection import Selection
from saywell.settings import Settings
from saywell.translation import Translation
from saywell.translation_ui import SelectionTranslate, near
from saywell.ui import STYLE


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    return app


def wait_for(condition, seconds=3.0):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


class Window(QWidget):
    """What the controller needs of the main window."""

    def __init__(self, **settings):
        super().__init__()
        self.settings = replace(Settings(), selection_translate=True, **settings)
        self.hotkeys = None
        self.usage = []

    def record_api_usage(self, usage):
        self.usage.append(usage)


@pytest.fixture
def run(qt_app, monkeypatch):
    asked = []

    def translate(cleaner, text, language, style="natural", glossary=None, cancel=None):
        asked.append((text, language, style))
        return Translation(text, f"[{language}] {text}", language, usage={"total_tokens": 42})

    monkeypatch.setattr(translation, "translate", translate)
    monkeypatch.setattr("saywell.settings.read_key", lambda **_: "key")
    monkeypatch.setattr("saywell.windows.current_target", lambda: "the field")

    def make(**settings):
        window = Window(**settings)
        controller = SelectionTranslate(window)
        controller.configure = lambda: ""  # No hooks or hotkeys in tests.
        controller.asked = asked
        return window, controller

    yield make
    for widget in QApplication.topLevelWidgets():
        widget.hide()


PAGE = Selection("The budget is final.", "chrome.exe", 10, 20, rect=(100, 100, 260, 118), area=(100, 100, 260, 118))


def test_the_mouse_only_counts_near_the_selection():
    assert near((150, 110), (100, 100, 260, 118)) and near((300, 130), (100, 100, 260, 118))
    assert not near((900, 700), (100, 100, 260, 118)) and near((900, 700), None)


def test_a_mouse_selection_shows_the_button_and_a_click_translates(run):
    window, controller = run()
    controller._read(("mouse", (260, 118)), PAGE)
    assert controller.icon.isVisible() and not controller.popup.isVisible()
    controller._icon_clicked()
    assert controller.popup.isVisible() and controller.popup.route.text() == "译成简体中文"
    assert wait_for(lambda: controller.translation is not None)
    assert controller.asked == [("The budget is final.", "简体中文", "natural")]
    assert controller.popup.result.text() == "[简体中文] The budget is final."
    assert window.usage == [{"total_tokens": 42}]
    controller.copy()
    assert QApplication.clipboard().text() == "[简体中文] The budget is final."
    controller._mouse_pressed(5, 5)  # A click somewhere else closes it.
    assert wait_for(lambda: not controller.popup.isVisible())


def test_selections_that_must_not_be_read_or_are_far_from_the_mouse_show_nothing(run):
    window, controller = run(selection_excluded=["chrome.exe"])
    controller._read(("mouse", (260, 118)), PAGE)
    assert not controller.icon.isVisible()
    window.settings = replace(window.settings, selection_excluded=[])
    controller._read(("mouse", (900, 700)), PAGE)  # A window dragged elsewhere, old selection.
    controller._read(("mouse", (260, 118)), replace(PAGE, text="", blocked=True))
    assert not controller.icon.isVisible() and not controller.popup.isVisible()


def test_direct_mode_translates_at_once_and_another_language_can_be_chosen(run):
    window, controller = run(selection_trigger="direct")
    controller._read(("mouse", (260, 118)), replace(PAGE, text="会议改到周三"))
    assert controller.popup.isVisible() and not controller.icon.isVisible()
    assert wait_for(lambda: controller.translation is not None)
    assert controller.asked[-1][1] == "英语"
    controller.popup.language_buttons["日语"].click()
    assert wait_for(lambda: controller.translation is not None and controller.translation.language == "日语")
    assert controller.popup.route.text() == "译成日语"


def test_the_shortcut_explains_what_it_cannot_read(run, monkeypatch):
    window, controller = run(selection_trigger="hotkey")
    controller._read(("hotkey", (50, 50)), replace(PAGE, text="", blocked=True))
    assert controller.popup.isVisible() and "密码" in controller.popup.problem_text.text()
    monkeypatch.setattr("saywell.selection.window_class", lambda window: "cascadia_hosting_window_class")
    controller._read(("hotkey", (50, 50)), replace(PAGE, text="", exe="windowsterminal.exe"))
    assert "终端" in controller.popup.problem_text.text() and controller.asked == []


def test_replacing_types_the_translation_over_the_selection_with_its_lines(run, monkeypatch):
    pasted = []
    monkeypatch.setattr("saywell.windows.paste_text", lambda text, target, clipboard, **options: pasted.append((text, target, options)))
    window, controller = run(selection_trigger="direct")
    controller._read(("mouse", (260, 118)), replace(PAGE, text="第一行\n第二行", editable=True))
    assert wait_for(lambda: controller.translation is not None)
    assert not controller.popup.replace_button.isHidden()
    controller.replace()
    assert pasted == [("[英语] 第一行\n第二行", "the field", {"keep_result": False, "single_line": False})]
    assert wait_for(lambda: not controller.popup.isVisible())


def test_read_only_text_offers_no_replace(run):
    window, controller = run(selection_trigger="direct")
    controller._read(("mouse", (260, 118)), replace(PAGE, editable=False))
    assert controller.popup.replace_button.isHidden()


def test_turning_it_off_hides_everything(qt_app):
    window = Window()
    controller = SelectionTranslate(window)
    controller._watch = lambda on: None
    controller.icon.show()
    window.settings = replace(window.settings, selection_translate=False)
    assert controller.configure() == ""
    assert not controller.icon.isVisible() and not controller.enabled
    asked = []
    controller.reader.ask = asked.append
    controller.hotkey()  # Ignored while off.
    assert asked == [] and not controller.popup.isVisible()


class Hotkeys:
    def __init__(self):
        self.registered = set()

    def register(self, identifier, modifiers, key):
        self.registered.add(identifier)
        return True

    def unregister(self, identifier):
        self.registered.discard(identifier)


def test_esc_is_only_taken_while_the_popup_is_open(run):
    window, controller = run(selection_trigger="direct")
    window.hotkeys = Hotkeys()
    del controller.configure  # The real one, with these hotkeys.
    controller._watch = lambda on: None
    assert controller.configure() == "" and window.hotkeys.registered == {controller.HOTKEY}
    controller._read(("mouse", (260, 118)), PAGE)
    assert controller.ESCAPE in window.hotkeys.registered
    controller.escape()
    assert window.hotkeys.registered == {controller.HOTKEY}
    controller._read(("mouse", (260, 118)), replace(PAGE, text="另一段"))
    window.settings = replace(window.settings, selection_translate=False)
    controller.configure()  # Turned off with the popup open.
    assert window.hotkeys.registered == set() and not controller.popup.isVisible()
