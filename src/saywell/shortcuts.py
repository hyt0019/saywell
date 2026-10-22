"""Shortcut names, Win32 key codes and the rules for which shortcuts can be used.

A shortcut is stored by name: either a key held on its own ("RightCtrl", watched by a
low-level keyboard hook because RegisterHotKey cannot take a lone modifier) or a
combination such as "Alt+X", "Ctrl+Alt+K" or "F8" registered with RegisterHotKey.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_HOTKEY = "RightCtrl"
INSERT_HOTKEY = "Alt+V"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
# Name, RegisterHotKey flag and the virtual keys that count as holding it, in display order.
MODIFIERS = (
    ("Ctrl", MOD_CONTROL, (0x11,)),
    ("Alt", MOD_ALT, (0x12,)),
    ("Shift", MOD_SHIFT, (0x10,)),
    ("Win", MOD_WIN, (0x5B, 0x5C)),
)

# Held on its own for about SOLO_HOLD_MS before recording starts; a quick tap keeps the key's
# usual job. Low-level hooks report the right-hand modifiers with their own codes.
SOLO_KEYS = {"RightCtrl": 0xA3, "RightAlt": 0xA5, "CapsLock": 0x14}
SOLO_LABELS = {"RightCtrl": "右 Ctrl", "RightAlt": "右 Alt", "CapsLock": "CapsLock"}
SOLO_HOLD_MS = 300

KEYS = {
    **{chr(code): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(digit): 0x30 + digit for digit in range(10)},
    **{f"F{number}": 0x6F + number for number in range(1, 25)},
    "Space": 0x20,
    "Pause": 0x13,
    "ScrollLock": 0x91,
    "Insert": 0x2D,
    "Delete": 0x2E,
    "Home": 0x24,
    "End": 0x23,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "Left": 0x25,
    "Up": 0x26,
    "Right": 0x27,
    "Down": 0x28,
}
KEY_NAMES = {code: name for name, code in KEYS.items()}
# Keys that type nothing, so they can be used without a modifier.
STANDALONE = {f"F{number}" for number in range(1, 25)} | {"Pause", "ScrollLock"}
# Everyday editing and system shortcuts a global hotkey would take away from every program.
RESERVED = {
    "Ctrl+A", "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+Y", "Ctrl+S", "Ctrl+F", "Ctrl+W",
    "Ctrl+Insert", "Shift+Insert", "Shift+Delete", "Alt+Space", "Alt+F4", "Win+L", "Win+D", "Win+E",
    "Win+R", "Win+V", INSERT_HOTKEY,
}

RECORDING_CHOICES = ("RightCtrl", "RightAlt", "CapsLock", "Alt+X", "Alt+C", "Alt+Q", "Alt+Z", "Ctrl+Space", "F8")


@dataclass(frozen=True)
class Shortcut:
    name: str
    modifiers: int = 0
    key: int = 0  # The virtual key: the combination's key, or the solo key itself.
    solo: bool = False


def parse_shortcut(name: str) -> Shortcut | None:
    if name in SOLO_KEYS:
        return Shortcut(name, key=SOLO_KEYS[name], solo=True)
    *mods, key = name.split("+")
    flags = {mod: flag for mod, flag, _vks in MODIFIERS}
    if key not in KEYS or len(set(mods)) != len(mods) or any(mod not in flags for mod in mods):
        return None
    modifiers = 0
    for mod in mods:
        modifiers |= flags[mod]
    return Shortcut(shortcut_name(mods, key), modifiers, KEYS[key])


def shortcut_name(modifiers, key: str) -> str:
    """Canonical name: modifiers in Ctrl, Alt, Shift, Win order, then the key."""
    return "+".join([mod for mod, _flag, _vks in MODIFIERS if mod in modifiers] + [key])


def shortcut_label(name: str) -> str:
    return SOLO_LABELS.get(name, name)


def shortcut_problem(name: str) -> str | None:
    """Why a recording shortcut cannot be used, or None if it can."""
    shortcut = parse_shortcut(name)
    if shortcut is None:
        return "不支持这个键，请换一个。"
    if shortcut.solo:
        return None
    if shortcut.name in RESERVED:
        return f"{shortcut.name} 是常用的编辑或系统快捷键（或好好说的补输键），请换一个。"
    key = shortcut.name.rsplit("+", 1)[-1]
    if key not in STANDALONE and shortcut.modifiers in (0, MOD_SHIFT):
        return "单独的字母、数字、空格或方向键会影响平时打字，请加上 Ctrl、Alt 或 Win，或者用 F1–F24。"
    return None
