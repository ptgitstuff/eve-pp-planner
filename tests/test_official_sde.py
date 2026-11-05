"""End-to-end test of the official-JSONL -> SQLite transform feeding the full
planner stack.  Run:  python -m tests.test_official_sde
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase
from eve_pi.planner import make_plan
from tests.make_official_fixture import build_fixture

ALL_BANDS = {"High", "Low", "Null"}
_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def main() -> int:
    path = build_fixture()
    db = PIDatabase(path)
    try:
        print("official-SDE transform sanity:")
        # shattered planet (typeID 30889) excluded -> 8 usable planets
        n = db.conn.execute("SELECT COUNT(*) FROM planets").fetchone()[0]
        check(n == 8, f"8 colonisable planets, shattered dropped (got {n})")
        prods = db.all_pi_products()
        check({"Water", "Silicon", "Test Widget"} <= set(prods),
              "PI product list includes schematic outputs")
        check(db.get_system("alpha").band == "High", "Alpha parsed as High sec")

        print("jump-range BFS (from stargates):")
        a = db.get_system("Alpha")
        dist = db.systems_within_range(a.id, 2)
        names = {db.systems_info([k])[k].name: v for k, v in dist.items()}
        check(names == {"Alpha": 0, "Bravo": 1, "Charlie": 2},
              f"within 2 jumps: {names}")

        print("product mode (Test Widget from Alpha):")
        plan = make_plan(db, "Alpha", 3, ALL_BANDS, product="Test Widget")
        check(plan.ok, "plan ok")
        check(plan.resolution.raw_resources == {"Aqueous Liquids", "Felsic Magma"},
              f"resolved P0s = {sorted(plan.resolution.raw_resources)}")
        check(plan.solution.size == 1, f"single system (got {plan.solution.size})")
        check(plan.chosen[0].system.name == "Bravo",
              f"chosen Bravo (got {plan.chosen[0].system.name})")

        print("planet-types mode (Temperate + Plasma):")
        plan = make_plan(db, "Alpha", 4, ALL_BANDS,
                         planet_types={"Temperate", "Plasma"})
        check(plan.ok and plan.solution.size == 2, "needs Alpha + Echo")
        check({c.system.name for c in plan.chosen} == {"Alpha", "Echo"},
              "chosen Alpha+Echo")

        print("security filter (High only excludes Storm@Charlie):")
        plan = make_plan(db, "Alpha", 4, {"High"}, planet_types={"Storm"})
        check(not plan.ok and plan.unmet == ["Storm"], "Storm unmet in High-only")
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
