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

- **Regime hard-gate** (`_hard_regime_gate`, `PERF-601-006/007`): becomes a
  conviction *input* (regime alignment), not a binary drop.
- **Advisory downsize** (reductive-only): generalized into the sizing lens
  (which can scale **up** within the risk budget, not only down).
- **Discrete stage gate**: stage governs a model's **weight** in its lens
  (continuous influence), instead of a hard advisory/shadow on-off.

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

1. **Per-trade risk budget** — the max risk fraction conviction scales within. *(open — set from § 4.3 research)*
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
  2026-06-12). Not part of this redesign.
