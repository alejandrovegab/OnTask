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

    def _save_button(self):
        footer = self.window.status.master
        return next(b for b in footer.winfo_children() if b.cget("text") == "Save")

    def test_the_buttons_keep_their_place_when_the_window_is_short(self):
        # A 982 px laptop screen pushed Save off the bottom edge. Tk takes the
        # missing space from whatever was packed last, so the button row must
        # be packed, at the bottom, before the tabs.
        footer = self._save_button().master
        packed = footer.master.pack_slaves()
        self.assertIs(packed[0], footer)
        self.assertEqual(footer.pack_info()["side"], "bottom")

    def _fitted_height(self, usable: int, screen: int = 982) -> int:
        """The height fit_to_screen asks for, for a window that wants 2000 px.

        The request is checked rather than the window's size: an unshown
        window reports its size differently on each platform.
        """
        from unittest import mock

        from ontask.ui.tk import window

        with (
            mock.patch.object(window, "_usable_height", return_value=usable),
            mock.patch.object(self.root, "winfo_screenheight", return_value=screen),
            mock.patch.object(self.root, "winfo_reqheight", return_value=2000),
            mock.patch.object(self.root, "geometry") as geometry,
        ):
            window.fit_to_screen(self.root)
        size = geometry.call_args[0][0]
        return int(size.split("x")[1])

    def test_the_window_opens_no_taller_than_the_screen(self):
        from ontask.ui.tk.window import TITLE_BAR

        self.assertEqual(self._fitted_height(usable=948), 948 - TITLE_BAR)

    def test_without_the_usable_height_a_margin_is_left(self):
        from ontask.ui.tk.window import SCREEN_MARGIN

        self.assertEqual(self._fitted_height(usable=0, screen=800), 800 - SCREEN_MARGIN)

    def test_a_window_that_fits_keeps_its_size(self):
        from unittest import mock

        from ontask.ui.tk import window

        with (
            mock.patch.object(window, "_usable_height", return_value=948),
            mock.patch.object(self.root, "winfo_reqheight", return_value=600),
            mock.patch.object(self.root, "geometry") as geometry,
        ):
            window.fit_to_screen(self.root)
        self.assertTrue(geometry.call_args[0][0].endswith("x600"))

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
        self.window.disapproved_var.set("5")
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
class UnsavedChangesTest(unittest.TestCase):
    """Closing with edits asks first; Save keeps what the menu wrote meanwhile."""

    def setUp(self):
        from unittest import mock

        from ontask.ui.tk.settings import SettingsWindow

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
        self.window = SettingsWindow(self.root, self.path)

    def _destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _closed(self) -> bool:
        try:
            self.root.winfo_exists()
        except tk.TclError:
            return True
        return False

    def _close_answering(self, choice):
        from unittest import mock

        with mock.patch("ontask.ui.tk.settings.ask_save_changes", return_value=choice) as ask:
            self.window.close()
        return ask

    def _menu_approves(self, rule="site:example.com"):
        # What the menu's Approve item does: load, add, save.
        other = Config.load(self.path)
        other.profile().approved.append(rule)
        other.save()

    def _set_approved(self, text):
        self.window.approved_text.delete("1.0", "end")
        self.window.approved_text.insert("1.0", text)

    # -- what counts as unsaved --------------------------------------------

    def test_an_untouched_window_has_nothing_unsaved(self):
        self.assertFalse(self.window.has_unsaved_changes())

    def test_an_edit_is_unsaved_until_it_is_undone(self):
        self.window.distraction_var.set("90")
        self.assertTrue(self.window.has_unsaved_changes())
        self.window.distraction_var.set("60")
        self.assertFalse(self.window.has_unsaved_changes())

    def test_an_invalid_value_counts_as_a_change(self):
        self.window.distraction_var.set("soon")
        self.assertTrue(self.window.has_unsaved_changes())

    def test_a_rule_edit_counts_on_any_profile(self):
        self._set_approved("app:Code\n")
        self.window.profile_list.selection_clear(0, "end")
        self.window.profile_list.selection_set(1)
        self.window._on_profile_selected()
        self.assertTrue(self.window.has_unsaved_changes())

    def test_profile_changes_count(self):
        self.window.add_profile()
        self.assertTrue(self.window.has_unsaved_changes())

    def test_ticking_a_browser_counts(self):
        for browser, var in self.window.browser_list._rows:
            if browser.name == "Zen":
                var.set(True)
        self.assertTrue(self.window.has_unsaved_changes())

    def test_nothing_is_unsaved_after_saving_or_reverting(self):
        self.window.poll_var.set("3")
        self.window.save()
        self.assertFalse(self.window.has_unsaved_changes())
        self.window.poll_var.set("4")
        self.window.revert()
        self.assertFalse(self.window.has_unsaved_changes())

    def test_restored_defaults_are_unsaved_when_the_file_differs(self):
        from unittest import mock

        self.window.poll_var.set("3")
        self.window.save()
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            self.window.restore_defaults()
        self.assertTrue(self.window.has_unsaved_changes())

    def test_a_menu_change_alone_is_not_unsaved(self):
        self._menu_approves()
        self.assertFalse(self.window.has_unsaved_changes())

    # -- closing -----------------------------------------------------------

    def test_closing_an_untouched_window_does_not_ask(self):
        ask = self._close_answering("cancel")
        ask.assert_not_called()
        self.assertTrue(self._closed())

    def test_save_saves_then_closes(self):
        self.window.poll_var.set("3")
        self._close_answering("save")
        self.assertTrue(self._closed())
        self.assertEqual(Config.load(self.path).general.poll_seconds, 3)

    def test_dont_save_closes_and_leaves_the_file_alone(self):
        before = self.path.read_text()
        self.window.poll_var.set("3")
        self._close_answering("dont_save")
        self.assertTrue(self._closed())
        self.assertEqual(self.path.read_text(), before)

    def test_cancel_keeps_the_window_and_the_edit(self):
        self.window.poll_var.set("3")
        self._close_answering("cancel")
        self.assertFalse(self._closed())
        self.assertEqual(self.window.poll_var.get(), "3")

    def test_save_with_an_invalid_value_stays_open(self):
        from unittest import mock

        self.window.distraction_var.set("soon")
        with mock.patch("tkinter.messagebox.showerror") as error:
            self._close_answering("save")
        error.assert_called_once()
        self.assertFalse(self._closed())

    def test_the_title_bar_close_button_asks_too(self):
        from unittest import mock

        # Tk hands back the command's Tcl name, not the method, so check it by
        # calling it.
        self.window.poll_var.set("3")
        with mock.patch("ontask.ui.tk.settings.ask_save_changes", return_value="cancel") as ask:
            self.root.tk.call(self.root.protocol("WM_DELETE_WINDOW"))
        ask.assert_called_once()

    # -- saving over menu changes ------------------------------------------

    def test_save_keeps_a_rule_the_menu_added_meanwhile(self):
        self._menu_approves("site:example.com")
        self.window.poll_var.set("3")
        self.window.save()
        saved = Config.load(self.path)
        self.assertIn("site:example.com", saved.profile("Deep Work").approved)
        self.assertEqual(saved.general.poll_seconds, 3)

    def test_save_shows_what_the_menu_added(self):
        self._menu_approves("site:example.com")
        self.window.poll_var.set("3")
        self.window.save()
        self.assertIn("site:example.com", self.window.approved_text.get("1.0", "end"))

    def test_both_sides_rule_additions_are_kept(self):
        self._menu_approves("site:example.com")
        self._set_approved(self.window.approved_text.get("1.0", "end") + "app:Figma\n")
        self.window.save()
        approved = Config.load(self.path).profile("Deep Work").approved
        self.assertIn("site:example.com", approved)
        self.assertIn("app:Figma", approved)

    def test_a_renamed_profile_keeps_the_menus_rule(self):
        from unittest import mock

        self._menu_approves("site:example.com")
        with mock.patch("ontask.ui.tk.settings._ask_text", return_value="Focus"):
            self.window.rename_profile()
        self.window.save()
        saved = Config.load(self.path)
        self.assertEqual(saved.profile_names(), ["Focus", "Writing"])
        self.assertIn("site:example.com", saved.profile("Focus").approved)
        self.assertEqual(saved.active_profile, "Focus")

    def test_a_profile_switched_from_the_menu_stays_active(self):
        other = Config.load(self.path)
        other.active_profile = "Writing"
        other.save()
        self.window.poll_var.set("3")
        self.window.save()
        self.assertEqual(Config.load(self.path).active_profile, "Writing")

    def test_a_damaged_file_is_replaced_with_the_window_as_a_whole(self):
        self.window.poll_var.set("3")
        self.path.write_text("{not json")
        self.window.save()
        saved = Config.load(self.path)
        self.assertEqual(saved.general.poll_seconds, 3)
        self.assertEqual(saved.profile_names(), ["Deep Work", "Writing"])


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


