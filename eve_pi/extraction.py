"""How much an extractor program really yields, cycle by cycle.

ESI's ``qty_per_cycle`` is a base figure per 15 minutes, not per cycle. A cycle's
output scales it by cycle length, decays as the program runs, and varies with a
repeating wave. The formula matches the game client's prediction as used by
community PI tools, and real colonies confirm the scale: the route out of an
extractor is sized to the peak, ``qty * cycle / 900 * 1.8``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

_BASE_SECONDS = 900.0
BASE_PER_HOUR = 3600.0 / _BASE_SECONDS
_DECAY = 0.012
_WAVE = 0.8


def cycle_output(qty: int, cycle_seconds: int, index: int) -> int:
    """Units from cycle ``index`` (from 0) of a program."""
    width = cycle_seconds / _BASE_SECONDS
    t = (index + 0.5) * width
    phase = qty ** 0.7
    wave = (math.cos(phase + t / 12.0) + math.cos(phase / 2.0 + t / 5.0)
            + math.cos(t / 2.0)) / 3.0
    output = width * qty / (1 + t * _DECAY) * (1 + _WAVE * max(wave, 0.0))
    return max(math.floor(output) - 1, 0)


def program_outputs(qty: int, cycle_seconds: int, cycles: int) -> list[int]:
    return [cycle_output(qty, cycle_seconds, i) for i in range(cycles)]


def program_average(qty: int, cycle_seconds: int, cycles: int) -> float:
    """Average units per hour over a whole program."""
    if qty <= 0 or cycle_seconds <= 0 or cycles <= 0:
        return 0.0
    return sum(program_outputs(qty, cycle_seconds, cycles)) * 3600.0 / (cycle_seconds * cycles)


Interval = tuple[float, float, float]     # start hour, end hour, units


def spread(intervals: Iterable[Interval], start: float, end: float,
           buckets: int) -> list[float]:
    """Units per hour in each of ``buckets`` equal steps from ``start`` to ``end``,
    sharing each interval's units evenly across its time."""
    if end <= start or buckets <= 0:
        return []
    width = (end - start) / buckets
    totals = [0.0] * buckets
    for first, last, units in intervals:
        if last <= first or last <= start or first >= end:
            continue
        per_hour = units / (last - first)
        low = max(int((max(first, start) - start) / width), 0)
        high = min(int((min(last, end) - start) / width), buckets - 1)
        for i in range(low, high + 1):
            left = start + i * width
            overlap = min(last, left + width) - max(first, left)
            if overlap > 0:
                totals[i] += per_hour * overlap
    return [total / width for total in totals]


def repeated_intervals(qty: int, cycle_seconds: int, cycles: int,
                       hours: float) -> list[Interval]:
    """A program restarted each time it ends, from hour 0 to ``hours``."""
    if qty <= 0 or cycle_seconds <= 0 or cycles <= 0:
        return []
    length = cycle_seconds / 3600.0
    outputs = program_outputs(qty, cycle_seconds, cycles)
    intervals: list[Interval] = []
    index = 0
    while index * length < hours:
        at = index * length
        intervals.append((at, at + length, outputs[index % cycles]))
        index += 1
    return intervals


def total_within(intervals: Iterable[Interval], start: float, end: float) -> float:
    """Units in the window, counting a part-finished cycle in proportion."""
    total = 0.0
    for first, last, units in intervals:
        overlap = min(last, end) - max(first, start)
        if overlap > 0 and last > first:
            total += units * overlap / (last - first)
    return total
