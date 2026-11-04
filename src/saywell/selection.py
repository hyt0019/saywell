"""Reading the text the user selected in another program, for selection translation.

UI Automation comes first: it asks the focused control for its selection, without touching the
clipboard or sending keys, and says whether the control is a password field. Programs that do
not offer their text that way are read with Ctrl+C, and only when the user pressed the
translation shortcut: never for a mouse selection alone, never in a terminal (Ctrl+C would stop
what runs there) and never in a password field. Reading happens on its own thread, because a
program that hangs makes UI Automation wait.

The slots of the COM interfaces below were read from UIAutomationCore.dll's type library.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
from ctypes import POINTER, byref, c_double, c_int, c_long, c_void_p, wintypes
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Callable

from PySide6.QtCore import QObject, Signal

from .capture import CLSCTX_ALL, GUID, com_thread, guid, method, ole32, process_details, release
from .windows import HOOKPROC, user32
from .windows import kernel32 as k32

CLSID_CUIAutomation8 = guid("E22AD333-B25F-460C-83D0-0581107395C9")
IID_IUIAutomation2 = guid("34723AFF-0C9D-49D0-9896-7AB52DF8CD8A")
IID_IUIAutomationTextPattern = guid("32EBA289-3583-42C9-9C59-3B6D9A1E9B6A")
IID_IUIAutomationValuePattern = guid("A94CD8B1-0844-4CD6-9D2D-640537AB39E9")
TEXT_PATTERN = 10014
VALUE_PATTERN = 10002
EDIT_CONTROL = 50004

# IUIAutomation(2)
GET_FOCUSED_ELEMENT, CONTROL_VIEW_WALKER, PUT_CONNECTION_TIMEOUT, PUT_TRANSACTION_TIMEOUT = 8, 14, 61, 63
# IUIAutomationElement
GET_PATTERN_AS, PROCESS_ID, CONTROL_TYPE, IS_PASSWORD = 14, 20, 21, 35
# IUIAutomationTreeWalker, IUIAutomationTextPattern, IUIAutomationTextRangeArray, IUIAutomationTextRange
PARENT_ELEMENT = 3
GET_SELECTION = 5
ARRAY_LENGTH, ARRAY_ELEMENT = 3, 4
BOUNDING_RECTANGLES, GET_TEXT = 10, 12
# IUIAutomationValuePattern
VALUE_IS_READ_ONLY = 5

# More than a popup should hold; longer selections are cut here and the popup says so.
MAX_CHARS = 4000
NEWLINE = chr(10)
# How far up from the focused control to look for the text that holds the selection (a link
# inside a page, a cell inside a document).
ANCESTORS = 3

# Where Ctrl+C means "stop", by window class and by executable.
TERMINAL_CLASSES = {"consolewindowclass", "cascadia_hosting_window_class", "mintty", "putty", "virtualconsoleclass"}
TERMINALS = {
    "windowsterminal.exe", "wt.exe", "openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "mintty.exe", "putty.exe", "kitty.exe", "alacritty.exe", "wezterm-gui.exe", "tabby.exe", "hyper.exe",
    "conemu64.exe", "conemu.exe", "cmder.exe", "mobaxterm.exe", "xshell.exe", "securecrt.exe", "termius.exe",
}
# Password managers keep secrets in ordinary-looking fields; they are never read.
NEVER = {"keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "enpass.exe", "dashlane.exe",
         "lastpass.exe", "roboform.exe", "nordpass.exe", "proton pass.exe"}

user32.GetCursorPos.argtypes = [POINTER(wintypes.POINT)]
user32.GetDoubleClickTime.restype = wintypes.UINT
oleaut32 = ctypes.WinDLL("oleaut32")
oleaut32.SysStringLen.argtypes = [c_void_p]
oleaut32.SysStringLen.restype = wintypes.UINT
oleaut32.SysFreeString.argtypes = [c_void_p]
oleaut32.SafeArrayGetUBound.argtypes = [c_void_p, wintypes.UINT, POINTER(c_long)]
oleaut32.SafeArrayAccessData.argtypes = [c_void_p, POINTER(c_void_p)]
oleaut32.SafeArrayUnaccessData.argtypes = [c_void_p]
oleaut32.SafeArrayDestroy.argtypes = [c_void_p]


@dataclass(frozen=True)
class Selection:
    text: str
    exe: str  # The program's executable, lower case ("chrome.exe").
    pid: int
    window: int  # The foreground window when it was read.
    # The selection's last line on screen (left, top, right, bottom), when the program says.
    rect: tuple[int, int, int, int] | None = None
    # Whether the selected text can be typed over: True, False, or None when the program does
    # not say.
    editable: bool | None = None
    cut: bool = False  # Longer than MAX_CHARS; only the start is kept.
    via: str = "uia"  # Or "clipboard".
    # A password field or a password manager: not read, and not to be copied either.
    blocked: bool = False
    # All of the selection on screen, when the program says.
    area: tuple[int, int, int, int] | None = None


def window_class(window: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(window, buffer, 256)
    return buffer.value.lower()


def executable(pid: int) -> str:
    path = process_details(pid)[1]
    return PureWindowsPath(path).name.lower() if path else ""


def foreground() -> tuple[int, int, str]:
    """The foreground window, its process and that process's executable."""
    window = user32.GetForegroundWindow()
    if not window:
        return 0, 0, ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, byref(pid))
    return int(window), pid.value, executable(pid.value)


