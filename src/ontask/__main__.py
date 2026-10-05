"""Entry point: pick the best shell for this platform.

python -m ontask                     run the app
python -m ontask --window settings   open one window on its own: settings, stats or setup
python -m ontask --headless          run with no UI (useful for checking focus detection)

The app opens its windows by starting itself again with --window, so the same
command works from source and from inside the .app bundle; see `self_command`.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

# The windows that run as their own process, and the module that draws each.
WINDOWS = {
    "settings": "ontask.ui.tk.settings",
    "stats": "ontask.ui.tk.stats",
    "setup": "ontask.ui.tk.browser_setup",
}


def self_command() -> list[str]:
    """The command that starts another copy of OnTask.

    From source that is this interpreter running the package. Inside a bundle
    there is no such interpreter to call: py2app marks the process frozen and
    records the bundle's own executable in ARGVZERO, and PyInstaller's
    executable is sys.executable itself.
    """
    if getattr(sys, "frozen", False):
        return [os.environ.get("ARGVZERO") or sys.executable]
    return [sys.executable, "-m", "ontask"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ontask", description="Periodic on-task check-ins.")
    parser.add_argument("--window", choices=sorted(WINDOWS), help="open one window and exit")
    parser.add_argument("--settings", action="store_true", help="same as --window settings")
    parser.add_argument(
        "--headless", action="store_true", help="run without a UI, logging to stdout"
    )
    parser.add_argument("--config", help="path to config.json")
    args = parser.parse_args(argv)

    window = "settings" if args.settings else args.window
    if window:
        module = importlib.import_module(WINDOWS[window])
        return module.main([args.config] if args.config else [])

    if args.headless:
        from .headless import run as headless_run

        return headless_run(args.config)

    from . import ipc

    # One OnTask per machine. Launching it again - from Spotlight, the Dock, a
    # second terminal - is a request to see the app that is already running,
    # not to start a rival copy with its own timers.
    config_path = Path(args.config).expanduser() if args.config else None
    directory = ipc.runtime_dir(config_path)
    lock = ipc.Lock(directory)
    if not lock.acquire():
        ipc.Signal(directory, ipc.OPEN_SETTINGS).send()
        print("OnTask is already running; opening its settings.")
        return 0

    try:
        if sys.platform == "darwin":
            try:
                from .platform.macos.menubar import run as mac_run
            except ImportError as exc:
                print(
                    f"Menu bar shell unavailable ({exc}); falling back to the window shell.",
                    file=sys.stderr,
                )
            else:
                mac_run(config_path)
                return 0

        from .ui.tk.shell import run as tk_run

        tk_run(config_path)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
