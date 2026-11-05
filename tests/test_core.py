"""Unit tests for the core PI-planner logic using the synthetic fixture.

Run from the project root:  python -m tests.test_core
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase
from eve_pi.optimizer import Candidate, min_set_cover
from eve_pi.planner import make_plan
from eve_pi.resolver import resolve_product
from tests.make_fixture import build_fixture

ALL_BANDS = {"High", "Low", "Null"}
_passed = 0


def check(cond: bool, label: str) -> None:
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_resolver(db: PIDatabase) -> None:
    print("resolver:")
    res = resolve_product(db, "Test Widget")
    check(res.raw_resources == {"Aqueous Liquids", "Felsic Magma"},
          f"Test Widget -> {sorted(res.raw_resources)}")
    check(res.resource_planets["Felsic Magma"] == {"Lava"},
          "Felsic Magma only from Lava")
    check("Gas" in res.resource_planets["Aqueous Liquids"],
          "Aqueous Liquids available from Gas")


def test_bfs(db: PIDatabase) -> None:
    print("jump-range BFS:")
    alpha = db.get_system("Alpha")
    dist = db.systems_within_range(alpha.id, 2)
    names = {db.systems_info([k])[k].name: v for k, v in dist.items()}
    check(names == {"Alpha": 0, "Bravo": 1, "Charlie": 2},
          f"within 2 jumps of Alpha: {names}")


def test_optimizer() -> None:
    print("optimizer (abstract set cover):")
    universe = {"a", "b", "c", "d"}
    cands = [
        Candidate(1, frozenset({"a", "b"}), 1),
        Candidate(2, frozenset({"c", "d"}), 1),
        Candidate(3, frozenset({"a", "b", "c"}), 3),
        Candidate(4, frozenset({"d"}), 1),
    ]
    sol = min_set_cover(universe, cands)
    check(sol.size == 2, f"min cover size == 2 (got {sol.size})")
    check(set(sol.system_ids) == {1, 2},
          f"fewest-jumps cover == {{1,2}} (got {set(sol.system_ids)})")
    # impossible cover
    check(min_set_cover({"x"}, cands) is None, "impossible cover -> None")


def test_plan_product(db: PIDatabase) -> None:
    print("plan: product mode (Test Widget from Alpha):")
    plan = make_plan(db, "Alpha", 3, ALL_BANDS, product="Test Widget")
    check(plan.ok, "plan ok")
    check(plan.solution.size == 1,
          f"single system covers Widget (got {plan.solution.size})")
    chosen = plan.chosen[0].system.name
    check(chosen == "Bravo", f"chosen system == Bravo (got {chosen})")
    reqs = {r for r, _ in plan.chosen[0].assignments}
    check(reqs == {"Aqueous Liquids", "Felsic Magma"},
          f"Bravo assigned both resources (got {sorted(reqs)})")


def test_plan_planet_types(db: PIDatabase) -> None:
    print("plan: planet-types mode (Temperate + Plasma from Alpha):")
    plan = make_plan(db, "Alpha", 4, ALL_BANDS,
                     planet_types={"Temperate", "Plasma"})
    check(plan.ok, "plan ok")
    check(plan.solution.size == 2, f"needs 2 systems (got {plan.solution.size})")
    names = {c.system.name for c in plan.chosen}
    check(names == {"Alpha", "Echo"}, f"chosen == Alpha+Echo (got {names})")


def test_security_filter(db: PIDatabase) -> None:
    print("security band filter:")
    plan = make_plan(db, "Alpha", 4, {"High"}, planet_types={"Storm"})
    # Storm is in Charlie (low sec) -> excluded -> unmet
    check(not plan.ok, "Storm unreachable in High-only -> not ok")
    check(plan.unmet == ["Storm"], f"unmet == [Storm] (got {plan.unmet})")


def test_out_of_range(db: PIDatabase) -> None:
    print("out-of-range requirement:")
    plan = make_plan(db, "Alpha", 2, ALL_BANDS, planet_types={"Ice"})
    check(not plan.ok, "Ice (in Delta, 3 jumps) out of 2-jump range -> not ok")


def main() -> int:
    path = build_fixture()
    db = PIDatabase(path)
    try:
        test_resolver(db)
        test_bfs(db)
        test_optimizer()
        test_plan_product(db)
        test_plan_planet_types(db)
        test_security_filter(db)
        test_out_of_range(db)
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
