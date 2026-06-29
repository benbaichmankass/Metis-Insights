# Design Proposal — Portfolio-level AI Capital Allocator

> **Status:** RESEARCH / DESIGN PROPOSAL — read-only analysis. **No code, config, or live
> state changed by this pass.** Every option that touches the intent/allocation/order path,
> sizing, model promotion, or risk budgeting is **Tier-3 — PROPOSE ONLY**, gated on backtest
> validation + explicit operator approval per `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers
> and the Prime Directive (no auto-off, no default-off `*_ENABLED` gate in front of a required
> capability; observe-only until graduated).
>
> **Origin:** the 2026-06-29 optimization investigation lineage. This is the **portfolio /
> N-way** companion to the three single-decision units already produced (merged #4994):
> Unit A — pairwise flip EV (`pnl-optimal-conflict-resolution-DESIGN.md`); Unit B —
> RiskManager-only confidence sizing (`position-sizing-confidence-DESIGN.md`); Unit C — prop
> exit banking (`prop-dynamic-exits-faster-banking-DESIGN.md`). Those optimize **one decision at
> a time**; this proposes the layer that optimizes **the whole opportunity set at once**.

---

## 0. The problem (framed against the current code)

Today the pipeline is **per-strategy / per-account independent**. Each tick:

1. The intent multiplexer runs every enabled strategy, collects their intents
   (`src/runtime/intent_multiplexer.py::_collect_intents`, `:333`), debounces same-bar repeats
   (`_debounce_emissions`, `:417`), and **collapses them to ONE `DesiredPosition` per symbol** via
   `aggregate_intents` (called at `intent_multiplexer.py:616`; defined `src/runtime/intents.py`).
   The pipeline adapter only ever sees that single collapsed result —
   `signal = builder(settings)` returns **one** signal per tick (`src/runtime/pipeline.py:439`).
2. That one signal becomes one order package and is fanned out to every eligible account in
   `Coordinator.multi_account_execute` (`src/core/coordinator.py:780`), where a **per-account**
   `RiskManager.position_size` (`src/units/accounts/risk.py:620`) sizes each survivor **on its own**,
   independent of every other pending trade this tick.

There is **no global step** that compares the full opportunity set and asks: *"of everything
actionable right now — across all strategies, symbols, and accounts — which trades deserve the
limited capital / risk budget, ranked by cost-aware expected value, and which should be skipped
because a better trade exists or because they pile correlated risk on an existing book?"*

Capital is allocated **first-come and per-cell**, not by comparative, cost-aware EV across all
candidates together. The observed cost of this is the same family the three units each chip at
from one angle: a weak trade holding a slot a stronger one wants (Unit A's flip case), a
low-conviction trade sized the same as a high-conviction one (Unit B), capital parked on a slow
trade while faster banking sits idle (Unit C). The allocator is the layer that sees all of those
trade-offs **simultaneously**.

## 1. The decisive architectural finding — the allocator seam already exists

This is the most important fact for scoping the build. A **centralized-allocator framework was
already built** (M11 refactor sprints `S-REFACTOR-S1..S10`) and is sitting dormant behind a
feature flag:

- **`AllocatorInterface.allocate(signals: Sequence[SignalPackage], portfolio_state: PortfolioState)
  -> list[OrderPackage]`** (`src/core/allocator.py:25-45`) — this is **exactly the N-way signature
  an EV allocator needs**: a *batch* of candidate signals in, a *selected + sized* batch out, with
  a portfolio snapshot for context.
- The only implementation wired is **`PassthroughAllocator`** (`src/core/allocator.py:55-100`): an
  identity sizer that replicates `risk.py`'s per-strategy formula with **"No cross-strategy netting,
  no portfolio-level exposure caps. Those are introduced in later sprints"** (`allocator.py:62-64`,
  verbatim). It loops candidates and sizes each independently — no ranking, no selection, no budget.
- **`PortfolioState`** (`src/core/portfolio_state.py:19-57`) carries `balance`,
  `risk_pct_by_strategy`, and `net_positions` (signed net qty per symbol from
  `net_positions_by_symbol()`). It is the typed snapshot the allocator reasons over.
- **`SignalPackage`** (`src/core/signal_contract.py:15-57`) already has a `source_context: dict`
  field explicitly **"for allocator decisions (regime, ML advisory, etc.)"** (`:29-30`) — a ready
  home for per-candidate EV inputs — plus `is_actionable` and `sl_distance` helpers.
- It is wired into the live pipeline behind **`CENTRALIZED_ALLOCATOR`** (default false,
  `src/runtime/runtime_flags.py::_centralized_allocator_enabled`): `pipeline.py:704` →
  `coord.build_order_packages([_sig_pkg], {...})` (`coordinator.py:194`, builds the `PortfolioState`
  + merges live net positions) → `multi_account_execute_typed` (`coordinator.py:289`). Crucially the
  allocator-computed qty is stored **`meta['allocator_qty']` for audit only — the RiskManager
  remains the single live-sizing authority** (`coordinator.py:296-301`).

**So two things are true and they define the entire build:**

1. The seam, the typed contracts, the portfolio snapshot, the feature flag, and the audit-only
   wiring **already exist**. We are not inventing a layer — we are writing a smarter
   `AllocatorInterface` implementation and **feeding it the full candidate batch instead of one
   signal at a time**.
2. The framework is fed **`[_sig_pkg]`** — a single already-collapsed signal in a list
   (`pipeline.py:716`). The N-way batch the allocator is designed to receive **is never assembled**;
   the multiplexer throws the candidate set away (collapses to one) before the allocator is called.
   **Exposing that candidate set is the first concrete piece of work** (§5.1).

## 2. Inventory — decision-capable ML/infra we already have (and its limits for allocation)

| Component | File (cited) | What it produces | N-way? | Cost-aware? | Live? | Reuse for allocation |
|---|---|---|---|---|---|---|
| **Allocator seam** | `core/allocator.py:25-100`, `core/portfolio_state.py`, `core/signal_contract.py` | `PassthroughAllocator`: identity per-cell sizing | Signature **yes**, impl **no** | No | Behind `CENTRALIZED_ALLOCATOR` (off) | **The host.** Write `EVAllocator(AllocatorInterface)`; extend `PortfolioState`. |
| **Conviction lens** | `runtime/conviction.py:70-100` | Calibrated `P(win)` blend `c_strat/c_setup/c_wr/c_reg` → `conviction ∈ [0,1]` | No (per-trade) | No | Observe-only, stamped on `meta` | **Closest per-candidate quality score.** Docstring already names *"competing-trade arbitration"* as its purpose (`conviction.py:4-6`). The EV scorer's `P(win)` input. |
| **Conviction sizing** | `runtime/conviction_sizing.py:133-415` | Would-be qty = `conviction × 2%-risk-budget`, with `throttle`/`margin_cap` | No | No | `CONVICTION_SIZING_MODE` off/annotate/apply | The per-trade sizing analogue; the allocator generalizes its throttle/margin logic to N trades. |
| **Pairwise flip EV** | `runtime/flip_ev.py` (Unit A) | EV of 1 held vs 1 new, 4-fill fee-aware | **Pairwise only** | **Yes (fees)** | Proposed (selective flip) | The 2-body case of the allocator's N-body EV. Same fee inequality, generalized. |
| **Offline EV + survival** | `prop/montecarlo.py::run_ev_montecarlo`, `scripts/prop/account_compat_matrix.py` | Block-bootstrap R-ledger → mean net-$, `p_profitable`, `p_breach` per (strategy, account) | Per-cell | **Yes (fees/swap/withdrawal)** | **Offline only** (~5k paths) | The **historical net-expectancy per cell** input to the EV scorer + the prop-account graduation gate. Too heavy to run live per candidate. |
| **Advisory regime heads** | `runtime/regime_bar_scoring.py`, `runtime/regime/ml_vol_verdict.py:355-395` | Per-SYMBOL `P(volatile)` → calm/volatile label | n/a | No | Advisory (live gate) | **Gate/filter, not a ranker.** A feature into the EV scorer (regime-conditional expectancy), and a hard pre-filter (already drops OFF-cell candidates). |
| **`order_packages.model_scores`** | `units/db/database.py:228` | `{model_id:{stage,score}}` captured at signal time | n/a | No | Logged live | Decision-time **features** for the learned ranker. |

**Bottom line:** we have a per-trade quality score (conviction), a cost-aware EV engine (offline,
per-cell), and a pairwise EV (Unit A) — but **nothing today ranks multiple simultaneous candidates
against each other, and nothing computes cost-aware EV at live decision time.** The allocator is
the missing N-way, live, cost-aware composition of pieces we mostly already have.

## 3. The gaps (what a real allocator needs that does not exist)

1. **No N-way, capital-constrained selector.** No knapsack / subset selection under free margin +
   per-account risk-cap + daily-loss budget. `PassthroughAllocator` sizes each candidate
   independently against full balance (`allocator.py:79-98`); every `RiskManager.position_size` call
   is independent of every other pending trade (`risk.py:620-728`). Confirmed: no cross-trade budget
   anywhere.
2. **No correlation / covariance-aware risk budgeting.** Nothing live computes correlation or
   covariance between symbols/positions. `config/cross_asset.yaml` peer features feed **shadow regime
   heads only** (`runtime/cross_asset_live.py`, observe-only) — they are not a portfolio risk input.
   Two highly-correlated longs are sized as two independent 1.5%-risk trades = ~3% correlated risk
   the caps never see as one exposure.
3. **Fees / funding / swap are not a live per-decision input.** They are modeled in **backtests
   only** (`FEE_BPS_ROUNDTRIP=7.5`, `scripts/backtest_system.py:97`; taker 0.055% / maker 0.02%,
   `src/backtest/backtester.py:30`) and in the offline prop Monte-Carlo (swap `0.033%/day`,
   `config/prop_rulesets/breakout.yaml`). At live decision time **no cost is charged** — the margin
   pre-flight's `0.9` buffer (`risk.py:76`) absorbs them implicitly but the allocator can't *rank* on
   them. A high-fee scalp into a small TP and a low-fee swing look equally good to the live path.
4. **No learned head-to-head ranker.** There is no model that outputs *"expected net-R given
   decision-time features"* to rank competing order packages. Conviction is a hand-weighted blend,
   not a trained ranker; the regime heads are gates, not EV rankers.

## 4. Honest "what we have vs what we must build / collect"

🟢 have · 🟡 partial / needs adaptation · 🔴 must build / collect. **A 🔴 in the "blocks" column means
that gap blocks training/▶live of the row that depends on it — do not propose the dependent model
before closing it.**

| Capability | Status | What exists | What's missing | Blocks |
|---|---|---|---|---|
| N-way allocator seam (batch in → selected batch out) | 🟢 | `AllocatorInterface` + `build_order_packages` + `multi_account_execute_typed` + `CENTRALIZED_ALLOCATOR` flag | A non-identity implementation; the batch is never assembled (fed `[one_sig]`) | — |
| Full candidate set per tick | 🟡 | `_collect_intents` gathers all strategy intents before collapse | The set is collapsed to one `DesiredPosition` and discarded before the allocator | Phase 0 soak |
| Per-candidate quality score | 🟡 | Conviction lens (calibrated `P(win)`) | Not exposed for ranking; observe-only | EV scorer |
| Historical net expectancy per cell | 🟢 | `run_ev_montecarlo` + closed-trade R-ledger in `trade_journal.db` | Offline-only; needs a cheap cached read at decision time | EV scorer (live use) |
| Cost-aware EV at decision time | 🔴 | fee/funding constants in backtest + prop rules | No live fee/funding/swap lookup; **not logged per trade** | EV scorer ranking on cost |
| Free margin / risk budget per account | 🟡 | Bybit live `available_usd`; daily-loss + intraday-DD gates (`risk.py:668-728, 491-519`); `daily_risk_state` | No *budget remaining* surfaced to a pre-execution selector; not in `PortfolioState` | Capital allocator |
| Correlation / covariance inputs | 🔴 | `cross_asset.yaml` peer lists (shadow-head features only) | No live correlation matrix; not logged at decision time | Correlation-aware budgeting |
| Capital-constrained subset selection | 🔴 | — | Greedy EV/risk → constrained optimization | Capital allocator |
| Training labels (realized net-R per order pkg) | 🟡 | `order_pkg → trade` join; `realized_R = pnl / (\|entry−sl\|·qty)` derivable (`database.py:119-148`) | **Per-trade fees/funding not separately logged** → labels inflated ~10–20 bps (perp multi-day funding worse); approximable with a fixed cost model for a first pass | Learned ranker (clean labels) |
| Decision-time features | 🟢 | `order_packages` (`confidence`, `model_scores`, `signal_logic`, `meta`); `account_context_snapshots` (equity, daily PnL, DD%, open-trades-count); `shadow_predictions.jsonl` (`feature_row`) | Per-symbol *exposure* (only a raw open-trade count today); cross-candidate context | Learned ranker (richer features) |
| Learned net-R ranker | 🔴 | — | New model family on the candidate→shadow→advisory ladder | ▶live influence |

**The single biggest data gap that blocks training the clean learned ranker is per-trade
transaction cost.** Realized PnL in `trades.pnl` is broker-net for Bybit but the *cost component*
is not separable, and multi-day perpetual **funding** may not be fully captured in the local-compute
PnL path — so a ranker can neither learn cost as a discriminable feature nor get an unbiased net-R
label. This is closeable by a **Tier-1 data-capture change** (log `fee_*` / `funding_*` per trade on
the close path; no order-flow mutation) — and it must land in Phase 0 so the substrate is clean
before the Phase 3 model is trained. Until then, Phases 0–2 use a **fixed cost model**
(round-trip taker + per-cell swap), which is sound for a rules-based scorer and explicitly flagged
as an approximation.

## 5. Proposed build (phased, shadow-first, graduation-gated)

The allocator is the **existing seam**, made smart, and fed the **full batch**. Four composable
pieces; each ships observe-only first.

### 5.1 Assemble + expose the candidate batch (the prerequisite)

The full candidate set already exists transiently inside the multiplexer (`_collect_intents`).
The work is to **surface it as a `list[SignalPackage]`** at the allocator boundary instead of
collapsing to one. Two options, in preference order:

- **(a) Multiplexer emits the batch [recommended].** Have `multiplexed_intent_signal_builder`
  additionally expose the pre-aggregation candidate list (as `SignalPackage`s, one per
  strategy×symbol intent across all eligible accounts) on the signal it already returns. The legacy
  collapse stays the live path; the batch rides alongside for the allocator/soak to consume.
- **(b) Coordinator re-derives the batch.** Less clean — the coordinator would re-run the
  per-strategy builders. Prefer (a); the intents are already in hand at `_collect_intents`.

**Hook point:** the allocator observes the batch at `build_order_packages` (`coordinator.py:194`),
exactly where `PortfolioState` is assembled — after intent collection, **before** any per-account
execution. This is the one place with the full opportunity set **and** the portfolio snapshot in
the same scope.

### 5.2 Cost-aware EV scorer per candidate (rules-based first, learned later)

For each candidate `SignalPackage`, compute an **expected net-R** (or net-$) and stamp it on
`source_context` (`signal_contract.py:29`):

```
EV_net(candidate) = P_win · R_target − (1 − P_win) · R_stop
                    − roundtrip_fee_R(symbol, qty)
                    − funding_R(symbol, expected_hold)        # perps / prop swap
