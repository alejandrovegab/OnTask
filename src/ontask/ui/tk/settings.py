"""Settings editor.

Runs as its own process (launched from the menu bar) so Tk never competes with
the AppKit run loop. It writes config.json; the running app notices the change
by mtime and reloads without a restart.

Usage: python -m ontask.ui.tk.settings [path/to/config.json]
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ontask import ipc
from ontask.core.config import ClockPenalty, Config, NoResponse, Profile
from ontask.ui.tk.browser_setup import BrowserList
from ontask.ui.tk.window import bring_to_front, watch_raise

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
        self.root.minsize(780, 660)
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
        ttk.Label(
            right, text="Approved apps and sites (no reminders beyond the normal cadence)"
        ).pack(anchor="w")
        self.approved_text = tk.Text(right, height=9, wrap="none", undo=True)
        self.approved_text.pack(fill="both", expand=True, pady=(4, 10))
        ttk.Label(
            right, text="Disapproved apps and sites (reminds after the short fuse below)"
        ).pack(anchor="w")
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
        self._row(
            ladder_box, 0, "Intervals (minutes, comma separated)", self.intervals_var, width=40
        )
        self._row(
            ladder_box, 1, "Yes answers needed to advance each rung", self.advance_var, width=40
        )
        self.ladder_preview = ttk.Label(ladder_box, text="", foreground="#444", justify="left")
        self.ladder_preview.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.intervals_var.trace_add("write", lambda *_: self._update_preview())
        self.advance_var.trace_add("write", lambda *_: self._update_preview())

        timing_box = ttk.LabelFrame(tab, text="Distraction timing", padding=10)
        timing_box.pack(fill="x", pady=(12, 0))
        self.distraction_var = tk.StringVar()
        self.disapproved_var = tk.StringVar()
        self.suggest_var = tk.StringVar()
        self._row(timing_box, 0, "Remind after this long off task (seconds)", self.distraction_var)
        self._row(
            timing_box,
            1,
            "Remind after this long on a disapproved app (seconds)",
            self.disapproved_var,
        )
        self._row(
            timing_box,
            2,
            "Offer to approve after this many yes answers (0 disables)",
            self.suggest_var,
        )

        penalty_box = ttk.LabelFrame(tab, text="When you answer No", padding=10)
        penalty_box.pack(fill="x", pady=(12, 0))
        self.penalty_on_var = tk.BooleanVar()
        self.penalty_match_var = tk.BooleanVar()
        self.penalty_approved_var = tk.StringVar()
        self.penalty_fixed_var = tk.StringVar()
        ttk.Checkbutton(
            penalty_box,
            text="Take time off the session clock",
            variable=self.penalty_on_var,
            command=self._sync_penalty_fields,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self.penalty_match_check = ttk.Checkbutton(
            penalty_box,
            text="Take off as much time as the stretch you were in",
            variable=self.penalty_match_var,
            command=self._sync_penalty_fields,
        )
        self.penalty_match_check.grid(row=1, column=0, columnspan=2, sticky="w", padx=(18, 0))
        self.penalty_explain = ttk.Label(
            penalty_box,
            text="On a disapproved app or site, takes off the disapproved wait above.\n"
            "On something not on the approved list, takes off the off-task wait above.",
            foreground="#666",
            justify="left",
        )
        self.penalty_explain.grid(
            row=2, column=0, columnspan=2, sticky="w", padx=(36, 0), pady=(2, 4)
        )
        self.penalty_approved_label, self.penalty_approved_entry = self._row(
            penalty_box,
            3,
            "On an approved app or site, take off (seconds)",
            self.penalty_approved_var,
        )
        self.penalty_fixed_label, self.penalty_fixed_entry = self._row(
            penalty_box, 4, "Take off the same amount every time (seconds)", self.penalty_fixed_var
        )

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
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.start_var = tk.BooleanVar()
        self.sound_var = tk.BooleanVar()
        self.menubar_var = tk.BooleanVar()
        ttk.Checkbutton(
            box, text="Start a session as soon as OnTask launches", variable=self.start_var
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(
            box, text="Play a sound when a check-in appears", variable=self.sound_var
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
        self.answer_sound_var = tk.BooleanVar()
        ttk.Checkbutton(
            box, text="Play a sound when you answer", variable=self.answer_sound_var
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(
            box, text="Show elapsed time in the menu bar", variable=self.menubar_var
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=2)
        self.position_var = tk.StringVar()
        ttk.Label(box, text="Check-in window position").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Combobox(
            box,
            textvariable=self.position_var,
            state="readonly",
            width=34,
            values=list(_POSITION_TO_LABEL.values()),
        ).grid(row=7, column=1, sticky="w", padx=(10, 0))

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
        self.browser_list = BrowserList(browser_box)
        self.browser_list.pack(fill="both", expand=True)
        ttk.Label(
            browser_box,
            text="Untick a browser to stop reading its tabs; OnTask still sees the app "
            "itself.\nAn app is identified by its bundle id, so it keeps matching even "
            "when its\nprocess name differs from its display name.",
            foreground="#666",
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def _row(self, parent, row: int, label: str, var: tk.StringVar, width: int = 12):
        """Label plus entry. Returns both so callers can grey them out."""
        widget = ttk.Label(parent, text=label)
        widget.grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="w", padx=(10, 0))
        return widget, entry

    def _sync_penalty_fields(self) -> None:
        """Only offer the boxes that the chosen combination actually uses."""
        on = bool(self.penalty_on_var.get())
        matching = on and bool(self.penalty_match_var.get())
        self.penalty_match_check.state(["!disabled"] if on else ["disabled"])
        for widget in (self.penalty_explain, self.penalty_approved_label):
            widget.configure(foreground="#666" if matching else "#aaa")
        self.penalty_approved_entry.state(["!disabled"] if matching else ["disabled"])
        fixed = on and not matching
        self.penalty_fixed_label.configure(foreground="" if fixed else "#aaa")
        self.penalty_fixed_entry.state(["!disabled"] if fixed else ["disabled"])

    # -- load / save ------------------------------------------------------

    def _load_into_widgets(self) -> None:
        cfg = self.config
        self.profile_list.delete(0, "end")
        for profile in cfg.profiles:
            self.profile_list.insert("end", profile.name)
        self.current_profile = None
        if cfg.profiles:
            index = (
                max(0, cfg.profile_names().index(cfg.active_profile))
                if cfg.active_profile in cfg.profile_names()
                else 0
            )
            self.profile_list.selection_set(index)
            self._show_profile(cfg.profiles[index].name)

        self.intervals_var.set(", ".join(_fmt(v) for v in cfg.reminder.intervals_minutes))
        self.advance_var.set(", ".join(str(v) for v in cfg.reminder.advance_after_yes))
        self.distraction_var.set(_fmt(cfg.reminder.distraction_grace_seconds))
        self.disapproved_var.set(_fmt(cfg.reminder.disapproved_grace_seconds))
        self.suggest_var.set(str(cfg.reminder.suggest_approve_after_yes))
        penalty = cfg.reminder.clock_penalty
        self.penalty_on_var.set(penalty.enabled)
        self.penalty_match_var.set(penalty.match_situation)
        self.penalty_approved_var.set(_fmt(penalty.approved_seconds))
        self.penalty_fixed_var.set(_fmt(penalty.fixed_seconds))
        self._sync_penalty_fields()
        self.policy_var.set(_POLICY_TO_LABEL[cfg.reminder.no_response.policy])
        self.renag_var.set(_fmt(cfg.reminder.no_response.renag_seconds))
        self.max_alerts_var.set(str(cfg.reminder.no_response.max_alerts))

        self.poll_var.set(_fmt(cfg.general.poll_seconds))
        self.prompt_ui_var.set(_PROMPT_TO_LABEL[cfg.general.prompt_ui])
        self.start_var.set(cfg.general.start_session_on_launch)
        self.sound_var.set(cfg.general.play_sound)
        self.answer_sound_var.set(cfg.general.play_answer_sound)
        self.position_var.set(_POSITION_TO_LABEL[cfg.general.prompt_position])
        self.menubar_var.set(cfg.general.show_elapsed_in_menu_bar)
        self.toggle_key_var.set(cfg.general.hotkeys.toggle_session)
        self.yes_key_var.set(cfg.general.hotkeys.answer_yes)
        self.no_key_var.set(cfg.general.hotkeys.answer_no)
        self.browser_list.set_browsers(cfg.general.browsers)
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
        parts = []
        for rung, value in enumerate(intervals):
            parts.append(f"{_fmt(value)} min")
            if rung < len(intervals) - 1:
                need = advance[rung] if rung < len(advance) else (advance[-1] if advance else 1)
                parts.append(f" --{need} yes--> ")
        preview = "".join(parts)
        note = (
            "" if len(advance) == max(0, len(intervals) - 1) else "  (list will be resized on save)"
        )
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

    def revert(self) -> None:
        self.config = Config.load(self.config.path)
        self._load_into_widgets()
        self._flash("Reverted to the saved settings.")

    def restore_defaults(self) -> None:
        if not messagebox.askyesno(
            "OnTask", "Replace all settings, including profiles, with the defaults?"
        ):
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
        reminder.advance_after_yes = [
            int(v) for v in _numbers(self.advance_var.get(), "Advance counts")
        ]
        reminder.distraction_grace_seconds = _number(self.distraction_var.get(), "Off-task seconds")
        reminder.disapproved_grace_seconds = _number(
            self.disapproved_var.get(), "Disapproved app seconds"
        )
        reminder.suggest_approve_after_yes = int(_number(self.suggest_var.get(), "Yes answers"))
        reminder.clock_penalty = ClockPenalty(
            enabled=bool(self.penalty_on_var.get()),
            match_situation=bool(self.penalty_match_var.get()),
            approved_seconds=_number(self.penalty_approved_var.get(), "Approved-list seconds"),
            fixed_seconds=_number(self.penalty_fixed_var.get(), "Fixed penalty seconds"),
        )
        reminder.no_response = NoResponse(
            policy=_LABEL_TO_POLICY[self.policy_var.get()],
            renag_seconds=_number(self.renag_var.get(), "Re-alert seconds"),
            max_alerts=int(_number(self.max_alerts_var.get(), "Alert count")),
        )
        cfg.general.poll_seconds = _number(self.poll_var.get(), "Poll seconds")
        cfg.general.prompt_ui = _LABEL_TO_PROMPT[self.prompt_ui_var.get()]
        cfg.general.start_session_on_launch = bool(self.start_var.get())
        cfg.general.play_sound = bool(self.sound_var.get())
        cfg.general.play_answer_sound = bool(self.answer_sound_var.get())
        cfg.general.prompt_position = _LABEL_TO_POSITION[self.position_var.get()]
        cfg.general.show_elapsed_in_menu_bar = bool(self.menubar_var.get())
        cfg.general.hotkeys.toggle_session = self.toggle_key_var.get().strip()
        cfg.general.hotkeys.answer_yes = self.yes_key_var.get().strip()
        cfg.general.hotkeys.answer_no = self.no_key_var.get().strip()
        cfg.general.browsers = self.browser_list.selected()

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
_POSITION_TO_LABEL = {
    "center": "Centre of the screen",
    "top_left": "Top left",
    "top_center": "Top middle",
    "top_right": "Top right",
    "bottom_left": "Bottom left",
    "bottom_center": "Bottom middle",
    "bottom_right": "Bottom right",
}
_LABEL_TO_POSITION = {v: k for k, v in _POSITION_TO_LABEL.items()}


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
    window = SettingsWindow(root, path)
    bring_to_front(root)
    watch_raise(root, window.config.path, ipc.RAISE_SETTINGS)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
