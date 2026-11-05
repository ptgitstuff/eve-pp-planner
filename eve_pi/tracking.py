"""Records of extractor programs, what your planets can produce in a time window,
and how to rebalance them for a plan (no Tkinter).

ESI can't say how much was really extracted, hauled or consumed, so history means
the programs that were installed, and their output comes from the prediction in
``extraction.py``. ESI only shows each extractor's latest program, so the record
grows from the first read onward, and programs replaced between reads are missed.
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .colonies import CharacterPI, RUNNING
from .db import PIDatabase
from .extraction import program_outputs, repeated_intervals, spread, total_within
from .facilities import FacilityRate
from .pi_data import P0_TO_P1
from .production import ORDER_PRODUCT, Recipe
from .settings import load_json, save_json


# ------------------------------------------------------------------- history
@dataclass
class ProgramRecord:
    character_id: int
    planet_id: int
    pin_id: int
    product: str
    qty: int
    cycle_seconds: int
    cycles: int
    install: str
    expiry: str


def history_path(folder: str) -> str:
    return os.path.join(folder, "production_history.json")


def load_history(path: str) -> dict[str, ProgramRecord]:
    """Saved records, skipping any that are damaged."""
    records: dict[str, ProgramRecord] = {}
    for key, value in load_json(path).items():
        try:
            record = ProgramRecord(**value)
            datetime.fromisoformat(record.install)
            datetime.fromisoformat(record.expiry)
        except (TypeError, ValueError):
            continue
        if record.cycle_seconds > 0 and record.cycles > 0:
            records[key] = record
    return records


def save_history(path: str, records: dict[str, ProgramRecord]) -> None:
    save_json(path, {key: asdict(value) for key, value in records.items()})


def record_programs(records: dict[str, ProgramRecord],
                    characters: list[CharacterPI]) -> int:
    """Add each extractor's current program, once. Returns how many are new."""
    added = 0
    for character in characters:
        for colony in character.colonies:
            for extractor in colony.extractors:
                if not (extractor.product and extractor.install and extractor.expires
                        and extractor.qty and extractor.cycles):
                    continue
                install = extractor.install.isoformat()
                key = f"{character.id}:{colony.planet_id}:{extractor.pin_id}:{install}"
                if key not in records:
                    added += 1
                    records[key] = ProgramRecord(
                        character.id, colony.planet_id, extractor.pin_id, extractor.product,
                        extractor.qty, extractor.cycle_seconds, extractor.cycles,
                        install, extractor.expires.isoformat())
    return added


def _hours(when: datetime, zero: datetime) -> float:
    return (when - zero).total_seconds() / 3600.0


def record_intervals(records: list[ProgramRecord], zero: datetime) -> list:
    """Each recorded cycle as (start hour, end hour, units). A program replaced
    early stops where the next one on that extractor began."""
    by_pin: dict[tuple, list[ProgramRecord]] = {}
    for record in records:
        by_pin.setdefault((record.character_id, record.planet_id, record.pin_id),
                          []).append(record)
    intervals = []
    for programs in by_pin.values():
        programs.sort(key=lambda p: datetime.fromisoformat(p.install))
        for position, program in enumerate(programs):
            start = datetime.fromisoformat(program.install)
            stop = datetime.fromisoformat(program.expiry)
            if position + 1 < len(programs):
                stop = min(stop, datetime.fromisoformat(programs[position + 1].install))
            length = timedelta(seconds=program.cycle_seconds)
            for index, units in enumerate(program_outputs(
                    program.qty, program.cycle_seconds, program.cycles)):
                begin = start + length * index
                if begin + length > stop:
                    break
                intervals.append((_hours(begin, zero), _hours(begin + length, zero), units))
    return intervals


@dataclass
class RecordCurve:
    start: datetime
    end: datetime
    per_hour: list[float]
    total: float
    programs: int


def record_curve(records: list[ProgramRecord], now: datetime,
                 buckets: int = 120) -> RecordCurve | None:
    """Predicted output of the recorded programs, from the earliest until now."""
    if not records:
        return None
    start = min(datetime.fromisoformat(r.install) for r in records)
    intervals = record_intervals(records, start)
    span = max(_hours(now, start), 1.0)
    return RecordCurve(start, now, spread(intervals, 0.0, span, buckets),
                       total_within(intervals, 0.0, span), len(records))


