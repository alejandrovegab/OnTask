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
direction symmetric, and `WheelScroller` turns steps into pixels, adds them up
so fractional touchpad movement is not lost, and applies them at most once per
frame.

Tk 8.6 cannot scroll a trackpad truly smoothly on macOS: it rounds each event to
a whole line before Python sees it, and redraws every embedded widget on each
move. Small steps and one redraw per frame hide most of that; the native window
that replaces this one on macOS has real pixel-precise, momentum scrolling.
"""

from __future__ import annotations

import sys
import tkinter as tk

# Pixels per scroll step. macOS sends a burst of one-line steps per gesture, so
# each is kept small for the motion to read as continuous; a Windows or X11
# notch is three steps of the larger size. Tk's own default step, a tenth of
# the visible height, is what made the window lurch.
STEP_PIXELS = {"darwin": 4}
DEFAULT_STEP_PIXELS = 16

# Wheel events arriving within one frame are applied together, so a fast swipe
# costs one redraw per frame rather than one per event.
FRAME_MS = 16

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


def step_pixels(platform: str = sys.platform) -> int:
    return STEP_PIXELS.get(platform, DEFAULT_STEP_PIXELS)


class WheelScroller:
    """Scrolls one canvas while the pointer is over a given area of it."""

    def __init__(self, area: tk.Misc, canvas: tk.Canvas, platform: str = sys.platform):
        self.area = area
        self.canvas = canvas
        self.platform = platform
        self._pending = 0.0
        self._flush_id: str | None = None
        # Scroll in single pixels; the step size is applied in `on_wheel`.
        canvas.configure(yscrollincrement=1)
        # Bound once, application-wide: on Windows Tk delivers the wheel to the
        # focused widget rather than the one under the pointer, so the handler
        # works out for itself whether the pointer is over this area.
        for sequence in WHEEL_EVENTS:
            area.bind_all(sequence, self.on_wheel, add="+")

    def on_wheel(self, event) -> None:
        if not self._pointer_inside(event):
            return
        self._pending += wheel_steps(event, self.platform) * step_pixels(self.platform)
        if self._flush_id is None:
            self._flush_id = self.canvas.after(FRAME_MS, self.flush)

    def scroll(self, pixels: float) -> int:
        """Add `pixels` and apply them now; returns the whole pixels moved."""
        self._pending += pixels
        return self.flush()

    def flush(self) -> int:
        self._flush_id = None
        whole = int(self._pending)  # truncates toward zero: symmetric
        if whole:
            self._pending -= whole
            try:
                self.canvas.yview_scroll(whole, "units")
            except tk.TclError:
                # The window closed while a frame's worth of scrolling waited.
                return 0
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
