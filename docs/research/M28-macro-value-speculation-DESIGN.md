# M28 — Thesis-Driven Macro/Value Speculation Sleeve (design of record)

> **Status:** 🔄 IN PROGRESS 2026-07-23 (operator-directed). Evidence-gathering,
> data plumbing, and backtests are Tier-1 autonomous; anything that places a
> live order, changes routing/sizing, or feeds the shared conviction model is
> Tier-3, walk-forward-gated, operator-approved.
>
> **Build status (2026-07-23):** **P0** (design + schema), **P1** (valuation
> core/feed/FRED-adapter/store), **P2** (event resolver + store + calendar feed),
> the **`TradeThesis` core object** + lifecycle, the **operational data layer**
> (thesis store + `macro_signals`), the **S1 rule-based thesis-former**, and
> **P3 tick-wiring** (the isolated `run_macro_thesis_tick` off `src/main.py` —
> observe-only scanner, `execution: shadow` default, **no order path**) are all
> **DONE** — merged #7411/#7418/#7422/#7424/#7429/#7434/#7437/#7440/#7445. All
> pure/observe-only, 148 tests green. **NEXT: P4 — the point-in-time backtest
> paradigm** (Tier-1, the decisive gate): no thesis engine graduates to a live
> path until it beats a naive baseline out-of-sample here. Then P5 (options
> executor, Tier-3) + P6 (`c_macro` overlay, Tier-3).
>
> **Operator framing (verbatim intent):** build a strategy that focuses on
> *value trading* — combining technical analysis, macro signals, and news to
> identify and act on opportunities based on *what's going on in the world*.
> Trades can take **up to weeks** to play out. Trade structure must include
> **non-price elements** (events we watch and make decisions based on their
> *results*). It is **not tied to a symbol** — the whole point is to *scan
> markets for opportunities* to make informed speculations. This is a big
> milestone that will necessitate new infrastructure: new data feeds, scraping
> tools, ML/LLM that turns unstructured data into *traceable* signals (maybe a
> small LLM), new model types, new testing tools and protocols.
>
> **Operator scoping decisions (2026-07-22):**
> - **(a) Isolated sleeve first, global overlay later.** Build M28 as a
>   self-contained sleeve with its own conviction, capital, and order path (the
>   M22 pairs-sleeve template). Prove it standalone. Only *after* it earns trust
>   does a macro "world-state" read become a global overlay tilting the whole book.
> - **(b) LLM end-goal is bold, the path is disciplined.** The target is an LLM
>   that **proposes full theses end-to-end**, reached via **stepping stones**:
>   extractor-only → extractor + rules-formed theses + LLM grader → LLM-proposed
>   theses. We build the *traceability + backtest scaffolding first* so the LLM
>   never silently "decides" a trade before we can hold it accountable.
> - **(c) Soak venue = `alpaca_options_paper`.** First paper theses express as
>   **defined-risk options** on the already-wired Alpaca options paper stack —
>   the natural fit for weeks-horizon asymmetric macro/value bets (known max
>   loss, no margin-call risk over a multi-week hold).
> - **(d) Start narrow.** A small, curated universe first (the macro-expressive
>   ETFs already wired + their options), not a broad screener on day one. The
>   scanner generalizes once the pipeline is proven end-to-end.
> - **(e) Free data only.** No paid feeds. The stack is built on **free/keyless**
>   sources — **FRED** (macro), **SEC EDGAR** (fundamentals/filings), the free
>   government release calendars (**BLS/BEA/Treasury/Fed**), **RSS** news, and
>   free market data (`market_data.py`/yfinance) — with light scraping only where
>   no free API exists. (The Bigdata.com MCP stays a *possible* accelerator but
>   is **not** a dependency — the design must stand on free sources.)
> - **(f) Fundamentals are P1 — the CORE, not deferred.** "Value" is the spine of
>   this strategy, so a **fundamental/valuation feed is a first-class P1
>   deliverable**, not a later phase. See §1a for what "value" means for the
>   ETF/options universe.

## 1. Why this is a new spine, not another strategy

