"""Global hotkeys via pynput, marshalled onto the UI thread.

pynput fires callbacks on its own listener thread, so every binding just posts
an action to the controller's queue; the UI timer runs it. On macOS this needs
Accessibility permission, and `available()` reports whether it started.
"""

from __future__ import annotations


class HotkeyManager:
    def __init__(self, controller):
        self.controller = controller
        self._listener = None
        self.error: str = ""

    def start(self) -> bool:
        self.stop()
        bindings = self._bindings()
        if not bindings:
            return False
        try:
            from pynput import keyboard
        except Exception as exc:
            self.error = f"pynput unavailable: {exc}"
            return False
        try:
            self._listener = keyboard.GlobalHotKeys(bindings)
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:
            self.error = str(exc)
            self._listener = None
            return False
        self.error = ""
        return True

    def _bindings(self) -> dict:
        keys = self.controller.config.general.hotkeys
        wanted = {
            keys.toggle_session: "toggle_session",
            keys.answer_yes: "answer_yes",
            keys.answer_no: "answer_no",
        }
        bindings = {}
        for combo, action in wanted.items():
            combo = (combo or "").strip()
            if combo:
                bindings[combo] = _post(self.controller, action)
        return bindings

    def restart(self) -> bool:
        return self.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def available(self) -> bool:
        return self._listener is not None


def _post(controller, action: str):
    def handler() -> None:
        controller.post(action)

    return handler
