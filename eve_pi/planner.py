"""High-level planning: turn a staging system + desired output + range into a
concrete set-up plan."""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import PIDatabase, Planet, System
from .optimizer import Candidate, Solution, min_set_cover
from .production import (ProductionReport, Recipe, allocate_slots, build_report,
                        recipe_demand)
from .resolver import Resolution, planet_types_resolution, resolve_product


@dataclass
class Option:
    system: System
    jumps: int
    planet: Planet


@dataclass
class ChosenSystem:
    system: System
    jumps: int
    assignments: list[tuple[str, Planet]] = field(default_factory=list)
    relevant_planets: list[Planet] = field(default_factory=list)
    factories: list[Planet] = field(default_factory=list)   # order plans only


@dataclass
class AltSolution:
    systems: list[System]
    total_jumps: int


@dataclass
class Plan:
    staging: System
    max_jumps: int
    bands: set[str]
    resolution: Resolution
    requirements: list[str]
    in_range_systems: int
    solution: Solution | None
    chosen: list[ChosenSystem] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    alt_solutions: list[AltSolution] = field(default_factory=list)
    per_requirement: dict[str, list[Option]] = field(default_factory=dict)
    production: ProductionReport | None = None
    single_system: bool = False       # was single-system mode requested?
    single_system_ok: bool = True     # could it be honoured in range?
    order_items: dict[str, int] = field(default_factory=dict)  # set for order plans

    @property
    def ok(self) -> bool:
        return self.solution is not None and not self.unmet


def make_plan(
    db: PIDatabase,
    staging_name: str,
    max_jumps: int,
    bands: set[str],
    *,
    product: str | None = None,
    planet_types: set[str] | None = None,
    slots: int | None = None,
    single_system: bool = False,
) -> Plan:
    staging = db.get_system(staging_name)
    if staging is None:
        raise ValueError(f"Unknown system: {staging_name!r}")

    if product:
        resolution = resolve_product(db, product)
    elif planet_types:
        resolution = planet_types_resolution(set(planet_types))
    else:
        raise ValueError("Specify either a product or a set of planet types.")

    resource_planets = resolution.resource_planets
    requirements = sorted(resource_planets.keys())

    dist = db.systems_within_range(staging.id, max_jumps)
    info = db.systems_info(list(dist.keys()))
    in_range: dict[int, System] = {
        sid: sys for sid, sys in info.items() if sys.band in bands
    }

    plan = Plan(
        staging=staging, max_jumps=max_jumps, bands=set(bands),
        resolution=resolution, requirements=requirements,
        in_range_systems=len(in_range), solution=None,
        single_system=single_system,
    )
    if not requirements:
        return plan

    planets = db.planets_in_systems(list(in_range.keys()))
    planets_by_system: dict[int, list[Planet]] = {}
    for p in planets:
        planets_by_system.setdefault(p.system_id, []).append(p)

    def covers(planet_types_present: set[str]) -> set[str]:
        return {req for req, types in resource_planets.items()
                if planet_types_present & types}

    per_req: dict[str, list[Option]] = {r: [] for r in requirements}
    for sid, sys in in_range.items():
        for p in planets_by_system.get(sid, ()):
            for req, types in resource_planets.items():
                if p.planet_type in types:
                    per_req[req].append(Option(sys, dist[sid], p))
    for req in per_req:
        per_req[req].sort(key=lambda o: (o.jumps, o.system.name))
    plan.per_requirement = per_req

    candidates: list[Candidate] = []
    for sid, sys in in_range.items():
        ptypes = {p.planet_type for p in planets_by_system.get(sid, ())}
        cov = covers(ptypes)
        if cov:
            candidates.append(Candidate(sid, frozenset(cov), dist[sid]))

    universe = set(requirements)
    coverable = set().union(*(c.covered for c in candidates)) if candidates else set()
    plan.unmet = sorted(universe - coverable)

    solution = min_set_cover(universe & coverable, candidates)
    plan.solution = solution
    if solution is None:
        return plan

    coverable_reqs = [r for r in requirements if r in coverable]
    base_cover = set(solution.system_ids)
    required_set = universe & coverable

    # --- single-system preference ----------------------------------------
    # A system can host the whole chain (extract -> P1 -> P2, factory planets
    # for P3/P4 are in-system) iff its own planets cover every required P0.
    single_target: int | None = None
    if single_system:
        self_suff = [
            sid for sid, sys in in_range.items()
            if required_set <= covers({p.planet_type
                                       for p in planets_by_system.get(sid, ())})
        ]
        plan.single_system_ok = bool(self_suff)
        if self_suff:
            single_target = min(self_suff, key=lambda s: (dist[s], in_range[s].name))
    # if single_system requested but none qualifies, single_target stays None
    # and we fall back to the fewest-systems assignment below.

    recipe: Recipe | None = None
    demand: dict[str, float] | None = None
    if product:
        recipe = recipe_demand(db, product)
        demand = recipe.demand or None

    if slots and slots > 0:
        target_alloc = allocate_slots(coverable_reqs, demand, slots)
    else:
        target_alloc = {r: 1 for r in coverable_reqs}

    chosen_map: dict[int, ChosenSystem] = {}
    used_planets: set[int] = set()
    assigned: dict[str, int] = {r: 0 for r in coverable_reqs}

    def chosen_system(sid: int) -> ChosenSystem:
        cs = chosen_map.get(sid)
        if cs is None:
            cs = ChosenSystem(in_range[sid], dist[sid])
            chosen_map[sid] = cs
        return cs

    def options_for(req: str) -> list[Option]:
        opts = per_req[req]
        if single_target is not None:
            opts = [o for o in opts if o.system.id == single_target]
        return opts

    def planet_key(option: Option) -> tuple:
        # Prefer planets few other resources could use, so shared planets stay
        # free for the resources that need them.
        users = sum(option.planet.planet_type in resource_planets[r]
                    for r in coverable_reqs)
        return (option.system.id not in chosen_map,
                option.system.id not in base_cover,
                option.jumps, option.system.name, users, option.planet.name)

    # Hand out planets one per round, scarcest resource first. Filling each
    # resource in turn would let early ones use up planets that later ones need.
    order = sorted(coverable_reqs,
                   key=lambda r: (len({o.planet.id for o in options_for(r)}), r))
    placed = True
    while placed:
        placed = False
        for req in order:
            if assigned[req] >= target_alloc.get(req, 0):
                continue
            free = [o for o in options_for(req) if o.planet.id not in used_planets]
            if not free:
                continue
            option = min(free, key=planet_key)
            used_planets.add(option.planet.id)
            chosen_system(option.system.id).assignments.append((req, option.planet))
            assigned[req] += 1
            placed = True

    shortfalls = {r: target_alloc[r] - assigned[r]
                  for r in coverable_reqs if assigned[r] < target_alloc[r]}

    ptypes_needed = (set().union(*(resource_planets[r] for r in coverable_reqs))
                     if coverable_reqs else set())
    for sid, cs in chosen_map.items():
        cs.relevant_planets = [p for p in planets_by_system.get(sid, ())
                               if p.planet_type in ptypes_needed]
    plan.chosen = sorted(chosen_map.values(), key=lambda c: (c.jumps, c.system.name))

    plan.production = build_report(
        coverable_reqs, demand, assigned,
        slots_requested=(slots or len(coverable_reqs)),
        recipe=recipe, shortfalls=shortfalls,
    )

    for alt_ids in solution.alternatives:
        syss = [in_range[i] for i in alt_ids if i in in_range]
        plan.alt_solutions.append(
            AltSolution(syss, sum(dist[i] for i in alt_ids if i in dist))
        )

    return plan
