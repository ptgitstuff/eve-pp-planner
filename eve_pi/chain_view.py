"""Tkinter canvas that draws the production-chain map.

Hover an item to trace its inputs and outputs, click to pin the trace, click it
again (or empty space) to release, and double-click a product to send it to the
planner. The graph and colour rules live in ``chain_map.py``.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk

from .chain_map import (COL_P0, COL_P1, COL_P4, CURRENT_COLOR, DIM_COLOR,
                        NORMAL_COLOR, ChainGraph, describe, edge_color,
                        node_color, trace)
from .icon_set import IconSet

BACKGROUND = "#000000"
HEADING_COLOR = "#8a8a8a"
SUBHEADING_COLOR = "#5a5a5a"
RULE_COLOR = "#1c1c1c"
GLOW_FILL = "#0b2a3a"
GLOW_OUTLINE = "#1f6f94"

HEADINGS = ("Planets", "Resources", "Basic", "Refined", "Specialized", "Advanced")
# (x position as a fraction of the width, text anchor)
COLUMN_LAYOUT = ((0.05, "center"), (0.232, "e"), (0.272, "w"),
                 (0.50, "center"), (0.71, "center"), (0.91, "center"))
ARROW_X = 0.252

HEADER_HEIGHT = 58
MIN_ROW_HEIGHT = 17
MAX_ROW_HEIGHT = 22
ICON_SLOT = 19      # room an icon takes beside its label
HINT = ("Hover an item to trace its chain  ·  click to pin  ·  "
        "double-click a product to plan it")


@dataclass
class _Node:
    text_id: int
    backing_id: int
    box: tuple[float, float, float, float]
    icon_id: int | None = None


class ChainMapView(ttk.Frame):
    def __init__(self, master, icons: IconSet,
                 on_pick: Callable[[str], None] | None = None):
        super().__init__(master)
        self.graph: ChainGraph | None = None
        self._on_pick = on_pick
        self._nodes: dict[str, _Node] = {}
        self._icons = icons
        self._blank_icon = tk.PhotoImage(master=self, width=32, height=32)
        self._hover: str | None = None
        self._pinned: str | None = None
        self._size = (0, 0)

        # The blank image keeps this strip one height, so hovering never
        # resizes the canvas above it.
        self._detail = tk.Label(self, text=HINT, bg=BACKGROUND, fg=NORMAL_COLOR,
                                anchor="w", padx=10, pady=3, compound="left",
                                image=self._blank_icon, font=("Segoe UI", 9))
        self._detail.pack(side="bottom", fill="x")

        self._canvas = tk.Canvas(self, bg=BACKGROUND, highlightthickness=0)
        scroll = ttk.Scrollbar(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", self._on_leave)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Double-Button-1>", self._on_double_click)
        self._canvas.bind("<MouseWheel>", self._on_wheel)

    def set_graph(self, graph: ChainGraph | None) -> None:
        self.graph = graph
        self._hover = self._pinned = None
        self._layout()

    def refresh(self) -> None:
        """Redraw, picking up any newly loaded icons."""
        self._layout()

    # ------------------------------------------------------------------ drawing
    def _layout(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        self._nodes.clear()
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            return
        if self.graph is None:
            canvas.create_text(width / 2, height / 2, fill=NORMAL_COLOR,
                               font=("Segoe UI", 11),
                               text="No SDE data yet — click 'Download / Update SDE'.")
            canvas.configure(scrollregion=(0, 0, width, height))
            return

        graph = self.graph
        self._draw_headings(width)

        row_count = max(len(col) for col in graph.columns)
        row_height = (height - HEADER_HEIGHT - 12) / row_count
        row_height = max(MIN_ROW_HEIGHT, min(MAX_ROW_HEIGHT, row_height))
        content_height = HEADER_HEIGHT + 12 + row_count * row_height
        middle = max(HEADER_HEIGHT + 6 + (row_count * row_height) / 2,
                     HEADER_HEIGHT + (height - HEADER_HEIGHT) / 2)

        for index, column in enumerate(graph.columns):
            x_frac, anchor = COLUMN_LAYOUT[index]
            x = x_frac * width
            top = middle - len(column) * row_height / 2
            for row, name in enumerate(column):
                y = top + (row + 0.5) * row_height
                if index == COL_P1 and name:
                    canvas.create_text(ARROW_X * width, y, text="→",
                                       fill=DIM_COLOR, font=("Segoe UI", 9))
                if name:
                    self._add_node(name, x, y, anchor)

        canvas.configure(scrollregion=(0, 0, width, max(height, content_height)))
        self._apply()

    def _draw_headings(self, width: int) -> None:
        canvas = self._canvas
        for index, title in enumerate(HEADINGS):
            x_frac, anchor = COLUMN_LAYOUT[index]
            x = x_frac * width
            canvas.create_text(x, 22, text=title, anchor=anchor,
                               fill=HEADING_COLOR, font=("Segoe UI Light", 17))
            tier = index - COL_P0
            cycle = self.graph.cycle_by_tier.get(tier) if tier >= 1 else None
            if cycle:
                canvas.create_text(x, 43, text=f"P{tier} - {cycle}s", anchor=anchor,
                                   fill=SUBHEADING_COLOR, font=("Segoe UI", 8))
        canvas.create_line(0, HEADER_HEIGHT - 4, width, HEADER_HEIGHT - 4,
                           fill=RULE_COLOR)

    def _add_node(self, name: str, x: float, y: float, anchor: str) -> None:
        """Draw one label and its icon. The icon sits on the arrow side of the
        P0 and P1 columns, and to the left of the centred columns."""
        canvas = self._canvas
        icon = self._icons.small(name)
        text_x = x
        if icon is not None:
            if anchor == "e":
                text_x = x - ICON_SLOT
            elif anchor == "w":
                text_x = x + ICON_SLOT
            else:
                text_x = x + ICON_SLOT / 2
        text_id = canvas.create_text(text_x, y, text=name, anchor=anchor,
                                     fill=NORMAL_COLOR, font=("Segoe UI", 9))
        x0, y0, x1, y1 = canvas.bbox(text_id)

        icon_id = None
        if icon is not None:
            if anchor == "e":
                icon_x = x1 + ICON_SLOT / 2
            else:
                icon_x = x0 - ICON_SLOT / 2
            x0 = min(x0, icon_x - ICON_SLOT / 2)
            x1 = max(x1, icon_x + ICON_SLOT / 2)
            y0 = min(y0, y - 8)
            y1 = max(y1, y + 8)
            icon_id = canvas.create_image(icon_x, y, image=icon)

        box = (x0 - 3, y0, x1 + 3, y1)
        backing_id = canvas.create_rectangle(*box, fill=BACKGROUND, outline="")
        canvas.tag_lower(backing_id)
        self._nodes[name] = _Node(text_id, backing_id, box, icon_id)

    def _apply(self) -> None:
        """Recolour every item for the active trace and redraw its lines."""
        graph = self.graph
        if graph is None or not self._nodes:
            return
        canvas = self._canvas
        active_name = self._pinned or self._hover
        active = trace(graph, active_name) if active_name else None

        canvas.delete("edge")
        for name, node in self._nodes.items():
            color = node_color(active, name)
            canvas.itemconfigure(node.text_id, fill=color)
            is_current = active is not None and name == active.current
            canvas.itemconfigure(node.backing_id,
                                 fill=GLOW_FILL if is_current else BACKGROUND,
                                 outline=GLOW_OUTLINE if is_current else "")
            if node.icon_id is not None:
                image = (self._icons.dim(name) if color == DIM_COLOR
                         else self._icons.small(name))
                canvas.itemconfigure(node.icon_id, image=image)

        if active is not None:
            for edge in active.edges:
                source = self._nodes.get(edge.source)
                target = self._nodes.get(edge.target)
                if source and target:
                    canvas.create_line(
                        source.box[2], (source.box[1] + source.box[3]) / 2,
                        target.box[0], (target.box[1] + target.box[3]) / 2,
                        fill=edge_color(edge), width=1, tags="edge")
            canvas.tag_lower("edge")

        if active_name:
            prefix = "Pinned:  " if self._pinned else ""
            self._detail.configure(
                text="  " + prefix + describe(graph, active_name),
                fg=CURRENT_COLOR,
                image=self._icons.large(active_name) or self._blank_icon)
        else:
            self._detail.configure(text="  " + HINT, fg=NORMAL_COLOR,
                                   image=self._blank_icon)

    # ------------------------------------------------------------------- events
    def _hit(self, event) -> str | None:
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        for name, node in self._nodes.items():
            x0, y0, x1, y1 = node.box
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _on_resize(self, event) -> None:
        if (event.width, event.height) != self._size:
            self._size = (event.width, event.height)
            self._layout()

    def _on_motion(self, event) -> None:
        hit = self._hit(event)
        self._canvas.configure(cursor="hand2" if hit else "")
        if hit != self._hover:
            self._hover = hit
            if self._pinned is None:
                self._apply()

    def _on_leave(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            if self._pinned is None:
                self._apply()

    def _on_click(self, event) -> None:
        hit = self._hit(event)
        self._pinned = None if hit == self._pinned else hit
        self._apply()

    def _on_double_click(self, event) -> None:
        hit = self._hit(event)
        column = self.graph.column_of.get(hit) if (self.graph and hit) else None
        if (self._on_pick and column is not None
                and COL_P1 <= column <= COL_P4):
            self._on_pick(hit)

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
