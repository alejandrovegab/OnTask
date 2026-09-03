"""macOS menu bar shell.

rumps owns the run loop; a repeating timer drives `Controller.poll()`. The
check-in itself is an AppKit panel (see prompt_macos) rather than a Tk window,
so there is only ever one run loop in the process.
"""

from __future__ import annotations

import rumps

from ..app import Controller
from ..engine import IDLE, PAUSED, RUNNING, ActivePrompt, format_duration
from ..hotkeys import HotkeyManager
from .prompt_macos import PromptWindow

STATUS_SLOTS = ("session", "profile", "interval", "next", "focus")


class OnTaskApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("OnTask", title="OnTask", quit_button=None)
        self.controller = Controller(shell=self)
        self.prompt = PromptWindow(self._on_answer)
        self.hotkeys = HotkeyManager(self.controller)
        self._pending: ActivePrompt | None = None
        self._profile_items: dict[str, rumps.MenuItem] = {}
        self._poll_seconds = self.controller.config.general.poll_seconds
        self._build_menu()
        self.hotkeys.start()
        self.refresh()
        self.timer = rumps.Timer(self._tick, self._poll_seconds)
        self.timer.start()

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

    def _pick_profile(self, sender) -> None:
        self.controller.set_profile(sender.title)

    def _quit(self, _sender) -> None:
        self.hotkeys.stop()
        rumps.quit_application()

    def _tick(self, _timer) -> None:
        self.controller.poll()
        self.refresh()

    def _on_answer(self, yes: bool) -> None:
        self.controller.answer(yes)

    # -- Shell interface --------------------------------------------------

    def show_prompt(self, prompt: ActivePrompt, controller: Controller) -> None:
        self._pending = prompt
        mode = controller.config.general.prompt_ui
        sound = controller.config.general.play_sound
        if mode in ("notification", "both"):
            self.notify("OnTask", prompt.question())
        if mode == "window":
            # "both" deliberately waits: it starts as a banner and only escalates
            # to the window on the first re-alert.
            self.prompt.show(prompt.question(), self._subtitle(prompt), sound)

    def realert(self, prompt: ActivePrompt) -> None:
        mode = self.controller.config.general.prompt_ui
        sound = self.controller.config.general.play_sound
        if mode == "notification":
            self.notify("OnTask", prompt.question())
            return
        if not self.prompt.visible:
            # "both" starts as a banner and escalates to the window if ignored.
            self.prompt.show(prompt.question(), self._subtitle(prompt), sound)
        else:
            self.prompt.realert(self._subtitle(prompt), sound)

    def close_prompt(self) -> None:
        self._pending = None
        self.prompt.close()

    def notify(self, title: str, message: str) -> None:
        try:
            rumps.notification(title, "", message)
        except Exception:
            # Notifications need a signed bundle; ignore when running from source.
            pass

    def ask_add_rule(self, target, rule: str, controller: Controller) -> None:
        profile = controller.config.active_profile
        response = rumps.alert(
            title="Add to approved list?",
            message=(
                f"You've said you're on task in {target.describe()} three times.\n\n"
                f"Add {rule} to the approved list for {profile}?"
            ),
            ok="Add",
            cancel="Not now",
        )
        if response == 1:
            controller.add_rule(rule, "approved")

    def refresh(self) -> None:
        snap = self.controller.engine.snapshot()
        general = self.controller.config.general
        if general.show_elapsed_in_menu_bar and snap.phase != IDLE:
            self.title = f"{'*' if snap.phase == RUNNING else '||'} {format_duration(snap.elapsed_seconds)}"
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

    def _subtitle(self, prompt: ActivePrompt) -> str:
        snap = self.controller.engine.snapshot()
        return f"{snap.profile} - {format_duration(snap.elapsed_seconds)} elapsed"


def run() -> None:
    OnTaskApp().run()
