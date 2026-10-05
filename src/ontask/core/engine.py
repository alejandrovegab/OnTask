"""The reminder state machine.

The engine owns all timing decisions and is deliberately free of UI and OS
calls: it is driven by `tick(now, target)` and `answer(now, yes)` and returns
events for the shell to act on. That keeps the timing rules testable with a
fake clock.

Two independent timers run:

* the **cadence** timer, which only counts down while the frontmost target is
  approved, and which escalates up the ladder on each yes;
* the **off-task** timer, which counts continuous time on unlisted targets
  (default 1 min) or time on an explicitly blocked target (default 10 s).

Answering an off-task prompt with yes never advances the ladder; it just buys
another base interval, per the spec.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..focus import UNKNOWN, FocusTarget
from .config import Config
from .ladder import Ladder
from .matching import classify, suggest_rule

IDLE = "idle"
RUNNING = "running"
PAUSED = "paused"

CADENCE = "cadence"
DISTRACTION = "distraction"
BLOCKED = "blocked"

# After an off-task check-in, a return to approved work inside this many
# seconds counts as the check-in having done its job.
RECOVERY_SECONDS = 120.0


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
    penalty_seconds: float = 0.0
    target_key: str = ""
    target_label: str = ""


@dataclass
class OffTaskLogged(Event):
    """A finished stretch on something that was not on the approved list."""

    key: str
    label: str
    seconds: float
    blocked: bool


@dataclass
class Recovered(Event):
    """A check-in was followed by a return to approved work."""

    key: str
    label: str


@dataclass
class SuggestApprove(Event):
    target: FocusTarget
    rule: str


@dataclass
class SessionChanged(Event):
    phase: str
    elapsed_seconds: float = 0.0
    profile: str = ""


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
        # The off-task stretch in progress, banked and reported when it ends.
        self._off_task_bucket: dict | None = None
        # Set when an off-task check-in is answered; a return to approved work
        # before the deadline is what "the check-in worked" means.
        self._recovery_deadline = 0.0

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

    def _bank_off_task(self, target: FocusTarget, blocked: bool, dt: float) -> list[Event]:
        """Add `dt` to the running off-task stretch, closing the previous one.

        Time is attributed per target so statistics can say *what* the time went
        on, which is independent of the cumulative off-task timer that decides
        when to interrupt.
        """
        key = target.key()
        events: list[Event] = []
        bucket = self._off_task_bucket
        if bucket is not None and bucket["key"] != key:
            events = self._flush_off_task()
            bucket = None
        if bucket is None:
            bucket = {"key": key, "label": target.describe(), "seconds": 0.0, "blocked": blocked}
            self._off_task_bucket = bucket
        bucket["seconds"] += dt
        bucket["blocked"] = blocked
        return events

    def _flush_off_task(self) -> list[Event]:
        bucket = self._off_task_bucket
        self._off_task_bucket = None
        if bucket is None or bucket["seconds"] <= 0:
            return []
        return [
            OffTaskLogged(
                key=bucket["key"],
                label=bucket["label"],
                seconds=bucket["seconds"],
                blocked=bool(bucket["blocked"]),
            )
        ]

    # -- session control --------------------------------------------------

    def start(self, now: float | None = None) -> list[Event]:
        now = self._now(now)
        self.phase = RUNNING
        self.elapsed_seconds = 0.0
        self.ladder.reset()
        self.cadence_remaining = self.ladder.interval_seconds
        self.last_tick = now
        self._reset_off_task()
        return [SessionChanged(self.phase, 0.0, self.config.active_profile)]

    def stop(self, now: float | None = None) -> list[Event]:
        elapsed = self.elapsed_seconds
        self.phase = IDLE
        self.last_tick = self._now(now)
        events: list[Event] = self._flush_off_task()
        if self.active_prompt is not None:
            self.active_prompt = None
            events.append(ClosePrompt("session stopped"))
        events.append(SessionChanged(self.phase, elapsed, self.config.active_profile))
        return events

    def pause(self, now: float | None = None) -> list[Event]:
        if self.phase != RUNNING:
            return []
        self.phase = PAUSED
        self.last_tick = self._now(now)
        events: list[Event] = self._flush_off_task()
        if self.active_prompt is not None:
            self.active_prompt = None
            events.append(ClosePrompt("session paused"))
        events.append(SessionChanged(self.phase, self.elapsed_seconds, self.config.active_profile))
        return events

    def resume(self, now: float | None = None) -> list[Event]:
        if self.phase != PAUSED:
            return []
        self.phase = RUNNING
        self.last_tick = self._now(now)
        return [SessionChanged(self.phase, self.elapsed_seconds, self.config.active_profile)]

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
        dt = min(dt, self._max_step())
        if self.phase != RUNNING:
            return []
        self.elapsed_seconds += dt

        if self.active_prompt is not None:
            return self._tick_open_prompt(now)

        profile = self.config.profile()
        result = classify(target, profile.approved, profile.disapproved)
        reminder = self.config.reminder

        if result.is_approved:
            events: list[Event] = []
            if self.last_key is not None:
                # Back on approved work: the cumulative off-task timer starts
                # over. Switching between two unapproved targets does not.
                events.extend(self._flush_off_task())
                self._reset_off_task()
            events.extend(self._check_recovery(target, now))
            self.cadence_remaining -= dt
            if self.cadence_remaining <= 0:
                events.extend(self._open(CADENCE, target, now))
            return events

        key = target.key()
        if key != self.last_key:
            self.target_elapsed = 0.0
            self.last_key = key
            if result.is_disapproved:
                # Deliberately opening a blocked app re-arms the short fuse.
                self.off_task_snooze = 0.0
        self.target_elapsed += dt
        self.off_task_elapsed += dt
        events = self._bank_off_task(target, result.is_disapproved, dt)

        if self.off_task_snooze > 0:
            self.off_task_snooze -= dt
            return events
        if result.is_disapproved:
            if self.target_elapsed >= reminder.disapproved_grace_seconds:
                events.extend(self._open(BLOCKED, target, now))
            return events
        if self.off_task_elapsed >= reminder.distraction_grace_seconds:
            events.extend(self._open(DISTRACTION, target, now))
        return events

    def _check_recovery(self, target: FocusTarget, now: float) -> list[Event]:
        """Emit Recovered if approved work resumed soon after a check-in."""
        if not self._recovery_deadline:
            return []
        deadline, self._recovery_deadline = self._recovery_deadline, 0.0
        if now <= deadline:
            return [Recovered(target.key(), target.describe())]
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

        penalty = 0.0
        if not yes:
            penalty = self.config.reminder.clock_penalty.seconds_for(
                prompt.kind, self.config.reminder
            )
            # The clock can be reduced to nothing, but never run backwards.
            penalty = min(penalty, self.elapsed_seconds)
            self.elapsed_seconds -= penalty

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

        if prompt.is_off_task:
            # Whichever way it was answered, watch for a return to approved work
            # so the statistics can say how often a check-in did its job.
            self._recovery_deadline = now + RECOVERY_SECONDS

        events.append(
            Answered(
                kind=prompt.kind,
                yes=yes,
                interval_minutes=self.ladder.interval_minutes,
                advanced=advanced,
                ignored=ignored,
                penalty_seconds=penalty,
                target_key=prompt.target.key(),
                target_label=prompt.target.describe(),
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

    def snapshot(self, now: float | None = None) -> Snapshot:
        """Current state. Pass `now` for a display-accurate reading.

        Without `now` the numbers are exactly as of the last tick, which is what
        the timing rules are written against. With it, the time since that tick
        is added so a clock redrawn every second counts every second, even when
        the frontmost window is only sampled every few. It is a display
        correction only: nothing here decides when to interrupt.
        """
        elapsed = self.elapsed_seconds
        remaining = self.next_prompt_seconds()
        if now is not None and self.phase == RUNNING:
            drift = min(max(0.0, now - self.last_tick), self._max_step())
            elapsed += drift
            if self.active_prompt is None:
                remaining = max(0.0, remaining - drift)
        return Snapshot(
            phase=self.phase,
            profile=self.config.active_profile,
            elapsed_seconds=elapsed,
            interval_minutes=self.ladder.interval_minutes,
            next_prompt_seconds=remaining,
            ladder_text=self.ladder.describe(),
            prompt_open=self.active_prompt is not None,
        )

    def _max_step(self) -> float:
        """Cap on time credited in one go, shared by tick and snapshot."""
        return max(self.config.general.poll_seconds * 3.0, 10.0)

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
