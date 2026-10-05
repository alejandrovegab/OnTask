"""Configuration model, defaults, and JSON persistence for OnTask.

Every number the reminder engine keys off of lives here rather than being
baked into the code, so all of it can be edited from the settings window.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .browsers import Browser, default_browsers
from .browsers import coerce as coerce_browser
from .files import ensure_private_dir, make_private, read_capped, write_private

CONFIG_VERSION = 2

# A real config is a few kilobytes; anything past this is damaged or not ours.
MAX_CONFIG_BYTES = 1_000_000


def default_config_path() -> Path:
    """Per-platform location of config.json, overridable with $ONTASK_CONFIG."""
    env = os.environ.get("ONTASK_CONFIG")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "OnTask"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home()) / "OnTask"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        base = Path(xdg) / "OnTask"
    return base / "config.json"


def _get(d: dict[str, Any], key: str, default: Any) -> Any:
    value = d.get(key, default)
    return default if value is None else value


def _read_browsers(value: Any) -> list[Browser]:
    """Load the browser list, tolerating the old list-of-names format.

    Older configs stored display names only. Those are mapped back to bundle ids
    where the name is one OnTask used to ship, so an existing setup keeps the
    browsers it had; anything unrecognisable is dropped rather than guessed at.
    """
    browsers = []
    for entry in value or []:
        browser = coerce_browser(entry)
        if browser is not None:
            browsers.append(browser)
    return browsers


def _migrate(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a config written by an older version up to CONFIG_VERSION.

    Each step runs once: the result is stamped with the current version, so a
    value the user sets afterwards is never touched again.
    """
    d = dict(d)
    version = int(_get(d, "version", 1))
    if version < 2:
        reminder = d.get("reminder")
        if isinstance(reminder, dict) and reminder.get("distraction_grace_seconds") == 150:
            # 150 s was the shipped default before version 2. A config still at
            # exactly that never chose it, so it follows the new 60 s default.
            d["reminder"] = {**reminder, "distraction_grace_seconds": 60.0}
    d["version"] = CONFIG_VERSION
    return d


