"""Reading a browser's address bar through the macOS Accessibility API.

Gecko browsers expose no AppleScript URL property, so the only way to tell which
site is in front is to walk the accessibility tree and read the URL bar's text.
That is inherently more fragile than AppleScript - it depends on the browser's
internal view hierarchy, which can change between releases - so every failure
degrades to app-level tracking rather than raising.

The bar is not always the same kind of control: Firefox exposes it as a text
field, while Zen exposes it as a combo box. Both roles are searched, and a node
whose description names it as the address bar is preferred over one that merely
happens to hold something URL-shaped.

Needs Accessibility permission, the same grant the global hotkeys use.
"""

from __future__ import annotations

import re
from collections import deque

from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    kAXChildrenAttribute,
    kAXComboBoxRole,
    kAXDescriptionAttribute,
    kAXFocusedWindowAttribute,
    kAXIdentifierAttribute,
    kAXRoleAttribute,
    kAXRoleDescriptionAttribute,
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
# Once a URL-shaped field is found, how much further to look for a field that
# names itself the address bar before settling for the first one.
EXTRA_NODES_AFTER_FALLBACK = 40

# Roles the address bar turns up as: a text field in Firefox, a combo box in Zen.
URL_BAR_ROLES = (kAXTextFieldRole, kAXComboBoxRole)

# Wording browsers put on the address bar. Matching one of these promotes a
# candidate above any other control that merely holds URL-shaped text.
URL_BAR_HINTS = (
    "address",
    "search with",
    "search or enter",
    "enter address",
    "url",
    "location",
    "awesomebar",
)


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


def _looks_like_the_url_bar(node) -> bool:
    """Whether a node labels itself as the address bar.

    Browsers describe it differently - Firefox says "Search with ... or enter
    address", Zen just says "Search..." - so a miss here is not a rejection, only
    the absence of a promotion.
    """
    for attribute in (
        kAXDescriptionAttribute,
        kAXTitleAttribute,
        kAXRoleDescriptionAttribute,
        kAXIdentifierAttribute,
    ):
        text = str(_copy(node, attribute) or "").lower()
        if text and any(hint in text for hint in URL_BAR_HINTS):
            return True
    return False


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
    # reached long before the bounds below matter. A self-described address bar
    # wins outright; otherwise the shallowest URL-shaped value is kept, which is
    # the toolbar rather than anything down in the page.
    #
    # Once that fallback exists, the search only looks a little further - one
    # level down, and a handful of nodes - for a self-described bar. Zen's bar
    # never describes itself, and walking on to MAX_NODES for it meant hundreds
    # of cross-process calls on every poll.
    fallback, fallback_depth, fallback_at = "", 0, 0
    queue = deque([(window, 0)])
    visited = 0
    while queue and visited < MAX_NODES:
        node, depth = queue.popleft()
        if fallback and (
            depth > fallback_depth + 1 or visited - fallback_at >= EXTRA_NODES_AFTER_FALLBACK
        ):
            break
        visited += 1
        if _copy(node, kAXRoleAttribute) in URL_BAR_ROLES:
            url = normalise_url(_copy(node, kAXValueAttribute))
            if url:
                if _looks_like_the_url_bar(node):
                    return url, title
                if not fallback:
                    fallback, fallback_depth, fallback_at = url, depth, visited
        if depth < MAX_DEPTH:
            for child in _copy(node, kAXChildrenAttribute) or []:
                queue.append((child, depth + 1))
    return fallback, title
