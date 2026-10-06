"""Tests for the macOS focus provider and the menu bar shell.

The AppleScript call is stubbed so these run without touching a real browser or
triggering the Automation permission prompt.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ontask.core.browsers import (
    ACCESSIBILITY,
    APPLESCRIPT,
    CHROMIUM,
    SAFARI,
    UNSUPPORTED,
    Browser,
    BrowserError,
    app_bundle_root,
    coerce,
    default_browsers,
    inspect_app,
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


def _fake_app(root, name, bundle_id, *, plist_extra=None, resources=()):
    """Build a minimal .app on disk so bundle inspection can be tested."""
    import plistlib

    app = Path(root) / f"{name}.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    info = {"CFBundleIdentifier": bundle_id, "CFBundleName": name}
    info.update(plist_extra or {})
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    for filename in resources:
        (app / "Contents" / "Resources" / filename).write_text("")
    return app


class BrowserDefaultsTest(unittest.TestCase):
    def test_safari_is_the_only_browser_out_of_the_box(self):
        browsers = default_browsers()
        self.assertEqual([b.name for b in browsers], ["Safari"])
        self.assertEqual(browsers[0].bundle_id, "com.apple.Safari")
        self.assertEqual(browsers[0].flavour, SAFARI)

    def test_browsers_are_matched_on_bundle_id_not_name(self):
        # Zen's process reports "zen" while the app is called "Zen".
        zen = Browser("Zen", "app.zen-browser.zen", ACCESSIBILITY)
        self.assertTrue(zen.matches("zen", "app.zen-browser.zen"))
        self.assertFalse(zen.matches("Zen", "org.mozilla.firefox"))

    def test_a_matching_name_without_the_id_is_not_a_match(self):
        browser = Browser("Odd", "com.example.odd", CHROMIUM)
        self.assertFalse(browser.matches("Odd", ""))
        self.assertFalse(browser.matches("Odd", "com.example.other"))

    def test_entries_without_a_bundle_id_are_upgraded_or_dropped(self):
        # A name cannot address AppleScript or match the frontmost app, and
        # Settings could not show such an entry, so it must not survive load.
        upgraded = Browser.from_dict({"name": "Zen"})
        self.assertEqual(upgraded.bundle_id, "app.zen-browser.zen")
        self.assertEqual(upgraded.flavour, ACCESSIBILITY)
        self.assertIsNone(Browser.from_dict({"name": "Orion", "flavour": "safari"}))

    def test_permission_kind_follows_the_flavour(self):
        self.assertTrue(needs_accessibility(Browser("Zen", "z", ACCESSIBILITY)))
        self.assertFalse(needs_automation(Browser("Zen", "z", ACCESSIBILITY)))
        for flavour in (SAFARI, CHROMIUM, APPLESCRIPT):
            browser = Browser("B", "b", flavour)
            self.assertTrue(needs_automation(browser), flavour)
            self.assertFalse(needs_accessibility(browser), flavour)

    def test_legacy_name_entries_migrate_to_bundle_ids(self):
        self.assertEqual(coerce("Zen").bundle_id, "app.zen-browser.zen")
        self.assertEqual(coerce("Zen").flavour, ACCESSIBILITY)
        self.assertEqual(coerce("Google Chrome").flavour, CHROMIUM)
        self.assertIsNone(coerce("Netscape Navigator"))

    def test_a_path_inside_a_bundle_resolves_to_the_bundle(self):
        self.assertEqual(
            app_bundle_root("/Applications/Zen.app/Contents/MacOS/zen"),
            Path("/Applications/Zen.app"),
        )
        self.assertIsNone(app_bundle_root("/Applications/notes.txt"))


class BundleInspectionTest(unittest.TestCase):
    """Classification reads the bundle rather than trusting a typed-in name."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_gecko_forks_are_detected_by_their_bundle_contents(self):
        # An unlisted Firefox fork still lands on the accessibility route,
        # despite Gecko setting NSAppleScriptEnabled without a URL to read.
        app = _fake_app(
            self.root,
            "Rocket",
            "com.example.rocket",
            plist_extra={"NSAppleScriptEnabled": True},
            resources=("application.ini", "omni.ja"),
        )
        self.assertEqual(inspect_app(app).flavour, ACCESSIBILITY)

    def test_scriptable_browser_of_unknown_dialect_is_probed_later(self):
        app = _fake_app(self.root, "Comet", "com.example.comet", resources=("scripting.sdef",))
        self.assertEqual(inspect_app(app).flavour, APPLESCRIPT)

    def test_known_bundle_ids_skip_detection(self):
        app = _fake_app(self.root, "Zen", "app.zen-browser.zen")
        self.assertEqual(inspect_app(app).flavour, ACCESSIBILITY)

    def test_a_non_scriptable_app_is_app_level_only(self):
        app = _fake_app(self.root, "Plain", "com.example.plain")
        self.assertEqual(inspect_app(app).flavour, UNSUPPORTED)

    def test_name_and_id_come_from_the_bundle(self):
        app = _fake_app(
            self.root,
            "Zen",
            "app.zen-browser.zen",
            plist_extra={"CFBundleDisplayName": "Zen Browser"},
        )
        browser = inspect_app(app)
        self.assertEqual(browser.name, "Zen Browser")
        self.assertEqual(browser.bundle_id, "app.zen-browser.zen")
        self.assertEqual(browser.app_path, str(app))

    def test_a_bundle_without_an_identifier_is_refused(self):
        app = _fake_app(self.root, "Nameless", "")
        with self.assertRaises(BrowserError):
            inspect_app(app)

    def test_a_plain_file_is_refused(self):
        plain = Path(self.root) / "notes.txt"
        plain.write_text("")
        with self.assertRaises(BrowserError):
            inspect_app(plain)


