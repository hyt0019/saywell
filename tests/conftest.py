import pytest


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    """Keep settings, statistics and logs written by tests out of the real user folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


@pytest.fixture(autouse=True, scope="module")
def no_windows_left_over():
    """Windows a test module leaves open are deleted when it ends: every one still alive is
    restyled again whenever a later module sets the style sheet or switches the theme."""
    yield
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()
