"""Tests for per-planet picks calculation and selection persistence.
Run:  python -m tests.test_selection
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.production import (PlanetPick, Recipe, calculate_from_picks,
                              load_selection, save_selection)

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def test_picks():
    print("calculate_from_picks:")
    recipe = Recipe(product="Test Widget",
                    demand={"Aqueous Liquids": 300.0, "Felsic Magma": 300.0},
                    chain=[])
    picks = [
        PlanetPick(1, "A I", "Alpha", "Gas", "Aqueous Liquids", 6000, True),
        PlanetPick(2, "A II", "Alpha", "Barren", "Aqueous Liquids", 4000, True),
        PlanetPick(3, "B I", "Bravo", "Lava", "Felsic Magma", None, True),  # uses default
        PlanetPick(4, "C I", "Charlie", "Plasma", "Base Metals", 5000, True),  # off-recipe
        PlanetPick(5, "D I", "Delta", "Lava", "Felsic Magma", 9999, False),  # not used
    ]
    res, counts = calculate_from_picks(recipe, picks, default_rate=3000)
    # supply: Aqueous 10000, Felsic 3000 (default), Base Metals 5000
    check(counts == {"Aqueous Liquids": 2, "Felsic Magma": 1, "Base Metals": 1},
          f"planet counts per resource = {counts}")
    check(res.limiting == "Felsic Magma", "Felsic Magma limits output")
    check(approx(res.product_per_hour, 3000 / 300), "product/hr = 10 (3000/300)")
    rows = {r.resource: r for r in res.rows}
    check(approx(rows["Aqueous Liquids"].excess, 7000), "Aqueous excess 7000/hr")
    check(approx(rows["Felsic Magma"].excess, 0.0), "Felsic fully used")
    check(approx(rows["Base Metals"].excess, 5000)
          and approx(rows["Base Metals"].consumed, 0.0),
          "off-recipe Base Metals all excess")


def test_persistence():
    print("selection persistence:")
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    try:
        check(load_selection(path) == {}, "missing file -> empty dict")
        entries = {
            1: {"resource": "Felsic Magma", "rate": 4200.0, "use": True},
            2: {"resource": "Aqueous Liquids", "rate": None, "use": False},
        }
        save_selection(path, entries)
        loaded = load_selection(path)
        check(loaded == entries, "round-trips with integer keys")
        check(set(loaded) == {1, 2} and all(isinstance(k, int) for k in loaded),
              "keys restored as ints")
    finally:
        if os.path.exists(path):
            os.remove(path)


def main() -> int:
    test_picks()
    test_persistence()
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
