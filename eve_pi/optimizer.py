"""Fewest-systems optimizer (minimum set cover).

Given a set of requirements (P0 resources, or planet types) and the candidate
systems in range — each covering some subset of those requirements — find the
*fewest* systems whose combined coverage satisfies every requirement.

This is set cover (NP-hard), but the universe here is tiny (<= 15 P0 resources
or <= 8 planet types), so an exact branch-and-bound with iterative deepening is
fast in practice. A node cap guards pathological inputs, falling back to a
greedy approximation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Candidate:
    system_id: int
    covered: frozenset[str]
    jumps: int


@dataclass
class Solution:
    system_ids: list[int]
    total_jumps: int
    size: int
    exact: bool = True           # False if produced by greedy fallback
    alternatives: list[list[int]] = field(default_factory=list)


_NODE_CAP = 200_000


def _prune_dominated(cands: list[Candidate]) -> list[Candidate]:
    """Drop candidates whose coverage is a subset of another at <= jump cost."""
    keep: list[Candidate] = []
    for c in sorted(cands, key=lambda x: (-len(x.covered), x.jumps)):
        dominated = False
        for k in keep:
            if c.covered <= k.covered and k.jumps <= c.jumps:
                dominated = True
                break
        if not dominated:
            keep.append(c)
    return keep


def _greedy(universe: set[str], cands: list[Candidate]) -> Solution:
    uncovered = set(universe)
    chosen: list[Candidate] = []
    pool = list(cands)
    while uncovered:
        pool.sort(key=lambda c: (-len(c.covered & uncovered), c.jumps))
        best = pool[0]
        if not (best.covered & uncovered):
            break  # cannot cover the rest
        chosen.append(best)
        uncovered -= best.covered
        pool.remove(best)
    return Solution(
        system_ids=[c.system_id for c in chosen],
        total_jumps=sum(c.jumps for c in chosen),
        size=len(chosen),
        exact=False,
    )


def min_set_cover(
    universe: set[str],
    candidates: list[Candidate],
    max_alternatives: int = 8,
) -> Solution | None:
    """Return the fewest-systems Solution, or None if the universe is
    impossible to cover with the given candidates."""
    universe = set(universe)
    if not universe:
        return Solution(system_ids=[], total_jumps=0, size=0)

    # Only candidates that contribute anything.
    cands = [c for c in candidates if c.covered & universe]
    coverable = set().union(*(c.covered for c in cands)) if cands else set()
    if not universe <= coverable:
        return None  # some requirement has no source in range

    cands = _prune_dominated(cands)

    # Map each requirement to the candidates that cover it (for MRV branching).
    covers: dict[str, list[Candidate]] = {u: [] for u in universe}
    for c in cands:
        for u in c.covered & universe:
            covers[u].append(c)

    best_size = [len(universe) + 1]      # upper bound
    solutions: list[tuple[int, tuple[int, ...]]] = []  # (total_jumps, ids)
    nodes = [0]
    overflow = [False]

    def search(uncovered: frozenset[str], chosen: tuple[Candidate, ...],
               jumps: int, limit: int) -> None:
        if overflow[0]:
            return
        nodes[0] += 1
        if nodes[0] > _NODE_CAP:
            overflow[0] = True
            return
        if not uncovered:
            ids = tuple(sorted(c.system_id for c in chosen))
            solutions.append((jumps, ids))
            return
        if len(chosen) >= limit:
            return
        # Most-constrained requirement first.
        target = min(uncovered, key=lambda u: len(covers[u]))
        # Try candidates that cover it, best coverage / cheapest first.
        options = sorted(
            covers[target],
            key=lambda c: (-len(c.covered & uncovered), c.jumps),
        )
        for c in options:
            if c in chosen:
                continue
            search(uncovered - c.covered, chosen + (c,), jumps + c.jumps, limit)

    full = frozenset(universe)
    for limit in range(1, len(universe) + 1):
        solutions.clear()
        nodes[0] = 0
        search(full, (), 0, limit)
        if overflow[0]:
            return _greedy(universe, cands)
        if solutions:
            best_size[0] = limit
            break

    if not solutions:
        return _greedy(universe, cands)

    # Dedupe and rank: fewest jumps, then by id for stability.
    seen: set[tuple[int, ...]] = set()
    ranked: list[tuple[int, tuple[int, ...]]] = []
    for jumps, ids in sorted(solutions, key=lambda x: (x[0], x[1])):
        if ids in seen:
            continue
        seen.add(ids)
        ranked.append((jumps, ids))

    best_jumps, best_ids = ranked[0]
    alts = [list(ids) for _, ids in ranked[1:max_alternatives + 1]]
    return Solution(
        system_ids=list(best_ids),
        total_jumps=best_jumps,
        size=best_size[0],
        exact=True,
        alternatives=alts,
    )
