"""Choosing which browsers OnTask reads tab URLs from.

The same widget serves two places: the first-run window, and the General tab in
Settings. It lists the browsers macOS reports as installed, each with its own
icon, plus anything already configured that is not installed here any more, so
a config never silently loses an entry it cannot show.

Ticking is what enables tracking. "Add from Finder..." exists for a browser
LaunchServices does not report - a build in an unusual place, or one that has
not registered itself - and reads its identity out of the bundle exactly as the
detected ones do.

Run standalone for the first-run window:

    python -m ontask.ui.tk.browser_setup [path/to/config.json]
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ontask import ipc
from ontask.core.browsers import (
    ACCESSIBILITY,
    UNSUPPORTED,
    Browser,
    BrowserError,
    inspect_app,
    installed_browsers,
)
from ontask.core.config import Config
from ontask.platform.macos.icons import icon_base64
from ontask.ui.tk.window import bring_to_front, watch_raise

ROUTE_NOTES = {
    ACCESSIBILITY: "reads the address bar, needs Accessibility",
    UNSUPPORTED: "app-level tracking only, no site detection",
}
DEFAULT_NOTE = "reads the tab URL with AppleScript"


class BrowserList(ttk.Frame):
    """Tick-list of browsers, with icons, backed by `Browser` records."""

    def __init__(self, parent, browsers: list[Browser] | None = None):
        super().__init__(parent)
        # Tk drops an image the moment nothing in Python refers to it.
        self._images: list[tk.PhotoImage] = []
        self._rows: list[tuple[Browser, tk.BooleanVar]] = []
        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True)
        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="Add from Finder...", command=self.add_from_finder).pack(
            side="left"
        )
        self.hint = ttk.Label(controls, text="", foreground="#666")
        self.hint.pack(side="left", padx=(10, 0))
        self.set_browsers(browsers or [])

    # -- contents ---------------------------------------------------------

    def set_browsers(self, configured: list[Browser]) -> None:
        """Show installed browsers plus anything configured, ticking the latter."""
        chosen = {b.bundle_id: b for b in configured if b.bundle_id}
        catalogue: dict[str, Browser] = {}
        for browser in installed_browsers():
            catalogue[browser.bundle_id] = browser
        for bundle_id, browser in chosen.items():
            # Keep a configured browser visible even where it is not installed,
            # and prefer the freshly inspected copy when it is.
            if bundle_id not in catalogue:
                catalogue[bundle_id] = browser
        order = sorted(catalogue.values(), key=lambda b: b.name.lower())
        self._render(order, set(chosen))

    def _render(self, browsers: list[Browser], ticked: set[str]) -> None:
        for child in self._body.winfo_children():
            child.destroy()
        self._images.clear()
        self._rows.clear()
        if not browsers:
            ttk.Label(
                self._body,
                text="No browsers detected. Use Add from Finder... to choose one.",
                foreground="#666",
            ).grid(row=0, column=0, sticky="w")
            return
        for row, browser in enumerate(browsers):
            var = tk.BooleanVar(value=browser.bundle_id in ticked)
            self._rows.append((browser, var))
            check = ttk.Checkbutton(self._body, text=f" {browser.name}", variable=var)
            image = self._image_for(browser)
            if image is not None:
                check.configure(image=image, compound="left")
            check.grid(row=row, column=0, sticky="w", pady=1)
            note = ROUTE_NOTES.get(browser.flavour, DEFAULT_NOTE)
            ttk.Label(self._body, text=note, foreground="#777").grid(
                row=row, column=1, sticky="w", padx=(12, 0)
            )

    def _image_for(self, browser: Browser) -> tk.PhotoImage | None:
        data = icon_base64(browser.app_path) if browser.app_path else None
        if not data:
            return None
        try:
            image = tk.PhotoImage(data=data)
        except tk.TclError:
            return None
        self._images.append(image)
        return image

    # -- interaction ------------------------------------------------------

    def add_from_finder(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self,
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
        for existing, var in self._rows:
            if existing.bundle_id == browser.bundle_id:
                var.set(True)
                self.hint.config(text=f"{browser.name} was already listed; ticked it.")
                return
        if browser.flavour == UNSUPPORTED and not messagebox.askyesno(
            "OnTask",
            f"{browser.name} offers no way to read the address of its active tab, so "
            "OnTask can only tell that the app is in front, not which site.\n\nAdd it anyway?",
        ):
            return
        current = [b for b, var in self._rows if var.get()]
        catalogue = [b for b, _ in self._rows] + [browser]
        self._render(
            sorted(catalogue, key=lambda b: b.name.lower()),
            {b.bundle_id for b in current} | {browser.bundle_id},
        )
        self.hint.config(text=f"Added {browser.name}.")

    def selected(self) -> list[Browser]:
        return [browser for browser, var in self._rows if var.get()]


# -- first-run window -----------------------------------------------------


class FirstRunWindow:
    def __init__(self, root: tk.Tk, path: Path | None):
        self.root = root
        self.config = Config.load(path)
        root.title("Welcome to OnTask")
        root.minsize(560, 420)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Which browsers do you use?", font=("", 16, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "OnTask checks the site in your active tab against your approved and\n"
                "disapproved lists. Tick the browsers you want it to read. You can change this\n"
                "at any time in Settings, and OnTask works without any of them - it just tracks\n"
                "apps only."
            ),
            foreground="#555",
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        self.list = BrowserList(outer, self.config.general.browsers)
        self.list.pack(fill="both", expand=True)
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(14, 0))
        ttk.Label(
            footer,
            text="Reading tabs asks for permission the first time, per browser.",
            foreground="#777",
        ).pack(side="left")
        ttk.Button(footer, text="Continue", command=self.finish).pack(side="right")

    def finish(self) -> None:
        self.answer(self.list.selected())
        self.root.destroy()

    def answer(self, browsers: list[Browser] | None) -> None:
        """Record the picker's answer on top of whatever is on disk *now*.

        The window can sit open while Settings or the menu bar app save their
        own changes, so writing back the copy loaded at startup would undo
        them. Only the two fields this window owns are touched. `None` means
        the window was closed without choosing: keep the browsers as they are.
        """
        config = Config.load(self.config.path)
        if browsers is not None:
            config.general.browsers = list(browsers)
        config.setup_complete = True
        config.normalize()
        config.save()
        self.config = config


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv and argv[0] else None
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.4)
    except tk.TclError:
        pass
    window = FirstRunWindow(root, path)
    bring_to_front(root)
    watch_raise(root, path, ipc.RAISE_SETUP)
    root.mainloop()
    # Closing the window with the red button still counts as answered, so the
    # picker does not reappear on every launch.
    if not window.config.setup_complete:
        window.answer(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
