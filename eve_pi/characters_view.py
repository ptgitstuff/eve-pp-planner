"""The Characters tab: log in with EVE SSO, then see each character's planet slots,
what every planet produces, at what rate, and what's in storage."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from tkinter import messagebox, ttk

from .colonies import RUNNING, CharacterPI, Colony, Totals, build_character, totals
from .db import PIDatabase
from .esi import (CALLBACK_URL, DEVELOPER_SITE, SCOPES, Character, EsiClient, EsiError,
                  default_store_path, load_store, login, save_store)
from .icon_set import IconSet
from .results_model import format_compact, format_duration, nice_ticks
from .results_view import BAR, FONT_SECTION, PAD, ROW, SECONDARY, SERIES_BLUE, _Panel
from .theme import RAISED

_POLL_MS = 150


def _age(when: datetime | None, now: datetime) -> str:
    if when is None:
        return "update time unknown"
    return f"updated {format_duration(max((now - when).total_seconds(), 0) / 3600)} ago"


class CharactersPanel(_Panel):
    def __init__(self, master, icons: IconSet):
        super().__init__(master, icons)
        self.saved_count = 0
        self.characters: list[CharacterPI] = []

    def show(self, characters: list[CharacterPI], saved_count: int) -> None:
        self.characters = characters
        self.saved_count = saved_count
        self.redraw()

    def _draw(self, width: int) -> int:
        if not self.characters:
            return self._draw_empty(width)
        now = datetime.now(timezone.utc)
        summary = totals(self.characters)
        y = self._draw_tiles(14, width, summary)
        y += 86
        when = (_age(summary.oldest_update, now) if summary.oldest_update
                else "no colonies found")
        self.text(PAD, y, "ESI only updates a colony when you view it in the game, so "
                          f"figures can lag. Oldest colony: {when}.", color=SECONDARY)
        y += 26
        if summary.extraction:
            y = self._draw_extraction(y, width, summary) + 14
        if summary.holdings:
            y = self._draw_holdings(y, width, summary) + 14
        for character in self.characters:
            y = self._draw_character(y, width, character, now) + 14
        return y + 10

    def _draw_empty(self, width: int) -> int:
        if self.saved_count:
            return self.placeholder(
                width, f"{self.saved_count} saved character(s). Click Refresh to read "
                       "their planets from ESI.")
        y = self.section(24, "Connect your characters")
        steps = (
            f"1.  Click Developer site and create an application with these scopes: "
            f"{', '.join(SCOPES)}.",
            f"2.  Set its callback URL to  {CALLBACK_URL}",
            "3.  Paste its Client ID above. The secret isn't needed.",
            "4.  Click Add character and approve it in your browser. Repeat for each "
            "character.",
        )
        for step in steps:
            item = self.text(PAD, y + 8, step, wrap=width - 2 * PAD)
            y = self.canvas.bbox(item)[3] + 4
        return y + 20

    def _draw_tiles(self, y, width, summary: Totals) -> int:
        slots = f"{summary.planets_used} of {summary.max_planets}"
        note = "Colonies / planet slots"
        free = str(summary.free_slots)
        if summary.unknown_slots:
            note = f"{summary.unknown_slots} character(s) with unreadable skills"
            free += "+?"
        return self.tiles(y, width, [
            ("Characters", str(summary.characters), "Logged in"),
            ("Planets", slots, note),
            ("Free slots", free, "From Interplanetary Consolidation"),
            ("Raw extraction", format_compact(sum(summary.extraction.values())),
             "Units per hour, nominal"),
        ])

    def _draw_extraction(self, y, width, summary: Totals) -> int:
        y = self.section(y, "Raw extraction", "Units per hour, all characters")
        x0, x1 = PAD + 256, max(PAD + 356, width - 160)
        ticks = nice_ticks(max(summary.extraction.values()))
        span = (x1 - x0) / ticks[-1]
        top = y
        for name, rate in sorted(summary.extraction.items(), key=lambda kv: -kv[1]):
            cy = y + ROW / 2
            self.named_row(cy, name)
            self.bar(x0, cy - BAR / 2, x0 + rate * span, cy + BAR / 2, SERIES_BLUE)
            self.text(x1 + 12, cy, f"{rate:,.0f}/hr", color=SECONDARY)
            y += ROW
        self.axis(x0, x1, top, y, ticks, format_compact)
        return y + 18

    def _draw_holdings(self, y, width, summary: Totals) -> int:
        y = self.section(y, "PI holdings", "In colony storage, all characters")
        columns = max(1, (width - 2 * PAD) // 300)
        items = sorted(summary.holdings.items())
        rows = (len(items) + columns - 1) // columns
        for index, (name, amount) in enumerate(items):
            x = PAD + (index // rows) * (width - 2 * PAD) / columns
            cy = y + (index % rows) * ROW + ROW / 2
            image = self.icons.small(name)
            if image is not None:
                self.canvas.create_image(x + 8, cy, image=image)
            self.text(x + 22, cy, f"{amount:,}  {name}")
        return y + rows * ROW

    def _draw_character(self, y, width, character: CharacterPI, now: datetime) -> int:
        self.canvas.create_rectangle(PAD, y, width - PAD, y + 30, fill=RAISED, outline="")
        item = self.text(PAD + 12, y + 15, character.name, font=FONT_SECTION)
        used = len(character.colonies)
        if character.free_slots is None:
            slots = f"{used} planets, slots unknown"
        else:
            slots = (f"{used} of {character.max_planets} planets, "
                     f"{character.free_slots} free")
        if character.command_center_skill is not None:
            slots += f"  ·  Command Center Upgrades {character.command_center_skill}"
        self.text(self.canvas.bbox(item)[2] + 14, y + 15, slots, color=SECONDARY)
        y += 40
        for error in character.errors:
            item = self.text(PAD + 12, y, "✕  " + error, wrap=width - 2 * PAD - 12)
            y = self.canvas.bbox(item)[3] + 8
        if not character.colonies and not character.errors:
            self.text(PAD + 12, y, "No colonies.", color=SECONDARY)
            y += 22
        for colony in character.colonies:
            y = self._draw_colony(y, width, colony, now) + 8
        return y

    def _draw_colony(self, y, width, colony: Colony, now: datetime) -> int:
        cy = y + 8
        image = self.icons.small(colony.planet_type)
        if image is not None:
            self.canvas.create_image(PAD + 20, cy, image=image)
        item = self.text(PAD + 34, cy, f"{colony.name}  ·  {colony.planet_type}")
        self.text(self.canvas.bbox(item)[2] + 14, cy,
                  f"Command center level {colony.command_center_level}  ·  "
                  f"{_age(colony.last_update, now)}", color=SECONDARY)
        y += 22
        x = PAD + 34
        if not colony.has_details:
            self.text(x, y, "Couldn't read this colony's details.", color=SECONDARY)
            return y + 18
        if not (colony.extractors or colony.factories or colony.stock):
            self.text(x, y, "Nothing installed.", color=SECONDARY)
            y += 18
        for extractor in colony.extractors:
            self.status(x, y, extractor.status, self._extractor_text(extractor, now))
            y += 18
        for group in colony.factories:
            count = "1 factory" if group.count == 1 else f"{group.count} factories"
            self.text(x, y, f"{group.product}: {count}, up to "
                            f"{group.max_per_hour:,.0f}/hr if fully supplied")
            y += 18
        if colony.stock:
            stock = ", ".join(f"{amount:,} {name}"
                              for name, amount in sorted(colony.stock.items()))
            item = self.text(x, y, "Stored: " + stock, color=SECONDARY,
                             wrap=width - x - PAD)
            y = self.canvas.bbox(item)[3] + 8
        return y

    @staticmethod
    def _extractor_text(extractor, now: datetime) -> str:
        if extractor.status is not RUNNING:
            what = extractor.product or "Extractor"
            return f"{what}: {extractor.status.label.lower()}"
        heads = "1 head" if extractor.heads == 1 else f"{extractor.heads} heads"
        text = f"{extractor.product}: {extractor.per_hour:,.0f}/hr, {heads}"
        if extractor.expires:
            left = (extractor.expires - now).total_seconds() / 3600
            text += f", program ends in {format_duration(left)}"
        return text


class CharactersTab(ttk.Frame):
    """Controls and results. ``get_db`` gives the current database or None."""

    def __init__(self, master, icons: IconSet, get_db: Callable[[], PIDatabase | None],
                 store_path: str | None = None):
        super().__init__(master)
        self._get_db = get_db
        self._store_path = store_path or default_store_path()
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self.on_change: Callable[[], None] | None = None
        self._results: dict[int, CharacterPI] = {}
        client_id, self._characters = load_store(self._store_path)

        bar = ttk.Frame(self, padding=(0, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="ESI Client ID:").pack(side="left")
        self.client_id = tk.StringVar(value=client_id)
        ttk.Entry(bar, textvariable=self.client_id, width=36).pack(side="left", padx=(6, 6))
        ttk.Button(bar, text="Developer site",
                   command=lambda: webbrowser.open(DEVELOPER_SITE)).pack(side="left",
                                                                         padx=(0, 14))
        self.add_btn = ttk.Button(bar, text="Add character", command=self.add_character)
        self.add_btn.pack(side="left")
        self.refresh_btn = ttk.Button(bar, text="Refresh", command=self.refresh)
        self.refresh_btn.pack(side="left", padx=(6, 14))
        self.remove_choice = tk.StringVar()
        self.remove_box = ttk.Combobox(bar, textvariable=self.remove_choice, width=22,
                                       state="readonly")
        self.remove_box.pack(side="left")
        self.remove_btn = ttk.Button(bar, text="Remove", command=self.remove_character)
        self.remove_btn.pack(side="left", padx=(6, 14))
        self.status_label = ttk.Label(bar, text="")
        self.status_label.pack(side="left")

        self.panel = CharactersPanel(self, icons)
        self.panel.pack(fill="both", expand=True)
        self._show()

    # ------------------------------------------------------------------ display
    def _show(self) -> None:
        self.remove_box.config(values=[c.name for c in self._characters])
        if self.remove_choice.get() not in {c.name for c in self._characters}:
            self.remove_choice.set(self._characters[0].name if self._characters else "")
        ordered = [self._results[c.id] for c in self._characters if c.id in self._results]
        self.panel.show(ordered, len(self._characters))
        if self.on_change is not None:
            self.on_change()

    def _set_busy(self, message: str) -> None:
        self._busy = bool(message)
        state = "disabled" if self._busy else "normal"
        for button in (self.add_btn, self.refresh_btn, self.remove_btn):
            button.config(state=state)
        self.status_label.config(text=message)

    def _save(self) -> None:
        try:
            save_store(self._store_path, self.client_id.get().strip(), self._characters)
        except OSError as exc:
            messagebox.showwarning("Characters", f"Couldn't save your characters:\n{exc}")

    def _run(self, message: str, work: Callable[[], object],
             done: Callable[[object], None]) -> None:
        """Run ``work`` on a worker thread, then ``done`` here with its result."""
        self._set_busy(message)

        def worker():
            try:
                self._queue.put((done, work()))
            except EsiError as exc:
                self._queue.put((None, str(exc)))
            except Exception as exc:  # the buttons must always come back
                self._queue.put((None, f"Unexpected problem: {type(exc).__name__}"))

        threading.Thread(target=worker, daemon=True).start()
        self.after(_POLL_MS, self._poll)

    def _poll(self) -> None:
        try:
            done, payload = self._queue.get_nowait()
        except queue.Empty:
            self.after(_POLL_MS, self._poll)
            return
        self._set_busy("")
        if done is None:
            messagebox.showerror("Characters", payload)
        else:
            done(payload)

    # ------------------------------------------------------------------ actions
    def add_character(self) -> None:
        client_id = self.client_id.get().strip()
        if not client_id:
            messagebox.showwarning("Client ID needed",
                                   "Paste the Client ID of your EVE application first.")
            return
        self._run("Waiting for you to approve in the browser…",
                  lambda: login(client_id), self._added)

    def _added(self, character: Character) -> None:
        self._characters = [c for c in self._characters if c.id != character.id]
        self._characters.append(character)
        self._save()
        self._fetch([character])

    def refresh(self) -> None:
        if not self._characters:
            messagebox.showinfo("Characters", "Add a character first.")
            return
        if not self.client_id.get().strip():
            messagebox.showwarning("Client ID needed",
                                   "Paste the Client ID of your EVE application first.")
            return
        self._fetch(list(self._characters))

    def _fetch(self, characters: list[Character]) -> None:
        client = EsiClient(self.client_id.get().strip())
        self._run(f"Reading {len(characters)} character(s) from ESI…",
                  lambda: [(c, client.fetch_character(c)) for c in characters],
                  self._fetched)

    def _fetched(self, results: list) -> None:
        # Refreshing may have rotated tokens, so save those even if the rest fails.
        self._save()
        db = self._get_db()
        if db is None:
            messagebox.showwarning("Characters", "Download the SDE first, so planets "
                                                 "and items can be named.")
            return
        for character, raw in results:
            self._results[character.id] = build_character(character.id, character.name,
                                                          raw, db)
        self._show()

    def remove_character(self) -> None:
        name = self.remove_choice.get()
        match = next((c for c in self._characters if c.name == name), None)
        if match is None:
            return
        if not messagebox.askyesno(
                "Remove character",
                f"Remove {name} from this app? To fully end its access, also revoke "
                "the application on CCP's account site."):
            return
        self._characters.remove(match)
        self._results.pop(match.id, None)
        self._save()
        self._show()
