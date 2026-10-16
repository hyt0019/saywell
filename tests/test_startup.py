import json
from types import SimpleNamespace

import pytest

from saywell import startup
from saywell.settings import Settings


@pytest.fixture
def registry(monkeypatch):
    values = {"UnrelatedApp": ("other.exe", startup.winreg.REG_SZ)}
    real = startup.winreg

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def open_key(root, path, reserved, access):
        assert root == real.HKEY_CURRENT_USER and path == startup.RUN_KEY
        return Key()

    def query_value(key, name):
        if name not in values:
            raise FileNotFoundError
        return values[name]

    def delete_value(key, name):
        if name not in values:
            raise FileNotFoundError
        del values[name]

    fake = SimpleNamespace(
        HKEY_CURRENT_USER=real.HKEY_CURRENT_USER,
        KEY_QUERY_VALUE=real.KEY_QUERY_VALUE,
        KEY_SET_VALUE=real.KEY_SET_VALUE,
        REG_SZ=real.REG_SZ,
        REG_EXPAND_SZ=real.REG_EXPAND_SZ,
        OpenKey=open_key,
        CreateKeyEx=open_key,
        QueryValueEx=query_value,
        DeleteValue=delete_value,
        SetValueEx=lambda key, name, reserved, kind, value: values.update({name: (value, kind)}),
    )
    monkeypatch.setattr(startup, "winreg", fake)
    return values


def test_enable_disable_only_changes_app_entry_and_saves_preference(tmp_path, registry):
    path = tmp_path / "settings.json"
    value = Settings(start_on_login=True)
    startup.save_settings_with_startup(value, path)
    assert startup.startup_enabled()
    assert registry[startup.VALUE_NAME] == (startup.startup_command(), startup.winreg.REG_SZ)
    assert Settings.load(path).start_on_login
    value.start_on_login = False
    startup.save_settings_with_startup(value, path)
    startup.save_settings_with_startup(value, path)  # Disabling twice is harmless.
    assert not startup.startup_enabled()
    assert not Settings.load(path).start_on_login
    assert registry == {"UnrelatedApp": ("other.exe", startup.winreg.REG_SZ)}


def test_failed_settings_save_restores_previous_entry_exactly(tmp_path, registry, monkeypatch):
    previous = (r"%LOCALAPPDATA%\Previous Copy\app.exe", startup.winreg.REG_EXPAND_SZ)
    registry[startup.VALUE_NAME] = previous

    def fail_save(*_):
        raise PermissionError("settings unavailable")

    monkeypatch.setattr(Settings, "save", fail_save)
    with pytest.raises(PermissionError, match="settings unavailable"):
        startup.save_settings_with_startup(Settings(start_on_login=False), tmp_path / "settings.json")
    assert registry[startup.VALUE_NAME] == previous


def test_registry_failure_does_not_save_enabled_preference(tmp_path, registry, monkeypatch):
    def deny_write(*_):
        raise PermissionError("denied")

    monkeypatch.setattr(startup.winreg, "SetValueEx", deny_write)
    path = tmp_path / "settings.json"
    with pytest.raises(startup.StartupError, match="无法修改"):
        startup.save_settings_with_startup(Settings(start_on_login=True), path)
    assert not path.exists()
    assert not startup.startup_enabled()


def test_failed_first_save_removes_new_startup_entry(tmp_path, registry, monkeypatch):
    def fail_save(*_):
        raise PermissionError("settings unavailable")

    monkeypatch.setattr(Settings, "save", fail_save)
    with pytest.raises(PermissionError):
        startup.save_settings_with_startup(Settings(start_on_login=True), tmp_path / "settings.json")
    assert not startup.startup_enabled()


def test_entry_from_the_former_app_name_is_replaced_by_one_for_this_copy(registry):
    registry[startup.LEGACY_VALUE_NAME] = (r"C:\Programs\BetterVoiceInput\BetterVoiceInput.exe --background", startup.winreg.REG_SZ)
    startup.migrate_legacy_entry()
    assert startup.LEGACY_VALUE_NAME not in registry
    assert registry[startup.VALUE_NAME] == (startup.startup_command(), startup.winreg.REG_SZ)
    startup.migrate_legacy_entry()  # Nothing left to move.
    assert set(registry) == {"UnrelatedApp", startup.VALUE_NAME}


def test_packaged_command_quotes_path_and_starts_in_background(monkeypatch):
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", r"D:\我的程序\Voice Input\Saywell.exe")
    assert startup.startup_command() == '"D:\\我的程序\\Voice Input\\Saywell.exe" --background'


def test_source_command_uses_pythonw_and_absolute_launcher(tmp_path, monkeypatch):
    root = tmp_path / "Voice Input"
    python = root / ".venv" / "Scripts" / "python.exe"
    pythonw = python.with_name("pythonw.exe")
    pythonw.parent.mkdir(parents=True)
    pythonw.touch()
    monkeypatch.setattr(startup.sys, "frozen", False, raising=False)
    monkeypatch.setattr(startup.sys, "executable", str(python))
    monkeypatch.setattr(startup, "project_root", lambda: root)
    command = startup.startup_command()
    assert command == f'"{pythonw}" "{root / "scripts" / "run_app.py"}" --background'


def test_old_or_invalid_preferences_keep_startup_off(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    assert not Settings.load(path).start_on_login
    path.write_text(json.dumps({"start_on_login": "true"}), encoding="utf-8")
    assert not Settings.load(path).start_on_login
