import json
import os
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from saywell.achievements import ACHIEVEMENTS, BY_ID, GROUPS, Achievements
from saywell.settings import Settings
from saywell.stats import Day, StatsStore


def stats_with(days: dict[date, Day]) -> StatsStore:
    store = StatsStore.empty()
    store.days = {day.isoformat(): value for day, value in days.items()}
    return store


def reached(tally: Achievements, stats: StatsStore | None = None, settings: Settings | None = None) -> set[str]:
    tally.check(stats or StatsStore.empty(), settings or Settings(glossary=[]))
    return set(tally.reached)


def test_every_achievement_is_well_formed_and_measurable(tmp_path):
    assert len(BY_ID) == len(ACHIEVEMENTS) >= 50
    facts = Achievements(tmp_path / "a.json").facts(StatsStore.empty(), Settings())
    for achievement in ACHIEVEMENTS:
        assert achievement.group in GROUPS
        assert achievement.measure in facts, achievement.id
        assert achievement.name and achievement.condition and achievement.comment.endswith("。")
        assert achievement.hidden == (achievement.group == "隐藏") == bool(achievement.hint)
    assert sum(a.hidden for a in ACHIEVEMENTS) == 10


def test_characters_streaks_and_days_come_from_the_statistics(tmp_path):
    start = date(2026, 10, 1)
    days = {start + timedelta(days=offset): Day(dictations=2, chars=1_500, seconds=60) for offset in range(7)}
    tally = Achievements(tmp_path / "a.json")
    got = reached(tally, stats_with(days))
    assert {"words-100", "words-1k", "words-10k", "streak-3", "streak-7", "saved-1h"} <= got
    assert not {"words-50k", "streak-15", "day-3k", "count-100"} & got
    assert tally.check(stats_with(days), Settings(glossary=[])) == []  # Each is reached once.


def test_moments_in_the_statistics_unlock_hidden_ones(tmp_path):
    days = {
        date(2025, 1, 1): Day(dictations=1, chars=10),
        date(2025, 3, 1): Day(dictations=1, chars=520),  # 59 days later: away for a while.
        date(2026, 10, 3): Day(dictations=1, chars=5),  # Saturday...
        date(2026, 10, 4): Day(dictations=1, chars=5),  # ...and Sunday.
    }
    got = reached(Achievements(tmp_path / "a.json"), stats_with(days))
    assert {"new-year", "day-520", "welcome-back", "weekend", "anniversary"} <= got
    plain = {date(2026, 10, 5): Day(dictations=1, chars=519), date(2026, 10, 6): Day(dictations=1, chars=10)}
    assert not {"new-year", "day-520", "welcome-back", "weekend", "anniversary"} & reached(
        Achievements(tmp_path / "b.json"), stats_with(plain))


def test_dictations_at_night_at_dawn_in_a_rush_and_at_full_length(tmp_path):
    tally = Achievements(tmp_path / "a.json")
    tally.dictated(3, datetime(2026, 10, 7, 13, 0))
    assert not {"night", "dawn", "rapid", "breath"} & reached(tally)
    tally.dictated(3, datetime(2026, 10, 7, 1, 30))
    tally.dictated(3, datetime(2026, 10, 8, 5, 40))
    tally.dictated(119, datetime(2026, 10, 8, 9, 0), translated=True, device="NVIDIA GeForce RTX 4060")
    assert {"night", "dawn", "breath", "voice-1", "gpu-1"} <= reached(tally)
    assert "rapid" not in tally.reached
    for second in range(0, 50, 10):
        tally.dictated(2, datetime(2026, 10, 8, 10, 0, second))
    assert "rapid" in reached(tally)


def test_a_dictation_on_the_cpu_is_not_the_graphics_card(tmp_path):
    tally = Achievements(tmp_path / "a.json")
    tally.dictated(3, datetime(2026, 10, 7, 13, 0), device="CPU")
    assert "gpu-1" not in reached(tally)


def test_meetings_translations_and_settings(tmp_path):
    tally = Achievements(tmp_path / "a.json")
    when = datetime(2026, 10, 7, 15, 0)
    tally.meeting_ended(2.5 * 3600, when)
    for template in ("general", "project", "client", "decision"):
        tally.minutes_written(template)
    for language, style in (("英语", "natural"), ("日语", "formal"), ("简体中文", "literal")):
        tally.translated(language, style, when)
    tally.translated("英语", "natural", when, selection=False, chars=12_000)
    settings = Settings(glossary=[f"词{n}" for n in range(20)], lexicons=[{"id": "x", "name": "医学"}])
    got = reached(tally, settings=settings)
    assert {"meeting-1", "minutes-1", "meeting-2h", "select-1", "styles-3", "languages-3", "page-10k",
            "glossary-20", "lexicon-1"} <= got
    assert not {"templates-5", "meeting-10h", "select-100", "all-round"} & got
    tally.minutes_written("lecture")
    tally.dictated(3, when)
    assert {"templates-5", "all-round"} <= reached(tally, settings=settings)


