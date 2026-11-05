"""PI reference: planet outputs and full production-chain trees (P0 -> P4).

Used by the GUI's PI guide tab. ``chain_lines`` walks a product's schematic
graph and returns indented rows, bottoming out at P0 raw materials annotated
with the planet types that yield them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import PIDatabase
from .pi_data import P0_RESOURCES, resources_to_planet_types


@dataclass
class ChainRow:
    depth: int            # 0 = the product itself
    name: str
    is_p0: bool
    sources: str          # for P0: planet types that yield it; else ""
    per_cycle: float      # units of this item per 1 output of its parent


def chain_lines(db: PIDatabase, product_name: str) -> list[ChainRow]:
    """Indented production tree for ``product_name`` down to P0 materials."""
    p0 = set(P0_RESOURCES)
    rp = resources_to_planet_types(p0)          # P0 -> {planet types}
    outputs = db.schematic_outputs_q()          # typeID -> (schematicID, out qty)
    rows: list[ChainRow] = []

    def walk(name: str, depth: int, per_cycle: float, seen: frozenset[int]) -> None:
        tid = db.type_id_by_name(name)
        if name in p0:
            src = ", ".join(sorted(rp.get(name, set()))) or "?"
            rows.append(ChainRow(depth, name, True, src, per_cycle))
            return
        rows.append(ChainRow(depth, name, False, "", per_cycle))
        if tid is None or tid not in outputs or tid in seen:
            return
        sid, out_q = outputs[tid]
        out_q = out_q or 1
        for in_tid, in_q in db.schematic_inputs_q(sid):
            nm = db.type_name_by_id(in_tid) or str(in_tid)
            walk(nm, depth + 1, in_q / out_q, seen | {tid})

    walk(product_name, 0, 1.0, frozenset())
    return rows