Every strategy the bot runs today is a **mechanical, per-tick, single-symbol,
price-pattern reflex**: a signal builder reads one symbol's candles, emits a
`{side, entry, sl, tp}`, the RiskManager sizes it, it holds minutes-to-hours,
and it exits on a *price* barrier. The whole stack assumes this — the intent
multiplexer skips a strategy on any tick whose symbol isn't in its `symbols:`
set (`intent_multiplexer._collect_intents`), the backtester loops one OHLCV
DataFrame for one symbol (`src/backtest/backtester.py`), horizons are counted in
**bars** (`timeout_bars`, `triple_barrier.BarrierConfig.max_holding`,
`splitters.label_horizon`), and **no harness has a non-price exit condition**.

M28 inverts almost all of that:

| Axis | Today's strategies | M28 |
|---|---|---|
| Unit of decision | a price signal on one bar | a **`TradeThesis`** (a structured world-view bet) |
| Symbol | one pinned symbol per instance | **symbol-agnostic** — scans a universe, picks the expression |
| Horizon | minutes → hours (bars) | **days → weeks** (calendar clock) |
| Exit | price barrier (SL/TP/trail/time-in-bars) | price **and** **event-outcome** conditions |
| Inputs | OHLCV + a few ML heads | OHLCV + **macro series + news + scheduled events + (later) fundamentals** |
| Cadence | every tick (~seconds) | **slow** (hourly/daily scan), event-driven re-evaluation |
| Backtest | bar-loop, single symbol, price-only | **point-in-time, cross-sectional, event-conditioned** (new paradigm) |

So M28 is a **parallel spine** that reuses the bot's *plumbing* (data feeds, LLM
infra, execution seam, journal, conviction model, promotion discipline) but runs
on its own cadence and its own object model — exactly as the M22 pairs sleeve is
an isolated order path off `src/main.py` rather than a coordinator strategy.

## 1a. What "value" means here (the core, per operator decision (f))

"Value" is the spine, not a garnish. But for the **ETF/options universe** we
start with (decision (c)/(d)), single-stock DCF is not the primary lens —
**asset-class / cross-asset relative value** is, and it is almost entirely
computable from **free** data:

- **Equity value:** the equity risk premium — earnings yield (S&P 500
  earnings/price, from free index-fundamental releases) minus the real yield
  (TIPS, FRED `DFII10`). Cheap equities = high ERP; expensive = low/negative.
- **Duration/bond value:** real yields + the term premium + breakeven inflation
  (FRED: `DGS10`, `DFII10`, `T10YIE`) → is TLT/IEF cheap or rich vs inflation and
  the cycle?
- **Gold/silver value:** real rates + DXY + (for silver) the gold/silver ratio →
  is GLD/SLV cheap vs its macro drivers? (SLV/GDX are the seed options underlyings.)
- **Miners (GDX) value:** the metal price vs the miners' cost curve / GDX-vs-GLD
  ratio (an operating-leverage value read).
- **Credit/risk value:** HY & IG OAS (FRED) as the risk-appetite valuation gauge.

When the universe later extends to **single names**, "value" becomes true
company fundamentals (valuations, earnings surprises, balance-sheet quality) —
sourced free from **SEC EDGAR** (`companyfacts` XBRL + filing stream). So the P1
fundamental feed is designed in two layers: (1) **asset-class valuation metrics**
(FRED-derived, live from day one), and (2) an **SEC EDGAR company-fundamentals**
ingestion path (built in P1, exercised as the universe widens). Both feed the
`TradeThesis.macro_context`/valuation fields as point-in-time snapshots.

## 2. The core object — `TradeThesis`

The unit of work is not a signal; it is a **thesis**: a fully-traceable,
machine-readable record of *a bet on the world*. Making the "discretionary"
trade a structured object is what makes it auditable, backtestable, and gradable
(and is the precondition for ever letting an LLM propose one). Proposed shape:

