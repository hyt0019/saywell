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
    parse_shortcut,
    shortcut_label,
    shortcut_name,
    shortcut_problem,
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
