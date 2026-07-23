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

- **M0a (now, zero live-path risk):** stand up the guard (done: 3 contracts, 3
  kept) + fix the two real violations the discovery flagged:
  1. `pairs_executor.py` imports `src.units.accounts.execute` directly (a Strategy
     module reaching into Execution) → route through the coordinator/executor seam.
  2. `intents.py` / `intent_multiplexer.py` hardcode the ICT strategy roster
     (`DEFAULT_PRIORITIES`, the builder registry) → lift the roster into
     config/data so aggregation becomes strategy-agnostic.
- **M0b (incremental, one gated PR at a time):** physically reorganize toward the
  clean tree and graduate to a `layers` contract. Cheapest-highest-value first
  (the roster extraction above). The 342 KB `order_monitor.py` and 234 KB
  `strategy_signal_builders.py` decompositions are the long tail — split by
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
| **M0** | Layer enforcement + legacy migration | all | import-linter guard (M0a: done); fix the 2 violations; incremental drain (M0b) | No behavioral change to ICT; CI enforces the boundary; contracts broaden | A violation fix would require rewriting live order logic → defer, keep the narrower contract |
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

The system is no longer ICT-only (ICT + pairs + macro/value + soon macro-events),
so a rename for coherence is warranted. **Low-risk, low-priority:** GitHub
auto-301-redirects all old URLs + git remotes, the VM clone dirs
(`/home/ubuntu/…`, `/opt/…`) are unaffected by a repo rename, and only
`ict_detection/` is ICT-named internally. Cost is a wide-but-mechanical doc/workflow
sweep of the ~831 `ict-trading-bot` references, doable lazily via the redirect.
Sequenced as its own small PR once the name is chosen; it does not gate the
architecture work.

---

## Change log
- **2026-07-23** — Created. M0a enforcement guard stood up (import-linter, 3
  contracts kept). Phase 1 discovery + Phase 2 architecture approved by operator;
  one-engine + M0a-now/M0b-incremental + cost-model-pillar decisions recorded.
