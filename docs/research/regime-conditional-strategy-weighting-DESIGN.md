# Regime-conditional strategy weighting — DESIGN (2026-06-18)

> **Tier-1 research/design doc.** Nothing here touches the live order path,
> `config/strategies.yaml`, `config/accounts.yaml`, or any unit the live VM
> consumes. It proposes a *meta-layer* that learns, per strategy, **when to
> listen to it** — validated offline with the same holdout rigor that just
> caught an overfit — and a phased, evidence-gated path to graduate it.
>
> Status: **DESIGN — for operator review before build.** Step 1 (the
> regime-conditional performance map) is the first cheap probe.
>
> Origin: operator direction 2026-06-18 — *"not every strategy has to win all
> the time. As long as we can know when it's a winning strategy and when it's
> not… so we know when we should listen to it and when we shouldn't. I want a
> realistic workplan that is overall P&L positive."*

## 1. Why this exists (the thesis)

This session's evidence loop ended on a precise finding: the alt cells
(`trend_4h`, `pullback_2h`) are **net-of-fee positive across the full window**
but **fail specific recent regime folds**; a hand-tuned per-cell ADX gate
*helped some cells and hurt others*, and a holdout showed the "best" thresholds
were **fold-split overfits** (`docs/claude/strategy-refinement-queue.json`,
SRQ-20260618-001/002). The lesson is not "these strategies are bad" — it is:

> **A strategy doesn't need to be good *everywhere*. It needs to be good
> *predictably* — and the system needs to know, in advance, which regime it is
> in so it can size that strategy up when favourable and down when not.**

That is a **portfolio meta-problem**, not a per-strategy tuning problem.
Chasing a per-cell threshold that survives every fold is the wrong target
(it overfits). The right target is a **learned, per-strategy favourability
weight** conditioned on a *predictable* regime signal — so an
individually-mediocre book of strategies can compose into an
**overall-P&L-positive** portfolio by listening to each member only when it
has an edge.

## 2. What already exists (reuse, don't rebuild)

This is a **graduation of built scaffolding**, not a green-field system. The
pieces:

- **The regime router** — `config/regime_policy.yaml` +
  `src/runtime/intents.py::_hard_regime_gate` (`REGIME_ROUTER_ENABLED`).
  Already maps `(strategy, side, regime) → ON/OFF` cells. **Currently phase 2**
  (shadow-log only: emits `regime_shadow_gate` audit rows, `enforced:false`).
  This is *already* a "when to listen to each strategy by regime" layer — but
  the cells are **hand-authored hard on/off**, not learned soft weights.
- **A regime classifier in the ML registry** — `btc-regime-1h-lgbm-yz-v1`
  (promoted to `advisory`), plus **per-bar regime scoring**
  (`src/runtime/regime_bar_scoring.py`, `REGIME_BAR_SCORING_DISABLED`) that
  scores every shadow regime head on its own bar cadence into
  `runtime_logs/shadow_predictions.jsonl`. The classifier output is the
  natural **predicted-regime input** to a weight map.
- **Regime is already stamped on every decision** — order-package `meta`
  carries `regime`, `adx_14`, `regime_source` (e.g. `adx-14`), `vol_regime`
  (verified live this session). So a regime-conditional performance map can be
  built from data the bot **already records** — no new instrumentation.
- **A portfolio backtester** — `scripts/backtest_system.py` replays the whole
  roster over one price history **through the real `intents.py::aggregate_intents`
  netting** + a shared account. This is where a soft-weight overlay is tested
  against the *real* arbitration, not standalone.
- **The holdout / readiness tooling** — `scripts/ops/m15_ws_b_fold_report.py`
  + `classify_strategy_tier.py` + the multi-fold-config holdout method this
  session proved out. The regime layer is validated with the **same** rigor.
- **The conviction / unified-confidence soak** — `conviction_sizing` /
  `conviction_arbitration` observe-only logs. A per-strategy favourability
  weight is exactly a conviction multiplier; this is the layer it would
  express through.
- **Roadmap already names this** — `docs/claude/performance-review-backlog.json`
  `PERF-20260601-006` (router phase 3 — hard gate) and **`PERF-20260601-007`
  (router phase 4 — graduate to soft weights + classifier-v0 detection).** The
  operator's idea **is** the planned phase 4, arrived at independently.

## 3. The model

For each strategy `s`, learn a **favourability weight**

```
w_s(x_t) ∈ [0, 1]
```

