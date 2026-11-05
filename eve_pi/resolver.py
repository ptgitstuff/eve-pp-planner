"""Resolve a finished PI product down to the raw P0 resources it requires.

Walks the schematic graph (``planetSchematicsTypeMap``) recursively: the
schematic that outputs the product defines its inputs, each input is resolved
in turn, until everything bottoms out at P0 raw materials. From the P0 set we
derive which planet types are needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import PIDatabase
from .pi_data import P0_RESOURCES, resources_to_planet_types


@dataclass
class Resolution:
    product: str
    raw_resources: set[str] = field(default_factory=set)   # P0 names
    # P0 resource -> planet types that can yield it
    resource_planets: dict[str, set[str]] = field(default_factory=dict)
    # ordered tiers of intermediates for display: [{names at P1}, {P2}, ...]
    tiers: list[set[str]] = field(default_factory=list)

    @property
    def required_planet_types(self) -> set[str]:
        out: set[str] = set()
        for pts in self.resource_planets.values():
            out |= pts
        return out


def resolve_product(db: PIDatabase, product_name: str) -> Resolution:
    """Resolve ``product_name`` to its P0 requirements."""
    p0_ids = {db.type_id_by_name(n): n for n in P0_RESOURCES}
    p0_ids = {tid: name for tid, name in p0_ids.items() if tid is not None}
    p0_id_set = set(p0_ids)

    outputs = db.schematic_outputs()  # output typeID -> schematicID

    target_id = db.type_id_by_name(product_name)
    res = Resolution(product=product_name)
    if target_id is None:
        return res

    raw_ids: set[int] = set()
    # depth tracking for tier display (depth from product)
    depth_of: dict[int, int] = {}

    def walk(type_id: int, depth: int, seen: frozenset[int]) -> None:
        if type_id in seen:
            return  # guard against cycles (shouldn't happen in PI)
        depth_of[type_id] = max(depth_of.get(type_id, 0), depth)
        if type_id in p0_id_set or type_id not in outputs:
            raw_ids.add(type_id)
            return
        for inp in db.schematic_inputs(outputs[type_id]):
            walk(inp, depth + 1, seen | {type_id})

    walk(target_id, 0, frozenset())

    # Keep only genuine P0s (an unknown leaf that isn't a known P0 is ignored
    # for planet mapping but still surfaced as a raw requirement name).
    res.raw_resources = {p0_ids[i] for i in raw_ids if i in p0_ids}
    res.resource_planets = resources_to_planet_types(res.raw_resources)
    return res


def planet_types_resolution(planet_types: set[str]) -> Resolution:
    """Build a Resolution for the 'pick planet types directly' mode."""
    res = Resolution(product="(planet types)")
    # In this mode the requirement *is* the planet types; resources are the
    # union of what those planets yield, but coverage is keyed on planet type.
    res.resource_planets = {pt: {pt} for pt in planet_types}
    return res
