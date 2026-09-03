"""The reminder state machine.

The engine owns all timing decisions and is deliberately free of UI and OS
calls: it is driven by `tick(now, target)` and `answer(now, yes)` and returns
events for the shell to act on. That keeps the timing rules testable with a
fake clock.

Two independent timers run:

* the **cadence** timer, which only counts down while the frontmost target is
  approved, and which escalates up the ladder on each yes;
* the **off-task** timer, which counts continuous time on unlisted targets
  (default 2.5 min) or time on an explicitly blocked target (default 10 s).

Answering an off-task prompt with yes never advances the ladder; it just buys
another base interval, per the spec.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Config
from .focus import UNKNOWN, FocusTarget
from .ladder import Ladder
from .matching import classify, suggest_rule

IDLE = "idle"
RUNNING = "running"
PAUSED = "paused"

CADENCE = "cadence"
DISTRACTION = "distraction"
BLOCKED = "blocked"


@dataclass
class ActivePrompt:
    kind: str
    target: FocusTarget
    opened_at: float
    alerts: int = 1
    next_alert_at: float = 0.0

    @property
    def is_off_task(self) -> bool:
        return self.kind in (DISTRACTION, BLOCKED)

    def question(self) -> str:
        if self.kind == BLOCKED:
            return f"{self.target.label()} is on your blocked list. Still on task?"
        if self.kind == DISTRACTION:
            return f"{self.target.label()} isn't on your approved list. Still on task?"
        return "Still on task?"


class Event:
    pass


@dataclass
class ShowPrompt(Event):
    prompt: ActivePrompt


@dataclass
class RealertPrompt(Event):
    prompt: ActivePrompt


@dataclass
class ClosePrompt(Event):
    reason: str = "answered"


@dataclass
class Answered(Event):
    kind: str
    yes: bool
    interval_minutes: float
    advanced: bool = False
    ignored: bool = False


@dataclass
class SuggestApprove(Event):
    target: FocusTarget
    rule: str


@dataclass
class SessionChanged(Event):
    phase: str


@dataclass
class Snapshot:
    phase: str
    profile: str
    elapsed_seconds: float
    interval_minutes: float
    next_prompt_seconds: float
    ladder_text: str
    prompt_open: bool


class Engine:
    def __init__(self, config: Config, now: float | None = None):
        self.config = config
        self.phase = IDLE
        self.ladder = Ladder(
            intervals_minutes=list(config.reminder.intervals_minutes),
            advance_after_yes=list(config.reminder.advance_after_yes),
        )
        self.elapsed_seconds = 0.0
        self.cadence_remaining = self.ladder.interval_seconds
        self.off_task_elapsed = 0.0
        self.target_elapsed = 0.0
        self.off_task_snooze = 0.0
        self.last_key: str | None = None
        self.consecutive_yes: dict[str, int] = {}
        self.active_prompt: ActivePrompt | None = None
        self.last_tick = now if now is not None else time.monotonic()

    # -- configuration ----------------------------------------------------

    def apply_config(self, config: Config) -> list[Event]:
        """Hot-reload settings, preserving as much live session state as sane."""
        previous_profile = self.config.active_profile
        self.config = config
        self.ladder.intervals_minutes = list(config.reminder.intervals_minutes)
        self.ladder.advance_after_yes = list(config.reminder.advance_after_yes)
        self.ladder.clamp()
        self.cadence_remaining = min(self.cadence_remaining, self.ladder.interval_seconds)
        if config.active_profile != previous_profile:
            return self._reset_off_task()
        return []

    def set_profile(self, name: str) -> list[Event]:
        self.config.active_profile = name
        return self._reset_off_task()

    def _reset_off_task(self) -> list[Event]:
        self.off_task_elapsed = 0.0
        self.target_elapsed = 0.0
        self.off_task_snooze = 0.0
        self.last_key = None
        self.consecutive_yes.clear()
        return []

    # -- session control --------------------------------------------------

    def start(self, now: float | None = None) -> list[Event]:
        now = self._now(now)
        self.phase = RUNNING
        self.elapsed_seconds = 0.0
        self.ladder.reset()
        self.cadence_remaining = self.ladder.interval_seconds
        self.last_tick = now
        self._reset_off_task()
        return [SessionChanged(self.phase)]

    def stop(self, now: float | None = None) -> list[Event]:
        self.phase = IDLE
        self.last_tick = self._now(now)
        events: list[Event] = []
        if self.active_prompt is not None:
            self.active_prompt = None
            events.append(ClosePrompt("session stopped"))
        events.append(SessionChanged(self.phase))
        return events

    def pause(self, now: float | None = None) -> list[Event]:
        if self.phase != RUNNING:
            return []
        self.phase = PAUSED
        self.last_tick = self._now(now)
        events: list[Event] = []
        if self.active_prompt is not None:
            self.active_prompt = None
            events.append(ClosePrompt("session paused"))
        events.append(SessionChanged(self.phase))
        return events

    def resume(self, now: float | None = None) -> list[Event]:
        if self.phase != PAUSED:
            return []
        self.phase = RUNNING
        self.last_tick = self._now(now)
        return [SessionChanged(self.phase)]

    def toggle(self, now: float | None = None) -> list[Event]:
        return self.stop(now) if self.phase != IDLE else self.start(now)

    # -- main loop --------------------------------------------------------

    def tick(self, now: float | None = None, target: FocusTarget = UNKNOWN) -> list[Event]:
        now = self._now(now)
        dt = now - self.last_tick
        self.last_tick = now
        if dt < 0:
            dt = 0.0
        # A large gap means the machine slept or the app was suspended; the user
        # was not sitting there being distracted, so do not bank that time.
        dt = min(dt, max(self.config.general.poll_seconds * 3.0, 10.0))
        if self.phase != RUNNING:
            return []
        self.elapsed_seconds += dt

        if self.active_prompt is not None:
            return self._tick_open_prompt(now)

        profile = self.config.profile()
        result = classify(target, profile.approved, profile.disapproved)
        reminder = self.config.reminder

        if result.is_approved:
            if self.last_key is not None:
                self._reset_off_task()
            self.cadence_remaining -= dt
            if self.cadence_remaining <= 0:
                return self._open(CADENCE, target, now)
            return []

        key = target.key()
        if key != self.last_key:
            self.target_elapsed = 0.0
            self.last_key = key
            if result.is_disapproved:
                # Deliberately opening a blocked app re-arms the short fuse.
                self.off_task_snooze = 0.0
        self.target_elapsed += dt
        self.off_task_elapsed += dt

        if self.off_task_snooze > 0:
            self.off_task_snooze -= dt
            return []
        if result.is_disapproved:
            if self.target_elapsed >= reminder.disapproved_grace_seconds:
                return self._open(BLOCKED, target, now)
            return []
        if self.off_task_elapsed >= reminder.distraction_grace_seconds:
            return self._open(DISTRACTION, target, now)
        return []

    def _tick_open_prompt(self, now: float) -> list[Event]:
        prompt = self.active_prompt
        assert prompt is not None
        rules = self.config.reminder.no_response
        if rules.policy == "wait" or now < prompt.next_alert_at:
            return []
        if rules.policy == "pause_session":
            self.active_prompt = None
            return [ClosePrompt("no response"), *self.pause(now)]
        prompt.alerts += 1
        if prompt.alerts > rules.max_alerts:
            return self.answer(False, now, ignored=True)
        prompt.next_alert_at = now + rules.renag_seconds
        return [RealertPrompt(prompt)]

    def _open(self, kind: str, target: FocusTarget, now: float) -> list[Event]:
        prompt = ActivePrompt(
            kind=kind,
            target=target,
            opened_at=now,
            next_alert_at=now + self.config.reminder.no_response.renag_seconds,
        )
        self.active_prompt = prompt
        return [ShowPrompt(prompt)]

    # -- answering --------------------------------------------------------

    def answer(self, yes: bool, now: float | None = None, ignored: bool = False) -> list[Event]:
        now = self._now(now)
        prompt = self.active_prompt
        if prompt is None:
            return []
        self.active_prompt = None
        events: list[Event] = [ClosePrompt("ignored" if ignored else "answered")]

        advanced = False
        if prompt.is_off_task:
            if yes:
                # Confirming you are on task somewhere unlisted buys one base
                # interval of quiet, but never grows the ladder.
                self.off_task_snooze = self.ladder.base_seconds
                self.off_task_elapsed = 0.0
                self.target_elapsed = 0.0
                events.extend(self._count_consecutive_yes(prompt.target))
            else:
                self.ladder.reset()
                self.cadence_remaining = self.ladder.base_seconds
                self.off_task_elapsed = 0.0
                self.target_elapsed = 0.0
                self.off_task_snooze = 0.0
                self.consecutive_yes.pop(prompt.target.key(), None)
        else:
            if yes:
                advanced = self.ladder.on_yes()
                self.cadence_remaining = self.ladder.interval_seconds
            else:
                self.ladder.reset()
                self.cadence_remaining = self.ladder.base_seconds

        events.append(
            Answered(
                kind=prompt.kind,
                yes=yes,
                interval_minutes=self.ladder.interval_minutes,
                advanced=advanced,
                ignored=ignored,
            )
        )
        return events

    def _count_consecutive_yes(self, target: FocusTarget) -> list[Event]:
        threshold = self.config.reminder.suggest_approve_after_yes
        key = target.key()
        count = self.consecutive_yes.get(key, 0) + 1
        if threshold and count >= threshold:
            self.consecutive_yes[key] = 0
            return [SuggestApprove(target, suggest_rule(target))]
        self.consecutive_yes[key] = count
        return []

    # -- introspection ----------------------------------------------------

    def next_prompt_seconds(self) -> float:
        if self.phase != RUNNING:
            return 0.0
        if self.active_prompt is not None:
            return 0.0
        return max(0.0, self.cadence_remaining)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            phase=self.phase,
            profile=self.config.active_profile,
            elapsed_seconds=self.elapsed_seconds,
            interval_minutes=self.ladder.interval_minutes,
            next_prompt_seconds=self.next_prompt_seconds(),
            ladder_text=self.ladder.describe(),
            prompt_open=self.active_prompt is not None,
        )

    @staticmethod
    def _now(now: float | None) -> float:
        return time.monotonic() if now is None else now


def format_duration(seconds: float) -> str:
    """Compact H:MM:SS / M:SS used in the menu bar and prompt."""
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
