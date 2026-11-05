"""Visual panels for the Production estimate and Output calculator tabs.

Each panel redraws a scrollable canvas from the latest results. Every value is
printed beside its mark, so no reading depends on hovering or on colour alone.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import ttk

from .icon_set import FACTORY_ICON, IconSet
from .production import ORDER_PRODUCT
from .results_model import (BALANCED, LIMITING, NO_PLANETS, NO_SUPPLY, Status,
                            balance_status, character_layout, format_compact,
                            format_duration, format_quantity, nice_ticks,
                            recipe_columns)
from .theme import BORDER, FG, MUTED, PANEL, RAISED

SECONDARY = "#a3abb6"
GRID = "#232830"
BASELINE = "#3f4752"
SERIES_BLUE = "#3987e5"
SERIES_ORANGE = "#d95926"

FONT = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)
FONT_SECTION = ("Segoe UI Semibold", 11)
FONT_TILE = ("Segoe UI Semibold", 16)
FONT_HERO = ("Segoe UI Semibold", -48)

PAD = 18
ROW = 24
BAR = 10
ICON_PITCH = 36


class _Panel(ttk.Frame):
    """A scrolling canvas with the drawing helpers both tabs share."""

    def __init__(self, master, icons: IconSet):
        super().__init__(master)
        self.icons = icons
        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0)
        scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        if width <= 1:
            return
        height = self._draw(width)
        canvas.configure(scrollregion=(0, 0, width,
                                       max(height, canvas.winfo_height())))

    def _draw(self, width: int) -> int:
        raise NotImplementedError

    # ------------------------------------------------------------------ helpers
    def text(self, x, y, value, color=FG, font=FONT, anchor="w", wrap=0) -> int:
        return self.canvas.create_text(x, y, text=value, fill=color, font=font,
                                       anchor=anchor, width=wrap)

    def placeholder(self, width: int, message: str) -> int:
        self.text(width / 2, 60, message, color=MUTED, anchor="center",
                  wrap=width - 2 * PAD)
        return 120

    def section(self, y: int, title: str, note: str = "") -> int:
        item = self.text(PAD, y, title, font=FONT_SECTION)
        if note:
            right = self.canvas.bbox(item)[2]
            self.text(right + 12, y, note, color=SECONDARY)
        return y + 24

    def tiles(self, y: int, width: int,
              tiles: Sequence[tuple[str, str, str]]) -> int:
        """(label, value, note) cards in one row."""
        gap = 10
        card = (width - 2 * PAD - gap * (len(tiles) - 1)) / len(tiles)
        for i, (label, value, note) in enumerate(tiles):
            x = PAD + i * (card + gap)
            self.canvas.create_rectangle(x, y, x + card, y + 72, fill=RAISED,
                                         outline="")
            self.text(x + 12, y + 14, label, color=SECONDARY)
            self.text(x + 12, y + 37, value, font=FONT_TILE)
            self.text(x + 12, y + 60, note, color=SECONDARY, font=FONT_SMALL)
        return y + 72

    def status(self, x, y, status: Status, text: str | None = None) -> None:
        """Status is always a coloured glyph plus a label in normal ink."""
        glyph = self.text(x, y, status.glyph, color=status.color)
        self.text(self.canvas.bbox(glyph)[2] + 5, y, text or status.label)

    def named_row(self, y: int, name: str, count_text: str = "") -> None:
        image = self.icons.small(name)
        if image is not None:
            self.canvas.create_image(PAD + 8, y, image=image)
        self.text(PAD + 22, y, name)
        if count_text:
            self.text(PAD + 240, y, count_text, color=SECONDARY, anchor="e")

    def bar(self, x0, y0, x1, y1, fill, rounded=True) -> None:
        """A bar segment, square at its start with a 4px rounded data-end."""
        if x1 - x0 < 1:
            return
        radius = min(4, x1 - x0, (y1 - y0) / 2)
        if not rounded or radius < 2:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
            return
        # Doubled points stay sharp under smoothing; single ones curve.
        self.canvas.create_polygon(
            x0, y0, x0, y0, x1 - radius, y0, x1, y0, x1, y0 + radius,
            x1, y1 - radius, x1, y1, x1 - radius, y1, x0, y1, x0, y1,
            fill=fill, smooth=True, splinesteps=8)

    def axis(self, x0, x1, top, bottom, ticks: Sequence[float],
             label: Callable[[float], str]) -> None:
        """Recessive gridlines behind the bars, with labels underneath."""
        span = (x1 - x0) / (ticks[-1] or 1)
        for tick in ticks:
            x = x0 + tick * span
            line = self.canvas.create_line(x, top, x, bottom,
                                           fill=BASELINE if tick == 0 else GRID)
            self.canvas.tag_lower(line)
            self.text(x, bottom + 9, label(tick), color=MUTED, font=FONT_SMALL,
                      anchor="center")

    def legend(self, y: int, entries: Sequence[tuple[str, str]]) -> int:
        """(kind-or-colour, label) entries in one row. A kind of "tick" draws
        the marker used for the recipe's share."""
        x = PAD
        for mark, label in entries:
            if mark == "tick":
                self.canvas.create_rectangle(x + 4, y - 7, x + 6, y + 7, fill=FG,
                                             outline="")
            else:
                self.canvas.create_rectangle(x, y - 5, x + 10, y + 5, fill=mark,
                                             outline="")
            item = self.text(x + 16, y, label, color=SECONDARY)
            x = self.canvas.bbox(item)[2] + 22
        return y + 18