# -------------------------------------------------------------------- window
@dataclass
class ResourceSupply:
    name: str
    planets: int = 0
    per_hour: float = 0.0            # averaged over the window
    window_total: float = 0.0
    refine_limit: float | None = None    # most these planets' basic factories can take
    usable_per_hour: float = 0.0     # after that limit, planet by planet
    curve: list[float] = field(default_factory=list)


@dataclass
class WindowEstimate:
    hours: float
    curve: list[float]
    resources: list[ResourceSupply]
    extractors: int
    running: int
    total: float
    oldest_update: datetime | None

    @property
    def per_hour(self) -> float:
        return self.total / self.hours if self.hours else 0.0


def refine_rates(db: PIDatabase, rates: dict[str, FacilityRate]) -> dict[str, float]:
    """Most raw material per hour one basic factory takes, for each resource."""
    outputs = db.schematic_outputs_q()
    limits: dict[str, float] = {}
    for resource, product in P0_TO_P1.items():
        type_id = db.type_id_by_name(product)
        rate = rates.get(product)
        if type_id not in outputs or rate is None:
            continue
        schematic, out_qty = outputs[type_id]
        for _input, in_qty in db.schematic_inputs_q(schematic):
            limits[resource] = rate.out_per_hour * in_qty / (out_qty or 1)
    return limits


