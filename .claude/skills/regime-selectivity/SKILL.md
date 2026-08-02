---
name: regime-selectivity
description: The binding rules for authoring, gating, and flipping a REGIME OFF-CELL — the (trend, vol) cells in config/regime_policy.yaml that drop a strategy's intents before routing. Use when authoring or changing a trend_vol cell, when deciding whether a strategy needs vol/trend gating, when reading a regime_cell_walkforward result, or before proposing a Tier-3 regime-router flip. Owns three rules that keep the router SELECTIVE (gates only money-losing regimes that generalize), not cosmetic: no-cosmetic-cell, walk-forward-before-Tier-3, axis-fidelity. Composes with backtesting (the evidence), ml-review (the advisory vol head), and the regime tooling (scripts/research/regime_cell_walkforward.py). NOT for training the vol head itself (that's model-training) and NOT for the intent layer's flip policy (FLIP_POLICY).
---

# /regime-selectivity — author regime OFF-cells that gate only what deserves gating

A regime **OFF-cell** is a `(trend, vol)` pair in `config/regime_policy.yaml` that
makes `Coordinator.aggregate_intents` **drop** a strategy's candidate intents before
the reinforcement / conflict-resolution logic runs (hard gate, `enforced:true`,
baseline-on via `REGIME_ROUTER_DISABLED`). It removes real-money trades. So a cell is
a Tier-3 order-routing decision, and the bar to author one is **evidence that the
gated regime loses money AND that the loss generalizes out-of-sample** — not that a
better label is available, and not that a backtest slice happened to be red.

Three rules, each from a real incident.

## Rule 1 — No cosmetic cells

**Do NOT author an OFF-cell for a book that is healthy.** A better vol *label* cannot
make an all-folds-profitable book need gating.

- Canonical negative: SOL's ungated book is the healthiest of the three crypto symbols
  (ret/DD 1.72, all folds profitable). Gating it LOSES net (full-history −$198) and only
  buys drawdown; the negative cells FAIL the walk-forward (fixed-cell 2/4, cell-selection
  1/3). So SOL has an advisory vol head (an **observer**) and **no cell** — that is the
  correct state, not a gap (`REGIME_ML_VERDICT_MODE` note, `A-vol-gating-ETH-SOL-OFFcell-
  evidence-2026-07-06.md`).
- The anti-pattern this rule kills: `BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS` — authoring
  a cell because a slice looked bad, not because a generalizing money-losing regime was
  proven. A cosmetic cell strands capability (netting-guard-class silent regression) while
  reading as "handled."
- Re-visit a "no cell" verdict ONLY when a **retrained head + a fresh walk-forward** proves
  a money-losing cell that GENERALIZES. Absent that, the observer-with-no-cell state stands.

## Rule 2 — Walk-forward before Tier-3

**A cell that gates real-money routing must clear `scripts/research/regime_cell_walkforward.py`
BEFORE the operator flip.** The gate is `*_stable_drag`: **`pooled < 0` AND strict
majority-negative under EVERY member of the fixed internal panel `FOLD_PANEL = (3, 4, 5)`**
(fold-count-invariant — a verdict must not flip on the caller's `--folds`, the
`BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP` defect). A cell that is `*_fold_sensitive`
(disagreement across the panel) is **not** a pass.

- The evidence must be a **clean backtest**, NOT the fabricated-journal PnL class
  (`src/runtime/provenance.py`; "Always state the population"). A red slice built from
  mark-priced closes is not evidence a regime loses money.
- The 2-D `(trend, vol)` cells the router enforces are a DIFFERENT population from the 1-D
  trend cell — fold the actual 2-D cell (`--vol {calm,volatile}` + `--vol-labels`, which is a
  **hard error without labels**, never a silent 1-D fallback; #8395 /
  `BL-20260730-WALKFORWARD-NO-VOL-AXIS`). Labels come from the advisory head the router
  reads (`ml_vol_label_replay`), not the frozen `vol_detector`.
- Prepare the Tier-3 packet; do not flip. The operator flips, walk-forward-gated.

## Rule 3 — Axis fidelity

**The vol/trend label the gate DECISION uses must be exactly the one the router reads at
runtime — reported honestly.**

- Vol-label resolution is **per-SYMBOL** via `ml_vol_regime_for_symbol` (the advisory head),
  NOT per-`(symbol, timeframe)`. A symbol with no advisory head resolves `unknown` → frozen
  (permissive). Under `REGIME_ML_VERDICT_MODE=use` the gate substitutes the head's
  `predict_proba(row)["volatile"]` (thresholded at `ML_VOL_VERDICT_THRESHOLD`).
- A diagnostic about the gate must report **`predict_proba(row)["volatile"]`**, never
  `max(proba)` — every regime head is multiclass, so `max(proba)` prints HIGH for a
  confidently-CALM row (the inverted-label class, `BL-20260730-PARITY-PROBE-MISLABELS-
  MAXPROBA`). Use `scripts/ml/_regime_score_semantics.py`; do not re-derive.
- Confirm all three "for use to change a real-money outcome" conditions before claiming a
  cell is live: (a) the `(trend, vol)` OFF-cell is authored; (b) the gated strategy's SYMBOL
  has an advisory head; (c) the hard gate is active (baseline-on).

## Checklist before proposing a cell

1. Is the ungated book already healthy (all-folds-profitable)? → **no cell** (Rule 1).
2. Is the red evidence a clean backtest, population stated, not fabricated marks? (Rule 2)
3. Does `regime_cell_walkforward.py` on the actual **2-D** cell (with `--vol-labels`) return
   `*_stable_drag` (not `*_fold_sensitive`) under `FOLD_PANEL=(3,4,5)`? (Rule 2)
4. Does the label the cell keys on match what `ml_vol_regime_for_symbol` /
   `predict_proba["volatile"]` resolves at runtime for that symbol? (Rule 3)
5. Only then: prepare the Tier-3 packet + `account_compat_matrix`; the operator flips.

Composes with `backtesting` (evidence), `model-training` / `ml-review` (the advisory head),
and `scripts/research/regime_cell_walkforward.py` (the gate).
