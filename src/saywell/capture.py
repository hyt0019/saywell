"""Meeting capture: the audio one app plays and the microphone, each as 16 kHz mono chunks.

The app's audio comes from WASAPI process loopback, which records a process and its children
only, so a video playing in another app stays out of the meeting. Nothing here keeps audio;
every chunk goes straight to the sink.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import uuid
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, c_float, c_long, c_ubyte, c_ulong, c_ushort, c_void_p
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import av
import numpy as np

from .audio import SAMPLE_RATE, AudioError

Sink = Callable[[np.ndarray], None]

# Meeting apps, by executable name, listed before anything else that plays audio.
MEETING_APPS = {
    "wemeetapp.exe": "腾讯会议",
    "feishu.exe": "飞书",
    "lark.exe": "Lark",
    "dingtalk.exe": "钉钉",
    "zoom.exe": "Zoom",
    "ms-teams.exe": "Microsoft Teams",
    "teams.exe": "Microsoft Teams",
    "wxwork.exe": "企业微信",
    "weixin.exe": "微信",
    "wechat.exe": "微信",
    "qq.exe": "QQ",
    "welink.exe": "WeLink",
    "ciscocollabhost.exe": "Webex",
    "webex.exe": "Webex",
    "atmgr.exe": "Webex Meetings",
    "slack.exe": "Slack",
    "discord.exe": "Discord",
}
# A browser plays every tab through one process tree, so all of its tabs are recorded together.
BROWSERS = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "vivaldi.exe": "Vivaldi",
    "360se.exe": "360安全浏览器",
    "360chrome.exe": "360极速浏览器",
    "qqbrowser.exe": "QQ浏览器",
    "sogouexplorer.exe": "搜狗浏览器",
}
# Hosts that start other programs. An app's tree stops below them, or picking one app would
# record everything started from the desktop or a terminal.
HOSTS = {
    "explorer.exe", "svchost.exe", "services.exe", "wininit.exe", "winlogon.exe", "userinit.exe",
    "smss.exe", "csrss.exe", "sihost.exe", "runtimebroker.exe", "taskhostw.exe", "dllhost.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "openconsole.exe", "conhost.exe",
    "applicationframehost.exe", "system", "[system process]",
}
# Windows' own sounds and shell, never a meeting.
SYSTEM = HOSTS | {
    "audiodg.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe",
    "textinputhost.exe", "shellhost.exe", "lockapp.exe", "systemsettings.exe",
}


def loopback_supported() -> bool:
    """Process loopback needs Windows 10 2004 (build 19041) or newer."""
    return sys.platform == "win32" and sys.getwindowsversion().build >= 19041


@dataclass(frozen=True)
class AudioApp:
    name: str
    exe: str
    path: str
    # Roots of the process trees to record; one in nearly every case.
    pids: tuple[int, ...]
    # Something in the app is playing audio right now.
    playing: bool = False
    meeting: bool = False
    browser: bool = False


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    exe: str
    created: int = 0
    path: str = ""


def app_root(pid: int, processes: dict[int, Process]) -> int:
    """The top of the app that `pid` belongs to: its oldest ancestor below the desktop, a terminal
    or a service host. A parent started after its child is a reused process id, not the parent."""
    current = processes[pid]
    seen = {pid}
    while True:
        parent = processes.get(current.parent)
        if (
            parent is None
            or parent.pid in seen
            or parent.exe.lower() in HOSTS
            or (parent.created and current.created and parent.created > current.created)
        ):
            return current.pid
        seen.add(parent.pid)
        current = parent


def group_apps(processes: dict[int, Process], sessions: dict[int, bool], own: int = 0) -> list[AudioApp]:
    """Apps to offer: every app with an audio session, and every meeting app that runs even when
    it is silent. `sessions` maps process ids to whether they play right now. An app started more
    than once (two roots with the same executable) is one entry recording all of its roots."""
    own_root = app_root(own, processes) if own in processes else 0
    roots: dict[int, bool] = {}
    for pid, playing in sessions.items():
        if pid in processes:
            root = app_root(pid, processes)
            roots[root] = roots.get(root, False) or playing
    for pid, process in processes.items():
        if process.exe.lower() in MEETING_APPS:
            roots.setdefault(app_root(pid, processes), False)
    grouped: dict[str, list[int]] = {}
    playing: dict[str, bool] = {}
    for root, active in roots.items():
        exe = processes[root].exe.lower()
        # No executable path: a service or protected process, not something a meeting runs in.
        if root == own_root or exe in SYSTEM or not processes[root].path:
            continue
        grouped.setdefault(exe, []).append(root)
        playing[exe] = playing.get(exe, False) or active
    apps = []
    for exe, pids in grouped.items():
        pids.sort(key=lambda pid: processes[pid].created or pid)
        first = processes[pids[0]]
        name = MEETING_APPS.get(exe) or BROWSERS.get(exe) or describe(first.path) or Path(first.exe).stem
        apps.append(
            AudioApp(name, first.exe, first.path, tuple(pids), playing[exe], exe in MEETING_APPS, exe in BROWSERS)
        )
    # Meeting apps first, then whatever plays now, then browsers; by name within each.
    apps.sort(key=lambda app: (not app.meeting, not app.playing, not app.browser, app.name.lower()))
    return apps


def describe(path: str) -> str:
    """The product's own name from its version resource, such as "Spotify" or "网易云音乐"."""
    if not path:
        return ""
    try:
        import win32api

        language, codepage = win32api.GetFileVersionInfo(path, "\\VarFileInfo\\Translation")[0]
        for key in ("FileDescription", "ProductName"):
            value = win32api.GetFileVersionInfo(path, f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{key}")
            if value and value.strip():
                return value.strip()
    except Exception:
        pass
    return ""


# --- Windows plumbing -------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
ole32 = ctypes.OleDLL("ole32") if sys.platform == "win32" else None

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x2
CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
RPC_E_CHANGED_MODE = -2147417850
E_NOINTERFACE = -2147467262
STILL_ACTIVE = 259


class GUID(Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_MMDeviceEnumerator = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IUnknown = guid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90")
IID_IMMDeviceEnumerator = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
IID_IAudioClient = guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
IID_IActivateAudioInterfaceCompletionHandler = guid("41D949AB-9862-444A-80F6-C261334DA5EB")


class PROCESSENTRY32W(Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class PROCESS_LOOPBACK_PARAMS(Structure):
    _fields_ = [("ActivationType", c_long), ("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", c_long)]


class BLOB(Structure):
    _fields_ = [("cbSize", c_ulong), ("pBlobData", c_void_p)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", c_ushort), ("reserved1", c_ushort), ("reserved2", c_ushort), ("reserved3", c_ushort),
                ("blob", BLOB)]


if kernel32 is not None:
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, POINTER(PROCESSENTRY32W)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [POINTER(ctypes.c_uint64)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, POINTER(wintypes.DWORD)]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    ole32.CoInitializeEx.restype = c_long  # S_FALSE and RPC_E_CHANGED_MODE are expected answers.
    ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p)]


def method(pointer: c_void_p, index: int, *argtypes, restype=ctypes.HRESULT):
    """Entry `index` of a COM interface's table, bound to the interface. An HRESULT failure raises
    OSError."""
    table = ctypes.cast(pointer, POINTER(POINTER(c_void_p)))[0]
    function = WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])
    return lambda *args: function(pointer, *args)


