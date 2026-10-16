import json
import time

from saywell.history import HistoryStore
from saywell.settings import Settings


def test_history_encrypts_roundtrips_and_clears(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.append("私密测试原文", "私密测试结果")
    raw = store.path.read_text(encoding="utf8")
    assert "私密测试" not in raw
    rows = store.read()
    assert rows[0]["original"] == "私密测试原文"
    assert rows[0]["text"] == "私密测试结果"
    store.clear()
    assert not store.path.exists()


def test_expired_history_is_removed(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.append("一段话", "结果")
    rows = json.loads(store.path.read_text())
    rows[0]["created"] = time.time() - 8 * 86400
    store.path.write_text(json.dumps(rows))
    assert store.read() == []
    assert json.loads(store.path.read_text()) == []


def test_corrupt_history_is_ignored(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    store.path.write_text("not json")
    assert store.read() == []


def test_wrong_setting_types_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"auto_insert": "false", "model_dir": 12, "hotkey": "invalid", "glossary": [3]})
    )
    assert Settings.load(path) == Settings()


def test_nonobject_settings_do_not_crash(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[]")
    assert Settings.load(path) == Settings()


def test_history_keeps_only_latest_five_on_disk_and_in_display(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    for index in range(8):
        store.append(f"原文{index}", f"结果{index}")
    assert [row["text"] for row in store.read()] == [f"结果{i}" for i in range(7, 2, -1)]
    assert len(json.loads(store.path.read_text(encoding="utf-8"))) == 5


def test_prune_migrates_old_history_without_decrypting_or_adding_entries(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    rows = [{"created": time.time() - 200 + i, "data": f"encrypted-{i}"} for i in range(100)]
    store.path.write_text(json.dumps(rows), encoding="utf-8")
    store.path.with_suffix(".tmp").write_text("old temporary history", encoding="utf-8")
    store.prune()
    assert json.loads(store.path.read_text(encoding="utf-8")) == rows[-5:]
    assert not store.path.with_suffix(".tmp").exists()


def test_prune_and_clear_remove_leftover_temporary_history(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    temp = store.path.with_suffix(".tmp")
    temp.write_text("leftover", encoding="utf-8")
    store.prune()
    assert not store.path.exists()
    assert not temp.exists()
    temp.write_text("leftover", encoding="utf-8")
    store.clear()
    assert not temp.exists()
