"""Production modelling (recipe demand, slot allocation, balance, calculator,
selection persistence)."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from .db import PIDatabase
from .pi_data import P0_RESOURCES

PLANETS_PER_CHARACTER = 6
# Recipe name for a multi-item order; one "unit" of it is the whole order.
ORDER_PRODUCT = "Whole order"


@dataclass
class ChainNode:
    name: str
    qty_per_unit: float
    depth: int
    is_p0: bool


@dataclass
class Recipe:
    product: str
    demand: dict[str, float] = field(default_factory=dict)
    chain: list[ChainNode] = field(default_factory=list)


def recipe_demand(db: PIDatabase, product: str) -> Recipe:
    p0_id_to_name = {}
    for n in P0_RESOURCES:
        tid = db.type_id_by_name(n)
        if tid is not None:
            p0_id_to_name[tid] = n
    p0_ids = set(p0_id_to_name)
    outputs = db.schematic_outputs_q()

    target = db.type_id_by_name(product)
    rec = Recipe(product=product)
    if target is None:
        return rec

    node_qty: dict[int, float] = {}
    node_depth: dict[int, int] = {}

    def add(tid: int, qty: float, depth: int, seen: frozenset[int]) -> None:
        node_qty[tid] = node_qty.get(tid, 0.0) + qty
        node_depth[tid] = max(node_depth.get(tid, 0), depth)
        if tid in p0_ids or tid not in outputs or tid in seen:
            return
        sid, out_q = outputs[tid]
        out_q = out_q or 1
        for in_tid, in_q in db.schematic_inputs_q(sid):
            add(in_tid, qty * in_q / out_q, depth + 1, seen | {tid})

    add(target, 1.0, 0, frozenset())

    for tid, qty in node_qty.items():
        name = db.type_name_by_id(tid) or str(tid)
        is_p0 = tid in p0_ids
        rec.chain.append(ChainNode(name, qty, node_depth[tid], is_p0))
        if is_p0:
            rec.demand[name] = qty

    rec.chain.sort(key=lambda c: (-c.depth, c.name))
    return rec


def allocate_slots(requirements, demand, slots):
    res = sorted(requirements)
    if not res:
        return {}
    weights = {r: (demand.get(r, 1.0) if demand else 1.0) for r in res}
    if slots <= len(res):
        order = sorted(res, key=lambda r: -weights[r])
        return {r: (1 if i < max(slots, 0) else 0) for i, r in enumerate(order)}
    alloc = {r: 1 for r in res}
    remaining = slots - len(res)
    total_w = sum(weights.values()) or 1.0
    quota = {r: remaining * weights[r] / total_w for r in res}
    floor = {r: int(quota[r]) for r in res}
    for r in res:
        alloc[r] += floor[r]
    left = remaining - sum(floor.values())
    for r in sorted(res, key=lambda r: quota[r] - floor[r], reverse=True)[:left]:
        alloc[r] += 1
    return alloc


@dataclass
class BalanceRow:
    resource: str
    planets: int
    demand_per_unit: float
    capacity_share: float
    demand_share: float
    index: float


@dataclass
class ProductionReport:
    slots_requested: int
    planets_used: int
    characters_needed: int
    allocation: dict[str, int]
    rows: list[BalanceRow] = field(default_factory=list)
    bottleneck: str | None = None
    efficiency: float = 1.0
    recipe: "Recipe | None" = None
    shortfalls: dict[str, int] = field(default_factory=dict)

    @property
    def relative_note(self) -> str:
        return ("Extractor planets are treated as equal nominal output; real "
                "rates scale with each planet's resource richness (scan in-game).")


def characters_needed(planets: int) -> int:
    return math.ceil(planets / PLANETS_PER_CHARACTER) if planets > 0 else 0


@dataclass
class CalcRow:
    resource: str
    planets: int
    supply: float
    consumed: float
    excess: float
    excess_pct: float


@dataclass
class CalcResult:
    rate_per_planet: float
    product: str | None
    product_per_hour: float
    product_per_day: float
    limiting: str | None
    rows: list[CalcRow] = field(default_factory=list)
    intermediates: list[tuple[str, float]] = field(default_factory=list)


def _outputs(recipe, supply, planets, rate_per_planet=0.0):
    """Core throughput maths from an explicit per-resource P0 supply
    (units/hour) and the planet count behind each resource."""
    res = CalcResult(rate_per_planet=rate_per_planet,
                     product=(recipe.product if recipe else None),
                     product_per_hour=0.0, product_per_day=0.0, limiting=None)
    demand = recipe.demand if (recipe and recipe.demand) else {}
    resources = sorted(set(supply) | set(demand))

    if not demand:
        for r in resources:
            s = supply.get(r, 0.0)
            res.rows.append(CalcRow(r, planets.get(r, 0), s, 0.0, s, 100.0))
        return res

    ratios = {r: (supply.get(r, 0.0) / demand[r]) for r in demand if demand[r] > 0}
    product_per_hour = min(ratios.values()) if ratios else 0.0
    res.limiting = min(ratios, key=ratios.get) if ratios else None
    res.product_per_hour = product_per_hour
    res.product_per_day = product_per_hour * 24

    for r in resources:
        d = demand.get(r, 0.0)
        s = supply.get(r, 0.0)
        consumed = product_per_hour * d
        excess = s - consumed
        pct = (excess / s * 100.0) if s else 0.0
        res.rows.append(CalcRow(r, planets.get(r, 0), s, consumed, excess, pct))

    for node in recipe.chain:
        if not node.is_p0:
            res.intermediates.append((node.name, node.qty_per_unit * product_per_hour))
    return res


def calculate(recipe, allocation, rate_per_planet, rates=None):
    """Estimate output assuming a uniform per-planet P0 rate (units/hour) times
    the planet allocation. ``rates`` overrides the rate for specific resources."""
    rates = rates or {}
    supply = {r: allocation[r] * rates.get(r, rate_per_planet) for r in allocation}
    return _outputs(recipe, supply, dict(allocation), rate_per_planet)


@dataclass
class PlanetPick:
    """One user-chosen extractor planet and its in-game output."""
    planet_id: int
    name: str
    system: str
    planet_type: str
    resource: str
    rate: float | None
    use: bool = True


def calculate_from_picks(recipe, picks, default_rate):
    """Compute output from explicitly selected planets, each with its own P0
    rate (falling back to ``default_rate`` when unset). Returns the result and
    the planet count per resource."""
    supply: dict[str, float] = {}
    counts: dict[str, int] = {}
    for p in picks:
        if not p.use or not p.resource:
            continue
        rate = p.rate if p.rate is not None else default_rate
        supply[p.resource] = supply.get(p.resource, 0.0) + rate
        counts[p.resource] = counts.get(p.resource, 0) + 1
    return _outputs(recipe, supply, counts, default_rate), counts


def build_report(requirements, demand, allocation, slots_requested,
                 recipe=None, shortfalls=None):
    planets_used = sum(allocation.values())
    rep = ProductionReport(
        slots_requested=slots_requested,
        planets_used=planets_used,
        characters_needed=characters_needed(planets_used),
        allocation=dict(allocation),
        recipe=recipe,
        shortfalls=dict(shortfalls or {}),
    )
    total_planets = planets_used or 1
    w = {r: (demand.get(r, 1.0) if demand else 1.0) for r in requirements}
    total_w = sum(w.values()) or 1.0

    worst = None
    for r in sorted(requirements):
        cap_share = allocation.get(r, 0) / total_planets
        dem_share = w[r] / total_w
        index = (cap_share / dem_share) if dem_share else 0.0
        rep.rows.append(BalanceRow(r, allocation.get(r, 0), w[r],
                                   cap_share, dem_share, index))
        if worst is None or index < worst[1]:
            worst = (r, index)
    if worst:
        rep.bottleneck = worst[0]
        rep.efficiency = worst[1]
    return rep


# ---------------------------------------------------------------------------
# selection persistence (per-planet 'use', extracted resource, and P0 rate)
# ---------------------------------------------------------------------------
def load_selection(path: str) -> dict[int, dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: dict[int, dict] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
    return out


def save_selection(path: str, entries: dict[int, dict]) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({str(k): v for k, v in entries.items()}, fh, indent=2)
    os.replace(tmp, path)


def default_selection_path() -> str:
    from .sde import default_cache_dir
    return os.path.join(default_cache_dir(), "planet_selection.json")
