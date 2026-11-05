"""The Production tracking tab: what your planets can produce in a chosen window,
what the recorded programs produced, and how to rebalance them for a plan."""

from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from tkinter import ttk

from .colonies import CharacterPI
from .db import PIDatabase
from .esi import default_store_path
from .icon_set import IconSet
from .production import Recipe, recipe_demand
from .results_model import (BALANCED, LIMITING, NO_SUPPLY, SURPLUS, UNDER_SUPPLIED,
                            format_compact, format_duration, nice_ticks)
from .results_view import (BAR, BASELINE, FONT_SMALL, GRID, PAD, ROW, SECONDARY, SERIES_BLUE,
                           _Panel)
from .settings import coerce
from .theme import FG, MUTED, RAISED
from .tracking import (ProgramRecord, RecordCurve, Tuning, WindowEstimate, average_planet_rate,
                       estimate_window, history_path, load_history, record_curve,
                       record_programs, refine_rates, save_history, tune)

WINDOWS = {"24 hours": 24, "3 days": 72, "7 days": 168, "14 days": 336, "30 days": 720}
LATEST_PLAN = "Latest plan or order"
_PLOT_HEIGHT = 130
_HOVER = "hover"


@dataclass
class _Chart:
    x0: float
    x1: float
    top: float
    bottom: float
    peak: float
    values: Sequence[float]
    label_of: Callable[[int], str]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _output_text(rate: float, tuning: Tuning) -> str:
    if rate <= 0:
        return "None"
    if tuning.is_order:
        return format_duration(1.0 / rate)
    return f"{format_compact(rate)}/hr"


