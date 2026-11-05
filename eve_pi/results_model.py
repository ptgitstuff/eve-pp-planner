"""Display logic for the visual results tabs: statuses, the planets-per-character
layout, axis ticks and number formats. There is no Tkinter here, so all of it is
testable without a display; ``results_view.py`` draws it."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .production import PLANETS_PER_CHARACTER, BalanceRow, ChainNode, Recipe

STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"


@dataclass(frozen=True)
class Status:
    label: str
    glyph: str
    color: str


BALANCED = Status("Balanced", "✓", STATUS_GOOD)
SURPLUS = Status("Surplus", "▲", STATUS_WARNING)
UNDER_SUPPLIED = Status("Under-supplied", "▼", STATUS_SERIOUS)
NO_PLANETS = Status("No planets", "✕", STATUS_CRITICAL)
LIMITING = Status("Limiting output", "▼", STATUS_SERIOUS)
NO_SUPPLY = Status("No supply", "✕", STATUS_CRITICAL)


def balance_status(row: BalanceRow) -> Status:
    if row.planets == 0:
        return NO_PLANETS
    if row.index < 0.85:
        return UNDER_SUPPLIED
    if row.index > 1.15:
        return SURPLUS
    return BALANCED


@dataclass(frozen=True)
class Slot:
    kind: str                     # "extractor", "factory" or "free"
    planet_type: str | None = None


def character_layout(extractor_types: Sequence[str], factory_count: int,
                     per_character: int = PLANETS_PER_CHARACTER
                     ) -> list[list[Slot]]:
    """Split planets across characters, extractors first, padding the last
    character with free slots."""
    slots = [Slot("extractor", t) for t in extractor_types]
    slots += [Slot("factory")] * max(factory_count, 0)
    if not slots:
        return []
    while len(slots) % per_character:
        slots.append(Slot("free"))
    return [slots[i:i + per_character]
            for i in range(0, len(slots), per_character)]


def nice_ticks(maximum: float, count: int = 4) -> list[float]:
    """Round axis ticks from 0 to at least ``maximum``."""
    if maximum <= 0:
        return [0.0, 1.0]
    rough = maximum / count
    magnitude = 10 ** math.floor(math.log10(rough))
    step = next((m * magnitude for m in (1, 2, 2.5, 5, 10)
                 if m * magnitude >= rough))
    ticks = [0.0]
    while ticks[-1] < maximum - 1e-9:
        ticks.append(ticks[-1] + step)
    return ticks


def format_compact(value: float) -> str:
    """1,234 / 12.3K / 4.56M / 1.23B."""
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e4, "K")):
        if abs(value) >= limit:
            scaled = value / (limit if suffix != "K" else 1e3)
            return f"{scaled:,.3g}{suffix}" if scaled < 1000 else f"{scaled:,.0f}{suffix}"
    return f"{value:,.0f}" if abs(value) >= 100 or value == int(value) else f"{value:,.1f}"


def format_duration(hours: float) -> str:
    """30 min / 5.0 hours / 2.0 days / 400 days."""
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} hours"
    days = hours / 24
    return f"{days:.1f} days" if days < 100 else f"{days:,.0f} days"


def format_quantity(value: float) -> str:
    """Recipe amounts, without ever falling back to scientific notation."""
    return f"{value:,.0f}" if value >= 100 else f"{value:.3g}"


TIER_ORDER = ("P0", "P1", "P2", "P3", "P4")


def recipe_columns(recipe: Recipe, column_of: Callable[[str], int | None]
                   ) -> list[tuple[str, list[ChainNode]]]:
    """Group a recipe's materials by tier, raw materials first. ``column_of``
    gives a name's chain-map column (P0 is column 1, P4 is column 5)."""
    by_tier: dict[str, list[ChainNode]] = {}
    for node in recipe.chain:
        column = column_of(node.name)
        if column is not None and 1 <= column <= 5:
            by_tier.setdefault(TIER_ORDER[column - 1], []).append(node)
    return [(tier, sorted(by_tier[tier], key=lambda n: n.name))
            for tier in TIER_ORDER if tier in by_tier]
