"""Read a shopping list from a Janice appraisal link or a pasted item list.

Janice (janice.e-351.com) loads appraisals through the JSON-RPC call its own web
page uses. That call isn't a documented public API and could change, so pasting
the item list as text always works as a fallback.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

RPC_URL = "https://janice.e-351.com/api/rpc/v1?m=Appraisal.find"
_USER_AGENT = "eve-pi-planner/3.0 (personal PI planning tool)"
_LINK_RE = re.compile(r"janice\.e-351\.com/a/([A-Za-z0-9]+)", re.IGNORECASE)
_NAME_THEN_AMOUNT = re.compile(r"^(.*?\S)(?:\s+[xX])?\s+([\d][\d,. ]*)$")
_AMOUNT_THEN_NAME = re.compile(r"^([\d][\d,.]*)\s*[xX]?\s+(\S.*)$")


class JaniceError(Exception):
    """The appraisal couldn't be loaded; the message is safe to show the user."""


@dataclass
class Appraisal:
    code: str
    items: dict[str, int] = field(default_factory=dict)
    split_value: float | None = None
    expires: str | None = None


def extract_code(text: str) -> str | None:
    """The appraisal code from a Janice link anywhere in ``text``."""
    match = _LINK_RE.search(text or "")
    return match.group(1) if match else None


def _amount(text: str) -> int | None:
    digits = re.sub(r"[,. ]", "", text)
    return int(digits) if digits.isdigit() else None


def parse_item_list(text: str) -> tuple[dict[str, int], list[str]]:
    """Parse lines like ``Coolant<tab>199036``, ``Coolant x 5`` or ``5 Coolant``.
    Returns (name -> total amount, lines that couldn't be read)."""
    items: dict[str, int] = {}
    unread: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _NAME_THEN_AMOUNT.match(line)
        if match:
            name, amount = match.group(1), _amount(match.group(2))
        else:
            match = _AMOUNT_THEN_NAME.match(line)
            if match:
                amount, name = _amount(match.group(1)), match.group(2)
            else:
                name, amount = None, None
        if name and amount:
            items[name.strip()] = items.get(name.strip(), 0) + amount
        else:
            unread.append(line)
    return items, unread


def _post(url: str, body: bytes, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": _USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_appraisal(code: str,
                    post: Callable[[str, bytes], bytes] = _post) -> Appraisal:
    """Load an appraisal's items. Raises JaniceError if that isn't possible."""
    body = json.dumps({"id": 1, "method": "Appraisal.find",
                       "params": {"code": code}}).encode("utf-8")
    try:
        reply = json.loads(post(RPC_URL, body).decode("utf-8"))
    except Exception as exc:
        raise JaniceError(f"Couldn't reach Janice ({exc}). You can paste the "
                          "item list instead.") from exc

    result = reply.get("result") if isinstance(reply, dict) else None
    if not isinstance(result, dict):
        raise JaniceError(f"Janice has no appraisal {code}; it may have expired. "
                          "You can paste the item list instead.")

    appraisal = Appraisal(code=code, expires=result.get("expires"))
    for item in result.get("items") or []:
        name = (item.get("itemType") or {}).get("name")
        amount = item.get("amount")
        if name and amount:
            appraisal.items[name] = appraisal.items.get(name, 0) + int(amount)
    if not appraisal.items:
        raise JaniceError(f"Appraisal {code} has no items.")
    prices = result.get("effectivePrices") or {}
    appraisal.split_value = prices.get("totalSplitPrice")
    return appraisal
