"""Fallback shell for Windows and Linux: a small always-available control window.

There is no menu bar to hang off, so the session controls live in a compact
window that can be minimised. The check-in itself is a separate Toplevel.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..app import Controller, Shell
from ..engine import IDLE, PAUSED, ActivePrompt
from ..hotkeys import HotkeyManager
from .prompt_tk import TkPrompt


class TkShell(Shell):
    def __init__(self, config_path: Path | None = None) -> None:
        self.root = tk.Tk()
        self.root.title("OnTask")
        self.root.minsize(360, 190)
        # `shell` is OnTask's UI shell, not subprocess's shell flag.
        self.controller = Controller(shell=self, config_path=config_path)  # nosec B604
        self.prompt = TkPrompt(self.root, self._on_answer)
        self.hotkeys = HotkeyManager(self.controller)
        self.status_var = tk.StringVar()
        self.profile_var = tk.StringVar(value=self.controller.config.active_profile)
        self._build()
        self.hotkeys.start()
        self.refresh()
        self._schedule()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.status_var, justify="left").pack(anchor="w")

        picker = ttk.Frame(frame)
        picker.pack(fill="x", pady=(12, 8))
        ttk.Label(picker, text="Profile").pack(side="left")
        self.profile_combo = ttk.Combobox(
            picker,
            textvariable=self.profile_var,
            state="readonly",
            values=self.controller.config.profile_names(),
            width=22,
        )
        self.profile_combo.pack(side="left", padx=(8, 0))
        self.profile_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.controller.set_profile(self.profile_var.get())
        )

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.toggle_button = ttk.Button(
            buttons, text="Start Session", command=self.controller.toggle_session
        )
        self.toggle_button.pack(side="left")
        self.pause_button = ttk.Button(
            buttons, text="Pause", command=self.controller.pause_or_resume
        )
        self.pause_button.pack(side="left", padx=6)
        ttk.Button(buttons, text="Settings...", command=self.controller.open_settings).pack(
            side="right"
        )

    def _schedule(self) -> None:
        interval = int(self.controller.config.general.poll_seconds * 1000)
        self.root.after(max(250, interval), self._tick)

    def _tick(self) -> None:
        self.controller.poll()
        self.refresh()
        self._schedule()

    def _on_answer(self, yes: bool) -> None:
        self.controller.answer(yes)

    # -- Shell interface --------------------------------------------------

    def show_prompt(self, prompt: ActivePrompt, controller: Controller) -> None:
        self.prompt.show(prompt.question(), self._subtitle(), controller.config.general.play_sound)

    def realert(self, prompt: ActivePrompt) -> None:
        self.prompt.realert(self._subtitle(), self.controller.config.general.play_sound)

    def close_prompt(self) -> None:
        self.prompt.close()

    def notify(self, title: str, message: str) -> None:
        self.status_var.set(f"{self.status_var.get()}\n{message}")

    def ask_add_rule(self, target, rule: str, controller: Controller) -> None:
        if messagebox.askyesno(
            "OnTask",
            f"You've said you're on task in {target.describe()} three times.\n\n"
            f"Add {rule} to the approved list for {controller.config.active_profile}?",
        ):
            controller.add_rule(rule, "approved")

    def refresh(self) -> None:
        snap = self.controller.engine.snapshot()
        self.status_var.set("\n".join(self.controller.status_lines()))
        self.toggle_button.config(text="End Session" if snap.phase != IDLE else "Start Session")
        self.pause_button.config(
            text="Resume" if snap.phase == PAUSED else "Pause",
            state="normal" if snap.phase != IDLE else "disabled",
        )
        names = self.controller.config.profile_names()
        if list(self.profile_combo["values"]) != names:
            self.profile_combo["values"] = names
        if self.profile_var.get() != snap.profile:
            self.profile_var.set(snap.profile)

    def _subtitle(self) -> str:
        snap = self.controller.engine.snapshot()
        from ..engine import format_duration

        return f"{snap.profile} - {format_duration(snap.elapsed_seconds)} elapsed"

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            # Closing the window is quitting: bank the running session and
            # flush the debounced statistics, as the menu bar shell does.
            self.hotkeys.stop()
            self.controller.shutdown()


def run(config_path: Path | None = None) -> None:
    TkShell(config_path).run()
