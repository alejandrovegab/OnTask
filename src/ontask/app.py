"""Controller wiring the engine to whatever UI shell is running.

The shell (rumps menu bar on macOS, Tk elsewhere) owns the event loop and calls
`poll()` on a timer. Everything else - config reloading, focus sampling, hotkey
marshalling, editing the approved list - lives here so both shells stay thin.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import time
from pathlib import Path

from . import engine as eng
from . import ipc
from .config import Config, Profile
from .engine import Engine, format_duration
from .focus import UNKNOWN, FocusTarget, get_provider
from .matching import classify, suggest_rule
from .stats import Stats, default_stats_path


class Shell:
    """Callbacks a UI must provide. Defaults make it safe to implement partially."""

    def show_prompt(self, prompt: eng.ActivePrompt, controller: Controller) -> None: ...

    def realert(self, prompt: eng.ActivePrompt) -> None: ...

    def close_prompt(self) -> None: ...

    def answer_feedback(self, yes: bool) -> None:
        """Acknowledge an answer before the prompt is torn down.

        Called for every route into an answer - button, hotkey, notification -
        so the UI can confirm which option was taken.
        """

    def notify(self, title: str, message: str) -> None: ...

    def ask_add_rule(self, target: FocusTarget, rule: str, controller: Controller) -> None: ...

    def refresh(self) -> None: ...


class Controller:
    def __init__(self, shell: Shell | None = None, config_path: Path | None = None):
        self.config = Config.load(config_path)
        self.engine = Engine(self.config)
        self.focus = get_provider()
        self.shell = shell or Shell()
        self.target: FocusTarget = UNKNOWN
        self.stats = Stats.load(default_stats_path(self.config.path))
        self._actions: queue.Queue[tuple] = queue.Queue()
        self._config_mtime = self._mtime()
        # Set by the shell so a hotkey can be acted on at once instead of
        # waiting for the next poll; see `post`.
        self._wake_hook = None
        self._runtime_dir = ipc.runtime_dir(self.config.path)
        self._open_settings_signal = ipc.Signal(self._runtime_dir, ipc.OPEN_SETTINGS)
        self._stats_cleared_signal = ipc.Signal(self._runtime_dir, ipc.STATS_CLEARED)
        self._settings_proc = None
        self._stats_proc = None
        self._setup_proc = None
        if self.config.general.start_session_on_launch:
            self.engine.start()

    def attach(self, shell: Shell) -> None:
        self.shell = shell

    # -- thread-safe action queue ----------------------------------------

    def set_wake_hook(self, hook) -> None:
        """Register the shell's "run the queue now" callback.

        Without it a hotkey waits for the next poll, which is why answering used
        to take a visible moment to dismiss the check-in.
        """
        self._wake_hook = hook

    def post(self, action: str, *args) -> None:
        """Queue an action from a non-UI thread (global hotkeys land here)."""
        self._actions.put((action, args))
        hook = self._wake_hook
        if hook is not None:
            try:
                hook()
            except Exception:
                pass

    def wake(self) -> None:
        """Run whatever is queued right now. Called on the UI thread."""
        self._drain()

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
        if self._open_settings_signal.received():
            # A second launch (Spotlight, Dock, another terminal) asking the
            # instance that is already running to show itself.
            self.open_settings()
        if self._stats_cleared_signal.received():
            self.stats.forget_before(self._stats_cleared_signal.sent_at())
            self.stats.maybe_save(force=True)
        try:
            self.target = self.focus.current(self.config.general.browsers)
        except Exception:
            self.target = UNKNOWN
        self._handle(self.engine.tick(target=self.target))
        self.stats.maybe_save()

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
                self.stats.record_answer(
                    event.kind,
                    event.yes,
                    event.ignored,
                    event.target_key,
                    event.target_label,
                    self.config.active_profile,
                )
                self._announce(event)
            elif isinstance(event, eng.OffTaskLogged):
                self.stats.record_off_task(
                    event.key,
                    event.label,
                    event.seconds,
                    event.blocked,
                    self.config.active_profile,
                )
            elif isinstance(event, eng.Recovered):
                self.stats.record_recovered(event.key, event.label, self.config.active_profile)
            elif isinstance(event, eng.SessionChanged):
                if event.phase == eng.IDLE:
                    self.stats.record_session(event.elapsed_seconds, event.profile)
                    self.stats.maybe_save(force=True)
                self.shell.refresh()
        if events:
            self.shell.refresh()

    def _announce(self, event: eng.Answered) -> None:
        if event.ignored:
            message = "No answer - reminders reset to the shortest interval."
            if event.penalty_seconds > 0:
                # An unanswered check-in counts as a No, so the clock can drop;
                # saying so is what keeps that from looking like a glitch.
                message = (
                    "No answer - reminders reset and "
                    f"{format_duration(event.penalty_seconds)} taken off the session clock."
                )
            self.shell.notify("OnTask", message)
        elif event.advanced:
            minutes = _fmt(event.interval_minutes)
            self.shell.notify("OnTask", f"Nice. Next check-in in {minutes} minutes.")
        elif event.penalty_seconds > 0:
            self.shell.notify(
                "OnTask", f"{format_duration(event.penalty_seconds)} taken off the session clock."
            )

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
        # Told before the engine runs, because answering emits ClosePrompt and
        # the shell needs to know which option to acknowledge first.
        if self.engine.active_prompt is not None:
            self.shell.answer_feedback(yes)
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

    def suggestion_message(self, target: FocusTarget, rule: str) -> str:
        """The question asked when offering to approve `target`."""
        count = self.config.reminder.suggest_approve_after_yes
        return (
            f"You've said you're on task in {target.describe()} {_times(count)}.\n\n"
            f"Add {rule} to the approved list for {self.config.active_profile}?"
        )

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
        self._open_window("ontask.ui.settings_app", "_settings_proc", ipc.RAISE_SETTINGS)

    def open_first_run(self) -> None:
        """Offer the browser picker once, on the first launch."""
        self._open_window("ontask.ui.browser_setup", "_setup_proc", ipc.RAISE_SETUP)

    def open_stats(self) -> None:
        self.stats.maybe_save(force=True)
        self._open_window("ontask.ui.stats_app", "_stats_proc", ipc.RAISE_STATS)

    def _open_window(self, module: str, attribute: str, raise_marker: str) -> None:
        """Show a helper window, reusing the one already open.

        Spawning a second copy would strand the user's unsaved edits in a window
        hidden behind the new one, so a live process is nudged to the front
        instead.
        """
        existing = getattr(self, attribute, None)
        if existing is not None and existing.poll() is None:
            ipc.Signal(self._runtime_dir, raise_marker).send()
            return
        path = str(self.config.path or "")
        # A separate process keeps Tk off the menu bar app's run loop.
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", module, path],
                cwd=str(Path(__file__).resolve().parents[1]),
            )
        except OSError:
            return
        setattr(self, attribute, proc)

    def shutdown(self) -> None:
        """Bank the running session before the app goes away."""
        if self.engine.phase != eng.IDLE:
            self._handle(self.engine.stop())
        self.stats.maybe_save(force=True)

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

    def snapshot(self) -> eng.Snapshot:
        """Engine state with the clock brought up to the current instant."""
        return self.engine.snapshot(time.monotonic())

    def status_title(self) -> str:
        snap = self.snapshot()
        if snap.phase == eng.RUNNING:
            return format_duration(snap.elapsed_seconds)
        if snap.phase == eng.PAUSED:
            return "paused"
        return ""

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
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


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
