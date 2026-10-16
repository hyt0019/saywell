import pytest


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    """Keep settings, statistics and logs written by tests out of the real user folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
