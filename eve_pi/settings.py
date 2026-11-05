"""Remember the app's settings between sessions, in the user cache dir. Saved values
are checked before use, so a damaged or outdated file can't break the app."""

from __future__ import annotations

import json
import os
import re

MIN_WINDOW = (640, 480)


def default_settings_path() -> str:
    from .sde import default_cache_dir
    return os.path.join(default_cache_dir(), "settings.json")


def load_json(path: str) -> dict:
    """A JSON object from ``path``. Missing or damaged files just give nothing."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: str, data: dict) -> None:
    """Write via a temporary file, so a crash can't leave half a file behind."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(temporary, path)


def coerce(value, default):
    """``value`` if it's the same type as ``default``, otherwise ``default``."""
    return value if type(value) is type(default) else default


def window_size(text, screen_width: int, screen_height: int) -> str | None:
    """A saved "WxH" as a size that suits this screen, or None if it's unusable.
    Only the size is kept, so the window can't reopen on an unplugged monitor."""
    match = re.fullmatch(r"(\d+)x(\d+)", text) if isinstance(text, str) else None
    if match is None:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width < MIN_WINDOW[0] or height < MIN_WINDOW[1]:
        return None
    return f"{min(width, screen_width)}x{min(height, screen_height)}"
