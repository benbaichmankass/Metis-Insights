# Conviction-blend v1 weight sweep — findings (2026-06-16)

> Resolves design § 4.2 / § 6 decision #4 ("v1 blend weights — sign off after the
> sweep"). **Recommendation: KEEP the hand-set defaults** for now — the corpus is
> too thin to *identify* the relative weights out-of-sample. The sweep harness
> ships so the decision is re-run automatically as the soak accrues multi-input
> decisions. Tier-1, offline, no config change.

## What was asked

Replace the hand-set `DEFAULT_CONVICTION_WEIGHTS` in `src/runtime/conviction.py`
(c_strat 0.45 / c_setup 0.20 / c_wr 0.20 / c_reg 0.15) with evidence: build a
`(calibrated lens inputs → realized won/EV)` corpus, sweep the per-input weights,
and pick the set that best predicts realized win/EV **out-of-sample** (rank-AUC +
Brier under a purged/walk-forward split), weighting live rows up and not
overfitting. If the data is too thin to choose, say so and keep the defaults.

## The harness (shipped)

`scripts/ml/sweep_conviction_weights.py` (+ `tests/ml/test_sweep_conviction_weights.py`):

- **Corpus** = the `conviction_meta` dataset family (one row per closed, filled,
  non-backtest order package: the calibrated lens inputs paired with `won`),
  reused verbatim so there is **no train/serve skew** with the live observe-only
  stamp. Optional backtest `--emit-trades` augmentation adds `c_strat`-only rows
  (`source=backtest`); live rows are up-weighted via `--live-weight`.
- **Scoring** — a candidate weight vector *is* the model: per row, blend the
  present inputs with the candidate weights (the live `compute_conviction`), then
  score the blended conviction vs `won` with **rank-AUC** (reuses
  `ml.promotion.attribution.rank_auc`) + weighted **Brier**.
- **Selection is out-of-sample** — purged walk-forward (chronological expanding
  folds, optional purge gap). A change is recommended only when a candidate
  **robustly** beats the hand-set default's OOS rank-AUC by `>= 0.02` **AND** the
  corpus carries enough **multi-input** rows (`>= 150`) to identify the head
  weights relative to `c_strat`.

## Why "keep the defaults" (the identifiability argument)

A weighting of `c_setup` / `c_wr` / `c_reg` **against** `c_strat` is only
identifiable from rows that carry **≥ 2 inputs**. Two structural facts make the
current corpus unable to support a re-weight:

1. **The live multi-input corpus is tiny.** The `conviction_meta` family is the
   only source of multi-input rows, and it is the same corpus the v2
   `conviction-meta-v1` model trains on — **registered at `candidate`, degenerate
   at n ≈ 65 with `f1_True = 0`** (design § 4b, verified 2026-06-16). Head slots
   (`c_setup`/`c_wr`/`c_reg`) are present only when a shadow head scored the
   decision, and the observe-only stamp deployed only just before this sprint, so
   the count of rows with ≥ 2 inputs is a *fraction* of 65 — far below the 150
   needed to choose a 4-way weighting out-of-sample without overfitting.
2. **Backtest augmentation cannot fix this.** The six per-strategy `--emit-trades`
   harnesses emit `(confidence, won)` — i.e. **`c_strat` only**. They can thicken
   `c_strat` calibration evidence (already done, design § 4b) but contribute
   **zero** multi-input rows, so they cannot inform the *relative* head weights no
   matter how large.

The design's own calibration finding reinforces this: for most strategies raw
`c_strat` barely discriminates win/loss, and the real conviction signal is
expected to come from the **v2 learned meta-model** (which finds interactions a
linear blend misses), not from re-tuning the v1 linear weights. Spending the
scarce multi-input rows to overfit a 4-way linear weighting would be exactly the
overfitting the task warns against.

> **Live-corpus confirmation:** a read-only trainer-relay pull of the current
> `conviction_meta` row count + input-presence was attempted (`trainer-vm-diag`,
> 2026-06-16) but the relay's command parser truncates long one-liners, so a clean
> live read wasn't obtained this session — the n rests on the design-doc-verified
> figure (§ 4b, n≈65) plus the structural argument below. This does **not** change
> the recommendation: the 150-multi-input-row threshold is baked into the harness,
> so running it on the live VM (`--db /data/bot-data/trade_journal.db`) decides on
> its own evidence and flips to `adopt_swept_weights` the moment the corpus is rich
> enough. (To get the live number now: run the harness directly on the VM, or land
> a short committed probe script the relay can call by name.)

## Decision

- **`DEFAULT_CONVICTION_WEIGHTS` is unchanged** (c_strat 0.45 / c_setup 0.20 /
  c_wr 0.20 / c_reg 0.15). The `c_strat`-heavy hand-set weighting is itself a
  *valid finding* (task: "a near-uniform or c_strat-heavy result is a valid
  finding") — c_strat is the only always-present, strategy-specific input, and
  the heads are still maturing.
- **Re-run trigger:** when the live `conviction_meta` multi-input row count
  reaches ≥ 150 (or the v2 meta-model stops being degenerate), run:
  ```bash
  python scripts/ml/sweep_conviction_weights.py \
      --db /data/bot-data/trade_journal.db \
      --backtest-corpus 'runtime_logs/calibration/*_trades.jsonl' \
      --live-weight 3 --out runtime_logs/conviction_weight_sweep.json
  ```
  If it reports `adopt_swept_weights`, the chosen weights ship via a Tier-3
  operator-approved PR editing `DEFAULT_CONVICTION_WEIGHTS`.

## Status

- Harness + tests: **shipped** (this PR).
- Default weights: **unchanged** (data too thin — sanctioned § 6 #4 outcome).
- Operator sign-off needed only **if/when** the re-run recommends a change.