ZEN = Browser("Zen", "app.zen-browser.zen", ACCESSIBILITY)


# A bundle id that would close the AppleScript string and run a command.
HOSTILE_ID = 'com.evil" to do shell script "touch /tmp/pwned" --'


class BundleIdValidationTest(unittest.TestCase):
    def test_real_ids_pass(self):
        from ontask.core.browsers import valid_bundle_id

        for good in ("com.apple.Safari", "app.zen-browser.zen", "org.mozilla.firefox"):
            self.assertTrue(valid_bundle_id(good), good)

    def test_anything_that_could_escape_a_script_string_fails(self):
        from ontask.core.browsers import valid_bundle_id

        for bad in (HOSTILE_ID, "com.apple.Safari\n", "no-dots", "", "a..b", "com.app\\le"):
            self.assertFalse(valid_bundle_id(bad), repr(bad))

    def test_a_hostile_config_entry_is_dropped(self):
        self.assertIsNone(Browser.from_dict({"name": "Evil", "bundle_id": HOSTILE_ID}))

    def test_a_hostile_bundle_is_refused_when_picked(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _fake_app(tmp, "Evil", HOSTILE_ID)
            with self.assertRaises(BrowserError):
                inspect_app(app)


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
        self.assertEqual(
            FocusTarget(app_name="Slack", bundle_id="com.tinyspeck.x").key(), "app:com.tinyspeck.x"
        )


@unittest.skipUnless(HAVE_PYOBJC, "PyObjC not installed")
class MacFocusProviderTest(unittest.TestCase):
    def _provider(self, app_name, bundle, script_result=("", 0, "")):
        from ontask.platform.macos.focus import MacFocusProvider

        provider = MacFocusProvider.__new__(MacFocusProvider)
        provider.timeout = 1.0
        provider.blocked_browsers = set()
        provider.needs_accessibility = set()
        provider.last_error = ""
        provider._cache = None
        provider._dialects = {}
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
            target = provider.current(default_browsers())
        self._run.assert_not_called()
        self.assertEqual(target.app_name, "Xcode")
        self.assertEqual(target.url, "")

    def test_browser_returns_url_and_title(self):
        provider = self._provider(
            "Safari", "com.apple.Safari", ("https://github.com/x\nGitHub\n", 0, "")
        )
        with mock.patch("subprocess.run", self._run):
            target = provider.current(default_browsers())
        self.assertEqual(target.url, "https://github.com/x")
        self.assertEqual(target.title, "GitHub")
        self.assertEqual(target.host, "github.com")

    def test_osascript_is_run_by_absolute_path(self):
        provider = self._provider("Safari", "com.apple.Safari", ("https://a.com\nA\n", 0, ""))
        with mock.patch("subprocess.run", self._run):
            provider.current(default_browsers())
        self.assertEqual(self._run.call_args[0][0][0], "/usr/bin/osascript")

    def test_a_hostile_bundle_id_is_never_scripted(self):
        # Even one that reached the provider without passing through config.
        evil = Browser("Evil", HOSTILE_ID, SAFARI)
        provider = self._provider("Evil", HOSTILE_ID)
        with mock.patch("subprocess.run", self._run):
            target = provider.current([evil])
        self._run.assert_not_called()
        self.assertEqual(target.url, "")

    def test_a_browser_that_was_never_added_is_not_queried(self):
        provider = self._provider("Arc", "company.thebrowser.Browser")
        with mock.patch("subprocess.run", self._run):
            target = provider.current(default_browsers())  # only Safari configured
        self._run.assert_not_called()
        self.assertEqual(target.url, "")

    def test_denied_automation_stops_repeat_prompts(self):
        provider = self._provider("Safari", "com.apple.Safari", ("", 1, "execution error: -1743"))
        with mock.patch("subprocess.run", self._run):
            provider.current(default_browsers())
            provider.current(default_browsers())
        self.assertEqual(self._run.call_count, 1)
        self.assertIn("Safari", provider.blocked_browsers)
        self.assertIn("System Settings", provider.permission_hint())

    def test_gecko_reads_the_address_bar_instead_of_applescript(self):
        provider = self._provider("zen", "app.zen-browser.zen")
        with (
            mock.patch("subprocess.run", self._run),
            mock.patch("ontask.platform.macos.ax.accessibility_trusted", return_value=True),
            mock.patch(
                "ontask.platform.macos.ax.address_bar",
                return_value=("https://github.com/x", "GitHub"),
            ),
        ):
            target = provider.current([ZEN])
        self._run.assert_not_called()  # never shells out to osascript
        self.assertEqual(target.host, "github.com")
        self.assertEqual(target.title, "GitHub")

    def test_gecko_without_accessibility_degrades_to_app_only(self):
        provider = self._provider("zen", "app.zen-browser.zen")
        with mock.patch("ontask.platform.macos.ax.accessibility_trusted", return_value=False):
            target = provider.current([ZEN])
        self.assertEqual(target.app_name, "zen")
        self.assertEqual(target.url, "")
        self.assertIn("Zen", provider.needs_accessibility)
        self.assertIn("Accessibility", provider.permission_hint())

    def test_accessibility_errors_do_not_propagate(self):
        provider = self._provider("zen", "app.zen-browser.zen")
        with (
            mock.patch("ontask.platform.macos.ax.accessibility_trusted", return_value=True),
            mock.patch(
                "ontask.platform.macos.ax.address_bar", side_effect=RuntimeError("tree changed")
            ),
        ):
            target = provider.current([ZEN])
        self.assertEqual(target.url, "")
        self.assertIn("tree changed", provider.last_error)

    def test_applescript_is_addressed_by_bundle_id(self):
        provider = self._provider("Safari", "com.apple.Safari", ("https://a.com\nA\n", 0, ""))
        with mock.patch("subprocess.run", self._run):
            provider.current(default_browsers())
        script = self._run.call_args[0][0][-1]
        self.assertIn('application id "com.apple.Safari"', script)

    def test_unknown_dialect_is_probed_and_then_remembered(self):
        provider = self._provider("Comet", "com.example.comet")
        comet = Browser("Comet", "com.example.comet", APPLESCRIPT)
        # Chromium is tried first and errors; the Safari form then answers.
        results = [
            mock.Mock(returncode=1, stdout="", stderr="execution error: can't get active tab"),
            mock.Mock(returncode=0, stdout="https://a.com\nA\n", stderr=""),
            mock.Mock(returncode=0, stdout="https://b.com\nB\n", stderr=""),
        ]
        run = mock.Mock(side_effect=results)
        with mock.patch("subprocess.run", run):
            first = provider.current([comet])
            provider._cache = None  # a later poll, past the cache window
            second = provider.current([comet])
        self.assertEqual(first.host, "a.com")
        self.assertEqual(second.host, "b.com")
        self.assertEqual(provider._dialects["com.example.comet"], SAFARI)
        # Two probes, then the remembered dialect only.
        self.assertEqual(run.call_count, 3)

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
            provider.current(default_browsers())
            provider.current(default_browsers())
        self.assertEqual(self._run.call_count, 1)


@unittest.skipUnless(HAVE_RUMPS and HAVE_PYOBJC, "rumps/PyObjC not installed")
class MenuBarTest(unittest.TestCase):
    """Builds the whole menu without starting the run loop."""

    def test_menu_builds_and_refreshes(self):
        from ontask.core.config import Config
        from ontask.platform.macos.menubar import OnTaskApp

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = Config()
            # The shell offers the browser picker on a first run, which would
            # spawn a real window; this is a test of the menu, not of setup.
            cfg.setup_complete = True
            cfg.general.hotkeys.toggle_session = ""  # avoid the Accessibility prompt
            cfg.general.hotkeys.answer_yes = ""
            cfg.general.hotkeys.answer_no = ""
            cfg.save(path)
            with (
                mock.patch("ontask.app.Config.load", return_value=Config.load(path)),
                mock.patch("rumps.Timer", _FakeTimer),
            ):
                app = OnTaskApp()
                self.assertIsNone(app.controller._setup_proc, "no first-run window spawned")
                app.refresh()
                titles = [item.title for item in app.status_items.values()]
                self.assertTrue(any("No session running" in t for t in titles))
                app.controller.start_session()
                app.refresh()
                titles = [item.title for item in app.status_items.values()]
                self.assertTrue(any("Session:" in t for t in titles))
                self.assertEqual(app.toggle_item.title, "End Session")
                self.assertIn("Deep Work", app._profile_items)


class _FakeTimer:
    """Stands in for rumps.Timer: records its pace and whether it is running."""

    def __init__(self, callback, interval):
        self.callback = callback
        self.interval = interval
        self.running = False
        self.starts = 0

    def start(self):
        self.running = True
        self.starts += 1

    def stop(self):
        self.running = False

    def is_alive(self):
        return self.running


@unittest.skipUnless(HAVE_RUMPS and HAVE_PYOBJC, "rumps/PyObjC not installed")
class MenuBarIdleTest(unittest.TestCase):
    """With no session running the menu bar app reads nothing on a timer."""

    def setUp(self):
        from ontask.core.config import Config
        from ontask.platform.macos.menubar import OnTaskApp

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.json"
        cfg = Config()
        cfg.setup_complete = True
        cfg.general.hotkeys.toggle_session = ""
        cfg.general.hotkeys.answer_yes = ""
        cfg.general.hotkeys.answer_no = ""
        cfg.general.poll_seconds = 5.0
        cfg.save(path)
        with (
            mock.patch("ontask.app.Config.load", return_value=Config.load(path)),
            mock.patch("rumps.Timer", _FakeTimer),
        ):
            self.app = OnTaskApp()
        self.focus = self.app.controller.focus = mock.Mock()
        self.focus.current.return_value = FocusTarget(app_name="Messages")

    def test_idle_the_clock_stops_and_the_poll_slows_to_the_idle_pace(self):
        from ontask.platform.macos.menubar import IDLE_POLL_SECONDS

        self.assertFalse(self.app.clock_timer.running)
        self.assertTrue(self.app.timer.running)
        self.assertEqual(self.app.timer.interval, IDLE_POLL_SECONDS)

    def test_a_session_starts_the_clock_and_the_configured_poll(self):
        self.app.controller.start_session()
        self.assertTrue(self.app.clock_timer.running)
        self.assertEqual(self.app.timer.interval, 5.0)

    def test_pausing_stops_the_clock_again(self):
        from ontask.platform.macos.menubar import IDLE_POLL_SECONDS

        self.app.controller.start_session()
        self.app.controller.pause_or_resume()
        self.assertFalse(self.app.clock_timer.running)
        self.assertEqual(self.app.timer.interval, IDLE_POLL_SECONDS)

    def test_an_unchanged_pace_does_not_restart_the_poll(self):
        starts = self.app.timer.starts
        self.app.refresh()
        self.app.refresh()
        self.assertEqual(self.app.timer.starts, starts)

    def test_opening_the_menu_reads_the_front_app_with_no_session(self):
        self.app._menu_will_open()
        self.focus.current.assert_called_once()
        self.assertEqual(self.app.status_items["focus"].title, "Focus: Messages - unapproved")
        self.assertEqual(self.app.approve_item.title, "Approve Messages")

    def test_a_poll_in_session_leaves_the_clock_to_its_own_timer(self):
        self.app.controller.start_session()
        with mock.patch.object(self.app, "refresh") as refresh:
            self.app._tick(None)
        refresh.assert_not_called()

    def test_a_poll_with_no_session_still_redraws(self):
        with mock.patch.object(self.app, "refresh") as refresh:
            self.app._tick(None)
        refresh.assert_called_once()

    def test_the_clock_is_moved_to_mid_second(self):
        self.app.controller.start_session()
        nstimer = self.app.clock_timer._nstimer = mock.Mock()
        # Due in 0.9 s, right as 8 s turns over; mid-second is 0.3 s away.
        nstimer.fireDate.return_value.timeIntervalSinceNow.return_value = 0.9
        self.app._align_clock(7.2)
        nstimer.setFireDate_.assert_called_once()

    def test_a_clock_already_near_mid_second_is_left_alone(self):
        self.app.controller.start_session()
        nstimer = self.app.clock_timer._nstimer = mock.Mock()
        nstimer.fireDate.return_value.timeIntervalSinceNow.return_value = 0.25
        self.app._align_clock(7.2)
        nstimer.setFireDate_.assert_not_called()

    def test_mid_second_arithmetic(self):
        from ontask.platform.macos.menubar import phase_gap, seconds_to_mid_second

        self.assertAlmostEqual(seconds_to_mid_second(7.2), 0.3)
        self.assertAlmostEqual(seconds_to_mid_second(7.5), 0.0)
        self.assertAlmostEqual(seconds_to_mid_second(7.9), 0.6)
        # Schedules a whole second apart are the same schedule.
        self.assertAlmostEqual(phase_gap(0.95, 0.05), 0.1)
        self.assertAlmostEqual(phase_gap(-0.05, 0.95), 0.0)
        self.assertAlmostEqual(phase_gap(0.0, 0.5), 0.5)

    def test_the_menu_has_the_opening_delegate(self):
        self.assertIs(self.app._menu._menu.delegate(), self.app._menu_opening)


@unittest.skipUnless(sys.platform == "darwin", "macOS accessibility API")
class AddressBarWalkTest(unittest.TestCase):
    """The accessibility walk, run over a fake tree instead of a real browser."""

    def setUp(self):
        from ontask.platform.macos import ax

        self.ax = ax
        self.calls = 0

    def _node(self, role="AXGroup", value=None, description="", children=()):
        return {
            self.ax.kAXRoleAttribute: role,
            self.ax.kAXValueAttribute: value,
            self.ax.kAXDescriptionAttribute: description,
            self.ax.kAXChildrenAttribute: list(children),
        }

    def _walk(self, window):
        def copy(node, attribute):
            self.calls += 1
            if attribute == self.ax.kAXFocusedWindowAttribute:
                return window
            return node.get(attribute) if isinstance(node, dict) else None

        with (
            mock.patch.object(self.ax, "accessibility_trusted", return_value=True),
            mock.patch.object(self.ax, "AXUIElementCreateApplication", return_value={}),
            mock.patch.object(self.ax, "_copy", side_effect=copy),
        ):
            return self.ax.address_bar(123)[0]

    def _page(self, size):
        return self._node(children=[self._node(role="AXStaticText") for _ in range(size)])

    def test_an_unlabelled_bar_does_not_walk_the_whole_page(self):
        # Zen: the bar is a combo box described only as "Search...".
        bar = self._node(self.ax.kAXComboBoxRole, "github.com/x", "Search...")
        window = self._node(children=[self._node(children=[bar]), self._page(350)])
        self.assertEqual(self._walk(window), "https://github.com/x")
        self.assertLess(self.calls, 200, "stopped near the toolbar, not at MAX_NODES")

    def test_a_labelled_bar_just_below_still_wins(self):
        decoy = self._node(self.ax.kAXTextFieldRole, "example.com")
        bar = self._node(self.ax.kAXTextFieldRole, "https://github.com/y", "Enter address")
        window = self._node(children=[decoy, self._node(children=[bar])])
        self.assertEqual(self._walk(window), "https://github.com/y")


class ProviderSelectionTest(unittest.TestCase):
    def _provider_on(self, platform):
        from ontask.focus import get_provider

        with mock.patch.object(sys, "platform", platform):
            return type(get_provider()).__name__

    def test_each_platform_gets_its_own_provider(self):
        self.assertEqual(self._provider_on("win32"), "WindowsFocusProvider")
        self.assertEqual(self._provider_on("linux"), "X11FocusProvider")

    def test_a_provider_that_cannot_load_stays_quiet(self):
        from ontask.focus import NullFocusProvider, get_provider

        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch("ontask.platform.linux.focus.X11FocusProvider", side_effect=OSError),
        ):
            provider = get_provider()
        self.assertIsInstance(provider, NullFocusProvider)
        self.assertTrue(provider.current().is_unknown)


