"""Extractor output maths, program records, window estimates and rebalancing."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

from eve_pi.colonies import RUNNING, STOPPED, CharacterPI, Colony, Extractor, FactoryGroup
from eve_pi.extraction import (cycle_output, program_average, program_outputs,
                               repeated_intervals, spread, total_within)
from eve_pi.facilities import FacilityRate
from eve_pi.production import ChainNode, Recipe
from eve_pi.tracking import (average_planet_rate, estimate_window, history_path, load_history,
                             planet_rates, rebalance, record_curve, record_intervals,
                             record_programs, save_history, tune)

failures = 0
checks = 0


def check(condition, label):
    global failures, checks
    checks += 1
    if not condition:
        failures += 1
        print("FAIL:", label)


NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)


def extractor(product, qty=1000, cycle=3600, cycles=24, pin=1, install=None, status=RUNNING):
    start = install or NOW - timedelta(hours=1)
    return Extractor(product, program_average(qty, cycle, cycles), 10,
                     start + timedelta(seconds=cycle * cycles), status, pin, qty, cycle, cycles,
                     start)


def colony(planet_id, extractors=(), factories=()):
    return Colony(planet_id, f"Planet {planet_id}", "Alpha", "Barren", 5, NOW,
                  list(extractors), list(factories))


def character(colonies, max_planets=6, char_id=7):
    return CharacterPI(char_id, "Pilot", max_planets, 5, list(colonies))


# ------------------------------------------------------------- output maths
first = cycle_output(1000, 3600, 0)
check(first > 0, "a cycle produces something")
# The base figure is per 15 minutes, so an hour's cycle starts near 4x it, or more
# when the wave is high, and never above the 1.8 peak the routes are sized for.
check(3000 < first <= 4 * 1000 * 1.8, "cycle scale follows cycle length")
check(cycle_output(0, 3600, 0) == 0, "no base means no output, never negative")
outputs = program_outputs(1000, 3600, 200)
check(len(outputs) == 200, "one output per cycle")
check(sum(outputs[-20:]) < sum(outputs[:20]), "output decays over a long program")
check(program_average(1000, 3600, 0) == 0.0, "no cycles averages to zero")
check(abs(program_average(1000, 3600, 24) - sum(program_outputs(1000, 3600, 24)) / 24) < 1e-6,
      "average per hour is total over hours")
check(program_average(1000, 3600, 24) > program_average(1000, 3600, 240),
      "a short program averages more than a long one")
check(program_average(1000, 3600, 24) > 1000,
      "the real rate is above the old qty-per-cycle figure")

# ------------------------------------------------------------------ windows
parts = repeated_intervals(1000, 3600, 4, 10)
check(len(parts) == 10, "program repeats to fill the window")
check(parts[0][2] == parts[4][2], "a restart begins the program again")
check(repeated_intervals(0, 3600, 4, 10) == [], "no base, no intervals")

check(total_within([(0, 2, 100)], 1, 2) == 50, "part of a cycle counts in proportion")
check(total_within([(0, 2, 100)], 5, 6) == 0, "outside the window counts for nothing")
curve = spread([(0, 2, 100)], 0, 4, 4)
check(curve == [50, 50, 0, 0], "rate spread across buckets")
check(abs(sum(spread(parts, 0, 10, 7)) * 10 / 7 - sum(p[2] for p in parts)) < 1e-6,
      "spreading keeps the total")
check(spread([], 0, 0, 4) == [], "empty span")

# ------------------------------------------------------------------ records
records = {}
old_start = NOW - timedelta(hours=100)
first_program = extractor("Base Metals", install=old_start, cycles=24, status=STOPPED)
team = [character([colony(1, [first_program])])]
check(record_programs(records, team) == 1, "new program recorded")
check(record_programs(records, team) == 0, "the same program isn't recorded twice")
no_install = Extractor("Base Metals", 10.0, 2, NOW, RUNNING, 2, 100, 3600, 0, None)
check(record_programs(records, [character([colony(1, [no_install])])]) == 0,
      "a program with no known start isn't recorded")

second_start = old_start + timedelta(hours=10)
replacement = extractor("Base Metals", install=second_start, cycles=5)
record_programs(records, [character([colony(1, [replacement])])])
check(len(records) == 2, "a new install is a new record")
intervals = record_intervals(list(records.values()), old_start)
check(len(intervals) == 15, "a replaced program stops where the next began")
check(intervals[-1][1] == 15, "the last cycle ends with its program")

curve = record_curve(list(records.values()), NOW)
check(curve is not None and curve.programs == 2 and curve.start == old_start, "curve spans records")
check(abs(curve.total - sum(i[2] for i in intervals)) < 1e-6, "curve total matches the cycles")
check(record_curve([], NOW) is None, "no records, no curve")
running_late = {"k": next(iter(records.values()))}
early = record_curve(list(running_late.values()), old_start + timedelta(hours=2))
check(early.total < curve.total, "cycles after now aren't counted")

with tempfile.TemporaryDirectory() as folder:
    path = history_path(folder)
    check(load_history(path) == {}, "no file, no records")
    save_history(path, records)
    check(load_history(path) == records, "records survive a save and load")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"bad": {"nope": 1}, "date": {"character_id": 1, "planet_id": 1, '
                     '"pin_id": 1, "product": "X", "qty": 1, "cycle_seconds": 1, "cycles": 1, '
                     '"install": "soon", "expiry": "later"}}')
    check(load_history(path) == {}, "damaged records are skipped")

# ------------------------------------------------------- estimating a window
team = [character([
    colony(1, [extractor("Base Metals"), extractor("Base Metals", pin=2)],
           [FactoryGroup("Reactive Metals", 1, 40.0)]),
    colony(2, [extractor("Base Metals", status=STOPPED)]),
    colony(3, [extractor("Noble Metals", qty=500)]),
    colony(4, factories=[FactoryGroup("Consumer Electronics", 4, 20.0)]),
])]
estimate = estimate_window(team, 24, {"Base Metals": 100.0})
check(estimate.extractors == 4 and estimate.running == 3, "running and total extractors")
base = next(r for r in estimate.resources if r.name == "Base Metals")
noble = next(r for r in estimate.resources if r.name == "Noble Metals")
check(base.planets == 2, "planets counted once however many extractors they hold")
check(estimate.resources[0] is base, "biggest resource first")
check(abs(estimate.total - base.window_total - noble.window_total) < 1e-6,
      "resources add up to the total")
check(abs(estimate.per_hour - estimate.total / 24) < 1e-6, "average over the window")
check(abs(sum(estimate.curve) * 24 / len(estimate.curve) - estimate.total) < 1e-6,
      "the chart matches the total")
check(base.refine_limit == 100.0, "only the planet with factories has a refine limit")
per_extractor = total_within(repeated_intervals(1000, 3600, 24, 24), 0, 24) / 24
check(abs(base.usable_per_hour - (100.0 + per_extractor)) < 1e-6,
      "refining caps that planet, and shipped material isn't capped")
check(noble.refine_limit is None, "no factories, no refine limit")
check(estimate.oldest_update == NOW, "oldest update reported")
empty = estimate_window([character([])], 24, {})
check(empty.total == 0 and not empty.resources, "no colonies, nothing to estimate")
longer = estimate_window(team, 240, {})
check(longer.per_hour < estimate.per_hour or longer.total > estimate.total,
      "a longer window produces more in total")

# --------------------------------------------------------------- rebalancing
check(rebalance({"A": 1, "B": 1}, {"A": 100, "B": 100}, 4) == {"A": 2, "B": 2}, "even split")
check(rebalance({"A": 2, "B": 1}, {"A": 100, "B": 100}, 3) == {"A": 2, "B": 1},
      "more planets for the bigger need")
check(rebalance({"A": 1, "B": 1}, {"A": 300, "B": 100}, 4) == {"A": 1, "B": 3},
      "more planets for the poorer resource")
check(rebalance({"A": 1, "B": 1}, {"A": 1, "B": 1}, 1) == {"A": 0, "B": 0},
      "too few planets makes nothing")

recipe = Recipe("Thing", {"Base Metals": 10.0, "Noble Metals": 10.0}, [
    ChainNode("Base Metals", 10.0, 2, True),
    ChainNode("Noble Metals", 10.0, 2, True),
    ChainNode("Reactive Metals", 5.0, 1, False),
    ChainNode("Precious Metals", 5.0, 1, False),
])
rates = {"Reactive Metals": FacilityRate(40.0, True), "Precious Metals": FacilityRate(40.0, True)}
team = [character([
    colony(1, [extractor("Base Metals")], [FactoryGroup("Reactive Metals", 8, 320.0)]),
    colony(2, [extractor("Base Metals")]),
    colony(3, [extractor("Base Metals")]),
    colony(4, [extractor("Noble Metals")]),
    colony(5, [extractor("Aqueous Liquids")]),
    colony(6, factories=[FactoryGroup("Consumer Electronics", 4, 20.0)]),
], max_planets=6)]
rate = program_average(1000, 3600, 24)
result = tune(recipe, team, 5000.0, rates)
check(result.planets == 5 and result.free_slots == 0, "factory planets aren't reassigned")
check(result.enough_planets, "enough planets")
row = {r.name: r for r in result.resources}
check(row["Base Metals"].planets == 3 and row["Noble Metals"].planets == 1, "current split")
check(row["Noble Metals"].limiting and not row["Base Metals"].limiting, "the short resource limits")
check(row["Base Metals"].proposed + row["Noble Metals"].proposed == 5, "all planets shared")
check(row["Base Metals"].proposed == 3 and row["Noble Metals"].proposed == 2, "balanced split")
check(row["Noble Metals"].change == 1 and [r.name for r in result.changes] == ["Noble Metals"],
      "changes list what moves")
check(abs(result.output_now - rate / 10) < 1e-6, "output limited by the smallest share")
check(abs(result.output_best - rate * 2 / 10) < 1e-6, "rebalancing raises the output")
check(result.spare == [("Aqueous Liquids", 1)], "extraction this plan doesn't need is listed")
check(row["Base Metals"].measured, "your own rate is used")
check(abs(row["Noble Metals"].need_per_hour - 10 * result.output_best) < 1e-6, "need at best output")

factory = {f.name: f for f in result.factories}
check(factory["Precious Metals"].have == 0, "missing factories show as none")
check(result.factories[0].name == "Precious Metals", "shortest factories come first")
expected = int(5 * result.output_best / 40) + 1
check(factory["Reactive Metals"].need == expected, "factories sized for the best output")
check(result.factory_output == 0.0, "a missing factory means factories can't make it yet")

unmeasured = Recipe("Other", {"Felsic Magma": 1.0}, [ChainNode("Felsic Magma", 1.0, 1, True)])
result = tune(unmeasured, team, 5000.0, rates)
check(not result.resources[0].measured and result.resources[0].per_planet == 5000.0,
      "resources you don't extract use the default rate, and say so")
check(result.output_now == 0.0, "nothing extracted, nothing made yet")
check(result.resources[0].proposed == 5, "all the planets could move to it")
check(result.factories == [] and result.factory_output == 0.0, "no factories needed")

check(tune(Recipe("Empty"), team, 5000.0, rates) is None, "nothing needed, nothing to tune")
short = tune(recipe, [character([colony(1, [extractor("Base Metals")])], max_planets=1)],
             5000.0, rates)
check(not short.enough_planets and short.output_best == 0.0, "too few planets is reported")

free = tune(recipe, [character([colony(1, [extractor("Base Metals")]),
                                colony(2, [extractor("Noble Metals")])], max_planets=4)],
            5000.0, rates)
check(free.planets == 4 and free.free_slots == 2, "free slots can be used")

both = planet_rates([character([colony(1, [extractor("Base Metals"),
                                           extractor("Base Metals", pin=2)])])])
check(len(both["Base Metals"]) == 1 and abs(both["Base Metals"][0] - 2 * rate) < 1e-6,
      "two extractors on a planet are one planet's rate")
check(abs(average_planet_rate(team) - rate) < 1e-6, "average rate per planet")
check(average_planet_rate([character([])]) is None, "no extractors, no average")

print(f"All {checks} checks passed." if not failures else f"{failures} of {checks} FAILED.")
raise SystemExit(1 if failures else 0)
