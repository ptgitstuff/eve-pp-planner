"""Data model behind the visual production-chain map.

Builds the whole PI graph (planet type -> P0 -> P1 -> ... -> P4) from the SDE and
traces what feeds into, and what is made from, any one item. There is no Tkinter
here, so all of this is testable without a display; ``chain_view.py`` draws it.

Levels count production steps away from the traced item. P0 -> P1 is free
(level 0 cost) because the two are one step on the map, joined by an arrow.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from .db import PIDatabase
from .pi_data import (P0_RESOURCES, PLANET_RESOURCES, PLANET_TYPE_BY_ID,
                      PLANET_TYPES)
from .profitability import classify_products

COL_PLANET, COL_P0, COL_P1, COL_P2, COL_P3, COL_P4 = range(6)

CURRENT_COLOR = "#79cef4"
NORMAL_COLOR = "#666666"
DIM_COLOR = "#333333"
UPSTREAM_COLORS = ("#ffff00", "#f7941d", "#763c19", "#492f1f")
DOWNSTREAM_COLORS = ("#00aeef", "#00ff00", "#008000", "#304b15")


@dataclass
class ChainRecipe:
    output_qty: int
    cycle_seconds: int
    inputs: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class ChainGraph:
    columns: list[list[str]]                    # six columns, left to right
    column_of: dict[str, int]
    inputs: dict[str, set[str]]
    outputs: dict[str, set[str]]
    recipes: dict[str, ChainRecipe]
    cycle_by_tier: dict[int, int]
    type_ids: dict[str, int] = field(default_factory=dict)   # for icon lookups

    def is_refining_step(self, a: str, b: str) -> bool:
        """True when a and b are a P0 and the P1 it refines into."""
        return {self.column_of.get(a), self.column_of.get(b)} == {COL_P0, COL_P1}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    direction: str    # "up" (feeds the traced item) or "down" (made from it)
    level: int


@dataclass
class Trace:
    current: str
    upstream: dict[str, int] = field(default_factory=dict)
    downstream: dict[str, int] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)


def build_chain_graph(db: PIDatabase) -> ChainGraph:
    tiers = classify_products(db)
    cycles = {int(row[0]): int(row[1] or 3600) for row in db.conn.execute(
        "SELECT schematicID, cycleTime FROM planetSchematics")}

    inputs: dict[str, set[str]] = {}
    outputs: dict[str, set[str]] = {}

    def link(source: str, target: str) -> None:
        inputs.setdefault(target, set()).add(source)
        outputs.setdefault(source, set()).add(target)

    for planet, resources in PLANET_RESOURCES.items():
        for resource in resources:
            link(planet, resource)

    recipes: dict[str, ChainRecipe] = {}
    tier_cycles: dict[int, Counter] = {}
    for out_tid, (sid, out_qty) in db.schematic_outputs_q().items():
        name = db.type_name_by_id(out_tid)
        if name is None or name not in tiers:
            continue
        cycle = cycles.get(sid, 3600)
        tier_cycles.setdefault(tiers[name], Counter())[cycle] += 1
        recipe = ChainRecipe(out_qty, cycle)
        for in_tid, in_qty in db.schematic_inputs_q(sid):
            in_name = db.type_name_by_id(in_tid) or str(in_tid)
            recipe.inputs.append((in_name, in_qty))
            link(in_name, name)
        recipe.inputs.sort()
        recipes[name] = recipe

    by_tier = {t: sorted(n for n, nt in tiers.items() if nt == t)
               for t in (1, 2, 3, 4)}

    # Keep each P1 on the same row as the P0 it refines from.
    p1_rows: list[str] = []
    for resource in P0_RESOURCES:
        refined = sorted(o for o in outputs.get(resource, ()) if tiers.get(o) == 1)
        p1_rows.append(refined[0] if refined else "")
    p1_rows += [n for n in by_tier[1] if n not in p1_rows]

    columns = [list(PLANET_TYPES), list(P0_RESOURCES), p1_rows,
               by_tier[2], by_tier[3], by_tier[4]]
    column_of = {name: i for i, col in enumerate(columns) for name in col if name}

    type_ids = {label: tid for tid, label in PLANET_TYPE_BY_ID.items()}
    for name in column_of:
        if name not in type_ids:
            tid = db.type_id_by_name(name)
            if tid is not None:
                type_ids[name] = tid

    return ChainGraph(
        columns=columns, column_of=column_of, inputs=inputs, outputs=outputs,
        recipes=recipes,
        cycle_by_tier={t: c.most_common(1)[0][0] for t, c in tier_cycles.items()},
        type_ids=type_ids,
    )


def _levels(graph: ChainGraph, start: str,
            neighbours: dict[str, set[str]]) -> dict[str, int]:
    """Lowest step count from ``start``; P0 <-> P1 hops cost nothing."""
    levels = {start: 0}
    pending = deque([start])
    while pending:
        node = pending.popleft()
        for nxt in neighbours.get(node, ()):
            free = graph.is_refining_step(node, nxt)
            level = levels[node] + (0 if free else 1)
            if level < levels.get(nxt, level + 1):
                levels[nxt] = level
                if free:
                    pending.appendleft(nxt)
                else:
                    pending.append(nxt)
    del levels[start]
    return levels


def trace(graph: ChainGraph, name: str) -> Trace:
    """Everything feeding into ``name`` and everything made from it."""
    result = Trace(current=name,
                   upstream=_levels(graph, name, graph.inputs),
                   downstream=_levels(graph, name, graph.outputs))

    for node, level in result.upstream.items():
        for target in graph.outputs.get(node, ()):
            if ((target == name or target in result.upstream)
                    and not graph.is_refining_step(node, target)):
                result.edges.append(Edge(node, target, "up", level))

    for node, level in result.downstream.items():
        for source in graph.inputs.get(node, ()):
            if ((source == name or source in result.downstream)
                    and not graph.is_refining_step(source, node)):
                result.edges.append(Edge(source, node, "down", level))
    return result


def _shade(palette: tuple[str, ...], level: int) -> str:
    if level <= 0:
        return CURRENT_COLOR
    return palette[min(level, len(palette)) - 1]


def node_color(active: Trace | None, name: str) -> str:
    """Text colour for ``name`` given the trace on screen, if any."""
    if active is None:
        return NORMAL_COLOR
    if name == active.current:
        return CURRENT_COLOR
    if name in active.upstream:
        return _shade(UPSTREAM_COLORS, active.upstream[name])
    if name in active.downstream:
        return _shade(DOWNSTREAM_COLORS, active.downstream[name])
    return DIM_COLOR


def edge_color(edge: Edge, strength: float = 0.5) -> str:
    """Line colour: the edge's level colour, darkened so labels stay readable."""
    palette = UPSTREAM_COLORS if edge.direction == "up" else DOWNSTREAM_COLORS
    base = _shade(palette, edge.level)
    channels = (int(base[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{int(c * strength):02x}" for c in channels)


def describe(graph: ChainGraph, name: str) -> str:
    """One-line summary of ``name`` for the map's detail strip."""
    column = graph.column_of.get(name)
    if column == COL_PLANET:
        return f"{name} planet  —  extracts: " + ", ".join(PLANET_RESOURCES[name])
    if column == COL_P0:
        sources = ", ".join(sorted(graph.inputs.get(name, ())))
        refined = ", ".join(sorted(graph.outputs.get(name, ())))
        text = f"{name} (P0)  —  extract on: {sources}"
        return f"{text}  —  refines into: {refined}" if refined else text

    recipe = graph.recipes.get(name)
    if recipe is None:
        return name
    used_in = sorted(graph.outputs.get(name, ()))
    parts = " + ".join(f"{qty:,} {item}" for item, qty in recipe.inputs)
    text = (f"{name} (P{column - COL_P0})  —  {parts}  →  "
            f"{recipe.output_qty:,} per {recipe.cycle_seconds:,}s")
    return f"{text}  —  used in {len(used_in)} product(s)" if used_in else text
