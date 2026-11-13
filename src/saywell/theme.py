"""Light and dark. Every colour in the code is written for paper; in the dark theme tone() and css()
turn each into its night counterpart, the 墨夜 palette: a deep ink ground, cards a step lighter,
and the paper colour for text, so the app reads as the light one with ink and paper swapped.

The main colours have exact counterparts in NIGHT. Any other is turned by a rule: a pale surface
becomes a dark one that sits as far above the cards as it sat below them on paper, dark text
becomes light, a colour keeps its hue (indigo links become a light periwinkle, coral stays coral),
and a pale tint becomes a deep one of the same hue.

The theme switches while the app runs. Stylesheets given through styled() and the app's own are
written again, icons drawn by glyph labels are drawn again, every widget repaints, and changed()
lets windows refresh text they built with colours in it."""

from __future__ import annotations

import colorsys
import re

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat

APPEARANCES = ("light", "dark", "system")

# The light colours that matter most, and exactly what they become at night.
NIGHT = {
    "#F6F1E8": "#13162A",  # The window.
    "#FFFCF7": "#1B1F35",  # Cards, fields, menus.
    "#FBF7F0": "#20243C",  # Keycaps.
    "#FAF8F4": "#1F2339",
    "#F8F4ED": "#20243B",  # Hover on a light chip.
    "#F3F3F8": "#22263F",
    "#F2ECE2": "#232842",
    "#F1EBE1": "#232842",
    "#EFE9DE": "#1F2339",
    "#EFE8DC": "#171A2F",  # The settings sidebar.
    "#EEE7DB": "#262B44",  # Plain buttons, rules.
    "#E8E0D3": "#2B304B",  # Hairlines.
    "#E7DFD1": "#2C3250",
    "#E5DCCD": "#333955",
    "#E2D9CA": "#303654",
    "#DED5C5": "#333A5A",
    "#DDD5C8": "#363C5A",
    "#DCD3C4": "#363C5A",  # Field borders.
    "#D0C5B3": "#3E4463",
    "#CFC6B8": "#454B6B",  # Disabled, scrollbars.
    "#C4BAAA": "#4F5577",
    "#B5AC9F": "#6D7189",
    "#A69D90": "#5A6080",
    "#9D9487": "#8E887E",
    "#958C7F": "#8A847A",  # Captions.
    "#8C8376": "#8E887E",  # Group names.
    "#776F63": "#A39D92",  # Quieter text.
    "#5A5248": "#C2BCB1",
    "#534C43": "#5B6180",
    "#47413A": "#CFC9BE",  # Secondary text.
    "#1D2240": "#E9E4D8",  # Text.
    "#192457": "#F1ECE1",  # Titles, the primary button, the chosen thing.
    "#26336E": "#FFFAF0",  # The primary button under the mouse.
    "#111A44": "#D9D3C6",  # ... and pressed.
    "#3C488E": "#A6B1F2",  # Indigo: links.
    "#ECEDF5": "#232A4D",  # Indigo wash.
    "#DFE2F1": "#2C3460",
    "#D9DCEC": "#2C3460",
    "#D8DBEE": "#34406E",  # Selected text.
    "#D3D7EB": "#3A4370",
    "#C3C8E4": "#4A5590",
    "#F2755F": "#F4826D",  # Coral.
    "#E5654F": "#F07A64",  # Recording.
    "#5A9E6F": "#6DB583",  # Passed.
    "#3F7A53": "#8FCB9F",
    "#B94534": "#F0907E",  # Failed.
    "#FBEAE5": "#3A2427",
    "#FAE9E4": "#3A2427",
    "#F6D9D0": "#4A2C2F",
    "#FBF2DE": "#332B1C",  # Notices.
    "#EEDDB4": "#4D4128",
    "#77561F": "#E6C98A",
    "#000000": "#000000",  # Shadows stay shadows.
}

_dark = False
_appearance = "light"
_base_style = ""
_notifier: Theme | None = None


class Theme(QObject):
    changed = Signal()


def notifier() -> Theme:
    global _notifier
    if _notifier is None:
        _notifier = Theme()
    return _notifier


def is_dark() -> bool:
    return _dark


def _hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


_turned: dict[str, tuple[int, int, int]] = {}


def _night(r: int, g: int, b: int) -> tuple[int, int, int]:
    key = _hex(r, g, b)
    if key in _turned:
        return _turned[key]
    if key in NIGHT:
        color = QColor(NIGHT[key])
        result = color.red(), color.green(), color.blue()
    else:
        hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if sat >= 0.28 and 0.12 < light < 0.88:  # A colour: keep its hue.
            light, sat = (0.74, min(sat, 0.7)) if light < 0.45 else (min(0.8, light + 0.05), sat)
        elif sat >= 0.28 and light >= 0.88:  # A pale tint: a deep one.
            light, sat = 0.21, min(sat, 0.4)
        else:  # Paper, ink and the greys between.
            light = 0.155 + (0.985 - light) * 0.9 if light >= 0.8 else min(0.93, 1.03 - light)
            hue, sat = (230 / 360, 0.3) if light < 0.5 else (40 / 360, 0.2)
        red, green, blue = colorsys.hls_to_rgb(hue, max(0.0, min(1.0, light)), sat)
        result = round(red * 255), round(green * 255), round(blue * 255)
    _turned[key] = result
    return result


