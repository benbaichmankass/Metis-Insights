# M28 — Signal-research methodology for the valuation-snapshot format

**Status:** active work plan (operator-directed 2026-07-23). Owns the *process*
(the iteration loop + construction backlog) for developing predictive signals in
the valuation-snapshot schema, not any one sleeve. The top-level program plan —
the full funnel (feasibility screen → signal grade → **PnL grade** → build →
express → live), the research map, the phasing + decision checkpoints, and the
signal→PnL translation — is [`M28-signal-RnD-program.md`](M28-signal-RnD-program.md);
this doc is its iteration-mechanics component. Composes with `research-driver` +
`RESEARCH-RIGOR-STANDARD.md`.

## The reframe (why this doc exists)

The first three signal sleeves — **value** (ERP/real-yield/GSR/OAS), **CFTC-COT**
positioning, **crypto** funding/OI/basis — were each built, graded honestly
(non-overlapping horizon-IC + conviction spread), and came back
`no_monetizable_horizon` / negligible-spread
([re-grade findings](M28-sleeve-honest-horizon-ic-regrade-2026-07-23.md)). That is
**not** a "these don't work, drop them" verdict. It is the **first three data
points in an iterative construction search**, and they share one property that
makes the null unsurprising:

> **All three used the weakest possible construction:** the *trailing percentile
> of a single raw series*, oriented contrarian. That is one cell in a large space.

The **point of M28 is not to ship these three sleeves.** It is to develop the
**repeatable methodology** — the process, the instrument, the toolkit, the
compounding record — for turning a market intuition into a signal that clears an
honest, cost-aware gate. The three nulls are the process *working*: the
measuring instrument is built and it is telling us the naive construction is too
weak. Now we iterate on construction. We are at step one.

## What is already built (the instrument — keep it as the single arbiter)

- **The schema** — `valuation_snapshots` rows (`symbol/metric/value/cheap_score/
  label/percentile/z_score/n_history/higher_is_cheaper/observed_at/as_of/source/
  inputs/note`). Any construction that emits this schema is graded UNCHANGED.
- **The honest gate** — `thesis_backtest_run.py` (P4, net-of-cost calibration +
  beat-baseline) + `horizon_ic_scan.py` **run `--non-overlapping`** (honest
  t-stat) with the **conviction spread** (market-neutral, monetizable reading of
  a +IC). Pre-registered bar: a **flagged-significant IC** AND a **positive
  conviction spread that survives the cost model** at a **tradeable horizon**.
- **PIT discipline** — `observed_at`/`as_of` no-lookahead, enforced by
  `thesis_replay.as_of_snapshot_rows`. Bound by `RESEARCH-RIGOR-STANDARD.md`
  (walk-forward/OOS, config-exact, truncation-honest, honest negatives recorded).

The instrument does not change as we iterate. Only the constructions feeding it do.

## The loop (the deliverable is this process, run to convergence)

```
   ┌─ 1. HYPOTHESIS ─────────────────────────────────────────────┐
   │  a specific construction = input × transform × conditioning  │
   │  × cross-section, stated as a falsifiable claim about which   │
   │  direction & horizon should carry edge.                      │
   └──────────────────────────────────────────────────────────────┘
                              ↓
   2. EMIT   → PIT-correct valuation-snapshot rows (reuse the schema).
                              ↓
   3. GRADE  → the honest gate (P4 + non-overlapping horizon-IC +
               cost-aware conviction spread). One arbiter, unchanged.
                              ↓
   4. LEARN  → record verdict + WHICH construction dimension moved the
               needle, in the research ledger (compounding record).
                              ↓
   5. ITERATE → the learning selects the next construction. Go to 1.
```

**Exit criterion for the *methodology* (not any sleeve):** either (a) the process
has produced ≥1 construction that clears the honest gate out-of-sample, OR (b) the
construction space for an input family is mapped and exhausted, telling us it
genuinely carries no tradeable edge at our costs. **Both are real, recorded
results.** "We tried percentile-of-one-series and stopped" is neither.

## The construction backlog (what to try next — the dimensions we have NOT varied)

The three nulls varied only the **input**. They held **transform =
level-percentile**, **conditioning = none**, **cross-section = time-series**,
**composite = single-series** constant. Those four held-constant dimensions are
the unexplored search space:

### D1 · Transform — *the highest-value unexplored lever*
The level of a positioning/valuation series is usually a worse predictor than its
**change**. Try, per input:
- **change / impulse** — Δ or z-score of the week-over-week change (positioning
  edge lives in the *shift*, not the *level*). *The operator's explicit instinct
  for COT.*
