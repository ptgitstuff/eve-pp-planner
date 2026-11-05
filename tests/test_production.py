"""Tests for slot allocation, recipe demand, and the production report.
Run:  python -m tests.test_production
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase
from eve_pi.planner import make_plan
from eve_pi.production import allocate_slots, characters_needed, recipe_demand
from tests.make_official_fixture import build_fixture

ALL_BANDS = {"High", "Low", "Null"}
_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_allocation():
    print("slot allocation:")
    check(allocate_slots(["A", "B"], {"A": 300, "B": 300}, 6) == {"A": 3, "B": 3},
          "equal demand -> even split (3/3)")
    check(allocate_slots(["A", "B"], {"A": 300, "B": 100}, 6) == {"A": 4, "B": 2},
          "weighted demand 3:1 -> 4/2")
    check(allocate_slots(["A", "B", "C"], None, 6) == {"A": 2, "B": 2, "C": 2},
          "no demand (planet-types) -> even 2/2/2")
    check(allocate_slots(["A", "B", "C"], None, 2) == {"A": 1, "B": 1, "C": 0},
          "too few slots -> partial coverage")


def test_characters():
    print("characters needed (6 planets/char):")
    for n, exp in [(0, 0), (1, 1), (6, 1), (7, 2), (12, 2), (13, 3)]:
        check(characters_needed(n) == exp, f"{n} planets -> {exp} char(s)")


def test_recipe_and_plan():
    path = build_fixture()
    db = PIDatabase(path)
    try:
        print("recipe demand:")
        rec = recipe_demand(db, "Test Widget")
        check(rec.demand == {"Aqueous Liquids": 300.0, "Felsic Magma": 300.0},
              f"Test Widget P0 demand/unit = {rec.demand}")

        print("make_plan with slots:")
        plan = make_plan(db, "Alpha", 3, ALL_BANDS, product="Test Widget", slots=6)
        pr = plan.production
        check(pr is not None, "production report present")
        # equal demand -> target 3/3; only 1 Lava (Felsic) planet exists in range
        check(pr.allocation["Aqueous Liquids"] == 3, "3 Aqueous planets placed")
        check(pr.allocation["Felsic Magma"] == 1, "only 1 Felsic planet available")
        check(pr.shortfalls.get("Felsic Magma") == 2, "shortfall of 2 Felsic reported")
        check(pr.planets_used == 4, f"4 planets used (got {pr.planets_used})")
        check(pr.characters_needed == 1, "1 character needed for 4 planets")
        check(pr.bottleneck == "Felsic Magma", "bottleneck is Felsic Magma")

        print("slots == #resources -> one each:")
        plan2 = make_plan(db, "Alpha", 3, ALL_BANDS, product="Test Widget", slots=2)
        check(plan2.production.allocation == {"Aqueous Liquids": 1, "Felsic Magma": 1},
              "2 slots -> 1 each")
    finally:
        db.close()
        os.remove(path)


def main() -> int:
    test_allocation()
    test_characters()
    test_recipe_and_plan()
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
