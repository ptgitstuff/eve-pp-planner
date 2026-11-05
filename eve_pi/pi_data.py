"""Static Planetary Interaction reference data."""

from __future__ import annotations

PLANET_RESOURCES: dict[str, list[str]] = {
    "Barren": ["Aqueous Liquids", "Base Metals", "Carbon Compounds",
               "Microorganisms", "Noble Metals"],
    "Gas": ["Aqueous Liquids", "Base Metals", "Ionic Solutions",
            "Noble Gas", "Reactive Gas"],
    "Ice": ["Aqueous Liquids", "Heavy Metals", "Microorganisms",
            "Noble Gas", "Planktic Colonies"],
    "Lava": ["Base Metals", "Felsic Magma", "Heavy Metals",
             "Non-CS Crystals", "Suspended Plasma"],
    "Oceanic": ["Aqueous Liquids", "Carbon Compounds", "Complex Organisms",
                "Microorganisms", "Planktic Colonies"],
    "Plasma": ["Base Metals", "Heavy Metals", "Noble Metals",
               "Non-CS Crystals", "Suspended Plasma"],
    "Storm": ["Aqueous Liquids", "Base Metals", "Ionic Solutions",
              "Noble Gas", "Suspended Plasma"],
    "Temperate": ["Aqueous Liquids", "Autotrophs", "Carbon Compounds",
                  "Complex Organisms", "Microorganisms"],
}

PLANET_TYPES: list[str] = sorted(PLANET_RESOURCES.keys())

PLANET_TYPE_BY_ID: dict[int, str] = {
    11: "Temperate", 12: "Ice", 13: "Gas", 2014: "Oceanic",
    2015: "Lava", 2016: "Barren", 2017: "Storm", 2063: "Plasma",
}

P0_RESOURCES: list[str] = sorted({r for rs in PLANET_RESOURCES.values() for r in rs})

P0_TO_P1: dict[str, str] = {
    "Aqueous Liquids": "Water", "Autotrophs": "Industrial Fibers",
    "Base Metals": "Reactive Metals", "Carbon Compounds": "Biofuels",
    "Complex Organisms": "Proteins", "Felsic Magma": "Silicon",
    "Heavy Metals": "Toxic Metals", "Ionic Solutions": "Electrolytes",
    "Microorganisms": "Bacteria", "Noble Gas": "Oxygen",
    "Noble Metals": "Precious Metals", "Non-CS Crystals": "Chiral Structures",
    "Planktic Colonies": "Biomass", "Reactive Gas": "Oxidizing Compound",
    "Suspended Plasma": "Plasmoids",
}


def resources_to_planet_types(resources: set[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {r: set() for r in resources}
    for planet, res_list in PLANET_RESOURCES.items():
        for r in res_list:
            if r in out:
                out[r].add(planet)
    return out


def planet_type_resources(planet_type: str, restrict_to: set[str] | None = None) -> set[str]:
    res = set(PLANET_RESOURCES.get(planet_type, ()))
    if restrict_to is not None:
        res &= restrict_to
    return res


def security_band(security: float) -> str:
    if security >= 0.45:
        return "High"
    if security > 0.0:
        return "Low"
    return "Null"
