"""Reading a browser's address bar through the macOS Accessibility API.

Firefox exposes no AppleScript URL property, so the only way to tell which site
is in front is to walk its accessibility tree and read the URL bar's text. That
is inherently more fragile than AppleScript - it depends on the browser's
internal view hierarchy, which can change between releases - so it is opt-in per
browser and every failure degrades to app-level tracking rather than raising.

Needs Accessibility permission, the same grant the global hotkeys use.
"""

from __future__ import annotations

import re

from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    kAXChildrenAttribute,
    kAXFocusedWindowAttribute,
    kAXRoleAttribute,
    kAXTextFieldRole,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXWindowsAttribute,
)

# Bare host, optionally with a path: "github.com", "docs.rs/foo/bar".
_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(/.*)?$", re.IGNORECASE)

# The tree is wide; these bounds keep a poll cheap even if the walk finds nothing.
MAX_NODES = 400
MAX_DEPTH = 12


def accessibility_trusted() -> bool:
    """Whether this process may read other apps' accessibility trees."""
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def request_accessibility() -> bool:
    """Ask macOS to show the Accessibility permission prompt.

    Only call this from an explicit user action; it opens a system dialog.
    """
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        return False


def _copy(element, attribute):
    """AXUIElementCopyAttributeValue, returning None instead of an error code."""
    try:
        error, value = AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    return value if error == 0 else None


def _focused_window(app):
    window = _copy(app, kAXFocusedWindowAttribute)
    if window is not None:
        return window
    windows = _copy(app, kAXWindowsAttribute)
    return windows[0] if windows else None


def normalise_url(value) -> str:
    """Turn address bar text into a URL, or "" if it is not one.

    The bar often holds a search query rather than an address, and Firefox drops
    the scheme when the bar is not focused.
    """
    if not value:
        return ""
    text = str(value).strip()
    if not text or " " in text:
        return ""
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return text
    if lowered.startswith(("about:", "chrome:", "file:", "moz-extension:")):
        return ""
    return f"https://{text}" if _DOMAIN.match(text) else ""


def address_bar(pid: int) -> tuple[str, str]:
    """Best-effort (url, window title) for the frontmost window of `pid`."""
    if not accessibility_trusted():
        return "", ""
    try:
        app = AXUIElementCreateApplication(pid)
    except Exception:
        return "", ""
    if app is None:
        return "", ""
    window = _focused_window(app)
    if window is None:
        return "", ""
    title = str(_copy(window, kAXTitleAttribute) or "")

    # Breadth-first: the toolbar sits near the top of the tree, so the URL bar is
    # reached long before the bounds below matter.
    queue = [(window, 0)]
    visited = 0
    while queue and visited < MAX_NODES:
        node, depth = queue.pop(0)
        visited += 1
        if _copy(node, kAXRoleAttribute) == kAXTextFieldRole:
            url = normalise_url(_copy(node, kAXValueAttribute))
            if url:
                return url, title
        if depth < MAX_DEPTH:
            for child in _copy(node, kAXChildrenAttribute) or []:
                queue.append((child, depth + 1))
    return "", title
