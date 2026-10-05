"""Wheel and trackpad scrolling for the Tk windows."""

import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import tkinter as tk

    from ontask.ui.tk.scrolling import STEPS_PER_NOTCH, WheelScroller, wheel_steps

    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def wheel(delta=0, num=None):
    return SimpleNamespace(delta=delta, num=num, x_root=0, y_root=0)


@unittest.skipUnless(HAVE_TK, "no Tk display")
class WheelStepsTest(unittest.TestCase):
    def test_macos_reports_lines_and_up_is_positive(self):
        self.assertEqual(wheel_steps(wheel(1), "darwin"), -1)
        self.assertEqual(wheel_steps(wheel(-1), "darwin"), 1)

    def test_windows_notches_are_three_steps(self):
        self.assertEqual(wheel_steps(wheel(120), "win32"), -STEPS_PER_NOTCH)
        self.assertEqual(wheel_steps(wheel(-240), "win32"), 2 * STEPS_PER_NOTCH)

    def test_x11_uses_buttons_four_and_five(self):
        self.assertEqual(wheel_steps(wheel(num=4), "linux"), -STEPS_PER_NOTCH)
        self.assertEqual(wheel_steps(wheel(num=5), "linux"), STEPS_PER_NOTCH)


@unittest.skipUnless(HAVE_TK, "no Tk display")
class WheelScrollerTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.area = tk.Frame(self.root)
        self.canvas = tk.Canvas(self.area)
        self.scroller = WheelScroller(self.area, self.canvas, platform="darwin")
        self.scrolled = []
        self.canvas.yview_scroll = lambda n, what: self.scrolled.append(n)

    def test_both_directions_move_the_same_amount(self):
        # The original bug: -1 // 3 is -1 but 1 // 3 is 0, so one way stuck.
        for _ in range(5):
            self.scroller.scroll(1)
        for _ in range(5):
            self.scroller.scroll(-1)
        self.assertEqual(sum(s for s in self.scrolled if s > 0), 5)
        self.assertEqual(sum(s for s in self.scrolled if s < 0), -5)

    def test_fractional_touchpad_movement_adds_up(self):
        for _ in range(8):
            self.scroller.scroll(0.25)  # a Windows precision touchpad, say
        self.assertEqual(sum(self.scrolled), 2)

    def test_steps_are_small_fixed_pixels(self):
        self.assertEqual(int(self.canvas.cget("yscrollincrement")), 16)

    def test_only_scrolls_with_the_pointer_over_the_area(self):
        elsewhere = tk.Frame(self.root)
        with mock.patch.object(self.area, "winfo_containing", return_value=elsewhere):
            self.scroller.on_wheel(wheel(-3))
        self.assertEqual(self.scrolled, [])
        with mock.patch.object(self.area, "winfo_containing", return_value=self.canvas):
            self.scroller.on_wheel(wheel(-3))
        self.assertEqual(self.scrolled, [3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
