"""Tests for the display logic behind the visual results tabs.
Run:  python -m tests.test_results_model
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.production import BalanceRow, ChainNode, Recipe
from eve_pi.results_model import (BALANCED, NO_PLANETS, SURPLUS, UNDER_SUPPLIED,
                                  balance_status, character_layout, format_compact,
                                  format_quantity, nice_ticks, recipe_columns)

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def _row(planets, index):
    return BalanceRow("X", planets, 1.0, 0.5, 0.5, index)


def test_status():
    print("balance status:")
    check(balance_status(_row(0, 0.0)) is NO_PLANETS, "no planets")
    check(balance_status(_row(2, 0.84)) is UNDER_SUPPLIED, "below 0.85 is under-supplied")
    check(balance_status(_row(2, 0.85)) is BALANCED, "0.85 is balanced")
    check(balance_status(_row(2, 1.15)) is BALANCED, "1.15 is balanced")
    check(balance_status(_row(2, 1.16)) is SURPLUS, "above 1.15 is surplus")


def test_characters():
    print("planets by character:")
    check(character_layout([], 0) == [], "no planets, no characters")
    layout = character_layout(["Lava"] * 5, 3)
    check(len(layout) == 2, "8 planets need 2 characters")
    check(all(len(c) == 6 for c in layout), "each character shows 6 slots")
    kinds = [s.kind for c in layout for s in c]
    check(kinds == ["extractor"] * 5 + ["factory"] * 3 + ["free"] * 4,
          "extractors, then factories, then free slots")
    check(layout[0][0].planet_type == "Lava", "extractor keeps its planet type")
    check(len(character_layout(["Ice"] * 6, 0)) == 1, "exactly 6 fit on one character")


def test_ticks():
    print("axis ticks:")
    check(nice_ticks(0) == [0.0, 1.0], "an empty scale still has an axis")
    check(nice_ticks(10000) == [0, 2500, 5000, 7500, 10000], "clean thousands")
    check(nice_ticks(0.34)[-1] >= 0.34, "top tick reaches the maximum")
    ticks = nice_ticks(13000)
    check(ticks[-1] >= 13000 and len(ticks) <= 6, "stays a small number of ticks")


def test_formats():
    print("number formats:")
    check(format_compact(999) == "999", "small values in full")
    check(format_compact(2500) == "2,500", "under 10,000 in full")
    check(format_compact(12300) == "12.3K", "thousands compact")
    check(format_compact(4_560_000) == "4.56M", "millions compact")
    check(format_compact(1_230_000_000) == "1.23B", "billions compact")
    check(format_compact(0) == "0", "zero")
    check(format_quantity(1200000) == "1,200,000", "large amounts never go scientific")
    check(format_quantity(0.75) == "0.75", "small amounts keep their decimals")


def test_recipe_columns():
    print("recipe columns:")
    recipe = Recipe("Widget", chain=[
        ChainNode("Widget", 1, 0, False), ChainNode("Water", 2, 1, False),
        ChainNode("Bacteria", 2, 1, False), ChainNode("Aqueous Liquids", 300, 2, True),
        ChainNode("Mystery", 1, 1, False)])
    columns = {"Aqueous Liquids": 1, "Water": 2, "Bacteria": 2, "Widget": 3}
    got = recipe_columns(recipe, columns.get)
    check([tier for tier, _ in got] == ["P0", "P1", "P2"], "raw materials come first")
    check([n.name for n in got[1][1]] == ["Bacteria", "Water"], "sorted within a tier")
    check(all(n.name != "Mystery" for _, nodes in got for n in nodes),
          "items not on the map are left out")


def main() -> int:
    test_status()
    test_characters()
    test_ticks()
    test_formats()
    test_recipe_columns()
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