def is_terminal(window: int, exe: str) -> bool:
    return exe in TERMINALS or window_class(window) in TERMINAL_CLASSES


def cursor() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    return point.x, point.y


def clip(text: str) -> tuple[str, bool]:
    """Selections end up with stray spaces and CRLF; long ones are cut at MAX_CHARS."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS], True
    return text, False


# --- UI Automation ------------------------------------------------------------------------------


def _bstr(pointer: c_void_p) -> str:
    if not pointer:
        return ""
    try:
        return ctypes.wstring_at(pointer, oleaut32.SysStringLen(pointer))
    finally:
        oleaut32.SysFreeString(pointer)


def _int(element: c_void_p, slot: int) -> int:
    value = c_int()
    method(element, slot, POINTER(c_int))(byref(value))
    return value.value


def _pattern(element: c_void_p, pattern: int, iid: GUID) -> c_void_p:
    found = c_void_p()
    try:
        method(element, GET_PATTERN_AS, c_int, POINTER(GUID), POINTER(c_void_p))(pattern, byref(iid), byref(found))
    except OSError:
        return c_void_p()
    return found


Rect = tuple[int, int, int, int]


def _rects(text_range: c_void_p) -> list[Rect]:
    """Where the range's lines are on screen, as (left, top, right, bottom)."""
    array = c_void_p()
    try:
        method(text_range, BOUNDING_RECTANGLES, POINTER(c_void_p))(byref(array))
    except OSError:
        return []
    if not array:
        return []
    try:
        upper = c_long()
        if oleaut32.SafeArrayGetUBound(array, 1, byref(upper)) != 0 or upper.value < 3:
            return []
        data = c_void_p()
        if oleaut32.SafeArrayAccessData(array, byref(data)) != 0:
            return []
        try:
            values = ctypes.cast(data, POINTER(c_double))
            count = (upper.value + 1) // 4 * 4
            lines = [tuple(values[start + index] for index in range(4)) for start in range(0, count, 4)]
        finally:
            oleaut32.SafeArrayUnaccessData(array)
        # (left, top, width, height) per line.
        return [(round(x), round(y), round(x + w), round(y + h)) for x, y, w, h in lines if w > 0 and h > 0]
    finally:
        oleaut32.SafeArrayDestroy(array)


def union(rects: list[Rect]) -> Rect | None:
    if not rects:
        return None
    return min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)


def _selected_text(pattern: c_void_p) -> tuple[str, list[Rect]]:
    ranges = c_void_p()
    method(pattern, GET_SELECTION, POINTER(c_void_p))(byref(ranges))
    if not ranges:
        return "", []
    parts, rects = [], []
    try:
        count = _int(ranges, ARRAY_LENGTH)
        for index in range(min(count, 50)):
            text_range = c_void_p()
            method(ranges, ARRAY_ELEMENT, c_int, POINTER(c_void_p))(index, byref(text_range))
            if not text_range:
                continue
            try:
                text = c_void_p()
                method(text_range, GET_TEXT, c_int, POINTER(c_void_p))(MAX_CHARS + 1, byref(text))
                value = _bstr(text)
                if value.strip():
                    parts.append(value)
                    rects += _rects(text_range)
            finally:
                release(text_range)
    finally:
        release(ranges)
    return NEWLINE.join(parts), rects


