"""Rank the most profitable systems within range for a chosen output tier.

For a tier (P1..P4) we look at every system in range and, treating it as a
*self-contained* base, fill the planet-slot budget with the best **mix** of that
tier's products its own planets can make (each product's P0 inputs must be
covered from within the system). Planets are allocated most-ISK/planet first so
all slots are put to work, output is valued at Jita, and systems are ranked by
the total ISK/hour the slots earn.

Profit can't use real planet richness (not in the SDE — see README), so a single
nominal P0/hour rate per planet is assumed; rankings are comparative.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import PIDatabase
from .pi_data import PLANET_RESOURCES
from .production import recipe_demand

TIER_LABEL = {1: "P1", 2: "P2", 3: "P3", 4: "P4"}


def classify_products(db: PIDatabase) -> dict[str, int]:
    """Map each PI product name to its tier (1..4) via recipe chain depth."""
    tiers: dict[str, int] = {}
    for name in db.all_pi_products():
        rec = recipe_demand(db, name)
        depth = max((n.depth for n in rec.chain), default=0)
        if depth >= 1:
            tiers[name] = depth
    return tiers


def tier_products(db: PIDatabase, tier: int) -> list[tuple[str, dict[str, float]]]:
    """[(product name, {P0 -> units per product unit}), ...] for the tier."""
    out = []
    for name, t in classify_products(db).items():
        if t == tier:
            demand = recipe_demand(db, name).demand
            if demand:
                out.append((name, demand))
    return out


def allocate_max_min(planet_sets: list[set[str]], demand: dict[str, float],
                     rate: float, slots: int):
    """Greedy max-min allocation of a system's planets across one product's P0
    inputs. Returns (product_per_hour, planets_used, consumed_indices)."""
    return _alloc_one(planet_sets, set(range(len(planet_sets))),
                      demand, rate, slots)


def _alloc_one(planet_sets, free_idx, demand, rate, budget):
    """Allocate up to ``budget`` of the free planets to a single product to
    maximise its output. Returns (product_per_hour, used, consumed_indices)."""
    required = list(demand)
    caps = {i: (planet_sets[i] & set(required)) for i in free_idx}
    caps = {i: c for i, c in caps.items() if c}
    if budget <= 0 or not caps:
        return 0.0, 0, []
    coverable = set().union(*caps.values())
    if not set(required) <= coverable:
        return 0.0, 0, []

    assigned = {r: 0 for r in required}
    avail = set(caps)
    consumed: list[int] = []
    cap_budget = min(budget, len(caps))
    while len(consumed) < cap_budget:
        min_ratio = min(assigned[r] / demand[r] for r in required)
        bottlenecks = [r for r in required
                       if assigned[r] / demand[r] <= min_ratio + 1e-9]
        picks: dict[str, int] = {}
        ok = True
        for r in bottlenecks:
            cand = None
            for i in avail:
                if r in caps[i] and (cand is None or len(caps[i]) < len(caps[cand])):
                    cand = i
            if cand is None:
                ok = False
                break
            picks[r] = cand
        if not ok:
            break
        r = min(picks, key=lambda rr: len(caps[picks[rr]]))
        i = picks[r]
        avail.discard(i)
        consumed.append(i)
        assigned[r] += 1

    pph = rate * min(assigned[r] / demand[r] for r in required)
    return pph, len(consumed), consumed


def allocate_portfolio(planet_sets: list[set[str]],
                       products: list[tuple[str, dict[str, float], float]],
                       rate: float, slots: int):
    """Fill up to ``slots`` planets with the best mix of products (each priced).
    Greedily commits the most ISK/planet product available each round, so all
    affordable slots are used. Returns (total_isk_hour, planets_used, mix) where
    mix is [(product, planets_used, isk_hour), ...] sorted by value."""
    free = set(range(len(planet_sets)))
    budget_total = min(slots, len(planet_sets))
    total_value = 0.0
    total_used = 0
    agg: dict[str, list] = {}   # name -> [used, value]

    while total_used < budget_total:
        best = None  # (eff, value, used, name, consumed)
        for name, demand, price in products:
            pph, used, consumed = _alloc_one(planet_sets, free, demand, rate,
                                             budget_total - total_used)
            if used == 0 or pph <= 0:
                continue
            value = pph * price
            eff = value / used
            if best is None or (eff, value) > (best[0], best[1]):
                best = (eff, value, used, name, consumed)
        if best is None:
            break
        _, value, used, name, consumed = best
        for i in consumed:
            free.discard(i)
        total_value += value
        total_used += used
        a = agg.setdefault(name, [0, 0.0])
        a[0] += used
        a[1] += value

    mix = sorted(((n, agg[n][0], agg[n][1]) for n in agg), key=lambda x: -x[2])
    return total_value, total_used, mix


@dataclass
class SystemScore:
    system_name: str
    band: str
    jumps: int
    isk_per_hour: float
    isk_per_planet: float
    planets_used: int
    mix: list = field(default_factory=list)   # [(product, planets, isk_hr)]

    @property
    def mix_label(self) -> str:
        parts = [f"{n} x{u}" for n, u, _ in self.mix[:3]]
        if len(self.mix) > 3:
            parts.append(f"+{len(self.mix) - 3} more")
        return ", ".join(parts) if parts else "-"


def score_system(planet_sets, products, prices, basis, rate, slots):
    """Best slot-filling product mix for one system. ``products`` is the tier's
    [(name, demand)]; ``prices`` maps name -> object with ``.price(basis)``."""
    priced = []
    for name, demand in products:
        mp = prices.get(name)
        up = mp.price(basis) if mp else None
        if up:
            priced.append((name, demand, up))
    if not priced:
        return None
    total_value, used, mix = allocate_portfolio(planet_sets, priced, rate, slots)
    if used == 0 or total_value <= 0:
        return None
    return total_value, used, mix


def rank_systems(db: PIDatabase, staging_id: int, max_jumps: int,
                 bands: set[str], tier: int, prices, basis: str,
                 rate: float, slots: int,
                 products: list[tuple[str, dict[str, float]]] | None = None
                 ) -> list[SystemScore]:
    if products is None:
        products = tier_products(db, tier)

    dist = db.systems_within_range(staging_id, max_jumps)
    info = db.systems_info(list(dist.keys()))
    in_range = {sid: s for sid, s in info.items() if s.band in bands}

    planets = db.planets_in_systems(list(in_range.keys()))
    by_sys: dict[int, list] = {}
    for p in planets:
        by_sys.setdefault(p.system_id, []).append(p)

    results: list[SystemScore] = []
    for sid, sysm in in_range.items():
        planet_sets = [set(PLANET_RESOURCES.get(p.planet_type, ()))
                       for p in by_sys.get(sid, [])]
        if not planet_sets:
            continue
        scored = score_system(planet_sets, products, prices, basis, rate, slots)
        if scored is None:
            continue
        total_value, used, mix = scored
        results.append(SystemScore(sysm.name, sysm.band, dist[sid],
                                   total_value, total_value / used, used, mix))

    results.sort(key=lambda r: (-r.isk_per_hour, -r.isk_per_planet))
    return results
