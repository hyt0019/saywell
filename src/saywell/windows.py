from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, Signal

from .core import single_line_text
from .shortcuts import INSERT_HOTKEY, MODIFIERS, SOLO_TAP_MS, parse_shortcut

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
    shortcut = parse_shortcut(name)
    if shortcut is None:
        return False
    groups = [(shortcut.key,)] + [vks for _name, flag, vks in MODIFIERS if shortcut.modifiers & flag]
    return all(any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in group) for group in groups)


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


# Marks keystrokes the app sends itself (paste, the Alt mask key, a re-sent CapsLock tap) so the
# solo-key hook skips them while still seeing keys injected by remote desktops and on-screen keyboards.
OWN_INPUT = 0x53415957


def send_keys(*strokes: tuple[int, int]) -> bool:
    """Send (virtual key, flags) keystrokes marked as the app's own."""
    events = (INPUT * len(strokes))()
    for event, (key, flags) in zip(events, strokes, strict=True):
        event.type = 1
        event.ki = KEYBDINPUT(key, user32.MapVirtualKeyW(key, 0) & 0xFF, flags, 0, OWN_INPUT)
    return user32.SendInput(len(strokes), ctypes.byref(events), ctypes.sizeof(INPUT)) == len(strokes)


def tap_key(key: int) -> bool:
    return send_keys((key, 0), (key, 2))


def mask_alt_release() -> bool:
    """Releasing Alt after holding it alone opens the active window's menu bar and takes the
    focus away from the text field. Any other key pressed while Alt is down prevents that;
    0xE8 is unassigned, so it does nothing else (AutoHotkey uses the same trick)."""
    return tap_key(0xE8)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105


class SoloKeyEvents(QObject):
    pressed = Signal()  # The key went down (key repeat is ignored).
    released = Signal()
    chorded = Signal()  # Another key went down while it was held: an ordinary shortcut.


class SoloKeyHook:
    """Watches one key held on its own (right Ctrl, right Alt, CapsLock) with a low-level hook.

    The hook runs on its own thread and message loop: Windows waits for every low-level hook
    before delivering each keystroke, so a busy GUI thread would make typing stall everywhere.
    Windows also drops a hook that is slow to answer without telling anyone, so it is put back
    every 30 seconds. With swallow, the key never reaches other programs (CapsLock would flip).
    With mask, a tap of right Alt (released within SOLO_TAP_MS, nothing else pressed) does not open
    the menu bar: an unassigned key is sent from the hook while it holds the release. Keys sent from
    inside the hook reach programs ahead of the key being handled, so programs see Alt, the mask
    key, then the release; the GUI thread would only hear of the tap after the release went out."""

    def __init__(self, key: int, swallow: bool = False, mask: bool = False):
        self.key = key
        self.swallow = swallow
        self.mask = mask
        self.events = SoloKeyEvents()
        self.held = False
        self.chorded = False
        self.pressed_at = 0.0
        self.thread: threading.Thread | None = None
        self.thread_id = 0

    def handle(self, message: int, key: int, extra: int) -> bool:
        """Process one keystroke; True when it should be kept from other programs."""
        if extra == OWN_INPUT:
            return False
        down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if key == self.key:
            if down and not self.held:
                self.held, self.chorded, self.pressed_at = True, False, time.monotonic()
                self.events.pressed.emit()
            elif not down and self.held:
                self.held = False
                if self.mask and not self.chorded and time.monotonic() - self.pressed_at < SOLO_TAP_MS / 1000:
                    mask_alt_release()
                self.events.released.emit()
            return self.swallow
        if down and self.held and not self.chorded:
            self.chorded = True
            self.events.chorded.emit()
        return False

    def start(self) -> bool:
        ready = threading.Event()
        result = []

        def callback(code, message, data):
            if code == 0:
                info = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                try:
                    if self.handle(int(message), info.vkCode, info.dwExtraInfo):
                        return 1
                except Exception:  # noqa: BLE001 - never let a hook error block the keyboard
                    pass
            return user32.CallNextHookEx(None, code, message, data)

        def run():
            self.thread_id = kernel32.GetCurrentThreadId()
            procedure = HOOKPROC(callback)  # Kept referenced for as long as the hook lives.
            module = kernel32.GetModuleHandleW(None)
            hook = user32.SetWindowsHookExW(13, procedure, module, 0)  # WH_KEYBOARD_LL
            result.append(bool(hook))
            ready.set()
            if not hook:
                return
            user32.SetTimer(None, 0, 30_000, None)
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0113:  # WM_TIMER: reinstall in case Windows dropped the hook.
                    user32.UnhookWindowsHookEx(hook)
                    hook = user32.SetWindowsHookExW(13, procedure, module, 0)
            if hook:
                user32.UnhookWindowsHookEx(hook)

        self.thread = threading.Thread(target=run, name="solo-key-hook", daemon=True)
        self.thread.start()
        ready.wait(5)
        return bool(result and result[0])

    def stop(self) -> None:
        if self.thread and self.thread.is_alive():
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
            self.thread.join(2)
        self.thread = None
        self.held = False


class PasteError(RuntimeError):
    pass


def paste_text(text: str, target: InputTarget, clipboard, restore_callback=None, keep_result: bool = False) -> None:
    """Paste once, with a focus check immediately before the Win32 input call.

    keep_result leaves the pasted text on the clipboard instead of restoring the previous content."""
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
        if not keep_result and user32.GetClipboardSequenceNumber() == sequence:
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
        event.ki = KEYBDINPUT(key, scan & 0xFF, flags, 0, OWN_INPUT)
    sent = user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    QTimer.singleShot(1000, restore)
    if sent != 4:
        # Always release the simulated keys if Windows accepted only part of the input.
        releases = (INPUT * 2)(events[2], events[3])
        user32.SendInput(2, ctypes.byref(releases), ctypes.sizeof(INPUT))
        raise PasteError("Windows 未完成输入，可能是目标程序权限较高。请复制结果后手动粘贴。")
