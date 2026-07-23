# ROADMAP_MACRO — Platform Layering + the Macro Event-Driven Strategy Family

> **Status:** 🔄 IN PROGRESS 2026-07-23 (operator-directed). This document is the
> design-of-record for two intertwined efforts: **(1)** making the repo's
> already-articulated layer boundary *machine-enforced* (the investment-grade
> upgrade), and **(2)** adding a **macro event-driven** strategy family
> (energy / carbon) as the first tenant that proves the platform.
>
> Companion to [`ROADMAP.md`](./ROADMAP.md) (the milestone ledger for the
> existing ICT + ML work, incl. the M28 macro/value sleeve this family reuses).
> Operating rules, tiers, and autonomy mandate are in
> [`docs/CLAUDE-RULES-CANONICAL.md`](./docs/CLAUDE-RULES-CANONICAL.md).

## 0. Framing — platform, not add-on

The discovery pass (Phase 1) found the repo is at an inflection point: the 3-layer
boundary is **already articulated** (`src/core/` contracts + the `_base.py`
"strategies are pure signal generators" rule) but **not enforced**, and the live
money-path lives in large `src/runtime/` monoliths that blur the layers. Left
alone, every new family accretes against un-enforced boundaries. The
investment-grade move is to **enforce the boundary now and give the legacy a
migration path**, then add the new family *into* the enforced structure.

**Decision (operator, 2026-07-23): ONE engine, not two.** The energy/carbon
family is **not** a parallel system — it is a new **Signals pack** + a new
**Execution venue** plugged into the **existing M28 thesis engine**
(`TradeThesis`, `event_resolver`, `thesis_backtest`, the point-in-time
discipline). This aligns with the long-term goal of a single master decision
system that "sees everything" rather than a fleet of disconnected bots.

## 1. Target architecture

Four layers; imports only ever point "down":

| Layer | Contents | Rule |
|---|---|---|
| **1 · Signals** | data ingestion / normalization / point-in-time stores / features / **event calendars** | No strategy, no broker, no P&L, no order types. Extractable/sellable on its own. |
| **2 · Strategy** | signals → `TradeThesis` / trade intents (mechanical + ML). `ict/` · `pairs/` · `macro_thesis/` · `macro_events/` (new) | May import Signals. **Emits an intent, not an order.** |
| **3 · Execution** | order routing · broker adapters · fills · reconciliation · **risk limits · kill switches** · the **cost model** | May import Strategy *types* + Signals. Nothing imports Execution. |
| **0 · Platform** | cross-cutting: config · db · logging · paths · web/observability | Any layer may use. |

### 1a. Enforcement — the piece that makes "sellable on its own" true

Folder structure will not hold the boundary; **import rules will.** Enforced by
[`import-linter`](https://import-linter.readthedocs.io/) via
[`.importlinter`](./.importlinter), run in CI by
[`.github/workflows/layer-guard.yml`](./.github/workflows/layer-guard.yml)
(`lint-imports` — static AST analysis, never executes the modules).

**Pragmatic finding:** today's packages *span* layers (`runtime/` touches all
three; `macro_thesis/` is Signals+Strategy; `units/` is all three), so a single
clean "layers" contract cannot be pointed at the current tree. The contracts
therefore start as **`forbidden` invariants that hold today** (so CI is green
immediately) and broaden as legacy violations are fixed:

- **Now (green):** `macro_thesis`, `news`, and `ict_detection` must not import
  Execution / brokers / the order path. This permanently locks in the clean
  Signals boundary — the base the energy tenant reuses — and prevents regression.
- **Broaden incrementally:** add each package to the contracts as its violations
  are fixed, graduating to a full `layers` contract once the physical reorg lands.

### 1b. Legacy migration plan (M0)

Behavior-preservation is absolute — the live ICT trader must not change behavior.