```
TradeThesis:
  id, created_at, status ∈ {draft, active, invalidated, closed, expired}
  # --- the claim ---
  rationale: str                      # human-legible thesis statement
  world_view: {regime, macro_tilt, theme}   # e.g. "easing cycle → duration/gold bid"
  # --- the evidence (every input traceable to a source) ---
  signals: [ {source, claim, entity, direction, weight, evidence_url, ts, extractor_id} ]
  ta_context: { symbol_candidates:[...], setup, levels }   # the technical read
  macro_context: { series_snapshot (point-in-time), z_scores }
  # --- the bet ---
  instrument: { symbol, venue, express_as ∈ {spot, future, etf, debit_vertical, ...} }
  direction, entry_plan, target, invalidation   # invalidation is thesis-based, not a tight stop
  horizon_days, max_hold_until
  # --- the non-price machinery (the operator's core ask) ---
  watched_events: [ {event_id, kind, scheduled_for, expected, on_outcome:{if→action}} ]
      # action ∈ {enter, add, trim, exit, flip, hold, extend}
  # --- the score + provenance ---
  thesis_conviction: float ∈ [0,1]    # the M28 conviction (→ c_macro later)
  conviction_provenance: {...}        # which signals/weights produced it
  grade: {llm_grade, calibration_bin} # the meta-label (LLM grader)
```

`watched_events` is the mechanism for "events we watch and make decisions based
on their **results**": each event carries a decision rule keyed on its *realized
outcome*, not just its proximity. When an event resolves (§P2), the sleeve
applies the rule. Everything a thesis rests on is a row with a source link, so
every trade can be reconstructed and every input replayed at backtest time.

## 3. What we REUSE (do not rebuild)

The investigation found substantial existing plumbing. M28 wires into it rather
than reinventing:

| Ingredient | Where | Reuse |
|---|---|---|
| **Unstructured→signal pipeline** | `src/news/` (fetch → per-item `{sentiment, relevance, impact, freshness}` enrich → aggregate → veto → reductive event-aware sizing → `news_decisions.jsonl` soak → `/api/bot/news/recent`) | The full news spine. The keyword/regex enrichment (`news_normalizer.py`) is the piece we *upgrade* to LLM extraction. |
| **LLM plumbing (the precedent)** | `src/runtime/insights/` — timer-writer calls Anthropic/Gemini (+ deterministic `template` fallback), `insights_usage` cost/budget table, `insights_history` durable store, cache-only reader, "cite-an-id" structured-data prompting; plus `src/prop/screenshot_parse.py` (Claude vision) | Model the M28 "signal extractor" + "thesis grader" on this wholesale: writer/reader split, provider abstraction, **budget gating**, honest-null extraction. |
| **Macro data ingestion** | keyless FRED adapters (`ml/datasets/adapters/fred_macro.py` + `fred_corpus.py`, ~28 series: equities/VIX/credit-OAS/full-curve/breakevens/FX) + `ml/datasets/corpus_store.py` (standing catalog + point-in-time JSONL series) + `macro_features.py` (leakage-safe daily→intraday as-of joins) | The macro feed + point-in-time panel scaffold. Note: today it's **off-VM, ML-feature-only** — M28 needs a *live-path* macro read (new; see §4). |
| **Event-risk schema + math + sizing fold-in** | `config/economic_calendar.yaml` + `src/news/news_events.py` (`event_risk = impact × proximity`), folded into `news_influence.py` sizing | The *schema* and risk math. The `events:` list is **empty** (manual) and models *proximity*, not *outcome* — the feed + outcome store are the gap (§4). |
| **Multi-asset market data** | `src/runtime/market_data.py::fetch_candles` + `config/instruments.yaml` (crypto/index-&-commodity-futures/equities/ETFs/FX/bonds through one fetcher) | The TA substrate across every asset class. GLD/TLT/SPY/QQQ/IWM/SLV/USO give macro-expressive instruments already wired. |
| **Defined-risk options expression** | `src/units/accounts/options_overlay.py` (+ `options_selector`/`sizing`/`alpaca_options_exec`/`lifecycle`); an account declares `options: {express_as: debit_vertical}` and the overlay converts a directional package into a spread; geometry persisted to `notes.options`, expiry/assignment reconciled | Weeks-horizon asymmetric macro bets *want* defined risk. The strategy stays a pure directional generator; the account expresses it. Adding a structure = extend `options_selector` + the `express_as` allowlist. |
| **Isolated custom order path** | `src/units/strategies/pairs_executor.py::run_pairs_tick` — its own `config/pairs.yaml` + `execution:` gate, open-state reconstructed from the journal, placed via the shared `execute_pkg` seam with `qty_override`, `monitor()` returns `None` so the executor owns the exit; called once/tick from `src/main.py` | **The template for M28's order path.** M28 gets `run_macro_thesis_tick(settings)` on a *slow* cadence, its own config + gate, its own state store, placement through `execute_pkg`. |
| **The "master model" (conviction)** | `src/runtime/conviction.py` (`DEFAULT_CONVICTION_WEIGHTS`, renormalize-over-present-inputs) + `conviction_inputs.py::build_conviction_inputs` + the gated `CONVICTION_SIZING_MODE` apply path | Genuinely additive extension point for a future `c_macro` (§7). Isolated-sleeve-first means M28 uses its **own** conviction internally at first; `c_macro`-into-the-global-blend is the deferred overlay. |
| **Testing discipline to inherit** | purged walk-forward + embargo (`ml/experiments/splitters.py`), the `oos_edge` gate that "never loosens", M25 parity-first (`ml/promotion/live_parity.py`, "mechanics live, edge offline"), net-R cost-aware labels (M24), the candidate→shadow→advisory ladder | The methodology is rigorous and reusable. The gap is that it's all bar/price/single-symbol — M28 extends the *shape*, not the *rigor*. |
| **Off-the-shelf event/sentiment source** | The **Bigdata.com MCP** (`bigdata_events_calendar`, `bigdata_search`, `bigdata_sentiment_tearsheet`) — the `economic_calendar.yaml` header *already names it* as the intended future event feed | A ready accelerator for the event calendar + thematic/unstructured content, cutting the initial scraping burden. |

