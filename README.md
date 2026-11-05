# Eve PI Planner

A desktop tool for planning **Planetary Production** in Eve Online.

Pick where you live (a staging system) and what you want to make, say how far
you're willing to travel, and the tool finds the planets you need — grouped
into the **fewest systems possible** — plus alternatives so you can adjust.

## What it does

- **Chain map** — the first tab shows every planet type, resource and product
  (P0 → P4) with EVE icons. Hover anything to trace what feeds it and what it
  makes, click to pin the trace, and double-click a product to plan it. The
  layout is inspired by [Alysii's PI Scheme](https://alysii.com/eve/pi/).
- **Fulfil order** — paste a [Janice](https://janice.e-351.com) appraisal link or
  an item list, set your characters and planets per character, and click
  *Optimise*. It works out the raw materials and factory work needed, splits your
  slots so everything finishes together in the least time, places the planets in
  as few in-range systems as it can, and assigns each planet to a character. It
  also shows how the finish time changes with more or fewer characters. The result
  also fills the other tabs: *Recommended set-up* lists every planet including
  factories, *Select planets* comes ticked with the optimiser's picks, and the
  *Output calculator* shows how long extraction takes (factory time stays on the
  Fulfil order tab). Optimising again resets the ticks to the optimiser's picks;
  per-planet rates you set are kept.
- **Characters** — log your characters in through EVE's own login page and see
  each one's planet slots, what every planet is extracting and making, at what
  rate, and what's in storage, with totals across all characters. To set it up,
  create an application at
  [developers.eveonline.com](https://developers.eveonline.com/applications) with
  the scopes `esi-planets.manage_planets.v1` and `esi-skills.read_skills.v1` and the
  callback URL `http://localhost:8766/callback`, then paste its Client ID into the
  tab. ESI only updates a colony when you view it in the game, so the tab shows how
  old each colony's figures are. Extractor rates are ESI's nominal figure, and
  factory rates assume full supply. Your login is stored encrypted to your Windows
  user. Removing a character only removes it here, so revoke the application on
  CCP's account site to fully end its access. After a Refresh, *Fulfil order* plans
  around each character's free slots, keeps planets off characters already on them,
  and names who gets what; untick its box to type your own numbers instead. Your
  existing colonies' output and holdings don't count towards the order.
- **Two ways to choose your output**
  - *Final product* — pick any PI commodity (P1 → P4, e.g. *Nano-Factory*).
    The tool walks the production chain back to the raw (P0) resources it needs
    and works out which planet types yield them.
  - *Planet types* — just tick the planet types you want to extract from.
- **Range search** — finds every system within *N* stargate jumps of your
  staging system (breadth-first over the real jump map).
- **Keep the whole chain in one system** (default) — the planner first tries to
  put *everything* in a single system: P0→P1 runs on each extractor planet, P2
  ideally in the same system, and P3/P4 on dedicated factory planets fed
  in-system. A system qualifies when its own planets cover every P0 the output
  needs; the nearest one is chosen. Untick *"Keep whole chain in one system"* to
  allow spreading. If no single system in range can host the whole chain, the
  plan says so (widen your jumps) and shows a fewest-systems fallback.
- **Fewest-systems fallback** — when mixing is allowed, a minimum set-cover gets
  your resources from as few systems as possible, ties broken by total jumps.
- **Planet-slot budget** — say how many planet slots you have to spend and the
  tool fills them, placing extra extractor planets to scale production. Slots
  are allocated **chain-balanced**: proportional to the product's real input
  ratios so your raw materials stay in step. It also reports how many
  **characters** the plan needs (Eve caps each character at 6 planets).
- **Production estimate** (richness-free) — bars, status labels and a
  planets-by-character diagram. Because real extraction rates depend
  on a planet's resource richness (scan-only, not in the SDE), the *Production
  estimate* tab shows the **balance** of your allocation against the recipe:
  capacity vs demand per resource, the bottleneck, and the full per-unit chain
  breakdown.
- **Output calculator** — enter how much P0 you actually pull per extractor
  planet (units/hour) and the *Output calculator* tab estimates your **maximum
  finished-product rate** (per hour and day), which input is **limiting**, and
  exactly **what's produced in excess** so you can rebalance or sell the surplus.
- **Hand-pick your planets** — the *Select planets* tab lists every in-range
  candidate (the optimiser's picks pre-ticked). Tick/untick to choose exactly
  which planets you'll use, and **right-click any planet** to set its real P0
  output (units/hour) and which raw resource it extracts. The production
  estimate, calculator and surplus all update from your selection. Choices and
  per-planet rates are **saved between sessions**.
- **Live Jita market data** — click *Fetch Jita prices* in the calculator to
  pull current **Jita 4-4 buy / sell / split** and **daily traded volume** from
  ESI (the official CCP API) for your finished product and every surplus you'd
  sell, and see the **ISK/hour and ISK/day** value of your output. Choose the
  price basis (split / buy / sell).
- **Most-profitable-system finder** — the *Best systems* tab ranks every system
  in range for a chosen output **tier (P1–P4)**. Each system is scored as a
  *self-contained* base and its **planet slots are filled with the best mix** of
  that tier's products it can make (most ISK/planet first) — so all your planets
  earn, not just one product line. Valued at Jita and ranked by **total ISK/hour**
  (with ISK/hour per planet shown too). Great for picking where to live.
- **PI guide** — a reference tab: the planet-type → raw-resource table, plus a
  picker to see any product's full **P0→P4 production tree** with per-step ratios
  and which planet each P0 is extracted on.
- **Full build: factory planets & characters** — the production estimate turns
  your throughput into real planet counts: **basic** facilities (P0→P1) on
  extractor planets and **advanced** facilities (P1→P4) on dedicated **factory
  planets**, from SDE facility rates. Set basic-factories-per-extractor and
  advanced-facilities-per-factory-planet (defaults 8) in the Output calculator
  tab; the plan then reports extractor + factory planet totals and the real
  **character count** (6 planets each).
- **Optional considerations**
  - Security-band filter (High / Low / Null).
  - *Alternatives per resource* tab: every in-range system/planet that can
    supply each resource, sorted by distance — so you can swap picks.
  - *Production chain* tab: the P0 breakdown and other equally-good system sets.

## Requirements

- Python 3.10+
- Standard library only (`sqlite3`, `tkinter`, `urllib`, `zipfile`, `json`).
  - Tkinter ships with the python.org installers on Windows and macOS.
  - On some Linux distros: `sudo apt install python3-tk`.

## Running

```
python main.py
```

On first launch, click **Download / Update SDE**. This fetches the **official
CCP Static Data Export** (JSON Lines format) from
[developers.eveonline.com/static-data](https://developers.eveonline.com/static-data)
— specifically the always-latest shorthand URL
`eve-online-static-data-latest-jsonl.zip` — and builds a local SQLite cache in
your user cache directory from the `mapSolarSystems`, `mapPlanets`,
`mapStargates`, `planetSchematics`, and `types` datasets. It's a one-time
download (a few hundred MB; the cached database is much smaller). Re-run it
after major Eve patches to refresh the data.

Then:

1. Type your **staging system** (autocomplete suggests names).
2. Set **max jumps**, your **planet slots** budget, and the **security bands**
   you'll travel to.
3. Choose **Final product** and a commodity, *or* **Planet types** and tick
   the types you want.
4. Click **Find planets**.
5. In the **Select planets** tab, tick the planets you'll actually use and
   right-click any of them to set its real P0/hour and extracted resource.
6. Open the **Output calculator** tab, set the default P0/hour, and hit
   **Recalculate** to see finished-product throughput and surpluses. Click
   **Fetch Jita prices** to value your output and surplus in ISK/hour.
7. Or skip ahead: in the **Best systems** tab pick a tier (P1–P4) and click
   **Find best systems** to rank the systems in range by ISK/hour.

The **Recommended set-up** tab lists each system to colonise, its planets, and
which resource each one covers (multiple planets per resource once you give it a
slot budget). The remaining tabs show alternatives, the production estimate, the
output calculator, and the full production chain.

## How it works (internals)

| Module | Responsibility |
|---|---|
| `eve_pi/sde.py` | Download the official CCP JSONL SDE zip, build the SQLite cache. |
| `eve_pi/db.py` | Read access: system lookup, jump-range BFS, planets, schematics. |
| `eve_pi/pi_data.py` | Fixed planet-type ↔ P0-resource matrix. |
| `eve_pi/resolver.py` | Resolve a product to its required P0s / planet types. |
| `eve_pi/optimizer.py` | Minimum set-cover (exact branch-and-bound + greedy fallback). |
| `eve_pi/production.py` | Recipe demand, slot allocation, balance, calculator, selection persistence. |
| `eve_pi/facilities.py` | Facility and factory-planet counts for a target output rate. |
| `eve_pi/chain_map.py` | Chain map data: full planet → P4 graph, tracing and colours. |
| `eve_pi/chain_view.py` | Tkinter canvas that draws the chain map. |
| `eve_pi/icons.py` | EVE icons from CCP's image server, cached on disk. |
| `eve_pi/icon_set.py` | Loaded icons shared across tabs. |
| `eve_pi/theme.py` | Dark theme for the whole app. |
| `eve_pi/results_model.py` | Display logic for the visual results tabs. |
| `eve_pi/results_view.py` | Production estimate and Output calculator panels. |
| `eve_pi/janice.py` | Reads an order from a Janice link or pasted list. |
| `eve_pi/fulfil.py` | Order optimiser: materials, slots, systems, characters. |
| `eve_pi/fulfil_view.py` | Fulfil order panel. |
| `eve_pi/esi.py` | EVE login, character reads from ESI, saved logins. |
| `eve_pi/colonies.py` | Planet slots, production, rates and holdings from ESI data. |
| `eve_pi/characters_view.py` | Characters tab. |
| `eve_pi/market.py` | Jita prices + daily volume from ESI; ISK valuation of output/surplus. |
| `eve_pi/profitability.py` | Tier classification + slot-filling portfolio ISK/hr ranking. |
| `eve_pi/guide.py` | Planet outputs + full P0→P4 production-chain trees. |
| `eve_pi/planner.py` | Orchestration (incl. single-system mode) → a `Plan` the GUI renders. |
| `eve_pi/gui.py` | Tkinter desktop app. |

### Data sources

- Universe, jumps, planets, and PI schematics: the **official CCP SDE** in
  JSON Lines format. Jump connectivity is built from `mapStargates`
  (`solarSystemID` → `destination.solarSystemID`); planet types from
  `mapPlanets.typeID`; the PI production graph from `planetSchematics` (each
  schematic's `types[]` entries with `isInput` true/false).
- The planet-type → raw-resource matrix is fixed game design and is hard-coded
  in `pi_data.py` (the SDE doesn't express it cleanly).
- Market prices come from **ESI** at runtime: best Jita 4-4 buy/sell from
  `/markets/10000002/orders/` (filtered to station 60003760) and average daily
  traded volume from `/markets/10000002/history/`. No login required.
- Janice links are read with the same request Janice's own page makes. It isn't a
  documented API and could change; pasting the item list always works.
- Item icons come from CCP's image server (`images.evetech.net`). They download
  once and are cached in your user cache directory; without a connection, the
  chain map shows text only.

> Note: actual per-planet resource *richness* is only visible from in-game
> scanning and is **not** in the SDE, so the tool plans by planet *type* and
> distance, not by individual-planet output quality.

## Tests

Synthetic fixtures exercise the resolver, BFS, optimizer, slot allocation,
recipe maths, the output calculator, and end-to-end planning without any
download. `test_official_sde` additionally feeds official-format JSONL records
through the real `sde.populate_database` transform:

```
python -m tests.test_core            # resolver, BFS, optimizer, planning (17 checks)
python -m tests.test_official_sde    # official JSONL -> SQLite -> planning (11 checks)
python -m tests.test_production      # slot allocation, recipe demand, balance (19 checks)
python -m tests.test_calculator      # absolute throughput & surplus calc (9 checks)
python -m tests.test_selection       # per-planet picks calc & persistence (9 checks)
python -m tests.test_market          # Jita valuation maths (8 checks)
python -m tests.test_profitability   # tier classify, slot-filling portfolio (16 checks)
python -m tests.test_guide_and_single  # chain tree + single-system planner (13 checks)
python -m tests.test_facilities      # facility & factory-planet counts (12 checks)
python -m tests.test_chain_map       # chain map graph, tracing, colours (31 checks)
python -m tests.test_icons           # icon download & cache, faked network (12 checks)
python -m tests.test_results_model   # results display logic (26 checks)
python -m tests.test_fulfil          # Janice import & order optimiser (90 checks)
python -m tests.test_esi             # EVE login, ESI reads, colony logic, faked CCP (70 checks)
```