def test_the_tally_keeps_only_counts_and_names_and_survives_a_restart(tmp_path):
    path = tmp_path / "a.json"
    tally = Achievements(path)
    assert tally.fresh
    tally.add("cancels", 10)
    tally.translated("英语", "natural", datetime(2026, 10, 7, 15, 0))
    tally.check(StatsStore.empty(), Settings(glossary=[]), datetime(2026, 10, 7, 15, 0))
    again = Achievements(path)
    assert not again.fresh
    assert again.reached == {"cancels": "2026-10-07T15:00", "select-1": "2026-10-07T15:00"}
    assert again.sets == {"languages": {"英语"}, "styles": {"natural"}}
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"version", "counts", "sets", "used", "reached"}
    again.clear()
    cleared = Achievements(path)
    assert not cleared.fresh and cleared.reached == {} and cleared.counts == {}


def test_unreadable_or_foreign_files_start_over(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("not json", encoding="utf-8")
    assert Achievements(path).fresh
    path.write_text(json.dumps({"counts": {"cancels": "x", "words": 5, "imports": 2}, "reached": {"nope": "x"},
                                "sets": {"styles": "natural"}}), encoding="utf-8")
    tally = Achievements(path)
    assert tally.counts == {"imports": 2.0} and tally.reached == {} and tally.sets == {}


def test_the_next_one_is_the_closest_visible_one_and_the_latest_come_first(tmp_path):
    tally = Achievements(tmp_path / "a.json")
    stats = stats_with({date(2026, 10, 1): Day(dictations=1, chars=8_000)})
    tally.check(stats, Settings(glossary=[]), datetime(2026, 10, 1, 9, 0))
    facts = tally.facts(stats, Settings(glossary=[]))
    upcoming = tally.upcoming(facts)
    assert upcoming.id == "words-10k" and tally.progress(upcoming, facts) == (8_000, 10_000)
    tally.add("imports")
    tally.check(stats, Settings(glossary=[]), datetime(2026, 10, 2, 9, 0))
    assert [a.id for a in tally.latest(3)] == ["import-1", "saved-1h", "day-3k"]


def test_meetings_from_before_are_counted_once(tmp_path):
    tally = Achievements(tmp_path / "a.json")
    tally.count_meetings([({"duration": 7300.0, "characters": 9000}, "general"),
                          ({"duration": 600.0, "characters": 0}, ""),  # Nothing was said.
                          ({"characters": 50}, "")])  # Never finished.
    assert tally.counts["meetings"] == 1 and tally.sets == {"templates": {"general"}}
    assert {"meeting-1", "minutes-1", "meeting-2h"} <= reached(tally)


# ---------- in the app ----------


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    from saywell.ui import STYLE

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    yield app


def test_hover_text_keeps_hidden_ones_secret_and_says_what_is_missing(tmp_path):
    from saywell.achievements_ui import amount, describe

    tally = Achievements(tmp_path / "a.json")
    tally.reached["words-100"] = "2026-10-07T15:00"
    facts = {"chars": 7_700, "saved_hours": 0.6}
    assert describe(BY_ID["words-100"], tally, facts) == ("开口", "累计说满 100 字 · 10月7日", BY_ID["words-100"].comment)
    assert describe(BY_ID["words-10k"], tally, facts)[2] == "还差 2,300 字"
    assert describe(BY_ID["night"], tally, facts) == ("？？？", "隐藏成就", "夜深人静的时候")
    assert describe(BY_ID["import-1"], tally, facts)[2] == "还没达成"
    assert amount(27_000, "字") == "2.7 万字" and amount(0.4, "小时") == "24 分钟" and amount(2, "天") == "2 天"


def test_a_dictation_reaches_one_and_the_card_says_so(qt_app, monkeypatch):
    from saywell.app import MainWindow
    from saywell.core import CleanupResult

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    window = MainWindow(Settings(), native=False)
    try:
        job, _ = window.begin("busy")
        result = CleanupResult("一" * 120, "一" * 120)
        window.on_complete(job, {"result": result, "asr_seconds": 0.5, "duration": 3.0, "kind": "audio"})
        assert set(window.achievements.reached) == {"words-100"}
        assert window.badge_toast.current[1] == "开口" and window.badge_toast.isVisible()
        assert window.overview.achievements.count.text() == "1 / 54"
        job, _ = window.begin("busy")
        window.on_complete(job, {"result": CleanupResult("好", "好"), "asr_seconds": 0.5, "duration": 3.0, "kind": "file"})
        assert window.achievements.counts["imports"] == 1 and "import-1" in window.achievements.reached
        assert [item[1] for item in window.badge_toast.queue] == ["旧声新文"]  # After the first is gone.
        window.begin("busy")
        window.cancel()
        assert window.achievements.counts["cancels"] == 1
    finally:
        window.shutdown()
        window.close()


def test_the_card_waits_while_recording(qt_app):
    from saywell.achievements_ui import AchievementToast

    toast = AchievementToast()
    busy = [True]
    toast.ready = lambda: not busy[0]
    toast.push(BY_ID["words-1k"])
    assert not toast.isVisible() and len(toast.queue) == 1
    busy[0] = False
    toast._next()
    assert toast.isVisible() and toast.current[1] == "千言" and not toast.queue
    toast.hide()
    toast.push_summary([BY_ID["words-100"], BY_ID["streak-3"]])
    assert toast.current[1] == "点亮了 2 个成就"
    toast.close()


def test_clearing_the_statistics_clears_achievements_and_turning_them_off_stops_them(qt_app, monkeypatch):
    from types import SimpleNamespace

    from saywell.app import MainWindow

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    window = MainWindow(Settings(), native=False)
    try:
        window.achieve(lambda tally: tally.add("imports"))
        assert "import-1" in window.achievements.reached
        window.apply_settings_dialog(SimpleNamespace(stats_cleared=True), accepted=False)
        assert window.achievements.reached == {} and Achievements().reached == {}
        window.achievements = window.usage = None
        window.achieve(lambda tally: tally.add("imports"))  # Off: nothing is counted.
        window.refresh_overview()
        assert window.overview.achievements.off.isVisibleTo(window.overview)
    finally:
        window.shutdown()
        window.close()


class Wheel:
    """Just what Reel.roll reads of a wheel event."""

    def __init__(self, delta: int):
        self.delta = delta

    def angleDelta(self):
        from PySide6.QtCore import QPoint

        return QPoint(0, self.delta)


def test_the_wheel_glides_the_folded_line_through_every_group_and_then_lets_the_page_scroll(qt_app, tmp_path):
    from saywell.achievements_ui import Reel
    from saywell.dashboard import HoverCard

    tally = Achievements(tmp_path / "a.json")
    tally.add("imports")
    tally.check(StatsStore.empty(), Settings(glossary=[]))
    reel = Reel(HoverCard())
    reel.show()  # So that resizing it reaches it at once.
    reel.resize(1000, reel.ROW)
    reel.set_items(tally.latest(12), tally, tally.facts(StatsStore.empty(), Settings(glossary=[])))
    assert list(dict.fromkeys(group for group, _ in reel.lines)) == ["最近", *GROUPS]
    assert sum(len(items) for _, items in reel.lines[1:]) == len(ACHIEVEMENTS)
    assert not reel.roll(Wheel(120))  # Already at the top: the page scrolls.
    assert reel.roll(Wheel(-120)) and reel.line == 1 and reel.offset.running()
    assert reel.roll(Wheel(-60)) and reel.line == 1  # Half a notch waits for the other half.
    assert reel.roll(Wheel(-60)) and reel.line == 2
    reel.offset.to(float(reel.line), animate=False)
    for _ in range(len(reel.lines)):
        reel.roll(Wheel(-120))
    assert reel.line == len(reel.lines) - 1 and not reel.roll(Wheel(-120))  # At the end: the page scrolls.
    reel.resize(500, reel.ROW)  # Fewer to a line: more lines, and still somewhere among them.
    assert max(len(items) for _, items in reel.lines) <= reel.columns() and reel.line < len(reel.lines)
    lengths = [len(items) for group, items in reel.lines if group == "字数"]
    assert lengths == [4, 3]  # Shared out evenly, not 4 columns and then 3 left over by chance.
    reel.close()


def test_the_card_glides_in_draws_its_ring_and_fades_out_slowly(qt_app):
    import time

    from PySide6.QtWidgets import QApplication

    from saywell.achievements_ui import AchievementToast

    def settle(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    toast = AchievementToast()
    toast.push(BY_ID["words-1k"])
    assert toast.isVisible() and toast.presence.value < 0.5 and toast.ring.value == 0
    settle(1.3)
    assert toast.presence.value == 1 and toast.ring.value == 1 and toast.pop.value == 1
    toast.vanish()
    settle(0.3)
    assert toast.isVisible() and 0 < toast.presence.value < 1 and toast.drift == toast.DRIFT
    toast.enterEvent(None)  # The pointer over it: it comes back and stays.
    assert not toast.leaving
    settle(0.5)
    assert toast.presence.value == 1 and not toast.timer.isActive()
    toast.vanish(200)
    settle(0.5)
    assert not toast.isVisible()
    toast.close()


def test_the_line_takes_the_wheel_only_once_the_pointer_moved_over_it(qt_app):
    from PySide6.QtCore import QPoint

    from saywell.achievements_ui import Reel
    from saywell.dashboard import HoverCard

    class Event:
        def __init__(self, delta):
            self.delta, self.accepted = delta, None

        def angleDelta(self):
            return QPoint(0, self.delta)

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    reel = Reel(HoverCard())
    reel.lines = [("最近", []), ("字数", []), ("坚持", [])]
    wheel = Event(-120)
    reel.notice(QPoint(10, 10))  # The page scrolled the line under a still pointer.
    reel.notice(QPoint(10, 10))
    reel.wheelEvent(wheel)
    assert wheel.accepted is False and reel.line == 0  # The page goes on scrolling.
    reel.notice(QPoint(14, 11))  # Moved over it.
    wheel = Event(-120)
    reel.wheelEvent(wheel)
    assert wheel.accepted and reel.line == 1
    reel.disarm()  # The page scrolled again.
    wheel = Event(-120)
    reel.wheelEvent(wheel)
    assert wheel.accepted is False and reel.line == 1