class ProductionView(_Panel):
    """Stat tiles, balance against the recipe, planets by character, materials."""

    def __init__(self, master, icons: IconSet):
        super().__init__(master, icons)
        self._data = None

    def show(self, report, breakdown, extractor_types: Sequence[str],
             column_of: Callable[[str], int | None]) -> None:
        self._data = (report, breakdown, list(extractor_types), column_of)
        self.redraw()

    def clear(self) -> None:
        self._data = None
        self.redraw()

    def _draw(self, width: int) -> int:
        if self._data is None:
            return self.placeholder(
                width, "Run Find planets or optimise an order to see your balance "
                       "and planet layout.")
        report, build, extractors, column_of = self._data

        y = self._draw_tiles(14, width, report, build)
        y = self._draw_headline(y + 22, report, build, width)
        y = self._draw_balance(y + 12, width, report)
        y = self._draw_characters(y + 18, width, extractors, build)
        if report.recipe and report.recipe.demand:
            y = self._draw_recipe(y + 18, width, report.recipe, column_of)
        return y + 20

    def _draw_tiles(self, y, width, report, build) -> int:
        if build is None:
            return self.tiles(y, width, [
                ("Extractor planets", str(report.planets_used), "Selected"),
                ("Characters", str(report.characters_needed), "6 planets each"),
            ])
        tiers = ", ".join(f"P{k} ×{build.advanced_by_tier[k]}"
                          for k in sorted(build.advanced_by_tier)) or "None needed"
        return self.tiles(y, width, [
            ("Extractor planets", str(build.extractor_planets), "Selected"),
            ("Factory planets", str(build.factory_planets),
             f"{build.adv_per_factory} facilities each"),
            ("Total planets", str(build.total_planets), "Extractor + factory"),
            ("Characters", str(build.characters), "6 planets each"),
            ("Basic facilities", str(build.basic_factories_needed),
             f"Capacity {build.basic_capacity}"),
            ("Advanced facilities", str(build.advanced_facilities), tiers),
        ])

    def _draw_headline(self, y, report, build, width) -> int:
        missing = [r.resource for r in report.rows if r.planets == 0]
        lines: list[tuple[Status, str]] = []
        if missing:
            lines.append((NO_PLANETS, "No planet selected for "
                          + ", ".join(missing)
                          + ", so finished output is zero until you tick one for each."))
        elif report.bottleneck and report.efficiency < 0.999:
            lines.append((LIMITING, f"Your mix is limited by {report.bottleneck}, at "
                          f"about {report.efficiency * 100:.0f}% of a balanced mix."))
        else:
            lines.append((BALANCED, "Your selection is well balanced to the recipe."))
        if build is not None and build.basic_over_capacity:
            lines.append((NO_SUPPLY, f"{build.basic_factories_needed} basic facilities "
                          f"needed, but your extractors hold {build.basic_capacity}. "
                          "Add extractor planets."))
        for status, message in lines:
            self.status(PAD, y, status, message)
            y += 20
        return y

    def _draw_balance(self, y, width, report) -> int:
        has_recipe = bool(report.recipe and report.recipe.demand)
        y = self.section(y, "Balance against the recipe" if has_recipe
                         else "Spread across your selection")
        y = self.legend(y, [(SERIES_BLUE, "Share of your planets"),
                            ("tick", "Share the recipe needs" if has_recipe
                             else "Even share")])
        rows = report.rows
        x0, x1 = PAD + 256, max(PAD + 356, width - 360)
        top = y
        scale_max = max([r.capacity_share for r in rows]
                        + [r.demand_share for r in rows] + [0.01])
        ticks = nice_ticks(scale_max)
        span = (x1 - x0) / ticks[-1]
        for row in rows:
            cy = y + ROW / 2
            planets = "1 planet" if row.planets == 1 else f"{row.planets} planets"
            self.named_row(cy, row.resource, planets)
            self.bar(x0, cy - BAR / 2, x0 + row.capacity_share * span, cy + BAR / 2,
                     SERIES_BLUE)
            mark = x0 + row.demand_share * span
            self.canvas.create_rectangle(mark - 1, cy - 8, mark + 1, cy + 8, fill=FG,
                                         outline="")
            self.text(x1 + 12, cy, f"{row.capacity_share * 100:.0f}% vs "
                                   f"{row.demand_share * 100:.0f}%", color=SECONDARY)
            self.status(x1 + 96, cy, balance_status(row))
            y += ROW
        self.axis(x0, x1, top, y, ticks, lambda t: f"{t * 100:.0f}%")
        return y + 18

    def _draw_characters(self, y, width, extractors, build) -> int:
        factories = build.factory_planets if build else 0
        characters = character_layout(extractors, factories)
        y = self.section(y, "Planets by character",
                         "Extractors first, then factories")
        y = self.legend(y, [(SERIES_BLUE, "Extractor planet (icon shows its type)"),
                            (SERIES_ORANGE, "Factory planet"),
                            (BORDER, "Free slot")])
        if not characters:
            self.text(PAD, y + 8, "No planets selected yet.", color=SECONDARY)
            return y + 20

        card = 6 * ICON_PITCH + 16
        gap = 12
        per_row = max(1, (width - 2 * PAD + gap) // (card + gap))
        card_height = 72
        for index, slots in enumerate(characters):
            row, col = divmod(index, per_row)
            x = PAD + col * (card + gap)
            top = y + row * (card_height + gap)
            self.canvas.create_rectangle(x, top, x + card, top + card_height,
                                         fill=RAISED, outline="")
            self.text(x + 8, top + 13, f"Character {index + 1}", color=SECONDARY)
            for s, slot in enumerate(slots):
                self._draw_slot(x + 8 + s * ICON_PITCH, top + 26, slot)
        rows = (len(characters) + per_row - 1) // per_row
        return y + rows * (card_height + gap)

    def _draw_slot(self, x, y, slot) -> None:
        size = ICON_PITCH - 4
        if slot.kind == "free":
            self.canvas.create_rectangle(x, y, x + size, y + size + 5,
                                         outline=BORDER, fill="")
            return
        name = slot.planet_type if slot.kind == "extractor" else FACTORY_ICON
        color = SERIES_BLUE if slot.kind == "extractor" else SERIES_ORANGE
        image = self.icons.large(name)
        if image is not None:
            self.canvas.create_image(x + size / 2, y + size / 2, image=image)
        else:
            label = (slot.planet_type or "?")[0] if slot.kind == "extractor" else "F"
            self.text(x + size / 2, y + size / 2, label, anchor="center")
        self.canvas.create_rectangle(x, y + size + 2, x + size, y + size + 5,
                                     fill=color, outline="")

    def _draw_recipe(self, y, width, recipe, column_of) -> int:
        y = self.section(y, "Materials for the whole order"
                         if recipe.product == ORDER_PRODUCT
                         else f"Materials per 1 {recipe.product}")
        columns = recipe_columns(recipe, column_of)
        if not columns:
            return y
        column_width = (width - 2 * PAD) / len(columns)
        bottom = y
        for c, (tier, nodes) in enumerate(columns):
            x = PAD + c * column_width
            self.text(x, y + 6, tier, color=SECONDARY)
            cy = y + 28
            for node in nodes:
                image = self.icons.small(node.name)
                if image is not None:
                    self.canvas.create_image(x + 8, cy, image=image)
                self.text(x + 22, cy, f"{format_quantity(node.qty_per_unit)}  {node.name}")
                cy += 22
            bottom = max(bottom, cy)
        return bottom


class OutputView(_Panel):
    """Headline output, raw material use, intermediates and Jita values."""

    def __init__(self, master, icons: IconSet):
        super().__init__(master, icons)
        self._data = None

    def show(self, result, used: int, rate: float, valuation, prices,
             priced_names: Sequence[str]) -> None:
        self._data = (result, used, rate, valuation, prices, list(priced_names))
        self.redraw()

    def clear(self) -> None:
        self._data = None
        self.redraw()

    def _draw(self, width: int) -> int:
        if self._data is None:
            return self.placeholder(
                width, "Run Find planets or optimise an order to estimate your "
                       "finished output.")
        result, used, rate, valuation, prices, _ = self._data

        y = self._draw_headline(20, width, result, valuation)
        planets = "1 planet" if used == 1 else f"{used} planets"
        self.text(PAD, y, f"{planets} selected at a default {rate:,.0f} P0/hr each. "
                          "Right-click a planet in Select planets to set its real rate.",
                  color=SECONDARY)
        y = self._draw_materials(y + 28, width, result)
        if result.intermediates and result.product_per_hour > 0:
            y = self._draw_intermediates(y + 18, width, result)
        y = self._draw_market(y + 18, width, valuation, prices)
        return y + 20

    def _draw_order_headline(self, y, width, result, valuation) -> int:
        hero = self.text(PAD, y + 26, format_duration(1 / result.product_per_hour),
                         font=FONT_HERO)
        right = self.canvas.bbox(hero)[2] + 14
        self.text(right, y + 12, "to extract the order's raw materials", font=FONT_SECTION)
        self.text(right, y + 34, "Factory time is on the Fulfil order tab.",
                  color=SECONDARY)
        if result.limiting:
            self.status(right, y + 54, LIMITING, f"Limited by {result.limiting}")
        if valuation is not None:
            self._draw_value_tiles(y, width, valuation)
        return y + 86

    def _draw_headline(self, y, width, result, valuation) -> int:
        if result.product == ORDER_PRODUCT and result.product_per_hour > 0:
            return self._draw_order_headline(y, width, result, valuation)
        if result.product and result.product_per_hour > 0:
            image = self.icons.large(result.product)
            x = PAD
            if image is not None:
                self.canvas.create_image(x + 16, y + 26, image=image)
                x += 44
            hero = self.text(x, y + 26, f"{result.product_per_hour:,.1f}",
                             font=FONT_HERO)
            right = self.canvas.bbox(hero)[2] + 14
            self.text(right, y + 12, f"{result.product} per hour", font=FONT_SECTION)
            self.text(right, y + 34, f"{result.product_per_day:,.0f} per day",
                      color=SECONDARY)
            if result.limiting:
                self.status(right, y + 54, LIMITING, f"Limited by {result.limiting}")
            if valuation is not None:
                self._draw_value_tiles(y, width, valuation)
            return y + 86
        if result.product:
            self.status(PAD, y + 4, NO_SUPPLY,
                        f"No {result.product} yet: tick a planet for every "
                        "required resource.")
        else:
            self.text(PAD, y + 4, "Raw extraction only. Choose a Final product to "
                                  "estimate finished output.", color=SECONDARY)
        return y + 30

    def _draw_value_tiles(self, y, width, valuation) -> None:
        card = 170
        for i, (label, value) in enumerate((
                ("ISK per hour", valuation.total_isk_hour),
                ("ISK per day", valuation.total_isk_day))):
            x = width - PAD - (2 - i) * card - (1 - i) * 10
            self.canvas.create_rectangle(x, y, x + card, y + 72, fill=RAISED, outline="")
            self.text(x + 12, y + 14, label, color=SECONDARY)
            self.text(x + 12, y + 37, format_compact(value), font=FONT_TILE)
            self.text(x + 12, y + 60, f"At {valuation.basis} price, incl. surplus",
                      color=SECONDARY, font=FONT_SMALL)

    def _draw_materials(self, y, width, result) -> int:
        has_product = bool(result.product)
        y = self.section(y, "Raw materials", "Units per hour")
        if has_product:
            y = self.legend(y, [(SERIES_BLUE, "Used"), (SERIES_ORANGE, "Spare")])
        rows = result.rows
        x0, x1 = PAD + 256, max(PAD + 356, width - 360)
        top = y
        ticks = nice_ticks(max([r.supply for r in rows] + [1.0]))
        span = (x1 - x0) / ticks[-1]
        for row in rows:
            cy = y + ROW / 2
            planets = "1 planet" if row.planets == 1 else f"{row.planets} planets"
            self.named_row(cy, row.resource, planets)
            label_x = x1 + 12
            if has_product:
                used_end = x0 + row.consumed * span
                spare_end = x0 + row.supply * span
                has_spare = spare_end - used_end > 3
                self.bar(x0, cy - BAR / 2, used_end, cy + BAR / 2, SERIES_BLUE,
                         rounded=not has_spare)
                if has_spare:
                    self.bar(used_end + 2 if row.consumed > 0 else x0, cy - BAR / 2,
                             spare_end, cy + BAR / 2, SERIES_ORANGE)
                if row.supply <= 0:
                    self.status(label_x, cy, NO_SUPPLY)
                elif row.resource == result.limiting:
                    self.status(label_x, cy, LIMITING,
                                f"{row.supply:,.0f} used, limiting output")
                else:
                    self.text(label_x, cy, f"{row.supply:,.0f} supplied, "
                                           f"{row.excess:,.0f} spare "
                                           f"({row.excess_pct:.0f}%)", color=SECONDARY)
            else:
                self.bar(x0, cy - BAR / 2, x0 + row.supply * span, cy + BAR / 2,
                         SERIES_BLUE)
                self.text(label_x, cy, f"{row.supply:,.0f} extracted", color=SECONDARY)
            y += ROW
        self.axis(x0, x1, top, y, ticks, format_compact)
        return y + 18

    def _draw_intermediates(self, y, width, result) -> int:
        y = self.section(y, "Throughput at max output", "Units per hour")
        per_row = max(1, (width - 2 * PAD) // 300)
        column_width = (width - 2 * PAD) / per_row
        for i, (name, amount) in enumerate(result.intermediates):
            row, col = divmod(i, per_row)
            x = PAD + col * column_width
            cy = y + row * 22 + 10
            image = self.icons.small(name)
            if image is not None:
                self.canvas.create_image(x + 8, cy, image=image)
            item = self.text(x + 22, cy, name)
            self.text(self.canvas.bbox(item)[2] + 8, cy, f"{amount:,.1f}/hr",
                      color=SECONDARY)
        rows = (len(result.intermediates) + per_row - 1) // per_row
        return y + rows * 22

    def _draw_market(self, y, width, valuation, prices) -> int:
        y = self.section(y, "Jita market", "Jita 4-4 prices, daily volume in The Forge")
        if valuation is None:
            self.text(PAD, y + 6, "Click Fetch Jita prices for live ISK values.",
                      color=SECONDARY)
            return y + 20

        columns = (("Buy", PAD + 340), ("Sell", PAD + 440), ("Split", PAD + 540),
                   ("Daily vol", PAD + 630), ("ISK/hr", PAD + 730))
        self.text(PAD + 22, y + 6, "Item", color=SECONDARY)
        self.text(PAD + 240, y + 6, "Type", color=SECONDARY, anchor="e")
        for label, x in columns:
            self.text(x, y + 6, label, color=SECONDARY, anchor="e")
        y += 16
        self.canvas.create_line(PAD, y, width - PAD, y, fill=GRID)

        bar_x0 = PAD + 744
        bar_x1 = max(bar_x0 + 40, width - PAD)
        top_value = max([r.isk_per_hour or 0 for r in valuation.rows] + [1.0])
        for row in valuation.rows:
            cy = y + ROW / 2
            self.named_row(cy, row.name, row.kind.capitalize())
            mp = prices.get(row.name)
            values = [mp.buy, mp.sell, mp.split, mp.volume] if mp else [None] * 4
            for (_, x), value, decimals in zip(columns, values, (2, 2, 2, 0)):
                text = "n/a" if value is None else f"{value:,.{decimals}f}"
                self.text(x, cy, text, anchor="e")
            isk = row.isk_per_hour
            self.text(columns[-1][1], cy, "n/a" if isk is None else format_compact(isk),
                      anchor="e")
            if isk:
                self.bar(bar_x0, cy - BAR / 2,
                         bar_x0 + (bar_x1 - bar_x0) * isk / top_value, cy + BAR / 2,
                         SERIES_BLUE)
            y += ROW
        return y
