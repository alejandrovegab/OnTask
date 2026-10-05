"""No-UI mode: prints what the engine is doing and answers nothing.

Handy for confirming focus detection and rule matching before trusting the app,
and for checking that Automation permission is actually granted.
"""

from __future__ import annotations

import time
from pathlib import Path

from .app import Controller, Shell


class ConsoleShell(Shell):
    def show_prompt(self, prompt, controller):
        print(f"[prompt] {prompt.question()}   (answer in the app; ignoring here)")

    def realert(self, prompt):
        print("[prompt] re-alert")

    def close_prompt(self):
        print("[prompt] closed")

    def notify(self, title, message):
        print(f"[notify] {message}")

    def ask_add_rule(self, target, rule, controller):
        print(f"[suggest] would offer to approve {rule}")


def run(config_path: str | None = None) -> int:
    controller = Controller(
        shell=ConsoleShell(), config_path=Path(config_path) if config_path else None
    )
    controller.start_session()
    print(f"Config: {controller.config.path}")
    print(f"Focus provider: {type(controller.focus).__name__}")
    print("Ctrl-C to stop.\n")
    last = ""
    try:
        while True:
            controller.poll()
            line = " | ".join(controller.status_lines()[:1] + [controller.current_status_text()])
            if line != last:
                print(line)
                last = line
            time.sleep(controller.config.general.poll_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0
