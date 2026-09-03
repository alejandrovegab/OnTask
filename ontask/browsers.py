"""Catalogue of browsers OnTask knows how to read tab URLs from.

Kept free of platform imports so the settings window can list them anywhere.
"""

from __future__ import annotations

SAFARI = "safari"
CHROMIUM = "chromium"
UNSUPPORTED = "unsupported"

# name -> (macOS bundle id, AppleScript flavour)
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
    # Firefox exposes no AppleScript URL property; it is tracked app-level only.
    "Firefox": ("org.mozilla.firefox", UNSUPPORTED),
}

DEFAULT_BROWSERS = ["Safari", "Google Chrome", "Arc", "Brave Browser", "Microsoft Edge"]