## 4. Genuine new infrastructure (the build)

| New piece | Why it's new | Notes |
|---|---|---|
| **LLM signal extractor** | Current news enrichment is keyword+regex (`news_normalizer.py`), not LLM. | A cheap/small-model pass turning article/filing/transcript text → `{claim, entity, direction, confidence, evidence_url, event_ref}`. Slots into the M13 writer/reader + budget pattern; honest-null. |
| **Economic-calendar FEED** | `economic_calendar.yaml::events` is empty + manual. | A scheduled job populating scheduled events from the **free government release calendars** (BLS CPI/employment, BEA GDP/PCE, Treasury auctions, the Fed/FOMC schedule) + earnings dates (free). Source-agnostic loader already exists. No paid feed. |
| **Event-OUTCOME tracking store** | Existing infra models event *risk/proximity*, never *outcome → decision*. | A new durable table: watched events, scheduled time, expected vs **realized** outcome (actual-vs-consensus for prints; the filing/ruling content for discrete events), resolution source. The heart of the "non-price trade elements." |
| **Live macro read on the trading path** | All macro is off-VM/ML-feature-only. | A cached, point-in-time-correct macro snapshot readable by the live sleeve (small on-VM cache refreshed by a timer; reuse the keyless FRED adapters). |
| **Fundamental / valuation feed — P1 CORE (decision (f))** | None exists anywhere in the repo. | **The spine of the strategy, built in P1, not deferred.** Two layers (§1a): (1) asset-class valuation metrics (ERP, real yields, breakevens, OAS, metal-ratio) — FRED-derived, free, live from day one; (2) an **SEC EDGAR** company-fundamentals ingestion path (free `companyfacts` XBRL + filing stream) for when the universe widens to single names. All point-in-time. |
| **General ingestion / light scraping layer** | Only RSS + NewsAPI + FRED CSV today. | Free-first: FRED CSV, SEC EDGAR API, government release schedules, RSS. Light scraping only where no free API exists. Bigdata.com MCP is an optional accelerator, **not a dependency**. |
| **The weeks-horizon, event-conditioned, cross-sectional BACKTEST paradigm** | Every harness is bar-based, single-symbol, price-only, intraday. | The hard part (§P4). Point-in-time store + as-of joins (no revised-data/lookahead leakage), a **calendar-clock** horizon model with carry/financing costs, non-price exit/leg conditions, a **universe-scan → candidate-rank** stage, and low-n calibration. |
| **Universe scanner** | No screener exists; "multi-symbol" today = N cloned single-symbol instances + the per-symbol tick loop. The M18 cross-market *selector* tested **negative** on the mechanical strategies. | M28's `P_win` comes from a *fundamentally different* (macro/value) source than M18's confidence proxy, so a fresh scan→rank is worth building — but we inherit M18's honesty (prove selection edge sizing-normalized, not via capital concentration). |

