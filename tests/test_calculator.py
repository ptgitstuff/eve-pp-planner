"""Tests for the absolute output calculator.
Run:  python -m tests.test_calculator
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase
from eve_pi.production import calculate, recipe_demand
from tests.make_official_fixture import build_fixture

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def main() -> int:
    path = build_fixture()
    db = PIDatabase(path)
    try:
        rec = recipe_demand(db, "Test Widget")  # demand 300 each P0

        print("calculator (product mode):")
        # 3 Aqueous planets, 1 Felsic planet, 5000 P0/hr each
        res = calculate(rec, {"Aqueous Liquids": 3, "Felsic Magma": 1}, 5000)
        # supply: Aqueous 15000, Felsic 5000; demand 300 each
        # product/hr limited by Felsic: 5000/300 = 16.667
        check(res.limiting == "Felsic Magma", "limiting input = Felsic Magma")
        check(approx(res.product_per_hour, 5000 / 300), "product/hr = 16.67")
        check(approx(res.product_per_day, 5000 / 300 * 24), "product/day = 400")
        rows = {r.resource: r for r in res.rows}
        check(approx(rows["Aqueous Liquids"].excess, 10000),
              "Aqueous excess 10,000/hr")
        check(approx(rows["Felsic Magma"].excess, 0.0), "Felsic fully consumed")

        print("balanced rates remove the bottleneck:")
        # give Felsic 3 planets too -> supply equal -> no excess, higher output
        res2 = calculate(rec, {"Aqueous Liquids": 3, "Felsic Magma": 3}, 5000)
        check(approx(res2.product_per_hour, 15000 / 300), "balanced product/hr = 50")
        check(all(approx(r.excess, 0.0) for r in res2.rows),
              "no excess when balanced")

        print("planet-types mode (no product) -> raw supply only:")
        res3 = calculate(None, {"Temperate": 2, "Plasma": 1}, 4000)
        check(res3.product_per_hour == 0.0, "no product rate without a recipe")
        check({r.resource: r.supply for r in res3.rows}
              == {"Temperate": 8000.0, "Plasma": 4000.0}, "raw supply reported")
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
