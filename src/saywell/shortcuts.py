"""Shared shortcut names and Win32 modifier/key values."""

DEFAULT_HOTKEY = "Alt+X"
INSERT_HOTKEY = "Alt+V"
HOTKEYS = {
    "Alt+X": (0x0001, ord("X")),
    "Alt+C": (0x0001, ord("C")),
    "Alt+Q": (0x0001, ord("Q")),
    "Alt+Z": (0x0001, ord("Z")),
    "Ctrl+Space": (0x0002, 0x20),
    # Keep existing saved choices usable after upgrading.
    "Ctrl+Shift+Space": (0x0002 | 0x0004, 0x20),
    "Ctrl+Alt+Space": (0x0002 | 0x0001, 0x20),
    "Alt+Shift+Space": (0x0001 | 0x0004, 0x20),
}
RECORDING_CHOICES = ("Alt+X", "Alt+C", "Alt+Q", "Alt+Z", "Ctrl+Space")
