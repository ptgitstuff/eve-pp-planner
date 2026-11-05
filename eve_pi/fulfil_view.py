"""The visual results panel for the Fulfil order tab."""

from __future__ import annotations

from .fulfil import FACTORIES, FulfilPlan
from .icon_set import FACTORY_ICON, IconSet
from .results_model import (BALANCED, LIMITING, NO_PLANETS, SURPLUS,
                            format_compact, format_duration, nice_ticks)
from .results_view import (BAR, GRID, PAD, ROW, SECONDARY, SERIES_BLUE,
                           SERIES_ORANGE, _Panel)
from .theme import MUTED, RAISED

MUTED_BAR = "#4a5360"
CARD_ROW = 22


class FulfilView(_Panel):
    def __init__(self, master, icons: IconSet):
        super().__init__(master, icons)
        self._plan: FulfilPlan | None = None
        self._source = ""

    def show(self, plan: FulfilPlan, source: str) -> None:
        self._plan = plan
        self._source = source
        self.redraw()

    def clear(self) -> None:
        self._plan = None
        self.redraw()

    def _draw(self, width: int) -> int:
        plan = self._plan
        if plan is None:
            return self.placeholder(
                width, "Paste a Janice link or an item list above, then click "
                       "Optimise. It uses the staging system, range and security "
                       "bands from the Plan box, and the P0 rate from the "
                       "Output calculator.")
        alloc = plan.allocation
        extractors = sum(alloc.extractors.values())
        used = len(plan.by_character)
        y = self.tiles(14, width, [
            ("Time to fulfil", format_duration(alloc.hours) if alloc.feasible else "n/a",
             f"At {plan.rate:,.0f} P0/hr per planet"),
            ("Extractor planets", str(extractors), "Raw materials"),
            ("Factory planets", str(alloc.factories), "P2 and above"),
            ("Characters used", f"{used} of {plan.characters}",
             f"{alloc.slots} free slots" if plan.roster is not None
             else f"{plan.per_character} planets each"),
            ("Systems", str(plan.systems_used),
             f"Of {plan.systems_in_range} in range"),
        ])
        y = self._draw_notes(y + 22, plan)
        y = self._draw_balance(y + 12, width, plan)
        y = self._draw_scenarios(y + 18, width, plan)
        y = self._draw_characters(y + 18, width, plan)
        y = self._draw_order(y + 18, width, plan)
        return y + 20

    def _draw_notes(self, y, plan) -> int:
        alloc = plan.allocation
        notes = []
        if not plan.bill.items:
            notes.append((NO_PLANETS, "Nothing in this order is a PI product."))
        elif not alloc.feasible:
            if alloc.limited_by_planets:
                notes.append((NO_PLANETS, "The planets in range can't supply every "
                              "resource. Widen Max jumps or allow more security bands."))
            elif plan.roster is not None:
                notes.append((NO_PLANETS, f"This order needs at least {alloc.min_slots} "
                              f"planets, but your characters have {alloc.slots} free "
                              "slots. Free up colonies or add a character."))
            else:
                notes.append((NO_PLANETS, f"This order needs at least {alloc.min_slots} "
                              f"planets, but you have {alloc.slots} slots. Add characters."))
        else:
            slowest = ("factory capacity" if alloc.bottleneck == FACTORIES
                       else alloc.bottleneck)
            notes.append((BALANCED if not alloc.limited_by_planets else LIMITING,
                          f"Slots are balanced so everything finishes together. "
                          f"Slowest: {slowest}."))
        if alloc.feasible and alloc.free_slots:
            notes.append((LIMITING, f"{alloc.free_slots} slot(s) left free: no suitable "
                          "planet in range would speed this up. Widen Max jumps to use them."))
        if plan.unassigned:
            notes.append((NO_PLANETS, f"{len(plan.unassigned)} planet(s) have no character: "
                          "everyone with free slots already has a colony there ("
                          + ", ".join(p.planet.name for p in plan.unassigned[:4]) + ")."))
        if plan.bill.not_pi:
            notes.append((SURPLUS, "Not PI products, so left out: "
                          + ", ".join(plan.bill.not_pi)))
        if self._source:
            self.text(PAD, y, self._source, color=SECONDARY)
            y += 20
        for status, message in notes:
            self.status(PAD, y, status, message)
            y += 20
        return y

    def _draw_balance(self, y, width, plan) -> int:
        alloc = plan.allocation
        y = self.section(y, "Finish time by material", "Each bar is that material alone")
        if not alloc.feasible:
            for name, units in sorted(plan.bill.raw_units.items()):
                cy = y + ROW / 2
                self.named_row(cy, name)
                self.text(PAD + 256, cy, f"{format_compact(units)} units needed",
                          color=SECONDARY)
                y += ROW
            return y

        rows = [(name, alloc.extractors[name], hours)
                for name, hours in sorted(alloc.resource_hours.items(),
                                          key=lambda kv: -kv[1])]
        x0, x1 = PAD + 256, max(PAD + 356, width - 300)
        ticks = nice_ticks(max(alloc.hours / 24, 0.001))
        span = (x1 - x0) / ticks[-1]
        top = y
        for name, count, hours in rows:
            cy = y + ROW / 2
            self.named_row(cy, name, "1 planet" if count == 1 else f"{count} planets")
            self.bar(x0, cy - BAR / 2, x0 + hours / 24 * span, cy + BAR / 2, SERIES_BLUE)
            self.text(x1 + 12, cy, f"{format_compact(plan.bill.raw_units[name])} units, "
                                   f"{format_duration(hours)}", color=SECONDARY)
            y += ROW
        if alloc.factories:
            cy = y + ROW / 2
            image = self.icons.small(FACTORY_ICON)
            if image is not None:
                self.canvas.create_image(PAD + 8, cy, image=image)
            self.text(PAD + 22, cy, "Factory capacity")
            count = alloc.factories
            self.text(PAD + 240, cy, "1 planet" if count == 1 else f"{count} planets",
                      color=SECONDARY, anchor="e")
            self.bar(x0, cy - BAR / 2, x0 + alloc.factory_hours / 24 * span, cy + BAR / 2,
                     SERIES_ORANGE)
            self.text(x1 + 12, cy, f"{format_compact(plan.bill.total_advanced_hours)} "
                                   f"facility-hours, {format_duration(alloc.factory_hours)}",
                      color=SECONDARY)
            y += ROW
        self.axis(x0, x1, top, y, ticks, lambda t: f"{t:,.0f}d" if t >= 1 or t == 0
                  else f"{t:.2g}d")
        return y + 18

    def _draw_scenarios(self, y, width, plan) -> int:
        note = (f"Most free slots first, then new characters with {plan.per_character} "
                "planets each" if plan.roster is not None
                else f"{plan.per_character} planets each, same range and rate")
        y = self.section(y, "Time by number of characters", note)
        rows = [s for s in plan.scenarios if s.feasible]
        if not rows:
            self.text(PAD, y + 6, "No character count up to "
                      f"{plan.scenarios[-1].characters if plan.scenarios else 0} "
                      "can make this order.", color=SECONDARY)
            return y + 20
        x0, x1 = PAD + 110, max(PAD + 210, width - 300)
        ticks = nice_ticks(max(s.hours for s in rows) / 24)
        span = (x1 - x0) / ticks[-1]
        top = y
        for s in rows:
            cy = y + ROW / 2
            label = "1 character" if s.characters == 1 else f"{s.characters} characters"
            self.text(PAD, cy, label)
            current = s.characters == plan.characters
            self.bar(x0, cy - BAR / 2, x0 + s.hours / 24 * span, cy + BAR / 2,
                     SERIES_BLUE if current else MUTED_BAR)
            suffix = ""
            if current:
                suffix = "  (your characters)" if plan.roster is not None else "  (your setting)"
            self.text(x1 + 12, cy, format_duration(s.hours) + suffix, color=SECONDARY)
            y += ROW
        self.axis(x0, x1, top, y, ticks, lambda t: f"{t:,.0f}d" if t >= 1 or t == 0
                  else f"{t:.2g}d")
        return y + 18

    def _draw_characters(self, y, width, plan) -> int:
        y = self.section(y, "Planets by character")
        if not plan.by_character:
            self.text(PAD, y + 6, "No planets placed yet.", color=SECONDARY)
            return y + 20
        gap = 12
        per_row = max(1, (width - 2 * PAD + gap) // (340 + gap))
        card = (width - 2 * PAD - gap * (per_row - 1)) / per_row
        tallest = max(len(c) for c in plan.by_character)
        card_height = 30 + tallest * CARD_ROW + 6
        for index, placements in enumerate(plan.by_character):
            row, col = divmod(index, per_row)
            x = PAD + col * (card + gap)
            top = y + row * (card_height + gap)
            self.canvas.create_rectangle(x, top, x + card, top + card_height,
                                         fill=RAISED, outline="")
            self.text(x + 10, top + 15, plan.character_names[index], color=SECONDARY)
            for r, placement in enumerate(placements):
                cy = top + 30 + r * CARD_ROW + CARD_ROW / 2
                image = self.icons.small(placement.planet.planet_type)
                if image is not None:
                    self.canvas.create_image(x + 18, cy, image=image)
                self.text(x + 32, cy, placement.planet.name)
                job = placement.resource or "Factory planet"
                self.text(x + card - 10, cy, job, anchor="e",
                          color=SECONDARY if placement.resource else MUTED)
        rows = (len(plan.by_character) + per_row - 1) // per_row
        return y + rows * (card_height + gap)

    def _draw_order(self, y, width, plan) -> int:
        y = self.section(y, "Your order")
        per_row = max(1, (width - 2 * PAD) // 300)
        column = (width - 2 * PAD) / per_row
        items = sorted(plan.bill.items.items())
        for i, (name, amount) in enumerate(items):
            row, col = divmod(i, per_row)
            x = PAD + col * column
            cy = y + row * 22 + 10
            image = self.icons.small(name)
            if image is not None:
                self.canvas.create_image(x + 8, cy, image=image)
            item = self.text(x + 22, cy, name)
            self.text(self.canvas.bbox(item)[2] + 8, cy, f"{amount:,}", color=SECONDARY)
        rows = (len(items) + per_row - 1) // per_row
        return y + rows * 22
