"""Download the official CCP Static Data Export (JSON Lines) and build a local
SQLite cache. (Fresh copy used to work around a sandbox mount cache; identical
to sde.py.)"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator

from .pi_data import PLANET_TYPE_BY_ID

SDE_URL = ("https://developers.eveonline.com/static-data/"
           "eve-online-static-data-latest-jsonl.zip")

NEEDED = {
    "systems": "mapSolarSystems.jsonl",
    "planets": "mapPlanets.jsonl",
    "stargates": "mapStargates.jsonl",
    "schematics": "planetSchematics.jsonl",
    "types": "types.jsonl",
}

_USER_AGENT = "eve-pi-planner/2.0 (personal PI planning tool)"

ProgressCb = Callable[[str, float], None]


def _noop(msg: str, frac: float) -> None:
    pass


def default_cache_dir() -> str:
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
            or os.path.join(os.path.expanduser("~"), ".cache"))
    path = os.path.join(base, "eve_pi_planner")
    os.makedirs(path, exist_ok=True)
    return path


def default_db_path() -> str:
    return os.path.join(default_cache_dir(), "eve_pi.sqlite")


def _en(value):
    if isinstance(value, dict):
        return value.get("en")
    return value


_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
          (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"),
          (4, "IV"), (1, "I")]


def _roman(n: int) -> str:
    out = []
    for val, sym in _ROMAN:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


def populate_database(conn, readers, progress=None):
    progress = progress or _noop
    cur = conn.cursor()

    progress("Reading types...", 0.05)
    cur.execute("DROP TABLE IF EXISTS invTypes")
    cur.execute("CREATE TABLE invTypes (typeID INTEGER, groupID INTEGER, typeName TEXT)")
    batch = []
    for rec in readers["types"]:
        tid = rec.get("_key")
        name = _en(rec.get("name"))
        if tid is None or name is None:
            continue
        batch.append((int(tid), rec.get("groupID"), name))
        if len(batch) >= 5000:
            cur.executemany("INSERT INTO invTypes VALUES (?,?,?)", batch)
            batch.clear()
    if batch:
        cur.executemany("INSERT INTO invTypes VALUES (?,?,?)", batch)

    progress("Reading solar systems...", 0.25)
    cur.execute("DROP TABLE IF EXISTS mapSolarSystems")
    cur.execute("CREATE TABLE mapSolarSystems "
                "(solarSystemID INTEGER, solarSystemName TEXT, "
                " constellationID INTEGER, security REAL)")
    sys_name = {}
    batch = []
    for rec in readers["systems"]:
        sid = rec.get("_key")
        if sid is None:
            continue
        sid = int(sid)
        name = _en(rec.get("name")) or str(sid)
        sec = rec.get("securityStatus")
        sys_name[sid] = name
        batch.append((sid, name, rec.get("constellationID"),
                      float(sec) if sec is not None else 0.0))
        if len(batch) >= 5000:
            cur.executemany("INSERT INTO mapSolarSystems VALUES (?,?,?,?)", batch)
            batch.clear()
    if batch:
        cur.executemany("INSERT INTO mapSolarSystems VALUES (?,?,?,?)", batch)

    progress("Reading stargates...", 0.45)
    cur.execute("DROP TABLE IF EXISTS mapSolarSystemJumps")
    cur.execute("CREATE TABLE mapSolarSystemJumps "
                "(fromSolarSystemID INTEGER, toSolarSystemID INTEGER)")
    seen_jumps = set()
    batch = []
    for rec in readers["stargates"]:
        frm = rec.get("solarSystemID")
        dest = (rec.get("destination") or {}).get("solarSystemID")
        if frm is None or dest is None:
            continue
        edge = (int(frm), int(dest))
        if edge in seen_jumps:
            continue
        seen_jumps.add(edge)
        batch.append(edge)
        if len(batch) >= 5000:
            cur.executemany("INSERT INTO mapSolarSystemJumps VALUES (?,?)", batch)
            batch.clear()
    if batch:
        cur.executemany("INSERT INTO mapSolarSystemJumps VALUES (?,?)", batch)

    progress("Reading planets...", 0.65)
    cur.execute("DROP TABLE IF EXISTS planets")
    cur.execute("CREATE TABLE planets "
                "(planetID INTEGER, planetName TEXT, solarSystemID INTEGER, typeName TEXT)")
    batch = []
    for rec in readers["planets"]:
        pid = rec.get("_key")
        sid = rec.get("solarSystemID")
        tid = rec.get("typeID")
        if pid is None or sid is None or tid is None:
            continue
        ptype = PLANET_TYPE_BY_ID.get(int(tid))
        if ptype is None:
            continue
        idx = rec.get("celestialIndex")
        sname = sys_name.get(int(sid), str(sid))
        pname = f"{sname} {_roman(int(idx))}" if idx else sname
        batch.append((int(pid), pname, int(sid), f"Planet ({ptype})"))
        if len(batch) >= 5000:
            cur.executemany("INSERT INTO planets VALUES (?,?,?,?)", batch)
            batch.clear()
    if batch:
        cur.executemany("INSERT INTO planets VALUES (?,?,?,?)", batch)

    progress("Reading planet schematics...", 0.85)
    cur.execute("DROP TABLE IF EXISTS planetSchematics")
    cur.execute("CREATE TABLE planetSchematics "
                "(schematicID INTEGER, schematicName TEXT, cycleTime INTEGER)")
    cur.execute("DROP TABLE IF EXISTS planetSchematicsTypeMap")
    cur.execute("CREATE TABLE planetSchematicsTypeMap "
                "(schematicID INTEGER, typeID INTEGER, quantity INTEGER, isInput INTEGER)")
    sch_rows = []
    map_rows = []
    for rec in readers["schematics"]:
        sch = rec.get("_key")
        if sch is None:
            continue
        sch = int(sch)
        sch_rows.append((sch, _en(rec.get("name")), rec.get("cycleTime")))
        for item in rec.get("types", ()):
            tid = item.get("_key")
            if tid is None:
                continue
            is_input = 1 if item.get("isInput") else 0
            map_rows.append((sch, int(tid), item.get("quantity"), is_input))
    if sch_rows:
        cur.executemany("INSERT INTO planetSchematics VALUES (?,?,?)", sch_rows)
    if map_rows:
        cur.executemany("INSERT INTO planetSchematicsTypeMap VALUES (?,?,?,?)", map_rows)

    progress("Building indexes...", 0.95)
    _build_indexes(cur)
    conn.commit()


def _build_indexes(cur):
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sys_name ON mapSolarSystems(solarSystemName)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sys_id ON mapSolarSystems(solarSystemID)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_jump_from ON mapSolarSystemJumps(fromSolarSystemID)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_type_name ON invTypes(typeName)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_type_id ON invTypes(typeID)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sch_type ON planetSchematicsTypeMap(typeID, isInput)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_planet_sys ON planets(solarSystemID)")


def _member_name(zf, basename):
    for n in zf.namelist():
        if n.rsplit("/", 1)[-1] == basename:
            return n
    raise FileNotFoundError(
        f"{basename} not found in SDE zip (members e.g. {zf.namelist()[:5]})")


def _records_from_zip(zf, basename):
    name = _member_name(zf, basename)
    with zf.open(name) as fh:
        for line in io.TextIOWrapper(fh, encoding="utf-8"):
            line = line.strip()
            if line:
                yield json.loads(line)


def build_database(db_path=None, progress=None):
    db_path = db_path or default_db_path()
    progress = progress or _noop
    progress("Downloading official EVE SDE (JSON Lines)...", -1)
    req = urllib.request.Request(SDE_URL, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    progress("Opening archive...", 0.02)
    zf = zipfile.ZipFile(io.BytesIO(data))
    readers = {key: _records_from_zip(zf, base) for key, base in NEEDED.items()}
    tmp_path = db_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    conn = sqlite3.connect(tmp_path)
    try:
        populate_database(conn, readers, progress)
    finally:
        conn.close()
    if os.path.exists(db_path):
        os.remove(db_path)
    os.replace(tmp_path, db_path)
    progress("Done.", 1.0)
    return db_path


def database_exists(db_path=None):
    db_path = db_path or default_db_path()
    if not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM planets")
        ok = cur.fetchone()[0] > 0
        conn.close()
        return ok
    except sqlite3.Error:
        return False


def ensure_database(db_path=None, progress=None):
    db_path = db_path or default_db_path()
    if not database_exists(db_path):
        build_database(db_path, progress)
    return db_path