def release(pointer: c_void_p | None) -> None:
    if pointer:
        method(pointer, 2, restype=c_ulong)()


def query(pointer: c_void_p, iid: GUID) -> c_void_p:
    result = c_void_p()
    method(pointer, 0, POINTER(GUID), POINTER(c_void_p))(byref(iid), byref(result))
    return result


class com_thread:
    """Initialize COM for the calling thread. A thread that already runs COM in another mode (Qt's
    main thread) keeps it, which works for everything here."""

    def __enter__(self):
        self.owned = ole32.CoInitializeEx(None, COINIT_MULTITHREADED) in (0, 1)
        return self

    def __exit__(self, *_):
        if self.owned:
            ole32.CoUninitialize()


def processes() -> dict[int, Process]:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return {}
    found: dict[int, Process] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, byref(entry))
        while more:
            pid = entry.th32ProcessID
            if pid:
                created, path = process_details(pid)
                found[pid] = Process(pid, entry.th32ParentProcessID, entry.szExeFile, created, path)
            more = kernel32.Process32NextW(snapshot, byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def process_details(pid: int) -> tuple[int, str]:
    """When the process started (FILETIME ticks) and its executable, where Windows lets us see them."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0, ""
    try:
        created, other = ctypes.c_uint64(), ctypes.c_uint64()
        if not kernel32.GetProcessTimes(handle, byref(created), byref(other), byref(other), byref(other)):
            created.value = 0
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        path = buffer.value if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, byref(size)) else ""
        return created.value, path
    finally:
        kernel32.CloseHandle(handle)


def process_running(pid: int) -> bool:
    """Whether the process still runs. Recording an app that has quit gives silence, not an error,
    so a meeting checks this to notice the app closing."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, byref(code))) and code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def audio_sessions() -> dict[int, bool]:
    """Process id → playing right now, for every audio session on an active output device."""
    sessions: dict[int, bool] = {}
    with com_thread():
        enumerator = c_void_p()
        ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL, byref(IID_IMMDeviceEnumerator), byref(enumerator)
        )
        collection = c_void_p()
        try:
            # EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)
            method(enumerator, 3, c_long, wintypes.DWORD, POINTER(c_void_p))(0, 1, byref(collection))
            count = ctypes.c_uint()
            method(collection, 3, POINTER(ctypes.c_uint))(byref(count))
            for index in range(count.value):
                device = c_void_p()
                method(collection, 4, ctypes.c_uint, POINTER(c_void_p))(index, byref(device))
                try:
                    _device_sessions(device, sessions)
                except OSError:
                    pass  # A device that went away while listing.
                finally:
                    release(device)
        finally:
            release(collection)
            release(enumerator)
    return sessions


