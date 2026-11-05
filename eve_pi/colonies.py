"""Turn raw ESI colony data into what the Characters tab shows: planet slots, what
each planet produces, at what rate, and what is in storage. There is no Tkinter
or network here, so all of it is testable.

ESI only recalculates a colony when someone views it in the game client, so every
figure is as of that colony's ``last_update``. Rates are nominal: the extractor
rate is what ESI reports per cycle, and real output varies from cycle to cycle. A
factory rate is the most its facilities can make when they are fully supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .db import PIDatabase
from .facilities import facility_rates
from .fulfil import CharacterSlots
from .results_model import STATUS_CRITICAL, STATUS_GOOD, STATUS_WARNING, Status
from .settings import load_json, save_json

INTERPLANETARY_CONSOLIDATION = 2495
COMMAND_CENTER_UPGRADES = 2505
MAX_PLANETS = 6

RUNNING = Status("Running", "✓", STATUS_GOOD)
STOPPED = Status("Stopped", "✕", STATUS_CRITICAL)
NO_PROGRAM = Status("No program", "▲", STATUS_WARNING)


@dataclass
class Extractor:
    product: str | None
    per_hour: float
    heads: int
    expires: datetime | None
    status: Status


@dataclass
class FactoryGroup:
    product: str
    count: int
    max_per_hour: float


@dataclass
class Colony:
    planet_id: int
    name: str
    system: str
    planet_type: str
    command_center_level: int
    last_update: datetime | None
    extractors: list[Extractor] = field(default_factory=list)
    factories: list[FactoryGroup] = field(default_factory=list)
    stock: dict[str, int] = field(default_factory=dict)
    has_details: bool = True


@dataclass
class CharacterPI:
    id: int
    name: str
    max_planets: int | None             # None when skills couldn't be read
    command_center_skill: int | None
    colonies: list[Colony] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    colonies_known: bool = True         # False when the colony list couldn't be read

    @property
    def free_slots(self) -> int | None:
        if self.max_planets is None or not self.colonies_known:
            return None
        return max(self.max_planets - len(self.colonies), 0)


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _skill_level(skills: dict, skill_id: int) -> int:
    for skill in skills.get("skills", ()):
        if skill.get("skill_id") == skill_id:
            return int(skill.get("active_skill_level", 0))
    return 0


def _read_pins(colony: Colony, pins: list[dict], db: PIDatabase, product_of: dict[int, int],
               max_rate: dict, now: datetime) -> None:
    def name_of(type_id: int) -> str:
        return db.type_name_by_id(type_id) or f"Type {type_id}"

    groups: dict[str, FactoryGroup] = {}
    for pin in pins:
        for content in pin.get("contents") or ():
            name = name_of(content["type_id"])
            colony.stock[name] = colony.stock.get(name, 0) + int(content["amount"])

        details = pin.get("extractor_details")
        if details is not None:
            product_id = details.get("product_type_id")
            cycle = details.get("cycle_time") or 0
            expires = parse_time(pin.get("expiry_time"))
            if product_id is None or not cycle:
                colony.extractors.append(Extractor(
                    name_of(product_id) if product_id is not None else None,
                    0.0, len(details.get("heads") or ()), expires, NO_PROGRAM))
                continue
            status = STOPPED if expires is not None and expires <= now else RUNNING
            colony.extractors.append(Extractor(
                name_of(product_id), (details.get("qty_per_cycle") or 0) * 3600.0 / cycle,
                len(details.get("heads") or ()), expires, status))
            continue

        factory = pin.get("factory_details") or {}
        schematic = pin.get("schematic_id") or factory.get("schematic_id")
        if schematic and schematic in product_of:
            product = name_of(product_of[schematic])
            rate = max_rate.get(product)
            group = groups.setdefault(product, FactoryGroup(product, 0, 0.0))
            group.count += 1
            group.max_per_hour += rate.out_per_hour if rate else 0.0
    colony.factories = sorted(groups.values(), key=lambda g: g.product)


def build_character(character_id: int, name: str, raw: dict, db: PIDatabase,
                    now: datetime | None = None) -> CharacterPI:
    """One character from ``EsiClient.fetch_character`` data. Must run on the
    thread that owns ``db``."""
    now = now or datetime.now(timezone.utc)
    skills = raw.get("skills")
    result = CharacterPI(character_id, name, None, None, errors=list(raw.get("errors", ())))
    if isinstance(skills, dict):
        level = _skill_level(skills, INTERPLANETARY_CONSOLIDATION)
        result.max_planets = min(1 + level, MAX_PLANETS)
        result.command_center_skill = _skill_level(skills, COMMAND_CENTER_UPGRADES)

    result.colonies_known = raw.get("colonies") is not None
    entries = raw.get("colonies") or []
    system_ids = sorted({entry["solar_system_id"] for entry in entries})
    systems = db.systems_info(system_ids) if system_ids else {}
    planets = {p.id: p for p in db.planets_in_systems(system_ids)} if system_ids else {}
    product_of = {schematic: type_id
                  for type_id, (schematic, _) in db.schematic_outputs_q().items()}
    max_rate = facility_rates(db)
    details = raw.get("details") or {}

    for entry in entries:
        planet_id = entry["planet_id"]
        planet = planets.get(planet_id)
        system = systems.get(entry["solar_system_id"])
        colony = Colony(
            planet_id,
            planet.name if planet else f"Planet {planet_id}",
            system.name if system else f"System {entry['solar_system_id']}",
            planet.planet_type if planet else str(entry.get("planet_type", "")).capitalize(),
            int(entry.get("upgrade_level", 0)),
            parse_time(entry.get("last_update")))
        detail = details.get(planet_id)
        if detail is None:
            colony.has_details = False
        else:
            _read_pins(colony, detail.get("pins") or [], db, product_of, max_rate, now)
        result.colonies.append(colony)
    result.colonies.sort(key=lambda c: (c.system, c.name))
    return result


def merge_raw(old: dict | None, new: dict) -> dict:
    """A new read, with anything it failed to get filled in from the last good one,
    so an ESI outage doesn't blank figures the app already had. Says when it does."""
    if not old:
        return new
    merged = dict(new)
    details = dict(new.get("details") or {})
    kept = False
    if new.get("skills") is None and old.get("skills") is not None:
        merged["skills"] = old["skills"]
        kept = True
    if new.get("colonies") is None and old.get("colonies") is not None:
        merged["colonies"] = old["colonies"]
        kept = True
    for entry in merged.get("colonies") or ():
        planet_id = entry["planet_id"]
        if planet_id not in details and planet_id in (old.get("details") or {}):
            details[planet_id] = old["details"][planet_id]
            kept = True
    merged["details"] = details
    if kept:
        merged["errors"] = [*new.get("errors", ()),
                            "Showing the last figures read for anything that failed."]
    return merged


