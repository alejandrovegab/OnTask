"""Identifying browsers OnTask can read tab URLs from.

Safari is the only browser configured out of the box. Every other browser is
added by picking its ``.app`` in Settings, and that is what makes the identity
right: the bundle id, the display name and the URL-reading route are all read
out of the bundle itself instead of being guessed from a name typed by hand.
Matching later happens on the bundle id, so a browser whose process name
differs from its display name (Zen reports ``zen``) still matches.

Flavours describe *how* the URL is obtained:

  safari / chromium  AppleScript, addressed by bundle id. The two differ only
                     in dialect: Safari has ``front document``, Chromium has
                     ``active tab of front window``. Needs Automation
                     permission, which macOS asks for once per browser.
  applescript        AppleScript-capable but the dialect is not known yet. The
                     focus provider tries both on first use and remembers
                     whichever answered.
  accessibility      Walks the accessibility tree to read the address bar. This
                     is the route for Gecko browsers (Firefox, Zen, LibreWolf
                     and other forks), which expose no AppleScript URL. Needs
                     Accessibility permission and is best effort.
  unsupported        App-level tracking only.

This module stays free of platform imports so the settings window can list and
inspect browsers anywhere.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAFARI = "safari"
CHROMIUM = "chromium"
APPLESCRIPT = "applescript"
ACCESSIBILITY = "accessibility"
UNSUPPORTED = "unsupported"

FLAVOURS = (SAFARI, CHROMIUM, APPLESCRIPT, ACCESSIBILITY, UNSUPPORTED)

FLAVOUR_LABELS = {
    SAFARI: "AppleScript",
    CHROMIUM: "AppleScript",
    APPLESCRIPT: "AppleScript",
    ACCESSIBILITY: "Accessibility",
    UNSUPPORTED: "App only",
}


class BrowserError(Exception):
    """A chosen .app could not be read as a browser. Message is user-facing."""


@dataclass(frozen=True)
class Browser:
    """One browser OnTask tracks tab URLs in, identified by bundle id."""

    name: str
    bundle_id: str
    flavour: str = UNSUPPORTED
    app_path: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Browser | None":
        d = d or {}
        bundle_id = str(d.get("bundle_id") or "").strip()
        name = str(d.get("name") or "").strip()
        if not bundle_id:
            # The bundle id is the identity: it is what the frontmost app is
            # matched on and what AppleScript is addressed by. A name alone can
            # do neither, so a known name is upgraded and anything else dropped.
            bundle_id = LEGACY_NAMES.get(name, "")
            if not bundle_id:
                return None
        flavour = str(d.get("flavour") or KNOWN_FLAVOURS.get(bundle_id, UNSUPPORTED))
        return cls(
            name=name or bundle_id,
            bundle_id=bundle_id,
            flavour=flavour if flavour in FLAVOURS else UNSUPPORTED,
            app_path=str(d.get("app_path") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bundle_id": self.bundle_id,
            "flavour": self.flavour,
            "app_path": self.app_path,
        }

    def matches(self, app_name: str, bundle_id: str) -> bool:
        """Whether a frontmost app is this browser.

        The bundle id is the identity. Comparing names instead is what used to
        make Zen invisible - it is called ``Zen`` but its process reports
        ``zen`` - so only the id is compared. `app_name` is accepted so callers
        need not care which one decides.
        """
        return bool(self.bundle_id) and bundle_id == self.bundle_id

    @property
    def route(self) -> str:
        return FLAVOUR_LABELS.get(self.flavour, "App only")

    def describe(self) -> str:
        return f"{self.name}  —  {self.bundle_id}  ({self.route})"


SAFARI_BROWSER = Browser("Safari", "com.apple.Safari", SAFARI, "/Applications/Safari.app")


def default_browsers() -> list[Browser]:
    """Safari, and only Safari. Anything else is added from Finder."""
    return [SAFARI_BROWSER]


# Bundle ids whose URL-reading route is known ahead of time. Used to classify a
# bundle the moment it is picked, so a well-known browser never has to be probed
# and never guesses the wrong AppleScript dialect. Anything absent here is
# classified from the bundle's own contents by `detect_flavour`.
KNOWN_FLAVOURS: dict[str, str] = {
    # Safari and other WebKit shells that speak Safari's dialect.
    "com.apple.Safari": SAFARI,
    "com.apple.SafariTechnologyPreview": SAFARI,
    # Chromium family.
    "com.google.Chrome": CHROMIUM,
    "com.google.Chrome.beta": CHROMIUM,
    "com.google.Chrome.dev": CHROMIUM,
    "com.google.Chrome.canary": CHROMIUM,
    "org.chromium.Chromium": CHROMIUM,
    "com.brave.Browser": CHROMIUM,
    "com.brave.Browser.beta": CHROMIUM,
    "com.brave.Browser.nightly": CHROMIUM,
    "com.microsoft.edgemac": CHROMIUM,
    "com.microsoft.edgemac.Beta": CHROMIUM,
    "com.microsoft.edgemac.Dev": CHROMIUM,
    "com.microsoft.edgemac.Canary": CHROMIUM,
    "company.thebrowser.Browser": CHROMIUM,
    "company.thebrowser.dia": CHROMIUM,
    "com.vivaldi.Vivaldi": CHROMIUM,
    "com.operasoftware.Opera": CHROMIUM,
    "com.operasoftware.OperaGX": CHROMIUM,
    "ru.yandex.desktop.yandex-browser": CHROMIUM,
    "com.naver.Whale": CHROMIUM,
    "net.imput.helium": CHROMIUM,
    "com.pushplaylabs.sidekick": CHROMIUM,
    # Gecko family: no AppleScript URL, so the address bar is read from the
    # accessibility tree. Forks are caught by `detect_flavour` even when they
    # are not listed here.
    "org.mozilla.firefox": ACCESSIBILITY,
    "org.mozilla.firefoxdeveloperedition": ACCESSIBILITY,
    "org.mozilla.nightly": ACCESSIBILITY,
    "app.zen-browser.zen": ACCESSIBILITY,
    "io.gitlab.librewolf": ACCESSIBILITY,
    "net.waterfox.waterfox": ACCESSIBILITY,
    "one.ablaze.floorp": ACCESSIBILITY,
    "org.torproject.torbrowser": ACCESSIBILITY,
    "net.mullvad.mullvadbrowser": ACCESSIBILITY,
}

# Legacy config used display names; map the ones OnTask used to ship so an
# existing config keeps working after the switch to bundle ids.
LEGACY_NAMES: dict[str, str] = {
    "Safari": "com.apple.Safari",
    "Safari Technology Preview": "com.apple.SafariTechnologyPreview",
    "Google Chrome": "com.google.Chrome",
    "Google Chrome Canary": "com.google.Chrome.canary",
    "Chromium": "org.chromium.Chromium",
    "Brave Browser": "com.brave.Browser",
    "Microsoft Edge": "com.microsoft.edgemac",
    "Arc": "company.thebrowser.Browser",
    "Dia": "company.thebrowser.dia",
    "Vivaldi": "com.vivaldi.Vivaldi",
    "Opera": "com.operasoftware.Opera",
    "Firefox": "org.mozilla.firefox",
    "Firefox Developer Edition": "org.mozilla.firefoxdeveloperedition",
    "Zen": "app.zen-browser.zen",
    "LibreWolf": "io.gitlab.librewolf",
}


def app_bundle_root(path: str | Path) -> Path | None:
    """The enclosing ``.app`` for a path, or None.

    A file dialog may hand back either the bundle or something inside it,
    depending on how the user navigated, so both are accepted.
    """
    candidate = Path(path).expanduser()
    for part in (candidate, *candidate.parents):
        if part.suffix == ".app":
            return part
    return None


def _read_info_plist(app: Path) -> dict[str, Any]:
    info = app / "Contents" / "Info.plist"
    try:
        with info.open("rb") as handle:
            data = plistlib.load(handle)
    except FileNotFoundError:
        raise BrowserError(f"{app.name} has no Contents/Info.plist, so it is not an app bundle.") from None
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        raise BrowserError(f"Could not read the Info.plist inside {app.name}: {exc}") from None
    return data if isinstance(data, dict) else {}


def _is_gecko(app: Path) -> bool:
    """Whether a bundle is Firefox or one of its forks.

    Every Gecko build ships an application.ini and a packed omni.ja beside the
    executable, which is why Zen, LibreWolf, Floorp and friends are recognised
    without needing to be listed by bundle id.
    """
    resources = app / "Contents" / "Resources"
    return (resources / "application.ini").is_file() or (resources / "omni.ja").is_file()


def _is_chromium(app: Path) -> bool:
    """Whether a bundle is a Chromium fork, by its embedded framework.

    Every Chromium build ships a `<Name> Framework.framework`, which is enough
    to route an unlisted fork straight to the Chromium dialect instead of
    making it earn that through a runtime probe.
    """
    frameworks = app / "Contents" / "Frameworks"
    try:
        return any(
            child.name.endswith(" Framework.framework") for child in frameworks.iterdir()
        )
    except OSError:
        return False


def _speaks_applescript(app: Path, info: dict[str, Any]) -> bool:
    if info.get("NSAppleScriptEnabled") in (True, "YES", "Yes", "yes", "true", "True"):
        return True
    if info.get("OSAScriptingDefinition"):
        return True
    resources = app / "Contents" / "Resources"
    try:
        return any(child.suffix == ".sdef" for child in resources.iterdir())
    except OSError:
        return False


def detect_flavour(app: Path, info: dict[str, Any], bundle_id: str) -> str:
    """Work out how this bundle's active tab URL can be read."""
    known = KNOWN_FLAVOURS.get(bundle_id)
    if known:
        return known
    # Gecko is checked before AppleScript: Firefox sets NSAppleScriptEnabled but
    # exposes no URL through it, so trusting that flag would strand the whole
    # family on a route that can never answer.
    if _is_gecko(app):
        return ACCESSIBILITY
    if _is_chromium(app):
        return CHROMIUM
    if _speaks_applescript(app, info):
        return APPLESCRIPT
    return UNSUPPORTED