- **M0a (DONE, zero live-path risk):** guard stood up — **5 contracts, 0 broken**
  (macro_thesis, news, ict_detection, pairs_engine, vwap). The two discovery-flagged
  "violations" are both **resolved**:
  1. `pairs_executor.py` imports `src.units.accounts.execute` — **resolved by
     classification** (2026-07-23): the pairs sleeve is an INTENTIONAL isolated
     order path, so `pairs_executor` IS Execution; the invariant locked is that the
     DECISION half (`pairs_engine`) stays pure (contract #4). Not a refactor of the
     live order path.
  2. `intents.py` / `intent_multiplexer.py` "hardcode the roster" — **assessed →
     misdiagnosis, operator-accepted 2026-07-23** (`BL-20260723-INTENTS-ROSTER-ASSESSMENT`):
     `DEFAULT_PRIORITIES` is a legitimate central Tier-3 conflict-arbitration policy
     table; the two builder rosters are a legacy-subset + authoritative-superset with
     a working fallback (a money-bug fix), not a dup-that-drifted. The broad decouple
     is NOT pursued; the one safe sub-fix (a stale comment) shipped (#7460).
- **M0b (incremental, one gated PR at a time):** physically reorganize toward the
  clean tree and graduate to a `layers` contract. **First drain DONE 2026-07-23**
  (#7459): cut `units.db.database → runtime.local_pnl → Execution` by single-sourcing
  the contract-value resolver in the pure `core.profile_loader`, which let **vwap** be
  locked as the first monolith-era strategy (contract #5). The 342 KB `order_monitor.py`
  and 234 KB `strategy_signal_builders.py` decompositions are the long tail — split by
  concern, each PR behavior-preserving + tested against the real-schema fixtures.

### 1c. Compute invariant (guards the "one engine" decision)

The live trader is OCI Always-Free (2 OCPU / 12 GB) and must stay light. **Rule:
heavy compute runs OFF the live VM; the live tick only reads pre-computed
point-in-time snapshots and is cadence-gated.** Adding energy = marginally more
symbols in the same hourly scan. The new feeds (EIA, weather, EU-ETS) + any LLM
extraction run off-VM (trainer VM / scheduled workflows), writing snapshots the
live tick reads. The one real pressure is the **LLM $-budget** (`insights_usage`),
not CPU — widen it deliberately, keep it observable.

## 2. The macro event-driven family as an M28 tenant

Reuse map (built, on `main`): `event_calendar` + `event_store` + `event_resolver`
(the non-price decision DSL), `TradeThesis` + `watched_events`/`on_outcome`,
`thesis_backtest` (calibration + net-of-cost + beat-baseline), `thesis_replay`
(point-in-time, no-lookahead). **New build:** a `signals/energy/` pack (the event
calendars below) + a new **Execution venue** (MNG-via-IB reuses a wired broker),
and — critically — the Strategy→Execution handoff M28 deferred (its executor is
"P5, not yet wired"). Energy is where that handoff finally gets built.

## 3. Design specification

### Markets & instruments (all cash-settled, screen-traded, retail-accessible)
- **Micro Henry Hub natural gas (MNG, NYMEX/CME Globex)** — 1,000 MMBtu, 1/10 the
  standard NG contract, financially settled. **Primary development market**:
  weekly scheduled catalysts + small size = cheapest place to validate. Fits the
  existing **IB** adapter (already trades MES/MGC/MHG futures).
- **Carbon** — direct EUA futures are institutional-scale (1,000 t/lot).
  Retail-accessible exposure is via **KraneShares ETFs** (KRBN global / KEUA
  Europe / KCCA California), which carry **~150–250 bp all-in annual cost**
  (expense ratio + roll drag) — this **rules out short-horizon event trades in
  the ETF wrapper** and implies longer holds. **Open research question (not a
  settled fact):** Nodal Exchange's financially-settled California Carbon
  Allowance futures (launched Mar 2026) may be a better instrument *if* retail
  access exists — treat as research, verify before scoping.
- **Power (PJM/ERCOT)** — the link between compute/data-center demand and the
  gas–carbon complex. **Research only in v1; no trading scoped.**

**Explicitly out of scope:** forward freight agreements (voice-brokered OTC,
clearing-member relationships, 1,000-t lots — research input only), prediction
markets, and any GPU/compute-contract trading (no liquid screen-traded market).

### Core thesis
These markets reprice around **scheduled** events with **forecastable** inputs.
The edge is **research latency, not wire latency** — modelling surprise-vs-consensus,
not being fast to the tape. The gas–power–carbon complex is one system observed
at three points; mispricings live in the **linkages** (spark/dark spreads, clean
variants, coal-to-gas switching) more than in single legs.

### Event calendar (the Signals layer's primary new asset)
- **EIA weekly natural-gas storage (Thursdays)** — the canonical test case.
- Weather model releases + forecast revisions.
- EU ETS auction calendar, free-allocation reform votes, compliance deadlines.
- Data-center interconnection-queue filings, utility resource plans, LNG export
  terminal commissioning.

### Scanner-first discipline (non-negotiable)
Before any capital or any live strategy, the deliverable is a **measurement
instrument**: a backtest of event dates, published (point-in-time) consensus,
realized surprise, and subsequent price response across multiple horizons —
answering whether repricing is systematic or we are fitting noise to a story.
**Proceed/stop thresholds are written down BEFORE results are seen** (a
pre-registered study). The `thesis_backtest` calibration machinery (does
conviction predict realized hit-rate?) is exactly this instrument.

## 4. The cost-model pillar (first-class)

Consolidate the scattered pieces (`runtime/trade_costs.py`,
`broker_cost_attribution.py`, `broker_truth.py`, `prop/montecarlo.py` EV, M24
net-R, M28 net-of-cost) into **one execution-realism component** in Layer 3 that
**both** every backtest **and** the live sizer consult — modelling fees, **roll
drag** (the carbon-ETF 150–250 bp problem), slippage, funding, and data-feed
staleness / point-in-time gaps. This is what lets us honestly ask *"does this
edge survive real costs on an instrument we have not wired yet."*

## 5. Milestones

Each milestone has a **gate** (criterion to proceed) and a **stop condition**.

| # | Milestone | Layer | Deliverables | Gate | Stop |
|---|---|---|---|---|---|
| **M0** | Layer enforcement + legacy migration | all | import-linter guard (M0a: **DONE** — 5 contracts, 0 broken); both flagged violations resolved (#1 by classification, #2 assessed→declined, operator-accepted); M0b first drain **DONE** (#7459, db.database→local_pnl cut + vwap locked); incremental drain continues | No behavioral change to ICT; CI enforces the boundary; contracts broaden | A violation fix would require rewriting live order logic → defer, keep the narrower contract |
| **M1** | Energy event calendar + signals | Signals | EIA release history + **point-in-time** published consensus + realized values + MNG price series, joined | Clean joined dataset over multiple years of releases | Consensus not available point-in-time (revised-only) → the whole study is unsafe; stop and re-scope the data source |
| **M2** | Event-response backtest | Strategy | surprise-vs-consensus → forward returns at several horizons, via `thesis_backtest` calibration + the cost model | **Pre-registered thresholds met** (calibration rank + net-of-cost edge vs a naive baseline) | Thresholds not met → repricing is not systematic; do not build the strategy |
| **M3** | Paper trading | Strategy+Execution | strategy emits intents; execution runs the new venue in paper mode (the M28-deferred handoff) | Realized paper behavior matches backtest expectation | Live behavior diverges from backtest → the model is mis-specified |
| **M4** | Carbon extension | Signals+Strategy | port the validated methodology to the slower policy-driven markets | M2-equivalent evidence on carbon events (accounting for the ETF cost drag) | No M2-equivalent evidence → carbon does not carry the edge at our cost structure |
| **M5** | Live, minimum size | Execution | MNG-via-IB, minimum size, kill-switches armed | Only after M3 holds + operator approval (Tier-3) | Any breach of the cost/risk assumptions → revert to paper |

## 6. Risk register

- **Overfitting to few events.** Weeks-horizon → low n. Validate by **calibration
  + net-R**, never by significance from a handful of wins (the M28 §8 rule).
- **Point-in-time consensus integrity (the classic event-study lookahead bug).**
  **Never use revised consensus figures** — only what was published *before* the
  release. Enforced by the same `observed_at`/`as_of` discipline as
  `thesis_replay.as_of_snapshot_rows`. This is the #1 way to manufacture a fake edge.
- **ETF cost drag vs holding period.** 150–250 bp/yr rules out short-horizon
  carbon-ETF event trades → the cost model must gate horizon selection.
- **Broker access + margin from Israel.** MNG-via-IB reuses a wired broker;
  verify instrument permissions + margin before M5.
- **Attentional risk of destabilizing a working ICT system.** M0 is
  behavior-preserving + incremental; the enforcement guard *protects* the live
  system by preventing new coupling. No big-bang extraction of live-path monoliths.
- **LLM $-budget** as feeds grow (§1c) — widen deliberately, keep observable.

## 7. Repo identity

**DONE (2026-07-23):** renamed to **`benbaichmankass/Metis-Insights`** (Metis —
the Greek Titaness of wisdom + strategy, mother of Athena). GitHub 301-redirects
every old URL, git remote, and API call, so nothing broke on the rename.

**Do NOT do a blind reference sweep.** Of the ~831 `ict-trading-bot` references,
most are either redirect-safe URLs or **VM clone-dir paths**
(`/home/ubuntu/ict-trading-bot`, `/opt/ict-trading-bot`) that a repo rename does
NOT change — sweeping those to the new name would break the deploys. The
session's MCP access scope is also still pinned to the string `ict-trading-bot`
(redirected), so tooling keeps using the old name. See CLAUDE.md § "Repo
identity" for the full operational contract. A scoped coherence sweep of the
prose/URL references (excluding the VM paths) is low-priority and migrates
lazily via the redirect.

---

## Change log
- **2026-07-23 (cont. 6)** — **CFTC-COT positioning sleeve built (signal #2 — a NEW input family).**
  Keyless weekly **Commitments-of-Traders** large-speculator positioning (crude/gas/gold/copper/
  ES/FX/rates) from the CFTC Legacy Futures-Only Socrata report → a **snapshot→percentile COT-index
  conviction emitted in the valuation-snapshot schema**, so the existing M28 P4 gate +
  horizon-IC scan grade it **UNCHANGED** (an integration test drives a COT row through
  `build_replay_entries`→`run_thesis_backtest` to prove it). Orientation is **contrarian on the
  large specs** (`cheap_score = 1 − percentile(spec_net)`, `higher_is_cheaper=False`: crowded
  net-long = rich = short bias; washed-out = cheap = long bias) — a **graded hypothesis**, not a
  claim (a negative edge just means flip to the momentum orientation). Point-in-time: each row's
  `observed_at` = report date + a 3-day release lag (Tuesday positions are public Friday), and each
  percentile uses only the trailing `lookback` window ending at its own date. Files:
  `scripts/macro/cot_data.py` (off-VM Socrata reader + pure parsers + COT-index), `cot_snapshot_backfill.py`
  (full-regen PIT backfill → `comms/macro/cot_snapshots.jsonl`), `tests/test_m29_cot.py` (10 tests
  incl. the P4-machinery integration proof), `.github/workflows/cot-positioning-backfill.yml`
  (off-VM: backfill → proxy candles → P4 + horizon-IC → land snapshots + both scorecards; label
  `cot-positioning-backfill-now`). Tradeable proxies: USO/UNG/GLD/CPER/SPY/FXE/IEF. This is the
  first NEW input family for the "signals too thin → richer inputs" thread (value P4 + gas P1b were
  both null). **Next: (3) crypto funding/OI/basis** (Bybit public API, same schema).
- **2026-07-23 (cont. 5)** — **M29 P1c built — the fair test of the SD thesis (dual-target
  gas calibration).** Extended the gas-seed calibration with the two real inputs P1b's null
  result named as the lever: **observed EIA weekly working-gas-in-storage** (`NG.NW2_EPG0_SWO_R48_BCF.W`,
  EIA v2 API, the now-set `EIA_API_KEY`) as the **anchor 2nd calibration target**
  (`seed_gas.storage_series` predictor — pins the stock trajectory to reality:
  `initial_storage`=first observed, `storage_normal`=mean observed), and **real weather HDD**
  (keyless Open-Meteo daily-temp archive over a gas-heating city basket → national heating
  degree days) as the `heating_demand` driver **replacing the calendar-seasonal proxy** — the
  surprise-carrying input the B1 loop's edge lives in. `identify` gained a pure, backward-compatible
  `steps` override so a stacked mean-normalised **joint** storage+price fit reuses the optimizer;
  `src/sysdyn/*` stays import-linter-pure (6 contracts kept), all IO in `scripts/`. New dual
  scorecard (`comms/macro/sysdyn_gas_dual_scorecard.json`) reports storage fit + **price-readout**
  fit (the tradeable quantity) + identifiability + a **`go_no_go`** verdict. **The decisive
  mechanistic-vs-static gate:** `invest_deeper` only if the storage-anchored + weather-driven
  model's **price readout predicts OUT-OF-SAMPLE** (beats P1b's ~0) **AND** the structure is
  identifiable; anything else parks deeper M29 investment. Files: `scripts/macro/sysdyn_gas_{data,calibrate}.py`,
  `src/sysdyn/identify.py`, `.github/workflows/sysdyn-gas-calibrate.yml` (runs P1b+P1c, lands both
  scorecards), `tests/test_m29_sysdyn_gas_p1c.py` (parsers + synthetic dual round-trip, 20 tests).
  Writeup: [`docs/research/M29-P1c-gas-dual-calibration.md`](docs/research/M29-P1c-gas-dual-calibration.md).
  The go/no-go call comes from the workflow run against live EIA+Open-Meteo+FRED data. **Next in
  the queue: (2) CFTC-COT positioning sleeve, (3) crypto funding/OI/basis** — both emit the
  valuation-snapshot schema so the P4 + horizon-IC scans grade them unchanged.
- **2026-07-23 (cont. 4)** — **Horizon-IC eval upgrade + next-signals queue (session handoff).**
  Added `scripts/macro/horizon_ic_scan.py` — runs the P4 point-in-time replay across a
  RANGE of horizons and reports IC(H)=Spearman(conviction, forward net-return) + a t + edge
  at each, so a signal is judged at the horizon it actually predicts (the P4 gate hard-codes
  30d). Reuses the P4 machinery wholesale → grades any valuation-snapshot-schema signal;
  wired as a step into `macro-valuation-backfill.yml`. Both this + M29 P1b are in **PR #7485**.
  **`EIA_API_KEY` secret is now set → M29 P1c is UNBLOCKED.** Next-session queue, in priority
  order (see the paste-ready handoff): **(1) M29 P1c** — inject observed EIA weekly storage
  (2nd calibration target via `seed_gas.storage_series`) + weather HDD as the real
  `heating_demand` driver, replacing the calendar proxy (EIA v2 API, series
  `NG.NW2_EPG0_SWO_R48_BCF.W`); dual-target scorecard → the mechanistic-vs-static go/no-go.
  **(2) CFTC-COT positioning sleeve** (keyless weekly, snapshot→percentile in the valuation
  schema). **(3) crypto funding/OI/basis** (Bybit public API, same schema). (2)+(3) are graded
  by the P4 + horizon-IC scans unchanged because they emit the same snapshot schema. Both
  operator-directed this session; the two null results so far (value P4 no-edge, gas P1b
  `equifinal_no_edge`) point the same way — signals too thin, next lever is richer inputs.
- **2026-07-23 (cont. 3)** — **M29 P1b built — calibrate the gas seed on real data.**
  Wired the off-VM calibrate-on-real-data harness for `gas_storage_price_v1` (the SD
  analogue of the M28 P4 value gate): `scripts/macro/sysdyn_gas_data.py` (injected
  reader — real weekly Henry Hub NG price via keyless FRED + calendar-seasonal exog
  drivers) + `scripts/macro/sysdyn_gas_calibrate.py` (identify + walk-forward-stability
  → out-of-sample fit + structural-param identifiability scorecard), the
  `sysdyn-gas-calibrate` workflow (off-VM, PAT auto-merge, label
  `sysdyn-gas-calibrate-now`), and tests (synthetic round-trip proves the harness).
  `src/sysdyn/*` stays import-linter-pure (all IO in `scripts/`). **First result
  (8y / 418 wk): `equifinal_no_edge` — the seed on a calendar-seasonal demand proxy
  does NOT explain real weekly NG price (OOS R²≈0) and its structural params are not
  identifiable from price alone.** Honest, expected finding (not a bug — the synthetic
  round-trip fits at OOS R²>0.9): price + calendar season is too thin. **P1c** is the
  unblocked next step — inject observed EIA weekly storage (2nd target) + weather HDD
  (real surprise-carrying demand), which needs a free `EIA_API_KEY` Actions secret (an
  operator hand-off). The go/no-go on deeper M29 investment should be taken after P1c.
  Writeup: [`docs/research/M29-P1b-gas-seed-calibration.md`](docs/research/M29-P1b-gas-seed-calibration.md).
- **2026-07-23 (cont. 2)** — **New milestone M29 — AI-Driven System-Dynamics Modelling**
  proposed (operator-directed), design-of-record
  [`docs/research/M29-ai-system-dynamics-DESIGN.md`](docs/research/M29-ai-system-dynamics-DESIGN.md).
  It **feeds this family**: its primary target (A) models the macro–energy complex as
  stocks/flows/feedback-loops (NG-storage↔weather↔power↔carbon linkages — the "one system
  observed at three points" framing of §3), AI-identifies the structure/params from
  point-in-time data, and runs forward scenarios that condition M28 `c_macro` conviction —
  directly serving the M1 energy-event-study / M2 event-response backtest here. It reuses
  the same point-in-time + calibration + cost-model discipline. PROPOSE-ONLY; observe-only
  until its P4 backtest passes; Tier-3 for any live influence.
  Also surfaced this session: the **M28 P4 gate blocker** — the FRED valuation-snapshot
  producer is unwired (nothing schedules `run_valuation_feed`), so the P4 backtest can't
  run until the off-VM producer is built
  ([`M28-P4-fred-producer-unwired-2026-07-23.md`](docs/research/M28-P4-fred-producer-unwired-2026-07-23.md),
  `MB-20260723-M28-VALUATION-PRODUCER-UNWIRED`). This is the same "off-VM FRED value soak"
  M1's clean-joined-dataset gate depends on.
- **2026-07-23** — Created. M0a enforcement guard stood up (import-linter, 3→4
  contracts kept; violation #1 resolved by classifying `pairs_executor` as
  Execution + locking `pairs_engine` purity). Phase 1 discovery + Phase 2
  architecture approved by operator; one-engine + M0a-now/M0b-incremental +
  cost-model-pillar decisions recorded. **Repo renamed → `Metis-Insights`** (§7).
  Violation #2 (intents.py roster decoupling) is the next M0b item.
- **2026-07-23 (cont.)** — M0a widened to **5 contracts, 0 broken** (added
  `pairs_engine` #7453 + `vwap` #7459). **M0b first drain shipped** (#7459): cut
  `units.db.database → runtime.local_pnl → Execution` by single-sourcing the
  contract-value resolver in the pure `core.profile_loader` (behavior byte-identical),
  which let `vwap` be locked as the first monolith-era strategy. **Violation #2
  assessed → misdiagnosis, operator-accepted** (`BL-20260723-INTENTS-ROSTER-ASSESSMENT`):
  the roster/priority centralization is legitimate, not decoupled; only the stale
  "keep in sync" comment was corrected (#7460). Encoded the Layer-2 refinement that a
  STRATEGY may reference contract TYPES (the seam) but not Execution implementations.
