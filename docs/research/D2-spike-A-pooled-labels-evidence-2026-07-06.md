# D2 Spike A — pooled real+paper labels: evidence (2026-07-06)

**Question** (`MB-20260705-META-LABEL-WALL`): do pooled real+paper execution
labels, with an `account_class` domain flag, lift trade-outcome prediction on a
held-out REAL-money slice — the M14 S8 pooling recipe retried with
better-matched (real-execution paper) data?

**Answer: NO — and for a structural reason, not a model-quality reason.**
Under a leak-free chronological design, the pool barely exists: almost all
paper rows postdate the earliest defensible real-money holdout window, so the
pooled arms train on (nearly) the same rows as the real-only arm and produce
identical predictions. The label wall stands. The pooling lever can only
become real as paper and real history accrue **contemporaneously** going
forward.

## Setup

- Harness: `scripts/ml/spike_a_pooled_labels.py` (merged #5628/#5635 line);
  run on the trainer 2026-07-06 ~06:21Z (relay
  [#5632](https://github.com/benbaichmankass/ict-trading-bot/issues/5632))
  against a **fresh** journal sync + `trade_outcomes` v002 rebuild
  (`include_snapshots=true`).
- Dataset: **605** closed, non-backtest trades — **375 real** (win rate 26.7%)
  / **230 paper** (win rate 34.4%) / 0 unknown-domain (domain resolved via the
  canonical `accounts_loader`).
- Three arms sharing ONE chronologically-held real slice (never trained on):
  `real_only`, `pooled_flag` (+`account_class` feature), `pooled_bare`.
  Paper rows admitted to training **only if strictly before the eval-window
  start** (the temporal-leak guard).
- Features: 6 categorical (strategy/symbol/direction/setup_type/killzone/bias)
  + 5 account-context `*_at_signal` numerics. Native LightGBM, fixed params,
  seed 42.

## Results (held real slice)

| cut | held n (pos) | majority acc | arm | n_train | AUC | acc | recall_win | Brier |
|---|---|---|---|---|---|---|---|---|
| 0.2 (primary) | 75 (26) | 0.6533 | real_only | 300 | **0.6966** | 0.6533 | 0.0 | 0.2071 |
| | | | pooled_flag | **306** | 0.6966 | 0.6533 | 0.0 | 0.2095 |
| | | | pooled_bare | **306** | 0.6966 | 0.6533 | 0.0 | 0.2095 |
| 0.3 (stability) | 112 (40) | 0.6429 | real_only | 263 | 0.6222 | 0.6429 | 0.0 | 0.2220 |
| | | | pooled_flag | **263** | 0.6222 | 0.6429 | 0.0 | 0.2220 |
| | | | pooled_bare | **263** | 0.6222 | 0.6429 | 0.0 | 0.2220 |

The load-bearing column is **n_train**: at the primary cut the pooled arms add
**6** paper rows to the 300 real ones; at the 0.3 cut they add **zero**. The
paper accounts (alpaca/oanda/options ramp, 2026-06 onward) simply did not
exist during most of the real-money history, so any eval window late enough to
be a genuine holdout excludes essentially the whole paper set via the
temporal-leak guard.

Secondary observations, honestly bounded:

- **EPV ≈ 5–7 across all arms** — far under the ~10–20 EPV floor the
  recommendation report's sample-size sources imply; nothing here is a
  powered read of feature quality.
- `AUC 0.70/0.62 > 0.5` on the real-only arm suggests *some* ranking signal
  in the account-context features, but it is unstable across cuts and the
  0.5-threshold classifier degenerates to the majority class
  (recall_win = 0) at the ~27% win rate. No arm beats the majority baseline
  on accuracy — the S-MLOPT-S6 bar is NOT cleared.

## Interpretation

1. **The M14 S8 analogy does not transfer.** S8's pooling win was
   *cross-symbol* — pools that coexist in time. Real-vs-paper pooling here is
   a *cross-time* pool: the domains are almost disjoint chronologically, so a
   leak-free design cannot use the extra rows. This was not visible until the
   harness enforced the cutoff against the actual timestamps.
2. **No design fix rescues it today.** Evaluating on an earlier real slice
   would train on the future to predict the past; relaxing the cutoff is
   leakage — exactly the "plausible but wrong" shape the backtest-overfitting
   literature warns about. The honest move is to keep the harness and re-run
   as contemporaneous history accrues.
3. **Going forward the pool grows at paper's live rate.** Every new real
   trade now has paper contemporaries, so each future month adds usable
   pooled rows. A re-run becomes interesting when the pre-cutoff paper count
   is within ~1× of the real training count (rough parity), which the current
   accrual rates put a few months out — check via the same harness (it prints
   n_train per arm).

## Disposition

- `MB-20260705-META-LABEL-WALL` → **answered-negative (structural), keep for
  re-run**; evidence note appended. The D2 direction stays ranked behind
  D4/D1 per the 2026-07-05 recommendation — this result *strengthens* that
  ordering (D2's offline lever is smaller than believed until the pool
  matures).
- Next D2-adjacent option if wanted sooner: pool across **paper accounts
  only** (alpaca vs oanda vs options as domains) to validate the domain-flag
  mechanics on a pool that IS contemporaneous — a mechanics check, not a
  real-money lift.
