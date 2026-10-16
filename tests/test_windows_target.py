import ctypes
from ctypes import wintypes

import pytest

from saywell import windows


@pytest.mark.parametrize("gui_available", [False, True])
@pytest.mark.parametrize("class_name", ["ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "Edit"])
def test_console_without_gui_queue_is_targeted_but_gui_fields_stay_strict(monkeypatch, class_name, gui_available):
    def owner(window, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = 200
        return 300

    def get_class(window, buffer, size):
        buffer.value = class_name
        return len(class_name)

    def gui_info(thread, pointer):
        if gui_available:
            info = ctypes.cast(pointer, ctypes.POINTER(windows.GUIThreadInfo)).contents
            info.hwndFocus = 101
        return gui_available

    monkeypatch.setattr(windows.user32, "GetForegroundWindow", lambda: 100)
    monkeypatch.setattr(windows.user32, "GetWindowThreadProcessId", owner)
    monkeypatch.setattr(windows.user32, "GetClassNameW", get_class)
    monkeypatch.setattr(windows.user32, "GetGUIThreadInfo", gui_info)
    target = windows.current_target()
    if class_name == "ConsoleWindowClass":
        assert target == windows.InputTarget(100, 100, (0, 0, 0, 0), 200)
    elif gui_available:
        assert target == windows.InputTarget(100, 101, (0, 0, 0, 0), 200)
    else:
        assert target is None


def test_missing_foreground_process_is_not_a_target(monkeypatch):
    monkeypatch.setattr(windows.user32, "GetForegroundWindow", lambda: 100)
    monkeypatch.setattr(windows.user32, "GetWindowThreadProcessId", lambda *args: 0)
    assert windows.current_target() is None
