"""Builds the settings window headlessly and exercises its load/save path."""

import tempfile
import unittest
from pathlib import Path

from ontask.core.browsers import ACCESSIBILITY, APPLESCRIPT, Browser
from ontask.core.config import Config

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


# The picker lists whatever browsers are installed; pinning that keeps the
# suite from depending on what happens to be on the machine running it.
INSTALLED = [
    Browser("Safari", "com.apple.Safari", "safari", ""),
    Browser("Zen", "app.zen-browser.zen", ACCESSIBILITY, ""),
    Browser("Google Chrome", "com.google.Chrome", "chromium", ""),
]


@unittest.skipUnless(HAVE_TK, "no Tk display")
class SettingsWindowTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock

        from ontask.ui.tk.settings import SettingsWindow

        patcher = mock.patch(
            "ontask.ui.tk.browser_setup.installed_browsers", return_value=list(INSTALLED)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        Config().save(self.path)
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = SettingsWindow(self.root, self.path)

    def tearDown(self):
        self.root.destroy()
        self.tmp.cleanup()

    def test_defaults_are_shown(self):
        self.assertEqual(self.window.intervals_var.get(), "3, 5, 7, 10, 14, 20")
        self.assertEqual(self.window.advance_var.get(), "1, 2, 2, 3, 3")
        self.assertEqual(self.window.policy_var.get(), "Re-alert, then count as No")
        self.assertEqual(self.window.prompt_ui_var.get(), "Floating window")

    def test_ladder_preview_reflects_edits(self):
        self.window.intervals_var.set("1, 2, 4")
        self.window.advance_var.set("2, 5")
        text = self.window.ladder_preview.cget("text")
        self.assertIn("1 min --2 yes--> 2 min --5 yes--> 4 min", text)

    def test_preview_flags_bad_input(self):
        self.window.intervals_var.set("three, five")
        self.assertIn("comma separated numbers", self.window.ladder_preview.cget("text"))

    def test_editing_and_saving_round_trips(self):
        self.window.intervals_var.set("2, 6, 12")
        self.window.advance_var.set("1, 4")
        self.window.distraction_var.set("90")
        self.window.blocked_var.set("5")
        self.window.policy_var.set("Pause the session")
        self.window.prompt_ui_var.set("Notification, then window")
        self.window.approved_text.delete("1.0", "end")
        self.window.approved_text.insert("1.0", "app:Code\nsite:example.com\n")
        self.window.save()

        saved = Config.load(self.path)
        self.assertEqual(saved.reminder.intervals_minutes, [2, 6, 12])
        self.assertEqual(saved.reminder.advance_after_yes, [1, 4])
        self.assertEqual(saved.reminder.distraction_grace_seconds, 90)
        self.assertEqual(saved.reminder.no_response.policy, "pause_session")
        self.assertEqual(saved.general.prompt_ui, "both")
        self.assertEqual(saved.profile("Deep Work").approved, ["app:Code", "site:example.com"])

    def test_rejects_non_increasing_intervals(self):
        from unittest import mock

        self.window.intervals_var.set("10, 5, 20")
        with mock.patch("tkinter.messagebox.showerror") as error:
            self.window.save()
        error.assert_called_once()
        self.assertIn("increase", error.call_args[0][1])
        self.assertEqual(Config.load(self.path).reminder.intervals_minutes, [3, 5, 7, 10, 14, 20])

    def test_rejects_unparseable_numbers(self):
        from unittest import mock

        self.window.distraction_var.set("soon")
        with mock.patch("tkinter.messagebox.showerror") as error:
            self.window.save()
        error.assert_called_once()

    def test_profile_edits_survive_switching(self):
        self.window.approved_text.delete("1.0", "end")
        self.window.approved_text.insert("1.0", "app:OnlyThis\n")
        self.window.profile_list.selection_clear(0, "end")
        self.window.profile_list.selection_set(1)
        self.window._on_profile_selected()
        self.assertEqual(self.window.current_profile, "Writing")
        self.window.profile_list.selection_clear(0, "end")
        self.window.profile_list.selection_set(0)
        self.window._on_profile_selected()
        self.assertIn("app:OnlyThis", self.window.approved_text.get("1.0", "end"))

    def test_add_and_remove_profile(self):
        from unittest import mock

        self.window.add_profile()
        self.assertIn("New Profile", self.window.config.profile_names())
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            self.window.remove_profile()
        self.assertNotIn("New Profile", self.window.config.profile_names())

    def test_only_safari_is_ticked_before_anything_is_chosen(self):
        ticked = [b.name for b in self.window.browser_list.selected()]
        self.assertEqual(ticked, ["Safari"])

    def test_untracked_browsers_are_still_offered(self):
        # Everything installed is listed; only what is ticked gets tracked.
        listed = {b.name for b, _ in self.window.browser_list._rows}
        self.assertIn("Safari", listed)

    def test_ticking_a_browser_persists_its_bundle_identity(self):
        for browser, var in self.window.browser_list._rows:
            var.set(browser.bundle_id in ("com.apple.Safari", "app.zen-browser.zen"))
        self.window.save()

        browsers = Config.load(self.path).general.browsers
        by_id = {b.bundle_id: b for b in browsers}
        self.assertEqual(set(by_id), {"com.apple.Safari", "app.zen-browser.zen"})
        self.assertEqual(by_id["app.zen-browser.zen"].flavour, ACCESSIBILITY)

    def test_unticking_everything_turns_url_tracking_off(self):
        for _, var in self.window.browser_list._rows:
            var.set(False)
        self.window.save()
        self.assertEqual(Config.load(self.path).general.browsers, [])

    def test_adding_a_browser_from_finder_reads_the_bundle(self):
        import plistlib
        import tempfile
        from pathlib import Path as _Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            app = _Path(tmp) / "Comet.app"
            (app / "Contents" / "Resources").mkdir(parents=True)
            (app / "Contents" / "Resources" / "scripting.sdef").write_text("")
            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {"CFBundleIdentifier": "com.example.comet", "CFBundleName": "Comet"}, handle
                )
            with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(app)):
                self.window.browser_list.add_from_finder()
            self.window.save()

        browsers = Config.load(self.path).general.browsers
        comet = [b for b in browsers if b.bundle_id == "com.example.comet"]
        self.assertEqual(len(comet), 1)
        self.assertEqual(comet[0].name, "Comet")
        self.assertEqual(comet[0].flavour, APPLESCRIPT)

    def test_clock_penalty_round_trips(self):
        self.window.penalty_on_var.set(True)
        self.window.penalty_match_var.set(False)
        self.window.penalty_fixed_var.set("45")
        self.window.save()

        penalty = Config.load(self.path).reminder.clock_penalty
        self.assertTrue(penalty.enabled)
        self.assertFalse(penalty.match_situation)
        self.assertEqual(penalty.fixed_seconds, 45.0)

    def test_prompt_position_round_trips(self):
        self.window.position_var.set("Bottom right")
        self.window.save()
        self.assertEqual(Config.load(self.path).general.prompt_position, "bottom_right")


@unittest.skipUnless(HAVE_TK, "no Tk display")
class FirstRunWindowTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock

        from ontask.ui.tk.browser_setup import FirstRunWindow

        patcher = mock.patch(
            "ontask.ui.tk.browser_setup.installed_browsers", return_value=list(INSTALLED)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        Config().save(self.path)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self._destroy)
        self.window = FirstRunWindow(self.root, self.path)

    def _destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _edit_elsewhere(self):
        # Settings saves a change while the picker is still open.
        other = Config.load(self.path)
        other.profile().approved.append("app:Figma")
        other.save()

    def test_continuing_keeps_changes_saved_meanwhile(self):
        self._edit_elsewhere()
        self.window.finish()
        saved = Config.load(self.path)
        self.assertIn("app:Figma", saved.profile().approved)
        self.assertTrue(saved.setup_complete)

    def test_closing_keeps_changes_saved_meanwhile(self):
        self._edit_elsewhere()
        self.window.answer(None)
        saved = Config.load(self.path)
        self.assertIn("app:Figma", saved.profile().approved)
        self.assertTrue(saved.setup_complete)
        self.assertEqual([b.bundle_id for b in saved.general.browsers], ["com.apple.Safari"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
