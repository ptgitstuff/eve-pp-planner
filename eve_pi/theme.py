"""Dark theme applied to the whole Tkinter app."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#0b0d10"
PANEL = "#12151a"
RAISED = "#1b2027"
HOVER = "#242b34"
BORDER = "#2a313a"
FG = "#c9ced6"
MUTED = "#6c7480"
ACCENT = "#79cef4"
SELECTED = "#14394b"


def apply_theme(root: tk.Tk) -> None:
    """Style ttk widgets and set defaults for plain Tk ones. Call this before
    building any widgets so the option database applies to them."""
    root.configure(bg=BG)

    for pattern, value in (
        ("*Text.background", PANEL),
        ("*Text.foreground", FG),
        ("*Text.insertBackground", FG),
        ("*Text.selectBackground", SELECTED),
        ("*Text.selectForeground", FG),
        ("*Text.borderWidth", 0),
        ("*Text.highlightThickness", 0),
        ("*Text.padX", 10),
        ("*Text.padY", 8),
        ("*Listbox.background", RAISED),
        ("*Listbox.foreground", FG),
        ("*Listbox.selectBackground", SELECTED),
        ("*Listbox.selectForeground", FG),
        ("*Listbox.borderWidth", 0),
        ("*Listbox.highlightThickness", 0),
        ("*Menu.background", RAISED),
        ("*Menu.foreground", FG),
        ("*Menu.activeBackground", SELECTED),
        ("*Menu.activeForeground", FG),
        ("*Toplevel.background", BG),
    ):
        root.option_add(pattern, value)

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=FG, fieldbackground=RAISED,
                    bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                    troughcolor=PANEL, selectbackground=SELECTED,
                    selectforeground=FG, insertcolor=FG, arrowcolor=FG,
                    focuscolor=BG)
    style.map(".", foreground=[("disabled", MUTED)])

    style.configure("TButton", background=RAISED, padding=(10, 4),
                    lightcolor=RAISED, darkcolor=RAISED)
    style.map("TButton",
              background=[("disabled", PANEL), ("active", HOVER)],
              lightcolor=[("active", HOVER)], darkcolor=[("active", HOVER)])

    style.configure("TNotebook", background=BG, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                    padding=(12, 5), lightcolor=PANEL)
    style.map("TNotebook.Tab",
              background=[("selected", RAISED), ("active", HOVER)],
              foreground=[("selected", ACCENT), ("active", FG)],
              lightcolor=[("selected", RAISED)])

    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=FG, rowheight=22, borderwidth=0)
    style.map("Treeview", background=[("selected", SELECTED)],
              foreground=[("selected", FG)])
    # The app's trees are flat lists, so drop the expand arrow's reserved space
    # and let a row's icon sit at the left edge.
    style.layout("Treeview.Item", [("Treeitem.padding", {"sticky": "nsew", "children": [
        ("Treeitem.image", {"side": "left", "sticky": ""}),
        ("Treeitem.focus", {"side": "left", "sticky": "", "children": [
            ("Treeitem.text", {"side": "left", "sticky": ""})]})]})])
    style.configure("Treeview.Heading", background=RAISED, foreground=FG,
                    relief="flat", lightcolor=RAISED, darkcolor=RAISED)
    style.map("Treeview.Heading", background=[("active", HOVER)])

    for field in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(field, fieldbackground=RAISED, background=RAISED,
                        foreground=FG, lightcolor=RAISED, darkcolor=RAISED)
    style.map("TCombobox",
              fieldbackground=[("readonly", RAISED)],
              foreground=[("readonly", FG)],
              selectbackground=[("readonly", RAISED)],
              selectforeground=[("readonly", FG)],
              background=[("active", HOVER)])
    style.map("TSpinbox", background=[("active", HOVER)])

    for toggle in ("TCheckbutton", "TRadiobutton"):
        style.configure(toggle, background=BG, foreground=FG,
                        indicatorbackground=RAISED, indicatorforeground=ACCENT,
                        upperbordercolor=BORDER, lowerbordercolor=BORDER)
        style.map(toggle,
                  background=[("active", BG)],
                  indicatorbackground=[("selected", SELECTED), ("active", HOVER)])

    style.configure("TLabelframe", background=BG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
    style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL)
    style.configure("TScrollbar", background=RAISED, troughcolor=PANEL,
                    lightcolor=RAISED, darkcolor=RAISED, bordercolor=PANEL,
                    arrowcolor=MUTED)
    style.map("TScrollbar", background=[("active", HOVER)])
