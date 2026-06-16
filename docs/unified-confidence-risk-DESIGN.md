# Unified Confidence Risk Architecture — DESIGN / RESEARCH PLAN

> **Status: PROPOSAL (research phase).** Nothing in here ships until the plan
> is approved. Implementation is Tier-3 (touches the live order path + sizing)
> and stays gated behind operator approval, phased rollout, and a shadow soak.
> This document is the research-backed plan; it does not change runtime
> behaviour.
>
> Author: Claude (/health+perf+ml review session, 2026-06-15). Supersedes the
> gate-based influence roadmap (`PERF-20260601-006/007` regime hard-gate,
> the discrete shadow→advisory→limited_live influence model) — see § 7.

## 0. Why

Two operator directives drive this redesign:

1. **One basis for risk management, not a thicket of independent gates.** Today
   every model and policy is its own binary/reductive gate (regime hard-gate,
   advisory downsize, news downsize, stage gate). The operator wants the models
   to **collectively produce confidence-style scores** that *advise* sizing and
   trade-selection — not gate. The only gate-like behaviour is a **no-trade
   floor**: if the collective score falls below a threshold, the trade isn't
   worth taking.
2. **Risk is per-strategy-per-trade, sized by confidence × per-trade risk ×
   available margin — with no per-account max-position cap.**

Refinement (2026-06-15): not *one* unified number but a small set of
**composite metric categories ("lenses")**, each aggregating the inputs natural
to it and feeding a different decision stage (§ 3.1).

## 1. Principles

- **Advice, not gates.** Models contribute to continuous scores; the only hard
  refusal is the per-lens no-trade floor (and the existing account loss guards).
- **One conviction basis** drives: (a) the no-trade decision, (b) the size
  scalar, (c) competing-trade arbitration.
- **Per-trade risk; no per-account max-position.** `daily_loss_pct` +
  `max_dd_pct` remain the *only* account-level guards.
- **No third execution gate** (Prime Directive). The no-trade floor must never
  silently strand a configured strategy; any kill-switch ships **inert /
  permissive-default** (the `REGIME_ROUTER_ENABLED` / `FLIP_POLICY` pattern),
  one env flip + restart to roll back, no redeploy.
- **Fail-permissive.** A scoring failure keeps the trade at its un-adjusted
  size / keeps the intent — never strands a live signal (matches every existing
  advisory/news/regime hook).

## 2. Current architecture (verified 2026-06-15, citations)

| Concern | Reality today | Cite |
|---|---|---|
| Signal confidence | Every strategy emits a **varying** `[0,1]` confidence, but each normalizes a **different geometric quantity** (breakout depth, VWAP σ, body/range…), so scores are **not comparable across strategies**. | `src/units/strategies/*`, e.g. `trend_donchian.py:224`, `vwap.py:581` |
| Confidence usage | Plumbed end-to-end (`StrategyIntent.confidence`) but **unused by the aggregator** and **unused by sizing**; only fed as an ML feature. | `intents.py:333-335,354`; `advisory_sizing.py:57` |
| Position sizing | `risk_usdt = balance × effective_risk_pct`; `qty = risk_usdt/(stop_dist × contract_value)`, floored, `max(min_qty,…)`. **Confidence not read.** | `src/units/accounts/risk.py:110-146,503-654` |
| Margin in sizing | Already a **pre-flight cap**: `qty ≤ (available_usd × leverage)/entry` (crypto/non-futures). | `risk.py:623-652` |
| `pos_size` cap | **DORMANT in production** — enforced against `order.meta["estimated_value"]`, which is **never set outside tests**. | `risk.py:488-490` |
| Live account guards | `daily_loss_pct` (scales qty + hard refusal) and `max_dd_pct` (refusal) are **active**; `min_balance_usd`, `leverage` active. | `risk.py:485-494,558-559,609-613` |
| ML→order influence | The **only** influence point is `apply_advisory_downsize` (post-sizing, **reductive-only**, `[size_floor,1.0]`); default `annotate` (logs, no resize). Stage-gated to `{advisory,limited_live,live_approved}`. `shadow` scores are audit-only. | `advisory_sizing.py:128-169`; `coordinator.py:1276-1279` |
| Head outputs | Heterogeneous: regime=multiclass probs; setup_quality=R-multiple `[-3,3]`; trade_outcome_winrate=P(win) `[0,1]`; execution_quality=bps `[-200,200]`; prop_mission_policy=TBD. **Not directly comparable.** | `ml/datasets/families/*` |
| Intent arbitration | Same-direction = `max(target_qty)` (not sum); opposite = **static priority map wins** (`turtle_soup 50 > … > 0`); **confidence NOT consulted**. `FLIP_POLICY=hold` default. | `intents.py:156-219,804-993` |

