"""Tests for the production-chain map's graph, tracing and colours.
Run:  python -m tests.test_chain_map
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.chain_map import (COL_P0, COL_P1, COL_P2, COL_PLANET, CURRENT_COLOR,
                              DIM_COLOR, DOWNSTREAM_COLORS, NORMAL_COLOR,
                              UPSTREAM_COLORS, build_chain_graph, describe,
                              edge_color, node_color, trace)
from eve_pi.db import PIDatabase
from tests.make_official_fixture import build_fixture

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_graph(g):
    print("graph layout:")
    check(g.column_of["Barren"] == COL_PLANET, "planets in the first column")
    check(g.column_of["Aqueous Liquids"] == COL_P0, "P0 in the resources column")
    check(g.column_of["Water"] == COL_P1, "Water in the P1 column")
    check(g.column_of["Test Widget"] == COL_P2, "Test Widget in the P2 column")
    row = g.columns[COL_P0].index("Felsic Magma")
    check(g.columns[COL_P1][row] == "Silicon", "P1 sits on the row of its P0")
    check(g.cycle_by_tier == {1: 1800, 2: 3600}, "cycle times come from the SDE")


def test_trace_up(g):
    print("trace from a P2 (inputs):")
    t = trace(g, "Test Widget")
    check(t.upstream["Water"] == 1, "direct input is level 1")
    check(t.upstream["Aqueous Liquids"] == 1, "P0 -> P1 costs no extra level")
    check(t.upstream["Barren"] == 2, "planets one step beyond the P0")
    check(not t.downstream, "nothing is made from the top of the chain")
    pairs = {(e.source, e.target) for e in t.edges}
    check(("Water", "Test Widget") in pairs, "line from input to product")
    check(("Barren", "Aqueous Liquids") in pairs, "line from planet to P0")
    check(("Aqueous Liquids", "Water") not in pairs,
          "no line where the arrow already joins P0 and P1")
    check(not any(a == "Gas" and b == "Felsic Magma" for a, b in pairs),
          "planets that can't yield the P0 aren't linked")


def test_trace_down(g):
    print("trace from a planet and a P0 (outputs):")
    t = trace(g, "Lava")
    check(t.downstream["Felsic Magma"] == 1, "P0 from the planet is level 1")
    check(t.downstream["Silicon"] == 1, "its P1 shares that level")
    check(t.downstream["Test Widget"] == 2, "next product is level 2")

    t = trace(g, "Aqueous Liquids")
    check(t.downstream["Water"] == 0, "P1 partner of a traced P0 is level 0")
    check(t.upstream["Barren"] == 1, "planets are level 1 above a P0")
    check("Silicon" not in t.downstream, "unrelated P1 isn't traced")


def test_colors(g):
    print("colours:")
    t = trace(g, "Test Widget")
    check(node_color(None, "Water") == NORMAL_COLOR, "default with no trace")
    check(node_color(t, "Test Widget") == CURRENT_COLOR, "traced item highlighted")
    check(node_color(t, "Water") == UPSTREAM_COLORS[0], "level 1 input colour")
    check(node_color(t, "Barren") == UPSTREAM_COLORS[1], "level 2 input colour")
    check(node_color(t, "Biofuels") == DIM_COLOR, "unrelated items dim")

    t = trace(g, "Lava")
    check(node_color(t, "Silicon") == DOWNSTREAM_COLORS[0], "level 1 output colour")
    check(node_color(t, "Test Widget") == DOWNSTREAM_COLORS[1], "level 2 output colour")
    edge = next(e for e in t.edges if e.target == "Test Widget")
    check(edge_color(edge) == "#007f00", "lines are a darkened node colour")


def test_describe(g):
    print("detail text:")
    check("extracts: Base Metals" in describe(g, "Lava"), "planet lists its resources")
    check("extract on:" in describe(g, "Felsic Magma")
          and "refines into: Silicon" in describe(g, "Felsic Magma"),
          "P0 lists its planets and P1")
    text = describe(g, "Test Widget")
    check("10 Silicon + 10 Water" in text and "5 per 3,600s" in text,
          "product shows its recipe")


def main() -> int:
    path = build_fixture()
    db = PIDatabase(path)
    try:
        g = build_chain_graph(db)
        test_graph(g)
        test_trace_up(g)
        test_trace_down(g)
        test_colors(g)
        test_describe(g)
    finally:
        db.close()
        os.remove(path)
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
