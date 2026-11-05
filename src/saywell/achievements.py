"""Achievements for using the app, judged from counts and times only, never from any text.

What the usage statistics already keep (characters and dictations per day) covers most of
them. A small tally beside them, achievements.json, keeps the rest: how often a feature was
used, which minutes templates, translation styles and target languages, and a few moments (a
dictation after midnight). Like the statistics it is only kept while 记录使用统计 is on, and
清空统计 clears it too."""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .audio import MAX_SECONDS
from .settings import Settings, data_dir
from .stats import StatsStore

GROUPS = ("字数", "坚持", "说话", "省时", "会议", "翻译", "探索", "隐藏")


@dataclass(frozen=True)
class Achievement:
    """One achievement: reached once `measure` is at least `goal`. A hidden one shows only
    `hint` until it is reached."""

    id: str
    name: str
    group: str
    condition: str
    comment: str
    measure: str
    goal: float = 1
    unit: str = ""  # For "还差 …": 字, 天, 次, 小时.
    hidden: bool = False
    hint: str = ""


def _a(id, name, group, condition, comment, measure, goal=1, unit="", hint=""):
    return Achievement(id, name, group, condition, comment, measure, goal, unit, bool(hint), hint)


ACHIEVEMENTS = (
    _a("words-100", "开口", "字数", "累计说满 100 字", "一百个字，差不多是一条长微信。", "chars", 100, "字"),
    _a("words-1k", "千言", "字数", "累计说满 1,000 字", "一千字，要是打字得敲二十多分钟。", "chars", 1_000, "字"),
    _a("words-10k", "万语", "字数", "累计说满 1 万字", "一万字，要是打字得敲四个多小时。", "chars", 10_000, "字"),
    _a("words-50k", "洋洋洒洒", "字数", "累计说满 5 万字", "五万字，已经是一部中篇小说的篇幅。", "chars", 50_000, "字"),
    _a("words-100k", "积沙成塔", "字数", "累计说满 10 万字", "十万字，打字的话要连着敲四十多个小时。", "chars", 100_000, "字"),
    _a("words-500k", "口若悬河", "字数", "累计说满 50 万字", "五十万字，打字得两百多个小时，你只是说了出来。", "chars", 500_000, "字"),
    _a("words-1m", "著作等身", "字数", "累计说满 100 万字", "一百万字。说出来的话，也能著作等身。", "chars", 1_000_000, "字"),
    _a("streak-3", "三日不辍", "坚持", "连续 3 天使用", "连着三天都来了，习惯正在养成。", "streak", 3, "天"),
    _a("streak-7", "一周如一", "坚持", "连续 7 天使用", "整整一周，一天没落下。", "streak", 7, "天"),
    _a("streak-15", "半月有余", "坚持", "连续 15 天使用", "半个月天天开口，手指轻松多了。", "streak", 15, "天"),
    _a("streak-30", "习惯成自然", "坚持", "连续 30 天使用", "一个月了，说话输入已经是你的日常。", "streak", 30, "天"),
    _a("streak-100", "百日之功", "坚持", "连续 100 天使用", "一百天没断过，比大多数新年计划坚持得久。", "streak", 100, "天"),
    _a("streak-365", "一年到头", "坚持", "连续 365 天使用", "一年三百六十五天，天天都在。", "streak", 365, "天"),
    _a("days-50", "常来常往", "坚持", "累计使用 50 天", "用了五十天，好好说已经是你的常用工具。", "active_days", 50, "天"),
    _a("days-200", "老朋友", "坚持", "累计使用 200 天", "两百个日子，算是老朋友了。", "active_days", 200, "天"),
    _a("day-3k", "话匣子", "说话", "一天说满 3,000 字", "话匣子一打开，一天就是三千字。", "day_chars", 3_000, "字"),
    _a("day-10k", "滔滔不绝", "说话", "一天说满 1 万字", "一天一万字，键盘今天可以歇着了。", "day_chars", 10_000, "字"),
    _a("day-50", "说个不停", "说话", "一天说 50 次", "一天五十次，今天一定很忙。", "day_dictations", 50, "次"),
    _a("breath", "一口气", "说话", "一次说满 2 分钟", "一口气说满两分钟，这是单次录音的上限。", "full_breath"),
    _a("count-100", "熟能生巧", "说话", "累计说 100 次", "第一百次了，越用越顺手。", "dictations", 100, "次"),
    _a("count-1000", "千锤百炼", "说话", "累计说 1,000 次", "第一千次。你说它写，配合得越来越默契。", "dictations", 1_000, "次"),
    _a("saved-1h", "偷得浮生", "省时", "省下 1 小时打字时间", "省下一个小时，够慢慢喝一壶茶。", "saved_hours", 1, "小时"),
    _a("saved-8h", "多出一天", "省时", "省下 8 小时打字时间", "省下的时间，够一个完整的工作日了。", "saved_hours", 8, "小时"),
    _a("saved-100h", "时间富翁", "省时", "省下 100 小时打字时间", "一百个小时，差不多是两周半的工作时间。", "saved_hours", 100, "小时"),
    _a("meeting-1", "列席", "会议", "记录第一场会议", "第一场会议记下来了，不用再埋头记笔记。", "meetings"),
    _a("minutes-1", "纪要一份", "会议", "生成第一份会议纪要", "纪要写好了，会上说了什么一目了然。", "minutes"),
    _a("export-1", "白纸黑字", "会议", "第一次导出会议记录", "导出成文件，说过的话有据可查。", "exports"),
    _a("meeting-2h", "马拉松", "会议", "记录一场超过 2 小时的会议", "两个多小时的会，辛苦了。", "longest_meeting", 2, "小时"),
    _a("meeting-10h", "会海沉浮", "会议", "累计记录 10 小时会议", "十个小时的会，都替你记着。", "meeting_hours", 10, "小时"),
    _a("templates-5", "对症下药", "会议", "五种纪要模板都用过", "五种模板都试过了，什么会配什么纪要。", "templates", 5, "种"),
    _a("check-1", "有备而来", "会议", "开会前试一次音", "先试音再开会，是个好习惯。", "sound_checks"),
    _a("select-1", "一眼看懂", "翻译", "第一次划词翻译", "看不懂的地方，选中就有译文。", "selections"),
    _a("replace-1", "原地换装", "翻译", "第一次用译文替换原文", "译文直接换掉原文，省得复制粘贴。", "replacements"),
    _a("select-100", "随身译员", "翻译", "划词翻译 100 次", "翻译了一百次，像是身边跟着一位译员。", "selections", 100, "次"),
    _a("page-10k", "长篇大论", "翻译", "在翻译页一次译完 1 万字", "一万字一口气译完，长文也不在话下。", "page_chars", 10_000, "字"),
    _a("styles-3", "字斟句酌", "翻译", "自然、正式、直译三种风格都用过", "同一句话换种译法，挑最合适的那版。", "styles", 3, "种"),
    _a("languages-3", "通晓多国", "翻译", "译成 3 种以上语言", "三种语言来回切换，走到哪儿都能看懂。", "languages", 3, "种"),
    _a("import-1", "旧声新文", "探索", "第一次导入录音", "旧录音也变成了文字。", "imports"),
    _a("model-1", "换只耳朵", "探索", "换用另一个语音模型", "换个模型听听，看哪个更懂你的口音。", "model_switches"),
    _a("lexicon-1", "术业专攻", "探索", "导入一个专业词库", "有了专业词库，行话也能听准。", "lexicons"),
    _a("glossary-20", "自有一套", "探索", "个人词库攒到 20 个词", "二十个词，它越来越懂你的说法。", "glossary", 20, "个词"),
    _a("voice-1", "脱口而译", "探索", "用语音翻译输入一次", "说完加一句“翻译成英文”，输入的就是译文。", "voice_translations"),
    _a("paste-10", "补上一句", "探索", "用 Alt+V 补输 10 次", "换了窗口也不怕，Alt+V 再输一次就行。", "paste_again", 10, "次"),
    _a("gpu-1", "显卡出马", "探索", "第一次用显卡识别", "显卡也来帮忙，出字更快了。", "gpu"),
    _a("night", "夜猫子", "隐藏", "在凌晨 0 点到 4 点之间说话", "夜深了，说完这句早点休息。", "night", hint="夜深人静的时候"),
    _a("dawn", "早起的燕子", "隐藏", "在早上 5 点到 7 点之间说话", "天刚亮就开口了，早起的燕子有虫吃。", "dawn", hint="天刚蒙蒙亮"),
    _a("new-year", "新年第一句", "隐藏", "在 1 月 1 日说话", "新年第一句，祝这一年都说得顺。", "new_year", hint="一年只有一天"),
    _a("day-520", "五二零", "隐藏", "某一天正好说了 520 字", "今天正好 520 字，巧了。", "day_520", hint="一个特别的数字"),
    _a("cancels", "欲言又止", "隐藏", "取消 10 次录音", "话到嘴边又咽回去，想好再说也不错。", "cancels", 10, "次", hint="话到嘴边……"),
    _a("all-round", "全能选手", "隐藏", "同一天里说话、会议、翻译都用过", "说话、开会、翻译，一天全用上了。", "all_round", hint="一天里样样都来"),
    _a("anniversary", "一周年", "隐藏", "第一次使用满一年后还在用", "认识一周年了，谢谢你一直在用。", "anniversary", hint="时间会给答案"),
    _a("welcome-back", "好久不见", "隐藏", "隔了 30 天以上又回来用", "好久不见，欢迎回来。", "welcome_back", hint="离开一阵子再回来"),
    _a("rapid", "连珠炮", "隐藏", "一分钟内连说 5 次", "一分钟连说五次，节奏真快。", "rapid", hint="快，再快一点"),
    _a("weekend", "周末也不闲", "隐藏", "同一个周末的周六和周日都在用", "周末两天都在用，也记得歇一歇。", "weekend", hint="本该休息的两天"),
)
BY_ID = {achievement.id: achievement for achievement in ACHIEVEMENTS}