where `x_t` is a vector of **regime features available at decision time `t`**
(no lookahead): ADX bucket, realised-vol bucket, trend/chop label, the regime
classifier's probability, killzone/session, time-of-day, and — importantly —
features that are *predictive*, not coincident. The strategy's per-account size
is scaled by `w_s` (reductively at first — it can shrink toward a floor but not
inflate beyond the configured risk, mirroring the existing news-influence
hook's reductive-only contract).

This **replaces the hand-authored hard on/off cells** with a continuous,
learned weight: `w_s ≈ 1` in a strategy's good regime, `≈ 0` (floored) in its
bad regime, and graded in between. A hard gate is the degenerate case
`w_s ∈ {0,1}`.

Why a learned soft weight beats per-cell threshold tuning (the thing that just
overfit): it is fit **across all strategies × regimes × the whole history**
with regularisation, not a separate knife-edge threshold per cell — so it has
far more signal per parameter and a single, testable OOS validation.

## 4. The phased plan (each step cheap, evidence-gated, OOS-validated)

**Step 1 — Regime-conditional performance map (the first probe, cheap).**
For each strategy (live book + the paper_ready cells), compute its edge
**conditioned on regime** — net-R / win-rate / expectancy bucketed by
ADX/vol/trend and by the regime classifier's output — from the backtest +
shadow data we already have. This answers the **falsifiable gating question**:

> *Does each strategy have a regime, identifiable from past data, in which it
> is reliably +EV — and is that regime persistent enough to act on?*

- If a strategy's edge **concentrates in a predictable regime** → it is a
  genuine candidate for regime weighting.
- If its edge is **regime-uniform but noisy**, or only identifiable **in
  hindsight** → a regime layer won't save it; say so and move on.

This step alone is high-information and decides whether the whole initiative
has legs **before** any model is built. Runs offline via `vm-driver`.

**Step 2 — Soft regime-weight portfolio overlay (the test that matters).**
Build a v0 weight map `w_s(x_t)` (start simple: a regularised logistic / GBM on
the regime features, or even a binned lookup from Step 1's map). Run
`scripts/backtest_system.py` **twice** — un-weighted roster vs. regime-weighted
roster — and compare **net P&L, max drawdown, and Sharpe**. The whole point is
the *portfolio* number: does knowing-when-to-listen turn a flat/negative book
positive?

**Crucially — fit the weight map on a TRAIN period, evaluate on a HELD-OUT
period** (and ideally a held-out symbol). This is the discipline that caught
the ADX overfit; the regime layer earns nothing until it clears the same bar.

**Step 3 — Graduate the regime router phase 2 → 4.** If Step 2 holds OOS,
replace `regime_policy.yaml`'s hard cells with the learned soft weights, run it
**shadow-first** (`regime_shadow_gate` rows, `enforced:false`) to confirm the
live would-be weights match the backtest, then enforce as a **reductive size
multiplier** (Tier-3, operator-gated). Rollback is one env flip, mirroring the
existing router.

## 5. Validation discipline — the one hard rule

**Do not move the overfitting up a level.** A regime layer is *itself* a
prediction model and can curve-fit exactly like the ADX thresholds did. The
non-negotiables:

1. **No lookahead.** Every regime feature must be computable from bars closed
   **at or before** the decision bar. Hindsight regime labels are useless for
   gating and must never enter the weight map.
2. **Predictability test.** Before trusting a regime, confirm it is
   **forecastable** from past features and **persistent** over the strategy's
   hold horizon — not just labelable after the fact. A regime that flips faster
   than we can act on it is noise.
3. **Holdout, same rigor as this session.** The weight map is fit on train and
   evaluated on a withheld period (multi-fold-config + held-out symbol). A
   regime overlay that only wins on its training split is rejected, exactly as
   the 4 ADX candidates were (2026-06-18 holdout).
4. **Honest null.** If the regime-weighted portfolio does **not** beat the
   un-weighted book out-of-sample, the answer is "regimes aren't predictable
   enough here" — report it and stop, don't tune until it passes.

## 6. Risks / failure modes

- **Regime unpredictable / non-persistent** → no edge; Step 1 surfaces this
  cheaply before any build.
- **Weight-map overfit** → caught by the Step 2 holdout (§5).
- **Interaction with the existing hard cells** → the soft weights *supersede*
  `regime_policy.yaml`; run shadow-first so the two are never silently
  double-applied.
- **Correlation** → weighting correlated strategies by the same regime can
  concentrate risk into one regime call; the portfolio backtest (§4 Step 2)
  measures realised drawdown, not assumed independence.
- **Reductive-only contract** → like the news-influence hook, v1 may only
  *shrink* size (toward a floor), never enlarge — so a wrong regime call
  cannot increase exposure, only forgo it.

## 7. Connection to the rest of the system

- **Readiness ladder** (`docs/strategy-readiness-ladder.md`): a regime weight
  is the bridge that can take a `paper_ready` cell to `live_ready` *as a
  regime-conditional contributor* even when it can't pass the every-fold gate
  standalone — the exact gap the 4 holdout-rejected candidates fell into.
- **Recombination orchestrator** (`docs/research/strategy-primitives-recombination-DESIGN.md`):
  the `regime_filter` axis there becomes a **learned weight**, not a hard ADX
  gate — this design is that axis's mature form.
- **Conviction layer**: the per-strategy favourability weight is the natural
  payload of the unified-confidence soak (`conviction_sizing`).
- **ML lifecycle**: the regime classifier is a normal registry model
  (`candidate → shadow → advisory`); the weight map is trained, shadow-logged,
  and promoted through the same 3-stage gate.

## 8. First step

**Step 1 — the regime-conditional performance map** — is the cheap, decisive
probe. It needs no new model and no live change: bucket each strategy's
existing backtest + shadow outcomes by regime and ask whether a *predictable*
+EV regime exists per strategy. Its output decides whether Steps 2–3 are worth
building. Recommended to run next via `vm-driver` on the trainer.
