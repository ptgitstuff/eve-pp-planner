"""Tests for Janice import and the order optimiser (no network: requests faked).
Run:  python -m tests.test_fulfil
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eve_pi.db import PIDatabase, Planet, System
from eve_pi.facilities import build_breakdown, facility_rates
from eve_pi.fulfil import (FACTORIES, Allocation, CharacterSlots, Placement, allocate,
                           build_bill, choose_planets, fulfil_order, order_plan,
                           split_characters, split_roster)
from eve_pi.janice import (JaniceError, extract_code, fetch_appraisal, parse_item_list)
from eve_pi.production import ORDER_PRODUCT, calculate
from eve_pi.results_model import format_duration
from tests.make_official_fixture import build_fixture

ALL = {"High", "Low", "Null"}
_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok: {label}")


def test_links():
    print("Janice links and pasted lists:")
    check(extract_code("https://janice.e-351.com/a/KsL8o4") == "KsL8o4", "plain link")
    check(extract_code("see janice.e-351.com/a/AbC123?x=1 ok") == "AbC123", "link in text")
    check(extract_code("Coolant 5") is None, "no link")
    items, unread = parse_item_list(
        "Coolant\t199036\nWater 1,000\n5 Oxygen\nBacteria x 2\nWater 1\nnonsense\n\n")
    check(items == {"Coolant": 199036, "Water": 1001, "Oxygen": 5, "Bacteria": 2},
          "tabs, spaces, thousands, either order, duplicates merged")
    check(unread == ["nonsense"], "unreadable lines are reported")


def test_fetch():
    print("loading an appraisal:")
    reply = {"id": 1, "result": {
        "expires": "2026-11-30", "effectivePrices": {"totalSplitPrice": 123.0},
        "items": [{"amount": 7, "itemType": {"name": "Coolant"}},
                  {"amount": 2, "itemType": {"name": "Water"}}]}}
    sent = []

    def post(url, body):
        sent.append(json.loads(body))
        return json.dumps(reply).encode()

    got = fetch_appraisal("Abc", post=post)
    check(got.items == {"Coolant": 7, "Water": 2}, "items read")
    check(got.split_value == 123.0, "value read")
    check(sent[0]["params"] == {"code": "Abc"}, "asks for the code")

    def fails(result):
        try:
            fetch_appraisal("Abc", post=result)
        except JaniceError:
            return True
        return False

    check(fails(lambda u, b: b'{"id":1,"error":{"code":-32603}}'), "expired code is an error")
    check(fails(lambda u, b: b'not json'), "a bad reply is an error")

    def offline(url, body):
        raise OSError("no network")

    check(fails(offline), "offline is an error, not a crash")


def test_bill(db):
    print("bill of materials (10 Test Widget = 20 Water + 20 Silicon):")
    bill = build_bill(db, {"test widget": 10, "Tritanium": 3})
    check(bill.items == {"Test Widget": 10}, "names are matched and normalised")
    check(bill.not_pi == ["Tritanium"], "non-PI items are reported")
    check(abs(bill.raw_units["Aqueous Liquids"] - 3000) < 1e-6, "20 Water = 3000 P0")
    check(abs(bill.raw_units["Felsic Magma"] - 3000) < 1e-6, "20 Silicon = 3000 P0")
    check(abs(bill.advanced_hours["Test Widget"] - 2.0) < 1e-6, "10 units at 5/hr = 2 hours")
    check("Water" not in bill.advanced_hours, "P1 runs on extractors, not factories")
    check(abs(bill.refine_per_facility["Aqueous Liquids"] - 6000) < 1e-6,
          "one basic facility refines 6000 P0/hr")
    both = build_bill(db, {"Test Widget": 10, "Water": 10})
    check(abs(both.raw_units["Aqueous Liquids"] - 4500) < 1e-6,
          "a P1 ordered directly adds to what the P2 needs")
    raw = build_bill(db, {"Aqueous Liquids": 100})
    check(raw.raw_units == {"Aqueous Liquids": 100.0}, "raw P0 can be ordered")


def test_allocate(db):
    print("allocating slots:")
    bill = build_bill(db, {"Test Widget": 10})
    a = allocate(bill, 1000, 6, 8, 8)
    check(a.feasible and a.planets_used == 6, "uses every slot")
    check(a.extractors["Aqueous Liquids"] + a.extractors["Felsic Magma"] == 5
          and a.factories == 1, "factories stay at 1 because they aren't slowest")
    check(abs(a.hours - 1.5) < 1e-6, f"3000 P0 over 2 planets at 1000/hr ({a.hours})")
    best = min(max(3000 / (x * 1000), 3000 / ((5 - x) * 1000)) for x in range(1, 5))
    check(abs(a.hours - best) < 1e-6, "matches a brute-force best split")

    check(not allocate(bill, 1000, 2, 8, 8).feasible, "too few slots is infeasible")
    check(allocate(bill, 1000, 2, 8, 8).min_slots == 3, "reports the minimum slots")

    capped = allocate(bill, 1000, 6, 8, 8, {"Lava": 1, "Barren": 5})
    check(capped.extractors["Felsic Magma"] == 1, "only one Lava planet exists")
    check(capped.bottleneck == "Felsic Magma" and capped.limited_by_planets,
          "the capped resource is the bottleneck")
    check(capped.free_slots > 0, "slots that can't help stay free")
    check(not allocate(bill, 1000, 6, 8, 8, {"Barren": 5}).feasible,
          "no Lava in range means it can't be made")

    refine = allocate(bill, 10000, 3, 1, 8)
    check(abs(refine.resource_hours["Felsic Magma"] - 0.5) < 1e-6,
          "extraction is capped by basic facility throughput")

    heavy = build_bill(db, {"Test Widget": 100000})
    many = allocate(heavy, 100000, 10, 8, 1)
    check(many.factories > 1 and many.bottleneck in (FACTORIES, "Aqueous Liquids",
                                                     "Felsic Magma"),
          "factory planets grow when they are the slowest")


def _planet(pid, system, planet_type):
    return Planet(pid, f"{system.name} {pid}", system.id, planet_type)


def test_placement():
    print("placing planets:")
    near = System(1, "Near", 0.9)
    far = System(2, "Far", 0.9)
    big = System(3, "Big", 0.9)
    systems = [
        (near, 0, [_planet(1, near, "Lava")]),
        (far, 5, [_planet(2, far, "Lava"), _planet(3, far, "Gas")]),
        (big, 9, [_planet(4, big, "Lava"), _planet(5, big, "Gas"), _planet(6, big, "Barren")]),
    ]
    alloc = Allocation(extractors={"Felsic Magma": 1, "Reactive Gas": 1}, factories=1)
    placed = choose_planets(alloc, systems)
    check({p.system.name for p in placed} == {"Big"}, "one system when one can do it all")
    check(sum(p.resource is None for p in placed) == 1, "factory goes on a spare planet")

    alloc = Allocation(extractors={"Felsic Magma": 1}, factories=0)
    check(choose_planets(alloc, systems)[0].system.name == "Near", "ties go to the nearest")

    alloc = Allocation(extractors={"Felsic Magma": 2, "Base Metals": 1}, factories=0)
    both = choose_planets(alloc, systems)
    check(sorted(str(p.resource) for p in both)
          == ["Base Metals", "Felsic Magma", "Felsic Magma"], "shared planets are re-matched")

    groups = split_characters(both + both, 2)
    check([len(g) for g in groups] == [2, 2, 2], "at most N planets per character")
    alloc = Allocation(extractors={"Felsic Magma": 9}, factories=0)
    check(len(choose_planets(alloc, systems)) == 3, "never places more than exist")


def test_end_to_end(db):
    print("whole order on the fixture map:")
    plan = fulfil_order(db, {"Test Widget": 10}, "Alpha", 4, ALL, 2, 6, 1000, 8, 8)
    alloc = plan.allocation
    check(alloc.feasible, "the fixture can make it")
    check(len(plan.placements) == alloc.planets_used, "every allocated planet is placed")
    check(len({p.planet.id for p in plan.placements}) == len(plan.placements),
          "no planet is used twice")
    check(all(len(c) <= 6 for c in plan.by_character), "respects planets per character")
    check([s.characters for s in plan.scenarios][:2] == [1, 2], "what-ifs start at 1 character")
    check(plan.scenarios[0].hours >= plan.scenarios[-1].hours, "more characters never slower")
    far = fulfil_order(db, {"Test Widget": 10}, "Alpha", 0, ALL, 2, 6, 1000, 8, 8)
    check(not far.allocation.feasible and far.allocation.limited_by_planets,
          "out of range is reported, not a crash")
    try:
        fulfil_order(db, {"Test Widget": 1}, "Nowhere", 4, ALL, 1, 6, 1000, 8, 8)
        check(False, "unknown staging system raises")
    except ValueError:
        check(True, "unknown staging system raises")


def test_order_plan(db):
    print("an order as a plan for the other tabs:")
    fp = fulfil_order(db, {"Test Widget": 10, "Water": 10}, "Alpha", 4, ALL, 2, 6,
                      1000, 8, 8)
    plan = order_plan(db, fp)
    placed = sorted((p.planet.id, p.resource) for p in fp.placements
                    if p.resource is not None)
    assigned = sorted((planet.id, res) for cs in plan.chosen
                      for res, planet in cs.assignments)
    check(assigned == placed, "extractor rows match the optimiser's placements")
    factories = sorted(planet.id for cs in plan.chosen for planet in cs.factories)
    check(factories == sorted(p.planet.id for p in fp.placements if p.resource is None)
          and len(factories) == fp.allocation.factories, "factory planets are kept separate")
    jumps = [(cs.jumps, cs.system.name) for cs in plan.chosen]
    check(jumps == sorted(jumps), "systems are listed nearest first")
    check(plan.solution is not None and plan.solution.size == len(plan.chosen),
          "the solution counts the systems used")
    check(plan.order_items == {"Test Widget": 10, "Water": 10}, "the order is kept")
    check(plan.requirements == sorted(fp.bill.raw_units), "requirements are the raw materials")
    check(all(plan.per_requirement[r] for r in plan.requirements),
          "every resource lists its in-range options")
    lava = plan.per_requirement["Felsic Magma"]
    check(all(o.planet.planet_type in plan.resolution.resource_planets["Felsic Magma"]
              for o in lava), "options are planets that yield the resource")
    check([o.jumps for o in lava] == sorted(o.jumps for o in lava), "options nearest first")
    check(plan.production.slots_requested == 12, "slots come from the order's characters")

    recipe = plan.production.recipe
    check(recipe.product == ORDER_PRODUCT, "the recipe is the whole order")
    check(recipe.demand == fp.bill.raw_units, "recipe demand equals the bill")
    by_name = {node.name: node for node in recipe.chain}
    check(by_name["Water"].qty_per_unit == fp.bill.intermediates["Water"],
          "chain quantities are whole-order totals")
    build = build_breakdown(recipe, 1.0, facility_rates(db), extractor_planets=5)
    check(build.advanced_by_tier == {2: 2},
          f"10 Test Widgets at 5/hr: 2 facilities, at P2 ({build.advanced_by_tier})")
    check(build.basic_factories_needed >= 1, "Water and Silicon count as basic")
    result = calculate(recipe, {r: 1 for r in recipe.demand}, 1000)
    hours = max(units / 1000 for units in recipe.demand.values())
    check(abs(1 / result.product_per_hour - hours) < 1e-6,
          "orders per hour inverts to the extraction time")

    stuck = order_plan(db, fulfil_order(db, {"Test Widget": 10}, "Alpha", 0, ALL, 2, 6,
                                        1000, 8, 8))
    check(stuck.solution is not None and stuck.chosen == [],
          "an order that can't be placed chooses nothing")
    check(stuck.production is not None and stuck.production.planets_used == 0,
          "and still reports zero planets")
    check(stuck.unmet == [r for r in stuck.requirements if not stuck.per_requirement[r]],
          "and names resources with no options")

    junk = order_plan(db, fulfil_order(db, {"Tritanium": 5}, "Alpha", 4, ALL, 2, 6,
                                       1000, 8, 8))
    check(junk.solution is None and junk.requirements == [] and junk.chosen == [],
          "an order with no PI items has nothing to show")
    check(junk.resolution.product == ORDER_PRODUCT, "and is still known to be an order")


def test_roster_split():
    print("sharing planets across real characters:")
    home = System(1, "Home", 0.9)
    placed = [Placement(_planet(i, home, "Lava"), home, 0, "Felsic Magma") for i in range(1, 6)]

    def names(result):
        return {name: sorted(p.planet.id for p in held) for name, held in result[0]}

    uneven = split_roster(placed, [CharacterSlots("Small", 1), CharacterSlots("Big", 4)])
    check(names(uneven) == {"Big": [1, 2, 3, 4], "Small": [5]}, "each keeps to its own free slots")
    few = split_roster(placed[:2], [CharacterSlots("Small", 2), CharacterSlots("Big", 4)])
    check(list(names(few)) == ["Big"], "as few characters as possible, most free first")

    blocked = split_roster(placed[:2], [CharacterSlots("A", 3, frozenset({1})),
                                        CharacterSlots("B", 1)])
    check(names(blocked) == {"A": [2], "B": [1]}, "nobody gets a planet they're already on")
    swap = split_roster(placed[:2], [CharacterSlots("A", 1), CharacterSlots("B", 1, frozenset({1}))])
    check(names(swap) == {"A": [1], "B": [2]}, "an order that needs a swap still works")
    swap2 = split_roster(placed[:2], [CharacterSlots("A", 1, frozenset({2})),
                                      CharacterSlots("B", 1)])
    check(names(swap2) == {"A": [1], "B": [2]}, "earlier choices move to make room")
    stuck = split_roster(placed[:1], [CharacterSlots("A", 3, frozenset({1}))])
    check(stuck[0] == [] and [p.planet.id for p in stuck[1]] == [1],
          "a planet nobody can take is reported, not dropped")


def test_roster_order(db):
    print("an order using real free slots:")
    roster = [CharacterSlots("Full", 0, frozenset({1})), CharacterSlots("Small", 2),
              CharacterSlots("Big", 4, frozenset({3}))]
    plan = fulfil_order(db, {"Test Widget": 10}, "Alpha", 4, ALL, 99, 6, 1000, 8, 8,
                        roster=roster)
    check(plan.characters == 2, "characters with no free slots don't count")
    check(plan.allocation.slots == 6, "slots are the free slots, not the typed numbers")
    check(plan.allocation.feasible and not plan.unassigned, "the order fits")
    placed = {name: held for name, held in zip(plan.character_names, plan.by_character)}
    limits = {"Small": 2, "Big": 4}
    check(all(len(held) <= limits[name] for name, held in placed.items()),
          "nobody goes over their free slots")
    check(sum(map(len, plan.by_character)) == len(plan.placements), "every planet has a character")
    check(all(p.planet.id != 3 for p in placed.get("Big", ())), "Big stays off its own colony")
    check("Full" not in placed, "the full character gets nothing")
    check([s.slots for s in plan.scenarios][:3] == [4, 6, 12],
          "what-ifs add real characters, most free first, then new ones")
    check(plan.scenarios[1].hours == plan.allocation.hours, "the real roster is one of them")
    check(order_plan(db, plan).production.slots_requested == 6, "other tabs see the real slots")

    full = fulfil_order(db, {"Test Widget": 10}, "Alpha", 4, ALL, 6, 6, 1000, 8, 8,
                        roster=[CharacterSlots("Full", 0)])
    check(not full.allocation.feasible and full.characters == 0, "no free slots at all is reported")
    check(full.scenarios[0].slots == 6, "and the what-ifs still show adding a character")
    typed = fulfil_order(db, {"Test Widget": 10}, "Alpha", 4, ALL, 2, 6, 1000, 8, 8)
    check(typed.character_names[0] == "Character 1" and typed.roster is None,
          "typed numbers still work")


def test_duration():
    print("durations:")
    check(format_duration(0.5) == "30 min", "minutes")
    check(format_duration(5) == "5.0 hours", "hours")
    check(format_duration(48) == "2.0 days", "days")
    check(format_duration(24 * 400) == "400 days", "long spans stay in days")


def main() -> int:
    test_links()
    test_fetch()
    path = build_fixture()
    db = PIDatabase(path)
    try:
        test_bill(db)
        test_allocate(db)
        test_placement()
        test_end_to_end(db)
        test_order_plan(db)
        test_roster_split()
        test_roster_order(db)
    finally:
        db.close()
        os.remove(path)
    test_duration()
    print(f"\nAll {_passed} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