def _editable(element: c_void_p) -> bool | None:
    pattern = _pattern(element, VALUE_PATTERN, IID_IUIAutomationValuePattern)
    if pattern:
        try:
            return not _int(pattern, VALUE_IS_READ_ONLY)
        except OSError:
            return None
        finally:
            release(pattern)
    try:
        return True if _int(element, CONTROL_TYPE) == EDIT_CONTROL else None
    except OSError:
        return None


class Automation:
    """One UI Automation client, for the thread that made it (COM must be running there)."""

    def __init__(self):
        self.client = c_void_p()
        ole32.CoCreateInstance(byref(CLSID_CUIAutomation8), None, CLSCTX_ALL, byref(IID_IUIAutomation2),
                               byref(self.client))
        # A program that hangs answers nothing; give up on it quickly instead of after 20 s.
        method(self.client, PUT_CONNECTION_TIMEOUT, wintypes.DWORD)(1000)
        method(self.client, PUT_TRANSACTION_TIMEOUT, wintypes.DWORD)(1500)
        self.walker = c_void_p()
        method(self.client, CONTROL_VIEW_WALKER, POINTER(c_void_p))(byref(self.walker))

    def close(self):
        release(self.walker)
        release(self.client)
        self.walker = self.client = c_void_p()

    def read(self) -> Selection | None:
        """The selection in the focused control. Its text is empty when nothing is selected or
        the control does not offer its text (then Ctrl+C may), and it is `blocked` in a password
        field. None when another program is not in front."""
        window, owner, exe = foreground()
        if not window or owner == os.getpid():
            return None
        nothing = Selection("", exe, owner, window, blocked=exe in NEVER)
        element = c_void_p()
        try:
            method(self.client, GET_FOCUSED_ELEMENT, POINTER(c_void_p))(byref(element))
        except OSError:
            return nothing
        if not element:
            return nothing
        try:
            pid = _int(element, PROCESS_ID)
            if pid == os.getpid():
                return None
            exe = executable(pid) or exe
            if _int(element, IS_PASSWORD) or exe in NEVER:
                return Selection("", exe, pid, window, blocked=True)
            nothing = Selection("", exe, pid, window)
            current, steps = element, 0
            while current and steps <= ANCESTORS:
                pattern = _pattern(current, TEXT_PATTERN, IID_IUIAutomationTextPattern)
                if pattern:
                    try:
                        text, rects = _selected_text(pattern)
                    finally:
                        release(pattern)
                    text, cut = clip(text)
                    if not text:
                        return nothing
                    last = rects[-1] if rects else None
                    return Selection(text, exe, pid, window, last, _editable(current), cut, area=union(rects))
                parent = c_void_p()
                try:
                    method(self.walker, PARENT_ELEMENT, c_void_p, POINTER(c_void_p))(current, byref(parent))
                except OSError:
                    parent = c_void_p()
                if current is not element:
                    release(current)
                current, steps = parent, steps + 1
            if current and current is not element:
                release(current)
            return nothing
        except OSError:
            return nothing
        finally:
            release(element)


class SelectionReader(QObject):
    """Reads selections on a thread of its own; each answer comes back as `read` on the GUI
    thread, with the request it answers."""

    read = Signal(object, object)  # (request, Selection | None)

    def __init__(self):
        super().__init__()
        self.requests: queue.Queue = queue.Queue()
        self.busy = False
        self.thread: threading.Thread | None = None

    def ask(self, request: object) -> bool:
        """Read the selection for `request`; False (nothing asked) while a read is running."""
        if self.busy:
            return False
        self.busy = True
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, name="selection-reader", daemon=True)
            self.thread.start()
        self.requests.put(request)
        return True

    def _run(self):
        with com_thread():
            try:
                automation = Automation()
            except OSError:
                automation = None
            try:
                while True:
                    request = self.requests.get()
                    if request is None:
                        return
                    try:
                        found = automation.read() if automation else None
                    except Exception:  # noqa: BLE001 - a broken provider in another program
                        found = None
                    self.busy = False
                    self.read.emit(request, found)
            finally:
                if automation:
                    automation.close()

    def stop(self):
        if self.thread is not None and self.thread.is_alive():
            self.requests.put(None)
            self.thread.join(2)
        self.thread = None
        self.busy = False


# --- Ctrl+C, for programs UI Automation cannot read -------------------------------------------


def may_copy(window: int, exe: str) -> str:
    """Why the selection may not be read with Ctrl+C here, or "" when it may."""
    if is_terminal(window, exe):
        return "终端里按 Ctrl+C 会中断正在运行的命令，所以不在终端里复制文字。"
    if exe in NEVER:
        return "不读取密码管理器里的文字。"
    return ""


