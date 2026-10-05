"""Windows focus detection: the foreground window's process, via Win32.

Browser tab URLs are not read here yet, so profiles match on the application
only.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ...focus import FocusProvider, FocusTarget

if TYPE_CHECKING:
    from ...core.browsers import Browser


class WindowsFocusProvider(FocusProvider):
    def current(self, browsers: list[Browser] | None = None) -> FocusTarget:
        try:
            return _windows_target()
        except Exception:
            return FocusTarget()


def _windows_target() -> FocusTarget:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return FocusTarget()

    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    name = ""
    if handle:
        try:
            size = wintypes.DWORD(260)
            path = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                name = os.path.splitext(os.path.basename(path.value))[0]
        finally:
            kernel32.CloseHandle(handle)
    return FocusTarget(app_name=name, bundle_id=name.lower(), title=title)
