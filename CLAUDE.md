# Eve PI Planner — project guide for Claude Code

A desktop (Tkinter) tool for planning Planetary Production in Eve Online.
Pure **Python 3.10+ standard library only** — no third-party packages.

## Run & test

```bash
python main.py                      # launch the GUI
python -m tests.test_core           # each suite is a plain script (no pytest)
python -m tests.test_official_sde
python -m tests.test_production
python -m tests.test_calculator
python -m tests.test_selection
python -m tests.test_market
python -m tests.test_profitability
python -m tests.test_guide_and_single
python -m tests.test_facilities
python -m tests.test_chain_map
python -m tests.test_icons
python -m tests.test_results_model
python -m tests.test_fulfil
python -m tests.test_esi
```

Quick "all green" check:
```bash
python -m py_compile main.py eve_pi/*.py tests/*.py
for t in core official_sde production calculator selection market profitability guide_and_single facilities chain_map icons results_model fulfil esi; do python -m tests.test_$t | tail -1; done
```
Tkinter isn't needed to run the tests or compile `gui.py` (py_compile only parses it).

## Module map (`eve_pi/`)

| File | Responsibility |
|---|---|
| `sde.py` | Download official CCP SDE (JSON Lines zip) → local SQLite cache (`populate_database`). |
| `db.py` | Read access: system lookup, jump-range BFS, planets, schematics (+ quantities/cycles). |
| `pi_data.py` | Fixed planet-type ↔ P0 matrix, planet-type IDs, P0→P1 names. |
| `resolver.py` | Resolve a product → required P0s / planet types. |
| `optimizer.py` | Minimum set-cover (exact branch-and-bound + greedy fallback). |
| `production.py` | Recipe demand, slot allocation, balance, output calculator, selection persistence. |
| `facilities.py` | Facility/planet-count accounting (basic vs advanced facilities → factory planets). |
| `profitability.py` | Tier classification + slot-filling portfolio ISK/hr ranking. |
| `guide.py` | Planet outputs + full P0→P4 production-chain trees. |
| `market.py` | Jita (ESI) best buy/sell + daily volume; ISK valuation. |
| `planner.py` | Orchestration → `Plan` (incl. single-system mode). |
| `chain_map.py` | Chain map data: full planet→P4 graph, input/output tracing, level colours (no Tkinter). |
| `chain_view.py` | Tkinter canvas that draws the chain map (hover, pin, double-click to plan). |
| `icons.py` | EVE icons from `images.evetech.net`, cached as PNGs in the user cache dir; optional. |
| `icon_set.py` | Loaded icons at the sizes the UI draws them, shared across tabs. |
| `theme.py` | Dark ttk/Tk theme for the whole app. |
| `results_model.py` | Display logic for visual results: statuses, character layout, axis ticks, number formats (no Tkinter). |
| `results_view.py` | Canvas panels for the Production estimate and Output calculator tabs. |
| `janice.py` | Reads an order from a Janice link or a pasted item list. |
| `fulfil.py` | Order optimiser: bill of materials, slot allocation, system choice, character split, and the `Plan` the other tabs display (no Tkinter). |
| `fulfil_view.py` | Canvas panel for the Fulfil order tab. |
| `esi.py` | EVE SSO login (PKCE, no secret), authenticated ESI reads, encrypted refresh-token store. |
| `colonies.py` | Raw ESI data → planet slots, per-planet production and rates, holdings, totals (no Tkinter). |
| `characters_view.py` | Controls and canvas panel for the Characters tab. |
| `gui.py` | Tkinter desktop app (tabs: chain map, fulfil order, characters, set-up, best systems, select planets, alternatives, production estimate, output calculator, production chain, PI guide). |

## Key domain facts & design decisions

- **Data source**: the official CCP SDE in JSON Lines
  (`developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip`),
  built into SQLite on first run. Jumps come from `mapStargates`
  (`solarSystemID` → `destination.solarSystemID`), planet types from
  `mapPlanets.typeID`, chains from `planetSchematics`.
- **Richness is NOT in the SDE** — per-planet extraction yield is scan-only.
  So everything downstream uses a nominal P0/hr rate the user supplies; the
  planet-type → P0 matrix in `pi_data.py` is hard-coded game design.
