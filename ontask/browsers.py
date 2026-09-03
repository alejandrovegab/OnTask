"""Catalogue of browsers OnTask knows how to read tab URLs from.

Kept free of platform imports so the settings window can list them anywhere.

Flavours describe *how* the URL is obtained:

  safari / chromium  AppleScript, needs Automation permission (one prompt per
                     browser). Exact and cheap.
  accessibility      Walks the accessibility tree to read the address bar, for
                     browsers with no AppleScript URL support. Needs
                     Accessibility permission and is best effort.
  unsupported        App-level tracking only.
"""

from __future__ import annotations

SAFARI = "safari"
CHROMIUM = "chromium"
ACCESSIBILITY = "accessibility"
UNSUPPORTED = "unsupported"

# name -> (macOS bundle id, flavour)
BROWSERS: dict[str, tuple[str, str]] = {
    "Safari": ("com.apple.Safari", SAFARI),
    "Safari Technology Preview": ("com.apple.SafariTechnologyPreview", SAFARI),
    "Google Chrome": ("com.google.Chrome", CHROMIUM),
    "Google Chrome Canary": ("com.google.Chrome.canary", CHROMIUM),
    "Chromium": ("org.chromium.Chromium", CHROMIUM),
    "Brave Browser": ("com.brave.Browser", CHROMIUM),
    "Microsoft Edge": ("com.microsoft.edgemac", CHROMIUM),
    "Arc": ("company.thebrowser.Browser", CHROMIUM),
    "Dia": ("company.thebrowser.dia", CHROMIUM),
    "Vivaldi": ("com.vivaldi.Vivaldi", CHROMIUM),
    "Opera": ("com.operasoftware.Opera", CHROMIUM),
    # Gecko browsers expose no AppleScript URL property, so they are read from
    # the accessibility tree instead.
    "Firefox": ("org.mozilla.firefox", ACCESSIBILITY),
    "Firefox Developer Edition": ("org.mozilla.firefoxdeveloperedition", ACCESSIBILITY),
    "Zen": ("app.zen-browser.zen", ACCESSIBILITY),
    "LibreWolf": ("io.gitlab.librewolf", ACCESSIBILITY),
}

DEFAULT_BROWSERS = [
    "Safari",
    "Google Chrome",
    "Arc",
    "Brave Browser",
    "Microsoft Edge",
    "Firefox",
]


def flavour_of(name: str) -> str:
    entry = BROWSERS.get(name)
    return entry[1] if entry else UNSUPPORTED


def needs_automation(name: str) -> bool:
    return flavour_of(name) in (SAFARI, CHROMIUM)


def needs_accessibility(name: str) -> bool:
    return flavour_of(name) == ACCESSIBILITY
