"""Facility & planet-count accounting for a production chain.

Turns a target throughput (final product / hour) into the real number of
facilities and *planets* needed, so plan totals include dedicated factory
planets and true character counts — not just extractor planets.

Model (matches how PI actually runs):
  * P0 -> P1 is done by **basic** industry facilities that live on the
    *extractor* planet. The player's building block here is "2 extractors
    feeding 8 basic factories", i.e. ~8 basic facilities per extractor planet.
  * P1 -> P2 -> P3 -> P4 are done by **advanced** facilities on dedicated
    *factory* planets, fed from the extractors.

Per-facility throughput comes straight from the SDE schematic quantities and
cycle times. Facilities-per-planet are caps the player sets (CPU/PG limited in
game) and default to sensible values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .db import PIDatabase
from .pi_data import P0_RESOURCES
from .production import Recipe

DEFAULT_BASIC_PER_EXTRACTOR = 8
DEFAULT_ADV_PER_FACTORY = 8


@dataclass
class FacilityRate:
    out_per_hour: float   # units produced per facility per hour
    is_basic: bool        # True = P0->P1 (basic, on extractor planet)


def facility_rates(db: PIDatabase) -> dict[str, FacilityRate]:
    """Per-facility output/hour for every PI product, from SDE quantities and
    cycle times, flagged basic (P0->P1) vs advanced (P1+)."""
    cycles = {int(sid): (ct or 3600)
              for sid, ct in db.conn.execute(
                  "SELECT schematicID, cycleTime FROM planetSchematics")}
    outputs = db.schematic_outputs_q()                       # out tid -> (sid, qty)
    p0_ids = {db.type_id_by_name(n) for n in P0_RESOURCES}
    p0_ids.discard(None)

    rates: dict[str, FacilityRate] = {}
    for out_tid, (sid, out_qty) in outputs.items():
        ct = cycles.get(sid, 3600) or 3600
        out_per_hour = out_qty * 3600.0 / ct
        inputs = db.schematic_inputs_q(sid)
        is_basic = bool(inputs) and all(itid in p0_ids for itid, _ in inputs)
        name = db.type_name_by_id(out_tid)
        if name:
            rates[name] = FacilityRate(out_per_hour, is_basic)
    return rates


@dataclass
class FacilityBreakdown:
    product: str
    product_per_hour: float
    extractor_planets: int
    basic_factories_needed: int           # P0->P1, hosted on extractor planets
    advanced_facilities: int              # P1..P4
    advanced_by_tier: dict[int, int] = field(default_factory=dict)  # tier(2..4)->count
    factory_planets: int = 0
    total_planets: int = 0
    characters: int = 0
    basic_per_extractor: int = DEFAULT_BASIC_PER_EXTRACTOR
    adv_per_factory: int = DEFAULT_ADV_PER_FACTORY

    @property
    def basic_capacity(self) -> int:
        return self.extractor_planets * self.basic_per_extractor

    @property
    def basic_over_capacity(self) -> bool:
        return self.basic_factories_needed > self.basic_capacity


def build_breakdown(recipe: Recipe, product_per_hour: float,
                    rates: dict[str, FacilityRate], extractor_planets: int,
                    basic_per_extractor: int = DEFAULT_BASIC_PER_EXTRACTOR,
                    adv_per_factory: int = DEFAULT_ADV_PER_FACTORY
                    ) -> FacilityBreakdown:
    """Facility and planet counts to sustain ``product_per_hour`` of the recipe's
    final product, given the extractor planets already planned."""
    max_depth = max((n.depth for n in recipe.chain), default=0)
    basic = 0
    advanced = 0
    by_tier: dict[int, int] = {}

    for node in recipe.chain:
        if node.is_p0:
            continue
        fr = rates.get(node.name)
        if fr is None or fr.out_per_hour <= 0:
            continue
        need = node.qty_per_unit * product_per_hour
        if need <= 0:
            continue
        fac = math.ceil(need / fr.out_per_hour)
        if fr.is_basic:
            basic += fac
        else:
            advanced += fac
            tier = max_depth - node.depth     # P0=0, P1=1, ... up to the product
            by_tier[tier] = by_tier.get(tier, 0) + fac

    factory_planets = math.ceil(advanced / adv_per_factory) if advanced > 0 else 0
    total = extractor_planets + factory_planets
    characters = math.ceil(total / 6) if total > 0 else 0

    return FacilityBreakdown(
        product=recipe.product, product_per_hour=product_per_hour,
        extractor_planets=extractor_planets, basic_factories_needed=basic,
        advanced_facilities=advanced, advanced_by_tier=by_tier,
        factory_planets=factory_planets, total_planets=total,
        characters=characters, basic_per_extractor=basic_per_extractor,
        adv_per_factory=adv_per_factory)
