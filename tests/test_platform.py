"""Tests for the macOS focus provider and the menu bar shell.

The AppleScript call is stubbed so these run without touching a real browser or
triggering the Automation permission prompt.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ontask.browsers import (
    ACCESSIBILITY,
    BROWSERS,
    CHROMIUM,
    SAFARI,
    UNSUPPORTED,
    flavour_of,
    needs_accessibility,
    needs_automation,
)
from ontask.focus import FocusTarget

try:
    import AppKit  # noqa: F401

    HAVE_PYOBJC = True
except Exception:
    HAVE_PYOBJC = False

try:
    import rumps  # noqa: F401

    HAVE_RUMPS = True
except Exception:
    HAVE_RUMPS = False


class BrowserCatalogueTest(unittest.TestCase):
    def test_every_entry_has_a_known_flavour(self):
        for name, (bundle, flavour) in BROWSERS.items():
            self.assertTrue(bundle, name)
            self.assertIn(flavour, (SAFARI, CHROMIUM, ACCESSIBILITY, UNSUPPORTED), name)

    def test_gecko_browsers_use_the_accessibility_route(self):
        for name in ("Firefox", "Zen", "LibreWolf"):
            self.assertEqual(flavour_of(name), ACCESSIBILITY, name)
            self.assertTrue(needs_accessibility(name), name)
            self.assertFalse(needs_automation(name), name)

    def test_applescript_browsers_need_automation(self):
        for name in ("Safari", "Google Chrome", "Arc"):
            self.assertTrue(needs_automation(name), name)
            self.assertFalse(needs_accessibility(name), name)

    def test_unknown_browser_is_unsupported(self):
        self.assertEqual(flavour_of("Netscape Navigator"), UNSUPPORTED)


class FocusTargetTest(unittest.TestCase):
    def test_host_strips_port_and_credentials(self):
        target = FocusTarget(url="https://user:pw@example.com:8443/a/b")
        self.assertEqual(target.host, "example.com")
        self.assertEqual(target.path, "/a/b")

    def test_label_prefers_site_over_app(self):
        target = FocusTarget(app_name="Safari", url="https://www.youtube.com/feed")
        self.assertEqual(target.label(), "youtube.com")
        self.assertEqual(target.describe(), "youtube.com (Safari)")

    def test_key_distinguishes_sites_from_apps(self):
        self.assertEqual(FocusTarget(url="https://a.com/x").key(), "site:a.com")
        self.assertEqual(FocusTarget(app_name="Slack", bundle_id="com.tinyspeck.x").key(), "app:com.tinyspeck.x")


@unittest.skipUnless(HAVE_PYOBJC, "PyObjC not installed")
class MacFocusProviderTest(unittest.TestCase):
    def _provider(self, app_name, bundle, script_result=("", 0, "")):
        from ontask.focus.macos import MacFocusProvider

        provider = MacFocusProvider.__new__(MacFocusProvider)
        provider.timeout = 1.0
        provider.blocked_browsers = set()
        provider.needs_accessibility = set()
        provider.last_error = ""
        provider._cache = None
        provider._workspace = mock.Mock()
        app = mock.Mock()
        app.localizedName.return_value = app_name
        app.bundleIdentifier.return_value = bundle
        app.processIdentifier.return_value = 4242
        provider._workspace.frontmostApplication.return_value = app
        stdout, code, stderr = script_result
        self._run = mock.Mock(return_value=mock.Mock(returncode=code, stdout=stdout, stderr=stderr))
        return provider

    def test_non_browser_skips_applescript(self):
        provider = self._provider("Xcode", "com.apple.dt.Xcode")
        with mock.patch("subprocess.run", self._run):
            target = provider.current(["Safari"])
        self._run.assert_not_called()
        self.assertEqual(target.app_name, "Xcode")
        self.assertEqual(target.url, "")

    def test_browser_returns_url_and_title(self):
        provider = self._provider("Safari", "com.apple.Safari", ("https://github.com/x\nGitHub\n", 0, ""))
        with mock.patch("subprocess.run", self._run):
            target = provider.current(["Safari"])
        self.assertEqual(target.url, "https://github.com/x")
        self.assertEqual(target.title, "GitHub")
        self.assertEqual(target.host, "github.com")

    def test_disabled_browser_is_not_queried(self):
        provider = self._provider("Arc", "company.thebrowser.Browser")
        with mock.patch("subprocess.run", self._run):
            target = provider.current(["Safari"])  # Arc switched off in settings
        self._run.assert_not_called()
        self.assertEqual(target.url, "")

    def test_denied_automation_stops_repeat_prompts(self):
        provider = self._provider("Safari", "com.apple.Safari", ("", 1, "execution error: -1743"))
        with mock.patch("subprocess.run", self._run):
            provider.current(["Safari"])
            provider.current(["Safari"])
        self.assertEqual(self._run.call_count, 1)
        self.assertIn("Safari", provider.blocked_browsers)
        self.assertIn("System Settings", provider.permission_hint())

    def test_firefox_reads_the_address_bar_instead_of_applescript(self):
        provider = self._provider("Firefox", "org.mozilla.firefox")
        with mock.patch("subprocess.run", self._run), \
             mock.patch("ontask.focus.ax.accessibility_trusted", return_value=True), \
             mock.patch("ontask.focus.ax.address_bar", return_value=("https://github.com/x", "GitHub")):
            target = provider.current(["Firefox"])
        self._run.assert_not_called()  # never shells out to osascript
        self.assertEqual(target.host, "github.com")
        self.assertEqual(target.title, "GitHub")

    def test_firefox_without_accessibility_degrades_to_app_only(self):
        provider = self._provider("Firefox", "org.mozilla.firefox")
        with mock.patch("ontask.focus.ax.accessibility_trusted", return_value=False):
            target = provider.current(["Firefox"])
        self.assertEqual(target.app_name, "Firefox")
        self.assertEqual(target.url, "")
        self.assertIn("Firefox", provider.needs_accessibility)
        self.assertIn("Accessibility", provider.permission_hint())

    def test_accessibility_errors_do_not_propagate(self):
        provider = self._provider("Firefox", "org.mozilla.firefox")
        with mock.patch("ontask.focus.ax.accessibility_trusted", return_value=True), \
             mock.patch("ontask.focus.ax.address_bar", side_effect=RuntimeError("tree changed")):
            target = provider.current(["Firefox"])
        self.assertEqual(target.url, "")
        self.assertIn("tree changed", provider.last_error)

    def test_hint_covers_both_permission_kinds(self):
        provider = self._provider("Safari", "com.apple.Safari")
        provider.blocked_browsers.add("Safari")
        provider.needs_accessibility.add("Firefox")
        hint = provider.permission_hint()
        self.assertIn("Automation", hint)
        self.assertIn("Accessibility", hint)

    def test_result_is_cached_between_rapid_polls(self):
        provider = self._provider("Safari", "com.apple.Safari", ("https://a.com\nA\n", 0, ""))
        with mock.patch("subprocess.run", self._run):
            provider.current(["Safari"])
            provider.current(["Safari"])
        self.assertEqual(self._run.call_count, 1)


@unittest.skipUnless(HAVE_RUMPS and HAVE_PYOBJC, "rumps/PyObjC not installed")
class MenuBarTest(unittest.TestCase):
    """Builds the whole menu without starting the run loop."""

    def test_menu_builds_and_refreshes(self):
        from ontask.config import Config
        from ontask.ui.menubar_macos import OnTaskApp

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = Config()
            cfg.general.hotkeys.toggle_session = ""  # avoid the Accessibility prompt
            cfg.general.hotkeys.answer_yes = ""
            cfg.general.hotkeys.answer_no = ""
            cfg.save(path)
            with mock.patch("ontask.app.Config.load", return_value=Config.load(path)), \
                 mock.patch("rumps.Timer"):
                app = OnTaskApp()
                app.refresh()
                titles = [item.title for item in app.status_items.values()]
                self.assertTrue(any("No session running" in t for t in titles))
                app.controller.start_session()
                app.refresh()
                titles = [item.title for item in app.status_items.values()]
                self.assertTrue(any("Session:" in t for t in titles))
                self.assertEqual(app.toggle_item.title, "End Session")
                self.assertIn("Deep Work", app._profile_items)


if __name__ == "__main__":
    unittest.main(verbosity=2)
