"""Focus detection for Windows and Linux.

Neither path can read browser tab URLs the way macOS can, so profiles on those
platforms match on the application only. Returning an empty target when
detection is impossible is deliberate: `matching.classify` treats an unknown
target as on-task so the app stays quiet instead of nagging constantly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

from . import FocusProvider, FocusTarget

if TYPE_CHECKING:
    from ..browsers import Browser


class FallbackFocusProvider(FocusProvider):
    def __init__(self) -> None:
        self._impl = None
        if os.name == "nt":
            self._impl = _windows_target
        elif shutil.which("xdotool"):
            self._impl = _xdotool_target

    def current(self, browsers: list[Browser] | None = None) -> FocusTarget:
        if self._impl is None:
            return FocusTarget()
        try:
            return self._impl()
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


def _xdotool_target() -> FocusTarget:
    def run(args: list[str]) -> str:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=1.0)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    window = run(["xdotool", "getactivewindow"])
    if not window:
        return FocusTarget()
    title = run(["xdotool", "getwindowname", window])
    name = ""
    if shutil.which("xprop"):
        wm_class = run(["xprop", "-id", window, "WM_CLASS"])
        if '"' in wm_class:
            parts = [p for p in wm_class.split('"') if p.strip(", ")]
            if parts:
                name = parts[-1]
    return FocusTarget(app_name=name, bundle_id=name.lower(), title=title)