- **divergence** — series A vs series B: COT **large-spec vs commercial** (the
  classic COT edge is the hedger/spec *split*, not spec level), crypto **funding
  vs basis**, value **price vs fundamental**.
- **acceleration** (2nd derivative), **deviation-from-trend** (detrended residual).

### D2 · Conditioning — gate the signal on a second variable
A raw contrarian percentile fires on every extreme; a *conditioned* one fires only
on the setups that historically resolve:
- **crypto**: funding-extreme ONLY WHEN OI-rising AND basis-premium (crowded **and**
  leveraged **and** paying up = a real squeeze, vs a lone funding blip).
- **value**: ERP-cheap ONLY WHEN momentum-turning (cheap-and-turning, not
  catching-a-knife).
- **regime-conditioning**: percentile *within the current vol/trend regime* (reuse
  the bot's regime heads).

### D3 · Cross-section vs time-series — rank against what
- time-series (current): an instrument vs its own history.
- **cross-sectional**: rank the instruments against *each other* at each date (long
  the cheapest, short the richest of the basket). This is the classic value/carry
  construction and often where the tradeable, market-neutral edge actually lives.

### D4 · Composite — combine the sub-signals
Within a sleeve, blend the sub-signals into one conviction (COT specs+commercials;
crypto funding+OI+basis; value ERP+real-yield+OAS) — equal-weight first, then
IC-weighted. A composite can clear the gate where each part alone doesn't.

### D5 · Horizon × cost
Pick the horizon where the conviction spread **survives the fee/carry model**.
Crypto's 1d spread (+2 bps/day gross) was killed by fees; the fix is a longer
horizon or a bigger-magnitude construction, chosen *by* the cost model, not fixed.

### The mechanistic family (M29 system-dynamics) as a signal generator
M29's stock/flow engine (`src/sysdyn/`) is a **fundamentally different generator**:
instead of "rank a series," it produces a **model-implied fair value → mispricing**
(the calibrated gas model says price *should* be $X given storage+weather; the
market is at $Y; the signal is the mispricing Y−X). **This has NOT yet been graded
through the signal gate.** The P1c `no_mechanistic_edge` verdict was on
*calibration R²* (does the model fit/forecast price) — a **different question** than
*does the mispricing signal trade through the P4/horizon gate*. The next M29 step is
to **emit the sysdyn mispricing as a valuation-snapshot signal and grade it on the
same instrument** as everything else. And the engine is reusable for other
stock/flow markets (storables, rates-as-flows, etc.), each a new candidate input.

### New input families (each a fresh candidate through the same gate)
Options-implied (skew / term-structure / put-call), ETF & fund flows, credit
spreads by rating, macro-surprise indices, seasonality, on-chain (crypto). Each is
D1–D4 applied to a new input.

## Infrastructure that makes iteration cheap (so the loop is fast)

1. **A construction toolkit** — pure, tested transforms so a new construction is a
   few lines over the existing readers, not a new bespoke sleeve:
   `pct_change_signal`, `zscore_signal`, `divergence_signal`,
   `cross_sectional_rank`, `composite_signal`, `regime_conditioned`. (The three
   sleeves each hand-rolled percentile; factor it once.)
2. **The research ledger** — [`M28-signal-research-ledger.md`](M28-signal-research-ledger.md):
   every construction tried, its honest verdict, and the learning, so dead ends
   aren't re-run and the process compounds. This is the "figure out the process"
   artifact.
3. **The gate** — already built; the single arbiter, unchanged per iteration.

## Recommended next construction (the first turn of the loop)

**Build the D1 transform family first** (change/impulse + divergence), applied to
**COT** and **crypto**, because: (a) all three nulls held transform constant, so it
is the largest unexplored lever; (b) COT's snapshots are already reconstructed, so
iteration is cheap; (c) the classic COT edge *is* the change + spec/commercial
divergence, and the classic crypto squeeze *is* funding-change conditioned on OI —
both are named, testable hypotheses, not fishing. Ship the toolkit + the two
re-emitted constructions, grade them through the same non-overlapping gate, and
record the result in the ledger — whichever way it comes out.

Tier-1 throughout (observe-only research; `src/sysdyn/*` stays import-linter-pure,
all IO in `scripts/`). Nothing graduates to an order-affecting sleeve without
clearing the honest gate out-of-sample AND operator approval (Tier-3).
