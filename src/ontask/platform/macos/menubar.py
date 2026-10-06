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
from AppKit import NSAttributedString, NSFont, NSFontAttributeName, NSFontWeightRegular
from Foundation import NSDate, NSObject, NSRunLoop, NSRunLoopCommonModes

from ...app import Controller
from ...core.engine import IDLE, PAUSED, RUNNING, ActivePrompt, format_duration
from ...hotkeys import HotkeyManager
from .notify import NOT_DETERMINED, Notifier
from .prompt import PromptWindow

STATUS_SLOTS = ("session", "profile", "interval", "next", "focus")

# The menu bar clock is redrawn on its own timer. Tying it to the focus poll
# made the seconds jump in whatever step `poll_seconds` happened to be.
CLOCK_SECONDS = 1.0

# With no session running (or paused), the poll only checks for a second launch
# and for saved settings, so it keeps this pace whatever `poll_seconds` is.
IDLE_POLL_SECONDS = 2.0

# How far the clock's redraw may wander from mid-second before it is moved back.
CLOCK_SLACK_SECONDS = 0.25


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


class _MenuOpening(NSObject):
    """The status menu's delegate: says when the menu is about to appear."""

    def initWithCallback_(self, callback):
        self = objc.super(_MenuOpening, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def menuWillOpen_(self, _menu):
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
        self._shown_title: str | None = None
        self._waker = _Waker.alloc().initWithCallback_(self._wake)
        self.controller.set_wake_hook(self._waker.wake)
        self._build_menu()
        # AppKit holds a menu's delegate weakly, so this reference keeps it alive.
        self._menu_opening = _MenuOpening.alloc().initWithCallback_(self._menu_will_open)
        self._menu._menu.setDelegate_(self._menu_opening)
        self.hotkeys.start()
        # `refresh` starts the timers, at the pace the session state calls for.
        self.timer = rumps.Timer(self._tick, IDLE_POLL_SECONDS)
        self._poll_seconds: float | None = None
        # A second, faster timer keeps the clock ticking every second without
        # sampling the frontmost window that often.
        self.clock_timer = rumps.Timer(self._clock_tick, CLOCK_SECONDS)
        self.refresh()
        if not self.controller.config.setup_complete:
            self.controller.open_first_run()

    # -- menu -------------------------------------------------------------

    def _build_menu(self) -> None:
        self.status_items = {key: rumps.MenuItem("") for key in STATUS_SLOTS}
        self.toggle_item = rumps.MenuItem("Start Session", callback=self._toggle)
        self.pause_item = rumps.MenuItem("Pause", callback=self._pause)
        self.profile_menu = rumps.MenuItem("Profile")
        self.approve_item = rumps.MenuItem("Approve Current", callback=self._approve)
        self.disapprove_item = rumps.MenuItem("Disapprove Current", callback=self._disapprove)
        self.menu = [
            *self.status_items.values(),
            None,
            self.toggle_item,
            self.pause_item,
            None,
            self.profile_menu,
            self.approve_item,
            self.disapprove_item,
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

    def _disapprove(self, _sender) -> None:
        self.controller.disapprove_current()

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
        # In a session the clock timer redraws within a second anyway. Redrawing
        # here too, at whatever point in the second the poll lands, would turn
        # the clock over early every couple of seconds.
        if not self.clock_timer.is_alive():
            self.refresh()

    def _clock_tick(self, _timer) -> None:
        """Redraw only. The engine is not advanced here; `poll` owns timing."""
        self.refresh()

    def _wake(self) -> None:
        """Run queued hotkey actions at once, on the main thread."""
        self.controller.wake()
        self.refresh()

    def _menu_will_open(self) -> None:
        """Read the frontmost app just before the menu shows.

        Opening a menu bar menu leaves the app you were in at the front, so
        this is what the focus line and Approve/Disapprove act on - with or without
        a session running.
        """
        self.controller.look_now()
        self.refresh()

    def _pace_timers(self, phase: str) -> None:
        """Run the timers only as often as the session state needs.

        rumps ignores an interval change on a timer that started less than one
        interval ago, so a change of pace restarts the timer instead.
        """
        running = phase == RUNNING
        interval = self.controller.config.general.poll_seconds if running else IDLE_POLL_SECONDS
        if interval != self._poll_seconds:
            self._poll_seconds = interval
            self.timer.stop()
            self.timer.interval = interval
            self.timer.start()
            keep_running_in_menus(self.timer)
        # Idle or paused, the menu bar title does not change from second to second.
        if running and not self.clock_timer.is_alive():
            self.clock_timer.start()
            keep_running_in_menus(self.clock_timer)
        elif not running and self.clock_timer.is_alive():
            self.clock_timer.stop()

    def _align_clock(self, elapsed: float) -> None:
        """Keep the clock's redraws halfway between its seconds.

        A redraw that lands right as a second turns over shows it late whenever
        the main thread is held up (a poll asking a browser for its tab), and
        the next one then follows quickly. Mid-second, a delay of up to half a
        second changes nothing on screen. The phase shifts when a session is
        resumed or time is taken off the clock, so it is checked on each redraw.
        """
        nstimer = getattr(self.clock_timer, "_nstimer", None)
        if nstimer is None:
            return
        wanted = seconds_to_mid_second(elapsed)
        scheduled = nstimer.fireDate().timeIntervalSinceNow()
        if phase_gap(scheduled, wanted) > CLOCK_SLACK_SECONDS:
            nstimer.setFireDate_(NSDate.dateWithTimeIntervalSinceNow_(wanted))

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
            self._show_title(
                f"{'*' if snap.phase == RUNNING else '||'} {format_duration(snap.elapsed_seconds)}"
            )
        else:
            self._show_title("OnTask")

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
        self.disapprove_item.title = f"Disapprove {label}" if label else "Disapprove Current"

        if set(self._profile_items) != set(self.controller.config.profile_names()):
            self._rebuild_profiles()
        for name, item in self._profile_items.items():
            item.state = 1 if name == snap.profile else 0

        self._pace_timers(snap.phase)
        if snap.phase == RUNNING:
            self._align_clock(snap.elapsed_seconds)

    def _show_title(self, text: str) -> None:
        """Set the menu bar text, with digits that all take the same width.

        In the normal font a "1" is narrower than an "8", so the ticking clock
        nudged everything beside it sideways. Before the run loop starts there
        is no status item yet; rumps draws the plain title at launch and the
        next change replaces it.
        """
        if text == self._shown_title:
            return
        self._shown_title = text
        self.title = text
        item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
        if item is not None:
            item.button().setAttributedTitle_(clock_title(text))

    # -- permissions ------------------------------------------------------

    def ask_for_notifications(self) -> None:
        """Ask for notification permission, whatever the check-in style.

        Called on every launch. macOS shows its dialog only while the answer is
        still undecided, so in practice that means the first launch; after that
        the request just reports the stored answer. Banners carry more than
        check-ins (confirmations, "Nice. Next check-in..."), so waiting for a
        banner check-in style left those silent with the default window.
        """
        self.notifier.request_authorization()

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
    """Add launch, reopen and terminate handling to rumps' application delegate.

    Three things AppKit tells the delegate that OnTask needs:

    * a *finished launching*, the first moment the run loop is up, which is
      when the notification permission can safely be asked for;
    * a *reopen*, which is what launching an already-running app produces -
      macOS never starts a second copy of a bundled app, so this is the only
      way a Spotlight launch can reach the instance that is running;
    * a *terminate*, which is how logging out or restarting asks apps to stop,
      and the last chance to bank the session that is still going.

    Signals are not an option here: under NSApplication.run() a Python-level
    SIGTERM handler is never invoked, whichever thread installs it.

    rumps builds its delegate from a module-level class, so substituting a
    subclass adds these without forking rumps.
    """
    try:
        base = rumps.rumps.NSApp
    except Exception:
        return

    class OnTaskNSApp(base):
        def applicationDidFinishLaunching_(self, notification):
            # rumps watches for sleep and wake here.
            objc.super(OnTaskNSApp, self).applicationDidFinishLaunching_(notification)
            app = _running_app()
            if app is not None:
                try:
                    app.ask_for_notifications()
                except Exception:
                    pass

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


def seconds_to_mid_second(elapsed: float) -> float:
    """Time until `elapsed` next reaches a whole second plus a half."""
    return (0.5 - elapsed) % 1.0


def phase_gap(a: float, b: float) -> float:
    """How far apart two once-a-second schedules are, from 0 to 0.5 s."""
    return abs((a - b + 0.5) % 1.0 - 0.5)


def keep_running_in_menus(timer) -> None:
    """Let a started rumps timer fire while a menu is open.

    rumps schedules timers for the run loop's default mode only, and macOS
    switches to another mode while it tracks an open menu, so the clock froze
    until the menu closed. The common modes include both.
    """
    nstimer = getattr(timer, "_nstimer", None)
    if nstimer is not None:
        NSRunLoop.currentRunLoop().addTimer_forMode_(nstimer, NSRunLoopCommonModes)


_clock_font = None


def clock_title(text: str):
    """`text` in the menu bar's font, with fixed-width digits like Apple's clock."""
    global _clock_font
    if _clock_font is None:
        size = NSFont.menuBarFontOfSize_(0).pointSize()
        _clock_font = NSFont.monospacedDigitSystemFontOfSize_weight_(size, NSFontWeightRegular)
    return NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: _clock_font}
    )
