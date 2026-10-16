from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, Signal

from .core import single_line_text
from .shortcuts import HOTKEYS, INSERT_HOTKEY

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def window_class(window: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(window, buffer, 256)
    return buffer.value.lower()


class GUIThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


@dataclass(frozen=True)
class InputTarget:
    window: int
    focus: int
    caret: tuple[int, int, int, int]
    process: int


def same_input_field(left: InputTarget | None, right: InputTarget | None) -> bool:
    # Caret rectangles can change during blinking, scrolling, and UI layout.
    # Only the foreground window, focused control, and process identify the field.
    return bool(
        left
        and right
        and (left.window, left.focus, left.process) == (right.window, right.focus, right.process)
    )


def current_target() -> InputTarget | None:
    window = user32.GetForegroundWindow()
    if not window:
        return None
    pid = wintypes.DWORD()
    thread = user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    if not thread or not pid.value:
        return None
    # Conhost reports its console client's thread, which may have no GUI queue.
    # A classic console has one input stream; its top-level window is the target.
    # Keep normal focused-control checks for GUI terminals with tabs/panes.
    if window_class(window) == "consolewindowclass":
        return InputTarget(int(window), int(window), (0, 0, 0, 0), pid.value)
    info = GUIThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
        return None
    rect = info.rcCaret
    return InputTarget(
        int(window), int(info.hwndFocus or 0), (rect.left, rect.top, rect.right, rect.bottom), pid.value
    )


def is_password(target: InputTarget) -> bool:
    # Native Edit controls expose ES_PASSWORD. Custom web fields may not expose it.
    return window_class(target.focus) == "edit" and bool(user32.GetWindowLongW(target.focus, -16) & 0x20)


def shortcut_held(name: str) -> bool:
    modifiers, key = HOTKEYS[name]
    keys = [key] + [vk for bit, vk in ((1, 0x12), (2, 0x11), (4, 0x10)) if modifiers & bit]
    return all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in keys)


def modifiers_held() -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))


class HotkeyEvents(QObject):
    triggered = Signal(int)


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self):
        super().__init__()
        self.events = HotkeyEvents()
        self.registered: set[int] = set()

    def register(self, identifier: int, modifiers: int, key: int) -> bool:
        self.unregister(identifier)
        success = bool(user32.RegisterHotKey(None, identifier, modifiers | 0x4000, key))
        if success:
            self.registered.add(identifier)
        return success

    def unregister(self, identifier: int):
        if identifier in self.registered:
            user32.UnregisterHotKey(None, identifier)
            self.registered.discard(identifier)

    def close(self):
        for identifier in tuple(self.registered):
            self.unregister(identifier)

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0312 and int(msg.wParam) in self.registered:
            self.events.triggered.emit(int(msg.wParam))
            return True, 0
        return False, 0


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


class PasteError(RuntimeError):
    pass


def paste_text(text: str, target: InputTarget, clipboard, restore_callback=None) -> None:
    """Paste once, with a focus check immediately before the Win32 input call."""
    from PySide6.QtCore import QMimeData

    # Apply at the final boundary too: edits and older history can contain newlines.
    text = single_line_text(text)
    if not text:
        raise PasteError("没有可输入的文字。")
    if not user32.IsWindow(target.window) or not same_input_field(current_target(), target):
        raise PasteError(f"输入位置已经变化，结果已保留。请回到目标输入框后按 {INSERT_HOTKEY}。")
    if is_password(target):
        raise PasteError("密码输入框不支持自动输入，请切换到普通文本框。")
    terminal = window_class(target.window) in (
        "consolewindowclass", "cascadia_hosting_window_class", "mintty"
    )
    if modifiers_held():
        raise PasteError("请松开快捷键后重试输入。")
    original = QMimeData()
    mime = clipboard.mimeData()
    if mime:
        for format_name in mime.formats():
            original.setData(format_name, mime.data(format_name))
    clipboard.setText(text)
    sequence = user32.GetClipboardSequenceNumber()

    def restore():
        if user32.GetClipboardSequenceNumber() == sequence:
            clipboard.setMimeData(original)
        if restore_callback:
            restore_callback()

    if not same_input_field(current_target(), target):
        restore()
        raise PasteError("输入位置已经变化，已取消自动输入。")
    events = (INPUT * 4)()
    # Shift+Insert also works in mintty, where Ctrl+V is not normally paste.
    # Never send Enter; terminal users decide when to execute/submit the text.
    modifier, paste_key = (0x10, 0x2D) if terminal else (0x11, 0x56)
    keys = ((modifier, 0), (paste_key, 0), (paste_key, 2), (modifier, 2))
    for event, (key, flags) in zip(events, keys, strict=True):
        event.type = 1
        scan = user32.MapVirtualKeyW(key, 4)  # MAPVK_VK_TO_VSC_EX preserves the E0 prefix.
        # Some layouts map VK_INSERT to keypad scan 0x52 without the E0 prefix.
        # Always request the dedicated Insert key so Num Lock cannot change the chord.
        if key == 0x2D or scan & 0xFF00 == 0xE000:
            flags |= 0x0001  # KEYEVENTF_EXTENDEDKEY, required for the dedicated Insert key.
        event.ki = KEYBDINPUT(key, scan & 0xFF, flags, 0, 0)
    sent = user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    QTimer.singleShot(1000, restore)
    if sent != 4:
        # Always release the simulated keys if Windows accepted only part of the input.
        releases = (INPUT * 2)(events[2], events[3])
        user32.SendInput(2, ctypes.byref(releases), ctypes.sizeof(INPUT))
        raise PasteError("Windows 未完成输入，可能是目标程序权限较高。请复制结果后手动粘贴。")
