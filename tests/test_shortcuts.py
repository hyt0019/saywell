import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog

from saywell.settings import Settings
from saywell.shortcuts import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    SOLO_TAP_MS,
    parse_shortcut,
    shortcut_label,
    shortcut_name,
    shortcut_problem,
    usage_hint,
)
from saywell.windows import OWN_INPUT, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, SoloKeyHook


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_shortcut_names_parse_into_register_hotkey_values():
    assert parse_shortcut("RightCtrl").solo and parse_shortcut("RightCtrl").key == 0xA3
    assert parse_shortcut("CapsLock").key == 0x14 and parse_shortcut("RightAlt").key == 0xA5
    alt_x = parse_shortcut("Alt+X")
    assert (alt_x.modifiers, alt_x.key, alt_x.solo) == (MOD_ALT, ord("X"), False)
    assert parse_shortcut("Ctrl+Shift+Space").modifiers == MOD_CONTROL | MOD_SHIFT
    assert parse_shortcut("Win+Shift+F9").name == "Shift+Win+F9"  # Canonical order.
    assert parse_shortcut("F8").key == 0x77 and parse_shortcut("Win+H").modifiers == MOD_WIN
    assert parse_shortcut("Alt+Alt+X") is None and parse_shortcut("Hyper+Q") is None and parse_shortcut("") is None


def test_names_are_canonical_and_labels_readable():
    assert shortcut_name({"Win", "Alt", "Ctrl"}, "K") == "Ctrl+Alt+Win+K"
    assert shortcut_label("RightCtrl") == "右 Ctrl" and shortcut_label("Alt+X") == "Alt+X"


@pytest.mark.parametrize(
    ("name", "allowed"),
    [
        ("RightCtrl", True),
        ("CapsLock", True),
        ("Ctrl+Alt+K", True),
        ("Win+Shift+H", True),
        ("F8", True),
        ("Shift+F9", True),
        ("Pause", True),
        ("A", False),  # Would stop the letter from typing.
        ("Space", False),
        ("Shift+A", False),  # Capital A.
        ("Left", False),
        ("Ctrl+C", False),  # Copy.
        ("Alt+V", False),  # Saywell's own insert shortcut.
        ("Alt+F4", False),
    ],
)
def test_which_shortcuts_can_be_used(name, allowed):
    assert (shortcut_problem(name) is None) == allowed


def hook(swallow=False):
    events = []
    solo = SoloKeyHook(0xA3, swallow)
    for name in ("pressed", "released", "chorded"):
        getattr(solo.events, name).connect(lambda name=name: events.append(name))
    return solo, events


def test_hook_reports_one_press_per_hold_and_notices_shortcuts(qt_app):
    solo, events = hook()
    assert solo.handle(WM_KEYDOWN, 0xA3, 0) is False  # Right Ctrl passes through to programs.
    solo.handle(WM_KEYDOWN, 0xA3, 0)  # Key repeat.
    assert solo.held
    solo.handle(WM_KEYDOWN, ord("C"), 0)  # Right Ctrl+C: an ordinary shortcut.
    solo.handle(WM_KEYDOWN, ord("V"), 0)
    solo.handle(WM_KEYUP, 0xA3, 0)
    solo.handle(WM_KEYUP, 0xA3, 0)
    assert events == ["pressed", "chorded", "released"] and not solo.held


def test_hook_ignores_the_apps_own_keystrokes_but_not_other_injected_ones(qt_app):
    solo, events = hook()
    solo.handle(WM_KEYDOWN, 0xA3, OWN_INPUT)
    assert events == [] and not solo.held
    solo.handle(WM_SYSKEYDOWN, 0xA3, 0)
    solo.handle(WM_KEYDOWN, 0x56, OWN_INPUT)  # Our own Ctrl+V is not a chord.
    assert events == ["pressed"]