def _visible_text(widget) -> list[str]:
    """Every `text` shown by `widget` and everything inside it."""
    found = []
    try:
        text = str(widget.cget("text"))
    except Exception:
        text = ""
    if text:
        found.append(text)
    for child in widget.winfo_children():
        found.extend(_visible_text(child))
    return found


@unittest.skipUnless(HAVE_TK, "no Tk display")
class WindowWordingTest(unittest.TestCase):
    """OnTask reminds rather than blocks, and unlisted things are "not listed"."""

    def setUp(self):
        from unittest import mock

        patcher = mock.patch(
            "ontask.ui.tk.browser_setup.installed_browsers", return_value=list(INSTALLED)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.json"
        Config().save(self.path)

    def _texts(self, window_class) -> str:
        root = tk.Tk()
        root.withdraw()
        try:
            window_class(root, self.path)
            return "\n".join(_visible_text(root))
        finally:
            root.destroy()

    def _assert_no_old_words(self, text: str) -> None:
        for word in ("blocked", "block ", "unapproved"):
            self.assertNotIn(word, text.lower())

    def test_settings(self):
        from ontask.ui.tk.settings import SettingsWindow

        text = self._texts(SettingsWindow)
        self._assert_no_old_words(text)
        self.assertIn("Disapproved apps and sites", text)
        self.assertIn("on a disapproved app", text)

    def test_statistics(self):
        from ontask.ui.tk.stats import StatsWindow

        text = self._texts(StatsWindow)
        self._assert_no_old_words(text)
        self.assertIn("on disapproved apps and sites", text)

    def test_first_run(self):
        from ontask.ui.tk.browser_setup import FirstRunWindow

        text = self._texts(FirstRunWindow)
        self._assert_no_old_words(text)
        self.assertIn("approved and\ndisapproved lists", text)