def estimate_window(characters: list[CharacterPI], hours: float,
                    refine: dict[str, float], buckets: int = 120) -> WindowEstimate:
    """Output over ``hours`` if every extractor starts its latest program now and
    restarts it whenever it ends, whether or not it's running today."""
    everything: list = []
    by_resource: dict[str, list] = {}
    supply: dict[str, ResourceSupply] = {}
    extractors = running = 0
    oldest = None
    for character in characters:
        for colony in character.colonies:
            if colony.last_update and (oldest is None or colony.last_update < oldest):
                oldest = colony.last_update
            on_planet: dict[str, float] = {}
            for extractor in colony.extractors:
                if not (extractor.product and extractor.qty and extractor.cycles):
                    continue
                extractors += 1
                running += extractor.status is RUNNING
                intervals = repeated_intervals(extractor.qty, extractor.cycle_seconds,
                                               extractor.cycles, hours)
                everything += intervals
                by_resource.setdefault(extractor.product, []).extend(intervals)
                on_planet[extractor.product] = (on_planet.get(extractor.product, 0.0)
                                                + total_within(intervals, 0.0, hours))
            factories = {group.product: group.count for group in colony.factories}
            for resource, units in on_planet.items():
                row = supply.setdefault(resource, ResourceSupply(resource))
                row.planets += 1
                row.window_total += units
                rate = units / hours if hours else 0.0
                count = factories.get(P0_TO_P1.get(resource, ""), 0)
                # With no basic factories here, the raw material is shipped elsewhere.
                if count and resource in refine:
                    limit = count * refine[resource]
                    row.refine_limit = (row.refine_limit or 0.0) + limit
                    row.usable_per_hour += min(rate, limit)
                else:
                    row.usable_per_hour += rate
    for row in supply.values():
        row.per_hour = row.window_total / hours if hours else 0.0
        row.curve = spread(by_resource[row.name], 0.0, hours, max(buckets // 2, 1))
    return WindowEstimate(hours, spread(everything, 0.0, hours, buckets),
                          sorted(supply.values(), key=lambda r: (-r.per_hour, r.name)),
                          extractors, running, total_within(everything, 0.0, hours), oldest)


def planet_rates(characters: list[CharacterPI]) -> dict[str, list[float]]:
    """Units per hour of each resource on each planet extracting it, from the latest
    programs. Stopped ones still count: they show what that planet can do."""
    found: dict[str, list[float]] = {}
    for character in characters:
        for colony in character.colonies:
            on_planet: dict[str, float] = {}
            for extractor in colony.extractors:
                if extractor.product and extractor.per_hour > 0:
                    on_planet[extractor.product] = (on_planet.get(extractor.product, 0.0)
                                                    + extractor.per_hour)
            for name, rate in on_planet.items():
                found.setdefault(name, []).append(rate)
    return found


def average_planet_rate(characters: list[CharacterPI]) -> float | None:
    """Your real units per hour per extractor planet, across all resources."""
    rates = [rate for values in planet_rates(characters).values() for rate in values]
    return sum(rates) / len(rates) if rates else None


# -------------------------------------------------------------------- tuning
@dataclass
class ResourceRow:
    name: str
    planets: int
    proposed: int
    per_planet: float       # units per hour
    measured: bool          # False means the default rate: you don't extract it yet
    per_hour: float
    need_per_hour: float    # for the output the proposed split reaches
    limiting: bool = False

    @property
    def change(self) -> int:
        return self.proposed - self.planets


@dataclass
class FactoryRow:
    name: str
    have: int
    need: int
    need_per_hour: float
    capacity_per_hour: float


@dataclass
class Tuning:
    product: str
    output_now: float       # units of product per hour from today's extractor split
    output_best: float      # the same after rebalancing
    factory_output: float   # the most the factories you have could make
    planets: int            # extractor planets plus free slots
    free_slots: int
    enough_planets: bool
    resources: list[ResourceRow] = field(default_factory=list)
    factories: list[FactoryRow] = field(default_factory=list)
    spare: list[tuple[str, int]] = field(default_factory=list)  # extracted, not needed

    @property
    def is_order(self) -> bool:
        return self.product == ORDER_PRODUCT

    @property
    def changes(self) -> list[ResourceRow]:
        return [row for row in self.resources if row.change]


def rebalance(demand: dict[str, float], rate_of: dict[str, float],
              planets: int) -> dict[str, int]:
    """Share ``planets`` for the most output by always growing whichever resource
    limits it. Output is the smallest of the shares, so this split is the best one."""
    names = sorted(name for name, amount in demand.items() if amount > 0)
    if planets < len(names):
        return {name: 0 for name in names}
    counts = {name: 1 for name in names}

    def share(name: str) -> float:
        return counts[name] * rate_of.get(name, 0.0) / demand[name]

    for _ in range(planets - len(names)):
        counts[min(names, key=share)] += 1
    return counts


def _output(demand: dict[str, float], counts: dict[str, int],
            rate_of: dict[str, float]) -> float:
    return min(counts.get(name, 0) * rate_of.get(name, 0.0) / amount
               for name, amount in demand.items())


def tune(recipe: Recipe, characters: list[CharacterPI], default_rate: float,
         rates: dict[str, FacilityRate]) -> Tuning | None:
    """Compare your extractors and factories with what ``recipe`` needs. Factory
    planets stay as they are; extractor planets and free slots can be reassigned."""
    demand = {name: amount for name, amount in recipe.demand.items() if amount > 0}
    if not demand:
        return None
    measured = planet_rates(characters)
    current = {name: len(values) for name, values in measured.items()}
    factories: dict[str, int] = {}
    extractor_planets = free = 0
    for character in characters:
        free += character.free_slots or 0
        for colony in character.colonies:
            extractor_planets += any(e.product and e.per_hour > 0 for e in colony.extractors)
            for group in colony.factories:
                factories[group.product] = factories.get(group.product, 0) + group.count

    rate_of = {name: (sum(measured[name]) / len(measured[name]) if name in measured
                      else default_rate) for name in demand}
    planets = extractor_planets + free
    proposed = rebalance(demand, rate_of, planets)
    output_now = _output(demand, current, rate_of)
    output_best = _output(demand, proposed, rate_of)
    limiting = min(demand, key=lambda n: current.get(n, 0) * rate_of[n] / demand[n])
    # Measure what you need against the better of the two splits.
    target = max(output_now, output_best)

    rows = [ResourceRow(name, current.get(name, 0), proposed[name], rate_of[name],
                        name in measured, current.get(name, 0) * rate_of[name],
                        demand[name] * target, name == limiting)
            for name in sorted(demand)]

    factory_rows = []
    factory_output = math.inf
    for node in recipe.chain:
        rate = rates.get(node.name)
        if node.is_p0 or node.qty_per_unit <= 0 or rate is None or rate.out_per_hour <= 0:
            continue
        have = factories.get(node.name, 0)
        need_per_hour = node.qty_per_unit * target
        factory_rows.append(FactoryRow(node.name, have,
                                       math.ceil(need_per_hour / rate.out_per_hour),
                                       need_per_hour, have * rate.out_per_hour))
        factory_output = min(factory_output, have * rate.out_per_hour / node.qty_per_unit)
    factory_rows.sort(key=lambda r: (r.have >= r.need, r.name))
    spare = sorted((name, count) for name, count in current.items() if name not in demand)
    return Tuning(recipe.product, output_now, output_best,
                  0.0 if factory_output == math.inf else factory_output,
                  planets, free, planets >= len(demand), rows, factory_rows, spare)
