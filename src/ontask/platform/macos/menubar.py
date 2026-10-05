"""macOS menu bar shell.

rumps owns the run loop; a repeating timer drives `Controller.poll()`. A
check-in is either an AppKit panel or an actionable notification, depending on
`prompt_ui`, and the panel is always the fallback: a banner that cannot be
delivered must never leave a question the user has no way to answer.
"""

from __future__ import annotations

from pathlib import Path

import objc
import rumps
from Foundation import NSObject

from ...app import Controller
from ...core.engine import IDLE, PAUSED, RUNNING, ActivePrompt, format_duration
from ...hotkeys import HotkeyManager
from .notify import NOT_DETERMINED, Notifier
from .prompt import PromptWindow

STATUS_SLOTS = ("session", "profile", "interval", "next", "focus")

# The menu bar clock is redrawn on its own timer. Tying it to the focus poll
# made the seconds jump in whatever step `poll_seconds` happened to be.
CLOCK_SECONDS = 1.0


class _Waker(NSObject):
    """Hops a background thread's request onto the main thread.

    Global hotkeys arrive on pynput's listener thread, and AppKit may only be
    touched from the main one, so the callback is bounced rather than run where
    it lands.
    """

    def initWithCallback_(self, callback):
        self = objc.super(_Waker, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def wake(self) -> None:
        self.performSelectorOnMainThread_withObject_waitUntilDone_("fire:", None, False)

    def fire_(self, _ignored):
        try:
            self._callback()
        except Exception:
            pass


class OnTaskApp(rumps.App):
    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__("OnTask", title="OnTask", quit_button=None)
        # `shell` is OnTask's UI shell, not subprocess's shell flag.
        self.controller = Controller(shell=self, config_path=config_path)  # nosec B604
        self.prompt = PromptWindow(self._on_answer)
        self.notifier = Notifier(self._on_answer)
        self.hotkeys = HotkeyManager(self.controller)
        self._pending: ActivePrompt | None = None
        self._profile_items: dict[str, rumps.MenuItem] = {}
        self._warned_notifications = False
        self._poll_seconds = self.controller.config.general.poll_seconds
        self._waker = _Waker.alloc().initWithCallback_(self._wake)
        self.controller.set_wake_hook(self._waker.wake)
        self._build_menu()
        self.hotkeys.start()
        self.refresh()
        self.timer = rumps.Timer(self._tick, self._poll_seconds)
        self.timer.start()
        if not self.controller.config.setup_complete:
            self.controller.open_first_run()
        # A second, faster timer keeps the clock ticking every second without
        # sampling the frontmost window that often.
        self.clock_timer = rumps.Timer(self._clock_tick, CLOCK_SECONDS)
        self.clock_timer.start()

    # -- menu -------------------------------------------------------------

    def _build_menu(self) -> None:
        self.status_items = {key: rumps.MenuItem("") for key in STATUS_SLOTS}
        self.toggle_item = rumps.MenuItem("Start Session", callback=self._toggle)
        self.pause_item = rumps.MenuItem("Pause", callback=self._pause)
        self.profile_menu = rumps.MenuItem("Profile")
        self.approve_item = rumps.MenuItem("Approve Current", callback=self._approve)
        self.block_item = rumps.MenuItem("Block Current", callback=self._block)
        self.menu = [
            *self.status_items.values(),
            None,
            self.toggle_item,
            self.pause_item,
            None,
            self.profile_menu,
            self.approve_item,
            self.block_item,
            None,
            rumps.MenuItem("Settings...", callback=self._settings),
            rumps.MenuItem("Statistics...", callback=self._statistics),
            rumps.MenuItem("Permissions...", callback=self._permissions),
            rumps.MenuItem("Quit OnTask", callback=self._quit),
        ]
        self._rebuild_profiles()

    def _rebuild_profiles(self) -> None:
        # rumps creates a submenu's NSMenu lazily on first add, so clearing an
        # untouched MenuItem would blow up.
        if self._profile_items:
            self.profile_menu.clear()
        self._profile_items = {}
        for name in self.controller.config.profile_names():
            item = rumps.MenuItem(name, callback=self._pick_profile)
            self.profile_menu.add(item)
            self._profile_items[name] = item

    # -- menu actions -----------------------------------------------------

    def _toggle(self, _sender) -> None:
        self.controller.toggle_session()

    def _pause(self, _sender) -> None:
        self.controller.pause_or_resume()

    def _approve(self, _sender) -> None:
        self.controller.approve_current()

    def _block(self, _sender) -> None:
        self.controller.block_current()

    def _settings(self, _sender) -> None:
        self.controller.open_settings()

    def _statistics(self, _sender) -> None:
        self.controller.open_stats()

    def _permissions(self, _sender) -> None:
        rumps.alert(title="OnTask permissions", message=self.permissions_report(), ok="Done")

    def _pick_profile(self, sender) -> None:
        self.controller.set_profile(sender.title)

    def _quit(self, _sender) -> None:
        self.hotkeys.stop()
        self.controller.shutdown()
        rumps.quit_application()

    def _tick(self, _timer) -> None:
        self.controller.poll()
        self.refresh()

    def _clock_tick(self, _timer) -> None:
        """Redraw only. The engine is not advanced here; `poll` owns timing."""
        self.refresh()

    def _wake(self) -> None:
        """Run queued hotkey actions at once, on the main thread."""
        self.controller.wake()
        self.refresh()

    def _on_answer(self, yes: bool) -> None:
        self.controller.answer(yes)

    # -- prompt routing ---------------------------------------------------

    def _effective_mode(self, mode: str) -> str:
        """Resolve the configured style against what can actually be delivered."""
        if mode == "window":
            return "window"
        notifier = self.notifier
        if notifier.status in (None, NOT_DETERMINED):
            # Raise the system prompt, but show the window for this check-in so
            # the question is answerable while permission is still undecided.
            notifier.request_authorization()
            return "window"
        if not notifier.available():
            self._warn_notifications_once()
            return "window"
        return mode

    def _warn_notifications_once(self) -> None:
        if self._warned_notifications:
            return
        self._warned_notifications = True
        rumps.alert(
            title="Notifications are unavailable",
            message=(
                f"Notifications are {self.notifier.status_text()}, so OnTask is using the "
                "floating window instead.\n\nEnable them in System Settings > Notifications, "
                "or set the check-in style to Floating window in Settings to hide this."
            ),
            ok="OK",
        )

    # -- Shell interface --------------------------------------------------

    def show_prompt(self, prompt: ActivePrompt, controller: Controller) -> None:
        self._pending = prompt
        general = controller.config.general
        mode = self._effective_mode(general.prompt_ui)
        if mode in ("notification", "both"):
            self.notifier.show(
                "OnTask", prompt.question(), actionable=True, sound=general.play_sound
            )
        else:
            self.prompt.show(
                prompt.question(),
                self._subtitle(prompt),
                general.play_sound,
                general.prompt_position,
            )

    def realert(self, prompt: ActivePrompt) -> None:
        general = self.controller.config.general
        mode = self._effective_mode(general.prompt_ui)
        if mode == "notification":
            self.notifier.show(
                "OnTask", prompt.question(), actionable=True, sound=general.play_sound
            )
            return
        # "both" deliberately escalates: banner first, then the window.
        if not self.prompt.visible:
            self.prompt.show(
                prompt.question(),
                self._subtitle(prompt),
                general.play_sound,
                general.prompt_position,
            )
        else:
            self.prompt.realert(self._subtitle(prompt), general.play_sound, general.prompt_position)

    def answer_feedback(self, yes: bool) -> None:
        self.prompt.flash_answer(yes, self.controller.config.general.play_answer_sound)

    def close_prompt(self) -> None:
        self._pending = None
        self.prompt.close()
        self.notifier.withdraw()

    def notify(self, title: str, message: str) -> None:
        if self.notifier.available():
            self.notifier.show(title, message, actionable=False, sound=False)

    def ask_add_rule(self, target, rule: str, controller: Controller) -> None:
        response = rumps.alert(
            title="Add to approved list?",
            message=controller.suggestion_message(target, rule),
            ok="Add",
            cancel="Not now",
        )
        if response == 1:
            controller.add_rule(rule, "approved")

    def refresh(self) -> None:
        snap = self.controller.snapshot()
        general = self.controller.config.general
        if general.show_elapsed_in_menu_bar and snap.phase != IDLE:
            self.title = (
                f"{'*' if snap.phase == RUNNING else '||'} {format_duration(snap.elapsed_seconds)}"
            )
        else:
            self.title = "OnTask"

        lines = {
            "session": (
                "No session running"
                if snap.phase == IDLE
                else f"Session: {format_duration(snap.elapsed_seconds)}"
                + (" (paused)" if snap.phase == PAUSED else "")
            ),
            "profile": f"Profile: {snap.profile}",
            "interval": f"Interval: {snap.ladder_text}",
            "next": (
                f"Next check-in: {format_duration(snap.next_prompt_seconds)}"
                if snap.phase == RUNNING and not snap.prompt_open
                else "Next check-in: --"
            ),
            "focus": f"Focus: {self.controller.current_status_text()}",
        }
        for key, text in lines.items():
            self.status_items[key].title = text

        self.toggle_item.title = "End Session" if snap.phase != IDLE else "Start Session"
        self.pause_item.title = "Resume" if snap.phase == PAUSED else "Pause"
        self.pause_item.set_callback(self._pause if snap.phase != IDLE else None)
        label = self.controller.target.label() if not self.controller.target.is_unknown else ""
        self.approve_item.title = f"Approve {label}" if label else "Approve Current"
        self.block_item.title = f"Block {label}" if label else "Block Current"

        if set(self._profile_items) != set(self.controller.config.profile_names()):
            self._rebuild_profiles()
        for name, item in self._profile_items.items():
            item.state = 1 if name == snap.profile else 0

        if general.poll_seconds != self._poll_seconds:
            self._poll_seconds = general.poll_seconds
            self.timer.interval = self._poll_seconds

    # -- diagnostics ------------------------------------------------------

    def permissions_report(self) -> str:
        from .ax import accessibility_trusted

        lines = [
            f"Notifications: {self.notifier.status_text()}",
            f"Accessibility: {'granted' if accessibility_trusted() else 'not granted'}",
            f"Global hotkeys: {'active' if self.hotkeys.available() else 'inactive'}"
            + (f" - {self.hotkeys.error}" if self.hotkeys.error else ""),
        ]
        hint = getattr(self.controller.focus, "permission_hint", lambda: "")()
        if hint:
            lines.append("")
            lines.append(hint)
        return "\n".join(lines)

    def _subtitle(self, prompt: ActivePrompt) -> str:
        snap = self.controller.snapshot()
        return f"{snap.profile} - {format_duration(snap.elapsed_seconds)} elapsed"


def _hide_dock_icon() -> None:
    """Run as a menu bar accessory, with no Dock tile or app switcher entry.

    A built bundle gets this from LSUIElement in its Info.plist; setting it at
    runtime means running from source behaves the same instead of showing a
    stray Python icon.
    """
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass


def _running_app():
    return getattr(rumps.App, "*app_instance", None)


def _install_delegate() -> None:
    """Add reopen and terminate handling to rumps' application delegate.

    Two things AppKit tells the delegate that OnTask needs:

    * a *reopen*, which is what launching an already-running app produces -
      macOS never starts a second copy of a bundled app, so this is the only
      way a Spotlight launch can reach the instance that is running;
    * a *terminate*, which is how logging out or restarting asks apps to stop,
      and the last chance to bank the session that is still going.

    Signals are not an option here: under NSApplication.run() a Python-level
    SIGTERM handler is never invoked, whichever thread installs it.

    rumps builds its delegate from a module-level class, so substituting a
    subclass adds both without forking rumps.
    """
    try:
        base = rumps.rumps.NSApp
    except Exception:
        return

    class OnTaskNSApp(base):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, has_windows):
            app = _running_app()
            if app is not None:
                try:
                    app.controller.open_settings()
                except Exception:
                    pass
            return True

        def applicationWillTerminate_(self, notification):
            app = _running_app()
            if app is not None:
                try:
                    app.hotkeys.stop()
                    app.controller.shutdown()
                except Exception:
                    pass

    try:
        rumps.rumps.NSApp = OnTaskNSApp
    except Exception:
        pass


def run(config_path: Path | None = None) -> None:
    _hide_dock_icon()
    _install_delegate()
    OnTaskApp(config_path).run()
