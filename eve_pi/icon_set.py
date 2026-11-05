"""Loaded EVE icons shared across the app, at the sizes the UI draws them."""

from __future__ import annotations

import tkinter as tk

# Key for the factory-planet icon, alongside item names in the icon cache.
FACTORY_ICON = "Advanced Industry Facility"
FACTORY_ICON_TYPE = "Barren Advanced Industry Facility"


def _dimmed(image: tk.PhotoImage, strength: float = 0.25) -> tk.PhotoImage:
    """A darkened copy of ``image`` for items outside the active trace."""
    width, height = image.width(), image.height()
    copy = tk.PhotoImage(master=image.tk, width=width, height=height)
    for y in range(height):
        for x in range(width):
            if not image.transparency_get(x, y):
                r, g, b = image.get(x, y)
                copy.put(f"#{int(r * strength):02x}{int(g * strength):02x}"
                         f"{int(b * strength):02x}", (x, y))
    return copy


class IconSet:
    def __init__(self, master):
        self._master = master
        self._large: dict[str, tk.PhotoImage] = {}
        self._small: dict[str, tk.PhotoImage] = {}
        self._dim: dict[str, tk.PhotoImage] = {}

    def load(self, paths: dict[str, str]) -> None:
        """Load 32px icon files. Unreadable files are skipped, so that item is
        shown as text only."""
        for name, path in paths.items():
            if name in self._large:
                continue
            try:
                large = tk.PhotoImage(master=self._master, file=path)
            except tk.TclError:
                continue
            self._large[name] = large
            self._small[name] = large.subsample(2)
            self._dim[name] = _dimmed(self._small[name])

    def large(self, name: str | None) -> tk.PhotoImage | None:
        return self._large.get(name)

    def small(self, name: str | None) -> tk.PhotoImage | None:
        return self._small.get(name)

    def dim(self, name: str | None) -> tk.PhotoImage | None:
        return self._dim.get(name)
