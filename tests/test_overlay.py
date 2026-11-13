import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from saywell.overlay import BOTTOM, OverlayDemo, OverlayPreview, RecordingOverlay, clamp_anchor
from saywell.settings import Settings
from saywell.ui import STYLE


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    yield app


def dispose(app, *widgets):
    """Delete the widgets now: left to the garbage collector, an unparented widget can be deleted
    in the middle of building another one, which crashes Qt."""
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()


def settle(app, seconds=0.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


def test_overlay_settings_are_checked_when_read(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"overlay_style": "minimal", "overlay_opacity": 35, "overlay_position": [800, 900]}))
    loaded = Settings.load(path)
    assert (loaded.overlay_style, loaded.overlay_opacity, loaded.overlay_position) == ("minimal", 35, [800, 900])
    path.write_text(json.dumps({"overlay_style": "huge", "overlay_opacity": 140, "overlay_position": [1, "2"]}))
    loaded = Settings.load(path)
    assert (loaded.overlay_style, loaded.overlay_opacity, loaded.overlay_position) == ("standard", 100, [])


def test_the_overlay_cannot_take_the_focus_but_can_be_dragged(qt_app):
    overlay = RecordingOverlay()
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    dispose(qt_app, overlay)


def test_the_minimal_overlay_shrinks_to_a_spinner_and_opens_only_for_a_message(qt_app):
    overlay = RecordingOverlay()
    overlay.set_look("minimal", 0.4)
    assert overlay.words.isHidden() and (overlay.width(), overlay.height()) == (440, 84)
    overlay.listen("松开快捷键结束，Esc 取消")
    overlay.set_level(80)
    assert overlay.trail.timer.isActive() and not overlay.level.timer.isActive()
    assert (overlay.kind, overlay.box_to.width()) == ("rec", 112)
    overlay.working("正在识别和整理…", "Esc 取消本次处理")
    assert (overlay.kind, overlay.box_to.width()) == ("busy", 36)
    overlay.show_message("已输入", "可以继续按快捷键说下一段", 1200, brief=True)
    assert overlay.kind == "done" and overlay.box_to.width() == 36
    overlay.show_message("未能自动输入，结果已复制", "打开输入框后按 Ctrl+V 粘贴", tone="warn")
    assert overlay.kind == "message" and 150 < overlay.box_to.width() <= overlay.width() - 24
    settle(qt_app)
    assert not overlay.grab().isNull()
    overlay.set_look("standard", 0.4)
    assert not overlay.words.isHidden() and (overlay.width(), overlay.height()) == (380, 104)
    dispose(qt_app, overlay)


def test_the_overlay_shows_where_it_was_dragged_and_stays_on_a_screen(qt_app):
    overlay = RecordingOverlay()
    area = overlay.screen().availableGeometry()
    overlay.listen("x")
    default = overlay.anchor()
    assert default == QPoint(area.center().x(), area.bottom() - 28 - BOTTOM)
    moved = []
    overlay.moved.connect(moved.append)
    start = QPointF(overlay.mapToGlobal(QPoint(overlay.width() // 2, overlay.height() - BOTTOM - 18)))

    def mouse(kind, point):
        local = QPointF(overlay.mapFromGlobal(point.toPoint()))
        return QMouseEvent(kind, local, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    overlay.mousePressEvent(mouse(QMouseEvent.Type.MouseButtonPress, start))
    target = start + QPointF(-120, -200)
    overlay.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove, target))
    overlay.mouseReleaseEvent(mouse(QMouseEvent.Type.MouseButtonRelease, target))
    assert moved == [default + QPoint(-120, -200)] and overlay.position == [default.x() - 120, default.y() - 200]
    overlay.dismiss()
    overlay.listen("x")
    assert overlay.anchor() == moved[0]  # Shows there next time.
    overlay.position = [area.right() + 5000, 10]  # A screen that is gone.
    overlay.place()
    assert overlay.anchor() == default
    assert clamp_anchor(QPoint(area.left() - 50, area.bottom() + 50), area) == QPoint(area.left() + 70, area.bottom() - 4)
    dispose(qt_app, overlay)


def test_a_click_without_a_drag_keeps_the_default_place(qt_app):
    overlay = RecordingOverlay()
    overlay.listen("x")
    moved = []
    overlay.moved.connect(moved.append)
    point = QPointF(overlay.mapToGlobal(QPoint(overlay.width() // 2, overlay.height() - BOTTOM - 18)))
    local = QPointF(overlay.mapFromGlobal(point.toPoint()))
    for kind in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        event = QMouseEvent(kind, local, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        (overlay.mousePressEvent if kind == QMouseEvent.Type.MouseButtonPress else overlay.mouseReleaseEvent)(event)
    assert not moved and overlay.position == []
    dispose(qt_app, overlay)


def test_the_preview_plays_dictation_and_stops_when_hidden(qt_app):
    preview = OverlayPreview()
    preview.resize(560, preview.height())
    preview.set_look("minimal", 0.0)
    preview.show()
    settle(qt_app, 0.3)
    assert preview.demo.timer.isActive() and preview.overlay.isVisible()
    assert preview.overlay.anchor() == QPoint(280, preview.height() - 20)
    preview.set_dark(True)
    assert not preview.grab().isNull()
    preview.hide()
    assert not preview.demo.timer.isActive()
    dispose(qt_app, preview)


def test_placing_keeps_the_overlay_in_sight(qt_app):
    overlay = RecordingOverlay()
    demo = OverlayDemo(overlay, vanish=False)
    demo.start()
    for _ in demo.steps:
        demo._next()
        assert overlay.isVisible()
    assert all(step != "gap" for step, _ in demo.steps)
    demo.stop()
    assert not overlay.isVisible()
    dispose(qt_app, overlay)


def test_settings_choose_the_overlay_look_and_place(qt_app, monkeypatch):
    from saywell.ui import SettingsDialog

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    dialog = SettingsDialog(Settings(overlay_position=[500, 600]), page="overlay")
    assert dialog.overlay_style.current() == 0 and dialog.overlay_opacity.value() == 100
    dialog.overlay_style.set_current(1)
    dialog.overlay_style.changed.emit(1)
    dialog.overlay_opacity.setValue(30)
    assert dialog.overlay_preview.overlay.look == "minimal" and dialog.overlay_preview.overlay.opacity == 0.3
    assert dialog.overlay_opacity_value.text() == "30%"
    assert not dialog.reset_overlay.isHidden()
    dialog.reset_overlay.click()
    assert dialog.overlay_position == [] and dialog.reset_overlay.isHidden()
    dialog.place_overlay.click()
    placing = dialog.placing
    assert placing is not None and placing.isVisible() and placing.look == "minimal"
    assert dialog.place_overlay.text() == "完成"
    placing.position = [640, 700]  # As if dragged there.
    dialog.place_overlay.click()
    assert dialog.placing is None and dialog.overlay_position == [640, 700]
    dialog.save()
    saved = dialog.updated
    assert (saved.overlay_style, saved.overlay_opacity, saved.overlay_position) == ("minimal", 30, [640, 700])
    dispose(qt_app, dialog)


def test_closing_settings_while_placing_puts_the_overlay_away(qt_app, monkeypatch):
    from saywell.ui import SettingsDialog

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    dialog = SettingsDialog(Settings(), page="overlay")
    dialog.place_overlay.click()
    dialog.placing.position = [300, 400]
    dialog.save()  # Saved without 完成: where it stands counts.
    assert dialog.placing is None and dialog.updated.overlay_position == [300, 400]
    dispose(qt_app, dialog)
