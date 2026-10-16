"""Local usage statistics: daily counts and token totals only, never any text."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .api_config import is_deepseek_api
from .settings import data_dir

# Chinese typing speed used for the "time saved" estimate.
TYPING_CHARS_PER_MINUTE = 40
MILESTONES = (1_000, 10_000, 50_000, 100_000, 500_000, 1_000_000)

# DeepSeek list prices, yuan per million tokens: (cache hit, cache miss, output) at peak time.
# Off-peak is half. Source: api-docs.deepseek.com/zh-cn/quick_start/pricing (2026-10).
DEEPSEEK_PEAK_PRICES = {
    "deepseek-flash": (0.04, 2.0, 8.0),
    "deepseek-v4-flash": (0.04, 2.0, 8.0),
    "deepseek-v4-pro": (0.30, 9.0, 27.0),
}
BEIJING = timezone(timedelta(hours=8))


def deepseek_cost(model: str, usage: dict, when: datetime) -> float | None:
    """Estimated cost in yuan; None for models without a known price."""
    prices = DEEPSEEK_PEAK_PRICES.get(model)
    if prices is None:
        return None
    local = when.astimezone(BEIJING)
    peak = local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18)
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or max(0, int(usage.get("prompt_tokens") or 0) - hit))
    output = int(usage.get("completion_tokens") or 0)
    hit_price, miss_price, output_price = prices
    cost = (hit * hit_price + miss * miss_price + output * output_price) / 1_000_000
    return cost if peak else cost / 2


def count_chars(text: str) -> int:
    return sum(not ch.isspace() for ch in text)


@dataclass
class Day:
    dictations: int = 0
    chars: int = 0
    seconds: float = 0.0
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> Day:
        names = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names and isinstance(value, (int, float))})


class StatsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "stats.json"
        self.days: dict[str, Day] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in raw.get("days", {}).items():
                date.fromisoformat(key)
                if isinstance(value, dict):
                    self.days[key] = Day.from_dict(value)
        except (OSError, ValueError, AttributeError):
            self.days = {}

    @classmethod
    def empty(cls) -> StatsStore:
        """A store with no data that never touches the disk (statistics turned off)."""
        store = cls.__new__(cls)
        store.path, store.days = None, {}
        return store

    def _day(self, when: datetime) -> Day:
        return self.days.setdefault(when.date().isoformat(), Day())

    def record_dictation(self, text: str, seconds: float, when: datetime | None = None) -> None:
        day = self._day(when or datetime.now())
        day.dictations += 1
        day.chars += count_chars(text)
        day.seconds += max(0.0, seconds)

    def record_request(self, usage: dict, base_url: str, model: str, when: datetime | None = None) -> None:
        if not usage:
            return
        when = when or datetime.now().astimezone()
        day = self._day(when)
        day.requests += 1
        day.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        day.completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            deepseek = is_deepseek_api(base_url)
        except ValueError:
            deepseek = False
        if deepseek:
            day.cost += deepseek_cost(model, usage, when) or 0.0

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        payload = {"version": 1, "days": {key: asdict(value) for key, value in sorted(self.days.items())}}
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    def clear(self) -> None:
        self.days = {}
        self.path.unlink(missing_ok=True)

    # ---------- summaries ----------
    def day(self, when: date) -> Day:
        return self.days.get(when.isoformat(), Day())

    def total(self, since: date | None = None) -> Day:
        result = Day()
        for key, value in self.days.items():
            if since and date.fromisoformat(key) < since:
                continue
            for field in fields(Day):
                setattr(result, field.name, getattr(result, field.name) + getattr(value, field.name))
        return result

    def streak(self, today: date) -> int:
        """Consecutive days with dictation, ending today (or yesterday if today has none yet)."""
        current = today if self.day(today).dictations else today - timedelta(days=1)
        count = 0
        while self.day(current).dictations:
            count += 1
            current -= timedelta(days=1)
        return count

    def longest_streak(self) -> int:
        best = run = 0
        previous = None
        for key in sorted(k for k, v in self.days.items() if v.dictations):
            current = date.fromisoformat(key)
            run = run + 1 if previous and current - previous == timedelta(days=1) else 1
            best, previous = max(best, run), current
        return best

    def active_days(self) -> int:
        return sum(1 for value in self.days.values() if value.dictations)

    def best_day(self) -> tuple[str, int]:
        if not self.days:
            return "", 0
        key = max(self.days, key=lambda k: self.days[k].chars)
        return key, self.days[key].chars

    def saved_minutes(self) -> float:
        total = self.total()
        return max(0.0, total.chars / TYPING_CHARS_PER_MINUTE - total.seconds / 60)

    def next_milestone(self) -> tuple[int, int]:
        """(reached, next) character milestones; next is 0 once all are reached."""
        chars = self.total().chars
        reached = max((m for m in MILESTONES if chars >= m), default=0)
        upcoming = next((m for m in MILESTONES if chars < m), 0)
        return reached, upcoming
