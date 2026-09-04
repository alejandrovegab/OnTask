"""Builds the settings window headlessly and exercises its load/save path."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ontask.browsers import ACCESSIBILITY
from ontask.config import Config

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "no Tk display")
class SettingsWindowTest(unittest.TestCase):
    def setUp(self):
        from ontask.ui.settings_app import SettingsWindow

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

    def test_only_safari_is_listed_before_anything_is_added(self):
        self.assertEqual([b.name for b in self.window.browsers], ["Safari"])
        self.assertIn("com.apple.Safari", self.window.browser_list.get(0))

    def test_adding_a_browser_from_finder_keeps_the_bundle_identity(self):
        import plistlib
        import tempfile
        from pathlib import Path as _Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            app = _Path(tmp) / "Zen.app"
            (app / "Contents" / "Resources").mkdir(parents=True)
            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {"CFBundleIdentifier": "app.zen-browser.zen", "CFBundleName": "Zen"}, handle
                )
            with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(app)), \
                 mock.patch("tkinter.messagebox.showinfo") as told:
                self.window.add_browser()
                self.window.add_browser()  # adding twice is a no-op
            told.assert_called_once()
            self.window.save()

        browsers = Config.load(self.path).general.browsers
        self.assertEqual([b.name for b in browsers], ["Safari", "Zen"])
        zen = browsers[1]
        self.assertEqual(zen.bundle_id, "app.zen-browser.zen")
        self.assertEqual(zen.flavour, ACCESSIBILITY)

    def test_removing_a_browser_persists(self):
        self.window.browser_list.selection_set(0)
        self.window.remove_browser()
        self.window.save()
        self.assertEqual(Config.load(self.path).general.browsers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