- **Single-system model** (`planner.single_system`, default on in GUI): a system
  can host the whole chain iff its own planets cover every required P0 (P0→P1 on
  the extractor planet, P2 in-system, P3/P4 on factory planets). Nearest
  qualifying system wins; otherwise fall back to fewest-systems + a "widen range"
  note.
- **Facility model** (`facilities.py`): per-facility output/hr from SDE
  quantities÷cycleTime. Basic (P0→P1) facilities live on extractor planets
  (default 8/planet); advanced (P1→P4) on factory planets (default 8/planet,
  both editable in the Output calculator tab). `build_breakdown` → extractor +
  factory planet totals and characters (6 planets each).
- **Best systems**: fills the slot budget with the best *mix* of a tier's
  products (most ISK/planet first), ranked by total ISK/hr. Self-contained.
- **Chain map levels** (`chain_map.py`): levels count production steps from the
  traced item, and P0→P1 costs nothing because the map joins them with an arrow.
  Layout and colours follow alysii.com/eve/pi, but the code is our own and the
  data comes from the SDE.
- **Icons are optional**: failed downloads leave that item as text only.
- **Order optimiser** (`fulfil.py`): minimises the time to finish the whole
  order. Finish time is the slowest of each P0 and the factory planets, so each
  spare slot goes to the current slowest. Facilities count as interchangeable
  facility-hours, and hauling time isn't modelled.
- **Orders in the other tabs** (`fulfil.order_plan`): an optimised order becomes a
  normal `Plan` whose recipe is `ORDER_PRODUCT` ("Whole order"), where one unit is
  the whole order. So "per hour" is orders per hour, which the Output calculator
  inverts into a duration. That duration covers extraction only; factory time is
  on the Fulfil order tab. Order plans set `Plan.order_items`. The optimiser's
  ticks and resources win over saved selections, and only per-planet rates are
  saved from them, so an order never changes the ticks Find planets remembers.
  Factory planets are in `ChosenSystem.factories` and aren't selectable.
- **Janice** has no key-free documented API, so `janice.py` uses the JSON-RPC call
  its web page makes (`Appraisal.find`). It may change; pasted lists always work.
- **Characters** (`esi.py`, `colonies.py`): one registered EVE application is built
  in as `esi.CLIENT_ID`, so users just click Log in with EVE. That ID is public by
  design under PKCE; the application's secret must never be in this repo. The
  callback is `http://localhost:8766/callback`. Scopes are `esi-planets.manage_planets.v1` and
  `esi-skills.read_skills.v1`. Access tokens stay in memory, and refresh tokens are
  stored with DPAPI on Windows (base64 only elsewhere) in `characters.json` in the
  user cache dir. Token signatures aren't verified (no RSA in the stdlib); tokens
  come straight from CCP over TLS. Slots = 1 + Interplanetary Consolidation (2495).
  ESI only recalculates a colony when it's viewed in game, so the tab always shows
  each colony's age. Extractor rates are ESI's nominal figure and factory rates are
  fully supplied maximums.
- **Real slots in orders** (`colonies.slot_roster`, `fulfil.split_roster`): once
  characters are read, Optimise plans around each one's free slots unless the box
  is unticked. Characters whose slots can't be read are named and left out, never
  guessed. A planet never goes to a character already on it. Results aren't saved,
  so typed numbers apply until Refresh runs. Existing colonies' output and holdings
  don't count towards the order, and Production estimate's character figures still
  assume 6 planets each.
- **Planner slot sharing**: planets are handed out one per round, scarcest
  resource first, so shared planet types aren't used up by earlier resources.

## Conventions

- Stdlib only. Keep it that way unless the user asks otherwise.
- Tests are assertion scripts printing "All N checks passed." Add one per feature.
- Synthetic test fixtures live in `tests/make_fixture.py` (legacy schema) and
  `tests/make_official_fixture.py` (official JSONL schema); no network in tests.
- `gui.py` can't be exercised headless (no display); validate its logic via the
  non-GUI modules + `py_compile`.

## Known-good state

All 14 suites pass (349 checks total). No third-party imports; no network needed
for tests (only `sde.py`, `market.py`, `icons.py`, `janice.py` and `esi.py` hit the network, at runtime).