def night(color: str) -> str:
    """A "#RRGGBB" colour as the dark theme shows it."""
    value = QColor(color)
    return _hex(*_night(value.red(), value.green(), value.blue()))


def tone(*value, dark: str | None = None) -> QColor:
    """The colour for the current theme. Takes what QColor takes: a colour name, (r, g, b[, a]),
    or a QColor, which is taken as already toned. dark names the night colour for this one use."""
    if len(value) == 1 and isinstance(value[0], QColor):
        return QColor(value[0])
    color = QColor(*value)
    if not _dark or not color.isValid():
        return color
    alpha = color.alpha()
    if dark is not None:
        turned = QColor(dark)
    else:
        turned = QColor(*_night(color.red(), color.green(), color.blue()))
    turned.setAlpha(alpha)
    return turned


def hexed(color: str) -> str:
    """A colour name for text built at run time, such as rich-text labels."""
    return night(color) if _dark else color


# A colour may name its own night colour in a comment after it: #192457/*night #7F8BDB*/.
_HEX = re.compile(r"#([0-9A-Fa-f]{6})(?:/\*night (#[0-9A-Fa-f]{6})\*/)?(?![0-9A-Fa-f])")
_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(,\s*[\d.]+\s*)?\)")


def css(text: str) -> str:
    """A stylesheet or rich text with its colours turned for the current theme."""
    if not _dark:
        return text
    text = _HEX.sub(lambda match: match.group(2) or night("#" + match.group(1)), text)

    def rgb(match):
        r, g, b = _night(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        alpha = match.group(4)
        return f"rgba({r}, {g}, {b}{alpha})" if alpha else f"rgb({r}, {g}, {b})"

    return _RGB.sub(rgb, text)


def retint(document: QTextDocument) -> None:
    """Text coloured with tone() as it was written, coloured again for the theme now, edits and
    all: each colour the other theme knows by name in NIGHT is swapped for its counterpart."""
    if _dark:
        swap = {QColor(paper).rgb(): night_color for paper, night_color in NIGHT.items()}
    else:
        swap = {QColor(night_color).rgb(): paper for paper, night_color in reversed(NIGHT.items())}
    changes = []
    block = document.begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            if fragment.isValid() and fragment.charFormat().hasProperty(QTextFormat.Property.ForegroundBrush):
                color = fragment.charFormat().foreground().color()
                if color.rgb() in swap:
                    turned = QColor(swap[color.rgb()])
                    turned.setAlpha(color.alpha())
                    changes.append((fragment.position(), fragment.length(), turned))
            fragments += 1
        block = block.next()
    cursor = QTextCursor(document)
    for position, length, color in changes:
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        change = QTextCharFormat()
        change.setForeground(color)
        cursor.mergeCharFormat(change)


def styled(widget, text: str) -> None:
    """Give a widget a stylesheet written for paper; it is written again when the theme changes."""
    widget.setProperty("paperStyle", text)
    widget.setStyleSheet(css(text))


def install(base_style: str) -> None:
    """The app-wide stylesheet, written for paper."""
    global _base_style
    _base_style = base_style
    app = QGuiApplication.instance()
    if app is not None and hasattr(app, "setStyleSheet"):
        app.setStyleSheet(css(base_style))


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def appearance() -> str:
    return _appearance


def apply(choice: str) -> None:
    """Light, dark, or as Windows is set; in the last case it follows Windows while running."""
    global _appearance, _dark
    choice = choice if choice in APPEARANCES else "light"
    first = not _watching
    _appearance = choice
    hints = QGuiApplication.styleHints()
    # Title bars and anything Qt draws itself follow the same choice.
    if choice == "system":
        hints.unsetColorScheme()
    else:
        hints.setColorScheme(Qt.ColorScheme.Dark if choice == "dark" else Qt.ColorScheme.Light)
    _watch(hints)
    dark = choice == "dark" or choice == "system" and system_is_dark()
    if dark == _dark and not first:
        return
    _dark = dark
    _refresh()


_watching: list[bool] = []


def _watch(hints) -> None:
    if not _watching:
        hints.colorSchemeChanged.connect(_system_changed)
        _watching.append(True)


def _system_changed(*_):
    global _dark
    if _appearance != "system":
        return
    dark = system_is_dark()
    if dark != _dark:
        _dark = dark
        _refresh()


def _refresh() -> None:
    app = QGuiApplication.instance()
    if app is None or not hasattr(app, "allWidgets"):
        return
    if _base_style:
        app.setStyleSheet(css(_base_style))
    for widget in app.allWidgets():
        text = widget.property("paperStyle")
        if text:
            widget.setStyleSheet(css(text))
        redraw = getattr(widget, "retheme", None)
        if callable(redraw):
            redraw()
        widget.update()
    notifier().changed.emit()
