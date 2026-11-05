"""Tests for the profitability ranking engine (slot-filling portfolio).
Run:  python -m tests.test_profitability
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase
from eve_pi.market import MarketPrice
from eve_pi.profitability import (allocate_max_min, allocate_portfolio,
                                 classify_products, rank_systems, tier_products)
from tests.make_official_fixture import build_fixture

ALL = {"High", "Low", "Null"}
_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def test_allocate():
    print("allocate_max_min (single product):")
    pph, used, _ = allocate_max_min([{"A"}, {"B"}], {"A": 300, "B": 300}, 3000, 6)
    check(approx(pph, 10) and used == 2, "balanced two inputs -> 10/hr, 2 planets")
    pph, used, _ = allocate_max_min([{"A"}, {"A"}], {"A": 300, "B": 300}, 3000, 6)
    check(pph == 0 and used == 0, "missing input -> 0 (not self-sufficient)")
    pph, used, _ = allocate_max_min([{"A"}, {"A"}, {"B"}], {"A": 300, "B": 300}, 3000, 6)
    check(approx(pph, 10) and used == 2, "caps to scarcest input; no wasted planet")
    pph, used, _ = allocate_max_min([{"A"}, {"A"}, {"A"}], {"A": 150}, 3000, 6)
    check(approx(pph, 60) and used == 3, "P1 scales with planets (3 -> 60/hr)")


def test_portfolio():
    print("allocate_portfolio (fill the slots with a mix):")
    prods = [("Silicon", {"Felsic Magma": 150}, 50.0),
             ("Water", {"Aqueous Liquids": 150}, 10.0)]
    total, used, mix = allocate_portfolio(
        [{"Felsic Magma"}, {"Aqueous Liquids"}], prods, 3000, 6)
    check(approx(total, 1200) and used == 2, "runs BOTH products, not just one (1200/2)")
    check(mix[0][0] == "Silicon", "highest-value product listed first")

    total, used, mix = allocate_portfolio(
        [{"Aqueous Liquids"}] * 4, [("Water", {"Aqueous Liquids": 150}, 10.0)], 3000, 6)
    check(approx(total, 800) and used == 4, "fills all available planets (4 -> 800)")

    total, used, mix = allocate_portfolio(
        [{"Felsic Magma"}, {"Aqueous Liquids"}, {"Aqueous Liquids"}], prods, 3000, 6)
    check(used == 3 and approx(total, 1400),
          "best P1 first, remaining slots used for next (3 planets, 1400)")


def test_tiers(db):
    print("tier classification:")
    tiers = classify_products(db)
    check(tiers.get("Water") == 1 and tiers.get("Silicon") == 1, "Water/Silicon = P1")
    check(tiers.get("Test Widget") == 2, "Test Widget = P2")


def test_rank(db):
    staging = db.get_system("Alpha").id
    print("rank_systems — tier 2 (only Bravo self-sufficient):")
    p2 = {"Test Widget": MarketPrice("Test Widget", 201, 100.0, 100.0, 1000.0)}
    r2 = rank_systems(db, staging, 4, ALL, 2, p2, "split", rate=3000, slots=6)
    check(len(r2) == 1 and r2[0].system_name == "Bravo", "only Bravo qualifies for P2")
    check(approx(r2[0].isk_per_hour, 1000) and r2[0].planets_used == 2,
          "Bravo P2 = 1000 ISK/hr over 2 planets")

    print("rank_systems — tier 1 (Bravo uses both planets: Silicon + Water):")
    p1 = {"Water": MarketPrice("Water", 101, 10.0, 10.0, 1e6),
          "Silicon": MarketPrice("Silicon", 106, 50.0, 50.0, 1e6)}
    r1 = rank_systems(db, staging, 4, ALL, 1, p1, "split", rate=3000, slots=6)
    names = [s.system_name for s in r1]
    check(r1[0].system_name == "Bravo", f"Bravo tops P1 (order {names})")
    check(approx(r1[0].isk_per_hour, 1200) and r1[0].planets_used == 2,
          f"Bravo earns from BOTH planets (1200/hr, got {r1[0].isk_per_hour})")
    check(any(m[0] == "Silicon" for m in r1[0].mix)
          and any(m[0] == "Water" for m in r1[0].mix), "Bravo mix = Silicon + Water")
    check("Echo" not in names, "Echo (no Aqueous/Felsic) excluded")


def main() -> int:
    test_allocate()
    test_portfolio()
    path = build_fixture()
    db = PIDatabase(path)
    try:
        test_tiers(db)
        test_rank(db)
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
