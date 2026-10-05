"""What the user is currently looking at, and how to find that out.

`FocusTarget` is the platform-neutral description of the frontmost app plus,
when that app is a supported browser, the URL of the active tab.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from ..core.browsers import Browser


@dataclass(frozen=True)
class FocusTarget:
    app_name: str = ""
    bundle_id: str = ""
    url: str = ""
    title: str = ""

    @property
    def is_unknown(self) -> bool:
        return not self.app_name and not self.bundle_id

    @property
    def host(self) -> str:
        if not self.url:
            return ""
        netloc = urlsplit(self.url).netloc.lower()
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
        return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc

    @property
    def path(self) -> str:
        return urlsplit(self.url).path.lower().rstrip("/") if self.url else ""

    def key(self) -> str:
        """Stable identity used to count consecutive yes answers per target."""
        if self.host:
            return f"site:{self.host}"
        return f"app:{self.bundle_id or self.app_name}".lower()

    def label(self) -> str:
        """Short human-readable name for prompts and menus."""
        if self.host:
            host = self.host[4:] if self.host.startswith("www.") else self.host
            return host
        return self.app_name or self.bundle_id or "an unknown app"

    def describe(self) -> str:
        if self.host and self.app_name:
            return f"{self.label()} ({self.app_name})"
        return self.label()


UNKNOWN = FocusTarget()


class FocusProvider:
    """Interface for platform focus detection."""

    def current(self, browsers: list[Browser] | None = None) -> FocusTarget:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass


class NullFocusProvider(FocusProvider):
    """Used where detection is impossible.

    Every target comes back unknown, which `matching.classify` treats as
    on-task, so the app stays quiet instead of nagging constantly.
    """

    def current(self, browsers: list[Browser] | None = None) -> FocusTarget:
        return FocusTarget()


def get_provider() -> FocusProvider:
    """Return the best focus provider available on this platform."""
    try:
        if sys.platform == "darwin":
            from ..platform.macos.focus import MacFocusProvider

            return MacFocusProvider()
        if sys.platform == "win32":
            from ..platform.windows.focus import WindowsFocusProvider

            return WindowsFocusProvider()
        from ..platform.linux.focus import X11FocusProvider

        return X11FocusProvider()
    except Exception:
        return NullFocusProvider()
