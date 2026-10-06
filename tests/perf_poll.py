"""How long one `Controller.poll()` takes: the work OnTask does every couple of seconds.

The real focus provider is swapped for a fake that cycles through apps and
sites, so this measures OnTask's own work (config and signal checks, rule
matching, the engine, statistics) without waiting on a browser. The profile
carries a long rule list, since matching is the part that grows with use.

    ./.venv/bin/python tests/perf_poll.py     print the numbers

`test_performance.py` runs the same measurement and fails above the budget.
"""

from __future__ import annotations

import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from ontask.app import Controller
from ontask.core.config import Config, Profile
from ontask.focus import FocusProvider, FocusTarget

# The most one poll may take, typically: unnoticeable even on a slow machine.
BUDGET_MS = 1.0

# Rules per list in the measured profile; far more than most people keep.
RULES_PER_LIST = 100

POLLS = 3000
WARMUP = 200

TARGETS = [
    FocusTarget(app_name="Code", bundle_id="com.microsoft.VSCode"),
    FocusTarget(
        app_name="Safari",
        bundle_id="com.apple.Safari",
        url="https://github.com/alejandrovegab/OnTask/pulls",
    ),
    FocusTarget(
        app_name="Google Chrome",
        bundle_id="com.google.Chrome",
        url="https://www.youtube.com/watch?v=abc",
    ),
    FocusTarget(
        app_name="Arc", bundle_id="company.thebrowser.Browser", url="https://example.org/a/b"
    ),
    FocusTarget(app_name="Messages", bundle_id="com.apple.MobileSMS"),
]


class CyclingProvider(FocusProvider):
    """Reports a different app or site on each poll, like someone switching around."""

    def __init__(self, targets=TARGETS):
        self.targets = targets
        self.calls = 0

    def current(self, browsers=None) -> FocusTarget:
        target = self.targets[self.calls % len(self.targets)]
        self.calls += 1
        return target


def long_profile(name: str = "Benchmark", per_list: int = RULES_PER_LIST) -> Profile:
    """A profile with `per_list` rules in each list, mixing apps, sites and paths."""
    approved = ["app:Code", "site:github.com"]
    disapproved = ["site:youtube.com", "site:github.com/trending"]
    for i in range(per_list - len(approved)):
        approved.append(
            (f"app:Tool{i}", f"site:docs{i}.example.com", f"site:wiki.example.com/team{i}")[i % 3]
        )
    for i in range(per_list - len(disapproved)):
        disapproved.append((f"app:Game{i}", f"site:feed{i}.example.net", f"*.social{i}.net")[i % 3])
    return Profile(name=name, approved=approved, disapproved=disapproved)


@dataclass
class Timing:
    median_ms: float
    p95_ms: float

    def __str__(self) -> str:
        return f"median {self.median_ms:.3f} ms, 95th percentile {self.p95_ms:.3f} ms"


def make_controller(folder: Path, session: bool) -> Controller:
    config = Config()
    profile = long_profile()
    config.profiles.append(profile)
    config.active_profile = profile.name
    config.general.start_session_on_launch = False
    path = folder / "config.json"
    config.save(path)
    with mock.patch("ontask.app.get_provider", return_value=CyclingProvider()):
        controller = Controller(config_path=path)
    if session:
        controller.start_session()
    return controller


def time_polls(controller: Controller, polls: int = POLLS, warmup: int = WARMUP) -> Timing:
    for _ in range(warmup):
        controller.poll()
    samples = []
    clock = time.perf_counter_ns
    for _ in range(polls):
        start = clock()
        controller.poll()
        samples.append((clock() - start) / 1e6)
    samples.sort()
    return Timing(statistics.median(samples), samples[int(len(samples) * 0.95)])


def measure(session: bool, polls: int = POLLS) -> Timing:
    with tempfile.TemporaryDirectory() as folder:
        controller = make_controller(Path(folder), session)
        try:
            return time_polls(controller, polls)
        finally:
            controller.focus.close()


def main() -> None:
    print(f"poll() with {RULES_PER_LIST} rules per list, {POLLS} polls each")
    print(f"  no session:  {measure(session=False)}")
    print(f"  in session:  {measure(session=True)}")
    print(f"  budget:      median under {BUDGET_MS} ms")


if __name__ == "__main__":
    main()
