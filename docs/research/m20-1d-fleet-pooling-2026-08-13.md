# Does pooling rescue the 1d fleet's exit-head cells? Measured: no.

**Date:** 2026-08-13 · **Tier:** 1 (research; changes no live lever) ·
**Evidence:** trainer-diag [#8923](https://github.com/benbaichmankass/Metis-Insights/issues/8923)
(+ [#8924](https://github.com/benbaichmankass/Metis-Insights/issues/8924), the symmetric arm)

## Why this was run

The 1d fleet is the single largest block of open M20 coverage cells — **25 of 37**,
of which **16 are `exit_head_ml`**. One cause was suspected behind all of them:
daily bars give each leg 31–72 trades, against fold standards calibrated on
intraday legs. The obvious escape is to pool legs into one training set.

I had estimated pooling would not rescue them. That estimate was worth nothing:
earlier the same session I predicted a fold count of 8 and the actual was 7 — off
by one trade. **So this was measured rather than argued.**

## What was run

Two arms, both on the existing `eh_1d` E0 datasets, both through the unmodified
`scripts/ml/train_exit_head.py` at its current `--min-fold-trades 50`.

- **(a) the principled pool** — the 7 `*_trend_long_1d` legs. `family_of()`
  (`build_exit_head_dataset.py:146`) already resolves all seven to one family,
  `donchian`, and they share one harness. This is a pool the design permits.
- **(b) the upper bound** — arm (a) plus the pullback family. **Not a design
  anyone would ship** (it mixes families); it exists so a negative closes the
  question instead of leaving "maybe more data would do it".

## Result

### Arm (a): zero usable folds

371 harness trades over 13,606 rows. **Every one of the 19 year-folds was
skipped.** Fold test sizes:

| | 2008 | 2009 | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 |
|---|---|---|---|---|---|---|---|---|---|---|
| trades | 2 | 3 | 17 | 18 | 22 | 25 | 23 | 12 | 20 | 27 |

| | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| trades | 14 | 23 | 30 | **33** | 20 | 24 | 26 | 18 | 12 |

The three oldest folds fail the separate `_MIN_FOLD_TRAIN_ROWS = 500` floor too.

**The largest fold in the pool is 33 trades.** The standard is 50. This is not a
near miss — the bound sits **51% above the best year the pool has**, so no
threshold at or above 34 can ever produce a single fold from these seven legs,
however they are combined.

### Arm (b): folds only by mixing families, and the per-leg samples still straddle the floor

Pooling across families (15 leg-sources, 28,699 rows) does clear the fold gate —
9 usable folds, OOS AUC 0.509–0.621. But the per-leg OOS samples it yields are
**23–89 trades**, straddling the `MIN_OOS_TRADES = 25` floor: `qld` (23) and
`tqqq` (24) were correctly refused as `insufficient_base`, and `iaum` passed at
exactly 25.

So even the deliberately-inadmissible upper bound does not produce comfortable
per-leg evidence. **The 1d fleet is short of data at every gate simultaneously,
not one decision away from gradeable.**

Arm (b)'s per-leg verdicts are **not** recorded in the coverage matrix. A verdict
from a mixed-family head is not the experiment those cells name; adopting it
would be sub-class **B**, implicit input selection (CLAUDE.md § "Diagnostic
provenance").

## The price of lowering the bound

Since "lower `--min-fold-trades`" is the obvious response, here is what each
value actually buys on arm (a)'s pool — and it is a cost table, not a menu:

| bound | folds surviving | share of the 371-trade pool inside a usable fold |
|---|---|---|
| **50** (current) | **0** | 0.0% |
| 40 | 0 | 0.0% |
| 33 | 1 | 8.9% |
| 30 | 2 | 17.0% |
| 25 | 5 | 38.0% |
| 20 | 11 | 73.6% |
| 15 | 14 | 87.9% |
| 10 | 17 | 98.1% |

*(The 19 fold sizes sum to 369 of the pool's 371 trades; 2 fall outside the fold
years. Stated rather than rounded away.)*

**Lowering the bound does not make the 1d fleet gradeable — it makes it *appear*
gradeable while the verdicts become noise.** The E1→E2 gate asks for OOS
AUC > 0.55 **and** a τ-policy beating the best hard rule on net_R **and** on
maxDD **and** agreeing in sign on the live set. At 20 trades per fold, `beats()`
— which carries **no minimum-n** — is close to a coin flip per comparison, and
four near-coinflips chained is a gate that passes things at random in both
directions. A bound of 20 would convert 16 honest `blocked` cells into 16
verdicts nobody should act on.

That is the argument *against* the move the table appears to invite, and it is
why this note proposes no value.

## What changed in the matrix

The 7 `*_trend_long_1d` `exit_head_ml` cells move `pending` →
`blocked:insufficient_folds`. The cause is now measured rather than un-run, which
is the same treatment `squeeze_breakout_4h` received.

**Read this as recording a cause, not as progress.** The headline moves
343/360 → **350/360 (97.2%)** because `blocked` counts as closed there; the
**done-condition is unchanged at 37 cells** because `blocked` counts as open
there. Both figures come from `scripts/research/m20_coverage_rollup.py`, run
before and after.

The 6 `*_pullback_1d` cells are deliberately **left `pending`** until #8924 — the
own-family pullback arm — reports. Arm (b) is not evidence about them.

## One error in #8923, recorded

Arm (b) was labelled **"ALL 1d pooled"** and was not all 1d. It covered the 13 1d
legs that *have* an E0 dataset — the 3 IBKR futures legs (`mes_trend_long_1d`,
`mgc_pullback_1d`, `mhg_pullback_1d`) are blocked on native history and have no
dataset to pool — and it additionally swept in 14 stray **1h** rows
(`gld_pullback_1h`, `spy_pullback_1h`) that the pullback family dir carries.

14 rows in 28,699 changes no verdict, but the label claimed a denominator it did
not have — sub-class **C**, in a probe I wrote to answer a question about
evidence quality. #8924 excludes the strays by name and prints the exclusion
count, so its population is stated rather than assumed.

## Open question this hands to the operator

The queued fold-standard decision now has a measured floor under it: **at the
current standard the 1d fleet cannot be graded at all, by pooling or otherwise,**
and the only lever that changes that is one which degrades every verdict it
produces. The genuine options are to accept the 1d `exit_head_ml` cells as
permanently blocked at this data volume, or to change what "gradeable" means for
daily-bar legs — which is a standards decision, not a threshold tweak.