**Two findings that shape the design:**
1. Removing `pos_size` is **cleanup**, not a behaviour change (it's already dormant).
2. Cross-strategy confidence comparability is the **central technical problem** — calibration is mandatory before any "unified" score is meaningful.

## 3. Target architecture

### 3.1 Composite metric lenses (not one number)

Each lens is its own confidence-style fusion (v1 formulaic → v2 learned), with
its own inputs, floor, and soak:

| Lens | Question | Inputs (heads/signals) | Feeds |
|---|---|---|---|
| **Conviction** | Should we trade, and how strongly (directionally)? | strategy signal confidence (calibrated) · setup_quality · trade_outcome_winrate · regime alignment · **news veto/boost** | no-trade floor · size scalar · competing-trade arbitration |
| **Sizing / feasibility** | How much can/should we put on? | **available margin** · per-trade risk budget · execution_quality (cost/slippage) · prop_mission_policy (funding/equity) · current portfolio exposure | final qty |
| **Exposure** | Is the book over-extended? | concurrent open risk · correlation across positions | book-level throttle (damps qty as exposure rises) |

*(Exposure lens is in-scope for the build, operator-decided 2026-06-15 — not deferred.)*

Heads land in the lens that matches what they *measure*: `execution_quality`
and `prop_mission_policy` are sizing/feasibility inputs; `setup_quality`,
`trade_outcome`, regime, and news are conviction inputs.

### 3.2 Fusion: formulaic v1 + learned v2 (parallel shadow soak)

- **v1 (ship-able):** within each lens, **normalize** every input to a common
  scale, then a **weighted blend**. Weights hand-set initially, documented,
  tunable. This is what *drives* decisions first.
- **v2 (built in parallel, shadow-only):** a **learned meta-model (stacker)**
  per lens — ingests all member head outputs + context, emits a single
  calibrated target (conviction → P(win)/EV; sizing → a feasibility scalar).
  **Logs alongside v1 with zero influence** until it clears a promotion gate
  (mirrors the shadow→advisory ladder, but the artifact is the *fused* score).
- **Calibration is the precondition** (§ 4.1): per-strategy confidence and each
  head get mapped to a comparable scale (e.g. isotonic/Platt against realized
  win/EV) so the blend isn't adding apples to oranges.

### 3.3 Sizing rule (with available margin)

```
risk_qty   = per_trade_risk_budget / stop_distance          # risk-based size (existing math)
desired    = conviction_score × risk_qty                    # conviction scales it
margin_cap = (available_margin × leverage) / price          # feasibility ceiling (existing clamp, risk.py:623-652)
final_qty  = min(desired, margin_cap [, exposure_throttle]) # → floor to exchange min / whole-contract
```

- `per_trade_risk_budget` replaces today's flat `effective_risk_pct` as the
  *max* risk per trade; conviction scales **within** it (low conviction → small,
  high conviction → up to the budget).
- **Available margin (operator-decided 2026-06-15): BOTH** — a *proportional
  throttle* so the book self-damps as free margin fills, **and** the
  `margin_cap` as the hard upper bound. Size scales down with shrinking free
  margin and can never exceed the ceiling.
- `pos_size` cap is **dropped** (dormant cleanup). `daily_loss_pct` +
  `max_dd_pct` remain the only account guards.

### 3.4 Competing-trade arbitration

Replace the static priority map with **conviction**: when strategies conflict on
a symbol, the higher-conviction intent wins; same-direction reinforcement can
weight by conviction instead of `max(target_qty)`. The hook already exists
(`StrategyIntent.confidence`, `intents.py:354`) — it's currently ignored.

### 3.5 No-trade floor

A per-lens floor (primarily conviction): below it, the trade is **journaled as a
refusal** (like a RiskManager per-trade refusal — *not* a mode flip, *not* a
new `*_ENABLED` gate). Ships inert (floor `0`) and is raised deliberately.

### 3.6 What gets subsumed / deprecated

**The per-model 7-stage ladder is UNCHANGED** (operator clarification
2026-06-16). Each model keeps its own independent lifecycle
(`research_only → … → shadow → advisory → limited_live → live_approved`) and
its own training/promotion path. What changes is only what *consumes* the
model's output: instead of each model having its own bespoke gate, **every
model's result becomes an input to the relevant confidence lens**, and the
model's **own stage governs which conviction it feeds**:

- a model at **`shadow`** contributes to the **observed conviction** (logged,
  no influence) — exactly its current "observe-only" semantics;
- a model at **`advisory`+** additionally contributes to the **influencing
  conviction** that drives sizing/arbitration.

So the two-tier influence gate (shadow=observe, advisory+=influence) is
preserved per-model; it just expresses itself as the model's weight/membership
in the lens rather than a separate downsize hook. Concretely:

- **Regime hard-gate** (`_hard_regime_gate`, `PERF-601-006/007`): becomes a
  conviction *input* (regime alignment), not a binary drop.
- **Advisory downsize** (reductive-only): generalized into the sizing/conviction
  lenses (which can scale **up** within the 2% risk budget, not only down).
- **Discrete stage→influence**: stays a per-model property, but its effect is
  "feeds observed vs influencing conviction" rather than a standalone gate.

## 4. Research agenda (resolve before/with the build)

1. **Calibration per input** — how to map each strategy's confidence + each
   head to a comparable scale. Candidate: isotonic/Platt vs realized win/EV on
   the closed-trade book + backtest replay. Deliverable: a calibration report
   per strategy/head.
2. **v1 blend weights** — initial weights per lens, justified by backtest of
   blended-vs-flat sizing. Deliverable: a sweep + recommended weights.
3. **Confidence→size curve + risk-budget value** — linear? floored/capped? what
   `per_trade_risk_budget` (Tier-3 risk number). Deliverable: walk-forward of
   the sizing curve on history.
4. **Margin: ceiling vs proportional throttle** (§ 6 fork) — backtest both.
5. **Meta-model (v2) design + data sufficiency** — features (all lens inputs +
   context), target (realized win/EV), and the **training-data problem**:
   closed-trade volume is tiny and the trade-outcome baselines are degenerate
   (f1=0, `MB-20260615-DEGEN-BASELINES`). Likely needs **backtest-augmented
   per-trade rows** (`MB-20260530-001`).
6. **Interaction with `FLIP_POLICY=hold` + the netting guard** — confirm
   conviction-driven sizing/arbitration composes cleanly with existing
   position-netting behaviour.
7. **Real-money fill-volume dependency** — the advisory model's `live_agreement`
   is 0 because real money is pinned at the 0.001 min lot (`bybit_2`); a
   confidence-driven size that can scale up is *also* what unblocks accruing
   real-money evidence. Quantify the interaction.

## 4a. P0 research findings (2026-06-15, code-verified)

**Calibration data sources.**
- Live join is clean: `order_packages.confidence`(+`model_scores`) ↔ `trades.pnl`
  on `order_package_id`, filtered `status='closed' AND is_backtest=0`
  (`src/units/db/database.py:179-297`). But **volume is the bottleneck**.
- **The fix is already shipped:** the six per-strategy backtest harnesses
  (`scripts/backtest_{trend,fade,squeeze,pullback,fvg_range,ict_scalp}.py`)
  emit per-trade `(confidence, net_r)` JSONL via `--emit-trades` today →
  the immediate high-volume calibration corpus. `ict_scalp` calls the **live**
  `order_package()` (best fidelity). **Gaps:** `run_backtest_vwap.py` and
  `backtest_system.py` carry confidence in-memory but don't emit it per-trade
  (~40-line hook each, Tier-1). `src/backtest/backtester.py` (the M5 `/test`
  → `backtest_results` table) is a *different* FVG/OB engine — **not** a
  calibration source.

**Calibration method.** Target basis = **P(win)** (what `trade_outcome_winrate`,
the `won` label, and `brier_lift` already speak). Per-strategy **isotonic
regression** raw_conf→P(win) (handles the saturation seen in the old
`htf_pullback` 1.0 pathology); **Platt/logistic** fallback for small n;
**decile equal-frequency binning** as the most robust small-sample fallback.
Heads: `trade_outcome_winrate`/`prop_mission` already `[0,1]`; `setup_quality`
R-multiple `[-3,3]`→isotonic; regime→a per-strategy **alignment scalar**
(P(favorable regime)); `execution_quality` (bps) belongs to the **sizing lens**,
not conviction. **Degenerate heads (regime f1=0) are excluded until they earn
`rank_auc > 0.5`.**

**Reuse, don't rebuild.** `ml/promotion/attribution.py:179-312` already does the
trade↔score join + `rank_auc`/`brier`/`brier_lift` — it's the calibration-data
assembler + reliability toolkit. Isotonic/Platt/reliability-curve code is
**greenfield** (no such code in repo → sklearn/scipy dependency).

**Concrete v1 conviction formula** (all inputs calibrated to `[0,1]` P(win)):
```
c_strat = conf_cal[strategy](raw_confidence)     # per-strategy calibrator
c_setup = cal_setup(setup_quality)               # R-multiple → P(win)
c_wr    = cal_wr(trade_outcome_winrate)          # recalibrated P(win)
c_reg   = regime_alignment(regime_probs, dir)    # P(favorable regime)
m_news  = news_multiplier ∈ [floor, 1]           # reductive only (existing layer)
conviction = m_news × (0.45·c_strat + 0.20·c_setup + 0.20·c_wr + 0.15·c_reg)
```
Weights hand-set (tunable, § 4.2 sweep). **Missing-input rule:** renormalize
weights over present inputs (a strategy with no heads → `conviction = c_strat`);
fail-permissive, never zero a live signal. No-trade floor reads off `conviction`
(ships at `0`, inert; refusal is journaled like a RiskManager per-trade refusal,
not a gate).

**P1 wiring (zero order-path change):** compute `conviction` + per-input
breakdown at signal time and stamp it on `pkg.meta` exactly as `model_scores`
already rides (`strategy_signal_builders._emit_shadow_preds:223-308` →
`coordinator._log_new_order_package:2450-2463`). `StrategyIntent.confidence`
(`intents.py:354`, carried-but-ignored today) is where a later phase routes it
for arbitration; sizing application later sits beside `apply_advisory_downsize`
(`coordinator.py:1276-1290`) — **not in P1**.

**Next experiments (P0→P1):** (1) run the 6 harnesses with `--emit-trades` over
full validated history (target ≥500–1000 rows/strategy); (2) add the
`--emit-trades` hook to VWAP + the system harness (Tier-1); (3) fit per-strategy
calibrators + reliability curves, cross-validate live-only vs backtest-augmented
(weight live rows up — backtest `won` is a fee-modeled proxy); (4) per-head
`rank_auc` readiness pass, calibrate only heads with `rank_auc>0.5`; (5) stamp
observe-only `conviction` and soak before any influence.

## 4b. P0 build status (2026-06-15) — Tier-1, no live path touched

Shipped on the branch (tested + lint-clean; **nothing wired into the order
path** — these are the offline calibration/observe-only building blocks):

- **`ml/calibration/`** — `Calibrator` family (isotonic / Platt / decile /
  constant) with a **pure-Python predict** (no sklearn at predict time;
  serializes to plain dicts), `fit_calibrator(auto-selects by sample size)`,
  and reliability/Brier/ECE metrics. 18 unit tests.
- **`src/runtime/conviction.py`** — the v1 conviction blend as a pure,
  stdlib-only, fail-permissive function (weight renormalization over present
  inputs, reductive news multiplier, inert no-trade floor). 10 unit tests.
- **`scripts/ml/build_calibration_corpus.py`** — runs the 6 per-strategy
  harnesses with `--emit-trades` → the `(confidence, won)` corpus.
- **`scripts/ml/fit_confidence_calibrators.py`** — fits per-strategy
  calibrators + writes a reliability report (raw-vs-calibrated Brier/ECE).
- **`src/backtest/run_backtest_vwap.py`** — fixed the `--emit-trades` hook to
  emit vwap's real confidence (was hardcoded `None`).

**End-to-end verified on the sample candle data** (illustrative, not production
volume): calibration sharply improved reliability, e.g. trend_donchian
ECE 0.30→0.011, squeeze 0.71→0.013, fade 0.19→0.012. The production fit needs
the corpus run over full validated history (P0 next step).

**P1 observe-only path — BUILT 2026-06-16 (operator: "everything up until
shadow"):**
- **`src/runtime/conviction_inputs.py`** — adapter from `model_scores`
  (`{model_id:{stage,score}}`) + strategy confidence → calibrated conviction
  inputs. `classify_head` routes heads to slots (trade-outcome→`c_wr`,
  setup-quality→`c_setup`, regime→`c_reg`; execution-quality/prop-mission →
  sizing lens, excluded from conviction). **Per-model stage respected**:
  `influencing_only=True` keeps only advisory+ heads (`c_strat` always live).
  Read-only cached calibrator-artifact loader (no sklearn on the live path).
  +12 tests.
- **`_emit_shadow_preds` stamp** (`src/runtime/strategy_signal_builders.py`) —
  computes the conviction observe-only and stamps `sig.meta.conviction`
  (with per-input provenance) alongside `model_scores`. **Never read back into
  the order** — fail-permissive, pure logging, so the score soaks before any
  P2+ influence. Verified end-to-end at runtime.

This is the shadow/observe-only stage: conviction is now *computed + logged on
every signal* but influences nothing. **Stops exactly at the operator's "up
until shadow" line.**

### Real calibration fit (2026-06-16, multiyear data on the trainer)

First production fit over `btc_1h_multiyear.csv` (1h strategies) — calibration
sharply improves reliability (ECE → ~0) on real history:

| strategy | n | base_rate | method | Brier raw→cal | ECE raw→cal |
|---|---|---|---|---|---|
| trend_donchian | 1261 | 0.354 | isotonic | 0.340 → **0.224** | 0.281 → **0.000** |
| fade_breakout_4h | 471 | 0.297 | isotonic | 0.264 → **0.205** | 0.187 → **0.000** |
| squeeze_breakout_4h | 247 | 0.385 | platt | 0.516 → **0.235** | 0.501 → **0.013** |
| htf_pullback_trend_2h | 348 | 0.325 | isotonic | 0.464 → **0.211** | 0.463 → **0.000** |
| fvg_range_15m | 686 | 0.583 | isotonic | 0.359 → **0.243** | 0.327 → **0.000** |

*(ict_scalp_5m + a from-5m fvg pass run detached on the trainer — fold in next.)*

**Key finding (shapes the design):** the calibrated reliability curves show
**several strategies' raw confidence barely discriminates win/loss** —
`fvg_range` collapses to a near-constant ~base-rate (no rank signal),
`trend_donchian` / `htf_pullback` concentrate ~75–95% of trades in one mid bin.
So **`c_strat` alone is a weak conviction input** for most strategies —
validating leaning on the ML head inputs (`c_setup` / `c_wr` / `c_reg`), and
meaning the **v2 learned meta-model** (which can find interactions raw
confidence misses) is where the real conviction signal will come from.
`trend_donchian` has the most usable gradient.

**Deployment path for fitted calibrators:** trainer-produced + regenerable →
ride the **trainer mirror** (like the registry / sweeps); the live loader reads
the mirrored `calibrators.json`. No git commit of the artifact. Moot until the
observe-only stamp deploys.

**Status as of 2026-06-16 (P1 complete + shipped):** the observe-only conviction
stamp is **deployed** (live `git_sha 022d4332`, trader verified healthy) and
soaking `meta.conviction`; the **calibration loop is shipped** (#3786 — auto-fit
each trainer cycle via `scripts/ops/fit_calibrators.sh`, published on the trainer
mirror, the live loader reads the mirrored `calibrators.json`, **5m `ict_scalp`
folded in**); the v2 **`conviction-meta-v1`** model is **registered at
`candidate`** (degenerate at n=65 — `f1_True=0`, expected at this volume; improves
as the soak accrues, stays at `candidate` with no influence). OANDA `xauusd_trend_1h`
paused (PB-20260616-001).

**Remaining:** per-head `rank_auc` readiness pass; **P2+ live influence**
(sizing/arbitration off the conviction) — Tier-3, operator-gated. The P2
implementation plan is **§ 10 (ready to build)**.

## 5. Phased rollout

| Phase | Scope | Gate |
|---|---|---|
| **P0** | This design doc + the § 4 research (calibration, weights, backtests). | Tier-1 |
| **P1** | Compute the lens scores as **observe-only** fields on every order package (log v1 *and* v2, **no influence**). Soak. | Tier-1 (no order-path change) |
| **P2** | Conviction-driven **sizing on demo (`bybit_1`) only**, behind an inert-default flag; validate vs current. | Tier-2/3, demo |
| **P3** | Conviction **arbitration** for competing intents (demo). | Tier-3, demo |
| **P4** | Real-money sizing (`per_trade_risk_budget × conviction × margin`) + drop the dormant `pos_size`; v2 meta-model still shadow. | **Tier-3, operator-gated** |
| **P5** | Promote each lens's fusion **v1 formulaic → v2 learned** once the soak shows agreement (per-lens promotion gate). | **Tier-3, operator-gated** |

## 6. Open operator decisions

1. ~~**Per-trade risk budget**~~ — **DECIDED 2026-06-16: 2%** — the max risk
   fraction a `conviction=1.0` trade may take; conviction scales size *within*
   this. (Today's `effective_risk_pct` is ~0.3-1%; 2% is the ceiling, reached
   only at full conviction.) Real money stays small for now (operator: "leave it
   small + loop") — the 2% ceiling applies but small balances still floor to the
   exchange minimum, which is accepted.
2. **No-trade floor threshold(s)** — per lens; ships at 0 (inert). *(open — set from § 4 research)*
3. ~~**Available margin: hard ceiling vs proportional throttle vs both**~~ — **DECIDED 2026-06-15: both** (§ 3.3).
4. **v1 blend weights** — sign off after the § 4.2 sweep. *(open)*
5. ~~**Exposure lens** — build now or defer~~ — **DECIDED 2026-06-15: build now** (in-scope, § 3.1).

## 7. Relationship to existing roadmap

This supersedes/absorbs: regime-router phases 3-4 (`PERF-20260601-006/007`),
the discrete stage-gate influence model, and the standalone advisory/news
downsize hooks (they become lens inputs). The 7-stage *training* ladder stays;
only its *order-influence* semantics change (continuous weight vs binary).

## 8. Guardrails (binding on implementation)

- **No new default-off `*_ENABLED` gate** — `env-gate-guard` CI enforces this;
  kill-switches ship inert with a permissive default.
- **Fail-permissive** scoring hooks (swallow, return unchanged qty/intent).
- Tier-3 files (`config/strategies.yaml`, `config/accounts.yaml`,
  `src/runtime/orders.py`, `src/units/accounts/risk.py` in spirit) change only
  via operator-approved PR.
- `dry-run-guard`, `canonical-config-loaders`, `account-class-guard` etc. must
  stay green.

## 9. Out of scope (tracked separately)

- **OANDA re-point** (XAU→tradeable OANDA-US FX pair) — its own Phase-0 mini-plan
  (OANDA US is spot-FX-only; XAU not tradeable, which is why it was shelved
  2026-06-12). **`xauusd_trend_1h` PAUSED 2026-06-16** (removed from
  `oanda_practice.strategies` to stop the orphan noise); re-point tracked as
  `PB-20260616-001`. Not part of this redesign.

## 10. P2 implementation plan (READY TO BUILD)

P2 = **conviction-driven sizing on the demo account only**, behind an inert
flag, annotate-first. Everything it needs is in place (the calibrated
`meta.conviction` is stamped on every order package; calibrators ship via the
mirror). This is the first time conviction *influences* an order, so it is
demo-scoped and Tier-2/3.

**Flag (mirror `NEWS_INFLUENCE_MODE`, NOT a `*_ENABLED` gate — env-gate-guard):**
- `CONVICTION_SIZING_MODE` ∈ `off` (default) | `annotate` (log the would-be
  resize, don't apply) | `apply` (resize). Read at call time.
- `CONVICTION_SIZING_ACCOUNTS` — comma-separated allowlist (set to `bybit_1` for
  the demo soak); permissive-when-unset *only after* real-money sign-off (mirror
  `POSITION_NETTING_GUARD_ACCOUNTS` semantics). For P2, require it to name demo.

**Integration point:** `src/core/coordinator.py::multi_account_execute`, right
after `sized_qty = RiskManager.position_size(...)` and beside
`apply_advisory_downsize` / `apply_news_downsize` (the existing post-sizing
multiplier site, ~`coordinator.py:1276-1290`). Add
`apply_conviction_sizing(pkg, sized_qty, account_name)` in a new
`src/runtime/conviction_sizing.py` — **fail-permissive** (any error returns
`sized_qty` unchanged), and a **no-op unless** `CONVICTION_SIZING_MODE != off`
AND the account is in the allowlist.

**Computation:** read the already-stamped `pkg.meta["conviction"]["conviction"]`
(the calibrated blend; if absent/None → no-op). Then:
```
risk_qty  = (per_trade_risk_budget=0.02 × balance) / (stop_distance × contract_value)
desired   = conviction × risk_qty
margin_cap= (available_margin × leverage) / entry            # existing clamp
throttle  = available_margin / total_account_margin          # proportional (§3.3)
final     = min(desired × throttle, margin_cap)  → floor to exchange min
if conviction < no_trade_floor (default 0, inert): journal a refusal, qty=0
```
**KEY difference from advisory/news:** conviction sizing can **enlarge** size
(up to the 2% budget / margin), not only shrink — so it MUST be bounded by
`margin_cap` and demo-scoped first. `annotate` mode logs `desired`/`final` to a
`runtime_logs/conviction_sizing.jsonl` soak log without changing `sized_qty`.

**Validation (demo):** run `annotate` on `bybit_1` first; compare the would-be
conviction-sized qty vs the actual risk-sized qty over a soak window; only flip
to `apply` once the conviction is non-degenerate (the v2 meta-model has matured
past the current `f1_True=0`) and the annotate log looks sane.

**Open numbers to set before `apply`:** per-trade risk budget (2% decided), the
no-trade floor (start 0), the throttle curve shape. **Rollback:** flag → `off`
(one env flip, no redeploy).

**Tests:** `apply_conviction_sizing` no-ops when mode=off / account not allowed /
conviction missing; `annotate` never changes qty; `apply` scales within
`[0, margin_cap]`; fail-permissive on any exception. Drift guard that the flag
isn't a `*_ENABLED` gate.
