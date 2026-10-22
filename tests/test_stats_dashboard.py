import json
import os
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from saywell.core import CleanupResult
from saywell.settings import Settings
from saywell.stats import Day, StatsStore, count_chars, deepseek_cost

BEIJING = timezone(timedelta(hours=8))


@pytest.fixture(scope="module")
def qt_app():
    from saywell.ui import STYLE

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    yield app
    app.clipboard().clear()


def test_stats_keep_only_counts_and_survive_a_restart(tmp_path):
    path = tmp_path / "stats.json"
    store = StatsStore(path)
    when = datetime(2026, 10, 16, 20, 0)
    store.record_dictation("我想明天 十点开会。", 4.5, when)
    store.record_request({"prompt_tokens": 500, "completion_tokens": 40}, "https://example.com/v1", "m", when)
    store.save()
    text = path.read_text(encoding="utf-8")
    assert "开会" not in text  # Never the spoken text, only numbers.
    loaded = StatsStore(path).day(date(2026, 10, 16))
    assert loaded == Day(1, count_chars("我想明天 十点开会。"), 4.5, 1, 500, 40, 0.0)


def test_unreadable_or_foreign_stats_files_start_empty(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text("not json", encoding="utf-8")
    assert StatsStore(path).days == {}
    path.write_text(json.dumps({"days": {"bad-date": {"chars": 3}}}), encoding="utf-8")
    assert StatsStore(path).days == {}
    path.write_text(json.dumps({"days": {"2026-10-01": {"chars": 3, "text": "secret", "seconds": "x"}}}), encoding="utf-8")
    assert StatsStore(path).day(date(2026, 10, 1)) == Day(chars=3)


def test_deepseek_cost_follows_peak_and_off_peak_prices():
    usage = {"prompt_cache_hit_tokens": 1_000_000, "prompt_cache_miss_tokens": 1_000_000, "completion_tokens": 1_000_000}
    peak = datetime(2026, 10, 16, 10, 0, tzinfo=BEIJING)  # Friday morning
    evening = datetime(2026, 10, 16, 20, 0, tzinfo=BEIJING)
    weekend = datetime(2026, 10, 17, 10, 0, tzinfo=BEIJING)
    assert deepseek_cost("deepseek-flash", usage, peak) == pytest.approx(0.04 + 2 + 8)
    assert deepseek_cost("deepseek-flash", usage, evening) == pytest.approx((0.04 + 2 + 8) / 2)
    assert deepseek_cost("deepseek-flash", usage, weekend) == pytest.approx((0.04 + 2 + 8) / 2)
    assert deepseek_cost("unknown-model", usage, peak) is None
    # Without the cache split, every prompt token counts as a cache miss.
    assert deepseek_cost("deepseek-flash", {"prompt_tokens": 1_000_000}, peak) == pytest.approx(2)


def test_costs_are_only_estimated_for_deepseek(tmp_path):
    store = StatsStore(tmp_path / "s.json")
    when = datetime(2026, 10, 16, 10, 0, tzinfo=BEIJING)
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    store.record_request(usage, "https://api.deepseek.com", "deepseek-flash", when)
    store.record_request(usage, "https://api.example.com/v1", "deepseek-flash", when)
    store.record_request(usage, "not a url", "deepseek-flash", when)
    store.record_request({}, "https://api.deepseek.com", "deepseek-flash", when)
    day = store.day(when.date())
    assert day.requests == 3 and day.cost == pytest.approx(2)


def test_streaks_milestones_and_saved_time():
    store = StatsStore.empty()
    today = date(2026, 10, 17)
    for offset in (0, 1, 2, 5, 6, 7, 8):
        store.days[(today - timedelta(days=offset)).isoformat()] = Day(dictations=1, chars=2_000, seconds=60)
    assert store.streak(today) == 3
    assert store.streak(today + timedelta(days=1)) == 3  # Today not used yet keeps yesterday's streak.
    assert store.streak(today + timedelta(days=2)) == 0
    assert store.longest_streak() == 4 and store.active_days() == 7
    assert store.next_milestone() == (10_000, 50_000)
    assert store.best_day()[1] == 2_000
    assert store.saved_minutes() == pytest.approx(14_000 / 40 - 7)


def test_overview_shows_totals_heatmap_and_milestones(qt_app):
    from saywell.dashboard import Heatmap, Overview, number

    assert number(8_600) == "8,600" and number(86_000) == "8.6 万" and number(100_000) == "10 万"
    assert [Heatmap.level(n) for n in (0, 1, 299, 300, 1000, 3000)] == [0, 1, 1, 2, 3, 4]
    store = StatsStore.empty()
    today = date(2026, 10, 17)
    store.days[today.isoformat()] = Day(3, 1_200, 30, 3, 900, 100, 0.01)
    store.days[(today - timedelta(days=1)).isoformat()] = Day(1, 100, 5, 1, 300, 20, 0.002)
    view = Overview()
    view.resize(1000, 900)
    view.refresh(store, today, deepseek=True)
    assert "1,200" in view.today.value.text() and view.streak.value.text().count("2")
    assert view.requests.text() == "4 次" and view.cost.text() == "约 ¥0.01"
    chars, streak = view.milestones
    assert chars.reached() == ["1 千字"] and streak.reached() == []
    assert chars.position(1_000) == 0.25 and chars.position(5_500) == 0.25 + 0.25 / 2
    assert streak.position(0) == 0 and streak.position(500) == 1
    assert "1,200" in view.best.text()
    assert view.heatmap.day_at(*cell_center(view.heatmap, today)) == today
    view.refresh(store, today, deepseek=False)
    assert view.cost.text() == "—"
    view.set_balance(None, "Example")
    assert view.balance.text() == "—" and view.refresh_button.isHidden()


def cell_center(heatmap, day):
    step, offset = heatmap.geometry_for()
    week = (day - heatmap.start).days // 7
    return offset + week * step + step / 2, heatmap.TOP + day.weekday() * step + step / 2


def test_completed_dictation_updates_statistics_and_latest_card(qt_app, monkeypatch):
    from saywell.app import MainWindow

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    window = MainWindow(Settings(), native=False)
    try:
        assert window.pages.currentIndex() == 0
        job, _ = window.begin("busy")
        result = CleanupResult("嗯我想明天开会", "我想明天开会。", usage={"prompt_tokens": 10, "completion_tokens": 5})
        window.on_complete(job, {"result": result, "asr_seconds": 0.5, "duration": 3.0})
        day = window.usage.day(date.today())
        assert (day.dictations, day.chars, day.requests, day.prompt_tokens) == (1, 7, 1, 10)
        assert window.overview.latest_text.text() == "我想明天开会。"
        assert StatsStore().day(date.today()) == day  # Saved under the (isolated) app data folder.
        window.overview.open_text.emit()
        assert window.pages.currentIndex() == 1 and window.page_buttons[1].isChecked()
    finally:
        window.shutdown()
        window.close()


def test_statistics_can_be_turned_off(qt_app, monkeypatch):
    from saywell.app import MainWindow

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    window = MainWindow(Settings(collect_stats=False), native=False)
    try:
        job, _ = window.begin("busy")
        window.on_complete(job, {"result": CleanupResult("原文", "结果"), "asr_seconds": 0.5, "duration": 3.0})
        assert window.usage is None and StatsStore().days == {}
        assert "已关闭" in window.overview.heat_summary.text()
    finally:
        window.shutdown()
        window.close()


def test_translate_button_replaces_the_result_and_can_be_undone(qt_app, monkeypatch):
    import time

    from saywell.app import MainWindow
    from saywell.cleanup import ApiCleaner

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    monkeypatch.setattr("saywell.app.read_key", lambda *args, **kwargs: "key")
    calls = []

    def translate(self, text, language, cancel=None):
        calls.append((text, language))
        return CleanupResult(text, "The meeting is at 10 tomorrow.", usage={"prompt_tokens": 40}, translated_to=language)

    monkeypatch.setattr(ApiCleaner, "translate", translate)
    window = MainWindow(Settings(), native=False)
    try:
        window.result.setPlainText("明天十点开会。")
        window.fill_translate_menu()
        languages = [action.text() for action in window.translate_menu.actions()[1:]]
        assert languages[0] == "英语" and "日语" in languages
        window.translate_result("英语")
        assert window.translating and not window.translate_button.isEnabled()
        deadline = time.monotonic() + 5
        while window.translating and time.monotonic() < deadline:
            qt_app.processEvents()
        assert calls == [("明天十点开会。", "英语")]
        assert window.result.toPlainText() == "The meeting is at 10 tomorrow."
        assert window.usage.day(date.today()).requests == 1
        window.result.undo()
        assert window.result.toPlainText() == "明天十点开会。"
        window.translate_result("日语")
        while window.translating and time.monotonic() < deadline + 5:
            qt_app.processEvents()
        assert Settings.load().translate_language == "日语"  # Remembered for next time.
    finally:
        window.shutdown()
        window.close()


def test_gpu_fallback_is_reported_once_with_the_result(qt_app, monkeypatch):
    from saywell.app import MainWindow

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    window = MainWindow(Settings(asr_model="qwen3-asr", gpu_acceleration=True), native=False)
    try:
        job, _ = window.begin("busy")
        result = CleanupResult("你好", "你好。")
        window.on_complete(
            job, {"result": result, "asr_seconds": 0.5, "duration": 1.0, "device": "", "gpu_problem": "显卡驱动不支持或显存不足"}
        )
        assert "已改用 CPU" in window.notice.text() and window.device == "CPU"
    finally:
        window.shutdown()
        window.close()