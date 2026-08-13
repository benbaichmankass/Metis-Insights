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

### The symmetric arm (#8924): pullback-1d gives the same verdict

`family_of()` resolves all six `*_pullback_1d` legs to one family, `pullback`, so
this pool is exactly as principled as arm (a). **568 harness trades over 15,066
rows — zero usable folds.** All 19 year-folds fail the bound; the largest is 42
trades (2023 and 2024).

So the pullback pool is *closer* to the bound than donchian's (42 vs 33 against
50) and still short. **Both same-family 1d pools fail, independently.** Every 1d
leg that has an E0 dataset — 13 of the 16 — is blocked on one cause.

The run's `live: AUC=0.864` line is computed over **n=1**. It is not a number
anyone may quote, and it is recorded here only so nobody later finds it in the
report JSON and mistakes it for evidence.

## The price of lowering the bound

Since "lower `--min-fold-trades`" is the obvious response, here is what each
value actually buys on arm (a)'s pool — and it is a cost table, not a menu:

| bound | donchian folds (of 19) | pullback folds (of 19) |
|---|---|---|
| **50** (current) | **0** | **0** |
| 45 | 0 | 0 |
| 42 | 0 | 2 |
| 40 | 0 | 2 |
| 35 | 0 | 5 |
| 33 | 1 | 7 |
| 30 | 2 | 7 |
| 25 | 5 | 15 |
| 20 | 11 | 16 |

*(Fold sizes sum to 369 of donchian's 371 trades and 548 of pullback's 568; the
remainder falls outside the fold years. Stated rather than rounded away.)*

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

## What changed in the matrix (final)

All **13** 1d `exit_head_ml` cells with an E0 dataset move `pending` →
`blocked:insufficient_folds` — 7 trend (#8923) and 6 pullback (#8924).

| | before tonight | after |
|---|---|---|
| HEADLINE | 343/360 = 95.3% | **356/360 = 98.9%** |
| DONE-CONDITION | **37 cells** | **37 cells** |
| pending / blocked split | 17 / 20 | **4 / 33** |

**The headline moved 13 cells and the done-condition moved zero.** 98.9% reads
like "nearly finished"; the actual state is that the same 37 cells are open and
we have converted *"we have not looked"* into *"we looked and cannot grade it"*.
That is real progress in knowledge and no progress toward done, and the two must
not be reported as one thing.

## Two errors in my own probes, recorded

Arm (b) was labelled **"ALL 1d pooled"** and was not all 1d. It covered the 13 1d
legs that *have* an E0 dataset — the 3 IBKR futures legs (`mes_trend_long_1d`,
`mgc_pullback_1d`, `mhg_pullback_1d`) are blocked on native history and have no
dataset to pool — and it additionally swept in 14 stray **1h** rows
(`gld_pullback_1h`, `spy_pullback_1h`) that the pullback family dir carries.

14 rows in 28,699 changes no verdict, but the label claimed a denominator it did
not have — sub-class **C**. #8924 excludes the strays by name and prints the
exclusion count, so its population is stated rather than assumed.

**#8924 then printed a `trades` column that counted something else.** Having
asked for "trade count, not just row count" *because* rows are bars, I keyed the
counter on `(strategy, entry_time)` — and E0 **dataset** rows do not carry
`entry_time`; that is a key of the harness *emit* rows. Every row hashed to
`(strategy, None)`, so the counter reported the number of **legs**:

```
legs pooled: 6   total rows: 15079   total TRADES: 6
  gdx_pullback_1d   rows=1606  trades=1
```

Sub-class **A**, semantic substitution: a column labelled `trades` holding
"distinct legs". The authoritative count was in the trainer's own next line the
whole time — `pullback: 568 harness trades` — and that is the figure used
throughout this note.

`trades=1` beside `rows=1606` is absurd on its face, which is the *lucky* version
of the bug — it announced itself, the way `attributed_pct: 136.8` did. Had it
printed a plausible 95, I would have quoted it.

**The pattern is worth naming, not just the two instances:** both errors are in
probes I wrote *while auditing evidence quality*, one relay apart. Writing a
check does not exempt the check. The cheap defence in both cases was the same and
was available: assert the probe's own output against a known positive
(a stated denominator; a count the trainer already prints) before believing it.

## Open question this hands to the operator

The queued fold-standard decision now has a measured floor under it: **at the
current standard the 1d fleet cannot be graded at all — by pooling within either
family, or otherwise** — and the only lever that changes that is one which
degrades every verdict it produces. The genuine options are to accept the 1d
`exit_head_ml` cells as permanently blocked at this data volume, or to change
what "gradeable" means for daily-bar legs — which is a standards decision, not a
threshold tweak.

One asymmetry the decision should not average away: the two families are not
equally far from the bound. Pullback reaches 42 and donchian 33, so any bound
chosen to unblock pullback (≤42) still leaves all 7 donchian legs blocked, and a
bound low enough to unblock donchian (≤33) hands pullback 7 folds at sample sizes
where `beats()` — which has **no minimum-n** — is near-coinflip. There is no
single value that treats both honestly.

**Nothing here is actionable without you.** No lever was flipped, no threshold
changed, no standard rewritten; the 13 cells were moved from "un-run" to "measured
and blocked", which is a bookkeeping correction, not a decision.
