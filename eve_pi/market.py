"""Jita market data from ESI (the official CCP API).

Pulls best Jita 4-4 buy/sell from the order book and average daily *traded*
volume from market history, then values your hourly output in ISK.

Network access happens on the user's machine. ``fetch_prices`` takes a
name -> typeID mapping (resolved on the caller's thread) so it never touches the
SQLite connection from a worker thread. The valuation helpers are pure and
unit-tested.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field

ESI = "https://esi.evetech.net/latest"
THE_FORGE = 10000002          # region containing Jita
JITA_4_4 = 60003760           # Jita IV - Moon 4 - Caldari Navy Assembly Plant
_UA = "eve-pi-planner/3.0 (personal PI planning tool)"


@dataclass
class MarketPrice:
    type_name: str
    type_id: int
    buy: float | None = None      # best (highest) buy price at Jita 4-4
    sell: float | None = None     # best (lowest) sell price at Jita 4-4
    volume: float | None = None   # avg daily traded volume (region, recent days)

    @property
    def split(self) -> float | None:
        if self.buy is not None and self.sell is not None:
            return (self.buy + self.sell) / 2.0
        return self.sell if self.sell is not None else self.buy

    def price(self, basis: str) -> float | None:
        return {"buy": self.buy, "sell": self.sell, "split": self.split}.get(basis)


# ---------------------------------------------------------------------------
# ESI fetch (network)
# ---------------------------------------------------------------------------
def _get(url: str, timeout: float = 30.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return body, resp.headers


def best_buy_sell(type_id: int, region: int = THE_FORGE,
                  station: int = JITA_4_4, timeout: float = 30.0):
    """Return (best_buy, best_sell) at the given station, scanning all pages."""
    buy = sell = None
    page, pages = 1, 1
    while page <= pages:
        url = (f"{ESI}/markets/{region}/orders/"
               f"?type_id={type_id}&order_type=all&page={page}")
        data, headers = _get(url, timeout)
        try:
            pages = int(headers.get("X-Pages", 1))
        except (TypeError, ValueError):
            pages = 1
        for o in data:
            if o.get("location_id") != station:
                continue
            p = o.get("price")
            if p is None:
                continue
            if o.get("is_buy_order"):
                buy = p if buy is None else max(buy, p)
            else:
                sell = p if sell is None else min(sell, p)
        page += 1
    return buy, sell


def avg_daily_volume(type_id: int, region: int = THE_FORGE,
                     days: int = 7, timeout: float = 30.0):
    """Average daily traded volume over the most recent ``days`` of history."""
    data, _ = _get(f"{ESI}/markets/{region}/history/?type_id={type_id}", timeout)
    if not data:
        return None
    recent = data[-days:]
    vols = [d.get("volume", 0) for d in recent]
    return (sum(vols) / len(vols)) if vols else None


def fetch_prices(name_to_id: dict[str, int], days: int = 7,
                 timeout: float = 30.0) -> dict[str, MarketPrice | None]:
    """Fetch Jita prices + volume for each name. Returns name -> MarketPrice
    (or None if the type is unknown or the request failed)."""
    out: dict[str, MarketPrice | None] = {}
    for name, tid in name_to_id.items():
        if tid is None:
            out[name] = None
            continue
        try:
            buy, sell = best_buy_sell(tid, timeout=timeout)
            vol = avg_daily_volume(tid, days=days, timeout=timeout)
            out[name] = MarketPrice(name, tid, buy, sell, vol)
        except Exception:
            out[name] = None
    return out


# ---------------------------------------------------------------------------
# valuation (pure)
# ---------------------------------------------------------------------------
@dataclass
class ValueRow:
    name: str
    rate_per_hour: float
    price: float | None
    isk_per_hour: float | None
    kind: str = "output"          # "output" or "surplus"


@dataclass
class Valuation:
    basis: str
    rows: list[ValueRow] = field(default_factory=list)
    total_isk_hour: float = 0.0
    total_isk_day: float = 0.0


def value_outputs(items, prices: dict[str, MarketPrice | None],
                  basis: str = "split") -> Valuation:
    """``items``: iterable of (name, rate_per_hour, kind). Multiply each by its
    Jita price (at ``basis``) to estimate ISK/hour and ISK/day."""
    val = Valuation(basis=basis)
    total = 0.0
    for name, rate, kind in items:
        mp = prices.get(name)
        price = mp.price(basis) if mp else None
        isk = (rate * price) if (price is not None) else None
        if isk:
            total += isk
        val.rows.append(ValueRow(name, rate, price, isk, kind))
    val.total_isk_hour = total
    val.total_isk_day = total * 24.0
    return val
