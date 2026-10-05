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
    """A real, mapped canvas: the edge behaviour depends on Tk's own geometry."""

    VISIBLE = 300
    CONTENT = 1007  # deliberately not a multiple of the 16 px step
    BOTTOM = CONTENT - VISIBLE

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry(f"300x{self.VISIBLE}+-3000+-3000")  # off-screen
        self.addCleanup(self.root.destroy)
        self.area = tk.Frame(self.root)
        self.area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.area, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.configure(scrollregion=(0, 0, 300, self.CONTENT))
        self.scroller = WheelScroller(self.area, self.canvas, platform="darwin")
        self.root.update()

    def top(self):
        self.root.update()
        return round(self.canvas.canvasy(0))

    def test_a_step_moves_sixteen_pixels(self):
        self.assertEqual(self.scroller.scroll(1), 16)
        self.assertEqual(self.top(), 16)

    def test_both_directions_move_the_same_amount(self):
        # The original bug: -1 // 3 is -1 but 1 // 3 is 0, so one way stuck.
        self.canvas.yview_moveto(320 / self.CONTENT)
        for _ in range(5):
            self.scroller.scroll(1)
        self.assertEqual(self.top(), 320 + 80)
        for _ in range(5):
            self.scroller.scroll(-1)
        self.assertEqual(self.top(), 320)

    def test_fractional_touchpad_movement_adds_up(self):
        for _ in range(8):
            self.scroller.scroll(0.25)  # a Windows precision touchpad, say
        self.assertEqual(self.top(), 32)

    def test_the_bottom_is_reached_exactly_not_overshot(self):
        for _ in range(100):
            self.scroller.scroll(1)
        self.assertEqual(self.top(), self.BOTTOM)  # Tk's own units stopped at 720

    def test_momentum_at_an_edge_does_not_redraw(self):
        for _ in range(100):
            self.scroller.scroll(1)
        with mock.patch.object(self.canvas, "yview_moveto") as moveto:
            for _ in range(20):
                self.assertEqual(self.scroller.scroll(1), 0)
        moveto.assert_not_called()
        self.canvas.yview_moveto(0)
        self.assertEqual(self.top(), 0)
        with mock.patch.object(self.canvas, "yview_moveto") as moveto:
            for _ in range(20):
                self.assertEqual(self.scroller.scroll(-1), 0)
        moveto.assert_not_called()

    def test_leftover_momentum_is_dropped_at_the_edge(self):
        for _ in range(100):
            self.scroller.scroll(1)
        self.scroller.scroll(0.9)  # a partial step, then the edge
        self.scroller.scroll(1)
        self.assertEqual(self.scroller._pending, 0)
        self.assertEqual(self.scroller.scroll(-1), -16, "reversing moves at once")

    def test_content_shorter_than_the_window_never_moves(self):
        self.canvas.configure(scrollregion=(0, 0, 300, 200))
        self.assertEqual(self.scroller.scroll(5), 0)

    def test_only_scrolls_with_the_pointer_over_the_area(self):
        elsewhere = tk.Frame(self.root)
        with mock.patch.object(self.area, "winfo_containing", return_value=elsewhere):
            self.scroller.on_wheel(wheel(-3))
        self.assertEqual(self.top(), 0)
        with mock.patch.object(self.area, "winfo_containing", return_value=self.canvas):
            self.scroller.on_wheel(wheel(-3))
        self.assertEqual(self.top(), 48)


if __name__ == "__main__":
    unittest.main(verbosity=2)