def test_swallowed_key_never_reaches_programs(qt_app):
    solo, _events = hook(swallow=True)
    assert solo.handle(WM_KEYDOWN, 0xA3, 0) is True and solo.handle(WM_KEYUP, 0xA3, 0) is True
    assert solo.handle(WM_KEYDOWN, ord("A"), 0) is False


@pytest.fixture
def window(qt_app, monkeypatch):
    from saywell.app import MainWindow

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    widget = MainWindow(Settings(), native=False)
    calls = []
    monkeypatch.setattr(widget, "on_hotkey", lambda identifier: calls.append(identifier))
    monkeypatch.setattr("saywell.app.tap_key", lambda key: calls.append(("tap", key)))
    monkeypatch.setattr("saywell.app.mask_alt_release", lambda: calls.append("mask"))
    widget.calls = calls
    yield widget
    widget.shutdown()
    widget.close()


def fake_solo(window, name="RightCtrl", swallow=False, key=0xA3):
    window.settings = replace(window.settings, hotkey=name)
    window.solo = SimpleNamespace(held=True, swallow=swallow, key=key, stop=lambda: None)


def test_holding_the_key_starts_recording_after_the_delay(window):
    fake_solo(window)
    window.solo_pressed()
    assert window.solo_timer.isActive() and window.calls == []
    window.solo_long_press()  # The timer ran out with the key still down.
    assert window.calls == [1] and window.solo_state == "active"
    window.solo_released()
    assert window.calls == [1] and window.solo_state == ""


@pytest.mark.parametrize("chord", [False, True])
def test_tap_or_shortcut_does_not_record(window, chord):
    fake_solo(window)
    window.solo_pressed()
    if chord:
        window.solo_chorded()
    window.solo_released()
    window.solo_long_press()  # A late timeout changes nothing.
    assert window.calls == [] and not window.solo_timer.isActive()


def test_capslock_tap_is_sent_on_so_it_still_toggles(window):
    fake_solo(window, "CapsLock", swallow=True, key=0x14)
    window.solo_pressed()
    window.solo_released()
    assert window.calls == [("tap", 0x14)]
    window.calls.clear()
    window.solo_pressed()
    window.solo_long_press()
    window.solo_released()
    assert window.calls == [1]  # Held to talk: CapsLock stays as it was.


def test_right_alt_hold_masks_the_menu_bar(window):
    fake_solo(window, "RightAlt", key=0xA5)
    window.solo_pressed()
    window.solo_long_press()
    assert window.calls == ["mask", 1]


def test_hold_to_talk_stops_when_the_solo_key_is_released(window, monkeypatch):
    fake_solo(window)
    stops = []
    monkeypatch.setattr(window, "stop_recording", lambda: stops.append(True))
    window.state, window.from_hotkey = "recording", True
    window.recorder.started = 0
    window.recorder.limit_reached.clear()
    window.tick()
    assert stops == []
    window.solo.held = False
    window.tick()
    assert stops == [True]
    window.state = "idle"


def key_event(key, vk, modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers, 0, vk, 0)


def test_recorder_accepts_combinations_and_explains_refusals(qt_app):
    from saywell.ui import HotkeyRecorder

    recorder = HotkeyRecorder()
    ctrl_alt = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    recorder.keyPressEvent(key_event(Qt.Key.Key_Control, 0x11, Qt.KeyboardModifier.ControlModifier))
    assert recorder.keys.text() == "Ctrl+…" and not recorder.use.isEnabled()
    recorder.keyPressEvent(key_event(Qt.Key.Key_K, ord("K"), ctrl_alt))
    assert recorder.chosen == "Ctrl+Alt+K" and recorder.use.isEnabled()
    recorder.keyPressEvent(key_event(Qt.Key.Key_A, ord("A")))
    assert recorder.chosen == "" and "影响平时打字" in recorder.problem.text()
    recorder.keyPressEvent(key_event(Qt.Key.Key_C, ord("C"), Qt.KeyboardModifier.ControlModifier))
    assert "常用的编辑或系统快捷键" in recorder.problem.text()
    recorder.keyPressEvent(key_event(Qt.Key.Key_F8, 0x77))
    assert recorder.chosen == "F8"
    recorder.keyPressEvent(key_event(Qt.Key.Key_Escape, 0x1B))
    assert recorder.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize("recorded", ["Ctrl+Alt+K", None])
