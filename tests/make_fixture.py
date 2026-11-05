"""Build a tiny in-memory-style SDE SQLite fixture that mirrors the real schema,
so the core logic can be tested without downloading the ~hundreds-of-MB SDE.

Universe (line graph):  A(0.9) - B(0.6) - C(0.3) - D(0.0) - E(0.7)
Planets:
  A: Temperate, Barren     B: Lava, Gas      C: Storm
  D: Ice, Oceanic          E: Plasma
PI chain: "Test Widget" (P2) <- Water + Silicon
          Water <- Aqueous Liquids (P0);  Silicon <- Felsic Magma (P0)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

from eve_pi.pi_data import P0_RESOURCES

SYSTEMS = [
    # id, name, security
    (1001, "Alpha", 0.9),
    (1002, "Bravo", 0.6),
    (1003, "Charlie", 0.3),
    (1004, "Delta", 0.0),
    (1005, "Echo", 0.7),
]

# undirected line graph A-B-C-D-E
EDGES = [(1001, 1002), (1002, 1003), (1003, 1004), (1004, 1005)]

PLANETS = [
    # planetID, name, systemID, planet type label
    (1, "Alpha I", 1001, "Temperate"),
    (2, "Alpha II", 1001, "Barren"),
    (3, "Bravo I", 1002, "Lava"),
    (4, "Bravo II", 1002, "Gas"),
    (5, "Charlie I", 1003, "Storm"),
    (6, "Delta I", 1004, "Ice"),
    (7, "Delta II", 1004, "Oceanic"),
    (8, "Echo I", 1005, "Plasma"),
]

# P0 typeIDs 1..15 (order matches P0_RESOURCES sorted list)
P0_IDS = {name: i + 1 for i, name in enumerate(P0_RESOURCES)}

# Intermediates / product
WATER, SILICON, WIDGET = 101, 106, 201
EXTRA_TYPES = [
    (WATER, 42, "Water"),
    (SILICON, 42, "Silicon"),
    (WIDGET, 43, "Test Widget"),
]

# schematics: (schematicID, [(typeID, qty, isInput)...])
SCHEMATICS = {
    901: [(P0_IDS["Aqueous Liquids"], 3000, 1), (WATER, 20, 0)],
    906: [(P0_IDS["Felsic Magma"], 3000, 1), (SILICON, 20, 0)],
    920: [(WATER, 10, 1), (SILICON, 10, 1), (WIDGET, 5, 0)],
}


def build_fixture(path: str | None = None) -> str:
    if path is None:
        fd, path = tempfile.mkstemp(suffix=".sqlite", prefix="eve_pi_fixture_")
        os.close(fd)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("CREATE TABLE mapSolarSystems (solarSystemID, solarSystemName, security)")
    c.executemany("INSERT INTO mapSolarSystems VALUES (?,?,?)", SYSTEMS)

    c.execute("CREATE TABLE mapSolarSystemJumps (fromSolarSystemID, toSolarSystemID)")
    jumps = []
    for a, b in EDGES:
        jumps.append((a, b))
        jumps.append((b, a))
    c.executemany("INSERT INTO mapSolarSystemJumps VALUES (?,?)", jumps)

    c.execute("CREATE TABLE planets (planetID, planetName, solarSystemID, typeName)")
    c.executemany(
        "INSERT INTO planets VALUES (?,?,?,?)",
        [(pid, nm, sid, f"Planet ({lbl})") for pid, nm, sid, lbl in PLANETS],
    )

    c.execute("CREATE TABLE invTypes (typeID, groupID, typeName)")
    rows = [(tid, 1042, name) for name, tid in P0_IDS.items()] + EXTRA_TYPES
    c.executemany("INSERT INTO invTypes VALUES (?,?,?)", rows)

    c.execute("CREATE TABLE planetSchematics (schematicID, schematicName, cycleTime)")
    c.executemany(
        "INSERT INTO planetSchematics VALUES (?,?,?)",
        [(sid, f"S{sid}", 3600) for sid in SCHEMATICS],
    )

    c.execute("CREATE TABLE planetSchematicsTypeMap (schematicID, typeID, quantity, isInput)")
    smrows = []
    for sid, items in SCHEMATICS.items():
        for tid, qty, isin in items:
            smrows.append((sid, tid, qty, isin))
    c.executemany("INSERT INTO planetSchematicsTypeMap VALUES (?,?,?,?)", smrows)

    conn.commit()
    conn.close()
    return path
