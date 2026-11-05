"""Build a SQLite fixture by feeding synthetic *official-format* JSONL records
(matching the CCP 2025 SDE schema) through ``eve_pi.sde.populate_database``.

This exercises the real download->DB transform without any network, using the
exact field shapes confirmed from the official schema:
  mapSolarSystems: _key, name{en}, securityStatus, constellationID
  mapPlanets:      _key, solarSystemID, typeID, celestialIndex
  mapStargates:    solarSystemID, destination{solarSystemID}
  planetSchematics:_key, name{en}, types[ {_key, isInput, quantity} ]
  types:           _key, groupID, name{en}
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

from eve_pi.pi_data import P0_RESOURCES
from eve_pi.sde import populate_database

# P0 name -> typeID (arbitrary but stable for the test)
P0_IDS = {name: i + 1 for i, name in enumerate(P0_RESOURCES)}
WATER, SILICON, WIDGET = 101, 106, 201

# planet type label -> real SDE typeID
PT = {"Temperate": 11, "Ice": 12, "Gas": 13, "Oceanic": 2014,
      "Lava": 2015, "Barren": 2016, "Storm": 2017, "Plasma": 2063}


def _sys(key, name, sec):
    return {"_key": key, "name": {"en": name}, "securityStatus": sec,
            "constellationID": 20000001}


def _gate(frm, to):
    return {"_key": 50000000 + frm * 10 + to, "solarSystemID": frm,
            "destination": {"solarSystemID": to, "stargateID": 1},
            "typeID": 16, "position": {"x": 0, "y": 0, "z": 0}}


def _planet(key, sysid, label, idx):
    return {"_key": key, "solarSystemID": sysid, "typeID": PT[label],
            "celestialIndex": idx, "orbitID": sysid}


def _records():
    systems = [
        _sys(1001, "Alpha", 0.9), _sys(1002, "Bravo", 0.6),
        _sys(1003, "Charlie", 0.3), _sys(1004, "Delta", 0.0),
        _sys(1005, "Echo", 0.7),
    ]
    # line graph A-B-C-D-E, both directions (as CCP ships paired gates)
    edges = [(1001, 1002), (1002, 1003), (1003, 1004), (1004, 1005)]
    stargates = []
    for a, b in edges:
        stargates += [_gate(a, b), _gate(b, a)]

    planets = [
        _planet(1, 1001, "Temperate", 1), _planet(2, 1001, "Barren", 2),
        _planet(3, 1002, "Lava", 1), _planet(4, 1002, "Gas", 2),
        _planet(5, 1003, "Storm", 1),
        _planet(6, 1004, "Ice", 1), _planet(7, 1004, "Oceanic", 2),
        _planet(8, 1005, "Plasma", 1),
        # a shattered planet (typeID 30889) that must be ignored
        {"_key": 9, "solarSystemID": 1005, "typeID": 30889, "celestialIndex": 2},
    ]

    types = [{"_key": tid, "groupID": 1042, "name": {"en": name}}
             for name, tid in P0_IDS.items()]
    types += [
        {"_key": WATER, "groupID": 42, "name": {"en": "Water"}},
        {"_key": SILICON, "groupID": 42, "name": {"en": "Silicon"}},
        {"_key": WIDGET, "groupID": 43, "name": {"en": "Test Widget"}},
    ]

    schematics = [
        {"_key": 901, "name": {"en": "Water"}, "cycleTime": 1800, "pins": [1, 2],
         "types": [{"_key": P0_IDS["Aqueous Liquids"], "isInput": True, "quantity": 3000},
                   {"_key": WATER, "isInput": False, "quantity": 20}]},
        {"_key": 906, "name": {"en": "Silicon"}, "cycleTime": 1800, "pins": [1, 2],
         "types": [{"_key": P0_IDS["Felsic Magma"], "isInput": True, "quantity": 3000},
                   {"_key": SILICON, "isInput": False, "quantity": 20}]},
        {"_key": 920, "name": {"en": "Test Widget"}, "cycleTime": 3600, "pins": [1, 2],
         "types": [{"_key": WATER, "isInput": True, "quantity": 10},
                   {"_key": SILICON, "isInput": True, "quantity": 10},
                   {"_key": WIDGET, "isInput": False, "quantity": 5}]},
    ]

    return {"systems": systems, "stargates": stargates, "planets": planets,
            "schematics": schematics, "types": types}


def build_fixture(path: str | None = None) -> str:
    if path is None:
        fd, path = tempfile.mkstemp(suffix=".sqlite", prefix="eve_pi_official_")
        os.close(fd)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    try:
        populate_database(conn, _records())
    finally:
        conn.close()
    return path