def test_settings_offer_presets_and_a_recorded_shortcut(qt_app, monkeypatch, recorded):
    from saywell import ui
    from saywell.ui import SettingsDialog

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    monkeypatch.setattr(ui.HotkeyRecorder, "ask", classmethod(lambda cls, parent=None: recorded))
    dialog = SettingsDialog(Settings(hotkey="Alt+X"))
    labels = [dialog.hotkey.itemText(i) for i in range(dialog.hotkey.count())]
    assert labels[:3] == ["右 Ctrl", "右 Alt", "CapsLock"] and labels[-1] == "自定义…"
    assert dialog.hotkey.currentText() == "Alt+X"
    custom = dialog.hotkey.count() - 1
    dialog.hotkey.setCurrentIndex(custom)
    dialog.hotkey.activated.emit(custom)
    assert dialog.hotkey.currentText() == (recorded or "Alt+X")
    dialog.save()
    assert dialog.updated.hotkey == (recorded or "Alt+X")
    dialog.close()


def tap_mode(window, name="RightCtrl", swallow=False, key=0xA3):
    fake_solo(window, name, swallow, key)
    window.settings = replace(window.settings, hold_to_talk=False)


def test_tap_mode_starts_and_stops_with_a_quick_tap(window):
    tap_mode(window)
    window.solo_pressed()
    assert window.solo_timer.isActive() and window.solo_timer.interval() == SOLO_TAP_MS
    window.solo_released()
    window.solo_pressed()
    window.solo_released()
    assert window.calls == [1, 1] and not window.solo_timer.isActive()


@pytest.mark.parametrize("chord", [False, True])
def test_tap_mode_leaves_long_presses_and_shortcuts_alone(window, chord):
    tap_mode(window)
    window.solo_pressed()
    if chord:
        window.solo_chorded()
    else:
        window.solo_long_press()  # Still down after SOLO_TAP_MS: not a tap.
    window.solo_released()
    assert window.calls == [] and window.solo_state == ""


def test_tap_mode_capslock_records_on_a_tap_and_still_toggles_when_held(window):
    tap_mode(window, "CapsLock", swallow=True, key=0x14)
    window.solo_pressed()
    window.solo_released()
    assert window.calls == [1]  # The tap is used to record, so CapsLock stays as it was.
    window.calls.clear()
    window.solo_pressed()
    window.solo_long_press()
    window.solo_released()
    assert window.calls == [("tap", 0x14)]


@pytest.mark.parametrize(("name", "hold", "mask"), [("RightAlt", False, True), ("RightAlt", True, False), ("RightCtrl", False, False)])
def test_only_right_alt_in_tap_mode_masks_every_press(window, monkeypatch, name, hold, mask):
    monkeypatch.setattr(SoloKeyHook, "start", lambda self: True)
    window.settings = replace(window.settings, hold_to_talk=hold)
    assert window.activate_hotkey(name)
    assert window.solo.mask is mask
    window.suspend_hotkey()


