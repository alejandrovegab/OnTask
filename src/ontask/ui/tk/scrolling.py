"""Mouse wheel and trackpad scrolling for Tk canvases, on every platform.

Tk reports scrolling three different ways, and treating them alike is what
made the statistics window misbehave:

* macOS sends a burst of small whole numbers per gesture (1, 2, -1...), one
  line each. Integer-dividing those rounds 1 down to 0 but -1 down to -1, so
  one direction barely moved while the other jumped.
* Windows sends multiples of 120 per wheel notch, and precision touchpads send
  fractions of that.
* X11 sends no wheel event at all, only button 4 (up) and button 5 (down).

Sideways scrolling arrives as the same wheel event with Shift held: macOS
splits each trackpad movement into a vertical event and, for any sideways
drift of the fingers, a horizontal one right behind it. Read as vertical, that
drift (usually against the swipe) stepped the view back off the bottom and the
next tick pushed it back again, shaking the report. These views only scroll
vertically, so horizontal events are ignored.

`wheel_steps` turns any of those into a signed number of steps, keeping the
direction symmetric, and `WheelScroller` adds them up so fractional touchpad
movement accumulates smoothly instead of being lost or rounded into jumps.

The view is moved in exact pixels and stopped at the content's real edges.
Letting Tk scroll by "units" instead rounds the view to whole steps, which
carried it past the end of the report, and kept redrawing everything for each
trackpad momentum tick that arrived once the edge was reached: the jitter at
the top and bottom of a fast swipe.
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

# Set in a wheel event's state when Tk means horizontal scrolling.
SHIFT_MASK = 0x0001


def wheel_steps(event, platform: str = sys.platform) -> float:
    """Signed vertical scroll steps for one wheel event. Positive scrolls down."""
    if int(getattr(event, "state", 0) or 0) & SHIFT_MASK:
        return 0.0
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
        # Scroll by single pixels: a coarser increment makes Tk round the view
        # to it, which overshoots the end of the content.
        canvas.configure(yscrollincrement=1)
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
        """Add `steps`, move by whatever whole steps are now due.

        Returns the pixels actually moved, which is less than asked for at the
        top or bottom.
        """
        self._pending += steps
        whole = int(self._pending)  # truncates toward zero: symmetric
        if not whole:
            return 0
        self._pending -= whole
        wanted = whole * STEP_PIXELS
        moved = self._move_by(wanted)
        if moved != wanted:
            # Hit an edge. Momentum still arriving must not pile up here and
            # then fire the moment the swipe reverses.
            self._pending = 0.0
        return moved

    def _move_by(self, pixels: int) -> int:
        """Move the view by `pixels`, stopping exactly at the content's edges."""
        top, height = self._region()
        visible = self.canvas.winfo_height()
        if height <= visible:
            return 0
        current = round(self.canvas.canvasy(0) - top)
        target = min(max(current + pixels, 0), height - visible)
        if target == current:
            # Already at the edge: do nothing at all, so a stream of momentum
            # ticks costs no redraws.
            return 0
        self.canvas.yview_moveto(target / height)
        return target - current

    def _region(self) -> tuple[float, float]:
        """Top and height of the canvas's scroll region."""
        try:
            _x0, y0, _x1, y1 = (float(v) for v in str(self.canvas.cget("scrollregion")).split())
        except ValueError:
            return 0.0, 0.0
        return y0, y1 - y0

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
