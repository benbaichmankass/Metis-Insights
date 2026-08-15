# How much of an E1 verdict is boundary placement? — the fold-offset dispersion measurement

**Date:** 2026-08-15 · **Tier:** 1 (measurement; no live path, no gate changed, no
matrix status flipped) · **Pre-registration:**
[`m20-fold-dispersion-preregistration-2026-08-15.md`](./m20-fold-dispersion-preregistration-2026-08-15.md),
written and committed (`017fc636`) before any arm reported, amended (`1c7e5ace`)
after the arms ran but **before any AUC was visible**.

Read this document against that one. Every threshold, the statistic, the control
values and the five committed consequences were fixed in advance; nothing below
selects them after the fact.

## The control PASSES

The pre-registration made one stop condition: offset 0 must reproduce the six
recorded `mean_auc` values, or the arms are not comparable and **no dispersion
number is reported**.

| leg | required | offset-0 arm | |
|---|---|---|---|
| gdx_pullback_1d | 0.6337 | 0.6337 | ✅ |
| gld_pullback_1d | 0.5277 | 0.5277 | ✅ |
| iaum_pullback_1d | 0.5525 | 0.5525 | ✅ |
| ief_pullback_1d | 0.5337 | 0.5337 | ✅ |
| slv_pullback_1d | 0.4895 | 0.4895 | ✅ |
| tlt_pullback_1d | 0.5300 | 0.5300 | ✅ |

Six of six, exact. `--fold-offset 0` is byte-for-byte the old partition — which
is also what `test_offset_zero_is_byte_for_byte_the_old_behaviour` asserts at the
unit level, now confirmed end-to-end on real data.

## Arm set 1 — the 3-arm cross-check (offsets 0 / 10 / 20)

Reported **as a cross-check, not as the primary**, per the amendment: this run's
other two arms (30 / 40) shrank the fold count 11 → 10, so only 0/10/20 are a
pure boundary shift. Relay #9377 launch, #9378 readout.

| leg | off0 | off10 | off20 | **spread** | u |
|---|---|---|---|---|---|
| gdx_pullback_1d | 0.6337 | 0.6513 | 0.6140 | 0.0373 | 7–8 |
| gld_pullback_1d | 0.5277 | 0.5167 | 0.5394 | 0.0227 | 11 |
| iaum_pullback_1d | 0.5525 | 0.5277 | 0.5231 | 0.0294 | 4 |
| ief_pullback_1d | 0.5337 | 0.5602 | 0.5860 | 0.0523 | 11 |
| slv_pullback_1d | 0.4895 | 0.4917 | 0.5157 | 0.0262 | 11 |
| tlt_pullback_1d | 0.5300 | 0.5038 | 0.4749 | **0.0551** | 11 |

**Median spread = 0.0333** · mean 0.0372 · **max 0.0551** (`tlt`), quoted
separately exactly as the pre-registration required. Every spread above was
recomputed from the printed AUCs rather than taken from the relay's own spread
column; all six agree.

### Which pre-registered band this lands in

**0.01–0.05 — "material but smaller than the observed one-day movement;
boundary placement is a contributor, not the explanation."**

That is the middle band, and it is the least convenient of the three: it does not
license "the gate is fine" and it does not license "the gate is noise". The
one-day re-measurement that opened this question moved `mean_auc` by −0.110 on
its worst leg; re-partitioning the *same* trades moves it a median 0.033. So
boundary placement accounts for roughly a third of that movement at the median —
real, and not the whole of it.

**Two of six legs exceed the 0.05 line individually** (`tlt` 0.0551, `ief`
0.0523). The headline is the median by pre-registration, and the median is what
governs; but a per-leg statistic that clears the top band on a third of the legs
is not something the median should be allowed to hide, which is why the
pre-registration demanded the max separately.

### `iaum` — the pre-registered watch, and it fires

Item 5 named this in advance: `iaum_pullback_1d` was graded `candidate` at offset
0 on a **0.0025 margin** over the 0.55 bar (0.5525). Across the pooled arms:

```
off0   0.5525   clears  (+0.0025)
off10  0.5277   FAILS   (-0.0223)
off20  0.5231   FAILS   (-0.0269)
```