# Kept in the tally: counts that only go up, the largest value seen, sets of names, and the
# last day each kind of use happened.
COUNTS = ("meetings", "minutes", "exports", "sound_checks", "selections", "replacements", "imports",
          "model_switches", "voice_translations", "paste_again", "cancels", "meeting_seconds",
          "night", "dawn", "rapid", "full_breath", "gpu")
LARGEST = ("longest_meeting_seconds", "page_chars")
SETS = ("templates", "styles", "languages")
USES = ("dictation", "meeting", "translation")
RAPID = 5, 60.0  # Dictations, within so many seconds.


class Achievements:
    """The tally and which achievements are reached (and when), saved as achievements.json.
    `fresh` while there is no file yet: the first check then counts what the statistics
    already show, and says so once instead of one achievement at a time."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "achievements.json"
        self.counts: dict[str, float] = {}
        self.sets: dict[str, set[str]] = {}
        self.used: dict[str, str] = {}
        self.reached: dict[str, str] = {}
        self.recent: deque[float] = deque(maxlen=RAPID[0])
        self.fresh = True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self.fresh = False
        self.counts = {k: float(v) for k, v in _dict(raw, "counts").items()
                       if k in COUNTS + LARGEST and isinstance(v, (int, float))}
        self.sets = {k: {str(item) for item in v} for k, v in _dict(raw, "sets").items()
                     if k in SETS and isinstance(v, list)}
        self.used = {k: v for k, v in _dict(raw, "used").items() if k in USES and isinstance(v, str)}
        self.reached = {k: v for k, v in _dict(raw, "reached").items() if k in BY_ID and isinstance(v, str)}

    # ---------- what happened ----------
    def add(self, key: str, amount: float = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + amount

    def largest(self, key: str, value: float) -> None:
        self.counts[key] = max(self.counts.get(key, 0), value)

    def include(self, key: str, item: str) -> None:
        if item:
            self.sets.setdefault(key, set()).add(item)

    def use(self, kind: str, when: datetime) -> None:
        self.used[kind] = when.date().isoformat()

    def dictated(self, seconds: float, when: datetime, translated: bool = False, device: str = "") -> None:
        """A dictation finished (or a recording was imported, with `seconds` 0)."""
        self.use("dictation", when)
        if when.hour < 4:
            self.add("night")
        elif 5 <= when.hour < 7:
            self.add("dawn")
        if seconds >= MAX_SECONDS - 3:
            self.add("full_breath")
        if translated:
            self.add("voice_translations")
        if device and device != "CPU":
            self.add("gpu")
        self.recent.append(when.timestamp())
        if len(self.recent) == RAPID[0] and self.recent[-1] - self.recent[0] <= RAPID[1]:
            self.add("rapid")

    def meeting_ended(self, seconds: float, when: datetime) -> None:
        self.add("meetings")
        self.add("meeting_seconds", max(0.0, seconds))
        self.largest("longest_meeting_seconds", seconds)
        self.use("meeting", when)

    def minutes_written(self, template: str) -> None:
        self.add("minutes")
        self.include("templates", template)

    def translated(self, language: str, style: str, when: datetime, selection: bool = True, chars: int = 0) -> None:
        """A selection translated, or (selection False) a text finished on the 翻译 page."""
        if selection:
            self.add("selections")
        else:
            self.largest("page_chars", chars)
        self.include("languages", language)
        self.include("styles", style)
        self.use("translation", when)

    def count_meetings(self, meetings: list[tuple[dict, str]]) -> None:
        """Meetings kept from before achievements existed: (info, minutes template or "")."""
        for info, template in meetings:
            seconds = info.get("duration")
            if isinstance(seconds, (int, float)) and info.get("characters"):
                self.add("meetings")
                self.add("meeting_seconds", max(0.0, seconds))
                self.largest("longest_meeting_seconds", seconds)
            if template:
                self.minutes_written(template)

    # ---------- what it adds up to ----------
    def facts(self, stats: StatsStore, settings: Settings) -> dict[str, float]:
        days = {date.fromisoformat(key): day for key, day in stats.days.items() if day.dictations}
        active = sorted(days)
        total = stats.total()
        counts = self.counts
        facts = {
            "chars": total.chars,
            "dictations": total.dictations,
            "streak": stats.longest_streak(),
            "active_days": len(active),
            "day_chars": max((day.chars for day in days.values()), default=0),
            "day_dictations": max((day.dictations for day in days.values()), default=0),
            "saved_hours": stats.saved_minutes() / 60,
            "longest_meeting": counts.get("longest_meeting_seconds", 0) / 3600,
            "meeting_hours": counts.get("meeting_seconds", 0) / 3600,
            "lexicons": len(settings.lexicons),
            "glossary": len(settings.glossary),
            "new_year": any(day.month == 1 and day.day == 1 for day in active),
            "day_520": any(day.chars == 520 for day in days.values()),
            "weekend": any(day.weekday() == 5 and day + timedelta(days=1) in days for day in active),
            "welcome_back": any((later - earlier).days > 30 for earlier, later in zip(active, active[1:], strict=False)),
            "anniversary": bool(active) and (active[-1] - active[0]).days >= 365,
            "all_round": len(self.used) == len(USES) and len(set(self.used.values())) == 1,
        }
        for key in COUNTS + LARGEST:
            facts.setdefault(key, counts.get(key, 0))
        for key in SETS:
            facts[key] = len(self.sets.get(key, ()))
        return facts

    def check(self, stats: StatsStore, settings: Settings, when: datetime | None = None) -> list[Achievement]:
        """Mark what is newly reached, in the order of ACHIEVEMENTS, and save."""
        facts = self.facts(stats, settings)
        stamp = (when or datetime.now()).isoformat(timespec="minutes")
        new = [a for a in ACHIEVEMENTS if a.id not in self.reached and facts.get(a.measure, 0) >= a.goal]
        for achievement in new:
            self.reached[achievement.id] = stamp
        self.fresh = False
        self.save()
        return new

    def progress(self, achievement: Achievement, facts: dict[str, float]) -> tuple[float, float]:
        return min(facts.get(achievement.measure, 0), achievement.goal), achievement.goal

    def upcoming(self, facts: dict[str, float]) -> Achievement | None:
        """The visible achievement not yet reached that is closest to being reached."""
        waiting = [a for a in ACHIEVEMENTS if a.id not in self.reached and not a.hidden and a.goal > 1]
        if not waiting:
            return None
        return max(waiting, key=lambda a: (facts.get(a.measure, 0) / a.goal, -ACHIEVEMENTS.index(a)))

    def latest(self, count: int) -> list[Achievement]:
        """The most recently reached, newest first."""
        order = sorted(self.reached, key=lambda key: (self.reached[key], ACHIEVEMENTS.index(BY_ID[key])), reverse=True)
        return [BY_ID[key] for key in order[:count]]

    # ---------- the file ----------
    def save(self) -> None:
        if self.path is None:
            return
        payload = {
            "version": 1,
            "counts": self.counts,
            "sets": {key: sorted(value) for key, value in self.sets.items()},
            "used": self.used,
            "reached": self.reached,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def clear(self) -> None:
        """Back to nothing, with the statistics. Not fresh: nothing old is counted again."""
        self.counts, self.sets, self.used, self.reached = {}, {}, {}, {}
        self.recent.clear()
        self.fresh = False
        self.save()


def _dict(raw: dict, key: str) -> dict:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}