def _device_sessions(device: c_void_p, sessions: dict[int, bool]) -> None:
    manager = c_void_p()
    method(device, 3, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(
        byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, byref(manager)
    )
    listing = c_void_p()
    try:
        method(manager, 5, POINTER(c_void_p))(byref(listing))
        count = ctypes.c_int()
        method(listing, 3, POINTER(ctypes.c_int))(byref(count))
        for index in range(count.value):
            control = c_void_p()
            method(listing, 4, ctypes.c_int, POINTER(c_void_p))(index, byref(control))
            control2 = None
            try:
                control2 = query(control, IID_IAudioSessionControl2)
                pid, state = wintypes.DWORD(), c_long()
                method(control2, 14, POINTER(wintypes.DWORD), restype=c_long)(byref(pid))
                method(control2, 3, POINTER(c_long))(byref(state))
                if pid.value:
                    sessions[pid.value] = sessions.get(pid.value, False) or state.value == 1
            except OSError:
                pass
            finally:
                release(control2)
                release(control)
    finally:
        release(listing)
        release(manager)


def audio_apps() -> list[AudioApp]:
    """Apps whose audio a meeting can record, meeting apps first."""
    if sys.platform != "win32":
        return []
    try:
        sessions = audio_sessions()
    except OSError:
        sessions = {}  # Windows Audio is not running; meeting apps are still offered.
    return group_apps(processes(), sessions, os.getpid())


# --- Capture -----------------------------------------------------------------------------------


class Timeline:
    """Keeps a source in step with the clock: when a source delivers nothing for a while (an app
    that is silent, a stalled device), the gap is filled with silence so that a moment in the
    stream is a moment in the meeting, the same for every source."""

    GAP = 0.3

    def __init__(self, sink: Sink, started: float | None = None):
        self.sink = sink
        self.started = time.monotonic() if started is None else started
        self.count = 0

    def deliver(self, samples: np.ndarray, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        missing = int((now - self.started) * SAMPLE_RATE) - self.count - samples.size
        if missing > self.GAP * SAMPLE_RATE:
            self._send(np.zeros(missing, dtype=np.float32))
        if samples.size:
            self._send(samples)

    def _send(self, samples: np.ndarray) -> None:
        self.count += samples.size
        self.sink(samples)


class StreamResampler:
    """Brings a running stream to 16 kHz mono, keeping the filter's state between chunks."""

    def __init__(self, rate: int, channels: int = 1):
        self.rate = rate
        self.channels = channels
        layout = "mono" if channels == 1 else "stereo"
        self.layout = layout
        self.resampler = (
            None if rate == SAMPLE_RATE and channels == 1
            else av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        )

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        if self.resampler is None:
            return samples.reshape(-1).copy()
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="flt", layout=self.layout)
        frame.sample_rate = self.rate
        frames = self.resampler.resample(frame)
        if not frames:
            return np.empty(0, dtype=np.float32)
        return np.concatenate([part.to_ndarray().reshape(-1) for part in frames]).astype(np.float32)


class MicAudio:
    """The microphone, delivered in step with the clock."""

    def __init__(self, sink: Sink, device: int | None = None, started: float | None = None):
        self.device = device
        self.timeline = Timeline(sink, started)
        self.stream = None
        self.resample: StreamResampler | None = None
        self.failed = ""

    def start(self) -> None:
        import sounddevice as sd

        try:
            info = sd.query_devices(self.device, "input")
            rate = int(info["default_samplerate"])
            self.resample = StreamResampler(rate)
            self.stream = sd.InputStream(
                device=self.device, samplerate=rate, channels=1, dtype="float32", callback=self._callback, blocksize=0
            )
            self.stream.start()
        except (sd.PortAudioError, ValueError):
            self.stop()
            raise AudioError("无法打开麦克风，请检查 Windows 麦克风权限及所选设备。") from None

    def _callback(self, data, frames, time_info, status):
        if self.stream is not None:
            self.timeline.deliver(self.resample(data[:, 0]))

    def stop(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass  # A microphone unplugged mid-meeting.
            finally:
                stream.close()


# Initialize flags: loopback, event driven, and let Windows convert to the format asked for.
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_IEEE_FLOAT = 3
VT_BLOB = 65
PROCESS_LOOPBACK = "VAD\\Process_Loopback"

QueryInterfaceType = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
RefCountType = WINFUNCTYPE(c_ulong, c_void_p)
CompletedType = WINFUNCTYPE(c_long, c_void_p, c_void_p)


class HandlerTable(Structure):
    _fields_ = [("QueryInterface", QueryInterfaceType), ("AddRef", RefCountType), ("Release", RefCountType),
                ("ActivateCompleted", CompletedType)]


class HandlerObject(Structure):
    _fields_ = [("table", POINTER(HandlerTable))]


class CompletionHandler:
    """A minimal IActivateAudioInterfaceCompletionHandler (agile, so Windows may call it on any
    thread). It lives as long as this Python object; activation finishes before that."""

    def __init__(self):
        self.done = threading.Event()
        accepted = [bytes(iid) for iid in (IID_IUnknown, IID_IAgileObject, IID_IActivateAudioInterfaceCompletionHandler)]

        def query_interface(this, iid, out):
            if bytes(iid.contents) in accepted:
                out[0] = this
                return 0
            out[0] = None
            return E_NOINTERFACE

        def completed(this, operation):
            self.done.set()
            return 0

        self.functions = (QueryInterfaceType(query_interface), RefCountType(lambda this: 1),
                          RefCountType(lambda this: 1), CompletedType(completed))
        self.table = HandlerTable(*self.functions)
        self.object = HandlerObject(ctypes.pointer(self.table))

    @property
    def pointer(self) -> c_void_p:
        return c_void_p(ctypes.addressof(self.object))


def activate_loopback(pid: int) -> c_void_p:
    """An IAudioClient for process `pid` and its children."""
    mmdevapi = ctypes.WinDLL("mmdevapi")
    activate = mmdevapi.ActivateAudioInterfaceAsync
    activate.restype = ctypes.HRESULT
    activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT), c_void_p, POINTER(c_void_p)]
    params = PROCESS_LOOPBACK_PARAMS(1, pid, 0)  # Process loopback, including the target's tree.
    variant = PROPVARIANT(vt=VT_BLOB)
    variant.blob = BLOB(ctypes.sizeof(params), ctypes.addressof(params))
    handler = CompletionHandler()
    operation = c_void_p()
    activate(PROCESS_LOOPBACK, byref(IID_IAudioClient), byref(variant), handler.pointer, byref(operation))
    try:
        if not handler.done.wait(5):
            raise AudioError("Windows 没有及时响应录制请求。")
        result, unknown = ctypes.HRESULT(), c_void_p()
        method(operation, 3, POINTER(ctypes.HRESULT), POINTER(c_void_p))(byref(result), byref(unknown))
        if result.value < 0:
            raise OSError(None, "activation failed", None, result.value)
        try:
            return query(unknown, IID_IAudioClient)
        finally:
            release(unknown)
    finally:
        release(operation)


