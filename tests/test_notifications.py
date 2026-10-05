"""Notification delivery, permission handling, and check-in routing.

Nothing here posts a real banner or raises a permission dialog: the notifier is
either stubbed or constructed with its status forced.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import AppKit  # noqa: F401
    import rumps  # noqa: F401

    HAVE_MAC_UI = True
except Exception:
    HAVE_MAC_UI = False


@unittest.skipUnless(HAVE_MAC_UI, "macOS UI stack not installed")
class NotifierTest(unittest.TestCase):
    def test_missing_framework_is_reported_not_raised(self):
        from ontask.ui import notify_macos

        with mock.patch.object(notify_macos, "HAVE_USER_NOTIFICATIONS", False):
            notifier = notify_macos.Notifier(lambda yes: None, auto_query=False)
        self.assertFalse(notifier.available())
        self.assertFalse(notifier.show("t", "b"))
        self.assertIn("unavailable", notifier.status_text())

    def test_availability_follows_authorisation_status(self):
        from ontask.ui.notify_macos import (
            AUTHORIZED,
            DENIED,
            NOT_DETERMINED,
            PROVISIONAL,
            Notifier,
        )

        notifier = Notifier(lambda yes: None, auto_query=False)
        if notifier._center is None:
            self.skipTest("no notification centre on this machine")
        for status, expected in (
            (AUTHORIZED, True),
            (PROVISIONAL, True),
            (NOT_DETERMINED, True),
            (DENIED, False),
        ):
            notifier.status = status
            self.assertEqual(notifier.available(), expected, status)
        notifier.status = DENIED
        self.assertTrue(notifier.known_denied())
        self.assertIn("denied", notifier.status_text())

    def test_withdraw_is_safe_with_nothing_delivered(self):
        from ontask.ui.notify_macos import Notifier

        notifier = Notifier(lambda yes: None, auto_query=False)
        notifier.withdraw()  # must not raise

    def test_request_authorization_only_fires_once(self):
        from ontask.ui.notify_macos import Notifier

        notifier = Notifier(lambda yes: None, auto_query=False)
        if notifier._center is None:
            self.skipTest("no notification centre on this machine")
        notifier._center = mock.Mock()
        notifier.request_authorization()
        notifier.request_authorization()
        self.assertEqual(
            notifier._center.requestAuthorizationWithOptions_completionHandler_.call_count, 1
        )

    def test_completion_handlers_are_retained(self):
        """An unreferenced block firing on a background queue segfaults."""
        from ontask.ui.notify_macos import Notifier

        notifier = Notifier(lambda yes: None, auto_query=False)
        if notifier._center is None:
            self.skipTest("no notification centre on this machine")
        notifier._center = mock.Mock()
        notifier.refresh_status()
        notifier.request_authorization()
        self.assertEqual(len(notifier._handlers), 2)


@unittest.skipUnless(HAVE_MAC_UI, "macOS UI stack not installed")
class NotificationDelegateTest(unittest.TestCase):
    def _delegate(self, answers):
        from ontask.ui.notify_macos import _Delegate

        return _Delegate.alloc().initWithCallback_(answers.append)

    def _respond(self, delegate, action_id, answers):
        response = mock.Mock()
        response.actionIdentifier.return_value = action_id
        done = []
        delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            None, response, lambda: done.append(True)
        )
        return done

    def test_yes_button_answers_yes(self):
        from ontask.ui.notify_macos import ACTION_YES

        answers = []
        done = self._respond(self._delegate(answers), ACTION_YES, answers)
        self.assertEqual(answers, [True])
        self.assertEqual(done, [True])

    def test_no_button_answers_no(self):
        from ontask.ui.notify_macos import ACTION_NO

        answers = []
        done = self._respond(self._delegate(answers), ACTION_NO, answers)
        self.assertEqual(answers, [False])
        self.assertEqual(done, [True])

    def test_plain_click_is_not_an_answer(self):
        answers = []
        done = self._respond(self._delegate(answers), "com.apple.UNNotificationDefaultActionIdentifier", answers)
        self.assertEqual(answers, [])
        self.assertEqual(done, [True], "the completion handler must still run")

    def test_completion_handler_runs_even_if_callback_raises(self):
        from ontask.ui.notify_macos import ACTION_YES, _Delegate

        def boom(_yes):
            raise RuntimeError("controller exploded")

        delegate = _Delegate.alloc().initWithCallback_(boom)
        response = mock.Mock()
        response.actionIdentifier.return_value = ACTION_YES
        done = []
        with self.assertRaises(RuntimeError):
            delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                None, response, lambda: done.append(True)
            )
        self.assertEqual(done, [True])


@unittest.skipUnless(HAVE_MAC_UI, "macOS UI stack not installed")
class PromptRoutingTest(unittest.TestCase):
    """How prompt_ui resolves against what can actually be delivered."""

    def setUp(self):
        from ontask.config import Config
        from ontask.ui.menubar_macos import OnTaskApp

        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "config.json"
        cfg = Config()
        # Without this the shell would offer the first-run browser picker and
        # spawn a real window for every test in this class.
        cfg.setup_complete = True
        cfg.general.hotkeys.toggle_session = ""
        cfg.general.hotkeys.answer_yes = ""
        cfg.general.hotkeys.answer_no = ""
        cfg.save(path)
        with mock.patch("ontask.app.Config.load", return_value=Config.load(path)), \
             mock.patch("rumps.Timer"):
            self.app = OnTaskApp()
        self.app.prompt = mock.Mock()
        self.app.notifier = mock.Mock()
        self.app.notifier.status = 2  # AUTHORIZED
        self.app.notifier.available.return_value = True

    def tearDown(self):
        self.tmp.cleanup()

    def _prompt(self):
        from ontask.engine import CADENCE, ActivePrompt
        from ontask.focus import FocusTarget

        return ActivePrompt(kind=CADENCE, target=FocusTarget(app_name="Code"), opened_at=0.0)

    def _set_mode(self, mode):
        self.app.controller.config.general.prompt_ui = mode

    def test_window_mode_never_notifies(self):
        self._set_mode("window")
        self.app.show_prompt(self._prompt(), self.app.controller)
        self.app.prompt.show.assert_called_once()
        self.app.notifier.show.assert_not_called()

    def test_notification_mode_sends_actionable_banner(self):
        self._set_mode("notification")
        self.app.show_prompt(self._prompt(), self.app.controller)
        self.app.notifier.show.assert_called_once()
        self.assertTrue(self.app.notifier.show.call_args.kwargs["actionable"])
        self.app.prompt.show.assert_not_called()

    def test_denied_notifications_fall_back_to_the_window(self):
        from ontask.ui.notify_macos import DENIED

        self._set_mode("notification")
        self.app.notifier.status = DENIED
        self.app.notifier.available.return_value = False
        self.app.notifier.status_text.return_value = "denied in System Settings"
        with mock.patch("rumps.alert") as alert:
            self.app.show_prompt(self._prompt(), self.app.controller)
        self.app.prompt.show.assert_called_once()
        self.app.notifier.show.assert_not_called()
        alert.assert_called_once()

    def test_fallback_warning_is_shown_only_once(self):
        self._set_mode("notification")
        self.app.notifier.available.return_value = False
        self.app.notifier.status = 1
        self.app.notifier.status_text.return_value = "denied"
        with mock.patch("rumps.alert") as alert:
            self.app.show_prompt(self._prompt(), self.app.controller)
            self.app.show_prompt(self._prompt(), self.app.controller)
        self.assertEqual(alert.call_count, 1)

    def test_undecided_permission_prompts_but_still_shows_the_window(self):
        from ontask.ui.notify_macos import NOT_DETERMINED

        self._set_mode("notification")
        self.app.notifier.status = NOT_DETERMINED
        self.app.show_prompt(self._prompt(), self.app.controller)
        self.app.notifier.request_authorization.assert_called_once()
        # The check-in must stay answerable while permission is undecided.
        self.app.prompt.show.assert_called_once()
        self.app.notifier.show.assert_not_called()

    def test_both_mode_starts_as_a_banner_then_escalates(self):
        self._set_mode("both")
        prompt = self._prompt()
        self.app.show_prompt(prompt, self.app.controller)
        self.app.notifier.show.assert_called_once()
        self.app.prompt.show.assert_not_called()

        self.app.prompt.visible = False
        self.app.realert(prompt)
        self.app.prompt.show.assert_called_once()

    def test_notification_mode_reposts_on_realert(self):
        self._set_mode("notification")
        prompt = self._prompt()
        self.app.realert(prompt)
        self.app.notifier.show.assert_called_once()
        self.app.prompt.show.assert_not_called()

    def test_closing_withdraws_the_banner(self):
        self.app.close_prompt()
        self.app.notifier.withdraw.assert_called_once()
        self.app.prompt.close.assert_called_once()

    def test_permissions_report_covers_all_three_grants(self):
        self.app.notifier.status_text.return_value = "allowed"
        report = self.app.permissions_report()
        self.assertIn("Notifications:", report)
        self.assertIn("Accessibility:", report)
        self.assertIn("Global hotkeys:", report)



@unittest.skipUnless(HAVE_MAC_UI, "macOS UI stack not installed")
class PromptFocusHandoverTest(unittest.TestCase):
    def _window_with_previous_app(self):
        from ontask.ui.prompt_macos import PromptWindow

        window = PromptWindow(lambda yes: None)
        previous = mock.Mock()
        previous.isTerminated.return_value = False
        window._previous_app = previous
        return window, previous

    def test_focus_returns_to_the_app_that_was_in_front(self):
        window, previous = self._window_with_previous_app()
        with mock.patch("ontask.ui.prompt_macos.NSApp") as app:
            app.isActive.return_value = True
            window._restore_front_app()
        previous.activateWithOptions_.assert_called_once()

    def test_focus_is_left_alone_once_the_user_has_moved_on(self):
        # The user switched to another app while the check-in was up, then
        # answered with a hotkey: they must stay where they are.
        window, previous = self._window_with_previous_app()
        with mock.patch("ontask.ui.prompt_macos.NSApp") as app:
            app.isActive.return_value = False
            window._restore_front_app()
        previous.activateWithOptions_.assert_not_called()
        self.assertIsNone(window._previous_app)

if __name__ == "__main__":
    unittest.main(verbosity=2)