@dataclass
class Hotkeys:
    """pynput-style global hotkey specs. Empty string disables one."""

    toggle_session: str = "<ctrl>+<alt>+<cmd>+o"
    answer_yes: str = "<ctrl>+<alt>+<cmd>+y"
    answer_no: str = "<ctrl>+<alt>+<cmd>+n"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Hotkeys:
        d = d or {}
        return cls(
            toggle_session=str(_get(d, "toggle_session", cls.toggle_session)),
            answer_yes=str(_get(d, "answer_yes", cls.answer_yes)),
            answer_no=str(_get(d, "answer_no", cls.answer_no)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "toggle_session": self.toggle_session,
            "answer_yes": self.answer_yes,
            "answer_no": self.answer_no,
        }


@dataclass
class NoResponse:
    """What to do when a prompt is neither confirmed nor denied.

    policy:
      renag_then_no  - re-alert every `renag_seconds`, count as No after `max_alerts`
      wait           - leave the prompt up indefinitely
      pause_session  - assume the user stepped away and pause the session
    """

    policy: str = "renag_then_no"
    renag_seconds: float = 60.0
    max_alerts: int = 3

    POLICIES = ("renag_then_no", "wait", "pause_session")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NoResponse:
        d = d or {}
        return cls(
            policy=str(_get(d, "policy", cls.policy)),
            renag_seconds=float(_get(d, "renag_seconds", cls.renag_seconds)),
            max_alerts=int(_get(d, "max_alerts", cls.max_alerts)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "renag_seconds": self.renag_seconds,
            "max_alerts": self.max_alerts,
        }

    def normalize(self) -> None:
        if self.policy not in self.POLICIES:
            self.policy = "renag_then_no"
        self.renag_seconds = max(5.0, float(self.renag_seconds))
        self.max_alerts = max(1, int(self.max_alerts))


@dataclass
class ClockPenalty:
    """Time taken off the session clock when a check-in is answered No.

    A No means the stretch that just ended was not really work, so the clock
    stops crediting it. With `match_situation` on, a No costs what that stretch
    actually was: the blocked grace period, the off-task grace period, or
    `approved_seconds` for a plain cadence check-in where nothing was off
    limits. With it off, every No costs `fixed_seconds` instead.
    """

    enabled: bool = True
    match_situation: bool = True
    approved_seconds: float = 60.0
    fixed_seconds: float = 60.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClockPenalty:
        d = d or {}
        base = cls()
        return cls(
            enabled=bool(_get(d, "enabled", base.enabled)),
            match_situation=bool(_get(d, "match_situation", base.match_situation)),
            approved_seconds=float(_get(d, "approved_seconds", base.approved_seconds)),
            fixed_seconds=float(_get(d, "fixed_seconds", base.fixed_seconds)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "match_situation": self.match_situation,
            "approved_seconds": self.approved_seconds,
            "fixed_seconds": self.fixed_seconds,
        }

    def normalize(self) -> None:
        self.approved_seconds = max(0.0, float(self.approved_seconds))
        self.fixed_seconds = max(0.0, float(self.fixed_seconds))

    def seconds_for(self, kind: str, reminder: ReminderSettings) -> float:
        """How much a No costs, given which kind of check-in it answered.

        `kind` is the engine's prompt kind: "cadence", "distraction" or
        "blocked".
        """
        if not self.enabled:
            return 0.0
        if not self.match_situation:
            return self.fixed_seconds
        if kind == "blocked":
            return reminder.disapproved_grace_seconds
        if kind == "distraction":
            return reminder.distraction_grace_seconds
        return self.approved_seconds


@dataclass
class ReminderSettings:
    """The escalation ladder and the distraction timings.

    `intervals_minutes` is the ladder itself; `advance_after_yes[i]` is how many
    yes answers are needed at rung i before moving to rung i+1. The default
    1,2,2,3,3 ramps quickly at first and then asks for sustained focus.
    """

    intervals_minutes: list[float] = field(default_factory=lambda: [3, 5, 7, 10, 14, 20])
    advance_after_yes: list[int] = field(default_factory=lambda: [1, 2, 2, 3, 3])
    distraction_grace_seconds: float = 60.0
    disapproved_grace_seconds: float = 10.0
    suggest_approve_after_yes: int = 3
    no_response: NoResponse = field(default_factory=NoResponse)
    clock_penalty: ClockPenalty = field(default_factory=ClockPenalty)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReminderSettings:
        d = d or {}
        base = cls()
        return cls(
            intervals_minutes=[
                float(x) for x in _get(d, "intervals_minutes", base.intervals_minutes)
            ],
            advance_after_yes=[
                int(x) for x in _get(d, "advance_after_yes", base.advance_after_yes)
            ],
            distraction_grace_seconds=float(
                _get(d, "distraction_grace_seconds", base.distraction_grace_seconds)
            ),
            disapproved_grace_seconds=float(
                _get(d, "disapproved_grace_seconds", base.disapproved_grace_seconds)
            ),
            suggest_approve_after_yes=int(
                _get(d, "suggest_approve_after_yes", base.suggest_approve_after_yes)
            ),
            no_response=NoResponse.from_dict(_get(d, "no_response", {})),
            clock_penalty=ClockPenalty.from_dict(_get(d, "clock_penalty", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervals_minutes": self.intervals_minutes,
            "advance_after_yes": self.advance_after_yes,
            "distraction_grace_seconds": self.distraction_grace_seconds,
            "disapproved_grace_seconds": self.disapproved_grace_seconds,
            "suggest_approve_after_yes": self.suggest_approve_after_yes,
            "no_response": self.no_response.to_dict(),
            "clock_penalty": self.clock_penalty.to_dict(),
        }

    def normalize(self) -> None:
        rungs = [float(x) for x in self.intervals_minutes if float(x) > 0]
        self.intervals_minutes = rungs or [3.0]
        # advance_after_yes describes the gaps between rungs, so it is one shorter.
        needed = max(0, len(self.intervals_minutes) - 1)
        advance = [max(1, int(x)) for x in self.advance_after_yes][:needed]
        while len(advance) < needed:
            advance.append(advance[-1] if advance else 1)
        self.advance_after_yes = advance
        self.distraction_grace_seconds = max(5.0, float(self.distraction_grace_seconds))
        self.disapproved_grace_seconds = max(1.0, float(self.disapproved_grace_seconds))
        self.suggest_approve_after_yes = max(0, int(self.suggest_approve_after_yes))
        self.no_response.normalize()
        self.clock_penalty.normalize()


@dataclass
class GeneralSettings:
    poll_seconds: float = 2.0
    prompt_ui: str = "window"  # window | notification | both
    start_session_on_launch: bool = False
    play_sound: bool = True
    play_answer_sound: bool = True
    show_elapsed_in_menu_bar: bool = True
    prompt_position: str = "center"
    # Safari alone is configured out of the box. Everything else is added in
    # Settings by picking its .app, which is what makes the bundle id and the
    # URL-reading route right rather than guessed.
    browsers: list[Browser] = field(default_factory=default_browsers)
    hotkeys: Hotkeys = field(default_factory=Hotkeys)

    PROMPT_UIS = ("window", "notification", "both")
    # Where the floating check-in window sits on the screen it appears on.
    PROMPT_POSITIONS = (
        "center",
        "top_left",
        "top_center",
        "top_right",
        "bottom_left",
        "bottom_center",
        "bottom_right",
    )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GeneralSettings:
        d = d or {}
        base = cls()
        return cls(
            poll_seconds=float(_get(d, "poll_seconds", base.poll_seconds)),
            prompt_ui=str(_get(d, "prompt_ui", base.prompt_ui)),
            start_session_on_launch=bool(
                _get(d, "start_session_on_launch", base.start_session_on_launch)
            ),
            play_sound=bool(_get(d, "play_sound", base.play_sound)),
            play_answer_sound=bool(_get(d, "play_answer_sound", base.play_answer_sound)),
            prompt_position=str(_get(d, "prompt_position", base.prompt_position)),
            show_elapsed_in_menu_bar=bool(
                _get(d, "show_elapsed_in_menu_bar", base.show_elapsed_in_menu_bar)
            ),
            browsers=_read_browsers(_get(d, "browsers", base.browsers)),
            hotkeys=Hotkeys.from_dict(_get(d, "hotkeys", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "poll_seconds": self.poll_seconds,
            "prompt_ui": self.prompt_ui,
            "start_session_on_launch": self.start_session_on_launch,
            "play_sound": self.play_sound,
            "play_answer_sound": self.play_answer_sound,
            "prompt_position": self.prompt_position,
            "show_elapsed_in_menu_bar": self.show_elapsed_in_menu_bar,
            "browsers": [b.to_dict() for b in self.browsers],
            "hotkeys": self.hotkeys.to_dict(),
        }

    def normalize(self) -> None:
        self.poll_seconds = min(30.0, max(0.5, float(self.poll_seconds)))
        if self.prompt_ui not in self.PROMPT_UIS:
            self.prompt_ui = "window"
        if self.prompt_position not in self.PROMPT_POSITIONS:
            self.prompt_position = "center"
        # One entry per bundle id; adding the same app twice is a no-op.
        seen: set[str] = set()
        unique: list[Browser] = []
        for browser in self.browsers:
            key = browser.bundle_id or browser.name
            if key and key not in seen:
                seen.add(key)
                unique.append(browser)
        self.browsers = unique


@dataclass
class Profile:
    """A named work mode with its own allow/block lists.

    Rules are plain strings so they stay editable by hand; see `matching.py`
    for the accepted syntax (``app:Xcode``, ``site:github.com``, or bare).
    """

    name: str
    approved: list[str] = field(default_factory=list)
    disapproved: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Profile:
        d = d or {}
        return cls(
            name=str(_get(d, "name", "Untitled")),
            approved=[str(x) for x in _get(d, "approved", [])],
            disapproved=[str(x) for x in _get(d, "disapproved", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "approved": self.approved, "disapproved": self.disapproved}


def default_profiles() -> list[Profile]:
    return [
        Profile(
            name="Deep Work",
            approved=[
                "app:Code",
                "app:Terminal",
                "app:iTerm2",
                "app:Xcode",
                "site:github.com",
                "site:stackoverflow.com",
                "site:docs.python.org",
            ],
            disapproved=[
                "site:youtube.com",
                "site:reddit.com",
                "site:x.com",
                "site:news.ycombinator.com",
            ],
        ),
        Profile(
            name="Writing",
            approved=["app:Notion", "app:Obsidian", "app:Pages", "site:docs.google.com"],
            disapproved=["site:youtube.com", "site:reddit.com"],
        ),
    ]


@dataclass
class Config:
    version: int = CONFIG_VERSION
    active_profile: str = "Deep Work"
    # False until the first-run browser picker has been answered, so the picker
    # is offered once rather than on every launch.
    setup_complete: bool = False
    general: GeneralSettings = field(default_factory=GeneralSettings)
    reminder: ReminderSettings = field(default_factory=ReminderSettings)
    profiles: list[Profile] = field(default_factory=default_profiles)
    path: Path | None = field(default=None, compare=False, repr=False)

    # -- persistence ------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        d = _migrate(d or {})
        profiles = [Profile.from_dict(p) for p in _get(d, "profiles", [])]
        cfg = cls(
            version=int(_get(d, "version", CONFIG_VERSION)),
            active_profile=str(_get(d, "active_profile", "")),
            setup_complete=bool(_get(d, "setup_complete", False)),
            general=GeneralSettings.from_dict(_get(d, "general", {})),
            reminder=ReminderSettings.from_dict(_get(d, "reminder", {})),
            profiles=profiles or default_profiles(),
        )
        cfg.normalize()
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "active_profile": self.active_profile,
            "setup_complete": self.setup_complete,
            "general": self.general.to_dict(),
            "reminder": self.reminder.to_dict(),
            "profiles": [p.to_dict() for p in self.profiles],
        }

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read config from disk, falling back to defaults on a missing or bad file."""
        default = default_config_path()
        path = Path(path) if path else default
        if path == default:
            # OnTask's own folder: make sure it is private, including one
            # created by an older version with default permissions.
            ensure_private_dir(path.parent, tighten_existing=True)
        try:
            raw = json.loads(read_capped(path, MAX_CONFIG_BYTES))
            make_private(path)
        except FileNotFoundError:
            cfg = cls()
            cfg.path = path
            cfg.save()
            return cfg
        except (json.JSONDecodeError, OSError, ValueError):
            # A corrupt file should not stop the app from starting; keep a
            # private copy for inspection.
            bad = path.with_suffix(".json.bad")
            try:
                path.replace(bad)
                os.chmod(bad, 0o600)
            except OSError:
                pass
            cfg = cls()
            cfg.path = path
            return cfg
        cfg = cls.from_dict(raw)
        cfg.path = path
        if isinstance(raw, dict) and raw.get("version") != CONFIG_VERSION:
            # Persist the migration so it happens once, not on every load.
            try:
                cfg.save()
            except OSError:
                pass
        return cfg

    def save(self, path: Path | None = None) -> Path:
        """Atomically write config to disk so a crash cannot truncate it."""
        target = Path(path) if path else (self.path or default_config_path())
        write_private(target, json.dumps(self.to_dict(), indent=2) + "\n")
        self.path = target
        return target

    # -- helpers ----------------------------------------------------------

    def normalize(self) -> None:
        self.general.normalize()
        self.reminder.normalize()
        if not self.profiles:
            self.profiles = default_profiles()
        seen: set[str] = set()
        for p in self.profiles:
            base_name, n = p.name, 2
            while p.name in seen:
                p.name = f"{base_name} {n}"
                n += 1
            seen.add(p.name)
        if self.active_profile not in seen:
            self.active_profile = self.profiles[0].name

    def profile(self, name: str | None = None) -> Profile:
        want = name or self.active_profile
        for p in self.profiles:
            if p.name == want:
                return p
        return self.profiles[0]

    def profile_names(self) -> list[str]:
        return [p.name for p in self.profiles]
