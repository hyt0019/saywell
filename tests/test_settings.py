import json
from pathlib import Path

from saywell import settings


def test_data_from_the_former_app_name_moves_over_with_its_saved_model_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "BetterVoiceInput"
    pinned = legacy / "models" / "x-asr-zh-en"
    pinned.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    saved = {"asr_model": "xasr", "model_dir": str(legacy / "models"), "download_dir": str(elsewhere)}
    saved["model_paths"] = {"xasr": str(pinned)}
    (legacy / "settings.json").write_text(json.dumps(saved), encoding="utf-8")
    loaded = settings.Settings.load()
    moved = tmp_path / "Saywell"
    assert not legacy.exists() and (moved / "settings.json").is_file()
    assert loaded.model_paths == {"xasr": str(moved / "models" / "x-asr-zh-en")}
    assert loaded.model_dir == str(moved / "models")
    assert loaded.download_dir == str(elsewhere)  # Paths outside the old folder stay as they are.


def test_old_data_folder_stays_in_use_while_it_cannot_be_moved(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "BetterVoiceInput"
    legacy.mkdir()

    def locked(*_):
        raise PermissionError("in use by an older copy")

    monkeypatch.setattr(Path, "rename", locked)
    assert settings.data_dir() == legacy
    assert not (tmp_path / "Saywell").exists()


def test_default_shortcut_is_two_keys_and_hold_to_talk_is_enabled():
    value = settings.Settings()
    assert value.hotkey == "Alt+X"
    assert value.hold_to_talk


def test_two_key_shortcut_survives_reload(tmp_path):
    value = settings.Settings(hotkey="Alt+C")
    path = tmp_path / "settings.json"
    value.save(path)
    assert settings.Settings.load(path) == value


def test_legacy_preview_preferences_do_not_disable_direct_input(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"auto_insert":false,"review_warnings":true}', encoding="utf-8")
    assert settings.Settings.load(path) == settings.Settings()


def test_packaged_build_finds_project_models_without_saved_config(tmp_path, monkeypatch):
    binary = tmp_path / "dist" / "Saywell" / "Saywell.exe"
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(settings.sys, "frozen", True, raising=False)
    monkeypatch.setattr(settings.sys, "executable", str(binary))
    assert settings.Settings().models == models


def test_models_beside_executable_take_precedence(tmp_path, monkeypatch):
    binary = tmp_path / "dist" / "Saywell" / "Saywell.exe"
    local = binary.parent / "models"
    local.mkdir(parents=True)
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(settings.sys, "frozen", True, raising=False)
    monkeypatch.setattr(settings.sys, "executable", str(binary))
    assert settings.Settings().models == local