def test_masking_hook_hides_only_a_tap_of_right_alt_from_the_menu_bar(qt_app, monkeypatch):
    masks, now = [], [100.0]
    monkeypatch.setattr("saywell.windows.mask_alt_release", lambda: masks.append(now[0]))
    monkeypatch.setattr("saywell.windows.time.monotonic", lambda: now[0])
    solo = SoloKeyHook(0xA5, mask=True)
    assert solo.handle(WM_SYSKEYDOWN, 0xA5, 0) is False  # Right Alt still reaches programs.
    solo.handle(WM_SYSKEYDOWN, 0xA5, 0)  # Key repeat.
    solo.handle(WM_KEYDOWN, 0xE8, OWN_INPUT)  # Our own keys are not a chord.
    assert masks == []
    now[0] += 0.1
    assert solo.handle(WM_KEYUP, 0xA5, 0) is False
    assert masks == [100.1]  # Sent while the release is held in the hook.
    solo.handle(WM_SYSKEYDOWN, 0xA5, 0)
    now[0] += 0.8
    solo.handle(WM_KEYUP, 0xA5, 0)  # Held: the menu bar works as usual.
    solo.handle(WM_SYSKEYDOWN, 0xA5, 0)
    solo.handle(WM_SYSKEYDOWN, 0x09, 0)  # Alt+Tab.
    solo.handle(WM_KEYUP, 0xA5, 0)
    assert masks == [100.1]


def test_usage_hint_describes_each_mode():
    assert usage_hint("RightCtrl", True).startswith("单独按住右 Ctrl 约 0.3 秒开始录音，松开结束")
    assert usage_hint("CapsLock", False).startswith("单独轻按一下 CapsLock 开始录音，再轻按一下结束")
    assert usage_hint("Alt+X", True) == "按住 Alt+X 说话，松开结束。"
    assert usage_hint("F8", False) == "按一下 F8 开始录音，再按一下结束。"


def test_settings_choose_tap_mode_and_unpunctuated_text(qt_app, monkeypatch):
    from saywell.ui import SettingsDialog

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    dialog = SettingsDialog(Settings())
    assert dialog.hold.current() == 0 and dialog.usage_hint.text().startswith("单独按住右 Ctrl")
    dialog.hold.buttons[1].click()
    assert dialog.usage_hint.text().startswith("单独轻按一下右 Ctrl")
    assert not dialog.no_punctuation.isChecked() and not dialog.no_english_punctuation.isChecked()
    dialog.no_punctuation.setChecked(True)
    dialog.save()
    assert dialog.updated.hold_to_talk is False and dialog.updated.no_punctuation is True
    assert dialog.updated.no_english_punctuation is False
    dialog.close()
    reopened = SettingsDialog(dialog.updated)
    assert reopened.hold.current() == 1 and reopened.no_punctuation.isChecked()
    reopened.no_punctuation.setChecked(False)
    reopened.no_english_punctuation.setChecked(True)
    reopened.save()
    assert reopened.updated.no_punctuation is False and reopened.updated.no_english_punctuation is True
    reopened.close()


def test_settings_check_for_updates_and_keep_the_choice(qt_app, monkeypatch):
    import time

    from saywell import __version__
    from saywell.ui import SettingsDialog
    from saywell.updates import Release

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    newer = Release("99.0", "修了一些问题。", "https://example.com", "Saywell-Setup-99.0.exe", 1, "0" * 64, ("https://a",))
    monkeypatch.setattr("saywell.updates.fetch_release", lambda: newer)
    found = []
    dialog = SettingsDialog(Settings())
    dialog.release_found.connect(found.append)
    assert dialog.check_updates.isChecked() and dialog.update_status.text.text() == f"当前版本 {__version__}"
    dialog.check_update_button.click()
    status = dialog.update_status
    deadline = time.monotonic() + 5
    # Also let the result's animations end: Qt crashes later if this unparented dialog is
    # deleted while they run.
    while not found or status.text.graphicsEffect() or status.icon.animation.state() == status.icon.animation.State.Running:
        qt_app.processEvents()
        assert time.monotonic() < deadline
    assert status.text.text().startswith("有新版本 99.0：修了一些问题。")
    assert dialog.check_update_button.isEnabled()
    dialog.check_updates.setChecked(False)
    dialog.save()
    assert dialog.updated.check_updates is False
    dialog.close()
