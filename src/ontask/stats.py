"""Persistent statistics: what sessions happened and where the time went.

The store is an append-only log of small records rather than running totals,
because every question worth asking about focus is a question about *when*:
which hour of the day goes well, whether distraction is trending down, which
sites keep pulling you away. Totals cannot answer any of those after the fact,
so the raw events are kept and aggregated on demand.

The log is capped and trimmed oldest-first, so the file cannot grow without
bound on a machine that runs OnTask every day.

No UI and no OS calls live here: `Stats` is fed by the controller and read by
the statistics window, and is testable with an injected clock.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .core.files import make_private, read_capped, write_private

# Roughly a year of heavy use; trimmed oldest-first past this.
MAX_EVENTS = 20000

# MAX_EVENTS of them come to a few megabytes; a file far past that is damaged.
MAX_STATS_BYTES = 32_000_000

# Writing on every record would hammer the disk during a distracted stretch.
SAVE_DEBOUNCE_SECONDS = 10.0

SESSION = "session"
ANSWER = "answer"
OFF_TASK = "offtask"
RECOVERED = "recovered"


def default_stats_path(config_path: Path | None = None) -> Path:
    """Stats live beside config.json, under whatever root that resolved to."""
    if config_path is not None:
        return Path(config_path).with_name("stats.json")
    from .core.config import default_config_path

    return default_config_path().with_name("stats.json")


@dataclass
class Stats:
    path: Path | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    clock: Callable[[], float] = time.time
    _last_saved: float = field(default=0.0, repr=False)
    _dirty: bool = field(default=False, repr=False)

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None, clock: Callable[[], float] = time.time) -> Stats:
        path = Path(path) if path else default_stats_path()
        try:
            raw = json.loads(read_capped(path, MAX_STATS_BYTES))
            make_private(path)
        except FileNotFoundError:
            return cls(path=path, clock=clock)
        except (json.JSONDecodeError, OSError, ValueError):
            # Statistics are never worth blocking a session over.
            return cls(path=path, clock=clock)
        events = raw.get("events") if isinstance(raw, dict) else None
        if not isinstance(events, list):
            events = []
        clean = [e for e in events if isinstance(e, dict) and "e" in e and "t" in e]
        return cls(path=path, events=clean, clock=clock)

    def save(self) -> Path | None:
        if self.path is None:
            return None
        target = Path(self.path)
        try:
            write_private(target, json.dumps({"events": self.events}, separators=(",", ":")) + "\n")
        except OSError:
            return None
        self._dirty = False
        self._last_saved = self.clock()
        return target

    def maybe_save(self, force: bool = False) -> None:
        """Write if something changed and the debounce window has passed."""
        if not self._dirty:
            return
        if force or (self.clock() - self._last_saved) >= SAVE_DEBOUNCE_SECONDS:
            self.save()

    # -- recording --------------------------------------------------------

    def record(self, name: str, **fields: Any) -> dict[str, Any]:
        """Append one event. `name` is the record type; an answer's own "kind"
        field is a separate thing, hence the deliberately different parameter."""
        event = {"t": float(self.clock()), "e": name}
        event.update(fields)
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]
        self._dirty = True
        return event

    def record_session(self, seconds: float, profile: str) -> None:
        # A session someone ended immediately is noise, not data.
        if seconds >= 1.0:
            self.record(SESSION, seconds=round(float(seconds), 1), profile=profile)

    def record_answer(
        self, kind: str, yes: bool, ignored: bool, key: str, label: str, profile: str
    ) -> None:
        self.record(
            ANSWER,
            kind=kind,
            yes=bool(yes),
            ignored=bool(ignored),
            key=key,
            label=label,
            profile=profile,
        )

    def record_off_task(
        self, key: str, label: str, seconds: float, blocked: bool, profile: str
    ) -> None:
        if seconds >= 1.0:
            self.record(
                OFF_TASK,
                key=key,
                label=label,
                seconds=round(float(seconds), 1),
                blocked=bool(blocked),
                profile=profile,
            )

    def record_recovered(self, key: str, label: str, profile: str) -> None:
        self.record(RECOVERED, key=key, label=label, profile=profile)

    def forget_before(self, stamp: float) -> None:
        """Drop everything recorded up to `stamp`, keeping anything newer.

        This is how a reset made in the statistics window reaches the running
        app, which still holds the old events in memory and would otherwise
        write them straight back on its next save.
        """
        kept = [e for e in self.events if float(e.get("t") or 0.0) > stamp]
        if len(kept) != len(self.events):
            self.events = kept
            self._dirty = True

    def clear(self) -> None:
        self.events.clear()
        self._dirty = True
        self.save()

    # -- aggregation ------------------------------------------------------

    def summary(self, since: float | None = None) -> Summary:
        events = self.events if since is None else [e for e in self.events if e["t"] >= since]
        return Summary.build(events)


@dataclass
class Summary:
    """Everything the statistics window shows, computed from the raw log."""

    session_count: int = 0
    session_seconds: float = 0.0
    seconds_by_profile: dict[str, float] = field(default_factory=dict)
    off_task_seconds: float = 0.0
    blocked_seconds: float = 0.0
    seconds_by_target: dict[str, float] = field(default_factory=dict)
    yes_count: int = 0
    no_count: int = 0
    ignored_count: int = 0
    recovered_count: int = 0
    answers_by_hour: dict[int, tuple[int, int]] = field(default_factory=dict)
    off_task_by_hour: dict[int, float] = field(default_factory=dict)
    by_day: list[tuple[str, float, float]] = field(default_factory=list)
    first_event: float = 0.0
    last_event: float = 0.0

    @classmethod
    def build(cls, events: list[dict[str, Any]]) -> Summary:
        out = cls()
        by_profile: dict[str, float] = defaultdict(float)
        by_target: dict[str, float] = defaultdict(float)
        by_hour_yes: dict[int, int] = defaultdict(int)
        by_hour_no: dict[int, int] = defaultdict(int)
        off_by_hour: dict[int, float] = defaultdict(float)
        day_session: dict[str, float] = defaultdict(float)
        day_off: dict[str, float] = defaultdict(float)

        for event in events:
            stamp = float(event.get("t") or 0.0)
            if stamp:
                out.first_event = stamp if not out.first_event else min(out.first_event, stamp)
                out.last_event = max(out.last_event, stamp)
            when = datetime.fromtimestamp(stamp) if stamp else None
            day = when.strftime("%Y-%m-%d") if when else "?"
            hour = when.hour if when else 0
            kind = event.get("e")

            if kind == SESSION:
                seconds = float(event.get("seconds") or 0.0)
                out.session_count += 1
                out.session_seconds += seconds
                by_profile[str(event.get("profile") or "?")] += seconds
                day_session[day] += seconds
            elif kind == OFF_TASK:
                seconds = float(event.get("seconds") or 0.0)
                out.off_task_seconds += seconds
                if event.get("blocked"):
                    out.blocked_seconds += seconds
                by_target[str(event.get("label") or event.get("key") or "?")] += seconds
                off_by_hour[hour] += seconds
                day_off[day] += seconds
            elif kind == ANSWER:
                if event.get("ignored"):
                    out.ignored_count += 1
                elif event.get("yes"):
                    out.yes_count += 1
                    by_hour_yes[hour] += 1
                else:
                    out.no_count += 1
                    by_hour_no[hour] += 1
            elif kind == RECOVERED:
                out.recovered_count += 1

        out.seconds_by_profile = dict(sorted(by_profile.items(), key=lambda kv: -kv[1]))
        out.seconds_by_target = dict(sorted(by_target.items(), key=lambda kv: -kv[1]))
        out.answers_by_hour = {
            hour: (by_hour_yes.get(hour, 0), by_hour_no.get(hour, 0))
            for hour in sorted(set(by_hour_yes) | set(by_hour_no))
        }
        out.off_task_by_hour = dict(sorted(off_by_hour.items()))
        out.by_day = [
            (day, day_session.get(day, 0.0), day_off.get(day, 0.0))
            for day in sorted(set(day_session) | set(day_off))
        ]
        return out

    # -- derived ----------------------------------------------------------

    @property
    def average_session_seconds(self) -> float:
        return self.session_seconds / self.session_count if self.session_count else 0.0

    @property
    def answered_count(self) -> int:
        return self.yes_count + self.no_count

    @property
    def yes_rate(self) -> float:
        return self.yes_count / self.answered_count if self.answered_count else 0.0

    @property
    def distraction_rate(self) -> float:
        """Off-task time as a share of session time."""
        return self.off_task_seconds / self.session_seconds if self.session_seconds else 0.0

    def top_targets(self, limit: int = 8) -> list[tuple[str, float]]:
        return list(self.seconds_by_target.items())[:limit]

    def best_hour(self) -> int | None:
        """Hour of day with the highest yes rate, needing enough answers to mean it."""
        best, best_rate = None, -1.0
        for hour, (yes, no) in self.answers_by_hour.items():
            total = yes + no
            if total < 3:
                continue
            rate = yes / total
            if rate > best_rate:
                best, best_rate = hour, rate
        return best

    def worst_hour(self) -> int | None:
        """Hour of day with the most off-task time."""
        if not self.off_task_by_hour:
            return None
        return max(self.off_task_by_hour.items(), key=lambda kv: kv[1])[0]

    @property
    def is_empty(self) -> bool:
        return not (self.session_count or self.answered_count or self.off_task_seconds)


def format_hour(hour: int) -> str:
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    return f"{display}{suffix}"


def format_span(seconds: float) -> str:
    """Human duration for report text: 2h 14m, 14m, 45s."""
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