class AppAudio:
    """What one app (a process tree) plays, through process loopback, delivered in step with the
    clock. Windows converts it to 16 kHz mono for us."""

    def __init__(self, pid: int, sink: Sink, started: float | None = None):
        self.pid = pid
        self.timeline = Timeline(sink, started)
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.error = ""

    def start(self, timeout: float = 6.0) -> None:
        self.thread = threading.Thread(target=self._run, name=f"app-audio-{self.pid}", daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout) or self.error:
            self.stop()
            raise AudioError(self.error or "无法录制所选应用的声音。")

    def stop(self) -> None:
        self.stopping.set()
        thread, self.thread = self.thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _run(self) -> None:
        client = capture = None
        event = None
        with com_thread():
            try:
                client = activate_loopback(self.pid)
                fmt = WAVEFORMATEX(WAVE_FORMAT_IEEE_FLOAT, 1, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 32, 0)
                flags = (
                    AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                    | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
                )
                # Initialize(shared, flags, 200 ms buffer, 0, format, no session)
                method(client, 3, c_long, wintypes.DWORD, ctypes.c_int64, ctypes.c_int64, POINTER(WAVEFORMATEX),
                       c_void_p)(0, flags, 2_000_000, 0, byref(fmt), None)
                event = kernel32.CreateEventW(None, False, False, None)
                method(client, 13, wintypes.HANDLE)(event)
                capture = c_void_p()
                method(client, 14, POINTER(GUID), POINTER(c_void_p))(byref(IID_IAudioCaptureClient), byref(capture))
                method(client, 10)()
            except (OSError, AudioError) as error:
                self.error = str(error) if isinstance(error, AudioError) else "无法录制所选应用的声音。"
                self.ready.set()
                release(capture)
                release(client)
                if event:
                    kernel32.CloseHandle(event)
                return
            self.ready.set()
            get_buffer = method(capture, 3, POINTER(POINTER(c_float)), POINTER(ctypes.c_uint32),
                                POINTER(wintypes.DWORD), c_void_p, c_void_p)
            release_buffer = method(capture, 4, ctypes.c_uint32)
            next_size = method(capture, 5, POINTER(ctypes.c_uint32))
            data, frames, flags_out, size = POINTER(c_float)(), ctypes.c_uint32(), wintypes.DWORD(), ctypes.c_uint32()
            try:
                while not self.stopping.is_set():
                    kernel32.WaitForSingleObject(event, 100)
                    chunks = []
                    next_size(byref(size))
                    while size.value:
                        get_buffer(byref(data), byref(frames), byref(flags_out), None, None)
                        count = frames.value
                        if flags_out.value & AUDCLNT_BUFFERFLAGS_SILENT or not count:
                            chunks.append(np.zeros(count, dtype=np.float32))
                        else:
                            chunks.append(np.ctypeslib.as_array(data, (count,)).copy())
                        release_buffer(count)
                        next_size(byref(size))
                    # Silence fills the gaps when the app plays nothing for a while.
                    self.timeline.deliver(np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32))
            except OSError:
                self.error = "所选应用的声音中断了。"
            finally:
                try:
                    method(client, 11)()
                except OSError:
                    pass
                release(capture)
                release(client)
                kernel32.CloseHandle(event)
