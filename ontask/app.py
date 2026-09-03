"""Controller wiring the engine to whatever UI shell is running.

The shell (rumps menu bar on macOS, Tk elsewhere) owns the event loop and calls
`poll()` on a timer. Everything else - config reloading, focus sampling, hotkey
marshalling, editing the approved list - lives here so both shells stay thin.
"""

from __future__ import annotations

import queue
import subprocess
import sys
from pathlib import Path

from . import engine as eng
from .config import Config, Profile
from .engine import Engine, format_duration
from .focus import UNKNOWN, FocusTarget, get_provider
from .matching import classify, suggest_rule


class Shell:
    """Callbacks a UI must provide. Defaults make it safe to implement partially."""

    def show_prompt(self, prompt: eng.ActivePrompt, controller: "Controller") -> None: ...

    def realert(self, prompt: eng.ActivePrompt) -> None: ...

    def close_prompt(self) -> None: ...

    def notify(self, title: str, message: str) -> None: ...

    def ask_add_rule(self, target: FocusTarget, rule: str, controller: "Controller") -> None: ...

    def refresh(self) -> None: ...


class Controller:
    def __init__(self, shell: Shell | None = None, config_path: Path | None = None):
        self.config = Config.load(config_path)
        self.engine = Engine(self.config)
        self.focus = get_provider()
        self.shell = shell or Shell()
        self.target: FocusTarget = UNKNOWN
        self._actions: queue.Queue[tuple] = queue.Queue()
        self._config_mtime = self._mtime()
        if self.config.general.start_session_on_launch:
            self.engine.start()

    def attach(self, shell: Shell) -> None:
        self.shell = shell

    # -- thread-safe action queue ----------------------------------------

    def post(self, action: str, *args) -> None:
        """Queue an action from a non-UI thread (global hotkeys land here)."""
        self._actions.put((action, args))

    def _drain(self) -> None:
        while True:
            try:
                action, args = self._actions.get_nowait()
            except queue.Empty:
                return
            handler = getattr(self, action, None)
            if callable(handler):
                handler(*args)

    # -- main timer -------------------------------------------------------

    def poll(self) -> None:
        self._drain()
        self.reload_if_changed()
        try:
            self.target = self.focus.current(self.config.general.browsers)
        except Exception:
            self.target = UNKNOWN
        self._handle(self.engine.tick(target=self.target))

    def _handle(self, events) -> None:
        for event in events:
            if isinstance(event, eng.ShowPrompt):
                self.shell.show_prompt(event.prompt, self)
            elif isinstance(event, eng.RealertPrompt):
                self.shell.realert(event.prompt)
            elif isinstance(event, eng.ClosePrompt):
                self.shell.close_prompt()
            elif isinstance(event, eng.SuggestApprove):
                self.shell.ask_add_rule(event.target, event.rule, self)
            elif isinstance(event, eng.Answered):
                self._announce(event)
            elif isinstance(event, eng.SessionChanged):
                self.shell.refresh()
        if events:
            self.shell.refresh()

    def _announce(self, event: eng.Answered) -> None:
        if event.ignored:
            self.shell.notify("OnTask", "No answer - reminders reset to the shortest interval.")
        elif event.advanced:
            minutes = _fmt(event.interval_minutes)
            self.shell.notify("OnTask", f"Nice. Next check-in in {minutes} minutes.")

    # -- session control --------------------------------------------------

    def toggle_session(self) -> None:
        self._handle(self.engine.toggle())

    def start_session(self) -> None:
        self._handle(self.engine.start())

    def stop_session(self) -> None:
        self._handle(self.engine.stop())

    def pause_or_resume(self) -> None:
        if self.engine.phase == eng.RUNNING:
            self._handle(self.engine.pause())
        elif self.engine.phase == eng.PAUSED:
            self._handle(self.engine.resume())

    def answer(self, yes: bool) -> None:
        self._handle(self.engine.answer(yes))

    def answer_yes(self) -> None:
        self.answer(True)

    def answer_no(self) -> None:
        self.answer(False)

    def set_profile(self, name: str) -> None:
        self._handle(self.engine.set_profile(name))
        self.config.save()
        self.shell.refresh()

    # -- list editing -----------------------------------------------------

    def add_rule(self, rule: str, listname: str = "approved", profile: str | None = None) -> None:
        target_profile: Profile = self.config.profile(profile)
        entries = getattr(target_profile, listname)
        if rule not in entries:
            entries.append(rule)
        other = "disapproved" if listname == "approved" else "approved"
        opposite = getattr(target_profile, other)
        if rule in opposite:
            opposite.remove(rule)
        self.config.save()
        self._config_mtime = self._mtime()
        self.engine.apply_config(self.config)
        self.shell.refresh()

    def approve_current(self) -> None:
        if not self.target.is_unknown:
            rule = suggest_rule(self.target)
            self.add_rule(rule, "approved")
            self.shell.notify("OnTask", f"Added {rule} to {self.config.active_profile}.")

    def block_current(self) -> None:
        if not self.target.is_unknown:
            rule = suggest_rule(self.target)
            self.add_rule(rule, "disapproved")
            self.shell.notify("OnTask", f"Blocked {rule} in {self.config.active_profile}.")

    # -- settings ---------------------------------------------------------

    def open_settings(self) -> None:
        path = str(self.config.path or "")
        # A separate process keeps Tk off the menu bar app's run loop.
        subprocess.Popen(
            [sys.executable, "-m", "ontask.ui.settings_app", path],
            cwd=str(Path(__file__).resolve().parents[1]),
        )

    def _mtime(self) -> float:
        try:
            return self.config.path.stat().st_mtime if self.config.path else 0.0
        except OSError:
            return 0.0

    def reload_if_changed(self) -> None:
        """Pick up edits made by the settings window without restarting."""
        mtime = self._mtime()
        if mtime and mtime != self._config_mtime:
            self._config_mtime = mtime
            self.config = Config.load(self.config.path)
            self.engine.apply_config(self.config)
            self.shell.refresh()

    # -- display ----------------------------------------------------------

    def status_title(self) -> str:
        snap = self.engine.snapshot()
        if snap.phase == eng.RUNNING:
            return format_duration(snap.elapsed_seconds)
        if snap.phase == eng.PAUSED:
            return "paused"
        return ""

    def status_lines(self) -> list[str]:
        snap = self.engine.snapshot()
        if snap.phase == eng.IDLE:
            return ["No session running", f"Profile: {snap.profile}"]
        lines = [
            f"Session: {format_duration(snap.elapsed_seconds)}"
            + (" (paused)" if snap.phase == eng.PAUSED else ""),
            f"Profile: {snap.profile}",
            f"Interval: {snap.ladder_text}",
        ]
        if snap.phase == eng.RUNNING and not snap.prompt_open:
            lines.append(f"Next check-in: {format_duration(snap.next_prompt_seconds)}")
        lines.append(f"Focus: {self.current_status_text()}")
        return lines

    def current_status_text(self) -> str:
        if self.target.is_unknown:
            return "unknown"
        profile = self.config.profile()
        status = classify(self.target, profile.approved, profile.disapproved).status
        return f"{self.target.describe()} - {status}"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
