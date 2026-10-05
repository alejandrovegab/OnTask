"""Entry point: pick the best shell for this platform.

python -m ontask                 run the app
python -m ontask --settings      open the settings window only
python -m ontask --headless      run with no UI (useful for checking focus detection)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ontask", description="Periodic on-task check-ins.")
    parser.add_argument("--settings", action="store_true", help="open the settings window and exit")
    parser.add_argument(
        "--headless", action="store_true", help="run without a UI, logging to stdout"
    )
    parser.add_argument("--config", help="path to config.json")
    args = parser.parse_args(argv)

    if args.settings:
        from .ui.settings_app import main as settings_main

        return settings_main([args.config] if args.config else [])

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

        from .ui.app_tk import run as tk_run

        tk_run(config_path)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
