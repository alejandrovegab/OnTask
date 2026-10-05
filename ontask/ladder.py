"""The escalating reminder interval.

The ladder holds a list of intervals and, for each rung, how many yes answers
are needed before moving up. Both lists come from settings; the shipped default
is 3, 5, 7, 10, 14, 20 minutes advancing after 1, 2, 2, 3, 3 yes answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ladder:
    intervals_minutes: list[float] = field(default_factory=lambda: [3, 5, 7, 10, 14, 20])
    advance_after_yes: list[int] = field(default_factory=lambda: [1, 2, 2, 3, 3])
    rung: int = 0
    yes_at_rung: int = 0

    def __post_init__(self) -> None:
        if not self.intervals_minutes:
            self.intervals_minutes = [3.0]
        self.clamp()

    def clamp(self) -> None:
        self.rung = max(0, min(self.rung, len(self.intervals_minutes) - 1))

    @property
    def at_max(self) -> bool:
        return self.rung >= len(self.intervals_minutes) - 1

    @property
    def interval_minutes(self) -> float:
        self.clamp()
        return float(self.intervals_minutes[self.rung])

    @property
    def interval_seconds(self) -> float:
        return self.interval_minutes * 60.0

    @property
    def base_seconds(self) -> float:
        """The starting interval, used after a No and after distractions."""
        return float(self.intervals_minutes[0]) * 60.0

    def yes_needed(self) -> int:
        """Yes answers required at the current rung to advance."""
        if self.at_max or self.rung >= len(self.advance_after_yes):
            return 0
        return max(1, int(self.advance_after_yes[self.rung]))

    def on_yes(self) -> bool:
        """Record an on-task answer. Returns True if the interval grew."""
        if self.at_max:
            self.yes_at_rung += 1
            return False
        self.yes_at_rung += 1
        if self.yes_at_rung >= self.yes_needed():
            self.rung += 1
            self.yes_at_rung = 0
            return True
        return False

    def reset(self) -> None:
        """Back to the shortest interval, after a No."""
        self.rung = 0
        self.yes_at_rung = 0

    def describe(self) -> str:
        mins = _fmt(self.interval_minutes)
        if self.at_max:
            return f"{mins} min (max)"
        remaining = max(0, self.yes_needed() - self.yes_at_rung)
        nxt = _fmt(self.intervals_minutes[self.rung + 1])
        yeses = "yes" if remaining == 1 else "yeses"
        return f"{mins} min - {remaining} more {yeses} to reach {nxt} min"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
