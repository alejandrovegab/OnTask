"""File-based signalling between the running app and its helper windows.

OnTask is really three processes: the menu bar app, and the Tk settings and
statistics windows it launches. They need to say very little to each other -
"you are already running, come to the front", "the user asked for settings" -
so the whole channel is a file whose modification time is the message. That
needs no port, no permission, and no cleanup if a process dies.

The single-instance lock is an OS file lock, which the system drops the moment
the holding process exits - quit, crash or kill alike. Nothing has to clean it
up, and there is no recorded pid to go stale or be reused by another process,
so OnTask can never be locked out of starting.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .core.files import ensure_private_dir, make_private, write_private

LOCK_NAME = ".ontask.lock"
OPEN_SETTINGS = ".ontask-open-settings"
RAISE_SETTINGS = ".ontask-raise-settings"
RAISE_STATS = ".ontask-raise-stats"
RAISE_SETUP = ".ontask-raise-setup"
STATS_CLEARED = ".ontask-stats-cleared"


def runtime_dir(config_path: Path | None) -> Path:
    """Where the marker files live: beside config.json."""
    if config_path is not None:
        return Path(config_path).parent
    from .core.config import default_config_path

    return default_config_path().parent


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class Lock:
    """Single-instance guard held for the life of the process.

    The descriptor is not inheritable (Python's default), so the helper windows
    the app spawns cannot keep the lock alive after the app itself has gone.
    The pid written into the file is for people reading it, not for deciding
    anything.
    """

    def __init__(self, directory: Path):
        self.path = Path(directory) / LOCK_NAME
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        try:
            ensure_private_dir(self.path.parent)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            # The mode above only applies to a new file; one left by an older
            # version keeps whatever it had.
            make_private(self.path)
        except OSError:
            # Without a lock the app still runs; it just cannot be single.
            return True
        if not _try_lock(fd):
            os.close(fd)
            return False
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            pass
        self._fd = fd
        return True

    def release(self) -> None:
        # The file is left in place: unlinking it would let a newcomer lock a
        # fresh file while a late holder still has the old one.
        fd, self._fd = self._fd, None
        if fd is None:
            return
        _unlock(fd)
        try:
            os.close(fd)
        except OSError:
            pass


class Signal:
    """A one-way nudge. The sender touches the file; the reader notices."""

    def __init__(self, directory: Path, name: str):
        self.path = Path(directory) / name
        self._seen = self._stamp()

    def _stamp(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def send(self) -> None:
        try:
            write_private(self.path, str(time.time()))
        except OSError:
            pass

    def sent_at(self) -> float:
        """Wall-clock time of the last nudge, as written by `send`; 0 if unknown."""
        try:
            return float(self.path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0.0

    def received(self) -> bool:
        """True once per nudge sent since the last call."""
        stamp = self._stamp()
        if stamp and stamp != self._seen:
            self._seen = stamp
            return True
        return False
