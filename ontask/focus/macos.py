"""macOS focus detection: frontmost app via NSWorkspace, then the active tab.

Two routes to a URL, depending on the browser:

* AppleScript for Safari and the Chromium family. Needs Automation permission,
  which macOS asks for once per browser.
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

from ..browsers import ACCESSIBILITY, BROWSERS, CHROMIUM, DEFAULT_BROWSERS, SAFARI, UNSUPPORTED
from . import FocusProvider, FocusTarget

_SAFARI_SCRIPT = '''
tell application "{app}"
    if (count of windows) is 0 then return ""
    set d to front document
    return (URL of d) & linefeed & (name of d)
end tell
'''

_CHROMIUM_SCRIPT = '''
tell application "{app}"
    if (count of windows) is 0 then return ""
    set t to active tab of front window
    return (URL of t) & linefeed & (title of t)
end tell
'''

CACHE_SECONDS = 0.75


def _script_for(flavour: str, app: str) -> str:
    template = _SAFARI_SCRIPT if flavour == SAFARI else _CHROMIUM_SCRIPT
    return template.format(app=app)


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

    def current(self, browsers: list[str] | None = None) -> FocusTarget:
        app = self._workspace.frontmostApplication()
        if app is None:
            return FocusTarget()
        name = str(app.localizedName() or "")
        bundle = str(app.bundleIdentifier() or "")
        enabled = browsers if browsers is not None else DEFAULT_BROWSERS
        flavour = self._browser_flavour(name, bundle, enabled)
        if flavour in (None, UNSUPPORTED) or name in self.blocked_browsers:
            return FocusTarget(app_name=name, bundle_id=bundle)
        try:
            pid = int(app.processIdentifier())
        except Exception:
            pid = -1
        url, title = self._tab_of(name, flavour, pid)
        return FocusTarget(app_name=name, bundle_id=bundle, url=url, title=title)

    def _browser_flavour(self, name: str, bundle: str, enabled: list[str]) -> str | None:
        for browser, (browser_bundle, flavour) in BROWSERS.items():
            if browser not in enabled:
                continue
            if name == browser or (bundle and bundle == browser_bundle):
                return flavour
        return None

    def _tab_of(self, app_name: str, flavour: str, pid: int) -> tuple[str, str]:
        # A short cache keeps rapid polling from re-querying constantly.
        now = time.monotonic()
        if self._cache and self._cache[0] == app_name and now - self._cache[1] < CACHE_SECONDS:
            return self._cache[2]
        if flavour == ACCESSIBILITY:
            result = self._tab_via_accessibility(app_name, pid)
        else:
            result = self._tab_via_applescript(app_name, flavour)
        if result != ("", ""):
            self._cache = (app_name, now, result)
        return result

    def _tab_via_applescript(self, app_name: str, flavour: str) -> tuple[str, str]:
        try:
            proc = subprocess.run(
                ["osascript", "-e", _script_for(flavour, app_name)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            self.last_error = f"{app_name}: {exc}"
            return "", ""
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            self.last_error = f"{app_name}: {err}"
            # -1743 is "not authorised to send Apple events"; asking again would
            # just re-prompt the user forever.
            if "-1743" in err or "not allowed" in err.lower():
                self.blocked_browsers.add(app_name)
            return "", ""
        lines = (proc.stdout or "").splitlines()
        return (lines[0].strip() if lines else "", lines[1].strip() if len(lines) > 1 else "")

    def _tab_via_accessibility(self, app_name: str, pid: int) -> tuple[str, str]:
        if pid < 0:
            return "", ""
        try:
            from .ax import accessibility_trusted, address_bar
        except Exception as exc:
            self.last_error = f"{app_name}: {exc}"
            return "", ""
        if not accessibility_trusted():
            self.needs_accessibility.add(app_name)
            self.last_error = f"{app_name}: Accessibility permission not granted"
            return "", ""
        self.needs_accessibility.discard(app_name)
        try:
            return address_bar(pid)
        except Exception as exc:
            self.last_error = f"{app_name}: {exc}"
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
