"""Mouse wheel and trackpad scrolling for Tk canvases, on every platform.

Tk reports scrolling three different ways, and treating them alike is what
made the statistics window misbehave:

* macOS sends a burst of small whole numbers per gesture (1, 2, -1...), one
  line each. Integer-dividing those rounds 1 down to 0 but -1 down to -1, so
  one direction barely moved while the other jumped.
* Windows sends multiples of 120 per wheel notch, and precision touchpads send
  fractions of that.
* X11 sends no wheel event at all, only button 4 (up) and button 5 (down).

`wheel_steps` turns any of those into a signed number of steps, keeping the
direction symmetric, and `WheelScroller` adds them up so fractional touchpad
movement accumulates smoothly instead of being lost or rounded into jumps.
"""

from __future__ import annotations

import sys
import tkinter as tk

# One scroll step, in pixels. Small enough to glide; Tk's default step is a
# tenth of the visible height, which lurches.
STEP_PIXELS = 16

# Steps per wheel notch on Windows and X11, matching those systems' default of
# three lines per notch.
STEPS_PER_NOTCH = 3

# Windows reports this much delta for one notch of a standard wheel.
WINDOWS_NOTCH = 120

WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def wheel_steps(event, platform: str = sys.platform) -> float:
    """Signed scroll steps for one wheel event. Positive scrolls down."""
    number = getattr(event, "num", None)
    if number == 4:
        return -float(STEPS_PER_NOTCH)
    if number == 5:
        return float(STEPS_PER_NOTCH)
    delta = float(getattr(event, "delta", 0) or 0)
    if platform == "win32":
        return -delta / WINDOWS_NOTCH * STEPS_PER_NOTCH
    # macOS already reports whole lines per event, with the sign meaning up.
    return -delta


class WheelScroller:
    """Scrolls one canvas while the pointer is over a given area of it."""

    def __init__(self, area: tk.Misc, canvas: tk.Canvas, platform: str = sys.platform):
        self.area = area
        self.canvas = canvas
        self.platform = platform
        self._pending = 0.0
        canvas.configure(yscrollincrement=STEP_PIXELS)
        # Bound once, application-wide: on Windows Tk delivers the wheel to the
        # focused widget rather than the one under the pointer, so the handler
        # works out for itself whether the pointer is over this area.
        for sequence in WHEEL_EVENTS:
            area.bind_all(sequence, self.on_wheel, add="+")

    def on_wheel(self, event) -> None:
        if not self._pointer_inside(event):
            return
        self.scroll(wheel_steps(event, self.platform))

    def scroll(self, steps: float) -> int:
        """Add `steps` and scroll by however many whole steps are now due."""
        self._pending += steps
        whole = int(self._pending)  # truncates toward zero: symmetric
        if whole:
            self._pending -= whole
            self.canvas.yview_scroll(whole, "units")
        return whole

    def _pointer_inside(self, event) -> bool:
        try:
            widget = self.area.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError):
            return False
        area = str(self.area)
        while widget is not None:
            if str(widget) == area:
                return True
            widget = widget.master
        return False
