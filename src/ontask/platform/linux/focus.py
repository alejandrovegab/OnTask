"""Linux focus detection on X11, through xdotool and xprop.

Browser tab URLs are not read here yet, so profiles match on the application
only. Without xdotool (or on a Wayland session) the target is unknown, which
`matching.classify` treats as on-task.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from ...focus import FocusProvider, FocusTarget

if TYPE_CHECKING:
    from ...core.browsers import Browser


class X11FocusProvider(FocusProvider):
    def __init__(self) -> None:
        self._available = bool(shutil.which("xdotool"))

    def current(self, browsers: list[Browser] | None = None) -> FocusTarget:
        if not self._available:
            return FocusTarget()
        try:
            return _xdotool_target()
        except Exception:
            return FocusTarget()


def _xdotool_target() -> FocusTarget:
    def run(args: list[str]) -> str:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=1.0)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    window = run(["xdotool", "getactivewindow"])
    if not window:
        return FocusTarget()
    title = run(["xdotool", "getwindowname", window])
    name = ""
    if shutil.which("xprop"):
        wm_class = run(["xprop", "-id", window, "WM_CLASS"])
        if '"' in wm_class:
            parts = [p for p in wm_class.split('"') if p.strip(", ")]
            if parts:
                name = parts[-1]
    return FocusTarget(app_name=name, bundle_id=name.lower(), title=title)
