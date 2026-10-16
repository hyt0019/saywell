"""Verify hotkeys and paste only against this script's own native test window."""

import ctypes
import json
import os

from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from saywell.windows import Hotkeys, INPUT, KEYBDINPUT, current_target, paste_text, user32

app = QApplication([])
window = QWidget()
window.setWindowTitle("Saywell — isolated input test")
layout = QVBoxLayout(window)
layout.addWidget(QLabel("仅用于验证当前测试窗口的快捷键和粘贴，稍后自动关闭。"))
editor = QPlainTextEdit()
layout.addWidget(editor)
window.resize(600, 240)
hotkeys = Hotkeys()
app.installNativeEventFilter(hotkeys)
result = {
    "hotkey_registered": hotkeys.register(91, 1, ord("X")),
    "hotkey_received": False,
    "paste_matches": False,
    "clipboard_restored": False,
}
hotkeys.events.triggered.connect(lambda identifier: result.update(hotkey_received=identifier == 91))
saved = QMimeData()
old = app.clipboard().mimeData()
if old:
    for format_name in old.formats():
        saved.setData(format_name, old.data(format_name))
window.show()
window.activateWindow()
editor.setFocus()
result["activated"] = bool(user32.SetForegroundWindow(int(window.winId())))


def verify_focus():
    target = current_target()
    return target and target.process == os.getpid()


def trigger():
    if not verify_focus() or not result["hotkey_registered"]:
        result["error"] = "Test window is not focused or hotkey unavailable; no input was sent."
        finish()
        return
    events = (INPUT * 4)()
    for event, (key, flags) in zip(events, ((0x12, 0), (ord("X"), 0), (ord("X"), 2), (0x12, 2)), strict=True):
        event.type = 1
        event.ki = KEYBDINPUT(key, 0, flags, 0, 0)
    user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    QTimer.singleShot(300, paste)


def paste():
    if not verify_focus():
        result["error"] = "Focus changed; no paste was sent."
        finish()
        return
    app.clipboard().setText("Saywell clipboard restore sentinel")
    try:
        paste_text("语音输入测试 123", current_target(), app.clipboard())
    except Exception as exc:
        result["error"] = str(exc)
    QTimer.singleShot(1400, finish)


def finish():
    result["paste_matches"] = editor.toPlainText() == "语音输入测试 123"
    result["clipboard_restored"] = app.clipboard().text() == "Saywell clipboard restore sentinel"
    if result["clipboard_restored"]:
        app.clipboard().setMimeData(saved)
    hotkeys.close()
    app.removeNativeEventFilter(hotkeys)
    print(json.dumps(result), flush=True)
    app.exit(
        0
        if all(
            result.get(k)
            for k in ("hotkey_registered", "hotkey_received", "paste_matches", "clipboard_restored")
        )
        else 1
    )


QTimer.singleShot(700, trigger)
raise SystemExit(app.exec())
