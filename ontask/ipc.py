"""File-based signalling between the running app and its helper windows.

OnTask is really three processes: the menu bar app, and the Tk settings and
statistics windows it launches. They need to say very little to each other -
"you are already running, come to the front", "the user asked for settings" -
so the whole channel is a file whose modification time is the message. That
needs no port, no permission, and no cleanup if a process dies.

The lock is advisory and self-healing: a lock file naming a pid that is gone is
treated as stale rather than as a running app, so a crash cannot leave OnTask
unable to start.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

LOCK_NAME = ".ontask.lock"
OPEN_SETTINGS = ".ontask-open-settings"
RAISE_SETTINGS = ".ontask-raise-settings"
RAISE_STATS = ".ontask-raise-stats"


def runtime_dir(config_path: Path | None) -> Path:
    """Where the marker files live: beside config.json."""
    if config_path is not None:
        return Path(config_path).parent
    from .config import default_config_path

    return default_config_path().parent


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Owned by someone else, but it exists.
        return True
    except OSError:
        return False
    return True


class Lock:
    """Single-instance guard naming the pid that holds it."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / LOCK_NAME
        self.held = False

    def holder(self) -> int:
        try:
            return int(self.path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            return 0

    def running_elsewhere(self) -> bool:
        pid = self.holder()
        return pid != os.getpid() and _pid_alive(pid)

    def acquire(self) -> bool:
        if self.running_elsewhere():
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            # Without a lock the app still runs; it just cannot be single.
            return True
        self.held = True
        return True

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        try:
            if self.holder() == os.getpid():
                self.path.unlink()
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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass

    def received(self) -> bool:
        """True once per nudge sent since the last call."""
        stamp = self._stamp()
        if stamp and stamp != self._seen:
            self._seen = stamp
            return True
        return False
