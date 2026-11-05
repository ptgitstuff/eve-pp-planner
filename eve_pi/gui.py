"""Tkinter desktop GUI for the Eve PI planner."""

from __future__ import annotations

import queue
import threading
from dataclasses import replace

import tkinter as tk
from tkinter import messagebox, ttk

from . import market, profitability, sde
from .chain_map import build_chain_graph
from .chain_view import ChainMapView
from .characters_view import CharactersTab
from .colonies import slot_roster
from .fulfil import fulfil_order, order_plan
from .fulfil_view import FulfilView
from .db import PIDatabase
from .facilities import build_breakdown, facility_rates
from .guide import chain_lines
from .janice import JaniceError, extract_code, fetch_appraisal, parse_item_list
from .icon_set import FACTORY_ICON, FACTORY_ICON_TYPE, IconSet
from .icons import cached_icons, default_icon_dir, fetch_icons
from .pi_data import PLANET_RESOURCES, PLANET_TYPES, P0_TO_P1
from .planner import Plan, make_plan
from .production import (ORDER_PRODUCT, PlanetPick, build_report, calculate_from_picks,
                        characters_needed, default_selection_path,
                        load_selection, save_selection)
from .results_view import OutputView, ProductionView
from .theme import apply_theme


class AutocompleteEntry(ttk.Frame):
    """Entry + dropdown listbox that suggests solar-system names."""

    def __init__(self, master, lookup):
        super().__init__(master)
        self._lookup = lookup
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=28)
        self.entry.pack(fill="x")
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<FocusOut>", lambda e: self.after(150, self._hide))
        self._lb = None

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: str) -> None:
        self.var.set(value)

    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            if self._lb:
                self._lb.event_generate(f"<{event.keysym}>")
            return
        text = self.var.get().strip()
        matches = self._lookup(text)[:12] if len(text) >= 2 else []
        if not matches:
            self._hide()
            return
        self._show(matches)

    def _show(self, matches):
        if self._lb is None:
            top = self.winfo_toplevel()
            self._lb = tk.Listbox(top, height=min(8, len(matches)))
            self._lb.bind("<<ListboxSelect>>", self._choose)
            self._lb.bind("<Return>", self._choose)
        self._lb.delete(0, tk.END)
        for m in matches:
            self._lb.insert(tk.END, m)
        x = self.entry.winfo_rootx() - self.winfo_toplevel().winfo_rootx()
        y = (self.entry.winfo_rooty() - self.winfo_toplevel().winfo_rooty()
             + self.entry.winfo_height())
        self._lb.place(x=x, y=y, width=self.entry.winfo_width())
        self._lb.lift()

    def _choose(self, event=None):
        if self._lb and self._lb.curselection():
            self.var.set(self._lb.get(self._lb.curselection()[0]))
        self._hide()
        self.entry.focus_set()

    def _hide(self):
        if self._lb is not None:
            self._lb.destroy()
            self._lb = None


def _add_icon_column(tree: ttk.Treeview) -> None:
    """Use a Treeview's first column just for each row's icon."""
    tree.column("#0", width=30, minwidth=30, stretch=False, anchor="center")


