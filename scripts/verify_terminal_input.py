"""Interactive regression check using only disposable, script-owned consoles.

The consoles start hidden, then briefly appear for foreground input testing.
No Enter is sent and no text is injected unless the foreground window belongs
to the receiver created by this script. Reports contain booleans, not screen text.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def receiver(directory: Path, shell: str):
    import win32con
    import win32console
    import win32file

    kernel = ctypes.WinDLL("kernel32")
    kernel.GetConsoleWindow.restype = wintypes.HWND
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    command = (
        [os.environ["COMSPEC"], "/d", "/q", "/k", "prompt Saywell-CMD$G"]
        if shell == "cmd" else
        [str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NoExit", "-Command", "function prompt { 'Saywell-PowerShell> ' }"]
    )
    child = subprocess.Popen(command)
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not (directory / "stop").exists():
            handle = win32file.CreateFile(
                "CONOUT$", win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None, win32con.OPEN_EXISTING, 0, None,
            )
            output = win32console.PyConsoleScreenBufferType(handle.Detach())
            try:
                # PowerShell can activate a different buffer than the inherited stdout handle.
                size = output.GetConsoleScreenBufferInfo()["Size"]
                screen = output.ReadConsoleOutputCharacter(size.X * size.Y, win32console.PyCOORDType(0, 0))
            finally:
                output.Close()
            report = {
                "window": int(kernel.GetConsoleWindow()), "pid": os.getpid(),
                "parent_pid": os.getppid(), "shell_pid": child.pid,
                "ready": "Saywell-" in screen,
                "legacy_present": expected["legacy"] in screen,
                "fixed_present": expected["fixed"] in screen,
                "shell_exited": child.poll() is not None,
            }
            pending = directory / "report.tmp"
            pending.write_text(json.dumps(report), encoding="utf-8")
            pending.replace(directory / "report.json")
            time.sleep(0.1)
    finally:
        child.terminate()
        child.wait(timeout=5)


def verify(shell: str) -> dict:
    from PySide6.QtWidgets import QApplication
    from saywell import windows

    app = QApplication.instance() or QApplication([])
    clipboard = app.clipboard()
    foreground = windows.user32.GetForegroundWindow()
    directory = Path(tempfile.mkdtemp(prefix="terminal-input-", dir=Path(__file__).resolve().parents[1] / "artifacts"))
    suffix = uuid.uuid4().hex[:8]
    expected = {"legacy": f"旧输入{suffix}", "fixed": f"新输入{suffix}"}
    (directory / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # Start hidden; the owned interactive test is shown after validation.
    host = subprocess.Popen(
        [str(Path(os.environ["WINDIR"]) / "System32/conhost.exe"), sys.executable, __file__,
         "--receiver", str(directory), "--shell", shell], startupinfo=startup,
    )
    target = None
    original_send = windows.user32.SendInput
    result = {"shell": shell, "legacy_present": False, "fixed_present": False}

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def report():
        try:
            return json.loads((directory / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def legacy_send(count, pointer, size):
        events = ctypes.cast(pointer, ctypes.POINTER(windows.INPUT))
        for index in range(count):
            events[index].ki.wScan = 0
            events[index].ki.dwFlags &= ~1
        return original_send(count, pointer, size)

    try:
        for _ in range(100):
            if report().get("ready"):
                break
            pump(0.2)
        data = report()
        if not data.get("ready"):
            result["error"] = "Disposable console did not become ready."
            result["shell_exited"] = data.get("shell_exited")
            return result
        window = data["window"]
        pid = wintypes.DWORD()
        windows.user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value not in (data["pid"], data["parent_pid"], data["shell_pid"], host.pid):
            result["error"] = "Window ownership could not be verified; no input sent."
            result["owner_pid"] = pid.value
            return result
        windows.user32.ShowWindow(window, 4)  # Interactive verification window; no activation yet.
        windows.user32.SetForegroundWindow(window)
        if windows.user32.GetForegroundWindow() != window:
            # Share the foreground input queue briefly to activate only our own test window.
            current_thread = ctypes.WinDLL("kernel32").GetCurrentThreadId()
            front_thread = windows.user32.GetWindowThreadProcessId(windows.user32.GetForegroundWindow(), None)
            attached = bool(windows.user32.AttachThreadInput(current_thread, front_thread, True))
            try:
                if attached:
                    windows.user32.SetForegroundWindow(window)
            finally:
                if attached:
                    windows.user32.AttachThreadInput(current_thread, front_thread, False)
        pump(0.4)
        target = windows.current_target()
        if not target or target.window != window:
            result["error"] = "Owned console could not take focus; no input sent."
            result["foreground_is_owned_console"] = windows.user32.GetForegroundWindow() == window
            result["target_detected"] = target is not None
            result["console_visible"] = bool(windows.user32.IsWindowVisible(window))
            return result
        for mode in ("legacy", "fixed"):
            windows.user32.SendInput = legacy_send if mode == "legacy" else original_send
            windows.paste_text(expected[mode], target, clipboard)
            pump(1.5)
            result[f"{mode}_present"] = report().get(f"{mode}_present", False)
        return result
    finally:
        windows.user32.SendInput = original_send
        if target and windows.user32.GetForegroundWindow() == target.window and foreground:
            windows.user32.SetForegroundWindow(foreground)
        (directory / "stop").touch()
        try:
            host.wait(timeout=6)
        except subprocess.TimeoutExpired:
            host.terminate()
        # Let paste_text restore the clipboard only if it was not changed by the user.
        pump(1.1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", type=Path)
    parser.add_argument("--shell", choices=("cmd", "powershell"), default="cmd")
    args = parser.parse_args()
    if args.receiver:
        receiver(args.receiver, args.shell)
    else:
        result = verify(args.shell)
        print(json.dumps(result), flush=True)
        raise SystemExit(0 if result.get("fixed_present") else 1)
