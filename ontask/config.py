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

from .browsers import Browser, coerce as coerce_browser, default_browsers

CONFIG_VERSION = 1


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


@dataclass
class Hotkeys:
    """pynput-style global hotkey specs. Empty string disables one."""

    toggle_session: str = "<ctrl>+<alt>+<cmd>+o"
    answer_yes: str = "<ctrl>+<alt>+<cmd>+y"
    answer_no: str = "<ctrl>+<alt>+<cmd>+n"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Hotkeys":
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
    def from_dict(cls, d: dict[str, Any]) -> "NoResponse":
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
class ReminderSettings:
    """The escalation ladder and the distraction timings.

    `intervals_minutes` is the ladder itself; `advance_after_yes[i]` is how many
    yes answers are needed at rung i before moving to rung i+1. The default
    1,2,2,3,3 ramps quickly at first and then asks for sustained focus.
    """

    intervals_minutes: list[float] = field(default_factory=lambda: [3, 5, 7, 10, 14, 20])
    advance_after_yes: list[int] = field(default_factory=lambda: [1, 2, 2, 3, 3])
    distraction_grace_seconds: float = 150.0
    disapproved_grace_seconds: float = 10.0
    suggest_approve_after_yes: int = 3
    no_response: NoResponse = field(default_factory=NoResponse)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReminderSettings":
        d = d or {}
        base = cls()
        return cls(
            intervals_minutes=[float(x) for x in _get(d, "intervals_minutes", base.intervals_minutes)],
            advance_after_yes=[int(x) for x in _get(d, "advance_after_yes", base.advance_after_yes)],
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
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervals_minutes": self.intervals_minutes,
            "advance_after_yes": self.advance_after_yes,
            "distraction_grace_seconds": self.distraction_grace_seconds,
            "disapproved_grace_seconds": self.disapproved_grace_seconds,
            "suggest_approve_after_yes": self.suggest_approve_after_yes,
            "no_response": self.no_response.to_dict(),
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


@dataclass
class GeneralSettings:
    poll_seconds: float = 2.0
    prompt_ui: str = "window"  # window | notification | both
    start_session_on_launch: bool = False
    play_sound: bool = True
    show_elapsed_in_menu_bar: bool = True
    # Safari alone is configured out of the box. Everything else is added in
    # Settings by picking its .app, which is what makes the bundle id and the
    # URL-reading route right rather than guessed.
    browsers: list[Browser] = field(default_factory=default_browsers)
    hotkeys: Hotkeys = field(default_factory=Hotkeys)

    PROMPT_UIS = ("window", "notification", "both")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GeneralSettings":
        d = d or {}
        base = cls()
        return cls(
            poll_seconds=float(_get(d, "poll_seconds", base.poll_seconds)),
            prompt_ui=str(_get(d, "prompt_ui", base.prompt_ui)),
            start_session_on_launch=bool(
                _get(d, "start_session_on_launch", base.start_session_on_launch)
            ),
            play_sound=bool(_get(d, "play_sound", base.play_sound)),
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
            "show_elapsed_in_menu_bar": self.show_elapsed_in_menu_bar,
            "browsers": [b.to_dict() for b in self.browsers],
            "hotkeys": self.hotkeys.to_dict(),
        }

    def normalize(self) -> None:
        self.poll_seconds = min(30.0, max(0.5, float(self.poll_seconds)))
        if self.prompt_ui not in self.PROMPT_UIS:
            self.prompt_ui = "window"
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
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
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
            disapproved=["site:youtube.com", "site:reddit.com", "site:x.com", "site:news.ycombinator.com"],
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
    general: GeneralSettings = field(default_factory=GeneralSettings)
    reminder: ReminderSettings = field(default_factory=ReminderSettings)
    profiles: list[Profile] = field(default_factory=default_profiles)
    path: Path | None = field(default=None, compare=False, repr=False)

    # -- persistence ------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        d = d or {}
        profiles = [Profile.from_dict(p) for p in _get(d, "profiles", [])]
        cfg = cls(
            version=int(_get(d, "version", CONFIG_VERSION)),
            active_profile=str(_get(d, "active_profile", "")),
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
            "general": self.general.to_dict(),
            "reminder": self.reminder.to_dict(),
            "profiles": [p.to_dict() for p in self.profiles],
        }

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Read config from disk, falling back to defaults on a missing or bad file."""
        path = Path(path) if path else default_config_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            cfg = cls()
            cfg.path = path
            cfg.save()
            return cfg
        except (json.JSONDecodeError, OSError):
            # A corrupt file should not stop the app from starting; keep a copy.
            try:
                path.replace(path.with_suffix(".json.bad"))
            except OSError:
                pass
            cfg = cls()
            cfg.path = path
            return cfg
        cfg = cls.from_dict(raw)
        cfg.path = path
        return cfg

    def save(self, path: Path | None = None) -> Path:
        """Atomically write config to disk so a crash cannot truncate it."""
        target = Path(path) if path else (self.path or default_config_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
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
