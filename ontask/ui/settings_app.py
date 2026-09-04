"""Settings editor.

Runs as its own process (launched from the menu bar) so Tk never competes with
the AppKit run loop. It writes config.json; the running app notices the change
by mtime and reloads without a restart.

Usage: python -m ontask.ui.settings_app [path/to/config.json]
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ontask.config import Config, NoResponse, Profile  # noqa: E402
from ontask.browsers import (  # noqa: E402
    ACCESSIBILITY,
    UNSUPPORTED,
    Browser,
    BrowserError,
    inspect_app,
)
from ontask.ladder import Ladder  # noqa: E402

RULE_HELP = (
    "One rule per line.   app:Slack   site:github.com   site:*.google.com   "
    "site:reddit.com/r/python\n"
    "A bare line with a dot or slash is a site, otherwise an app. Sites match "
    "subdomains. The most specific rule wins."
)


class SettingsWindow:
    def __init__(self, root: tk.Tk, path: Path | None):
        self.root = root
        self.config = Config.load(path)
        self.current_profile: str | None = None
        self.root.title("OnTask Settings")
        self.root.minsize(720, 560)
        self._build()
        self._load_into_widgets()

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self._build_profiles_tab(notebook)
        self._build_reminders_tab(notebook)
        self._build_general_tab(notebook)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        self.status = ttk.Label(footer, text="")
        self.status.pack(side="left")
        ttk.Button(footer, text="Close", command=self.root.destroy).pack(side="right")
        ttk.Button(footer, text="Save", command=self.save).pack(side="right", padx=(0, 8))
        ttk.Button(footer, text="Revert", command=self.revert).pack(side="right", padx=(0, 8))
        ttk.Button(footer, text="Restore Defaults", command=self.restore_defaults).pack(
            side="right", padx=(0, 8)
        )

    def _build_profiles_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Profiles")

        left = ttk.Frame(tab)
        left.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(left, text="Session profiles").pack(anchor="w")
        self.profile_list = tk.Listbox(left, width=22, height=14, exportselection=False)
        self.profile_list.pack(fill="y", expand=True, pady=(4, 6))
        self.profile_list.bind("<<ListboxSelect>>", self._on_profile_selected)
        for label, command in (
            ("Add", self.add_profile),
            ("Duplicate", self.duplicate_profile),
            ("Rename", self.rename_profile),
            ("Remove", self.remove_profile),
        ):
            ttk.Button(left, text=label, command=command).pack(fill="x", pady=1)

        right = ttk.Frame(tab)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Approved apps and sites (no reminders beyond the normal cadence)").pack(
            anchor="w"
        )
        self.approved_text = tk.Text(right, height=9, wrap="none", undo=True)
        self.approved_text.pack(fill="both", expand=True, pady=(4, 10))
        ttk.Label(right, text="Blocked apps and sites (reminds after the short fuse below)").pack(
            anchor="w"
        )
        self.disapproved_text = tk.Text(right, height=6, wrap="none", undo=True)
        self.disapproved_text.pack(fill="both", expand=True, pady=(4, 6))
        ttk.Label(right, text=RULE_HELP, foreground="#666", justify="left").pack(anchor="w")

    def _build_reminders_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Reminders")

        ladder_box = ttk.LabelFrame(tab, text="Escalation ladder", padding=10)
        ladder_box.pack(fill="x")
        self.intervals_var = tk.StringVar()
        self.advance_var = tk.StringVar()
        self._row(ladder_box, 0, "Intervals (minutes, comma separated)", self.intervals_var, width=40)
        self._row(ladder_box, 1, "Yes answers needed to advance each rung", self.advance_var, width=40)
        self.ladder_preview = ttk.Label(ladder_box, text="", foreground="#444", justify="left")
        self.ladder_preview.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.intervals_var.trace_add("write", lambda *_: self._update_preview())
        self.advance_var.trace_add("write", lambda *_: self._update_preview())

        timing_box = ttk.LabelFrame(tab, text="Distraction timing", padding=10)
        timing_box.pack(fill="x", pady=(12, 0))
        self.distraction_var = tk.StringVar()
        self.blocked_var = tk.StringVar()
        self.suggest_var = tk.StringVar()
        self._row(timing_box, 0, "Remind after this long off task (seconds)", self.distraction_var)
        self._row(timing_box, 1, "Remind after this long on a blocked app (seconds)", self.blocked_var)
        self._row(timing_box, 2, "Offer to approve after this many yes answers (0 disables)", self.suggest_var)

        ignore_box = ttk.LabelFrame(tab, text="If a check-in is ignored", padding=10)
        ignore_box.pack(fill="x", pady=(12, 0))
        self.policy_var = tk.StringVar()
        ttk.Label(ignore_box, text="Behaviour").grid(row=0, column=0, sticky="w", pady=3)
        self.policy_combo = ttk.Combobox(
            ignore_box,
            textvariable=self.policy_var,
            state="readonly",
            width=34,
            values=["Re-alert, then count as No", "Wait indefinitely", "Pause the session"],
        )
        self.policy_combo.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.renag_var = tk.StringVar()
        self.max_alerts_var = tk.StringVar()
        self._row(ignore_box, 1, "Re-alert every (seconds)", self.renag_var)
        self._row(ignore_box, 2, "Alerts before it counts as No", self.max_alerts_var)

    def _build_general_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="General")

        box = ttk.LabelFrame(tab, text="Behaviour", padding=10)
        box.pack(fill="x")
        self.poll_var = tk.StringVar()
        self._row(box, 0, "Check the frontmost window every (seconds)", self.poll_var)
        self.prompt_ui_var = tk.StringVar()
        ttk.Label(box, text="Check-in style").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(
            box,
            textvariable=self.prompt_ui_var,
            state="readonly",
            width=34,
            values=["Floating window", "Notification only", "Notification, then window"],
        ).grid(row=1, column=1, sticky="w", padx=(10, 0))
        ttk.Label(
            box,
            text="Notification banners carry Yes and No buttons. If notifications are "
            "denied,\nOnTask falls back to the floating window so a check-in is never "
            "unanswerable.",
            foreground="#666",
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.start_var = tk.BooleanVar()
        self.sound_var = tk.BooleanVar()
        self.menubar_var = tk.BooleanVar()
        ttk.Checkbutton(box, text="Start a session as soon as OnTask launches", variable=self.start_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=2
        )
        ttk.Checkbutton(box, text="Play a sound with each check-in", variable=self.sound_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=2
        )
        ttk.Checkbutton(box, text="Show elapsed time in the menu bar", variable=self.menubar_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=2
        )

        keys_box = ttk.LabelFrame(tab, text="Global hotkeys", padding=10)
        keys_box.pack(fill="x", pady=(12, 0))
        self.toggle_key_var = tk.StringVar()
        self.yes_key_var = tk.StringVar()
        self.no_key_var = tk.StringVar()
        self._row(keys_box, 0, "Start / end session", self.toggle_key_var, width=30)
        self._row(keys_box, 1, "Answer yes", self.yes_key_var, width=30)
        self._row(keys_box, 2, "Answer no", self.no_key_var, width=30)
        ttk.Label(
            keys_box,
            text="pynput syntax, e.g. <ctrl>+<alt>+<cmd>+o. Leave blank to disable. "
            "Needs Accessibility permission on macOS.",
            foreground="#666",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        browser_box = ttk.LabelFrame(tab, text="Track tab URLs in these browsers", padding=10)
        browser_box.pack(fill="both", expand=True, pady=(12, 0))
        self.browsers: list[Browser] = []
        listing = ttk.Frame(browser_box)
        listing.pack(fill="both", expand=True)
        self.browser_list = tk.Listbox(listing, height=5, exportselection=False)
        self.browser_list.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(listing)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Add from Finder...", command=self.add_browser).pack(fill="x", pady=1)
        ttk.Button(buttons, text="Remove", command=self.remove_browser).pack(fill="x", pady=1)
        ttk.Label(
            browser_box,
            text="Safari is set up already. Add any other browser by choosing its app, so "
            "OnTask reads\nthe real bundle id and matches it even when the app's process "
            "name differs.\n\nSafari and Chromium browsers are read with AppleScript "
            "(Automation permission). Firefox,\nZen and other Gecko browsers have no "
            "AppleScript URL, so their address bar is read from\nthe accessibility tree "
            "instead - best effort, and needs Accessibility.",
            foreground="#666",
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def _row(self, parent, row: int, label: str, var: tk.StringVar, width: int = 12) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky="w", padx=(10, 0))

    # -- load / save ------------------------------------------------------

    def _load_into_widgets(self) -> None:
        cfg = self.config
        self.profile_list.delete(0, "end")
        for profile in cfg.profiles:
            self.profile_list.insert("end", profile.name)
        self.current_profile = None
        if cfg.profiles:
            index = max(0, cfg.profile_names().index(cfg.active_profile)) if cfg.active_profile in cfg.profile_names() else 0
            self.profile_list.selection_set(index)
            self._show_profile(cfg.profiles[index].name)

        self.intervals_var.set(", ".join(_fmt(v) for v in cfg.reminder.intervals_minutes))
        self.advance_var.set(", ".join(str(v) for v in cfg.reminder.advance_after_yes))
        self.distraction_var.set(_fmt(cfg.reminder.distraction_grace_seconds))
        self.blocked_var.set(_fmt(cfg.reminder.disapproved_grace_seconds))
        self.suggest_var.set(str(cfg.reminder.suggest_approve_after_yes))
        self.policy_var.set(_POLICY_TO_LABEL[cfg.reminder.no_response.policy])
        self.renag_var.set(_fmt(cfg.reminder.no_response.renag_seconds))
        self.max_alerts_var.set(str(cfg.reminder.no_response.max_alerts))

        self.poll_var.set(_fmt(cfg.general.poll_seconds))
        self.prompt_ui_var.set(_PROMPT_TO_LABEL[cfg.general.prompt_ui])
        self.start_var.set(cfg.general.start_session_on_launch)
        self.sound_var.set(cfg.general.play_sound)
        self.menubar_var.set(cfg.general.show_elapsed_in_menu_bar)
        self.toggle_key_var.set(cfg.general.hotkeys.toggle_session)
        self.yes_key_var.set(cfg.general.hotkeys.answer_yes)
        self.no_key_var.set(cfg.general.hotkeys.answer_no)
        self.browsers = list(cfg.general.browsers)
        self._refresh_browser_list()
        self._update_preview()

    def _on_profile_selected(self, _event=None) -> None:
        selection = self.profile_list.curselection()
        if not selection:
            return
        name = self.profile_list.get(selection[0])
        if name != self.current_profile:
            self._stash_profile_text()
            self._show_profile(name)

    def _show_profile(self, name: str) -> None:
        profile = self.config.profile(name)
        self.current_profile = profile.name
        self.approved_text.delete("1.0", "end")
        self.approved_text.insert("1.0", "\n".join(profile.approved))
        self.disapproved_text.delete("1.0", "end")
        self.disapproved_text.insert("1.0", "\n".join(profile.disapproved))

    def _stash_profile_text(self) -> None:
        """Copy the text boxes back into the profile before switching away."""
        if not self.current_profile:
            return
        profile = self.config.profile(self.current_profile)
        profile.approved = _lines(self.approved_text)
        profile.disapproved = _lines(self.disapproved_text)

    def _update_preview(self) -> None:
        try:
            intervals = _numbers(self.intervals_var.get())
            advance = [int(v) for v in _numbers(self.advance_var.get())]
        except ValueError:
            self.ladder_preview.config(text="Enter comma separated numbers.", foreground="#b00")
            return
        if not intervals:
            self.ladder_preview.config(text="Add at least one interval.", foreground="#b00")
            return
        ladder = Ladder(intervals_minutes=intervals, advance_after_yes=advance or [1])
        parts = []
        for rung, value in enumerate(intervals):
            parts.append(f"{_fmt(value)} min")
            if rung < len(intervals) - 1:
                need = advance[rung] if rung < len(advance) else (advance[-1] if advance else 1)
                parts.append(f" --{need} yes--> ")
        preview = "".join(parts)
        note = "" if len(advance) == max(0, len(intervals) - 1) else "  (list will be resized on save)"
        self.ladder_preview.config(text=preview + note, foreground="#444")

    # -- buttons ----------------------------------------------------------

    def add_profile(self) -> None:
        self._stash_profile_text()
        name = _unique_name("New Profile", self.config.profile_names())
        self.config.profiles.append(Profile(name=name))
        self.profile_list.insert("end", name)
        self.profile_list.selection_clear(0, "end")
        self.profile_list.selection_set("end")
        self._show_profile(name)

    def duplicate_profile(self) -> None:
        if not self.current_profile:
            return
        self._stash_profile_text()
        source = self.config.profile(self.current_profile)
        name = _unique_name(f"{source.name} copy", self.config.profile_names())
        self.config.profiles.append(
            Profile(name=name, approved=list(source.approved), disapproved=list(source.disapproved))
        )
        self.profile_list.insert("end", name)
        self.profile_list.selection_clear(0, "end")
        self.profile_list.selection_set("end")
        self._show_profile(name)

    def rename_profile(self) -> None:
        if not self.current_profile:
            return
        self._stash_profile_text()
        new_name = _ask_text(self.root, "Rename profile", "New name:", self.current_profile)
        if not new_name or new_name == self.current_profile:
            return
        if new_name in self.config.profile_names():
            messagebox.showerror("OnTask", f"A profile called {new_name} already exists.")
            return
        profile = self.config.profile(self.current_profile)
        if self.config.active_profile == profile.name:
            self.config.active_profile = new_name
        profile.name = new_name
        index = self.profile_list.curselection()
        if index:
            self.profile_list.delete(index[0])
            self.profile_list.insert(index[0], new_name)
            self.profile_list.selection_set(index[0])
        self.current_profile = new_name

    def remove_profile(self) -> None:
        if not self.current_profile or len(self.config.profiles) <= 1:
            messagebox.showinfo("OnTask", "Keep at least one profile.")
            return
        name = self.current_profile
        if not messagebox.askyesno("OnTask", f"Delete the profile {name}?"):
            return
        self.config.profiles = [p for p in self.config.profiles if p.name != name]
        if self.config.active_profile == name:
            self.config.active_profile = self.config.profiles[0].name
        self.current_profile = None
        self._load_into_widgets()

    def _refresh_browser_list(self) -> None:
        self.browser_list.delete(0, "end")
        for browser in self.browsers:
            self.browser_list.insert("end", browser.describe())

    def add_browser(self) -> None:
        """Pick a .app and read its identity out of the bundle."""
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a browser",
            initialdir="/Applications",
            filetypes=[("Applications", "*.app"), ("All files", "*")],
        )
        if not chosen:
            return
        try:
            browser = inspect_app(chosen)
        except BrowserError as exc:
            messagebox.showerror("OnTask", str(exc))
            return
        for existing in self.browsers:
            if existing.bundle_id == browser.bundle_id:
                messagebox.showinfo("OnTask", f"{browser.name} is already in the list.")
                return
        if browser.flavour == UNSUPPORTED:
            keep = messagebox.askyesno(
                "OnTask",
                f"{browser.name} offers no way to read the address of its active tab, so "
                "OnTask can only tell that the app is in front, not which site.\n\n"
                "Add it anyway?",
            )
            if not keep:
                return
        self.browsers.append(browser)
        self._refresh_browser_list()
        self.browser_list.selection_clear(0, "end")
        self.browser_list.selection_set("end")
        if browser.flavour == ACCESSIBILITY:
            self._flash(f"Added {browser.name}. Reading its tabs needs Accessibility permission.")
        else:
            self._flash(f"Added {browser.name}.")

    def remove_browser(self) -> None:
        selection = self.browser_list.curselection()
        if not selection:
            return
        removed = self.browsers.pop(selection[0])
        self._refresh_browser_list()
        self._flash(f"Removed {removed.name}.")

    def revert(self) -> None:
        self.config = Config.load(self.config.path)
        self._load_into_widgets()
        self._flash("Reverted to the saved settings.")

    def restore_defaults(self) -> None:
        if not messagebox.askyesno("OnTask", "Replace all settings, including profiles, with the defaults?"):
            return
        path = self.config.path
        self.config = Config()
        self.config.path = path
        self._load_into_widgets()
        self._flash("Defaults loaded. Choose Save to keep them.")

    def save(self) -> None:
        self._stash_profile_text()
        try:
            self._collect()
        except ValueError as exc:
            messagebox.showerror("OnTask", str(exc))
            return
        self.config.normalize()
        self.config.save()
        self._load_into_widgets()
        self._flash(f"Saved to {self.config.path}")

    def _collect(self) -> None:
        cfg = self.config
        reminder = cfg.reminder
        intervals = _numbers(self.intervals_var.get(), "Intervals")
        if not intervals:
            raise ValueError("Add at least one interval.")
        if any(v <= 0 for v in intervals):
            raise ValueError("Intervals must be greater than zero.")
        if sorted(intervals) != intervals:
            raise ValueError("Intervals must increase from left to right.")
        reminder.intervals_minutes = intervals
        reminder.advance_after_yes = [int(v) for v in _numbers(self.advance_var.get(), "Advance counts")]
        reminder.distraction_grace_seconds = _number(self.distraction_var.get(), "Off-task seconds")
        reminder.disapproved_grace_seconds = _number(self.blocked_var.get(), "Blocked app seconds")
        reminder.suggest_approve_after_yes = int(_number(self.suggest_var.get(), "Yes answers"))
        reminder.no_response = NoResponse(
            policy=_LABEL_TO_POLICY[self.policy_var.get()],
            renag_seconds=_number(self.renag_var.get(), "Re-alert seconds"),
            max_alerts=int(_number(self.max_alerts_var.get(), "Alert count")),
        )
        cfg.general.poll_seconds = _number(self.poll_var.get(), "Poll seconds")
        cfg.general.prompt_ui = _LABEL_TO_PROMPT[self.prompt_ui_var.get()]
        cfg.general.start_session_on_launch = bool(self.start_var.get())
        cfg.general.play_sound = bool(self.sound_var.get())
        cfg.general.show_elapsed_in_menu_bar = bool(self.menubar_var.get())
        cfg.general.hotkeys.toggle_session = self.toggle_key_var.get().strip()
        cfg.general.hotkeys.answer_yes = self.yes_key_var.get().strip()
        cfg.general.hotkeys.answer_no = self.no_key_var.get().strip()
        cfg.general.browsers = list(self.browsers)

    def _flash(self, message: str) -> None:
        self.status.config(text=message)
        self.root.after(4000, lambda: self.status.config(text=""))


_POLICY_TO_LABEL = {
    "renag_then_no": "Re-alert, then count as No",
    "wait": "Wait indefinitely",
    "pause_session": "Pause the session",
}
_LABEL_TO_POLICY = {v: k for k, v in _POLICY_TO_LABEL.items()}
_PROMPT_TO_LABEL = {
    "window": "Floating window",
    "notification": "Notification only",
    "both": "Notification, then window",
}
_LABEL_TO_PROMPT = {v: k for k, v in _PROMPT_TO_LABEL.items()}


def _lines(widget: tk.Text) -> list[str]:
    return [line.strip() for line in widget.get("1.0", "end").splitlines() if line.strip()]


def _numbers(text: str, label: str = "Value") -> list[float]:
    values = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            values.append(_number(chunk, label))
    return values


def _number(text: str, label: str = "Value") -> float:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label}: '{text}' is not a number.") from None


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _unique_name(base: str, existing: list[str]) -> str:
    name, counter = base, 2
    while name in existing:
        name = f"{base} {counter}"
        counter += 1
    return name


def _ask_text(root, title: str, prompt: str, initial: str) -> str | None:
    from tkinter import simpledialog

    return simpledialog.askstring(title, prompt, initialvalue=initial, parent=root)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else None
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.4)
    except tk.TclError:
        pass
    SettingsWindow(root, path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
