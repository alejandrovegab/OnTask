"""macOS focus detection: frontmost app via NSWorkspace, tab URL via AppleScript.

Reading a browser's active tab needs Automation permission, which macOS asks
for once per browser the first time OnTask queries it. If it is denied the
provider degrades to app-level tracking rather than failing.
"""

from __future__ import annotations

import subprocess
import time

from AppKit import NSWorkspace  # noqa: F401  (import verifies PyObjC is present)

from . import FocusProvider, FocusTarget

from ..browsers import BROWSERS, DEFAULT_BROWSERS, SAFARI, UNSUPPORTED  # noqa: F401

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


def _script_for(flavour: str, app: str) -> str:
    template = _SAFARI_SCRIPT if flavour == SAFARI else _CHROMIUM_SCRIPT
    return template.format(app=app)


class MacFocusProvider(FocusProvider):
    def __init__(self, timeout: float = 1.5):
        self.timeout = timeout
        self._workspace = NSWorkspace.sharedWorkspace()
        # Browsers whose Automation permission was refused; stop asking so the
        # user is not re-prompted on every poll.
        self.blocked_browsers: set[str] = set()
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
        url, title = self._tab_of(name, flavour)
        return FocusTarget(app_name=name, bundle_id=bundle, url=url, title=title)

    def _browser_flavour(self, name: str, bundle: str, enabled: list[str]) -> str | None:
        for browser, (browser_bundle, flavour) in BROWSERS.items():
            if browser not in enabled:
                continue
            if name == browser or (bundle and bundle == browser_bundle):
                return flavour
        return None

    def _tab_of(self, app_name: str, flavour: str) -> tuple[str, str]:
        # A short cache keeps rapid polling from spawning osascript constantly.
        now = time.monotonic()
        if self._cache and self._cache[0] == app_name and now - self._cache[1] < 0.75:
            return self._cache[2]
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
        result = (lines[0].strip() if lines else "", lines[1].strip() if len(lines) > 1 else "")
        self._cache = (app_name, now, result)
        return result

    def permission_hint(self) -> str:
        if not self.blocked_browsers:
            return ""
        names = ", ".join(sorted(self.blocked_browsers))
        return (
            f"OnTask cannot read tabs in {names}. Grant access in System Settings > "
            "Privacy & Security > Automation, then restart OnTask."
        )
