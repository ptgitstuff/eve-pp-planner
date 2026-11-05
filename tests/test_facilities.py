"""Tests for facility & planet-count accounting.
Run:  python -m tests.test_facilities
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase
from eve_pi.facilities import (FacilityRate, build_breakdown, facility_rates)
from eve_pi.production import ChainNode, Recipe, recipe_demand
from tests.make_official_fixture import build_fixture

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_rates(db):
    print("facility_rates (from SDE quantities + cycle times):")
    rates = facility_rates(db)
    # Water: 20 per 1800s = 40/hr, basic (input is a P0)
    check(abs(rates["Water"].out_per_hour - 40.0) < 1e-6 and rates["Water"].is_basic,
          "Water = 40/hr, basic (P0->P1)")
    # Test Widget: 5 per 3600s = 5/hr, advanced (inputs are P1)
    check(abs(rates["Test Widget"].out_per_hour - 5.0) < 1e-6
          and not rates["Test Widget"].is_basic,
          "Test Widget = 5/hr, advanced")


def test_breakdown():
    print("build_breakdown (planet & facility counts):")
    recipe = Recipe(product="Test Widget", demand={"Aq": 300, "Fe": 300}, chain=[
        ChainNode("Test Widget", 1.0, 0, False),
        ChainNode("Water", 2.0, 1, False),
        ChainNode("Silicon", 2.0, 1, False),
        ChainNode("Aq", 300.0, 2, True),
        ChainNode("Fe", 300.0, 2, True),
    ])
    rates = {"Test Widget": FacilityRate(5.0, False),
             "Water": FacilityRate(40.0, True),
             "Silicon": FacilityRate(40.0, True)}
    b = build_breakdown(recipe, product_per_hour=5.0, rates=rates,
                        extractor_planets=2, basic_per_extractor=8, adv_per_factory=8)
    # Widget: ceil(5/5)=1 adv ; Water ceil(10/40)=1 basic ; Silicon 1 basic
    check(b.advanced_facilities == 1 and b.basic_factories_needed == 2,
          "1 advanced + 2 basic facilities at 5 widgets/hr")
    check(b.advanced_by_tier.get(2) == 1, "the P2 is a tier-2 advanced facility")
    check(b.factory_planets == 1 and b.total_planets == 3,
          "1 factory planet; 2 extractors + 1 factory = 3 planets")
    check(b.characters == 1, "3 planets -> 1 character")

    # scale up: 100 widgets/hr -> 20 advanced, 40 basic
    b2 = build_breakdown(recipe, 100.0, rates, extractor_planets=12,
                         basic_per_extractor=8, adv_per_factory=8)
    check(b2.advanced_facilities == 20 and b2.factory_planets == 3,
          "100/hr -> 20 advanced facilities over ceil(20/8)=3 factory planets")
    check(b2.total_planets == 15 and b2.characters == 3,
          "12 extractors + 3 factory = 15 planets -> 3 characters")
    # 100/hr -> Water/Silicon each ceil(200/40)=5 -> 10 basic facilities
    check(b2.basic_factories_needed == 10 and b2.basic_over_capacity is False,
          "10 basic needed, under 12*8 capacity")
    b3 = build_breakdown(recipe, 100.0, rates, extractor_planets=1,
                         basic_per_extractor=8, adv_per_factory=8)
    check(b3.basic_over_capacity is True,
          "1 extractor (cap 8) < 10 basic needed -> flagged over capacity")


def test_end_to_end(db):
    print("end-to-end with fixture recipe:")
    rates = facility_rates(db)
    recipe = recipe_demand(db, "Test Widget")
    b = build_breakdown(recipe, product_per_hour=5.0, rates=rates,
                        extractor_planets=2)
    check(b.advanced_facilities == 1 and b.basic_factories_needed == 2,
          "fixture Test Widget @5/hr -> 1 adv + 2 basic")
    check(b.total_planets == 3, "2 extractors + 1 factory planet")


def main() -> int:
    test_breakdown()
    path = build_fixture()
    db = PIDatabase(path)
    try:
        test_rates(db)
        test_end_to_end(db)
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
