"""Work out how to fulfil a shopping list with the planets you have.

Goal: finish the whole order as quickly as possible with the available slots.

  1. ``build_bill`` totals the raw (P0) units and advanced facility-hours needed.
  2. ``allocate`` splits the slots. Finish time is set by whichever is slowest:
     one raw resource, or the factory planets. Each spare slot goes to the
     current slowest, which is the best possible split for a finish time set by
     a maximum like this.
  3. ``choose_planets`` places those planets in as few in-range systems as it can.
  4. ``split_characters`` shares them out, keeping each system's planets together.

``order_plan`` turns the result into the ``Plan`` the other results tabs display.

Richness isn't in the SDE, so everything uses the user's nominal P0/hr rate.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from .db import PIDatabase, Planet, System
from .facilities import FacilityRate, facility_rates
from .optimizer import Solution
from .pi_data import P0_RESOURCES, PLANET_RESOURCES, resources_to_planet_types
from .planner import ChosenSystem, Option, Plan
from .production import ORDER_PRODUCT, ChainNode, Recipe, build_report, recipe_demand
from .resolver import Resolution

FACTORIES = "Factories"


@dataclass
class Bill:
    items: dict[str, int] = field(default_factory=dict)      # PI items ordered
    not_pi: list[str] = field(default_factory=list)          # items we can't make
    raw_units: dict[str, float] = field(default_factory=dict)
    intermediates: dict[str, float] = field(default_factory=dict)
    advanced_hours: dict[str, float] = field(default_factory=dict)
    # Most P0/hour one basic facility can refine, per resource.
    refine_per_facility: dict[str, float] = field(default_factory=dict)

    @property
    def total_advanced_hours(self) -> float:
        return sum(self.advanced_hours.values())


def build_bill(db: PIDatabase, items: dict[str, int],
               rates: dict[str, FacilityRate] | None = None) -> Bill:
    """Total everything needed to make ``items``."""
    rates = rates if rates is not None else facility_rates(db)
    outputs = db.schematic_outputs_q()
    bill = Bill()

    for name, amount in items.items():
        type_id = db.type_id_by_name(name)
        canonical = (db.type_name_by_id(type_id) if type_id is not None else None) or name
        recipe = recipe_demand(db, canonical)
        if not recipe.demand:
            bill.not_pi.append(name)
            continue
        bill.items[canonical] = bill.items.get(canonical, 0) + amount
        for node in recipe.chain:
            units = node.qty_per_unit * amount
            if node.is_p0:
                bill.raw_units[node.name] = bill.raw_units.get(node.name, 0.0) + units
                continue
            bill.intermediates[node.name] = bill.intermediates.get(node.name, 0.0) + units
            rate = rates.get(node.name)
            if rate is None or rate.out_per_hour <= 0:
                continue
            if not rate.is_basic:
                bill.advanced_hours[node.name] = (bill.advanced_hours.get(node.name, 0.0)
                                                  + units / rate.out_per_hour)
                continue
            schematic, out_qty = outputs[db.type_id_by_name(node.name)]
            for input_id, in_qty in db.schematic_inputs_q(schematic):
                input_name = db.type_name_by_id(input_id)
                if input_name in P0_RESOURCES:
                    bill.refine_per_facility[input_name] = (
                        rate.out_per_hour * in_qty / (out_qty or 1))
    return bill


@dataclass
class Allocation:
    extractors: dict[str, int] = field(default_factory=dict)
    factories: int = 0
    hours: float = math.inf
    bottleneck: str | None = None
    resource_hours: dict[str, float] = field(default_factory=dict)
    factory_hours: float = 0.0
    slots: int = 0
    min_slots: int = 0
    feasible: bool = False
    limited_by_planets: bool = False     # ran out of suitable planets in range

    @property
    def planets_used(self) -> int:
        return sum(self.extractors.values()) + self.factories

    @property
    def free_slots(self) -> int:
        return max(self.slots - self.planets_used, 0)


class _PlanetSupply:
    """Tracks which planet types serve which resource, moving earlier choices
    around when that makes room for another planet."""

    def __init__(self, limits: dict[str, int]):
        self.free = dict(limits)
        self.total = sum(limits.values())
        self.using: dict[str, Counter] = {}

    def _add(self, resource: str, seen: set[str]) -> bool:
        seen.add(resource)
        types = [t for t, yields in PLANET_RESOURCES.items() if resource in yields]
        for planet_type in types:
            if self.free.get(planet_type, 0) > 0:
                self.free[planet_type] -= 1
                self.using.setdefault(resource, Counter())[planet_type] += 1
                return True
        for planet_type in types:
            for other, used in self.using.items():
                if other not in seen and used[planet_type] > 0 and self._add(other, seen):
                    used[planet_type] -= 1
                    self.using.setdefault(resource, Counter())[planet_type] += 1
                    return True
        return False

    def add(self, resource: str) -> bool:
        return self._add(resource, set())


def allocate(bill: Bill, rate: float, slots: int, basic_per: int, adv_per: int,
             limits: dict[str, int] | None = None) -> Allocation:
    """Split ``slots`` between extractor and factory planets to finish soonest.
    ``limits`` caps planets of each type (what's in range); None means no cap."""
    resources = sorted(bill.raw_units)
    factory_work = bill.total_advanced_hours
    alloc = Allocation(slots=slots,
                       min_slots=len(resources) + (1 if factory_work > 0 else 0))
    if not resources or rate <= 0:
        return alloc

    def planet_rate(resource: str) -> float:
        refine = bill.refine_per_facility.get(resource)
        return min(rate, refine * basic_per) if refine else rate

    supply = _PlanetSupply(limits) if limits is not None else None

    def add_extractor(resource: str) -> bool:
        if supply is not None and not supply.add(resource):
            return False
        alloc.extractors[resource] = alloc.extractors.get(resource, 0) + 1
        return True

    def room_for_factory() -> bool:
        return supply is None or alloc.planets_used < supply.total

    if slots >= alloc.min_slots:
        placed = all(add_extractor(r) for r in resources)
        if factory_work > 0:
            placed = placed and room_for_factory()
            if placed:
                alloc.factories = 1
        alloc.feasible = placed
        alloc.limited_by_planets = not placed

    def time_for(name: str) -> float:
        if name == FACTORIES:
            return factory_work / (alloc.factories * adv_per) if alloc.factories else math.inf
        count = alloc.extractors.get(name, 0)
        return bill.raw_units[name] / (count * planet_rate(name)) if count else math.inf

    names = resources + ([FACTORIES] if factory_work > 0 else [])
    while alloc.feasible and alloc.planets_used < slots:
        slowest = max(names, key=time_for)
        if slowest == FACTORIES:
            added = room_for_factory()
            if added:
                alloc.factories += 1
        else:
            added = room_for_factory() and add_extractor(slowest)
        if not added:          # the slowest can't grow, so nothing else helps
            alloc.limited_by_planets = True
            break

    if alloc.feasible:
        alloc.resource_hours = {r: time_for(r) for r in resources}
        alloc.factory_hours = time_for(FACTORIES) if factory_work > 0 else 0.0
        alloc.bottleneck = max(names, key=time_for)
        alloc.hours = time_for(alloc.bottleneck)
    return alloc


@dataclass
class Placement:
    planet: Planet
    system: System
    jumps: int
    resource: str | None     # None means a factory planet


def _match_system(planets: list[Planet], need: dict[str, int]) -> dict[int, str]:
    """Most extractor needs this system's planets can fill: planet id -> resource."""
    holders: dict[str, list[int]] = {}
    assigned: dict[int, str] = {}

    def place(index: int, seen: set[str]) -> bool:
        yields = PLANET_RESOURCES.get(planets[index].planet_type, ())
        options = [r for r in yields if need.get(r, 0) > 0]
        for resource in options:
            if len(holders.get(resource, ())) < need[resource]:
                holders.setdefault(resource, []).append(index)
                assigned[index] = resource
                return True
        for resource in options:
            if resource in seen:
                continue
            seen.add(resource)
            for position, other in enumerate(holders.get(resource, ())):
                if place(other, seen):
                    holders[resource][position] = index
                    assigned[index] = resource
                    return True
        return False

    for index in range(len(planets)):
        place(index, set())
    return {planets[i].id: r for i, r in assigned.items()}


def choose_planets(alloc: Allocation, systems: Iterable[tuple[System, int, list[Planet]]]
                   ) -> list[Placement]:
    """Place the allocation in as few systems as possible: repeatedly take the
    system that fills the most remaining planets, the nearest on a tie."""
    need = dict(alloc.extractors)
    factories = alloc.factories
    remaining = list(systems)
    placements: list[Placement] = []

    while (any(need.values()) or factories > 0) and remaining:
        best = None
        for entry in remaining:
            system, jumps, planets = entry
            matched = _match_system(planets, need)
            spare = len(planets) - len(matched)
            score = len(matched) + min(spare, factories)
            key = (score, -jumps, system.name)
            if score > 0 and (best is None or (key[0], key[1]) > (best[0][0], best[0][1])
                              or (key[0], key[1]) == (best[0][0], best[0][1])
                              and key[2] < best[0][2]):
                best = (key, entry, matched)
        if best is None:
            break
        _, entry, matched = best
        remaining.remove(entry)
        system, jumps, planets = entry
        for planet in planets:
            resource = matched.get(planet.id)
            if resource is not None:
                need[resource] -= 1
                placements.append(Placement(planet, system, jumps, resource))
            elif factories > 0:
                factories -= 1
                placements.append(Placement(planet, system, jumps, None))
    return placements


def _system_order(placements: list[Placement]) -> list[Placement]:
    return sorted(placements, key=lambda p: (
        p.jumps, p.system.name, p.resource is None, p.resource or "", p.planet.name))


def split_characters(placements: list[Placement], per_character: int
                     ) -> list[list[Placement]]:
    """Share planets across characters, keeping each system's planets together."""
    ordered = _system_order(placements)
    return [ordered[i:i + per_character]
            for i in range(0, len(ordered), per_character)]


@dataclass
class CharacterSlots:
    """A real character: how many colonies it can still add, and where it already
    has one (a character can't put a second command center on a planet)."""
    name: str
    free: int
    existing: frozenset[int] = frozenset()


def split_roster(placements: list[Placement], roster: list[CharacterSlots]
                 ) -> tuple[list[tuple[str, list[Placement]]], list[Placement]]:
    """Give each planet to a character with room that isn't already on it, moving
    earlier choices around when that makes room. Most free slots go first, so as
    few characters as possible are used. Returns ((name, planets) for characters
    given any, planets nobody can take)."""
    people = sorted(roster, key=lambda c: -c.free)
    holders: list[list[Placement]] = [[] for _ in people]

    def assign(placement: Placement, seen: set[int]) -> bool:
        allowed = [i for i, person in enumerate(people)
                   if placement.planet.id not in person.existing]
        for i in allowed:
            if len(holders[i]) < people[i].free:
                holders[i].append(placement)
                return True
        for i in allowed:
            if i in seen:
                continue
            seen.add(i)
            for position, other in enumerate(holders[i]):
                if assign(other, seen):
                    holders[i][position] = placement
                    return True
        return False

    unassigned = [p for p in _system_order(placements) if not assign(p, set())]
    named = [(person.name, _system_order(held))
             for person, held in zip(people, holders) if held]
    return named, unassigned


def _roster_slots(roster: list[CharacterSlots], count: int, spare_per: int) -> int:
    """Slots from the ``count`` characters with most free, then new characters."""
    frees = sorted((c.free for c in roster), reverse=True)
    return sum(frees[:count]) + max(count - len(frees), 0) * spare_per


@dataclass
class Scenario:
    characters: int
    slots: int
    hours: float
    feasible: bool


@dataclass
class FulfilPlan:
    bill: Bill
    allocation: Allocation
    placements: list[Placement] = field(default_factory=list)
    by_character: list[list[Placement]] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    characters: int = 0
    per_character: int = 0
    rate: float = 0.0
    systems_in_range: int = 0
    staging: System | None = None
    max_jumps: int = 0
    bands: set[str] = field(default_factory=set)
    candidates: list[tuple[System, int, list[Planet]]] = field(default_factory=list)
    roster: list[CharacterSlots] | None = None     # None means typed-in numbers
    character_names: list[str] = field(default_factory=list)  # one per by_character
    unassigned: list[Placement] = field(default_factory=list)

    @property
    def systems_used(self) -> int:
        return len({p.system.id for p in self.placements})


def fulfil_order(db: PIDatabase, items: dict[str, int], staging_name: str,
                 max_jumps: int, bands: set[str], characters: int, per_character: int,
                 rate: float, basic_per: int, adv_per: int,
                 roster: list[CharacterSlots] | None = None) -> FulfilPlan:
    """Plan the whole order from the staging system. ``roster`` gives real
    characters and replaces ``characters``; what-ifs past the roster assume new
    characters with ``per_character`` planets. Raises ValueError for an unknown
    staging system."""
    staging = db.get_system(staging_name)
    if staging is None:
        raise ValueError(f"Unknown system: {staging_name!r}")

    bill = build_bill(db, items)
    distance = db.systems_within_range(staging.id, max_jumps)
    info = {sid: s for sid, s in db.systems_info(list(distance)).items()
            if s.band in bands}
    by_system: dict[int, list[Planet]] = {}
    limits: Counter = Counter()
    for planet in db.planets_in_systems(list(info)):
        by_system.setdefault(planet.system_id, []).append(planet)
        limits[planet.planet_type] += 1
    systems = [(info[sid], distance[sid], planets)
               for sid, planets in by_system.items()]

    if roster is not None:
        roster = [c for c in roster if c.free > 0]
        characters = len(roster)
        slots = sum(c.free for c in roster)
    else:
        slots = characters * per_character
    alloc = allocate(bill, rate, slots, basic_per, adv_per, dict(limits))
    plan = FulfilPlan(bill, alloc, characters=characters, per_character=per_character,
                      rate=rate, systems_in_range=len(info), staging=staging,
                      max_jumps=max_jumps, bands=set(bands), candidates=systems,
                      roster=roster)
    if alloc.feasible:
        plan.placements = choose_planets(alloc, systems)
        if roster is None:
            plan.by_character = split_characters(plan.placements, per_character)
            plan.character_names = [f"Character {i + 1}"
                                    for i in range(len(plan.by_character))]
        else:
            named, plan.unassigned = split_roster(plan.placements, roster)
            plan.character_names = [name for name, _ in named]
            plan.by_character = [held for _, held in named]

    upper = min(max(characters + 3, 6), 12)
    for count in range(1, upper + 1):
        count_slots = (count * per_character if roster is None
                       else _roster_slots(roster, count, per_character))
        what_if = allocate(bill, rate, count_slots, basic_per, adv_per, dict(limits))
        plan.scenarios.append(Scenario(count, count_slots, what_if.hours,
                                       what_if.feasible))
    return plan


def _tier_of(db: PIDatabase):
    """A function giving an item's PI tier: P0 is 0, anything made is one above
    its highest input."""
    outputs = db.schematic_outputs_q()
    memo: dict[int, int] = {}

    def tier(type_id: int | None) -> int:
        if type_id is None or type_id not in outputs:
            return 0
        if type_id not in memo:
            memo[type_id] = 1           # guards against a cycle in the data
            inputs = db.schematic_inputs_q(outputs[type_id][0])
            memo[type_id] = 1 + max((tier(i) for i, _ in inputs), default=0)
        return memo[type_id]

    return lambda name: tier(db.type_id_by_name(name))


def _order_recipe(db: PIDatabase, bill: Bill) -> Recipe:
    """The order as one recipe, where one unit is the whole order. Depth is set so
    ``build_breakdown``, which counts tiers up from the deepest node, reads true
    tiers even though the items ordered can sit at different tiers."""
    tier = _tier_of(db)
    chain = [ChainNode(name, units, 4, True) for name, units in bill.raw_units.items()]
    chain += [ChainNode(name, units, 4 - tier(name), False)
              for name, units in bill.intermediates.items()]
    chain.sort(key=lambda node: (-node.depth, node.name))
    return Recipe(ORDER_PRODUCT, dict(bill.raw_units), chain)


def order_plan(db: PIDatabase, fp: FulfilPlan) -> Plan:
    """Express an optimised order as the ``Plan`` the results tabs display. An
    order that can't be placed still lists its options, with nothing chosen. An
    order with no PI items has no requirements and no solution."""
    requirements = sorted(fp.bill.raw_units)
    resource_planets = resources_to_planet_types(set(requirements))
    resolution = Resolution(ORDER_PRODUCT, set(requirements), resource_planets)
    plan = Plan(staging=fp.staging, max_jumps=fp.max_jumps, bands=set(fp.bands),
                resolution=resolution, requirements=requirements,
                in_range_systems=fp.systems_in_range, solution=None,
                order_items=dict(fp.bill.items))
    if not requirements:
        return plan

    per_requirement: dict[str, list[Option]] = {r: [] for r in requirements}
    for system, jumps, planets in fp.candidates:
        for planet in planets:
            for resource in requirements:
                if planet.planet_type in resource_planets[resource]:
                    per_requirement[resource].append(Option(system, jumps, planet))
    for options in per_requirement.values():
        options.sort(key=lambda o: (o.jumps, o.system.name, o.planet.name))
    plan.per_requirement = per_requirement
    plan.unmet = [r for r in requirements if not per_requirement[r]]

    chosen: dict[int, ChosenSystem] = {}
    counts: Counter = Counter()
    for placement in fp.placements:
        entry = chosen.setdefault(placement.system.id,
                                  ChosenSystem(placement.system, placement.jumps))
        if placement.resource is None:
            entry.factories.append(placement.planet)
        else:
            entry.assignments.append((placement.resource, placement.planet))
            counts[placement.resource] += 1
    plan.chosen = sorted(chosen.values(), key=lambda c: (c.jumps, c.system.name))
    plan.solution = Solution(system_ids=[c.system.id for c in plan.chosen],
                             total_jumps=sum(c.jumps for c in plan.chosen),
                             size=len(plan.chosen))

    recipe = _order_recipe(db, fp.bill)
    plan.production = build_report(
        [r for r in requirements if per_requirement[r]], recipe.demand, dict(counts),
        slots_requested=fp.allocation.slots, recipe=recipe)
    return plan