def inspect_app(path: str | Path) -> Browser:
    """Build a `Browser` from a ``.app`` chosen in Finder.

    Raises `BrowserError` with a message worth showing the user.
    """
    app = app_bundle_root(path)
    if app is None:
        raise BrowserError("Choose an application bundle (a .app) rather than a plain file.")
    if not app.is_dir():
        raise BrowserError(f"{app.name} could not be found.")
    info = _read_info_plist(app)
    bundle_id = str(info.get("CFBundleIdentifier") or "").strip()
    if not bundle_id:
        raise BrowserError(
            f"{app.name} declares no bundle identifier, so OnTask has no reliable way to "
            "recognise it when it is in front."
        )
    name = str(
        info.get("CFBundleDisplayName") or info.get("CFBundleName") or app.stem
    ).strip() or app.stem
    return Browser(
        name=name,
        bundle_id=bundle_id,
        flavour=detect_flavour(app, info, bundle_id),
        app_path=str(app),
    )


def coerce(entry: Any) -> Browser | None:
    """Read one config entry, accepting the old bare-name format."""
    if isinstance(entry, Browser):
        return entry
    if isinstance(entry, dict):
        return Browser.from_dict(entry)
    if isinstance(entry, str):
        name = entry.strip()
        bundle_id = LEGACY_NAMES.get(name)
        if not bundle_id:
            return None
        return Browser(name=name, bundle_id=bundle_id, flavour=KNOWN_FLAVOURS.get(bundle_id, UNSUPPORTED))
    return None


