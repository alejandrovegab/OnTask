"""The statistics window.

Runs as its own process, like Settings, so Tk stays clear of the menu bar app's
run loop. Everything shown is derived from the event log in `stats.py`; nothing
is computed here that could not be recomputed from that log, so changing a date
range is just a re-read.

Charts are drawn straight onto a Tk canvas. That avoids a plotting dependency
for what are two small charts, and it redraws on resize so the window stays
usable at any size.

Usage: python -m ontask.ui.tk.stats [path/to/config.json]
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ontask import ipc
from ontask.stats import Stats, Summary, default_stats_path, format_hour, format_span
from ontask.ui.tk.scrolling import WheelScroller
from ontask.ui.tk.window import bring_to_front, watch_raise

DAY = 86400.0
RANGES = {
    "All time": None,
    "Last 7 days": 7 * DAY,
    "Last 30 days": 30 * DAY,
    "Today": None,  # resolved against local midnight, not a rolling window
}

INK = "#3b6ea5"
WARN = "#c2593a"
GRID = "#d9d9d9"
MUTED = "#777777"


class Chart(tk.Canvas):
    """A small auto-scaling chart that redraws itself on resize."""

    def __init__(self, parent, height: int = 150, **kw):
        super().__init__(parent, height=height, highlightthickness=0, background="white", **kw)
        self._rows: list[tuple[str, list[float]]] = []
        self._series: list[tuple[str, str]] = []
        self._mode = "bars"
        self._unit = ""
        self.bind("<Configure>", lambda _event: self._redraw())

    def show(self, rows, series, mode: str = "bars", unit: str = "") -> None:
        """rows: [(label, [v1, v2...])]. series: [(name, colour)]."""
        self._rows, self._series, self._mode, self._unit = list(rows), list(series), mode, unit
        self._redraw()

    # -- drawing ----------------------------------------------------------

    def _redraw(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 40 or height < 40:
            return
        left, right, top, bottom = 46, 12, 16, 34
        plot_w, plot_h = width - left - right, height - top - bottom
        if plot_w <= 0 or plot_h <= 0:
            return
        if not self._rows:
            self.create_text(
                width / 2, height / 2, text="Nothing recorded yet", fill=MUTED, font=("", 11)
            )
            return

        peak = max((max(values, default=0.0) for _, values in self._rows), default=0.0)
        peak = peak or 1.0
        # Horizontal grid, four bands, labelled at the axis.
        for step in range(5):
            y = top + plot_h - (plot_h * step / 4)
            self.create_line(left, y, width - right, y, fill=GRID)
            self.create_text(
                left - 6, y, text=self._tick(peak * step / 4), anchor="e", fill=MUTED, font=("", 9)
            )

        if self._mode == "lines":
            self._draw_lines(left, top, plot_w, plot_h, peak)
        else:
            self._draw_bars(left, top, plot_w, plot_h, peak)
        self._draw_labels(left, top, plot_w, plot_h)
        self._draw_legend(left, height - 12)

    def _draw_bars(self, left, top, plot_w, plot_h, peak) -> None:
        slot = plot_w / len(self._rows)
        count = max(1, len(self._series))
        bar_w = max(2.0, min(22.0, (slot - 6) / count))
        for index, (_label, values) in enumerate(self._rows):
            base_x = left + slot * index + (slot - bar_w * count) / 2
            for series_index, value in enumerate(values[:count]):
                x = base_x + bar_w * series_index
                bar_h = plot_h * (value / peak)
                self.create_rectangle(
                    x,
                    top + plot_h - bar_h,
                    x + bar_w - 1,
                    top + plot_h,
                    fill=self._series[series_index][1],
                    width=0,
                )

    def _draw_lines(self, left, top, plot_w, plot_h, peak) -> None:
        if len(self._rows) == 1:
            # A single point has no line to draw; show it as a dot.
            self._mode = "bars"
            return self._draw_bars(left, top, plot_w, plot_h, peak)
        step = plot_w / (len(self._rows) - 1)
        for series_index, (_name, colour) in enumerate(self._series):
            points = []
            for index, (_label, values) in enumerate(self._rows):
                value = values[series_index] if series_index < len(values) else 0.0
                points.extend([left + step * index, top + plot_h - plot_h * (value / peak)])
            if len(points) >= 4:
                self.create_line(*points, fill=colour, width=2, smooth=False)

    def _draw_labels(self, left, top, plot_w, plot_h) -> None:
        # Thin the labels out rather than letting them overlap.
        stride = max(1, len(self._rows) // max(1, int(plot_w // 56)))
        slot = plot_w / len(self._rows)
        for index, (label, _values) in enumerate(self._rows):
            if index % stride:
                continue
            x = (
                left + slot * index + slot / 2
                if self._mode == "bars"
                else (left + (plot_w / max(1, len(self._rows) - 1)) * index)
            )
            self.create_text(x, top + plot_h + 12, text=label, fill=MUTED, font=("", 9))

    def _draw_legend(self, left, y) -> None:
        x = left
        for name, colour in self._series:
            self.create_rectangle(x, y - 5, x + 10, y + 3, fill=colour, width=0)
            self.create_text(x + 15, y, text=name, anchor="w", fill=MUTED, font=("", 9))
            x += 22 + len(name) * 6

    def _tick(self, value: float) -> str:
        if self._unit == "minutes":
            return f"{value:.0f}m" if value < 90 else f"{value / 60:.1f}h"
        return f"{value:.0f}"


class StatsWindow:
    def __init__(self, root: tk.Tk, config_path: Path | None):
        self.root = root
        self.config_path = config_path
        self.stats = Stats.load(default_stats_path(config_path))
        root.title("OnTask Statistics")
        root.minsize(780, 620)
        self._build()
        self.reload()

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Showing").pack(side="left")
        self.range_var = tk.StringVar(value="All time")
        combo = ttk.Combobox(
            header, textvariable=self.range_var, state="readonly", width=14, values=list(RANGES)
        )
        combo.pack(side="left", padx=(6, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        ttk.Button(header, text="Refresh", command=self.reload).pack(side="left", padx=(8, 0))
        ttk.Button(header, text="Reset statistics", command=self.reset).pack(side="right")
        self.range_label = ttk.Label(header, text="", foreground=MUTED)
        self.range_label.pack(side="right", padx=(0, 12))

        body, self.scroll_host = _scrollable(outer)
        body.pack(fill="both", expand=True, pady=(10, 0))

        self.summary_box = ttk.LabelFrame(self.scroll_host, text="Summary", padding=10)
        self.summary_box.pack(fill="x")
        self.summary_grid = ttk.Frame(self.summary_box)
        self.summary_grid.pack(fill="x")

        self.profile_box = ttk.LabelFrame(
            self.scroll_host, text="Session time by profile", padding=10
        )
        self.profile_box.pack(fill="x", pady=(12, 0))
        self.profile_grid = ttk.Frame(self.profile_box)
        self.profile_grid.pack(fill="x")

        self.target_box = ttk.LabelFrame(
            self.scroll_host, text="What pulls you away most", padding=10
        )
        self.target_box.pack(fill="x", pady=(12, 0))
        self.target_grid = ttk.Frame(self.target_box)
        self.target_grid.pack(fill="x")

        hour_box = ttk.LabelFrame(self.scroll_host, text="By time of day", padding=10)
        hour_box.pack(fill="both", expand=True, pady=(12, 0))
        self.hour_chart = Chart(hour_box, height=150)
        self.hour_chart.pack(fill="both", expand=True)

        day_box = ttk.LabelFrame(
            self.scroll_host, text="Over time - is distraction trending down?", padding=10
        )
        day_box.pack(fill="both", expand=True, pady=(12, 0))
        self.day_chart = Chart(day_box, height=160)
        self.day_chart.pack(fill="both", expand=True)

    # -- data -------------------------------------------------------------

    def _since(self) -> float | None:
        choice = self.range_var.get()
        if choice == "Today":
            local = time.localtime()
            midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
            return midnight
        window = RANGES.get(choice)
        return time.time() - window if window else None

    def reload(self) -> None:
        self.stats = Stats.load(default_stats_path(self.config_path))
        summary = self.stats.summary(self._since())
        self._fill_summary(summary)
        self._fill_profiles(summary)
        self._fill_targets(summary)
        self._fill_charts(summary)
        if summary.first_event:
            first = time.strftime("%d %b %Y", time.localtime(summary.first_event))
            self.range_label.config(text=f"Recording since {first}")
        else:
            self.range_label.config(text="")

    def reset(self) -> None:
        if not messagebox.askyesno(
            "OnTask", "Delete all recorded statistics? Sessions and check-ins cannot be recovered."
        ):
            return
        self.stats.clear()
        # The running app holds its own copy of the log; tell it to drop
        # everything up to now, or its next save would restore the lot.
        ipc.Signal(ipc.runtime_dir(self.config_path), ipc.STATS_CLEARED).send()
        self.reload()

    def _fill_summary(self, s: Summary) -> None:
        recovered = f"{s.recovered_count}" if s.recovered_count else "0"
        rows = [
            ("Time in sessions", format_span(s.session_seconds)),
            ("Sessions", str(s.session_count)),
            ("Average session", format_span(s.average_session_seconds)),
            ("Time off task", format_span(s.off_task_seconds)),
            ("...of that, on blocked apps and sites", format_span(s.blocked_seconds)),
            ("Share of session time off task", f"{s.distraction_rate * 100:.0f}%"),
            ("Check-ins answered yes", str(s.yes_count)),
            ("Check-ins answered no", str(s.no_count)),
            ("Check-ins ignored", str(s.ignored_count)),
            ("Answered yes", f"{s.yes_rate * 100:.0f}%" if s.answered_count else "-"),
            ("Check-ins followed by a return to work", recovered),
            ("Most focused hour", format_hour(s.best_hour()) if s.best_hour() is not None else "-"),
            (
                "Most distracted hour",
                format_hour(s.worst_hour()) if s.worst_hour() is not None else "-",
            ),
        ]
        _fill_grid(self.summary_grid, rows, columns=2)

    def _fill_profiles(self, s: Summary) -> None:
        rows = [(name, format_span(seconds)) for name, seconds in s.seconds_by_profile.items()]
        _fill_grid(self.profile_grid, rows or [("No sessions recorded yet", "")], columns=2)

    def _fill_targets(self, s: Summary) -> None:
        targets = s.top_targets(8)
        if not targets:
            _fill_grid(self.target_grid, [("Nothing recorded yet", "")], columns=1)
            return
        peak = max(seconds for _, seconds in targets) or 1.0
        for child in self.target_grid.winfo_children():
            child.destroy()
        for row, (label, seconds) in enumerate(targets):
            ttk.Label(self.target_grid, text=label).grid(row=row, column=0, sticky="w", pady=1)
            bar = tk.Canvas(self.target_grid, height=12, width=240, highlightthickness=0)
            bar.grid(row=row, column=1, sticky="w", padx=(12, 8))
            bar.create_rectangle(0, 2, max(2, 240 * seconds / peak), 11, fill=WARN, width=0)
            ttk.Label(self.target_grid, text=format_span(seconds), foreground=MUTED).grid(
                row=row, column=2, sticky="w"
            )

    def _fill_charts(self, s: Summary) -> None:
        hours = sorted(set(s.answers_by_hour) | set(s.off_task_by_hour))
        self.hour_chart.show(
            [
                (
                    format_hour(hour),
                    [
                        s.off_task_by_hour.get(hour, 0.0) / 60.0,
                        float(s.answers_by_hour.get(hour, (0, 0))[1]),
                    ],
                )
                for hour in hours
            ],
            [("Minutes off task", WARN), ("Check-ins answered no", INK)],
            mode="bars",
            unit="minutes",
        )
        self.day_chart.show(
            [(day[5:], [session / 60.0, off / 60.0]) for day, session, off in s.by_day],
            [("Session minutes", INK), ("Minutes off task", WARN)],
            mode="lines",
            unit="minutes",
        )


# -- helpers --------------------------------------------------------------


def _fill_grid(parent: ttk.Frame, rows, columns: int = 2) -> None:
    for child in parent.winfo_children():
        child.destroy()
    for index, (label, value) in enumerate(rows):
        ttk.Label(parent, text=label).grid(row=index, column=0, sticky="w", pady=1)
        if columns > 1:
            ttk.Label(parent, text=value, foreground="#222").grid(
                row=index, column=1, sticky="e", padx=(24, 0)
            )
    parent.columnconfigure(1, weight=1)


def _scrollable(parent) -> tuple[ttk.Frame, ttk.Frame]:
    """A vertically scrolling area, since the report is taller than the window."""
    holder = ttk.Frame(parent)
    canvas = tk.Canvas(holder, highlightthickness=0)
    bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
    # Kept on the holder so it lives as long as the scrolling area does.
    holder.wheel = WheelScroller(holder, canvas)
    return holder, inner


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv and argv[0] else None
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.4)
    except tk.TclError:
        pass
    StatsWindow(root, path)
    bring_to_front(root)
    watch_raise(root, path, ipc.RAISE_STATS)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