def copy_selection(clipboard, done: Callable[[str], None], wait_keys: Callable[[], bool], send_copy: Callable[[], bool],
                   sequence: Callable[[], int], timer) -> None:
    """Copy the selection with Ctrl+C and hand its text to `done` ("" if nothing came), then put
    the clipboard back. Runs on the GUI thread in small steps: first the shortcut's keys must be
    up (Alt held with Ctrl+C would be another shortcut), then the copy has a moment to land."""
    from PySide6.QtCore import QMimeData

    saved = QMimeData()
    mime = clipboard.mimeData()
    if mime:
        for name in mime.formats():
            saved.setData(name, mime.data(name))
    started = time.monotonic()
    state = {"before": 0, "sent": 0.0}

    def step():
        now = time.monotonic()
        if not state["sent"]:
            if wait_keys() and now - started < 1.5:
                timer(30, step)
                return
            state["before"] = sequence()
            if not send_copy():
                done("")
                return
            state["sent"] = now
            timer(40, step)
            return
        if sequence() == state["before"]:
            if now - state["sent"] < 0.5:
                timer(30, step)
            else:
                done("")  # Nothing selected, or the program does not copy.
            return
        text = clipboard.text()
        clipboard.setMimeData(saved)  # What the user had copied comes back.
        done(text)

    step()


# --- Noticing a selection made with the mouse ------------------------------------------------


class MouseEvents(QObject):
    selected = Signal(int, int)  # A drag or a double click ended here: text may be selected.
    pressed = Signal(int, int)  # Any button went down here.


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0201, 0x0202, 0x0204, 0x0207
DRAG = 6  # Pixels the mouse must move with the button down to count as selecting.


class MouseWatcher:
    """Tells when the left button comes up after a drag, a double click or a triple click: the
    gestures that select text. Nothing else about the mouse is kept. Like SoloKeyHook it runs
    its hook on its own thread, so a busy GUI thread never makes the mouse stutter."""

    def __init__(self):
        self.events = MouseEvents()
        self.down: tuple[int, int, float] | None = None
        self.last_up: tuple[int, int, float] | None = None
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.double = user32.GetDoubleClickTime() / 1000

    def handle(self, message: int, x: int, y: int, now: float) -> None:
        if message in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
            self.events.pressed.emit(x, y)
            if message == WM_LBUTTONDOWN:
                self.down = (x, y, now)
            return
        if message != WM_LBUTTONUP or self.down is None:
            return
        start_x, start_y, _pressed = self.down
        self.down = None
        dragged = abs(x - start_x) > DRAG or abs(y - start_y) > DRAG
        last, self.last_up = self.last_up, (x, y, now)
        clicked_again = last is not None and now - last[2] <= self.double and abs(x - last[0]) <= DRAG and abs(y - last[1]) <= DRAG
        if dragged or clicked_again:
            self.events.selected.emit(x, y)

    def start(self) -> bool:
        ready = threading.Event()
        result = []

        def callback(code, message, data):
            if code == 0:
                try:
                    info = ctypes.cast(data, POINTER(MSLLHOOKSTRUCT)).contents
                    self.handle(int(message), info.pt.x, info.pt.y, time.monotonic())
                except Exception:  # noqa: BLE001 - never let a hook error block the mouse
                    pass
            return user32.CallNextHookEx(None, code, message, data)

        def run():
            self.thread_id = k32.GetCurrentThreadId()
            procedure = HOOKPROC(callback)
            module = k32.GetModuleHandleW(None)
            hook = user32.SetWindowsHookExW(14, procedure, module, 0)  # WH_MOUSE_LL
            result.append(bool(hook))
            ready.set()
            if not hook:
                return
            user32.SetTimer(None, 0, 30_000, None)
            msg = wintypes.MSG()
            while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0113:  # WM_TIMER: put back a hook Windows may have dropped.
                    user32.UnhookWindowsHookEx(hook)
                    hook = user32.SetWindowsHookExW(14, procedure, module, 0)
            if hook:
                user32.UnhookWindowsHookEx(hook)

        self.thread = threading.Thread(target=run, name="selection-mouse-hook", daemon=True)
        self.thread.start()
        ready.wait(5)
        return bool(result and result[0])

    def stop(self) -> None:
        if self.thread and self.thread.is_alive():
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
            self.thread.join(2)
        self.thread = None
        self.down = self.last_up = None