def load_character_data(path: str) -> dict[int, dict]:
    """Saved raw reads by character ID. JSON has only string keys, so restore IDs."""
    out: dict[int, dict] = {}
    for key, raw in load_json(path).items():
        try:
            out[int(key)] = {
                "skills": raw.get("skills"), "colonies": raw.get("colonies"), "errors": [],
                "details": {int(k): v for k, v in (raw.get("details") or {}).items()}}
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def save_character_data(path: str, data: dict[int, dict]) -> None:
    """Colony and skill figures only: tokens are never part of a raw read."""
    save_json(path, {str(k): {"skills": v.get("skills"), "colonies": v.get("colonies"),
                              "details": v.get("details") or {}}
                     for k, v in data.items()})


def slot_roster(characters: list[CharacterPI]) -> tuple[list[CharacterSlots], list[str]]:
    """Characters as the order optimiser needs them, plus the names of any whose
    free slots are unknown. Those are left out instead of guessed."""
    roster: list[CharacterSlots] = []
    unknown: list[str] = []
    for character in characters:
        if character.free_slots is None:
            unknown.append(character.name)
        else:
            roster.append(CharacterSlots(
                character.name, character.free_slots,
                frozenset(c.planet_id for c in character.colonies)))
    return roster, unknown


@dataclass
class Totals:
    characters: int = 0
    planets_used: int = 0
    max_planets: int = 0
    free_slots: int = 0
    unknown_slots: int = 0              # characters whose skills couldn't be read
    extraction: dict[str, float] = field(default_factory=dict)
    holdings: dict[str, int] = field(default_factory=dict)
    oldest_update: datetime | None = None


def totals(characters: list[CharacterPI]) -> Totals:
    """Across all characters. Extraction only counts extractors still running."""
    out = Totals(characters=len(characters))
    for character in characters:
        out.planets_used += len(character.colonies)
        if character.free_slots is None:
            out.unknown_slots += 1
        else:
            out.max_planets += character.max_planets
            out.free_slots += character.free_slots
        for colony in character.colonies:
            for extractor in colony.extractors:
                if extractor.status is RUNNING and extractor.product:
                    out.extraction[extractor.product] = (
                        out.extraction.get(extractor.product, 0.0) + extractor.per_hour)
            for name, amount in colony.stock.items():
                out.holdings[name] = out.holdings.get(name, 0) + amount
            if colony.last_update and (out.oldest_update is None
                                       or colony.last_update < out.oldest_update):
                out.oldest_update = colony.last_update
    return out
