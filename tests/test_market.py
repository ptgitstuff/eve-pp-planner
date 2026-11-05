"""Tests for market valuation (pure; no network).
Run:  python -m tests.test_market
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.market import MarketPrice, value_outputs

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def approx(a, b, tol=0.5):
    return a is not None and abs(a - b) <= tol


def main() -> int:
    print("MarketPrice:")
    mp = MarketPrice("Test Widget", 201, buy=100.0, sell=120.0, volume=500.0)
    check(mp.split == 110.0, "split = midpoint of buy/sell")
    check(mp.price("buy") == 100.0 and mp.price("sell") == 120.0, "price(basis)")
    check(MarketPrice("x", 1, buy=None, sell=50.0).split == 50.0,
          "split falls back to sell when no buy")

    print("value_outputs:")
    prices = {
        "Test Widget": MarketPrice("Test Widget", 201, 100.0, 120.0, 500.0),
        "Aqueous Liquids": MarketPrice("Aqueous Liquids", 1, 8.0, 12.0, 9e6),
        "Felsic Magma": None,  # price unavailable
    }
    items = [
        ("Test Widget", 16.0, "output"),
        ("Aqueous Liquids", 7000.0, "surplus"),
        ("Felsic Magma", 100.0, "surplus"),
    ]
    val = value_outputs(items, prices, basis="split")
    # 16*110 + 7000*10 = 1760 + 70000 = 71760 ; Felsic has no price -> skipped
    check(approx(val.total_isk_hour, 71760.0), f"total ISK/hr (got {val.total_isk_hour})")
    check(approx(val.total_isk_day, 71760.0 * 24), "ISK/day = hr*24")
    byname = {r.name: r for r in val.rows}
    check(byname["Felsic Magma"].isk_per_hour is None, "missing price -> no ISK")
    check(approx(byname["Aqueous Liquids"].isk_per_hour, 70000.0), "surplus valued")

    print("buy-basis valuation:")
    val_buy = value_outputs([("Test Widget", 10.0, "output")], prices, basis="buy")
    check(approx(val_buy.total_isk_hour, 1000.0), "valued at buy price (10*100)")

    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