def _declares_html(info: dict[str, Any]) -> bool:
    """Whether a bundle claims it can open HTML documents.

    This is what separates a browser from the other apps macOS is willing to
    hand an http:// URL to - password managers, launchers, single-site
    wrappers. They register the scheme; only a browser also declares HTML.
    """
    for entry in info.get("CFBundleDocumentTypes") or []:
        if not isinstance(entry, dict):
            continue
        names = list(entry.get("LSItemContentTypes") or [])
        names.append(entry.get("CFBundleTypeName") or "")
        if any("html" in str(name).lower() for name in names):
            return True
    return False


def installed_browsers() -> list[Browser]:
    """Every browser macOS knows about, for the picker. [] if unavailable.

    Asking LaunchServices which apps can open an https:// URL is how the list
    stays honest: it finds browsers wherever they are installed, including ones
    OnTask has never heard of, without scanning the disk.
    """
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL
    except Exception:
        return []
    try:
        workspace = NSWorkspace.sharedWorkspace()
        urls = workspace.URLsForApplicationsToOpenURL_(
            NSURL.URLWithString_("https://example.com")
        )
    except Exception:
        return []
    found: dict[str, Browser] = {}
    for url in urls or []:
        try:
            path = Path(str(url.path()))
        except Exception:
            continue
        try:
            info = _read_info_plist(path)
        except BrowserError:
            continue
        if not _declares_html(info):
            continue
        try:
            browser = inspect_app(path)
        except BrowserError:
            continue
        # The same browser can be registered from several copies on disk; the
        # bundle id is the identity, so the first one found wins.
        found.setdefault(browser.bundle_id, browser)
    return sorted(found.values(), key=lambda b: b.name.lower())


def needs_automation(browser: Browser) -> bool:
    return browser.flavour in (SAFARI, CHROMIUM, APPLESCRIPT)


def needs_accessibility(browser: Browser) -> bool:
    return browser.flavour == ACCESSIBILITY
