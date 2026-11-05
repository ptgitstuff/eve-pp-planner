"""Read access to the local SDE SQLite cache. (Fresh copy; identical to db.py.)"""

from __future__ import annotations

import re
import sqlite3
from collections import deque
from dataclasses import dataclass

from .pi_data import PLANET_TYPES, security_band

_PLANET_TYPE_RE = re.compile(r"\(([^)]+)\)")


def parse_planet_type(type_name: str) -> str | None:
    m = _PLANET_TYPE_RE.search(type_name or "")
    if not m:
        return None
    label = m.group(1).strip()
    return label if label in PLANET_TYPES else None


@dataclass(frozen=True)
class System:
    id: int
    name: str
    security: float

    @property
    def band(self) -> str:
        return security_band(self.security)


@dataclass(frozen=True)
class Planet:
    id: int
    name: str
    system_id: int
    planet_type: str


class PIDatabase:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def search_systems(self, text: str, limit: int = 50) -> list[System]:
        text = (text or "").strip()
        if not text:
            return []
        cur = self.conn.execute(
            """SELECT solarSystemID, solarSystemName, security
               FROM mapSolarSystems
               WHERE solarSystemName LIKE ? COLLATE NOCASE
               ORDER BY CASE WHEN solarSystemName LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,
                        solarSystemName
               LIMIT ?""",
            (f"%{text}%", f"{text}%", limit),
        )
        return [System(int(r[0]), r[1], float(r[2])) for r in cur.fetchall()]

    def get_system(self, name: str) -> System | None:
        cur = self.conn.execute(
            """SELECT solarSystemID, solarSystemName, security
               FROM mapSolarSystems WHERE solarSystemName = ? COLLATE NOCASE""",
            (name,),
        )
        r = cur.fetchone()
        return System(int(r[0]), r[1], float(r[2])) if r else None

    def all_system_names(self) -> list[str]:
        cur = self.conn.execute(
            "SELECT solarSystemName FROM mapSolarSystems ORDER BY solarSystemName"
        )
        return [r[0] for r in cur.fetchall()]

    def _adjacency(self) -> dict[int, list[int]]:
        adj: dict[int, list[int]] = {}
        cur = self.conn.execute(
            "SELECT fromSolarSystemID, toSolarSystemID FROM mapSolarSystemJumps"
        )
        for a, b in cur.fetchall():
            adj.setdefault(int(a), []).append(int(b))
        return adj

    def systems_within_range(self, staging_id: int, max_jumps: int) -> dict[int, int]:
        adj = self._adjacency()
        dist = {staging_id: 0}
        q = deque([staging_id])
        while q:
            cur = q.popleft()
            if dist[cur] >= max_jumps:
                continue
            for nxt in adj.get(cur, ()):
                if nxt not in dist:
                    dist[nxt] = dist[cur] + 1
                    q.append(nxt)
        return dist

    def systems_info(self, ids: list[int]) -> dict[int, System]:
        if not ids:
            return {}
        out: dict[int, System] = {}
        for chunk in _chunks(ids, 800):
            qs = ",".join("?" for _ in chunk)
            cur = self.conn.execute(
                f"""SELECT solarSystemID, solarSystemName, security
                    FROM mapSolarSystems WHERE solarSystemID IN ({qs})""",
                chunk,
            )
            for r in cur.fetchall():
                out[int(r[0])] = System(int(r[0]), r[1], float(r[2]))
        return out

    def planets_in_systems(self, ids: list[int]) -> list[Planet]:
        if not ids:
            return []
        out: list[Planet] = []
        for chunk in _chunks(ids, 800):
            qs = ",".join("?" for _ in chunk)
            cur = self.conn.execute(
                f"""SELECT planetID, planetName, solarSystemID, typeName
                    FROM planets WHERE solarSystemID IN ({qs})""",
                chunk,
            )
            for r in cur.fetchall():
                ptype = parse_planet_type(r["typeName"])
                if ptype:
                    out.append(Planet(int(r["planetID"]), r["planetName"],
                                      int(r["solarSystemID"]), ptype))
        return out

    def type_id_by_name(self, name: str) -> int | None:
        cur = self.conn.execute(
            "SELECT typeID FROM invTypes WHERE typeName = ? COLLATE NOCASE", (name,)
        )
        r = cur.fetchone()
        return int(r[0]) if r else None

    def type_name_by_id(self, type_id: int) -> str | None:
        cur = self.conn.execute(
            "SELECT typeName FROM invTypes WHERE typeID = ?", (type_id,)
        )
        r = cur.fetchone()
        return r[0] if r else None

    def schematic_outputs(self) -> dict[int, int]:
        cur = self.conn.execute(
            "SELECT schematicID, typeID FROM planetSchematicsTypeMap WHERE isInput = 0"
        )
        return {int(t): int(s) for s, t in cur.fetchall()}

    def schematic_inputs(self, schematic_id: int) -> list[int]:
        cur = self.conn.execute(
            """SELECT typeID FROM planetSchematicsTypeMap
               WHERE schematicID = ? AND isInput = 1""",
            (schematic_id,),
        )
        return [int(r[0]) for r in cur.fetchall()]

    def schematic_outputs_q(self) -> dict[int, tuple[int, int]]:
        cur = self.conn.execute(
            """SELECT typeID, schematicID, quantity
               FROM planetSchematicsTypeMap WHERE isInput = 0"""
        )
        return {int(t): (int(s), int(q or 1)) for t, s, q in cur.fetchall()}

    def schematic_inputs_q(self, schematic_id: int) -> list[tuple[int, int]]:
        cur = self.conn.execute(
            """SELECT typeID, quantity FROM planetSchematicsTypeMap
               WHERE schematicID = ? AND isInput = 1""",
            (schematic_id,),
        )
        return [(int(t), int(q or 1)) for t, q in cur.fetchall()]

    def all_pi_products(self) -> list[str]:
        cur = self.conn.execute(
            """SELECT DISTINCT t.typeName
               FROM planetSchematicsTypeMap m
               JOIN invTypes t ON t.typeID = m.typeID
               WHERE m.isInput = 0
               ORDER BY t.typeName"""
        )
        return [r[0] for r in cur.fetchall()]


def _chunks(seq: list[int], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