## 5. The LLM ladder — stepping stones to LLM-proposed theses

Per operator decision (b): the destination is an LLM proposing full theses; the
route builds accountability first. Each rung must clear its gate before the next:

- **S1 — Extractor-only (traceable signals).** LLM converts unstructured text →
  structured `signals[]` rows with source links. Deterministic rules combine
  signals into a thesis. *Gate:* extraction quality measured against a labeled
  set; every signal reproducible. The LLM never picks a trade yet.
- **S2 — Rules-formed theses + LLM grader (meta-label).** Explicit, auditable
  logic forms candidate theses from S1 signals; an **LLM grader** scores thesis
  quality (a meta-label, in the M23 spirit) and calibrates the conviction.
  *Gate:* the grader's grade is calibrated vs realized thesis outcomes offline.
- **S3 — LLM proposes full theses end-to-end.** An LLM agent reads the
  point-in-time world state and proposes complete theses (instrument, direction,
  watched events, targets), which the *same* deterministic risk/expression/gate
  layer filters and sizes. *Gate:* S3 theses beat S2 rules-formed theses on the
  new backtest paradigm, net-of-cost, out-of-sample — and remain fully traceable
  (the proposal's cited evidence is logged like any other signal).

Traceability is the invariant across all three: an LLM-proposed thesis is
accepted only with its evidence rows attached, so it is as replayable and
gradable as a rules-formed one. This is how we "aim for 2 via 3 and 1."

## 6. Phased plan (observe → advise → gate → apply)

Each phase ships **observe-only/shadow first** (house discipline), paper-first,
with edge proven offline (M25 reframe) — no live behavior change before P4's
paradigm proves the sleeve out-of-sample.

- **P0 — Scope + `TradeThesis` schema + integration design (Tier-1).** Finalize
  the object model (§2), the event-outcome store schema, and the master-model
  integration contract (§7). Deliverable: this doc + a schema doc. No code on any
  live path.
- **P1 — Data & signal ingestion (incl. the fundamental/value core) + LLM
  extractor S1 (Tier-1 → Tier-2 deploy).** All **free-sourced** (decision (e)),
  all writing traceable, point-in-time `signals[]`/valuation rows to a store.
  Stand up: (i) the live macro cache (keyless FRED → on-VM point-in-time
  snapshot); (ii) **the fundamental/valuation feed — the P1 CORE (decision (f))**:
  layer-1 asset-class valuation metrics (ERP, real yields, breakevens, OAS,
  gold/silver ratio — FRED-derived) live from day one, plus the layer-2 **SEC
  EDGAR** `companyfacts`/filing ingestion path stood up for universe widening
  (§1a); (iii) the economic-calendar feed from the free government release
  schedules (BLS/BEA/Treasury/Fed) + earnings dates; (iv) the **LLM signal
  extractor** (M13 writer/reader + budget) turning free news/filings text →
  structured signals; (v) richer thematic RSS news beyond the scalp-veto use.
  Observe-only.
- **P2 — Event store + non-price decision engine (Tier-2).** The durable
  event-outcome table + a resolver that ingests realized outcomes and, for any
  active thesis, applies its `on_outcome` rule. Observe-only soak first (log the
  *would-be* action).
- **P3 — Thesis-generation engine / scanner (Tier-2, isolated path, soak).** The
  slow-cadence `run_macro_thesis_tick(settings)` off `src/main.py` (pairs-sleeve
  template): scan the universe, align macro+news+TA, form theses (S1 rules; then
  S2), score `thesis_conviction`, emit **observe-only thesis records** +
  `/api/bot/macro-thesis/soak`. Its own `config/macro_theses.yaml` +
  `execution: shadow|live` gate.
- **P4 — The backtest paradigm (Tier-1, the decisive gate).** Point-in-time
  replay: reconstruct macro/news/event/price state *as-of* each historical date
  (no lookahead, no revised macro), form theses on that state, hold on a calendar
  clock with carry costs, exit on price **and** event-outcome conditions, and
  score net-of-cost. Low-n → lean on M24 net-R + **calibration** (does
  `thesis_conviction` predict realized hit-rate?) rather than high-n
  significance. **Nothing graduates to live until a thesis engine beats a naive
  baseline here, out-of-sample.**
- **P5 — Expression & risk (Tier-2 → Tier-3).** Express theses as
  **defined-risk options on `alpaca_options_paper`** (decision (c)) over a
  **narrow** seed universe (decision (d)) — the wired SLV/GDX options underlyings
  plus a small set of macro-expressive ETFs (GLD/TLT/SPY/IWM) as they gain
  options support. Weeks-horizon risk accounting: max-loss is the option debit
  (defined by construction), exit on **thesis-invalidation or event-outcome**,
  *not* tight price stops; roll/extend logic for multi-week holds. Paper only
  until P4 proves the sleeve.
- **P6 — Feed the master model (Tier-3, the "overlay later" step).** Promote the
  thesis engine's world-state read into a global conviction overlay via `c_macro`
  (§7). This is deferred until the isolated sleeve has earned trust.
- **S2→S3 LLM graduation** threads through P3/P4 once the scaffolding + backtest
  can hold an LLM-proposed thesis accountable.

## 7. How M28 feeds the master model

The "master model" is the **unified-confidence conviction framework**
(`docs/unified-confidence-risk-DESIGN.md`): `conviction = news_mult ×
Σ wᵢ·cᵢ` over `{c_strat, c_setup, c_wr, c_reg}`, declared in
`conviction.py::DEFAULT_CONVICTION_WEIGHTS`, produced by
`conviction_inputs.build_conviction_inputs`, renormalized over present inputs (so
a new key is additive), and applied via the gated `CONVICTION_SIZING_MODE`.

Per **isolated-sleeve-first**, integration is two-staged:

1. **Sleeve-local (P3–P5).** M28 computes its **own** `thesis_conviction` and
   sizes its **own** book through its own path (it can reuse `compute_conviction`
   with a sleeve-local weight set, or size directly from `thesis_conviction`).
   It does **not** touch the shared blend yet — blast radius stays inside the
   sleeve. This is the whole point of "prove it standalone first."
2. **Global overlay (P6, Tier-3).** Once trusted, expose the world-state read as
   **`c_macro`** to the shared model: add `"c_macro"` to
   `DEFAULT_CONVICTION_WEIGHTS`, produce it in `build_conviction_inputs` (either
   as a direct discretionary input or, if it ships as an ML head, extend
   `classify_head` to route `macro-*`→`c_macro` and add it to the v2 stacker's
   `conviction_meta._LENS_COLUMNS`). It then flows through the *existing* gated
   `CONVICTION_SIZING_MODE` apply path — observe-only via the flagless annotator
   soak first — tilting the whole book toward/away from risk with the macro read.
   Because the framework renormalizes over present inputs, this strands nothing.

## 8. Risk, discipline, and correctness invariants

- **Point-in-time integrity is the #1 correctness rule.** The signature failure
  mode of macro/value backtesting is training/testing on *revised* data or
  *future* news. Every feed stores as-of snapshots; every backtest join is a
  strict past-only as-of join (extend the `macro_features.py` one-day-lag
  discipline into a first-class point-in-time store).
- **Observe-only / shadow first, paper before real.** Every layer soaks before
  it acts (news layer, pairs sleeve, conviction — all did this). Real-money is a
  late, separate Tier-3 gate gated on P4.
- **LLM cost is budgeted.** Reuse `insights_usage` budget gating; the extractor +
  grader run on cheap models on a slow cadence, not per-tick.
- **Kill-switches + the two gates.** The sleeve carries an `execution:
  shadow|live` gate in its own config (pairs-sleeve pattern) and honors the
  account `mode`. No third `*_ENABLED` gate (Prime Directive).
- **Low-n honesty.** Weeks-horizon → few trades → we validate by **calibration
  and net-R**, never by claiming significance from a handful of wins.

## 9. Non-goals / honesty

- **Not an M18 redux.** The M18 cross-market *selector* tested definitively
  negative (OOS AUC ≈ 0.51; apparent edge was capital concentration, not
  selection). M28 is worth building anyway because its `P_win` comes from a
  *different* information source (macro/value/event), **but** it inherits M18's
  honesty bar: any selection/ranking edge must be shown sizing-normalized and
  out-of-sample, not via leverage.
- **Not a live launch on theory.** `hold`-vs-`reverse`, the allocator, the SSL
  encoder, the "reads-everything" corpus thesis — several ambitious ideas here
  tested negative before. The daily-macro "reads-everything" thesis failed *four
  times* for *intraday* heads; M28 is the natural home to retest it because it
  operates at the daily+ cadence those series actually match — but honestly, as a
  hypothesis to be proven in P4, not assumed.
- **Not a replacement for the mechanical book.** It's a diversifying,
  low-correlation sleeve that (eventually) also provides a macro overlay.

## 10. Resolved scoping decisions (operator, 2026-07-22)

All four framing questions are answered; P1 is unblocked.

1. **Soak venue → `alpaca_options_paper`.** First paper theses express as
   defined-risk options on the wired Alpaca options paper stack — the natural fit
   for weeks-horizon asymmetric bets (known max loss over a multi-week hold).
2. **Universe → start narrow.** A small curated seed set (the SLV/GDX options
   underlyings + a few macro-expressive ETFs), not a broad screener day one; the
   scanner generalizes once the pipeline is proven.
3. **Data → free only.** FRED, SEC EDGAR, free government release calendars, RSS,
   free market data; light scraping only where no free API exists. Bigdata.com
   MCP is an optional accelerator, not a dependency. LLM extractor/grader run on
   cheap models on a slow cadence under the M13 budget mechanism.
4. **Fundamentals → P1 core.** "Value" is the spine; the fundamental/valuation
   feed is a first-class P1 deliverable (§1a), not a deferred phase.

## 11. Anchors

- **Milestone:** M28 (M27 is the current highest). Add to `ROADMAP.md` Milestone
  Roadmap + the "Next — prioritized work plan" as the next major
  strategy-development program.
- **Backlog anchor:** `MB-20260722-M28-MACRO-VALUE-SPECULATION` (ml-review
  backlog) — P0 = this design; P1 next.
- **Composes with:** M9 (news spine), M13 (LLM plumbing + budget), M22 (isolated
  order path), M23 (meta-labeling → the LLM grader), M24 (net-R labels for
  low-n validation), M25 (promotion/parity discipline), M18 (the cross-market
  honesty bar), the unified-confidence master model (the `c_macro` overlay), and
  the options overlay (defined-risk expression).
- **Code touch-points when P1 builds:** a new `src/units/strategies/macro_thesis/`
  package (decision core + `run_macro_thesis_tick`), a new `config/macro_theses.yaml`
  (+ `execution:` gate), an event-outcome + signals store (new tables in
  `trade_journal.db` via `src/units/db/`), a **free fundamental/valuation feed**
  (asset-class valuation metrics reusing `ml/datasets/adapters/fred_corpus.py`
  + a new **SEC EDGAR** `companyfacts`/filing adapter, both point-in-time via the
  `corpus_store.py` panel scaffold), an economic-calendar populator over the free
  government release schedules, an LLM extractor under `src/runtime/` modeled on
  `src/runtime/insights/`, a live macro cache, a new point-in-time backtest
  harness under `scripts/research/m28/` + `src/backtest/`, and (P6) the `c_macro`
  additions to `conviction.py` / `conviction_inputs.py`.