class PlannerApp(ttk.Frame):
    def __init__(self, master, db_path: str):
        super().__init__(master, padding=10)
        self.master = master
        self.db_path = db_path
        self.db: PIDatabase | None = None
        self.pi_products: list[str] = []
        self._last_plan: Plan | None = None
        self._picks: dict[int, PlanetPick] = {}
        self._servable: dict[int, list[str]] = {}
        self._sel_path = default_selection_path()
        self.saved = load_selection(self._sel_path)
        self._prices: dict = {}
        self._value_items: list = []
        self._facility_rates: dict = {}
        self._q: queue.Queue = queue.Queue()
        self._mq: queue.Queue = queue.Queue()
        self._bq: queue.Queue = queue.Queue()
        self._iq: queue.Queue = queue.Queue()
        self._jq: queue.Queue = queue.Queue()
        self.icons = IconSet(self)
        self.pack(fill="both", expand=True)

        self._build_dataline()
        self._build_controls()
        self._build_results()

        self._try_open_db()

    def _build_dataline(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        self.data_status = ttk.Label(bar, text="Checking SDE data...")
        self.data_status.pack(side="left")
        self.dl_btn = ttk.Button(bar, text="Download / Update SDE",
                                 command=self._start_download)
        self.dl_btn.pack(side="right")
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1.0)

    def _build_controls(self):
        ctl = ttk.LabelFrame(self, text="Plan", padding=10)
        ctl.pack(fill="x")

        ttk.Label(ctl, text="Staging system:").grid(row=0, column=0, sticky="w")
        self.staging = AutocompleteEntry(ctl, self._lookup_systems)
        self.staging.grid(row=0, column=1, sticky="w", padx=(4, 16))

        ttk.Label(ctl, text="Max jumps:").grid(row=0, column=2, sticky="w")
        self.jumps = tk.IntVar(value=5)
        ttk.Spinbox(ctl, from_=0, to=30, width=5, textvariable=self.jumps)\
            .grid(row=0, column=3, sticky="w", padx=(4, 16))

        ttk.Label(ctl, text="Planet slots:").grid(row=0, column=4, sticky="w")
        self.slots = tk.IntVar(value=6)
        ttk.Spinbox(ctl, from_=1, to=144, width=5, textvariable=self.slots)\
            .grid(row=0, column=5, sticky="w", padx=(4, 16))

        sec = ttk.Frame(ctl)
        sec.grid(row=0, column=6, sticky="w")
        ttk.Label(sec, text="Security:").pack(side="left")
        self.band_vars = {}
        for b in ("High", "Low", "Null"):
            v = tk.BooleanVar(value=True)
            self.band_vars[b] = v
            ttk.Checkbutton(sec, text=b, variable=v).pack(side="left")

        self.mode = tk.StringVar(value="product")
        modes = ttk.Frame(ctl)
        modes.grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))
        ttk.Radiobutton(modes, text="Final product", value="product",
                        variable=self.mode, command=self._sync_mode).pack(side="left")
        ttk.Radiobutton(modes, text="Planet types", value="types",
                        variable=self.mode, command=self._sync_mode).pack(
                            side="left", padx=(12, 0))
        self.single_system_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(modes, text="Keep whole chain in one system (don't mix)",
                        variable=self.single_system_var).pack(side="left", padx=(24, 0))

        self.product_frame = ttk.Frame(ctl)
        self.product_frame.grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))
        ttk.Label(self.product_frame, text="Product:").pack(side="left")
        self.product = tk.StringVar()
        self.product_cb = ttk.Combobox(self.product_frame, textvariable=self.product,
                                       width=34, state="readonly")
        self.product_cb.pack(side="left", padx=4)

        self.types_frame = ttk.Frame(ctl)
        self.types_frame.grid(row=3, column=0, columnspan=7, sticky="w", pady=(6, 0))
        ttk.Label(self.types_frame, text="Planet types:").pack(side="left")
        self.type_vars = {}
        for t in PLANET_TYPES:
            v = tk.BooleanVar(value=False)
            self.type_vars[t] = v
            ttk.Checkbutton(self.types_frame, text=t, variable=v).pack(side="left")

        self.find_btn = ttk.Button(ctl, text="Find planets", command=self._find)
        self.find_btn.grid(row=4, column=0, columnspan=7, sticky="w", pady=(10, 0))

        self._sync_mode()

    def _sync_mode(self):
        if self.mode.get() == "product":
            self.product_frame.grid()
            self.types_frame.grid_remove()
        else:
            self.product_frame.grid_remove()
            self.types_frame.grid()

    def _build_results(self):
        self.summary = ttk.Label(self, text="", font=("", 10, "bold"))
        self.summary.pack(fill="x", pady=(10, 4))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.results_nb = nb

        # Chain map tab
        self.chain_map = ChainMapView(nb, self.icons, on_pick=self._plan_product)
        nb.add(self.chain_map, text="Chain map")

        # Fulfil order tab
        self._build_fulfil_tab(nb)

        # Characters tab
        self.characters_tab = CharactersTab(nb, self.icons, lambda: self.db)
        nb.add(self.characters_tab, text="Characters")
        self.characters_tab.on_change = self._sync_order_slots
        self._sync_order_slots()

        # Recommended set-up tab
        f1 = ttk.Frame(nb)
        nb.add(f1, text="Recommended set-up")
        cols = ("system", "sec", "jumps", "planet", "ptype", "resource")
        self.setup_tab = f1
        self.plan_tv = ttk.Treeview(f1, columns=cols, show="tree headings", height=12)
        _add_icon_column(self.plan_tv)
        for c, w in zip(cols, (120, 50, 55, 130, 90, 150)):
            self.plan_tv.heading(c, text=c.capitalize())
            self.plan_tv.column(c, width=w, anchor="w")
        self.plan_tv.pack(side="left", fill="both", expand=True)
        sb1 = ttk.Scrollbar(f1, command=self.plan_tv.yview)
        sb1.pack(side="right", fill="y")
        self.plan_tv.configure(yscrollcommand=sb1.set)

        # Best systems tab
        f7 = ttk.Frame(nb)
        nb.add(f7, text="Best systems")
        bar7 = ttk.Frame(f7, padding=(0, 6))
        bar7.pack(fill="x")
        ttk.Label(bar7, text="Most profitable systems in range for tier:").pack(side="left")
        self.best_tier = tk.StringVar(value="P2")
        ttk.Combobox(bar7, textvariable=self.best_tier, width=5, state="readonly",
                     values=["P1", "P2", "P3", "P4"]).pack(side="left", padx=4)
        self.best_btn = ttk.Button(bar7, text="Find best systems",
                                   command=self._find_best)
        self.best_btn.pack(side="left", padx=(6, 0))
        self.best_status = ttk.Label(bar7, text="")
        self.best_status.pack(side="left", padx=8)
        cols7 = ("rank", "system", "sec", "jumps", "product",
                 "isk_planet", "isk_hr", "planets")
        self.best_tv = ttk.Treeview(f7, columns=cols7, show="tree headings", height=12)
        _add_icon_column(self.best_tv)
        heads7 = ("#", "System", "Sec", "Jumps", "Best mix (fills slots)",
                  "ISK/planet/hr", "Total ISK/hr", "Planets")
        widths7 = (35, 110, 45, 50, 200, 110, 120, 60)
        for c, h, w in zip(cols7, heads7, widths7):
            self.best_tv.heading(c, text=h)
            self.best_tv.column(c, width=w, anchor="w")
        self.best_tv.pack(side="left", fill="both", expand=True)
        sb7 = ttk.Scrollbar(f7, command=self.best_tv.yview)
        sb7.pack(side="right", fill="y")
        self.best_tv.configure(yscrollcommand=sb7.set)

        # Select planets tab
        f6 = ttk.Frame(nb)
        nb.add(f6, text="Select planets")
        ttk.Label(f6, text="Tick a planet to use it. Right-click to set its P0 "
                          "output (units/hr) and which resource it extracts.")\
            .pack(anchor="w", pady=(4, 4))
        cols6 = ("use", "planet", "system", "jumps", "type", "extracts", "rate")
        self.sel_tv = ttk.Treeview(f6, columns=cols6, show="tree headings", height=12)
        _add_icon_column(self.sel_tv)
        widths6 = (40, 140, 110, 50, 80, 140, 100)
        heads6 = ("Use", "Planet", "System", "Jumps", "Type", "Extracts", "P0/hr")
        for c, w, h in zip(cols6, widths6, heads6):
            self.sel_tv.heading(c, text=h)
            self.sel_tv.column(c, width=w, anchor="w")
        self.sel_tv.column("use", anchor="center")
        self.sel_tv.pack(side="left", fill="both", expand=True)
        sb6 = ttk.Scrollbar(f6, command=self.sel_tv.yview)
        sb6.pack(side="right", fill="y")
        self.sel_tv.configure(yscrollcommand=sb6.set)
        self.sel_tv.bind("<Button-1>", self._on_select_click)
        self.sel_tv.bind("<Button-3>", self._select_menu)
        self.sel_tv.bind("<Button-2>", self._select_menu)  # mac

        # Alternatives tab
        f2 = ttk.Frame(nb)
        nb.add(f2, text="Alternatives per resource")
        cols2 = ("resource", "planet_types", "system", "sec", "jumps", "planet")
        self.alt_tv = ttk.Treeview(f2, columns=cols2, show="tree headings", height=12)
        _add_icon_column(self.alt_tv)
        for c, w in zip(cols2, (140, 130, 120, 50, 55, 130)):
            self.alt_tv.heading(c, text=c.replace("_", " ").capitalize())
            self.alt_tv.column(c, width=w, anchor="w")
        self.alt_tv.pack(side="left", fill="both", expand=True)
        sb2 = ttk.Scrollbar(f2, command=self.alt_tv.yview)
        sb2.pack(side="right", fill="y")
        self.alt_tv.configure(yscrollcommand=sb2.set)

        # Production estimate tab
        f4 = ttk.Frame(nb)
        nb.add(f4, text="Production estimate")
        self.prod_view = ProductionView(f4, self.icons)
        self.prod_view.pack(fill="both", expand=True)

        # Output calculator tab
        f5 = ttk.Frame(nb)
        nb.add(f5, text="Output calculator")
        bar5 = ttk.Frame(f5, padding=(0, 6))
        bar5.pack(fill="x")
        ttk.Label(bar5, text="P0/planet/hr:").pack(side="left")
        self.p0_rate = tk.StringVar(value="5000")
        ttk.Entry(bar5, textvariable=self.p0_rate, width=8).pack(side="left", padx=4)
        ttk.Label(bar5, text=" Basic fac/extractor:").pack(side="left")
        self.basic_per = tk.IntVar(value=8)
        ttk.Spinbox(bar5, from_=1, to=20, width=4, textvariable=self.basic_per)\
            .pack(side="left", padx=2)
        ttk.Label(bar5, text=" Adv fac/factory planet:").pack(side="left")
        self.adv_per = tk.IntVar(value=8)
        ttk.Spinbox(bar5, from_=1, to=20, width=4, textvariable=self.adv_per)\
            .pack(side="left", padx=2)
        ttk.Button(bar5, text="Recalculate", command=self._recompute).pack(side="left", padx=(6, 0))
        ttk.Label(bar5, text=" Price basis:").pack(side="left")
        self.price_basis = tk.StringVar(value="split")
        basis_cb = ttk.Combobox(bar5, textvariable=self.price_basis, width=6,
                                values=["split", "buy", "sell"], state="readonly")
        basis_cb.pack(side="left", padx=4)
        basis_cb.bind("<<ComboboxSelected>>", lambda e: self._recompute())
        self.mkt_btn = ttk.Button(bar5, text="Fetch Jita prices",
                                  command=self._fetch_market)
        self.mkt_btn.pack(side="left", padx=(6, 0))
        self.calc_view = OutputView(f5, self.icons)
        self.calc_view.pack(fill="both", expand=True)

        # Production chain tab
        f3 = ttk.Frame(nb)
        nb.add(f3, text="Production chain")
        self.chain_txt = tk.Text(f3, wrap="word", height=12)
        self.chain_txt.pack(fill="both", expand=True)

        # PI guide tab
        self._build_guide_tab(nb)

    def _plan_product(self, name: str) -> None:
        """Chain map double-click: make ``name`` the product to plan."""
        self.mode.set("product")
        self._sync_mode()
        self.product.set(name)
        self.summary.config(text=f"{name} selected — set your staging system "
                                 "and click Find planets.")

    def _build_fulfil_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Fulfil order")
        self.fulfil_tab = tab

        bar = ttk.Frame(tab, padding=(0, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Janice link or item list:").pack(side="left", anchor="n")
        self.order_txt = tk.Text(bar, height=3, width=60, wrap="none")
        self.order_txt.pack(side="left", padx=(6, 14))

        options = ttk.Frame(bar)
        options.pack(side="left", anchor="n")
        ttk.Label(options, text="Characters:").grid(row=0, column=0, sticky="w")
        self.order_chars = tk.IntVar(value=6)
        self.order_chars_box = ttk.Spinbox(options, from_=1, to=50, width=5,
                                           textvariable=self.order_chars)
        self.order_chars_box.grid(row=0, column=1, padx=(4, 14))
        ttk.Label(options, text="Planets per character:").grid(row=0, column=2, sticky="w")
        self.order_per_char = tk.IntVar(value=6)
        ttk.Spinbox(options, from_=1, to=6, width=4, textvariable=self.order_per_char)\
            .grid(row=0, column=3, padx=(4, 14))
        self.order_btn = ttk.Button(options, text="Optimise", command=self._optimise_order)
        self.order_btn.grid(row=0, column=4)
        self.order_use_real = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="Use free slots from the Characters tab",
                        variable=self.order_use_real, command=self._sync_order_slots)\
            .grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self.order_slots_note = ttk.Label(options, text="")
        self.order_slots_note.grid(row=2, column=0, columnspan=5, sticky="w")
        self.order_status = ttk.Label(options, text="")
        self.order_status.grid(row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))

        self.fulfil_view = FulfilView(tab, self.icons)
        self.fulfil_view.pack(fill="both", expand=True)

    def _optimise_order(self):
        if not self.db:
            return
        text = self.order_txt.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Missing input", "Paste a Janice link or an item list.")
            return
        if self.db.get_system(self.staging.get()) is None:
            messagebox.showwarning("Missing input",
                                   "Enter a valid staging system in the Plan box.")
            return
        if not any(v.get() for v in self.band_vars.values()):
            messagebox.showwarning("Missing input", "Select at least one security band.")
            return

        code = extract_code(text)
        if code is None:
            items, unread = parse_item_list(text)
            if not items:
                messagebox.showwarning("Nothing to read",
                                       "That doesn't look like a Janice link or an "
                                       "item list (one 'Name amount' per line).")
                return
            source = f"From your pasted list: {len(items)} item(s)."
            if unread:
                source += f" Couldn't read {len(unread)} line(s): " + ", ".join(unread[:3])
            self._run_order(items, source)
            return

        self.order_btn.config(state="disabled")
        self.order_status.config(text=f"Loading appraisal {code} from Janice…")

        def work():
            try:
                self._jq.put(("ok", fetch_appraisal(code)))
            except JaniceError as exc:
                self._jq.put(("err", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self.after(150, self._poll_order)

    def _poll_order(self):
        try:
            kind, payload = self._jq.get_nowait()
        except queue.Empty:
            self.after(150, self._poll_order)
            return
        self.order_btn.config(state="normal")
        if kind != "ok":
            self.order_status.config(text="")
            messagebox.showerror("Janice", payload)
            return
        source = f"From Janice appraisal {payload.code}: {len(payload.items)} item(s)"
        if payload.split_value:
            source += f", worth {payload.split_value:,.0f} ISK at the appraisal's split price"
        self._run_order(payload.items, source + ".")

    def _sync_order_slots(self) -> None:
        """Show which slots Optimise will use, after any Characters tab change."""
        read = self.characters_tab.panel.characters
        real = self.order_use_real.get() and bool(read)
        self.order_chars_box.config(state="disabled" if real else "normal")
        if not self.order_use_real.get():
            note = "Using the numbers typed above."
        elif not read:
            note = "No character data yet, so using the numbers typed above. Click " \
                   "Refresh on the Characters tab."
        else:
            roster, unknown = slot_roster(read)
            usable = sum(c.free > 0 for c in roster)
            note = (f"{sum(c.free for c in roster)} free slots on {usable} character(s), "
                    "as of the last Refresh.")
            if unknown:
                note += " Unreadable, so left out: " + ", ".join(unknown) + "."
        self.order_slots_note.config(text=note)

    def _run_order(self, items: dict[str, int], source: str) -> None:
        bands = {b for b, v in self.band_vars.items() if v.get()}
        roster = None
        if self.order_use_real.get() and self.characters_tab.panel.characters:
            roster, unknown = slot_roster(self.characters_tab.panel.characters)
            if unknown:
                source += " Left out, slots unreadable: " + ", ".join(unknown) + "."
        try:
            plan = fulfil_order(self.db, items, self.staging.get(), int(self.jumps.get()),
                                bands, max(1, int(self.order_chars.get())),
                                max(1, int(self.order_per_char.get())),
                                self._default_rate(), self._basic_per(), self._adv_per(),
                                roster=roster)
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Can't plan this order", str(exc))
            return
        self.order_status.config(text="")
        self.fulfil_view.show(plan, source)
        self._render(order_plan(self.db, plan), switch_tab=False)

    def _build_guide_tab(self, nb):
        f8 = ttk.Frame(nb)
        nb.add(f8, text="PI guide")

        ttk.Label(f8, text="Planet types and the raw (P0) resources they yield:",
                  font=("", 9, "bold")).pack(anchor="w", pady=(4, 2))
        pt = ttk.Treeview(f8, columns=("type", "res"), show="tree headings", height=8)
        _add_icon_column(pt)
        self.guide_planets_tv = pt
        pt.heading("type", text="Planet type")
        pt.heading("res", text="P0 resources")
        pt.column("type", width=110, anchor="w")
        pt.column("res", width=560, anchor="w")
        for t in PLANET_TYPES:
            pt.insert("", tk.END, iid=t, values=(t, ", ".join(PLANET_RESOURCES[t])))
        pt.pack(fill="x", pady=(0, 8))

        bar8 = ttk.Frame(f8)
        bar8.pack(fill="x")
        ttk.Label(bar8, text="Production chain for product:").pack(side="left")
        self.guide_product = tk.StringVar()
        self.guide_cb = ttk.Combobox(bar8, textvariable=self.guide_product,
                                     width=34, state="readonly")
        self.guide_cb.pack(side="left", padx=4)
        self.guide_cb.bind("<<ComboboxSelected>>", lambda e: self._show_guide())
        ttk.Label(bar8, text="(indent = one step deeper; leaves are P0)")\
            .pack(side="left", padx=8)
        self.guide_txt = tk.Text(f8, wrap="word", height=12)
        self.guide_txt.pack(fill="both", expand=True, pady=(4, 0))

    def _show_guide(self):
        name = self.guide_product.get()
        t = self.guide_txt
        t.delete("1.0", tk.END)
        if not self.db or not name:
            return
        rows = chain_lines(self.db, name)
        tier = max((r.depth for r in rows), default=0)
        tlabel = {1: "P1", 2: "P2", 3: "P3", 4: "P4"}.get(tier, f"depth {tier}")
        t.insert(tk.END, f"{name}  —  {tlabel}\n\n")
        for r in rows:
            indent = "    " * r.depth
            if r.is_p0:
                line = f"{indent}• {r.name}   [P0 — extract on: {r.sources}]"
            elif r.depth == 0:
                line = f"{r.name}"
            else:
                line = f"{indent}• {r.name}"
            if r.depth > 0 and r.per_cycle:
                line += f"   (x{r.per_cycle:g} per parent cycle)"
            t.insert(tk.END, line + "\n")
        t.insert(tk.END, "\nP0 -> P1 is produced on the extractor planet itself; "
                         "P2 ideally in the same system; P3/P4 on dedicated factory "
                         "planets fed from the rest.\n")

    def _try_open_db(self):
        if sde.database_exists(self.db_path):
            self.db = PIDatabase(self.db_path)
            self.pi_products = self.db.all_pi_products()
            self.product_cb["values"] = self.pi_products
            self.guide_cb["values"] = self.pi_products
            self._facility_rates = facility_rates(self.db)
            self.chain_map.set_graph(build_chain_graph(self.db))
            self.data_status.config(text="SDE data loaded.")
            self._load_icons()
            self.find_btn.state(["!disabled"])
        else:
            self.data_status.config(
                text=("No SDE data yet — click 'Download / Update SDE'. "
                      "Fetches the official CCP SDE (one-time, a few hundred MB)."))
            self.find_btn.state(["disabled"])

    def _load_icons(self):
        """Show cached icons now and download any missing ones in the background."""
        type_ids = dict(self.chain_map.graph.type_ids)
        factory_id = self.db.type_id_by_name(FACTORY_ICON_TYPE)
        if factory_id is not None:
            type_ids[FACTORY_ICON] = factory_id
        folder = default_icon_dir()
        have = cached_icons(type_ids, folder)
        self._apply_icons(have)
        if len(have) == len(type_ids):
            return

        def work():
            self._iq.put((fetch_icons(type_ids, folder), len(type_ids)))

        threading.Thread(target=work, daemon=True).start()
        self.after(200, self._poll_icons)

    def _poll_icons(self):
        try:
            paths, wanted = self._iq.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_icons)
            return
        self._apply_icons(paths)
        if len(paths) < wanted:
            self.data_status.config(
                text=f"SDE data loaded. {wanted - len(paths)} item icon(s) couldn't "
                     "be downloaded; the app will retry next launch.")

    def _apply_icons(self, paths: dict[str, str]) -> None:
        self.icons.load(paths)
        self.chain_map.refresh()
        self.prod_view.redraw()
        self.calc_view.redraw()
        self.fulfil_view.redraw()
        self.characters_tab.panel.redraw()
        for planet_type in PLANET_TYPES:
            self.guide_planets_tv.item(planet_type,
                                       image=self._tree_icon(planet_type))

    def _tree_icon(self, name: str | None):
        """A row image for a Treeview, or nothing if the icon isn't available."""
        return self.icons.small(name) or ""

    def _start_download(self):
        self.dl_btn.state(["disabled"])
        self.find_btn.state(["disabled"])
        if self.db is not None:
            self.db.close()
            self.db = None
        self.progress.pack(fill="x", pady=(0, 8))
        self.progress["value"] = 0

        def work():
            try:
                def cb(msg, frac):
                    self._q.put(("progress", msg, frac))
                sde.build_database(self.db_path, cb)
                self._q.put(("done", "", 1.0))
            except Exception as exc:  # pragma: no cover
                self._q.put(("error", str(exc), 0))

        threading.Thread(target=work, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                kind, msg, frac = self._q.get_nowait()
                if kind == "progress":
                    self.data_status.config(text=msg)
                    if frac >= 0:
                        self.progress.config(mode="determinate")
                        self.progress["value"] = frac
                    else:
                        self.progress.config(mode="indeterminate")
                        self.progress.start(12)
                elif kind == "done":
                    self.progress.stop()
                    self.progress.pack_forget()
                    self.dl_btn.state(["!disabled"])
                    self._try_open_db()
                    return
                elif kind == "error":
                    self.progress.stop()
                    self.progress.pack_forget()
                    self.dl_btn.state(["!disabled"])
                    messagebox.showerror("Download failed", msg)
                    self.data_status.config(text="Download failed.")
                    return
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _lookup_systems(self, text: str):
        if not self.db:
            return []
        return [s.name for s in self.db.search_systems(text, limit=12)]

    def _find(self):
        if not self.db:
            return
        staging = self.staging.get()
        if not staging:
            messagebox.showwarning("Missing input", "Enter a staging system.")
            return
        if self.db.get_system(staging) is None:
            messagebox.showwarning("Unknown system", f"No system named {staging!r}.")
            return
        bands = {b for b, v in self.band_vars.items() if v.get()}
        if not bands:
            messagebox.showwarning("Missing input", "Select at least one security band.")
            return

        kwargs = {}
        if self.mode.get() == "product":
            if not self.product.get():
                messagebox.showwarning("Missing input", "Choose a product.")
                return
            kwargs["product"] = self.product.get()
        else:
            chosen = {t for t, v in self.type_vars.items() if v.get()}
            if not chosen:
                messagebox.showwarning("Missing input", "Tick at least one planet type.")
                return
            kwargs["planet_types"] = chosen

        try:
            plan = make_plan(self.db, staging, int(self.jumps.get()), bands,
                             slots=int(self.slots.get()),
                             single_system=self.single_system_var.get(), **kwargs)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        self._render(plan)

    # ---- rendering -------------------------------------------------------
    def _render(self, plan: Plan, switch_tab: bool = True):
        self._last_plan = plan
        for tv in (self.plan_tv, self.alt_tv, self.sel_tv):
            tv.delete(*tv.get_children())
        self.chain_txt.delete("1.0", tk.END)
        self.prod_view.clear()
        self.calc_view.clear()

        if plan.solution is None or not plan.requirements:
            self._picks = {}
            self._value_items = []
            self.summary.config(text="No PI items in this order."
                                if plan.resolution.product == ORDER_PRODUCT
                                else "No requirements resolved.")
            return

        for cs in plan.chosen:
            first = True
            rows = (list(cs.assignments)
                    + [("Factory planet", p) for p in cs.factories])
            rows = rows or [(None, p) for p in cs.relevant_planets]
            if not rows:
                rows = [(None, None)]
            for req, planet in rows:
                self.plan_tv.insert(
                    "", tk.END,
                    image=self._tree_icon(planet.planet_type if planet else None),
                    values=(cs.system.name if first else "",
                            cs.system.band if first else "",
                            cs.jumps if first else "",
                            planet.name if planet else "",
                            planet.planet_type if planet else "",
                            req or ""))
                first = False

        rp = plan.resolution.resource_planets
        for req in plan.requirements:
            opts = plan.per_requirement.get(req, [])
            ptypes = ", ".join(sorted(rp.get(req, set())))
            if not opts:
                self.alt_tv.insert("", tk.END,
                                   values=(req, ptypes, "(none in range)", "", "", ""))
            for o in opts[:15]:
                self.alt_tv.insert("", tk.END,
                                   image=self._tree_icon(o.planet.planet_type),
                                   values=(req, ptypes, o.system.name, o.system.band,
                                           o.jumps, o.planet.name))

        self._render_chain(plan)
        self._build_picks(plan)
        self._populate_select_tree()
        self._recompute()
        if switch_tab:
            self.results_nb.select(self.setup_tab)

    # ---- planet selection ------------------------------------------------
    def _build_picks(self, plan: Plan):
        self._picks = {}
        self._servable = {}
        self._pick_jumps: dict[int, int] = {}
        rp = plan.resolution.resource_planets
        reqs = [r for r in plan.requirements if plan.per_requirement.get(r)]
        suggested: dict[int, str] = {}
        factories: set[int] = set()
        for cs in plan.chosen:
            for res, planet in cs.assignments:
                suggested[planet.id] = res
            factories.update(planet.id for planet in cs.factories)

        for r in reqs:
            for o in plan.per_requirement.get(r, []):
                pid = o.planet.id
                if pid in factories:    # already busy, so it can't also extract
                    continue
                self._pick_jumps[pid] = o.jumps
                servable = sorted({rr for rr in reqs
                                   if o.planet.planet_type in rp.get(rr, set())})
                self._servable[pid] = servable or [r]
                if pid in self._picks:
                    continue
                res = suggested.get(pid, servable[0] if servable else r)
                use = pid in suggested
                rate = None
                sav = self.saved.get(pid)
                if sav:
                    rate = sav.get("rate", None)
                    if not plan.order_items:   # an order keeps the optimiser's picks
                        if sav.get("resource") in self._servable[pid]:
                            res = sav["resource"]
                        use = bool(sav.get("use", use))
                self._picks[pid] = PlanetPick(
                    pid, o.planet.name, o.system.name, o.planet.planet_type,
                    res, rate, use)

    def _populate_select_tree(self):
        self.sel_tv.delete(*self.sel_tv.get_children())
        picks = sorted(self._picks.values(),
                       key=lambda p: (p.resource, p.system, p.name))
        for p in picks:
            rate = "(default)" if p.rate is None else f"{p.rate:,.0f}"
            self.sel_tv.insert("", tk.END, iid=str(p.planet_id),
                               image=self._tree_icon(p.planet_type),
                               values=("✓" if p.use else "",
                                       p.name, p.system,
                                       self._pick_jumps.get(p.planet_id, ""),
                                       p.planet_type,
                                       p.resource, rate))

    def _on_select_click(self, event):
        if self.sel_tv.identify("region", event.x, event.y) != "cell":
            return
        if self.sel_tv.identify_column(event.x) != "#1":
            return
        row = self.sel_tv.identify_row(event.y)
        if not row:
            return
        p = self._picks.get(int(row))
        if p:
            p.use = not p.use
            self._persist()
            self._populate_select_tree()
            self._recompute()

    def _select_menu(self, event):
        row = self.sel_tv.identify_row(event.y)
        if not row:
            return
        self.sel_tv.selection_set(row)
        pid = int(row)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Set P0 output & resource…",
                         command=lambda: self._edit_pick(pid))
        menu.add_command(label="Toggle use", command=lambda: self._toggle(pid))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _toggle(self, pid: int):
        p = self._picks.get(pid)
        if p:
            p.use = not p.use
            self._persist()
            self._populate_select_tree()
            self._recompute()

    def _edit_pick(self, pid: int):
        pick = self._picks.get(pid)
        if not pick:
            return
        win = tk.Toplevel(self)
        win.title(f"Set output — {pick.name}")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"{pick.name}  ({pick.planet_type}, {pick.system})",
                  font=("", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(frm, text="Extracts (P0):").grid(row=1, column=0, sticky="w",
                                                   pady=(10, 0))
        rvar = tk.StringVar(value=pick.resource)
        ttk.Combobox(frm, textvariable=rvar, values=self._servable.get(pid, [pick.resource]),
                     state="readonly", width=24).grid(row=1, column=1, sticky="w",
                                                       pady=(10, 0))

        ttk.Label(frm, text="P0 units / hour:").grid(row=2, column=0, sticky="w",
                                                     pady=(8, 0))
        rate_var = tk.StringVar(value=("" if pick.rate is None else f"{pick.rate:g}"))
        ent = ttk.Entry(frm, textvariable=rate_var, width=26)
        ent.grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Label(frm, text="(leave blank to use the default rate)",
                  foreground="grey").grid(row=3, column=1, sticky="w")

        def ok():
            pick.resource = rvar.get() or pick.resource
            txt = rate_var.get().strip().replace(",", "")
            if txt == "":
                pick.rate = None
            else:
                try:
                    pick.rate = float(txt)
                except ValueError:
                    messagebox.showwarning("Invalid", "Enter a number or leave blank.")
                    return
            pick.use = True
            self._persist()
            self._populate_select_tree()
            self._recompute()
            win.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=ok).pack(side="right", padx=(0, 6))
        ent.focus_set()

    def _persist(self):
        order = bool(self._last_plan and self._last_plan.order_items)
        for pid, p in self._picks.items():
            if not order:
                self.saved[pid] = {"resource": p.resource, "rate": p.rate, "use": p.use}
            elif pid in self.saved or p.rate is not None:
                # Ticks on an order last for that order, but a planet's measured
                # rate holds everywhere. Saved ticks for Find planets stay as they are.
                self.saved[pid] = {**self.saved.get(pid, {}), "rate": p.rate}
        try:
            save_selection(self._sel_path, self.saved)
        except OSError:
            pass

    def _default_rate(self) -> float:
        try:
            return float(self.p0_rate.get())
        except (ValueError, AttributeError):
            return 0.0

    def _basic_per(self) -> int:
        try:
            return max(1, int(self.basic_per.get()))
        except (ValueError, AttributeError):
            return 8

    def _adv_per(self) -> int:
        try:
            return max(1, int(self.adv_per.get()))
        except (ValueError, AttributeError):
            return 8

    def _recompute(self):
        plan = self._last_plan
        if not plan or not plan.requirements:
            return
        recipe = plan.production.recipe if plan.production else None
        demand = recipe.demand if (recipe and recipe.demand) else None
        default_rate = self._default_rate()
        picks = list(self._picks.values())
        calc, counts = calculate_from_picks(recipe, picks, default_rate)
        used = sum(counts.values())

        slots_requested = (plan.production.slots_requested
                           if plan.order_items and plan.production
                           else int(self.slots.get() or used))
        report = build_report(
            [r for r in plan.requirements if plan.per_requirement.get(r)],
            demand, counts,
            slots_requested=slots_requested, recipe=recipe)

        items = []
        if calc.product and calc.product_per_hour > 0:
            if plan.order_items:    # "Whole order" isn't an item with a market price
                items += [(name, amount * calc.product_per_hour, "output")
                          for name, amount in plan.order_items.items()]
            else:
                items.append((calc.product, calc.product_per_hour, "output"))
        for r in calc.rows:
            if r.excess > 0.0001:
                items.append((r.resource, r.excess, "surplus"))
        self._value_items = items

        bd = None
        if (recipe and calc.product and calc.product_per_hour > 0
                and self._facility_rates):
            bd = build_breakdown(recipe, calc.product_per_hour, self._facility_rates,
                                 extractor_planets=used,
                                 basic_per_extractor=self._basic_per(),
                                 adv_per_factory=self._adv_per())
            if plan.order_items:    # report the factories actually placed
                placed = sum(len(cs.factories) for cs in plan.chosen)
                bd = replace(bd, factory_planets=placed,
                             total_planets=bd.extractor_planets + placed,
                             characters=characters_needed(bd.extractor_planets + placed))

        graph = self.chain_map.graph
        self.prod_view.show(report, bd,
                            [p.planet_type for p in picks if p.use and p.resource],
                            graph.column_of.get if graph else (lambda _name: None))

        valuation = None
        if self._prices and items:
            valuation = market.value_outputs(items, self._prices,
                                             self.price_basis.get())
        self.calc_view.show(calc, used, default_rate, valuation, self._prices,
                            [name for name, _, _ in items])

        sys_used = len({p.system for p in picks if p.use})
        if bd:
            txt = (f"✓ {bd.extractor_planets} extractor + {bd.factory_planets} factory "
                   f"= {bd.total_planets} planets, {bd.characters} character(s)  —  "
                   f"extractors in {sys_used} system(s), "
                   f"{plan.in_range_systems} systems in range")
        else:
            txt = (f"✓ {used} planets selected across {sys_used} system(s), "
                   f"{characters_needed(used)} character(s)  —  "
                   f"{plan.in_range_systems} systems in range")
        if plan.single_system and not plan.single_system_ok:
            txt = ("⚠ No single system in range can host the whole chain — widen "
                   "Max jumps, or accept this fewest-systems fallback.  |  ") + txt
        if plan.unmet:
            txt = "⚠ unreachable: " + ", ".join(plan.unmet) + "  |  " + txt
        if plan.resolution.product == ORDER_PRODUCT and not plan.chosen:
            txt = "⚠ This order can't be placed. See the Fulfil order tab for why."
        self.summary.config(text=txt)

    def _fetch_market(self):
        if not self.db:
            return
        if not self._value_items:
            messagebox.showinfo("Market", "Run a search first so there's a "
                                          "product/surplus to price.")
            return
        names = [n for n, _, _ in self._value_items]
        name_to_id = {n: self.db.type_id_by_name(n) for n in names}
        self.mkt_btn.config(state="disabled", text="Fetching…")

        def work():
            try:
                prices = market.fetch_prices(name_to_id)
                self._mq.put(("ok", prices))
            except Exception as exc:  # pragma: no cover - network
                self._mq.put(("err", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self.after(200, self._poll_market)

    def _poll_market(self):
        try:
            kind, payload = self._mq.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_market)
            return
        self.mkt_btn.config(state="normal", text="Fetch Jita prices")
        if kind == "ok":
            self._prices = payload
            self._recompute()
        else:
            messagebox.showerror("Market fetch failed",
                                 f"Could not reach ESI:\n{payload}")

    # ---- best systems ----------------------------------------------------
    def _find_best(self):
        if not self.db:
            return
        staging = self.staging.get()
        sysm = self.db.get_system(staging) if staging else None
        if sysm is None:
            messagebox.showwarning("Missing input",
                                   "Enter a valid staging system first.")
            return
        bands = {b for b, v in self.band_vars.items() if v.get()}
        if not bands:
            messagebox.showwarning("Missing input", "Select a security band.")
            return
        tier = int(self.best_tier.get()[1])
        products = profitability.tier_products(self.db, tier)
        if not products:
            messagebox.showinfo("Best systems", f"No {self.best_tier.get()} "
                                                "products found in the SDE.")
            return
        name_to_id = {n: self.db.type_id_by_name(n) for n, _ in products}
        self._best_params = (sysm.id, int(self.jumps.get()), bands, tier,
                             self.price_basis.get(), self._default_rate(),
                             int(self.slots.get()), products)
        self.best_btn.config(state="disabled")
        self.best_status.config(text=f"Fetching {len(products)} Jita prices…")

        def work():
            try:
                prices = market.fetch_prices(name_to_id)
                self._bq.put(("ok", prices))
            except Exception as exc:  # pragma: no cover - network
                self._bq.put(("err", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self.after(200, self._poll_best)

    def _poll_best(self):
        try:
            kind, payload = self._bq.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_best)
            return
        self.best_btn.config(state="normal")
        if kind != "ok":
            self.best_status.config(text="ESI fetch failed.")
            messagebox.showerror("Best systems", f"Could not reach ESI:\n{payload}")
            return
        sid, jumps, bands, tier, basis, rate, slots, products = self._best_params
        self.best_status.config(text="Ranking systems…")
        results = profitability.rank_systems(
            self.db, sid, jumps, bands, tier, payload, basis, rate, slots,
            products=products)
        self.best_tv.delete(*self.best_tv.get_children())
        for i, r in enumerate(results[:100], 1):
            self.best_tv.insert("", tk.END,
                                image=self._tree_icon(r.mix[0][0] if r.mix else None),
                                values=(
                i, r.system_name, r.band, r.jumps, r.mix_label,
                f"{r.isk_per_planet:,.0f}", f"{r.isk_per_hour:,.0f}",
                r.planets_used))
        self.best_status.config(
            text=(f"{len(results)} self-sufficient {profitability.TIER_LABEL[tier]} "
                  f"system(s).  Values at {basis} price, "
                  f"{rate:,.0f} P0/hr per planet; slots filled with best mix."))

    def _render_chain(self, plan: Plan):
        t = self.chain_txt
        r = plan.resolution
        if r.product != "(planet types)":
            if plan.order_items:
                t.insert(tk.END, "Order:\n")
                for name, amount in sorted(plan.order_items.items()):
                    t.insert(tk.END, f"  • {name}  × {amount:,}\n")
                t.insert(tk.END, "\n")
            else:
                t.insert(tk.END, f"Product: {r.product}\n\n")
            t.insert(tk.END, "Raw (P0) resources required:\n")
            for res in sorted(r.raw_resources):
                p1 = P0_TO_P1.get(res, "?")
                pts = ", ".join(sorted(r.resource_planets.get(res, set())))
                t.insert(tk.END, f"  • {res}  (→ {p1})   from: {pts}\n")
        else:
            t.insert(tk.END, "Mode: direct planet-type selection\n\n")
            for req in plan.requirements:
                t.insert(tk.END, f"  • {req}\n")

        if plan.alt_solutions:
            t.insert(tk.END, "\nAlternative full system sets (same size):\n")
            for alt in plan.alt_solutions[:6]:
                names = ", ".join(s.name for s in alt.systems)
                t.insert(tk.END, f"  • {names}  ({alt.total_jumps} jumps)\n")


def run():
    root = tk.Tk()
    root.title("Eve PI Planner")
    # Tall enough to show the whole chain map without scrolling, if the screen is.
    width = min(1200, root.winfo_screenwidth() - 40)
    height = min(900, root.winfo_screenheight() - 80)
    root.geometry(f"{width}x{height}")
    apply_theme(root)
    PlannerApp(root, sde.default_db_path())
    root.mainloop()
