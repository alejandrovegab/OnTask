"""Tk check-in window, used on platforms without the AppKit shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class TkPrompt:
    def __init__(self, root: tk.Tk, on_answer):
        self.root = root
        self.on_answer = on_answer
        self.window: tk.Toplevel | None = None
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()

    def _build(self) -> None:
        if self.window is not None:
            return
        window = tk.Toplevel(self.root)
        window.title("OnTask")
        window.resizable(False, False)
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", lambda: None)  # must be answered
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.title_var, font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.subtitle_var, foreground="#666").pack(anchor="w", pady=(4, 16))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e")
        ttk.Button(buttons, text="No (N)", command=lambda: self.on_answer(False)).pack(side="left", padx=6)
        yes = ttk.Button(buttons, text="Yes (Y)", command=lambda: self.on_answer(True))
        yes.pack(side="left")
        for key in ("y", "Y"):
            window.bind(key, lambda _e: self.on_answer(True))
        for key in ("n", "N"):
            window.bind(key, lambda _e: self.on_answer(False))
        self.window = window

    def show(self, question: str, subtitle: str, play_sound: bool = True) -> None:
        self._build()
        self.title_var.set(question)
        self.subtitle_var.set(subtitle)
        self._center()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        if play_sound:
            self.window.bell()

    def realert(self, subtitle: str | None = None, play_sound: bool = True) -> None:
        if self.window is None:
            return
        if subtitle is not None:
            self.subtitle_var.set(subtitle)
        self.window.lift()
        self.window.focus_force()
        if play_sound:
            self.window.bell()

    def close(self) -> None:
        if self.window is not None:
            self.window.withdraw()

    @property
    def visible(self) -> bool:
        return self.window is not None and self.window.winfo_viewable()

    def _center(self) -> None:
        self.window.update_idletasks()
        width = max(self.window.winfo_reqwidth(), 380)
        height = self.window.winfo_reqheight()
        x = (self.window.winfo_screenwidth() - width) // 2
        y = int((self.window.winfo_screenheight() - height) * 0.35)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