The E1 gate is a **conjunction** — `u >= 2` AND `mean_auc > 0.55` AND the two
`beats_*` fold majorities — so a leg at 0.5277 cannot be `candidate` whatever the
other three terms do. Failing the AUC term is decisive on its own. This is the
concrete demonstration the pre-registration said would be worth more than the
aggregate: **one leg, one gate term, three draws, and the grade does not survive
re-drawing the fold boundaries.**

It is one leg at `u = 4`, and that is the whole caveat — see the limits below.

### Arms 30 / 40 — reported under their own label, not pooled

| leg | off30 | off40 | folds |
|---|---|---|---|
| gdx | 0.6265 | 0.6185 | 7 |
| gld | 0.5329 | 0.5369 | **10** |
| iaum | 0.5791 | 0.5389 | 4 |
| ief | 0.4983 | 0.5240 | **10** |
| slv | 0.5192 | 0.4885 | **10** |
| tlt | 0.4788 | 0.4977 | **10** |

These answer a *different* question — boundary shift **plus one fewer fold** —
because `u = floor((N−k)/b) − 1` with `N = 629, b = 50` holds `u = 11` only for
`k ≤ 29`. They are recorded, labelled, and excluded from every number above.

Worth one observation, offered as a note and not a finding: `iaum` reads 0.5791
at offset 30 — its **highest** of the five — while reading 0.5231 at offset 20.
Across all five arms the leg spans 0.5231–0.5791, straddling its gate bar in both
directions. That is consistent with the pooled reading, from an arm that cannot
be pooled.

## Arm set 2 — the primary (offsets 0 / 6 / 12 / 18 / 24)

All five arms completed `exit=0` (`dispersion_clean_20260815T012717Z`). Readout
dispatched as relay #9379, which additionally asserts the **fold-count invariant
per leg** — `u` constant across all five arms — since holding `u` fixed is the
entire reason this set was relaunched. **Results pending; this section will be
filled from #9379 and the headline median restated from it.** The 3-arm figures
above stand as the cross-check either way.

## What this does NOT do

Committed in advance, and honoured:

- **No matrix status is flipped, in either direction.** `iaum`'s `candidate` is
  *not* being re-graded here. The measurement is of the instrument, not the leg.
- **No gate change is proposed.** Changing a gate after seeing which term it
  fails on is how a gate stops meaning anything. The deliverable is the number;
  the Tier-3 decision is the operator's.
- The backlog row
  `BL-20260814-EXIT-HEAD-AUC-MOVES-MORE-THAN-ITS-OWN-GATE-MARGIN-ACROSS-A-ONE-DAY-RE-MEASUREMENT`
  is **narrowed, not closed**: boundary placement is a measured contributor at
  ~⅓ of the observed movement, so the other two candidates (TP geometry, pool
  size) still own the remainder.

## Limits, stated plainly

1. **This family is the THIN one, so this is an UPPER BOUND** — pre-registered as
   item 1. `u` runs 4–11 here against 26 for `pullback_1h`. The reading that
   travels is the *small*-spread direction (it would hold a fortiori for thicker
   families); a large spread on thin legs does not establish that well-powered
   verdicts are unstable. The median 0.0333 is therefore a ceiling on what a
   thick family would show, not an estimate of it.
2. **The arms share trades.** This measures re-partitioning sensitivity, **not**
   sampling error over new data. Those are different quantities and are not
   conflated here.
3. **Three or five offsets out of fifty** is a sample of the partition space, not
   a census.
4. **Six legs.** The median of six is a weak median. It is reported with all six
   values visible so a reader can form their own view of it.

## Reproduce

```
scripts/research/m20_exit_head_round.py --fold-offset K ...   # K in 0..block_n-1
```
The flag refuses `--fold-mode=years` with a non-zero offset and refuses
`K >= block_n`, prints the skipped head count, and is stamped unconditionally
into `_round_meta` so an offset-0 arm is distinguishable from a round predating
the flag. Twelve tests in `tests/test_fold_offset.py`, each verified
load-bearing by planting its own regression.