class X11ProviderTest(unittest.TestCase):
    def test_tools_run_by_the_absolute_path_found_at_startup(self):
        from ontask.platform.linux.focus import X11FocusProvider

        found = {"xdotool": "/usr/bin/xdotool", "xprop": "/usr/bin/xprop"}
        with mock.patch("shutil.which", side_effect=found.get):
            provider = X11FocusProvider()
        outputs = iter(["42", "Some Title", 'WM_CLASS(STRING) = "slack", "Slack"'])
        run = mock.Mock(side_effect=lambda *a, **k: mock.Mock(returncode=0, stdout=next(outputs)))
        with mock.patch("subprocess.run", run):
            target = provider.current()
        self.assertEqual(
            [call[0][0][0] for call in run.call_args_list],
            ["/usr/bin/xdotool", "/usr/bin/xdotool", "/usr/bin/xprop"],
        )
        self.assertEqual(target.app_name, "Slack")


class SelfCommandTest(unittest.TestCase):
    """How the app starts its own windows, from source and from a bundle."""

    def test_from_source_it_runs_the_package(self):
        from ontask.__main__ import self_command

        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(self_command(), [sys.executable, "-m", "ontask"])

    def test_inside_a_py2app_bundle_it_runs_the_bundle(self):
        from ontask.__main__ import self_command

        exe = "/Applications/OnTask.app/Contents/MacOS/OnTask"
        with (
            mock.patch.object(sys, "frozen", "macosx_app", create=True),
            mock.patch.dict("os.environ", {"ARGVZERO": exe}),
        ):
            self.assertEqual(self_command(), [exe])

    def test_windows_open_through_the_window_flag(self):
        from ontask.app import Controller
        from ontask.core.config import Config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            Config().save(path)
            with mock.patch("ontask.app.get_provider"):
                controller = Controller(config_path=path)
            with (
                mock.patch("ontask.app.self_command", return_value=["ontask-exe"]),
                mock.patch("ontask.app.subprocess.Popen") as popen,
            ):
                controller.open_stats()
            popen.assert_called_once_with(
                ["ontask-exe", "--window", "stats", "--config", str(path)]
            )

    def test_every_window_module_has_a_main(self):
        import importlib

        from ontask.__main__ import WINDOWS

        for name, module in WINDOWS.items():
            self.assertTrue(callable(importlib.import_module(module).main), name)


class EntryPointTest(unittest.TestCase):
    def test_config_flag_reaches_the_shell(self):
        # The lock and the nudge signals live beside the config, so the shell
        # must watch the same config the launcher locked, not the default one.
        from ontask import __main__ as entry

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alt" / "config.json"
            with (
                mock.patch.object(sys, "platform", "linux"),
                mock.patch("ontask.ui.tk.shell.run") as tk_run,
            ):
                self.assertEqual(entry.main(["--config", str(path)]), 0)
            tk_run.assert_called_once_with(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
