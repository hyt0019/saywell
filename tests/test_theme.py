import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication, QLabel

from saywell import theme
from saywell.settings import Settings
from saywell.ui import STYLE, ThemedLabel


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    theme.install(STYLE)
    yield app
    theme.apply("light")
    app.setStyleSheet(STYLE)


@pytest.fixture(autouse=True)
def back_to_light():
    yield
    theme.apply("light")


def dispose(app, *widgets):
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()


def test_theme_settings_are_checked_when_read(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"appearance": "dark", "selection_icon_size": "large", "selection_icon_opacity": 45}))
    loaded = Settings.load(path)
    assert (loaded.appearance, loaded.selection_icon_size, loaded.selection_icon_opacity) == ("dark", "large", 45)
    path.write_text(json.dumps({"appearance": "sepia", "selection_icon_size": "huge", "selection_icon_opacity": 10}))
    loaded = Settings.load(path)
    assert (loaded.appearance, loaded.selection_icon_size, loaded.selection_icon_opacity) == ("light", "medium", 100)


def test_light_leaves_colours_alone_and_dark_turns_them(qt_app):
    text = "QLabel { color: #1D2240; background: rgba(255, 252, 247, 0.5); border: 1px solid #192457/*night #7F8BDB*/; }"
    theme.apply("light")
    assert theme.css(text) == text
    assert theme.tone("#1D2240").name().upper() == "#1D2240"
    theme.apply("dark")
    assert theme.is_dark()
    turned = theme.css(text)
    assert "color: #E9E4D8" in turned and "border: 1px solid #7F8BDB;" in turned
    assert "rgba(27, 31, 53, 0.5)" in turned
    shade = theme.tone(0, 0, 0, 40)
    assert (shade.name(), shade.alpha()) == ("#000000", 40)  # Shadows stay shadows.
    faint = theme.tone(29, 34, 64, 120)
    assert faint.name().upper() == "#E9E4D8" and faint.alpha() == 120
    assert theme.tone("#192457", dark="#7F8BDB").name().upper() == "#7F8BDB"
    # A colour without its own counterpart keeps its hue and goes the other way in lightness.
    pale, deep = QColor(theme.night("#F4F0FF")), QColor(theme.night("#6A3FA0"))
    assert pale.lightness() < 90 and deep.lightness() > 150


def test_switching_restyles_what_is_open_and_comes_back_exactly(qt_app):
    plain = QLabel("x")
    theme.styled(plain, "color: #776F63;")
    rich = ThemedLabel("<span style='color:#3C488E'>链接</span>")
    theme.apply("dark")
    assert qt_app.styleSheet() == theme.css(STYLE) != STYLE
    assert plain.styleSheet() == "color: #A39D92;"
    assert "#A6B1F2" in rich.text()
    theme.apply("light")
    assert qt_app.styleSheet() == STYLE
    assert plain.styleSheet() == "color: #776F63;"
    assert "#3C488E" in rich.text()
    dispose(qt_app, plain, rich)


def test_documents_are_recoloured_in_place(qt_app):
    document = QTextDocument()
    cursor = QTextCursor(document)
    for color, words in (("#1D2240", "我改过的字"), ("#958C7F", "灰色说明")):
        form = QTextCharFormat()
        form.setForeground(theme.tone(color))
        cursor.insertText(words, form)
    theme.apply("dark")
    theme.retint(document)
    block = document.begin()
    colors = []
    fragments = block.begin()
    while not fragments.atEnd():
        colors.append(fragments.fragment().charFormat().foreground().color().name().upper())
        fragments += 1
    assert colors == ["#E9E4D8", "#8A847A"] and document.toPlainText() == "我改过的字灰色说明"
    theme.apply("light")
    theme.retint(document)
    fragments = document.begin().begin()
    assert fragments.fragment().charFormat().foreground().color().name().upper() == "#1D2240"


def test_the_theme_shows_at_once_in_settings_and_goes_back_unless_saved(qt_app, monkeypatch):
    from saywell.ui import SettingsDialog

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    dialog = SettingsDialog(Settings())
    assert dialog.appearance.current() == 0
    dialog.appearance.set_current(1)
    dialog.appearance.changed.emit(1)
    assert theme.is_dark() and theme.appearance() == "dark"
    assert dialog.pending().appearance == "dark"
    dialog.reject()  # Not saved: back to light.
    assert not theme.is_dark() and theme.appearance() == "light"
    dispose(qt_app, dialog)

    dialog = SettingsDialog(Settings(appearance="dark"))
    assert dialog.appearance.current() == 1
    dialog.appearance.set_current(2)
    dialog.appearance.changed.emit(2)
    assert theme.appearance() == "system"
    dialog.reject()
    assert theme.appearance() == "dark"
    dispose(qt_app, dialog)


def test_selection_button_size_and_opacity(qt_app, monkeypatch):
    from saywell.translation_ui import ICON_SIZES, SelectionIcon
    from saywell.ui import SettingsDialog

    icon = SelectionIcon()
    assert icon.width() == ICON_SIZES["medium"] + 2 * icon.MARGIN
    icon.set_look("large", 40)
    assert icon.width() == 38 + 2 * icon.MARGIN and icon.opacity == pytest.approx(0.4)
    icon.set_look("small", 5)  # Never fainter than 30%.
    assert icon.width() == 24 + 2 * icon.MARGIN and icon.opacity == pytest.approx(0.3)
    theme.apply("dark")
    assert not icon.grab().isNull()  # The dark tile draws.
    dispose(qt_app, icon)

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    dialog = SettingsDialog(Settings(selection_icon_size="small", selection_icon_opacity=70), page="translate")
    assert dialog.icon_size.current() == 0 and dialog.icon_opacity.value() == 70
    assert dialog.icon_preview.size == 24 and dialog.icon_preview.opacity == pytest.approx(0.7)
    dialog.icon_size.set_current(2)
    dialog.icon_size.changed.emit(2)
    dialog.icon_opacity.setValue(45)
    assert dialog.icon_opacity_value.text() == "45%" and dialog.icon_preview.size == 38
    pending = dialog.pending()
    assert (pending.selection_icon_size, pending.selection_icon_opacity) == ("large", 45)
    # The button's group only matters when the button is how translation starts.
    assert not dialog.icon_group.isHidden()
    dialog.selection_trigger.set_current(1)
    dialog.selection_trigger.changed.emit(1)
    assert dialog.icon_group.isHidden()
    dispose(qt_app, dialog)