```

- `P_win` ← the **conviction lens** (`conviction.py`), already a calibrated `[0,1]` `P(win)`.
- `R_target / R_stop` ← from the candidate's TP/SL geometry (`sl_distance` already on the contract).
- `roundtrip_fee_R / funding_R` ← **fixed cost model** in Phases 1–2 (taker bps from
  `instruments.yaml` + per-cell swap from the prop ruleset), graduating to the **logged** per-trade
  costs once §Phase-0 capture lands. The per-cell **historical net expectancy** from
  `run_ev_montecarlo` (cached, refreshed offline) anchors/calibrates the EV so it reflects realized
  cost-aware performance, not just decision-time geometry.

This is a pure function (`src/runtime/allocator_ev.py`, proposed) — testable, fail-permissive (a
scoring error drops the candidate to a neutral score, never strands the legacy path).

### 5.3 Capital / risk allocator — select the best subset

Given scored candidates + an enriched `PortfolioState`, select the EV-optimal subset that respects
every existing cap:

- **Budget inputs (extend `PortfolioState`):** per-account free margin (Bybit live `available_usd`,
  else `balance×0.9×leverage` per `risk.py:702-715`), **daily-loss budget remaining**
  (`effective_daily_loss_usd − daily_pnl`, `risk.py:668-689`), intraday-DD headroom
  (`risk.py:515-517`), and a max-concurrent-positions cap (new, per-account).
- **Selector, graduated complexity:**
  1. **Greedy EV-per-unit-risk** — sort candidates by `EV_net / risk_used`, take greedily until a
     budget binds. Transparent, O(n log n), the natural first selector.
  2. **Correlation-aware risk budgeting** — discount the marginal risk contribution of a candidate
     by its correlation to the existing book + already-selected candidates (a covariance-adjusted
     risk term), so two correlated longs don't both get full budget. Needs the §6 correlation input.
  3. **Constrained optimization** (knapsack / small QP) only if greedy+correlation proves
     insufficient in backtest — most of the gain is usually in steps 1–2.
- **Hard invariants (Prime Directive):** the allocator **only ever reduces or skips** — it never
  enlarges a trade beyond what the per-account `RiskManager` would allow, and the **RiskManager
  remains the final per-account sizing authority** (`coordinator.py:296-301`). The allocator's job
  is *which trades and at what fraction of their risk-budget*, then the RiskManager sizes each
  survivor as today. No new order path; no capital gate that can strand a strategy when the allocator
  is off.

### 5.4 The learned ranker (Phase 3)

Replace/augment the rules-based `P_win`/EV with a trained **"expected net-R given decision-time
features"** model (§6), riding the **candidate → shadow → advisory** ladder like every other model:
shadow-logs its ranking, accrues a track record, and only influences the order package at
**advisory** stage after operator-approved promotion. The rules EV stays as the fail-permissive
fallback and the shadow baseline the learned ranker must beat.

## 6. Datasets / features / targets / models for the learned ranker

- **Target (label):** realized **net-R** per order package = `pnl / (|entry − sl| · qty)` minus
  logged round-trip fees + funding (the §Phase-0 capture). Available via the
  `order_packages → trades` join (`database.py:215-234, 119-148`).
- **Features (decision-time, already logged):** `confidence`, `model_scores`
  (`{model_id:{stage,score}}`), `meta` (setup_type / killzone / bias), conviction inputs
  (`c_strat/c_setup/c_wr/c_reg`), the advisory regime label (`ml_vol_regime_for_symbol`), and the
  **`account_context_snapshots`** row (equity, daily PnL, daily DD%, open-trades-count) keyed by
  `(order_package_id, account_id)` (`context_snapshot.py:35-52`).
- **Features to add (Phase 0/3):** per-symbol / per-asset-class **exposure at decision time** (today
  only a raw `open_trades_count`), and a **correlation/covariance** feature (rolling cross-pair
  correlation over the candidate set + open book). The correlation feature doubles as the §5.3
  risk-budget input — build it once, log it, reuse it.
- **Model family:** a gradient-boosted regressor/ranker (LightGBM — the fleet's existing recipe,
  e.g. the regime heads) trained per asset-class or globally with asset-class as a feature; manifest
  under `ml/configs/`, dataset family built on the trainer VM, scored through `ShadowPredictor` so it
  logs to `shadow_predictions.jsonl` exactly like the regime heads. **It is a *ranker/regressor*, a
  new task family** (the current heads are vol classifiers) — a genuinely new model, not a re-train.
- **Sample-size reality:** ~500–2000 live closed real-money trades exist today; per-strategy that is
  thin. Pool across strategies (with strategy as a feature) and **augment with backtest-labeled
  decisions** for the first model; treat live shadow accrual as the calibration/validation stream.

## 7. Shadow-soak plan (observe-only, measures regret)

Mirror the `conviction_sizing` / `exit_ladder` soaks exactly — a new
`runtime_logs/allocator_soak.jsonl` (+ a `/api/bot/allocator/soak` read surface and a diag
`log_file` name), written each tick the candidate batch is non-trivial:

- **Log:** the full scored candidate set, what the allocator **would** select + size, vs what the
  system **actually** executed (the legacy per-cell path), plus the binding budget per account.
- **Regret metric:** the core question — *did we leave EV on the table?* Define
  `regret = EV_net(allocator-chosen subset) − EV_net(actually-executed subset)`, and after close,
  the **realized** counterpart `realized_R(allocator subset) − realized_R(executed)` over matched
  ticks. A persistently positive realized regret is the evidence the allocator helps; near-zero
  means the per-cell path was already near-optimal and the allocator isn't worth the order-path risk.
- **Coverage:** log how often a budget actually binds (if margin/risk caps rarely bind, the
  allocator's selection rarely differs and the win is small — surface that honestly rather than
  shipping a layer that does nothing).

No order influence at any point in the soak — `allocator_qty` stays audit-only (`coordinator.py:296`)
until §8 graduation.

## 8. Phased roadmap + graduation gates

| Phase | Scope | Tier | Graduation gate |
|---|---|---|---|
| **P0 — Substrate + soak** | (a) Tier-1 per-trade **cost capture** (`fee_*`/`funding_*` on the close path); (b) expose the candidate batch (§5.1); (c) `allocator_soak.jsonl` + regret metric (§7), observe-only | T1 (capture/soak) | Soak accruing; regret computable; cost columns populating. No order influence. |
| **P1 — Rules EV scorer** | `allocator_ev.py` cost-aware `EV_net` per candidate, stamped on `source_context`; still soak-only | T1 (observe) | EV scores logged for every candidate; sanity vs realized R. |
| **P2 — Greedy selector (shadow)** | `EVAllocator(AllocatorInterface)`: greedy EV/risk subset under enriched `PortfolioState` budgets; **annotate mode** (logs the selected subset, does not execute it) | T3 (proposes; annotate only) | Soak shows positive realized regret reduction; budgets bind often enough to matter. |
| **P3 — Correlation budgeting + learned ranker** | covariance-adjusted risk term; trained net-R ranker on the candidate→shadow→advisory ladder | T3 | Ranker beats rules EV in shadow; RG-style robustness check; clean cost labels (P0a) in hand. |
| **P4 — Graduate to influence** | backtest A/B (allocator vs per-cell baseline) on net-of-cost PnL + maxDD; live behind `CAPITAL_ALLOCATOR_MODE=off\|annotate\|apply` (a `*_MODE` flag, **not** `*_ENABLED` — same pattern as `NEWS_INFLUENCE_MODE` / `CONVICTION_SIZING_MODE`) | T3 | Backtest A/B PASS (beat baseline on net PnL, then maxDD%, OOS lift ≥ in-sample); prop arm clears `run_ev_montecarlo` survival; operator approval; one-line rollback to `off`. |

**Backtest gate detail (P4):** add an allocator arm to `scripts/backtest_system.py` that replays
ticks through `EVAllocator` with the full candidate batch and the same fee path, vs the per-cell
baseline. Win condition (priority order): beat the baseline on **net-of-cost PnL**, then **maxDD%**,
with **OOS lift ≥ in-sample** and **realized regret reduction > 0**. The prop accounts additionally
must clear `account_compat_matrix.py → run_ev_montecarlo` (faster, better-selected banking →
higher `mean_net_usd` at equal-or-better `p_breach`). Only a clean PASS graduates
`CAPITAL_ALLOCATOR_MODE=apply` on an operator-named account set.

## 9. Tier flags

| Change | Tier | Gate |
|---|---|---|
| Per-trade cost capture (`fee_*`/`funding_*` columns + close-path writer) | T1 | observability/data-path; `new-table-wiring` / db-wiring guard; no order-flow change |
| Candidate-batch exposure (§5.1) + `allocator_soak.jsonl` + read surface | T1 | observe-only; no order influence |
| `allocator_ev.py` rules EV scorer (soak-only) | T1 | observe-only |
| `EVAllocator` greedy selector — **annotate** | T3 | proposes a real selection; annotate logs only, executes nothing |
| Correlation budgeting + learned net-R ranker (shadow→advisory) | T3 | trainer-VM model; promotion gate is operator-approved; advisory-only influence |
| `CAPITAL_ALLOCATOR_MODE=apply` live | T3 | backtest A/B PASS + prop EV/survival + operator approval; rollback = `off` |

## 10. Prime-Directive compliance

- **No default-off `*_ENABLED` gate** in front of a required capability. The allocator graduates via
  a `*_MODE` (`off`/`annotate`/`apply`) flag — the sanctioned pattern (`NEWS_INFLUENCE_MODE`,
  `CONVICTION_SIZING_MODE`, `REGIME_ML_VERDICT_MODE`) that passes `env-gate-guard`. When `off` the
  pipeline is byte-for-byte the legacy per-cell path.
- **No auto-off / no breaker.** The allocator only **selects + reduces**; it never flips an account
  mode, never strands a strategy, and the per-account `RiskManager` stays the final sizing authority.
  A capability that would strand a strategy when the flag is dropped on a migration (the
  netting-guard / Ampere failure class) is forbidden — the legacy path is always reachable.
- **Observe-only until graduated.** Every phase before P4 logs what it *would* do; nothing influences
  a live order until the backtest A/B passes and the operator approves the `apply` flip.

---

### Provenance

All citations verified against the tree on branch `claude/portfolio-capital-allocator-g84p3m`
on 2026-06-29 via direct read + four delegated code-inventory passes (coordinator hook point;
decision-ML units; training substrate/schemas; risk/cost/correlation mechanics). Line numbers are
anchors at read time — re-confirm at edit time, the repo is a single squashed history so commit
archaeology is unavailable. **No `src/`, `config/`, or live-path file was modified by this
research.**
