"""EVE item icons from CCP's public image server, cached on disk as PNGs.

Runs without Tkinter so it can be used from a worker thread and tested without a
display. Icons are optional: anything that can't be fetched is left out, and the
caller carries on with text only.
"""

from __future__ import annotations

import os
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

IMAGE_SERVER = "https://images.evetech.net/types/{type_id}/icon?size={size}"
ICON_SIZE = 32
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_USER_AGENT = "eve-pi-planner/3.0 (personal PI planning tool)"


def default_icon_dir() -> str:
    from .sde import default_cache_dir
    return os.path.join(default_cache_dir(), "icons")


def icon_path(folder: str, type_id: int, size: int = ICON_SIZE) -> str:
    return os.path.join(folder, f"{type_id}_{size}.png")


def cached_icons(name_to_id: dict[str, int], folder: str,
                 size: int = ICON_SIZE) -> dict[str, str]:
    """name -> file path for the icons already on disk."""
    paths = {name: icon_path(folder, tid, size) for name, tid in name_to_id.items()}
    return {name: path for name, path in paths.items() if os.path.exists(path)}


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def fetch_icons(name_to_id: dict[str, int], folder: str, size: int = ICON_SIZE,
                download: Callable[[str], bytes] = _download) -> dict[str, str]:
    """Download whatever isn't cached yet. Returns name -> path for every icon
    now available; failed downloads are simply missing from the result."""
    os.makedirs(folder, exist_ok=True)

    def fetch_one(type_id: int) -> None:
        path = icon_path(folder, type_id, size)
        try:
            data = download(IMAGE_SERVER.format(type_id=type_id, size=size))
        except Exception:
            return
        if not data.startswith(PNG_SIGNATURE):
            return
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    have = cached_icons(name_to_id, folder, size)
    missing = {tid for name, tid in name_to_id.items() if name not in have}
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetch_one, sorted(missing)))
    return cached_icons(name_to_id, folder, size)
