"""Tests for the PI guide chain tree and the planner's single-system mode.
Run:  python -m tests.test_guide_and_single
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase
from eve_pi.guide import chain_lines
from eve_pi.planner import make_plan
from eve_pi.sde import populate_database
from tests.make_official_fixture import P0_IDS, _records, build_fixture

ALL = {"High", "Low", "Null"}
_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_guide(db):
    print("guide chain_lines (Test Widget = P2):")
    rows = chain_lines(db, "Test Widget")
    by = {r.name: r for r in rows}
    check(rows[0].name == "Test Widget" and rows[0].depth == 0, "root is the product")
    check(by["Water"].depth == 1 and by["Silicon"].depth == 1,
          "P1 intermediates at depth 1")
    check(by["Aqueous Liquids"].is_p0 and by["Aqueous Liquids"].depth == 2,
          "P0 leaves at depth 2")
    check("Lava" in by["Felsic Magma"].sources, "Felsic Magma sourced from Lava")
    check(abs(by["Water"].per_cycle - 2.0) < 1e-6,
          "Water per-cycle ratio 10/5 = 2")
    check(abs(by["Aqueous Liquids"].per_cycle - 150.0) < 1e-6,
          "Aqueous per Water cycle 3000/20 = 150")


def test_single_system(db):
    staging = "Alpha"
    print("single-system mode (Test Widget -> only Bravo self-contains):")
    plan = make_plan(db, staging, 3, ALL, product="Test Widget", slots=6,
                     single_system=True)
    check(plan.single_system_ok, "a self-sufficient system exists")
    check(len(plan.chosen) == 1 and plan.chosen[0].system.name == "Bravo",
          "whole plan placed in one system (Bravo)")

    print("single-system concentrates vs multi-system spreads (Water, slots=6):")
    one = make_plan(db, staging, 4, ALL, product="Water", slots=6,
                    single_system=True)
    check(len(one.chosen) == 1 and one.chosen[0].system.name == "Alpha",
          "single-system keeps everything in nearest system (Alpha)")
    check(one.production.planets_used == 2,
          f"Alpha only has 2 Aqueous planets (got {one.production.planets_used})")

    many = make_plan(db, staging, 4, ALL, product="Water", slots=6,
                     single_system=False)
    check(len(many.chosen) > 1 and many.production.planets_used > 2,
          f"multi-system spreads to use more planets "
          f"({many.production.planets_used} across {len(many.chosen)} systems)")


def test_shared_planets():
    """Base Metals sorts first and any Lava planet can yield it, but only Lava
    yields Felsic Magma. Bravo has one Lava planet and one Gas planet."""
    print("shared planets go to the resource with fewest options:")
    records = _records()
    records["types"] += [
        {"_key": 103, "groupID": 42, "name": {"en": "Reactive Metals"}},
        {"_key": 230, "groupID": 43, "name": {"en": "Test Alloy"}}]
    records["schematics"] += [
        {"_key": 903, "name": {"en": "Reactive Metals"}, "cycleTime": 1800,
         "types": [{"_key": P0_IDS["Base Metals"], "isInput": True, "quantity": 3000},
                   {"_key": 103, "isInput": False, "quantity": 20}]},
        {"_key": 930, "name": {"en": "Test Alloy"}, "cycleTime": 3600,
         "types": [{"_key": 103, "isInput": True, "quantity": 10},
                   {"_key": 106, "isInput": True, "quantity": 10},
                   {"_key": 230, "isInput": False, "quantity": 5}]}]
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    populate_database(conn, records)
    conn.close()
    db = PIDatabase(path)
    try:
        plan = make_plan(db, "Bravo", 0, ALL, product="Test Alloy", slots=4,
                         single_system=True)
        allocation = plan.production.allocation
        check(allocation["Felsic Magma"] == 1, "Felsic Magma keeps the Lava planet")
        check(allocation["Base Metals"] == 1, "Base Metals takes the Gas planet")
    finally:
        db.close()
        os.remove(path)


def main() -> int:
    test_shared_planets()
    path = build_fixture()
    db = PIDatabase(path)
    try:
        test_guide(db)
        test_single_system(db)
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