class TrackingPanel(_Panel):
    def __init__(self, master, icons: IconSet):
        super().__init__(master, icons)
        self.estimate: WindowEstimate | None = None
        self.curve: RecordCurve | None = None
        self.tuning: Tuning | None = None
        self.tuning_note = ""
        self.window_name = ""
        self._charts: list[_Chart] = []
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", lambda _e: self.canvas.delete(_HOVER))

    def show(self, estimate, curve, tuning, tuning_note, window_name) -> None:
        self.estimate, self.curve = estimate, curve
        self.tuning, self.tuning_note = tuning, tuning_note
        self.window_name = window_name
        self.redraw()

    # ------------------------------------------------------------------ layout
    def _draw(self, width: int) -> int:
        self._charts = []
        if self.estimate is None or not self.estimate.extractors:
            return self.placeholder(
                width, "Read your characters on the Characters tab to see what their "
                       "planets can produce. This needs extractors with a program.")
        y = self._draw_summary(14, width) + 16
        y = self._draw_window_chart(y, width) + 16
        y = self._draw_resources(y, width) + 16
        y = self._draw_tuning(y, width) + 16
        return self._draw_history(y, width) + 16

    def _draw_summary(self, y: int, width: int) -> int:
        estimate = self.estimate
        now = datetime.now(timezone.utc)
        stale = "No update time"
        if estimate.oldest_update:
            age = max((now - estimate.oldest_update).total_seconds(), 0) / 3600
            stale = f"Oldest colony read {format_duration(age)} ago"
        y = self.tiles(y, width, [
            (f"Output in {self.window_name}", format_compact(estimate.total), "Raw units, predicted"),
            ("Average rate", f"{format_compact(estimate.per_hour)}/hr", "Raw units, all planets"),
            ("Extractors running", f"{estimate.running} of {estimate.extractors}",
             "As of each colony's last update"),
        ]) + 20
        idle = estimate.extractors - estimate.running
        text = ("This shows what your planets can produce, assuming each extractor starts "
                "its latest program now and restarts it whenever it ends.")
        if idle:
            text += f" {_plural(idle, 'extractor')} stopped, so today's real output is lower."
        item = self.text(PAD, y, text + f" {stale}.", color=SECONDARY, wrap=width - 2 * PAD)
        return self.canvas.bbox(item)[3] + 4

    def _draw_window_chart(self, y: int, width: int) -> int:
        estimate = self.estimate
        y = self.section(y + 8, "Output over the window",
                         "Raw units per hour, all planets, from a restart")
        step = estimate.hours / max(len(estimate.curve), 1)
        return self._line_chart(
            y, width, estimate.curve,
            [format_duration(estimate.hours * i / 4) if i else "Start" for i in range(5)],
            lambda i: f"{format_duration(i * step) if i else 'Start'}: "
                      f"{estimate.curve[i]:,.0f}/hr")

    def _draw_resources(self, y: int, width: int) -> int:
        y = self.section(y, "By resource", "Each line has its own scale, to show its shape")
        x0, x1 = PAD + 256, max(PAD + 356, width - 310)
        for row in self.estimate.resources:
            cy = y + ROW / 2
            self.named_row(cy, row.name, _plural(row.planets, "planet"))
            self._sparkline(x0, x1, cy - 9, cy + 9, row.curve)
            self.text(x1 + 12, cy, f"{row.per_hour:,.0f}/hr")
            self.text(x1 + 90, cy, f"{format_compact(row.window_total)} total", color=SECONDARY)
            if row.usable_per_hour < row.per_hour - 0.5:
                y += ROW
                self.status(PAD + 22, y + ROW / 2, LIMITING,
                            f"Basic factories on these planets can only take "
                            f"{row.usable_per_hour:,.0f}/hr of this")
            y += ROW
        return y

    def _draw_tuning(self, y: int, width: int) -> int:
        tuning = self.tuning
        if tuning is None:
            y = self.section(y, "Balance against a plan")
            item = self.text(PAD, y + 8, self.tuning_note, color=SECONDARY, wrap=width - 2 * PAD)
            return self.canvas.bbox(item)[3]
        name = "this order" if tuning.is_order else tuning.product
        y = self.section(y, f"Balance for {name}", self.tuning_note)
        unit = "Finish time" if tuning.is_order else "Units per hour"
        y = self.tiles(y + 2, width, [
            ("Extraction today", _output_text(tuning.output_now, tuning), unit),
            ("After rebalancing", _output_text(tuning.output_best, tuning), unit),
            ("Your factories allow", _output_text(tuning.factory_output, tuning), unit),
            ("Planets to share", str(tuning.planets),
             f"Extractor planets + {tuning.free_slots} free slots"),
        ]) + 22
        if not tuning.enough_planets:
            self.status(PAD, y, NO_SUPPLY, f"This needs {len(tuning.resources)} resources, but "
                                           f"you only have {tuning.planets} planets to share.")
            y += 22
        y = self._draw_adjustments(y, width, tuning)
        if tuning.spare:
            spare = ", ".join(f"{name} on {_plural(count, 'planet')}"
                              for name, count in tuning.spare)
            item = self.text(PAD, y + 14, f"Also extracting, not needed here: {spare}.",
                             color=SECONDARY, wrap=width - 2 * PAD)
            y = self.canvas.bbox(item)[3] + 8
        if tuning.factories:
            y = self._draw_factories(y + 10, tuning)
        return y

    def _draw_adjustments(self, y: int, width: int, tuning: Tuning) -> int:
        self.legend(y, [(SERIES_BLUE, "Rate from today's split"),
                        ("tick", "Needed after rebalancing")])
        y += 18
        x0, x1 = PAD + 256, max(PAD + 356, width - 420)
        peak = max(max(r.per_hour, r.need_per_hour) for r in tuning.resources)
        ticks = nice_ticks(peak)
        span = (x1 - x0) / ticks[-1]
        top = y
        for row in tuning.resources:
            cy = y + ROW / 2
            self.named_row(cy, row.name, f"{row.planets} → {row.proposed}")
            self.bar(x0, cy - BAR / 2, x0 + row.per_hour * span, cy + BAR / 2, SERIES_BLUE)
            tick = x0 + row.need_per_hour * span
            self.canvas.create_rectangle(tick - 1, cy - 8, tick + 1, cy + 8, fill=FG, outline="")
            source = "per planet" if row.measured else "per planet, assumed"
            self.text(x1 + 12, cy, f"{row.per_planet:,.0f}/hr {source}", color=SECONDARY)
            self._draw_change(x1 + 190, cy, row)
            y += ROW
        self.axis(x0, x1, top, y, ticks, format_compact)
        return y + 18

    def _draw_change(self, x: float, y: float, row) -> None:
        if row.change > 0:
            self.status(x, y, UNDER_SUPPLIED, f"Add {_plural(row.change, 'planet')}")
        elif row.change < 0:
            self.status(x, y, SURPLUS, f"Remove {_plural(-row.change, 'planet')}")
        else:
            self.status(x, y, BALANCED, "Keep")

    def _draw_factories(self, y: int, tuning: Tuning) -> int:
        y = self.section(y, "Factories needed",
                         "For the rebalanced output. Other products may share yours")
        for row in tuning.factories:
            cy = y + ROW / 2
            self.named_row(cy, row.name, f"{row.have} of {row.need}")
            self.text(PAD + 256, cy, f"Need {row.need_per_hour:,.0f}/hr, "
                                     f"yours make up to {row.capacity_per_hour:,.0f}/hr",
                      color=SECONDARY)
            if row.have < row.need:
                self.status(PAD + 560, cy, UNDER_SUPPLIED, f"Build {row.need - row.have}")
            else:
                self.status(PAD + 560, cy, BALANCED, "Enough")
            y += ROW
        return y

    def _draw_history(self, y: int, width: int) -> int:
        curve = self.curve
        if curve is None:
            return y
        y = self.section(y, "Recorded programs",
                         f"Predicted units per hour, {format_compact(curve.total)} total from "
                         f"{_plural(curve.programs, 'program')}")
        span = (curve.end - curve.start) / max(len(curve.per_hour), 1)
        whole = curve.end - curve.start
        labels = [(curve.start + whole * (i / 4)).strftime("%d %b %Y") for i in range(5)]
        y = self._line_chart(
            y, width, curve.per_hour, labels,
            lambda i: f"{(curve.start + span * i).strftime('%d %b %Y')}: "
                      f"{curve.per_hour[i]:,.0f}/hr")
        item = self.text(PAD, y + 6, "ESI only shows each extractor's latest program, so this "
                                     "fills in as the app reads your characters over time. "
                                     "Refresh before replacing a program to keep it.",
                         color=SECONDARY, wrap=width - 2 * PAD)
        return self.canvas.bbox(item)[3]

    # ------------------------------------------------------------------ charts
    def _line_chart(self, y: int, width: int, values: Sequence[float],
                    x_labels: Sequence[str], label_of: Callable[[int], str]) -> int:
        """One series on one axis, with a recessive grid and a hover readout."""
        x0, x1 = PAD + 46, width - PAD - 8
        top, bottom = y + 4, y + 4 + _PLOT_HEIGHT
        ticks = nice_ticks(max(values, default=0))
        peak = ticks[-1] or 1
        for tick in ticks:
            at = bottom - tick / peak * _PLOT_HEIGHT
            self.canvas.create_line(x0, at, x1, at, fill=BASELINE if tick == 0 else GRID)
            self.text(x0 - 6, at, format_compact(tick), color=MUTED, font=FONT_SMALL, anchor="e")
        for index, label in enumerate(x_labels):
            at = x0 + (x1 - x0) * index / max(len(x_labels) - 1, 1)
            anchor = "w" if index == 0 else "e" if index == len(x_labels) - 1 else "center"
            self.text(at, bottom + 11, label, color=MUTED, font=FONT_SMALL, anchor=anchor)
        points = self._points(x0, x1, top, bottom, values, peak)
        if len(points) >= 4:
            self.canvas.create_line(*points, fill=SERIES_BLUE, width=2)
        self._charts.append(_Chart(x0, x1, top, bottom, peak, values, label_of))
        return bottom + 24

    @staticmethod
    def _points(x0, x1, top, bottom, values, peak) -> list[float]:
        last = max(len(values) - 1, 1)
        points: list[float] = []
        for index, value in enumerate(values):
            points += [x0 + (x1 - x0) * index / last,
                       bottom - value / peak * (bottom - top)]
        return points

    def _sparkline(self, x0, x1, top, bottom, values: Sequence[float]) -> None:
        peak = max(values, default=0) or 1
        points = self._points(x0, x1, top, bottom, values, peak)
        if len(points) >= 4:
            self.canvas.create_line(*points, fill=SERIES_BLUE, width=2)

    def _on_hover(self, event) -> None:
        canvas = self.canvas
        canvas.delete(_HOVER)
        x, y = canvas.canvasx(event.x), canvas.canvasy(event.y)
        for chart in self._charts:
            if chart.x0 <= x <= chart.x1 and chart.top <= y <= chart.bottom and chart.values:
                self._draw_hover(chart, x)
                return

    def _draw_hover(self, chart: _Chart, x: float) -> None:
        canvas = self.canvas
        last = max(len(chart.values) - 1, 1)
        index = round((x - chart.x0) / (chart.x1 - chart.x0) * last)
        at = chart.x0 + (chart.x1 - chart.x0) * index / last
        canvas.create_line(at, chart.top, at, chart.bottom, fill=SECONDARY, tags=_HOVER)
        anchor = "e" if at > (chart.x0 + chart.x1) / 2 else "w"
        offset = -10 if anchor == "e" else 10
        label = canvas.create_text(at + offset, chart.top + 12, text=chart.label_of(index),
                                   fill=FG, font=FONT_SMALL, anchor=anchor, tags=_HOVER)
        box = canvas.bbox(label)
        back = canvas.create_rectangle(box[0] - 6, box[1] - 3, box[2] + 6, box[3] + 3,
                                       fill=RAISED, outline="", tags=_HOVER)
        canvas.tag_lower(back, label)


