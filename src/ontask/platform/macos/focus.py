"""macOS focus detection: frontmost app via NSWorkspace, then the active tab.

Browsers are matched on their bundle id rather than their name, because the two
are not always the same string - Zen's process reports ``zen`` while it is
called ``Zen`` everywhere else - and because a bundle id survives the app being
renamed or moved. The same id is what AppleScript is addressed by.

Two routes to a URL, depending on the browser:

* AppleScript for Safari and the Chromium family. Needs Automation permission,
  which macOS asks for once per browser. A browser added from Finder that turns
  out to be scriptable but of unknown dialect is probed once: both forms are
  tried and whichever answers is remembered for the rest of the run.
* The accessibility tree for Gecko browsers, which expose no AppleScript URL.
  Needs Accessibility permission.

Either can be refused. When that happens the browser silently drops to
app-level tracking and OnTask stops asking, so a denied prompt never turns into
a prompt loop.
"""

from __future__ import annotations

import subprocess
import time

from AppKit import NSWorkspace

from ...core.browsers import (
    ACCESSIBILITY,
    APPLESCRIPT,
    CHROMIUM,
    SAFARI,
    UNSUPPORTED,
    Browser,
    default_browsers,
)
from ...focus import FocusProvider, FocusTarget

_SAFARI_SCRIPT = """
tell application id "{bundle}"
    if (count of windows) is 0 then return ""
    set d to front document
    return (URL of d) & linefeed & (name of d)
end tell
"""

_CHROMIUM_SCRIPT = """
tell application id "{bundle}"
    if (count of windows) is 0 then return ""
    set t to active tab of front window
    return (URL of t) & linefeed & (title of t)
end tell
"""

CACHE_SECONDS = 0.75

# Tried in this order when a browser is scriptable but its dialect is unknown.
_PROBE_ORDER = (CHROMIUM, SAFARI)


def _script_for(flavour: str, bundle: str) -> str:
    template = _SAFARI_SCRIPT if flavour == SAFARI else _CHROMIUM_SCRIPT
    return template.format(bundle=bundle)


class MacFocusProvider(FocusProvider):
    def __init__(self, timeout: float = 1.5):
        self.timeout = timeout
        self._workspace = NSWorkspace.sharedWorkspace()
        # Browsers whose permission was refused; stop asking so the user is not
        # re-prompted on every poll.
        self.blocked_browsers: set[str] = set()
        self.needs_accessibility: set[str] = set()
        self.last_error: str = ""
        self._cache: tuple[str, float, tuple[str, str]] | None = None
        # bundle id -> the AppleScript dialect that actually answered.
        self._dialects: dict[str, str] = {}

    def current(self, browsers: list[Browser] | None = None) -> FocusTarget:
        app = self._workspace.frontmostApplication()
        if app is None:
            return FocusTarget()
        name = str(app.localizedName() or "")
        bundle = str(app.bundleIdentifier() or "")
        enabled = browsers if browsers is not None else default_browsers()
        browser = self._match(name, bundle, enabled)
        if (
            browser is None
            or browser.flavour == UNSUPPORTED
            or browser.name in self.blocked_browsers
        ):
            return FocusTarget(app_name=name, bundle_id=bundle)
        try:
            pid = int(app.processIdentifier())
        except Exception:
            pid = -1
        url, title = self._tab_of(browser, pid)
        return FocusTarget(app_name=name, bundle_id=bundle, url=url, title=title)

    def _match(self, name: str, bundle: str, enabled: list[Browser]) -> Browser | None:
        for browser in enabled:
            if browser.matches(name, bundle):
                return browser
        return None

    def _tab_of(self, browser: Browser, pid: int) -> tuple[str, str]:
        # A short cache keeps rapid polling from re-querying constantly.
        now = time.monotonic()
        key = browser.bundle_id or browser.name
        if self._cache and self._cache[0] == key and now - self._cache[1] < CACHE_SECONDS:
            return self._cache[2]
        if browser.flavour == ACCESSIBILITY:
            result = self._tab_via_accessibility(browser, pid)
        else:
            result = self._tab_via_applescript(browser)
        if result != ("", ""):
            self._cache = (key, now, result)
        return result

    def _tab_via_applescript(self, browser: Browser) -> tuple[str, str]:
        target = browser.bundle_id or browser.name
        if browser.flavour == APPLESCRIPT:
            dialects = [self._dialects[target]] if target in self._dialects else list(_PROBE_ORDER)
        else:
            dialects = [browser.flavour]
        for dialect in dialects:
            ok, result = self._run_script(browser, dialect, target)
            if not ok:
                # A refused or missing app will not answer in any dialect either.
                break
            if result != ("", ""):
                if browser.flavour == APPLESCRIPT:
                    self._dialects[target] = dialect
                return result
        return "", ""

    def _run_script(
        self, browser: Browser, dialect: str, target: str
    ) -> tuple[bool, tuple[str, str]]:
        """Run one dialect. Returns (the app was reachable, (url, title))."""
        try:
            proc = subprocess.run(
                ["osascript", "-e", _script_for(dialect, target)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            self.last_error = f"{browser.name}: {exc}"
            return False, ("", "")
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            self.last_error = f"{browser.name}: {err}"
            # -1743 is "not authorised to send Apple events"; asking again would
            # just re-prompt the user forever.
            if "-1743" in err or "not allowed" in err.lower():
                self.blocked_browsers.add(browser.name)
                return False, ("", "")
            # Any other error is usually the wrong dialect for this browser, so
            # the caller is free to try the next one.
            return True, ("", "")
        lines = (proc.stdout or "").splitlines()
        return True, (
            lines[0].strip() if lines else "",
            lines[1].strip() if len(lines) > 1 else "",
        )

    def _tab_via_accessibility(self, browser: Browser, pid: int) -> tuple[str, str]:
        if pid < 0:
            return "", ""
        try:
            from .ax import accessibility_trusted, address_bar
        except Exception as exc:
            self.last_error = f"{browser.name}: {exc}"
            return "", ""
        if not accessibility_trusted():
            self.needs_accessibility.add(browser.name)
            self.last_error = f"{browser.name}: Accessibility permission not granted"
            return "", ""
        self.needs_accessibility.discard(browser.name)
        try:
            return address_bar(pid)
        except Exception as exc:
            self.last_error = f"{browser.name}: {exc}"
            return "", ""

    def permission_hint(self) -> str:
        parts = []
        if self.blocked_browsers:
            names = ", ".join(sorted(self.blocked_browsers))
            parts.append(
                f"OnTask cannot read tabs in {names}. Grant access in System Settings > "
                "Privacy & Security > Automation, then restart OnTask."
            )
        if self.needs_accessibility:
            names = ", ".join(sorted(self.needs_accessibility))
            parts.append(
                f"Reading tabs in {names} needs Accessibility. Grant it in System Settings > "
                "Privacy & Security > Accessibility, then restart OnTask."
            )
        return "\n\n".join(parts)
