"""Actionable macOS notifications.

The banner carries real Yes and No buttons, so `prompt_ui = "notification"` is
answerable without the floating window.

Delivery is never assumed. Authorisation is *read* at startup with
`getNotificationSettings`, which never shows a dialog; the permission prompt is
only raised when notification mode is actually selected. If notifications are
denied or undelivered, `available()` says so and the shell falls back to the
floating window rather than leaving a check-in that cannot be answered.

Note that an unbundled process posts under whichever app owns the interpreter,
so banners say "Python" until you build a real bundle with setup_app.py.
"""

from __future__ import annotations

import uuid

import objc
from Foundation import NSObject

try:
    import UserNotifications as UN

    HAVE_USER_NOTIFICATIONS = True
except Exception:  # pragma: no cover - framework missing on older systems
    UN = None
    HAVE_USER_NOTIFICATIONS = False

CATEGORY = "ONTASK_CHECKIN"
ACTION_YES = "ONTASK_YES"
ACTION_NO = "ONTASK_NO"

# UNAuthorizationOptionAlert | UNAuthorizationOptionSound
AUTH_OPTIONS = (1 << 2) | (1 << 1)

# UNAuthorizationStatus
NOT_DETERMINED, DENIED, AUTHORIZED, PROVISIONAL, EPHEMERAL = 0, 1, 2, 3, 4
_OK_STATUSES = (AUTHORIZED, PROVISIONAL, EPHEMERAL)
_STATUS_NAMES = {
    NOT_DETERMINED: "not yet requested",
    DENIED: "denied in System Settings",
    AUTHORIZED: "allowed",
    PROVISIONAL: "allowed (quiet)",
    EPHEMERAL: "allowed (temporary)",
}

# UNNotificationPresentationOption: Banner is newer, Alert is the older spelling.
_PRESENT_BANNER = (1 << 4) | (1 << 1)
_PRESENT_ALERT = (1 << 2) | (1 << 1)


class _Delegate(NSObject):
    """Receives button taps. Must stay referenced or PyObjC will free it."""

    def initWithCallback_(self, callback):
        self = objc.super(_Delegate, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def userNotificationCenter_willPresentNotification_withCompletionHandler_(
        self, center, notification, handler
    ):
        # Show the banner even while OnTask itself is frontmost.
        try:
            handler(_PRESENT_BANNER)
        except Exception:
            handler(_PRESENT_ALERT)

    def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        self, center, response, handler
    ):
        action = str(response.actionIdentifier())
        try:
            if action == ACTION_YES:
                self._callback(True)
            elif action == ACTION_NO:
                self._callback(False)
            # Any other identifier means the banner was clicked or dismissed,
            # which is not an answer; the engine's ignore policy covers it.
        finally:
            handler()


class Notifier:
    def __init__(self, on_answer, auto_query: bool = True):
        self.on_answer = on_answer
        self.status: int | None = None
        self.error: str = ""
        self.requested = False
        self._center = None
        self._delegate = None
        self._live: list[str] = []
        # Completion blocks fire on a background queue; dropping the Python
        # reference while one is in flight segfaults the interpreter.
        self._handlers: list = []
        self._setup()
        if auto_query:
            self.refresh_status()

    def _setup(self) -> None:
        if not HAVE_USER_NOTIFICATIONS:
            self.error = "UserNotifications framework unavailable"
            return
        try:
            center = UN.UNUserNotificationCenter.currentNotificationCenter()
        except Exception as exc:
            self.error = f"no notification centre: {exc}"
            return
        if center is None:
            self.error = "no notification centre"
            return
        self._center = center
        self._delegate = _Delegate.alloc().initWithCallback_(self.on_answer)
        try:
            center.setDelegate_(self._delegate)
            center.setNotificationCategories_({self._category()})
        except Exception as exc:
            self.error = str(exc)

    def _category(self):
        yes = UN.UNNotificationAction.actionWithIdentifier_title_options_(ACTION_YES, "Yes", 0)
        no = UN.UNNotificationAction.actionWithIdentifier_title_options_(ACTION_NO, "No", 0)
        return UN.UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(
            CATEGORY, [yes, no], [], 0
        )

    # -- authorisation ----------------------------------------------------

    def refresh_status(self) -> None:
        """Read the current authorisation. Never shows a dialog."""
        if self._center is None:
            return

        def handler(settings):
            try:
                self.status = int(settings.authorizationStatus())
            except Exception:
                self.status = None

        self._handlers.append(handler)
        try:
            self._center.getNotificationSettingsWithCompletionHandler_(handler)
        except Exception as exc:
            self.error = str(exc)

    def request_authorization(self) -> None:
        """Raise the system permission prompt. Only for notification mode.

        Safe only while a run loop is running, which is why nothing calls this
        during construction.
        """
        if self._center is None or self.requested:
            return
        self.requested = True

        def handler(granted, error):
            self.status = AUTHORIZED if granted else DENIED
            if error is not None:
                self.error = str(error)

        self._handlers.append(handler)
        try:
            self._center.requestAuthorizationWithOptions_completionHandler_(AUTH_OPTIONS, handler)
        except Exception as exc:
            self.error = str(exc)
            self.status = DENIED

    def available(self) -> bool:
        """True unless we know a banner would not be delivered."""
        if self._center is None:
            return False
        return self.status is None or self.status in _OK_STATUSES or self.status == NOT_DETERMINED

    def known_denied(self) -> bool:
        return self.status == DENIED

    # -- delivery ---------------------------------------------------------

    def show(self, title: str, body: str, actionable: bool = True, sound: bool = True) -> bool:
        if self._center is None:
            return False
        try:
            content = UN.UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(body)
            if actionable:
                content.setCategoryIdentifier_(CATEGORY)
            if sound:
                content.setSound_(UN.UNNotificationSound.defaultSound())
            identifier = str(uuid.uuid4())
            request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
                identifier, content, None
            )
            self._center.addNotificationRequest_withCompletionHandler_(request, None)
            if actionable:
                self._live.append(identifier)
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def withdraw(self) -> None:
        """Pull any check-in banners back once the question is answered."""
        if self._center is None or not self._live:
            return
        try:
            self._center.removeDeliveredNotificationsWithIdentifiers_(list(self._live))
            self._center.removePendingNotificationRequestsWithIdentifiers_(list(self._live))
        except Exception:
            pass
        self._live.clear()

    def status_text(self) -> str:
        if self._center is None:
            return f"unavailable ({self.error})" if self.error else "unavailable"
        if self.status is None:
            return "checking"
        name = _STATUS_NAMES.get(self.status, str(self.status))
        return f"{name}{f' - {self.error}' if self.error else ''}"