class TrackingTab(ttk.Frame):
    """Controls and results. Call ``update_view`` when the characters change."""

    def __init__(self, master, icons: IconSet, get_db: Callable[[], PIDatabase | None],
                 get_characters: Callable[[], list[CharacterPI]],
                 get_plan_recipe: Callable[[], Recipe | None],
                 get_default_rate: Callable[[], float], get_rates: Callable[[], dict],
                 set_default_rate: Callable[[float], None], folder: str | None = None):
        super().__init__(master)
        self._get_db = get_db
        self._get_characters = get_characters
        self._get_plan_recipe = get_plan_recipe
        self._get_default_rate = get_default_rate
        self._get_rates = get_rates
        self._set_default_rate = set_default_rate
        self._path = history_path(folder or os.path.dirname(default_store_path()))
        self._records: dict[str, ProgramRecord] = load_history(self._path)
        self.window = tk.StringVar(value="7 days")
        self.target = tk.StringVar(value=LATEST_PLAN)

        bar = ttk.Frame(self, padding=(0, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Window").pack(side="left")
        window_box = ttk.Combobox(bar, textvariable=self.window, values=list(WINDOWS), width=10,
                                  state="readonly")
        window_box.pack(side="left", padx=(6, 14))
        ttk.Label(bar, text="Balance for").pack(side="left")
        self.target_box = ttk.Combobox(bar, textvariable=self.target, values=[LATEST_PLAN],
                                       width=30, state="readonly")
        self.target_box.pack(side="left", padx=(6, 14))
        self.rate_btn = ttk.Button(bar, text="Use my average rate", command=self._use_rate,
                                   state="disabled")
        self.rate_btn.pack(side="left")
        self.rate_note = ttk.Label(bar, text="")
        self.rate_note.pack(side="left", padx=(8, 0))

        self.panel = TrackingPanel(self, icons)
        self.panel.pack(fill="both", expand=True)
        window_box.bind("<<ComboboxSelected>>", lambda _e: self.update_view())
        self.target_box.bind("<<ComboboxSelected>>", lambda _e: self.update_view())

    def set_products(self, products: list[str]) -> None:
        self.target_box.config(values=[LATEST_PLAN, *products])

    def _use_rate(self) -> None:
        rate = average_planet_rate(self._get_characters())
        if rate:
            self._set_default_rate(rate)
            self.update_view()

    def _record(self, characters: list[CharacterPI]) -> None:
        if record_programs(self._records, characters):
            try:
                save_history(self._path, self._records)
            except OSError:
                pass     # the next read records them again, if still installed

    def _recipe(self) -> tuple[Recipe | None, str]:
        """The recipe to balance for, and a note for the section heading."""
        name = self.target.get()
        if name != LATEST_PLAN:
            db = self._get_db()
            return (recipe_demand(db, name) if db else None), ""
        recipe = self._get_plan_recipe()
        if recipe is None:
            return None, ("Run Find planets, optimise an order, or choose a product under "
                          "Balance for, to see which planets to add or remove.")
        return recipe, "From your latest plan"

    def update_view(self) -> None:
        characters = self._get_characters()
        self._record(characters)
        hours = WINDOWS.get(coerce(self.window.get(), ""), 168)
        db = self._get_db()
        rates = self._get_rates()
        refine = refine_rates(db, rates) if db and rates else {}
        estimate = estimate_window(characters, hours, refine)
        ids = {character.id for character in characters}
        curve = record_curve([r for r in self._records.values() if r.character_id in ids],
                             datetime.now(timezone.utc))
        recipe, note = self._recipe()
        tuning = None
        if recipe is not None and characters:
            tuning = tune(recipe, characters, self._get_default_rate(), rates)
        self.panel.show(estimate, curve, tuning, note,
                        next((k for k, v in WINDOWS.items() if v == hours), "7 days"))
        average = average_planet_rate(characters)
        self.rate_btn.config(state="normal" if average else "disabled")
        self.rate_note.config(text=f"{average:,.0f}/hr per planet, used by the Output "
                                   "calculator and Fulfil order" if average else "")
