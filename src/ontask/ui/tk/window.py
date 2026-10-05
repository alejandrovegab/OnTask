"""Shared behaviour for OnTask's Tk helper windows.

Settings and Statistics run as their own processes so Tk never competes with
the menu bar app's AppKit run loop. That buys isolation but costs two things a
window launched from a menu needs, and both are fixed here:

* it opens behind whatever app is frontmost, where it is easy to miss;
* asking for it again from the menu cannot raise it, because the menu bar app
  has no handle on another process's windows.

`watch_raise` closes the second gap by polling for the nudge the controller
sends when a window is already open.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

RAISE_POLL_MS = 400


def bring_to_front(root: tk.Tk) -> None:
    """Put a Tk window in front of whatever the user was doing."""
    try:
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        # Dropped again straight away: the window should arrive in front, not
        # stay pinned over everything afterwards.
        root.after(400, lambda: _drop_topmost(root))
        root.focus_force()
    except tk.TclError:
        pass
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def _drop_topmost(root: tk.Tk) -> None:
    try:
        root.attributes("-topmost", False)
    except tk.TclError:
        pass


def watch_raise(root: tk.Tk, config_path: Path | None, marker: str) -> None:
    """Come to the front whenever the running app asks this window to."""
    try:
        from .. import ipc

        signal = ipc.Signal(ipc.runtime_dir(config_path), marker)
    except Exception:
        return

    def check() -> None:
        try:
            if signal.received():
                bring_to_front(root)
            root.after(RAISE_POLL_MS, check)
        except tk.TclError:
            pass  # window closed

    root.after(RAISE_POLL_MS, check)
