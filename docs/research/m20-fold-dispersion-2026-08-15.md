# How much of an E1 verdict is boundary placement? — the fold-offset dispersion measurement

**Date:** 2026-08-15 · **Tier:** 1 (measurement; no live path, no gate changed, no
matrix status flipped) · **Pre-registration:**
[`m20-fold-dispersion-preregistration-2026-08-15.md`](./m20-fold-dispersion-preregistration-2026-08-15.md),
written and committed (`017fc636`) before any arm reported, amended (`1c7e5ace`)
after the arms ran but **before any AUC was visible**.

Read this against that document. The statistic, the control values, the three
interpretation bands and the five committed consequences were all fixed in
advance; nothing below selects them after the fact.

---

## In one screen

**What was asked:** how much of an `exit_head_ml` verdict is the accident of
where the fold boundaries fall? A backlog row had specified the measurement; it
had never been run because no flag could move a boundary at fixed block size.

**What was measured:** `--fold-offset` shipped, 10 arms over the 1d pullback
family, same pool and geometry. The pre-registered control passed — offset 0
reproduced all six recorded AUCs exactly.

**Three findings, in ascending order of how much they matter:**

1. **The AUC spread lands ON its own threshold.** Median 0.0515 against a
   pre-registered 0.05 line; 0.0496 without the one leg whose fold count moves.
   The measurement cannot resolve which side, and is reported that way.
2. **A recorded NEGATIVE is boundary-fragile.** `gdx_pullback_1d` reads
   `candidate` on 2 of 7 draws. The `candidate` direction was expected to be
   fragile; the negative direction was not, and nothing revisits a negative.
3. **The candidate column is ~2.7× more exposed than the negative one** —
   **8 of 14** candidates sit one fold flip from failing, against 4 of 19
   negatives. This is *arithmetic over the whole committed corpus*, not an
   extrapolation from (1): the gate is `beats * 3 >= u * 2`, so a slack of 0–2
   means one fold changes the verdict. Three candidates sit **exactly** at a
   fold-majority bar. The criterion is validated by the single leg actually
   re-partitioned — `gdx`, predicted fragile, observed fragile.

**Why (3) is the one to act on:** a fragile negative costs an unexplored
opportunity; a fragile candidate is a cell that would justify shipping a lever
onto a **live** leg.

**⚠️ AND THE SCREEN TEMPERS (3) — read § "The screen finished" before acting on
it.** A 16-arm screen over 17 legs came back after this summary was first
written: **every leg with a proven-clean off0 control was UNANIMOUS across four
boundary draws (8 of 8)**, and all five verdict changes sat in legs that *also*
failed their control. Pooling both runs' clean-control legs gives **2 of 14 (14%)
showing verdict instability**, a lower bound. So being one flip from the bar
predicts a verdict *can* move, **not that it does** — and the one fragile cell
that produced a clean arm was stable.

**⚠️ SUPERSEDING COUNT (09:45Z) — the tempering above is right in DIRECTION but
its numbers are stale, and two follow-up heuristics have since been refuted.**
Read this before quoting "2 of 14".

Sixteen further arms have run since, each with a pre-committed sha256 gate and a
control that had to reproduce the recorded row(s) exactly before any offset was
read. Current standing, over **17 re-partitioned legs**:

- **4 of 17 have moved.** (`gdx_pullback_1d` ×2 draws, `iaum_pullback_1d`,
  `ict_scalp_xrp_15m`, `trend_donchian_eth`.)
- 🔴 ~~**Every leg that moved was flagged; no unflagged leg has moved.** Zero
  false negatives in 17.~~ **REFUTED at 09:41Z by the 4h donchian round** — see
  § "🔴 The 4h donchian round". `trend_donchian_ada_4h` moved from slack **+7**
  and `trend_donchian_eth_4h` from **−5**, both outside the flag, while the
  flagged `xrp_4h` held. Struck rather than deleted because it was quoted to the
  operator before it was refuted.
- ⚠️ ~~**The flag does not predict boundary sensitivity** … flagged 6/11 vs
  unflagged 2/4, p = 0.66.~~ **OVERSTATED — corrected 10:05Z.** That comparison
  pooled legs measured at *different numbers of draws* (7 for `gdx`/`iaum`, 4 for
  the rest); more draws is more chances to move, so it was not a valid
  comparison, and its unflagged denominator was **four**. Redone at matched draws
  (off0/4/8, 17 legs): flagged **3/6 = 50%**, unflagged **3/11 = 27%**, Fisher
  **p = 0.34** — the predicted direction, **not** statistically established.
- **The slack-blind round is the reason that changed.** A round with **zero**
  flagged legs, chosen by a rule that ignores margin, moved **1 of 7** — the
  lowest rate of any round screened. See § "The SLACK-BLIND 2h round".
- **The one mover flipped on the AUC bar, not a fold bar** (`0.5427` vs `0.55`),
  and its control clears that bar by **+0.0006** — already on record as the
  thinnest margin in the corpus. The gate has two independent failure terms and
  the flag measures one, so that is a false negative of a *one-term* criterion,
  not proof fragility is unpredictable.
- **What stands: 6 of 17 legs (35%) moved at three matched draws**, and two
  genuine false negatives remain unexplained (`ada_4h` at slack +7, `eth_4h` at
  −5, both far from both bars). Quote 35%, not the earlier 53%, which mixed draw
  counts.

**Two ways of triaging the flagged population down to a shorter list have been
measured and BOTH fail:**

| heuristic | refuted by |
|---|---|
| rank by **AUC spread** | `trend_donchian_eth` flipped with its arm's AUC at `0.6077` vs a `0.6079` control — flat to three decimals. Its 0.0086 spread is *tighter* than `ict_scalp_sol_15m`'s 0.0088, which held. |
| rank by **slack** (proximity to the bar) | `trend_donchian_eth_prop` at slack **0** — literally at the bar — is unanimous across four draws, while its sibling `trend_donchian_eth` at slack **+2** moved. 1 of 3 slack-0 cells has moved. |

**No third heuristic is offered.** Two causal stories have already been advanced
and retracted in this document (sample size at 07:00Z; bar-proximity here), each
on evidence that looked sufficient when written. The supported statement is
narrow: **slack identifies which cells are exposed and nothing yet ranks them, so
a flagged cell is re-measured rather than reasoned about.** A screen is 4 arms ×
~45 s and covers every leg of a pooled round at once, so that recommendation is
cheap rather than cautious.

**One mechanism worth carrying into any reading of these tables:** `u` is not
constant across arms (measured 24, 24, 23, 23 on one leg), and the gate bar is
`2u` — so the bar moves between draws, 48 → 46. A leg can change verdict with its
`beats_*` counts unchanged. Compare each arm against **its own** bar.

**⚠️ AND A FOURTH FINDING LANDED AT 04:50 THAT OUTRANKS ALL THREE — read
§ "ROOT CAUSE" at the end.** Chasing why nine legs failed their control turned up
a defect in the harness itself: **an E1 verdict depends on the ORDER the legs were
typed on the command line.** `--legs` order becomes the row order in
`rows.jsonl`, which becomes the tie-break in a *stable* sort over a `bar_t` that
is massively tied (on a 2h family every leg entering on the same bar ties), which
moves fold membership. Confirmed end-to-end: two runs of the same seven legs in
different orders produced identical trade counts, identical total rows and an
identical 43×50 fold shape, yet **8 of 43 folds differ**, AUC moved up to
**0.0331**, and two legs *lost a usable fold*.

That is roughly **two-thirds of the 0.0515 median dispersion this entire study was
built to measure — from a nuisance parameter nobody declared.** It also means the
"control MISMATCH" partition below is not measuring experiment comparability at
all; it is measuring whether the leg order happened to match. Filed
`BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER`; no verdict
re-graded on it, and no fix applied, because every candidate fix changes recorded
numbers.

**What was NOT done, deliberately:** no matrix status flipped, no gate changed,
no cell re-graded. Coverage unchanged at **373/376 = 99.2%**. Both are Tier-3
and queued for the operator, with options written out and none taken.

---

## The control PASSES — twice

The one stop condition: offset 0 must reproduce the six recorded `mean_auc`
values, or the arms are not comparable and **no dispersion number is reported**.

| leg | required | run 1 off0 | run 2 off0 | |
|---|---|---|---|---|
| gdx_pullback_1d | 0.6337 | 0.6337 | 0.6337 | ✅ |
| gld_pullback_1d | 0.5277 | 0.5277 | 0.5277 | ✅ |
| iaum_pullback_1d | 0.5525 | 0.5525 | 0.5525 | ✅ |
| ief_pullback_1d | 0.5337 | 0.5337 | 0.5337 | ✅ |
| slv_pullback_1d | 0.4895 | 0.4895 | 0.4895 | ✅ |
| tlt_pullback_1d | 0.5300 | 0.5300 | 0.5300 | ✅ |

Six of six, exact, on both independent runs. `--fold-offset 0` is byte-for-byte
the old partition — asserted at unit level by
`test_offset_zero_is_byte_for_byte_the_old_behaviour`, now confirmed end-to-end
on real data, including `gdx`'s `u = 7`.

## The primary — 5 arms, offsets 0 / 6 / 12 / 18 / 24

This is the arm set the pre-registered thresholds were written for ("per leg,
over the 5 arms"). Relay #9378 launch, #9379 readout.

| leg | off0 | off6 | off12 | off18 | off24 | **spread** | u | n_oos |
|---|---|---|---|---|---|---|---|---|
| gdx_pullback_1d | 0.6337 | 0.6694 | 0.6507 | 0.6215 | 0.6036 | **0.0658** | 7·8·8·8·8 | 81 |
| gld_pullback_1d | 0.5277 | 0.5166 | 0.5233 | 0.5378 | 0.5108 | 0.0270 | 11 | 128 |
| iaum_pullback_1d | 0.5525 | 0.5307 | 0.5460 | 0.5400 | 0.4903 | 0.0622 | 4 | 30 |
| ief_pullback_1d | 0.5337 | 0.5529 | 0.5606 | 0.5870 | 0.5365 | 0.0533 | 11 | 67 |
| slv_pullback_1d | 0.4895 | 0.5027 | 0.5008 | 0.5199 | 0.5107 | 0.0304 | 11 | 160 |
| tlt_pullback_1d | 0.5300 | 0.4855 | 0.4924 | 0.4804 | 0.5080 | 0.0496 | 11 | 84 |

Every spread was recomputed from the printed AUCs rather than taken from the
relay's own spread column; all six agree.

### **Median spread = 0.0515** · max **0.0658** (`gdx`)

By the pre-registered rule that is the **top band**: *"boundary placement alone
can move a verdict across the 0.55 bar, and every AUC-alone `honest_negative` in
the matrix is a decision taken inside that noise."*

**It clears that threshold by 0.0015, and I am not going to present that as a
clean result.** Two things sit against it, and both are visible in the table
above:

1. **Drop `gdx` and the median is 0.0496 — below the line, in the middle band.**
   `gdx` is the one leg whose `u` is not constant across arms (7 at offset 0, 8
   at the other four), so its offset-0 AUC is an average over one fewer fold than
   its siblings'. That is not a clean like-for-like, and it happens to be the leg
   carrying the largest spread.
2. **The median of six is a weak median**, and both candidate values (0.0515,
   0.0496) sit within 0.0015 of the threshold.

**So the honest reading is that this family's median dispersion lands *on* the
0.05 boundary and the measurement cannot resolve which side of it.** The
pre-registered headline is 0.0515 and I report it as the headline because that
is what was committed to — but the band it selects is decided by one leg, and
that has to travel with the number.

### What the invariant check caught

Committing "`u` constant across arms" as an explicit assertion was worth it. It
flagged `gdx` immediately. The partition itself is stable — four legs read
exactly `u = 11` on all five arms — so the 11 blocks of 50 held; what moves is
how many of those folds contain enough `gdx` trades to score. That is a
*consequence* of boundary placement rather than a separate treatment, so it is
not a confound of the same kind as the discarded 30/40 arms. It is still a
contaminant of that one leg's spread, and it is why the second median is
reported rather than buried.

### `iaum` — the pre-registered watch, and it fires decisively

Item 5 named this leg in advance: graded `candidate` at offset 0 on a **0.0025
margin** over the 0.55 bar. Verdicts read directly from each arm's
`e1_report.json`, not inferred from the AUC:

```
off0    0.5525   candidate          <- the recorded matrix verdict
off6    0.5307   honest_negative
off12   0.5460   honest_negative
off18   0.5400   honest_negative
off24   0.4903   honest_negative
```

**One of five boundary draws produces the grade.** The other four terms of the
gate (`u >= 2`, both fold majorities) pass at every offset — recomputed
independently from the round data, reproducing all six recorded verdicts exactly
— so `mean_auc` is the term doing the work, and re-drawing the boundary is
enough to take it away.

This is the top band's first clause demonstrated rather than argued: **boundary
placement alone moves a verdict across the 0.55 bar.**

### The second clause of that band is vacuous on this family — stated, not hidden

"Every AUC-alone `honest_negative` is a decision taken inside that noise" needs
AUC-alone negatives to exist. Recomputing all four gate terms per leg:

| leg | failing terms |
|---|---|
| gdx | `beats_hard` only |
| gld | auc + `beats_actual` + `beats_hard` |
| ief | auc + `beats_actual` |
| slv | auc + `beats_actual` + `beats_hard` |
| tlt | auc + `beats_actual` + `beats_hard` |

**None of the five negatives fails on AUC alone.** Four fail AUC *and* at least
one fold-majority term, and `gdx` fails only `beats_hard` — consistent with
[`m20-exit-head-binding-term-2026-08-14.md`](./m20-exit-head-binding-term-2026-08-14.md),
which measured `beats_hard` as the sole failing term in 6 of 7 single-term
failures across 33 rounds. A leg failing AUC by 0.02 *and* missing a fold
majority by 4 folds is not re-graded by a 0.05 AUC wobble.

An earlier revision of this memo concluded from that table that "the dispersion
bites on the `candidate` side and essentially not at all on the negative side."
**That is falsified and withdrawn** — see the verdict grid below. It was written
from AUC values before per-arm *verdicts* were extracted, and the leg that breaks
it is `gdx`, whose single failing term is `beats_hard`, not AUC.

## The verdict grid — post-hoc, not pre-registered, and the sharper result

The pre-registered statistic is the AUC spread above. Once relay #9380 emitted
each arm's **verdict** the direct question became answerable, so it is reported
here as an explicit **post-hoc addition** with no pre-registered reading attached.
It is not a selected view: it is the complete 6 legs × 7 pure-boundary offsets
grid, every cell.

`C` = `candidate`, `n` = `honest_negative`. Offsets 0 · 6 · 10 · 12 · 18 · 20 · 24
(the 0/10/20 and 0/6/12/18/24 sets share offset 0, which is one partition, counted
once).

| leg | recorded | 0 | 6 | 10 | 12 | 18 | 20 | 24 | disagrees with recorded | single failing term? |
|---|---|---|---|---|---|---|---|---|---|---|
| gld | `honest_negative` | n | n | n | n | n | n | n | **0/7** | no — 3 terms |
| ief | `honest_negative` | n | n | n | n | n | n | n | **0/7** | no — 2 terms |
| slv | `honest_negative` | n | n | n | n | n | n | n | **0/7** | no — 3 terms |
| tlt | `honest_negative` | n | n | n | n | n | n | n | **0/7** | no — 3 terms |
| **gdx** | `honest_negative` | n | n | **C** | n | n | **C** | n | **2/7** | **yes — `beats_hard`** |
| **iaum** | `candidate` | C | n | n | n | n | n | n | **6/7** | **yes — `mean_auc`** |

**Four of six verdicts are unanimous across every boundary draw. The two that are
not are exactly the two sitting on a single-term margin, and they move in opposite
directions.** `iaum` is a recorded pass that survives 1 of 7 draws; `gdx` is a
recorded *negative* that reads `candidate` on 2 of 7 (and on both non-pooled arms,
30 and 40).

Two things follow that the AUC spread alone could not have shown:

1. **Boundary placement moves `beats_hard`, not just AUC.** That matters more than
   the AUC finding, because `beats_hard` is the programme's binding term —
   [`m20-exit-head-binding-term-2026-08-14.md`](./m20-exit-head-binding-term-2026-08-14.md)
   measured it as the sole failing term in 6 of 7 single-term failures across 33
   rounds. The term that decides most cells is itself boundary-sensitive.
2. **Stability tracks MARGIN, not sample size.** `slv` is unanimous on 154–160 OOS
   trades; `gld` is unanimous on 128–131; `ief` is unanimous on 62–67. `gdx` flips
   on 81–86. A leg is stable here because it fails several terms decisively, not
   because its book is large — so "n_oos is healthy" is not evidence a verdict is
   boundary-robust.

### Per-term failure across the 7 pure arms — and a qualifier on the binding term

`F` = term fails at that offset, in offset order 0 · 6 · 10 · 12 · 18 · 20 · 24.

| leg | `auc > 0.55` | `beats_actual` | `beats_hard` | verdict |
|---|---|---|---|---|
| gdx | `.......` | `.F.FF.F` | `FF....F` | `nnCnnCn` |
| gld | `FFFFFFF` | `FFFFFFF` | `FFFFFFF` | `nnnnnnn` |
| iaum | `.FFFFFF` | `.FFFFFF` | `.FFFFF.` | `Cnnnnnn` |
| ief | `F.....F` | `FFFFFF.` | `.F..FF.` | `nnnnnnn` |
| slv | `FFFFFFF` | `FFFF.FF` | `FFFFFF.` | `nnnnnnn` |
| tlt | `FFFFFFF` | `FFFFFFF` | `FFFFFF.` | `nnnnnnn` |

**Every one of the 60 arms' recorded verdicts is reproduced exactly by
recomputing the four gate terms from the committed row** — zero disagreements.
That is the check that the artifact and this reading of the gate are both sound,
and it is why the grid above can be trusted at cell level.

**A qualifier on the binding-term finding.** Across these 60 arms the failure
counts are `beats_actual` 48 · `auc` 42 · `beats_hard` 40, and as the *sole*
failing term: `beats_actual` 4 · `auc` 2 · `beats_hard` 2. So in **this family**
`beats_actual` is the most frequently failing term, not `beats_hard`. That does
not contradict
[`m20-exit-head-binding-term-2026-08-14.md`](./m20-exit-head-binding-term-2026-08-14.md)
— that measured 33 rounds across many families and found `beats_hard` the sole
failure in 6 of 7 single-term failures — but it does bound it: **the binding term
is family-dependent, and the 1d pullback family is not where `beats_hard` binds.**
Anyone carrying that finding forward should carry this sentence with it.

Note also `ief`: its AUC clears the bar at 5 of the 7 offsets and it is never a
`candidate`, because `beats_actual` fails at 6 of 7. A leg can be well above the
AUC bar on most partitions and still be a stable, correct negative.

## The 3-arm cross-check reconciles — and my earlier band call on it was wrong

Run 1's arms 0/10/20 (the only pure boundary shifts in that run) gave:

| leg | off0 | off10 | off20 | spread |
|---|---|---|---|---|
| gdx | 0.6337 | 0.6513 | 0.6140 | 0.0373 |
| gld | 0.5277 | 0.5167 | 0.5394 | 0.0227 |
| iaum | 0.5525 | 0.5277 | 0.5231 | 0.0294 |
| ief | 0.5337 | 0.5602 | 0.5860 | 0.0523 |
| slv | 0.4895 | 0.4917 | 0.5157 | 0.0262 |
| tlt | 0.5300 | 0.5038 | 0.4749 | 0.0551 |

Median **0.0333**. An earlier revision of this memo assigned that to the
0.01–0.05 band. **That was an error and is withdrawn.** `max − min` is an order
statistic: it can only grow as arms are added, so a 3-arm spread is not
comparable to thresholds written for 5 arms.

The correction is also the reconciliation. Taking all ten 3-arm subsets of the
**primary** data:

```
C(5,3) = 10 subsets   min 0.0202   median 0.0326   max 0.0400
```

The independent 3-arm run measured **0.0333**, sitting essentially on that
median. **The two runs do not disagree — the 3-arm/5-arm gap is entirely the arm
count.** That is a stronger cross-check than matching medians would have been,
because it reproduces the *distribution* the primary implies.

It also exposes a real gap in the pre-registration: it fixed thresholds on a
statistic that is not invariant to the number of arms, without saying so. The
thresholds are valid for the 5-arm primary and for nothing else.

### Arms 30 / 40 — labelled, not pooled

Recorded in the pre-registration's amendment: these also dropped a fold
(`u = floor((N−k)/b) − 1` holds `u = 11` only for `k ≤ 29` at `N = 629, b = 50`),
so they answer "boundary shift **plus one fewer fold**" and are excluded from
every number above. For the record: `iaum` reads 0.5791 at offset 30 — its
highest of any arm — against 0.4903 at offset 24. Across all seven measured
offsets it spans 0.4903–0.5791, straddling its own gate bar in both directions.

## What this does NOT do — committed in advance, honoured

- **No matrix status is flipped, in either direction.** `iaum`'s `candidate` is
  *not* re-graded here. Headline coverage is unchanged at **373/376 = 99.2%**
  (verified by running `m20_coverage_rollup.py`, not by counting).
- **No gate change is proposed.** Changing a gate after seeing which term it
  fails on is how a gate stops meaning anything.
- `BL-20260814-EXIT-HEAD-AUC-MOVES-MORE-THAN-ITS-OWN-GATE-MARGIN-ACROSS-A-ONE-DAY-RE-MEASUREMENT`
  is **narrowed, not closed.** The one-day re-measurement moved `mean_auc` by
  −0.110 on its worst leg; re-partitioning the same trades moves it a median
  0.0515 over five draws. Boundary placement is a measured contributor of roughly
  half that movement, so the other candidates (TP geometry, pool size) still own
  a remainder.

## Queued for the operator (Tier-3, not acted on)

**Two cells, not one.**

**(a) `iaum_pullback_1d` / `exit_head_ml` — a recorded `candidate` that survives 1
of 7 boundary draws.** It also carries `n_oos = 30` and `u = 4`, the thinnest book
of the six. Three options, none taken here:

1. Leave it. It passed the gate as written; the gate does not claim
   boundary-invariance.
2. Re-grade it against a boundary-averaged AUC. This is a **gate change** and
   would be proposed *after* seeing which term it fails on — the thing item 3
   forbids doing quietly. It would need its own pre-registration.
3. Treat `u >= 2` as too permissive at `n_oos = 30`. Independent of this
   measurement, and the one worth asking about: `iaum` clears a four-term
   conjunction on four folds of thirty trades.

**(b) `gdx_pullback_1d` / `exit_head_ml` — a recorded `honest_negative` that reads
`candidate` on 2 of 7 pure draws and on both non-pooled ones.** This direction is
the more uncomfortable of the two: a *negative* is a decision not to pursue a
lever, and nothing re-examines it. The matrix status is **not** changed here — a
verdict that holds on 5 of 7 draws is not thereby wrong, and re-grading a cell
because two alternative partitions liked it is precisely the selection this whole
programme refuses. What is queued is the **question**: `gdx` fails on `beats_hard`
alone, and `beats_hard` is a fold-majority count, so it is mechanically the term
most exposed to where the folds fall. Whether single-term `beats_hard` negatives
across the matrix deserve a boundary-robustness check before being treated as
settled is an operator call, and it is a larger population than this one cell.

## How exposed is the negative column? — sized, same night, pure read

`gdx` prompts the obvious question: how many recorded negatives rest on a single
gate term, i.e. are in the position `gdx` was in when it flipped? The committed
rounds carry every input, so this needs no harness run.

**Population, stated: the 33 rounds in `m20-exit-head-rounds.jsonl` — 19 of them
`honest_negative`.** This is *not* the matrix's 281-cell negative column, which
spans all 8 levers; it is the `exit_head_ml` legs actually re-measured.

| failing gate terms | negatives |
|---|--:|
| **exactly 1** | **7 (37%)** |
| 2 | 6 |
| 3 | 6 |

Of the 7 single-term negatives, **6 fail on `beats_hard`** and 1 on `mean_auc`:
`gdx_pullback_1d` · `gld_pullback_1h` · `ict_scalp_eth_15m` · `sol_pullback_2h` ·
`trend_donchian_eth_4h` · `trend_donchian_sol_4h` (all `beats_hard`), and
`tlt_pullback_1h` (`mean_auc`).

Two checks before reading anything into that. Recomputing the four gate terms
from the stored fields **reproduces all 33 recorded verdicts, zero
disagreements** — as it did for the 60 arms. And the split reconciles *exactly*
with [`m20-exit-head-binding-term-2026-08-14.md`](./m20-exit-head-binding-term-2026-08-14.md),
which reported `beats_hard` as the sole failure in **6 of 7** single-term
failures. Same 6-of-7: that memo had already sized this population; what is new
is that one of its members has now been shown to flip under re-partitioning.

**So the exposure is 7 cells, one of which is measured fragile.** That is a
tractable follow-up, not an alarm — and deliberately no more is claimed. `gdx`
flipping does not make the other six wrong, `beats_hard` being the common term is
expected (it is a count over folds, so it is the most boundary-exposed term by
construction), and six of these legs have never been offset-tested at all.

## The candidate column is thinner than the negative column — arithmetic, whole corpus

`gdx` raised the question empirically. The committed rounds answer it *without a
harness*, because the fold-majority terms have exact arithmetic: the gate is
`beats * 3 >= u * 2`, so one fold changing side moves `beats` by 1 and the slack
by **3**. **A slack of 0, 1 or 2 means a single fold flip changes the verdict.**
That is not an extrapolation from the dispersion measured above — it holds for
any family, any `u`.

**Population: the 33 committed rounds — 14 `candidate`, 19 `honest_negative`.**

| | one fold flip from the opposite verdict |
|---|---|
| **candidates** | **8 of 14 (57%)** |
| negatives | 4 of 19 (21%) |

The fragile candidates, with slack `(beats_actual, beats_hard)`:

| leg | AUC margin | slack | what is thin |
|---|--:|---|---|
| `ict_scalp_sol_15m` | +0.0308 | `(+0, +3)` | `beats_actual` **at the bar** |
| `ict_scalp_xrp_15m` | +0.0181 | `(+3, +0)` | `beats_hard` **at the bar** |
| `trend_donchian_eth_prop` | +0.0638 | `(+12, +0)` | `beats_hard` **at the bar** |
| `iaum_pullback_1d` | **+0.0025** | `(+1, +1)` | all three — *and it flipped* |
| `trend_donchian_xrp_4h` | +0.1054 | `(+7, +1)` | `beats_hard` |
| `trend_donchian_eth` | +0.0579 | `(+2, +2)` | both majorities |
| `ict_scalp_sol_5m` | +0.0684 | `(+20, +2)` | `beats_hard` |
| `eth_pullback_2h` | **+0.0006** | `(+7, +4)` | AUC, by six ten-thousandths |

**The criterion flags correctly on the one leg actually re-partitioned — but it
does not establish the mechanism, and I checked rather than assumed.**
`gdx_pullback_1d` sits at `beats_hard` slack **−2**, so the arithmetic says it is
within one fold of passing; re-drawing the boundary did pass it, on 2 of 7 draws.
Predicted fragile, observed fragile.

What the arms do **not** show is a single fold changing side:

```
off0    u=7   beats_hard=4   slack −2   honest_negative
off10   u=8   beats_hard=6   slack +2   candidate
```

`u` moved 7→8 *and* `beats_hard` moved 4→6. So the observed transition is larger
than the one-fold move the criterion is built on. The criterion is validated as a
**flag** — it selected the leg that turned out to be unstable — and not as a
model of how the instability happens. (`gdx` is also the leg whose `u` is not
constant across arms, which is why it is excluded from the second median above;
it is doing awkward duty in both directions.) `n = 1`, and it is the only
empirical anchor this criterion has.

**What this does and does not say.** "One fold flip away" is a statement about
**robustness, not correctness**: none of these verdicts is thereby wrong, and
several may be perfectly stable under re-partitioning — `eth_pullback_2h` sits
at +0.0006 on AUC but has comfortable slack on both majorities, so it is thin in
a different way than `ict_scalp_sol_15m`, which is exactly at its `beats_actual`
bar. The fold-majority half is pure arithmetic; the AUC half (`< 0.005`) is a
threshold I chose against the dispersion measured above, and is therefore the
softer of the two.

**The reframing matters more than the count.** The night began with a worry about
the *negative* column and ended measuring that the *candidate* column is roughly
**2.7× more exposed** — and candidates are the cells that would justify shipping
a lever onto a live leg. `trend_donchian_xrp_4h` appears here on its
`exit_head_ml` cell; that is a **different lever** from the `trail_decay` change
in PR #9257 on the same leg, with no interaction, and is flagged only so the two
are not conflated.

Nothing is re-graded here. Which of these eight actually flip is what an offset
screen measures.

### Screen coverage — what is measured, what is not, and what it would take

Recorded so the next session does not re-derive it. A screen must run the
**whole family** (see § Reproduce), so one round covers every fragile cell in
that family at once.

| cell | family / tf | status |
|---|---|---|
| `iaum_pullback_1d` **(C)** · `gdx_pullback_1d` **(N)** | pullback 1d | ✅ done — the dispersion run |
| `trend_donchian_xrp_4h` **(C)** · `avax_4h` **(N)** · `sol_4h` **(N)** | donchian 4h | 🔄 running |
| `eth_pullback_2h` **(C)** | pullback 2h | 🔄 running |
| `gld_pullback_1h` **(N)** | pullback 1h | 🔄 running |
| `trend_donchian_eth` **(C)** · `trend_donchian_eth_prop` **(C)** | donchian 1h | ❌ **one round covers both** |
| `ict_scalp_sol_15m` **(C)** · `ict_scalp_xrp_15m` **(C)** | scalp 15m (`per_leg`) | ❌ two single-leg rounds |
| `ict_scalp_sol_5m` **(C)** | scalp 5m (`per_leg`) | ❌ heaviest — 1150 OOS trades |

After the running screen: **all 4 fragile negatives covered, 3 of 8 fragile
candidates.** The remaining five candidates need **three more rounds** — donchian
1h (two cells for one round, the best ratio left), scalp 15m ×2, scalp 5m ×1.

That the negatives finish first is an artefact of when each finding landed, not a
judgement that they matter more — by the reasoning above they matter less.

**The corrected design is validated.** The first arm back reproduces its recorded
round exactly — `ict_scalp_eth_15m`, arm `u=11 auc=0.6083` against recorded
`u=11 auc=0.6083` — so running the whole family (here `per_leg`, so one leg) is
what makes an arm comparable, and the off0 control now runs automatically inside
the readout rather than depending on me to remember it.

*One caveat on reading that readout:* its first version printed **"UNANIMOUS"**
for a leg with a **single** arm. Unanimity over one draw is not evidence of
anything, and a leg with one arm has not been screened at all — the same
overstating-label class this document keeps finding. The readout now refuses to
say "unanimous" below two arms and always prints the arm count beside the claim.

## This is not an `exit_head_ml` quirk — the same bar governs the other levers

The one-flip criterion was derived on the E1 gate. It transfers, and I checked
rather than assumed it.

The sweep corpus (`m20-sweep-corpus.jsonl`) grades the *other* lever families —
`trail_decay`, `stale_stop`, `giveback_stop` — through a walk-forward gate on
`wf_wins / wf_usable`. Deriving that threshold **empirically** from the 78 wf
cells rather than taking the "5/6" folklore:

| `wf_wins`/`wf_usable` | verdicts |
|---|---|
| 6/6 · 5/6 · 4/6 · 3/4 | all PASS (or `path_b_wf_pass`) |
| 3/6 · 2/6 · 2/4 · 1/6 · 1/4 · 0/6 | all fail |

That is exactly `wf_wins * 3 >= wf_usable * 2` — **the same 2/3 majority as the
E1 fold terms**, fitting **78 of 78 rows with zero mispredictions**. So one
walk-forward fold changing side moves the slack by 3, and the identical
arithmetic applies:

| | one fold flip from the opposite verdict |
|---|---|
| **passing wf cells** | **24 of 49 (49%)** |
| failing wf cells | 15 of 29 (52%) |

**Twenty-three of those 24 sit at `4/6` — slack exactly zero.** That is not a
coincidence, and the distribution says why. Over the 75 cells with six usable
folds:

```
0/6   2   2.7%  ##
1/6   4   5.3%  ####
2/6   7   9.3%  #######
3/6  14  18.7%  ##############
4/6  23  30.7%  #######################   <- THE PASS THRESHOLD
5/6  16  21.3%  ################
6/6   9  12.0%  #########
```

**The gate's bar sits exactly on the MODE of the win distribution** (4/6, 30.7%
of all cells), with the mean at 3.81/6 = 0.636 just under the 0.667 threshold. A
threshold placed at the peak of the statistic it thresholds puts the *largest
single group of cells* at zero slack — which is the mechanical reason the
exposure is ~half rather than a few percent.

To be fair to the gate: a discriminating threshold often *belongs* near the
middle of a distribution, and this one separates cleanly (78/78). The problem is
not the placement in the abstract — it is that each of these verdicts is a
ship-or-don't decision about **one leg**, so "half the passes are one fold from
not being passes" is a statement about how much individual decisions can bear.

**So the fragility is structural to the 2/3 majority rule, not a property of the
exit-head programme.** Two independent gates, two different harnesses, two
different lever families, the same arithmetic and the same order of exposure
(49% / 57%).

### The one reassuring reading, and it is about the live change

**PR #9257's cell — `trend_donchian_xrp_4h` / `decay_arm2R_t2.5` — passes at
`wf 5/6`, slack `+3`. It SURVIVES one fold flip.** It is not in the fragile
population, on either of the two runs that measured it. That is a genuine point
in favour of the queued Tier-3 merge, and it is the reason to state this finding
beside that decision rather than only as a caveat somewhere else.

## The screen finished — and it TEMPERS the fragility reading

16 arms over four (family, tf) groups, 17 legs, all `exit=0 skips=0` (relay
#9393). The result is not what the arithmetic flag predicted, and the honest
partition is by whether each arm's **off0 control** reproduced its recorded round.

| | verdict unanimous over 4 draws | verdict changes |
|---|--:|--:|
| **control OK** (8 legs) | **8** | **0** |
| control MISMATCH (9 legs) | 4 | 5 |

**Every leg whose arm is provably comparable is unanimous. Every one of the five
verdict changes sits in a leg that also fails its control.** So on this run the
clean evidence says verdicts are *stable* under re-partitioning, and the changes
cannot yet be attributed to the boundary — something else differs in those arms.

### The denominator that actually matters

Pooling **only** legs with a proven-clean control, across both runs:

| | legs | verdict changes |
|---|--:|--:|
| 1d dispersion (7 draws each) | 6 | **2** — `gdx`, `iaum` |
| unanimity screen (4 draws each) | 8 | 0 |
| **total** | **14** | **2 (14%)** |

**Two of fourteen clean-control legs show verdict instability**, and both are from
the arm set with **seven** draws rather than four. More draws is more chance to
*observe* a change, so **14% is a lower bound on instability, not an estimate of
it** — the 4-draw legs are less likely to have revealed one even if fragile.

### What this does to the one-flip finding

It does **not** refute it — the arithmetic is unchanged, and `8 of 14` candidates
still sit one fold from failing. What it changes is the inference from flag to
behaviour: **being one flip from the bar predicts that a verdict *can* move, not
that it *does*.** Of the fragile cells this screen was built to test, exactly one
produced a clean arm — `gld_pullback_1h`, a fragile negative — and it was
**unanimous across four draws**. One clean observation, and it says stable.

That is the opposite of the direction I would have guessed at 02:15, and it is
why the screen was worth running before proposing anything to the operator.

### The control failures are their own finding, and are being investigated

Nine legs' arms do not reproduce their recorded round.

⚠️ **This sentence used to end "…while `u` matches exactly", and that was wrong**
(corrected 04:40Z from relay #9402, which read the arms' per-leg fold counts
rather than restating the claim). On the `2h` group `usable_folds` matches on 5
of 7 legs and **differs on two** — `avax` 42 vs 43, `sol` 41 vs 43 — and those
are among the legs whose AUC moved. The error is the same accessor mistake I
made on `provenance`: the field is `usable_folds`, I had been reading `u`, and
every row returned `None`, so "matches exactly" was `None == None` on both
sides. A reassurance built on comparing two nulls is worse than no reassurance,
because it closes off the question. **`usable_folds` differing is now the single
most informative fact in this section** — see below.

**Data growth is REFUTED** (relay #9395): `n_oos` is **identical on all 17 legs**,
`d_n = +0`, including every mismatch. Same trade population, different AUC. That
was the leading hypothesis and it is dead.

What the numbers look like:

| | legs | AUC delta vs recorded |
|---|--:|---|
| control OK | 8 | **exactly 0.0000** |
| control MISMATCH | 9 | 0.0009 → **0.0331** |

Two features argue against plain training noise. The reproducing legs match **to
the digit**, not approximately. And within a **single pooled 2h arm** — one model,
one training call — 3 legs reproduce exactly while 4 do not; a globally noisy fit
would have moved all seven.

**Training is DETERMINISTIC** (relay #9396): the `4h off0` arm re-run
byte-identically reproduced **5 of 5 legs to exactly 0.0000**. So the off0 control
is a valid equality check — no tolerance needed — and the mismatched rows were
produced by a *different configuration*, not by a noisy fit.

### A wrong claim of mine, retracted in place

I then asserted that the rounds file "carries no run id and no timestamp, so a row
cannot be traced to the run that produced it", and filed a backlog row saying so.
**That was false.** `m20_exit_head_round.py:413` stamps
`provenance = f"round {out.name}; driver-emitted"`, and the committed rows read
e.g. `round eth_denom_4h_20260814T134036Z; relays #9288 launch / #9294 report` —
round directory, UTC timestamp, and relay numbers.

**How I got it wrong matters more than the claim.** I printed the *field names*,
saw no key called `run`/`time`/`stamp`, and concluded absence — while
`provenance` sat in that very list with its value unread. That is RULE ONE
inverted: I read the schema instead of the data, and asserted a negative from a
probe I never showed could find a positive. The backlog row is retracted and
rescoped to what survives — the run identity is *free text*, so a consumer cannot
group or filter by run without parsing prose. Low severity, and not the integrity
gap I filed.

### And reading the field answered the question immediately

| control | producing round |
|---|---|
| **OK** (8 legs) | `pullback_1h_20260814T182245Z` · `scalp_15m_20260814T135244Z` |
| **MISMATCH** (9 legs) | `eth_denom_2h_20260814T130657Z` · `eth_denom_4h_20260814T134036Z` |

Every mismatched row comes from an **`eth_denom_*` round** — the ETH-denominator
investigation, a different experiment — and every reproducing row comes from a
plain family round. The split is by *provenance*, not by leg, family, geometry or
timeframe.

**One piece resisted explanation for four relays — it is now RESOLVED, and the
answer is § "ROOT CAUSE" below.** The elimination sequence is kept in full,
because which hypotheses were killed, and by what, is the part worth reusing:
inside
the single `eth_denom_2h` round, 3 of its 7 legs reproduce exactly under my
full-7-leg re-run and 4 do not. Had that round pooled a different leg set, all
seven should have differed. Three explanations are *ruled out*, not merely
unselected — data growth (`n_oos` identical, `d_n = +0`), training
non-determinism (5/5 legs reproduce byte-identically), and hand-editing of the
evidence (all 7 rows unchanged since commit `306ee999` introduced them, checked
with `git show`).

**A fourth is now ruled out too, and it was the leading one** (relay #9398). The
round's own `e1_report.json` names the legs it pooled:

```
['ada_pullback_2h', 'avax_pullback_2h', 'eth_pullback_2h', 'eth_pullback_prop_2h',
 'htf_pullback_trend_2h', 'sol_pullback_2h', 'xrp_pullback_2h']
```

That is **exactly** the 7-leg set my re-run pooled — same legs, same count. So
"the recorded round pooled a different subset, and `family_pooled` makes a subset
a different measurement" is dead as an explanation. It was the hypothesis the
whole comparability rule was built on, and it does not cover this round.

**What that left was a narrower question** — same legs, same trade population,
deterministic training, unedited rows, and yet four of the seven differ by
0.0009 to 0.0331. At this point every difference-generating input I could *name*
had been measured and excluded, which is exactly the state in which the next
move is to stop hypothesising and read the producer.

**That is what resolved it.** The answer was not a new input but an undeclared
one: the ORDER the legs were passed. See § "ROOT CAUSE: an E1 verdict depends on
the ORDER the legs were typed" — and note that the leg *set* being identical,
established here, is precisely what forced the search onto the leg *order*.

That same read appeared to show the round records **no flags** — `_round_meta`
came back `<<KEY ABSENT>>` from `e1_report.json`, and #9400 confirmed the absence
against a positive control (the same reader on `per_leg`, which returned a dict
of 7). **The absence was real and the conclusion was still wrong: I was reading
the wrong file.** `m20_exit_head_round.py:343` writes
`{"_round_meta": meta, **report}` into **`round_report.json`**, and `meta`
(`:324-340`) carries `legs` in command-line order, `fold_offset`, `target`,
`features`, `tp_cap_pct` and the family list. The round records its flags
completely; I opened the wrong artifact three times, then read the right one at
the wrong nesting depth (`legs` is inside `_round_meta`, not top-level).

**The lesson is about the control, and it is not the one I thought I had
learned.** I added the positive control specifically to avoid repeating the
`provenance` false-absence — and it did not save me, because **a positive control
validates the READER, not the choice of TARGET.** `per_leg` proved my parser
worked on the file I opened; nothing in it could say that file was the wrong one.
Four accessor errors in one night (`provenance`, `u` vs `usable_folds`,
`_round_meta`'s file, `legs`' nesting) share one root: I kept naming a key and
inspecting what came back, instead of printing the object and reading what is
there. The cheap general fix is to dump the whole structure when exploring an
unfamiliar schema, and only then name a field.

### The generalizable rule: check comparability BEFORE launching

The first screen discovered its incomparable arms *after* running 16 of them. The
file can predict that, and this is the check to run first.

**A `family_pooled` re-run can only reproduce legs from ONE round.** So where a
family's committed rows span several runs, control failure is *guaranteed* for
the legs belonging to the other runs. Measured across the whole file by grouping
`(family, tf)` on the round id parsed out of `provenance`:

| family / tf | runs | a single family re-run… |
|---|--:|---|
| pullback 1d · 1h · 2h · trend_donchian 4h | 1 each | reproduces the whole family |
| scalp 15m · 5m | `per_leg` | n/a — each leg is its own round |
| **trend_donchian 1h** | **2** | **must mismatch some legs** |

`trend_donchian 1h` is the **only** offender: `relay #9206` produced
`trend_donchian` / `_eth` / `_sol`, while `relay #9156` produced `_eth_prop` /
`_sol_prop`. That matters concretely — its two fragile candidates,
`trend_donchian_eth` and `trend_donchian_eth_prop`, sit on **opposite sides of
that split**, so one re-run can validate at most one of them.

This is why the second-pass screen targets the two 15m scalp candidates first
(relay #9399): both `per_leg`, both from `scalp_15m_20260814T135244Z`, a round
whose sibling leg already came back control-OK — comparability established in
advance rather than discovered afterwards.

## Limits, stated plainly

1. **This family is the THIN one, so this is an UPPER BOUND** — pre-registered as
   item 1. `u` runs 4–11 here against 26 for `pullback_1h`. The direction that
   travels is the *small*-spread one (it would hold a fortiori for thicker
   families); a large spread on thin legs does not establish that well-powered
   verdicts are unstable. Median 0.0515 is a ceiling on what a thick family would
   show, not an estimate of it.
2. **The arms share trades.** This measures re-partitioning sensitivity, **not**
   sampling error over new data. Different quantities, not conflated.
3. **Five offsets of fifty** is a sample of the partition space, not a census.
4. **Six legs**, and the band assignment turns on one of them.
5. **The statistic grows with arm count** (see the cross-check section). Any
   future comparison must hold the number of arms fixed.
6. **The arms do not score identical trade sets, and "same pool" oversold that.**
   `--fold-offset k` drops the first `k` trades before blocking, so each leg's OOS
   count moves between arms: `gdx` 81–86, `gld` 128–131, `iaum` 30–34, `ief` 62–67,
   `slv` 154–160, `tlt` 82–84 (swings of 2–13% of the smallest). That is intrinsic
   to shifting a boundary at fixed block size rather than a defect — but a reader
   told the arms share a pool could reasonably assume the scored sets are identical,
   and they are not. This surfaced only from the emitted per-arm rows, not from the
   formatted summary table, which is the argument for committing the raw arms.

## The committed arms

[`m20-fold-dispersion-arms.jsonl`](./m20-fold-dispersion-arms.jsonl) — all **60
rows** (6 legs × 10 arms across both runs), emitted by the trainer and verified
**byte-exact against its own sha256** (`41c34c75…`, 18758 chars) rather than
trusted as a transcription.

Two things about how it was produced, because both were defects caught in flight:

- A first emit wrote `"beats_actual": null` / `"beats_hard": null` — my extractor
  guessed key names, and the real ones are `beats_actual_folds` /
  `beats_hard_folds`. Committing those nulls would have put *"we did not look"*
  and *"the value is absent"* into one field, in the artifact whose entire job is
  provenance. Fixed by copying each record verbatim instead of naming keys.
- A second emit was **silently truncated at 34 of 60 rows** by GitHub's comment
  size cap — including its own `reconciles=` line, so the output lost the very
  assertion that would have flagged it. Caught only because the expected row count
  was asserted independently. The nested `per_fold` array was the cause; it is
  omitted here (flagged per row as `per_fold_omitted: true`, and still present in
  each arm's `e1_report.json` on the trainer), and the emitter now prints a
  checksum so a future truncation cannot pass as a complete read.

These rows deliberately live **outside**
`docs/research/m20-exit-head-rounds.jsonl`. That file is the *graded-round*
record, read by a leg-keyed dict (last-wins) and by a pooled flip rate; thirty
arms of six legs would silently change which measurement each leg is judged
against and inflate the denominator. `m20_exit_head_denominator._load_graded_rounds`
now excludes any non-zero `fold_offset` and says which rows it dropped
(`tests/test_rounds_exclude_dispersion_arms.py`, 7 tests, each verified
load-bearing by planting its own regression).

## Reproduce — and the two ways an arm silently stops being comparable

```
scripts/research/m20_exit_head_round.py --fold-offset K --data-dir <repo>/data --legs <FULL family set> --tf <tf>
```

**Always run the off0 control and check it against the recorded round.** Both
failures below produced clean-looking runs with plausible numbers, and both were
caught only by that check.

1. **The leg set must be the round's whole family.** `block_unit: family_pooled`
   means the head trains on the *pooled* family, so running a subset is a
   different measurement, not a cheaper one. A screen launched on just the two
   1h legs of interest returned `gld_pullback_1h` at `u=16 / auc 0.5426` against
   the recorded `u=26 / auc 0.601` — same leg, same flag, incomparable numbers.
   Recover each round's real leg set by grouping
   `m20-exit-head-rounds.jsonl` on `(family, tf)`.
2. **`--data-dir` defaults to `REPO/"data"`**, which inside a git worktree is
   empty. That run "finished" 12 arms in 45 s, every one `exit=1`
   `data_missing`, and wrote `DONE`. Pass the path explicitly and pre-flight it.

**`u` is not free to choose.** The legal offset band is `N mod block_n` wide —
the 1d family held `u = 11` only for `k <= 29` at `N = 629, b = 50` — and `N` is
not known before the round runs. So pick small offsets, then **check `u` per arm
at readout** and report any arm whose fold count moved separately rather than
pooling it.
The flag refuses `--fold-mode=years` with a non-zero offset, refuses
`K >= block_n`, prints the skipped-head count, and is stamped unconditionally
into `_round_meta` so an offset-0 arm is distinguishable from a round predating
the flag. Twelve tests in `tests/test_fold_offset.py`, each verified load-bearing
by planting its own regression.

---

## The fragility flag has a base rate, and it is not constant (04:45Z)

Everything above reports "one fold flip from the opposite verdict" as a single
pooled number. That number is not comparable across rows, and the reason is
arithmetic.

**`usable_folds` spans 4 to 43 across the 33 committed rounds** — more than a
tenfold range. One fold flip is therefore **25.0% of the evidence** on
`iaum_pullback_1d` (`u = 4`) and **2.3%** on `eth_pullback_2h` (`u = 43`). Those
are not the same claim, and § 5 of the operator queue reports them under one
percentage.

Worse, the *flag itself* is likelier to fire at low `u`. Slack is
`3·beats − 2·u`, so it lives on one residue class mod 3 with spacing 3, taking
`u + 1` achievable values; the one-flip band `[−3, 2]` is six consecutive
integers and contains exactly **2** points of any residue class. So the lattice
alone gives a base rate of `2/(u+1)` per term:

| `u` | one flip perturbs | lattice base rate (either term) | observed |
|--:|--:|--:|:--|
| 4 | 25.0% | 64.0% | 1/1 |
| 9 | 11.1% | 36.0% | 2/2 |
| 16 | 6.2% | 22.1% | 3/5 |
| 23 | 4.3% | 16.0% | 4/5 |
| 43 | 2.3% | 8.9% | 1/5 |

**A row with `u = 4` is nine times likelier to be flagged than a row with
`u = 43`, before any evidence about the leg.** Pooling them and quoting one
percentage lets the small-`u` rows set the headline.

### Correcting for it makes the finding STRONGER, not weaker

That was a deflationary check and it failed to deflate. Over all 33 rounds:

| | rows flagged | share |
|---|--:|--:|
| **observed** | 16 / 33 | **48.5%** |
| expected from the fold-count lattice alone | 6.9 / 33 | 20.8% |
| | | **excess +27.6 pp** |

And the excess is **larger where one flip is a smaller perturbation**:

| population | observed | lattice-expected | excess |
|---|--:|--:|--:|
| `u ≤ 11` (one flip = 9–25% of evidence) | 55.6% | 36.9% | **+18.6 pp** |
| `u ≥ 23` (one flip = 2–4% of evidence) | 44.4% | 12.7% | **+31.8 pp** |

So verdicts cluster at the bar most tightly exactly where a flip is cheapest —
the opposite of an artefact of coarse low-fold lattices, which is what I was
testing for.

**State the null honestly, because it is crude.** "Expected" here assumes
`beats` is uniform over `0..u`, which no real gate produces: a *discriminating*
threshold ought to sit where cells are dense, and this memo already argued
exactly that about the walk-forward mode. So the +27.6 pp excess over uniform is
**not** by itself evidence of a defect. What does not depend on the null is the
**differential** — both groups face the same crude assumption, so `u ≥ 23`
carrying a larger excess than `u ≤ 11` is a comparison between like and like.

**What this changes for the operator.** Nothing yet, and deliberately: it is a
qualification of how a number was reported, not a new verdict on any leg. But
queue item 5 should be read with it — "8 of 14 candidates are one flip from
failing" is a pooled figure over rows where a flip means between 2% and 25% of
the evidence, and the correct reading of the sub-population that matters most
(`u ≥ 23`, where a flip is nearly free) is *more* concerning than the pooled
number, not less.

**Reproduce:** the table is computed from `docs/research/m20-exit-head-rounds.jsonl`
alone — `usable_folds`, `beats_actual`, `beats_hard` per row; no trainer call.

---

## ROOT CAUSE: an E1 verdict depends on the ORDER the legs were typed (04:50Z)

The open item above is closed, and the answer is worse than "these two rounds
were incomparable".

**The chain, each link read in the code and then confirmed on the artifacts:**

| # | link | evidence |
|--:|---|---|
| 1 | `--legs` is split and iterated in **command-line order** | `m20_exit_head_round.py:157` — `for leg in a.legs.split(",")` |
| 2 | each leg's emit file is appended to `emits` in that order | same loop, `:236` |
| 3 | the dataset builder reads those paths in list order and `extend`s, so `rows.jsonl` is written in **leg-concatenation order** | `build_exit_head_dataset.py:583` → `:634` → `:730` |
| 4 | folds are cut from `sorted(h_trades.items(), key=bars[0]["bar_t"])` — and Python's sort is **stable**, so **ties inherit that file order** | `train_exit_head.py:518` |
| 5 | on a 2h family every leg entering on the same bar has an **identical `bar_t`**, so tie groups are large — one per bar, spanning all 7 legs | — |

**And the two runs did pass the legs in different orders** (relay #9406, read from
each round's own `_round_meta`):

```
RECORDED : eth, eth_prop, sol, xrp, ada, avax, htf     <- hand-typed
off0 ARM : ada, avax, eth, eth_prop, htf, sol, xrp     <- alphabetical (my re-run sorted them)
```

Same seven legs. Different permutation. That is the entire difference between the
two runs, and it predicts **every** observation that had accumulated:

| observation | explained |
|---|---|
| identical `harness_trades` (2220) and total rows (71199) | same trades, only reordered |
| identical fold shape (43 folds × exactly 50 trades) | blocking is by trade count |
| **35 of 43 folds byte-identical**, 8 shifted | ties are local to a bar, so most boundaries never move |
| small AUC deltas (0.0009 → 0.0331) on the shifted legs | different fold composition, same data |
| `ada` and `eth_pullback_prop_2h` reproduce at **+0.0000** | their trades never sat in a moved tie group |
| `avax` 42 and `sol` 41 usable folds vs 43 | a fold's per-leg OOS count crossed `min_oos_trades_floor: 25` |
| the determinism test passing **5/5** | it re-ran the *same command*, so the *same* order |

### Why this matters more than the mismatch it explains

**A nuisance parameter — the order arguments were typed — moves `mean_auc` by up
to 0.0331.** The deliberate boundary re-draw this whole memo measures has a median
spread of **0.0515**. So permuting the leg list produces AUC movement of the same
order as the effect the study was designed to detect — roughly **two-thirds of
it** — and nothing anywhere records or controls for it.

That reframes several things above:

- **The "control MISMATCH" partition was not measuring comparability.** Those nine
  legs were not different experiments; they were the *same* experiment with a
  permuted tie-break. The memo's clean/dirty split is really an
  order-matched/order-mismatched split.
- **The comparability rule I committed earlier is insufficient, and now
  demonstrably so.** It says to check the round id before launching. Matching the
  round id does not match the leg *order*, and the order is what bites.
- **`usable_folds` is not a stable property of a leg.** It moved 43 → 41 on `sol`
  from reordering alone, which feeds `u` directly into the gate's `u >= 2` term
  and into every fragility computation in the section above.

### The primary measurement is NOT affected, and the control is why

This is the corollary, and it deserves stating as loudly as the defect, because
without it the finding reads as undermining the study it came out of.

**The pre-registered off0 control passing IS proof that the leg order matched.**
An order mismatch changes fold membership, which changes AUC — so six of six legs
reproducing their recorded `mean_auc` *exactly*, on two independent runs, cannot
happen across a permuted leg list. The 1d pullback dispersion (median **0.0515**,
§ "The primary") is therefore measuring what it claims: boundary placement at a
fixed leg order.

So the control did exactly the job it was written for. It was written to catch
"these arms are not comparable to the recorded round"; the *mechanism* it turned
out to be catching was one nobody had identified, and it caught it anyway. That
is the argument for pre-registering a stop condition you cannot yet name the
failure modes of — and it is why the nine mismatched legs were never quoted as a
dispersion result.

The screen's "control MISMATCH" partition is best relabelled: those nine legs
were not incomparable *experiments*, they were the same experiment at a different
leg order, and the control is what refused to pool them.

### What is NOT claimed

This does not show any recorded verdict is wrong, and **no cell is re-graded on
it.** Both leg orders are equally valid; neither is the "correct" one. What is
established is that the harness has an undeclared degree of freedom, that it is
large relative to the effects being measured, and that nothing in the rounds file
or the round report lets a reader detect two rows that differ by it — `legs` is
recorded, but no consumer compares it, and the committed row does not carry it at
all.

Filed as `BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER`.

**Reproduce:** `m20_exit_head_round.py:157,343` · `build_exit_head_dataset.py:583,634,730`
· `train_exit_head.py:90,518` · relays #9403 (fold divergence), #9406 (leg orders).

### Which rows are exposed — and the comparability rule, upgraded

The confound needs **two or more legs pooled into one sort**. A `per_leg` round
trains on one leg, so there are no cross-leg ties to permute and its result
cannot depend on argument order. That splits the corpus cleanly:

| `block_unit` | rounds | rows | exposure |
|---|--:|--:|---|
| `family_pooled` (2–7 legs each) | 6 | **27** | **exposed** — order sets the tie-break |
| `per_leg` (`scalp_15m`, `scalp_5m`) | 2 | 6 | **structurally immune** |

**This predicts the observed control partition with no free parameters:**

| round | pooled? | control | why |
|---|---|---|---|
| `scalp_15m` | per_leg | ✅ OK | immune |
| `pullback_1h` | family_pooled ×4 | ✅ OK | order happened to match |
| `pullback_1d` | family_pooled ×6 | ✅ OK (×2 runs) | order matched |
| `eth_denom_2h` | family_pooled ×7 | ❌ MISMATCH | order differed |
| `eth_denom_4h` | family_pooled ×5 | ❌ MISMATCH | order differed |

And it is being confirmed live as this is written: the running 15m screen's arms
are single-leg (`legs=['ict_scalp_sol_15m']`), and its off0 control reproduces the
recorded `0.5808` at `u = 9` **exactly** — the immunity prediction, met.

**So the comparability rule committed earlier gets one more line.** It previously
said: check the round id before launching, because a `family_pooled` re-run can
only reproduce legs from one round. Add:

> …and for a `family_pooled` round, **match the leg ORDER too** — it is recorded
> in `round_report.json::_round_meta::legs`. Matching the round id is not
> sufficient. A `per_leg` round needs no such check.

That is a check that costs one file read and would have saved the four relays
this took to find.

### The determinism test and the control failure were a controlled pair

Relay #9412 recovered the leg orders of the 4h rounds, and they turn the root
cause from *"explains every observation"* into *"isolated by a controlled
comparison"* — because two experiments I had already run differ in **exactly one
variable**, and I had not noticed they formed a pair:

| run | leg order | compared against | result |
|---|---|---|---|
| `determinism_20260815T032648Z` (mine) | `ada, avax, eth, sol, xrp` — alphabetical | its own re-run, **same order** | **5 of 5 byte-identical** |
| the 4h off0 control arm | alphabetical | `eth_denom_4h`, order `eth, sol, xrp, ada, avax` | **MISMATCH** |

Same code, same data, same flags, same legs. **Order held fixed → exact
reproduction. Order permuted → mismatch.** Nothing else varies between those two
comparisons, which is what makes it a controlled contrast rather than a
consistent story.

It also retires a worry the earlier text left open. § "The control failures are
their own finding" cited the determinism result as proof that *training* is
deterministic, and then had to leave the mismatches unexplained — the two read as
being in tension. They never were: the determinism run compared **like order to
like order**, so it was measuring the training, and it was right. The control
compared **unlike to unlike**. Both results are correct and they were answering
different questions.

**`eth_denom_4h`'s order is hand-typed** (`eth, sol, xrp, ada, avax` — the ETH
investigation's legs first), while every re-run I launched sorted alphabetically.
That is the whole mechanism, and it is why the mismatch tracked *provenance*
rather than leg, family, geometry or timeframe: the `eth_denom_*` rounds are
precisely the ones a human typed in investigation order.

### The remaining second-pass rounds are re-runnable — with the recorded order

`trend_donchian 1h` was the family the comparability rule flagged as spanning two
runs. Both round directories survive and state their orders, so it **is**
re-runnable comparably, one run at a time:

```
1h_donchian_reswept  (relay #9206)  legs: trend_donchian, trend_donchian_eth, trend_donchian_sol
prop_1h              (relay #9156)  legs: trend_donchian_sol_prop, trend_donchian_eth_prop
```

A re-run must pass `--legs` in **that** order, not alphabetically. `scalp_5m` is
`per_leg` and needs no such care. Recording the orders here so the next session
does not re-derive them from four relays, which is what this cost.

### How much of the corpus could the confound actually reach?

"27 of 33 rows are exposed" says which rows the mechanism can touch, not which
verdicts it could move. This bounds the second question from committed data.

The AUC term is graded against a **0.55** bar, and leg order alone was observed
to move `mean_auc` by between **0.0009 and 0.0331** (relay #9402, 2h family, one
permutation). So a row whose `mean_auc` sits closer to 0.55 than the movement
could have its AUC term change side from argument order alone:

| bound used | rows within it, of the 27 exposed |
|---|--:|
| the **largest** observed movement, 0.0331 | **13** |
| the **smallest** observed movement, 0.0009 | **1** |

**So: between 1 and 13 of 27.** That is a genuine range, not a hedge — 0.0331 is
the worst movement seen on one family under one permutation, and quoting it as
*the* noise level would be as wrong as quoting the smallest.

**Two things that must travel with the number.** Crossing the AUC bar is
**necessary but not sufficient** to flip a verdict: the gate is a four-term
conjunction, so a row already failing a fold-majority term does not flip because
its AUC moved. And `usable_folds` was *also* observed to move (43→42, 43→41),
which feeds the other three terms — so this bound covers one term of four and is
not a bound on verdict changes.

**The single most marginal row in the corpus is an exposed one.**
`eth_pullback_2h` is graded `candidate` on a `mean_auc` of **0.5506** — it clears
the bar by **0.0006**, while the order-noise measured on *its own family, in its
own round* reaches **0.0331**. The margin is roughly **55× smaller than the
nuisance term**. That single row is the clearest statement of why the defect
matters, and it is why I would not want a `candidate` at that margin read as a
finding without the fix.

**Reproduce:** `docs/research/m20-exit-head-rounds.jsonl` alone — `mean_auc`,
`block_unit` and the round id parsed from `provenance`; no trainer call.

---

## The 15m candidate screen — COMPLETE: `sol` does not budge, `xrp` does

**Read the xrp subsection below before quoting this one.** The section was
written when only the sol arms had landed and its original heading
("`ict_scalp_sol_15m` complete, and it does not budge") described half the
screen; both legs are now in and they disagree, which is the result.

The screen was launched to test the § 5 fragility flag on a cell where the flag
actually fires and the arm is provably comparable. `ict_scalp_sol_15m` qualifies
on both counts: it is graded `candidate` at **slack 0** on the fold-majority term
(`beats_actual 6 × 3 = 18 = u 9 × 2`), so **one fold changing side fails it**.

| offset | mean_auc | u | verdict |
|--:|--:|--:|---|
| **0 (control)** | **0.5808** | 9 | candidate |
| 4 | 0.5777 | 9 | candidate |
| 8 | 0.5729 | 9 | candidate |
| 12 | 0.5720 | 9 | candidate |

**The off0 control reproduces the recorded `0.5808` exactly**, and `u` is 9 on
every arm — so the arms are comparable and the partition itself is stable. These
are `per_leg` rounds (`legs=['ict_scalp_sol_15m']`), which the § "Which rows are
exposed" analysis predicted would be *structurally immune* to the leg-order
confound. That prediction held.

**Unanimous `candidate` across four boundary draws, spread 0.0088** — **5.9×
tighter** than the 1d family's 0.0515 median.

`ict_scalp_xrp_15m` has reported its control so far: **0.5681, exact**, `u = 9`,
`candidate` — also a slack-0 cell (on the `beats_hard` term). Three arms pending.

### ✅ The xrp arms finished — and xrp MOVED (07:00Z, screen complete)

All 8 arms are in. **`ict_scalp_xrp_15m` is the first non-1d leg to change
verdict under re-partition**, which revises the conclusion written below.

| offset | mean_auc | `beats_actual` | `beats_hard` | u | slack | verdict |
|--:|--:|--:|--:|--:|--:|---|
| **0 (control)** | **0.5681** | 7 | **6** | 9 | **0** | candidate |
| 4 | 0.5800 | 8 | **5** | 9 | **−3** | **`honest_negative`** |
| 8 | 0.5833 | 8 | 8 | 9 | +6 | candidate |
| 12 | 0.5783 | 8 | 8 | 9 | +6 | candidate |

I recomputed the E1 gate independently over all 8 arms
(`u ≥ 2 ∧ auc > 0.55 ∧ 3·beats_actual ≥ 2u ∧ 3·beats_hard ≥ 2u`) and it
**reproduces every recorded verdict**, so the flip is the gate working, not a
harness artifact.

**The flip is on `beats_hard`, and the AUC went UP while the verdict went
down** — 0.5681 → 0.5800 as it failed. That is worth stating plainly because it
is the sharpest available demonstration that **the verdict is not monotone in
the headline number.** Anyone reading these cells by AUC alone would rank off4
*above* the control it fails against. The fold-majority terms decide, and they
move independently of the mean.

**sol vs xrp is the controlled comparison.** Both are `per_leg` 15m scalp legs,
both `u = 9`, both `n_oos = 450`, both slack 0 on the control — differing in
*which* term is at the bar (sol on `beats_actual`, xrp on `beats_hard`). sol's
three off-control draws all moved **away** from its bar (slack 0 → +3, +3, +3);
xrp's moved **both ways** (0 → −3, +6, +6). One of two slack-0 legs moved.

⚠️ **READ THE `xrp` RESULT AGAINST THE ROUNDS ROW, NOT THE MATRIX STATUS** (added
06:40Z, before the arms finish, so the finish is not misread). That cell's matrix
status is `honest_negative` from a **different measurement**: the 2026-08-13
re-run (relay #8963, `fold-mode=trades`, the `fold_blocks` fix) at **6 folds**,
`beats_actual 5/6`, `beats_hard 2/6`, `auc 0.5622`. My screen re-partitions the
**2026-08-14** round instead — `u = 9`, `auc 0.5681` — which is what its off0
control reproduces exactly.

So the screen measures **whether the 08-14 measurement is boundary-stable**, and
says nothing about whether the 08-13 six-fold measurement or the 08-14 nine-fold
one is the better estimate. Those are different questions and only the first is
being asked here. The cell has now read `candidate → downgraded → candidate`
across three measurements, and the matrix ref for its sibling records the reason
an earlier session found: *"3-fold years-mode was systematically optimistic
across the fleet: 2 downgrades, 0 upgrades."*

### What this does to the fragility finding

It reinforces the tempering, on the hardest available test. A cell sitting
**exactly** at a fold-majority bar — where the arithmetic says one flip decides
it — did not move across four re-partitions, and its AUC barely moved either.

Running total of clean-control legs that were re-partitioned:

| leg | flag | draws | verdict changes |
|---|---|--:|--:|
| `gdx_pullback_1d` | fragile negative | 7 | **2** |
| `iaum_pullback_1d` | candidate, 0.0025 margin | 7 | **1** |
| `gld_pullback_1h` | fragile negative | 4 | 0 |
| `ict_scalp_sol_15m` | **candidate, slack 0** | 4 | **0** |
| `ict_scalp_xrp_15m` | **candidate, slack 0** | 4 | **1** |
| 7 other clean-control legs | — | 4 | 0 |

**So the flag identifies cells whose verdict COULD move, and most of them do
not** — 3 of 12 re-partitioned legs moved at all.

⚠️ **CORRECTION (07:00Z) — I wrote the wrong cause here, and the xrp arms
refuted it before this memo left my hands.** This paragraph previously read:

> Both legs that actually moved are 1d — the thinnest books in the corpus
> (`u` 4–11 against 9–26 elsewhere) — which points at *sample size* as the thing
> that predicts instability, rather than proximity to the bar.

**That is no longer true and the reasoning behind it was weak.** `ict_scalp_xrp_15m`
moved, and it is **not** 1d and **not** thin: `u = 9`, `n_oos = 450`, a 524-trade
harness book. The 1d pattern was a real observation over 2 movers — which is
simply too small a base to carry a causal claim, and I stated it as one anyway
because every mover I had seen happened to share a property.

**What the evidence now supports, stated at the confidence it earns:** of the two
slack-0 cells tested at equal depth (`u = 9`, `n_oos = 450`, same family, same
`block_unit`), **one moved and one did not**. Proximity to the bar is *not*
sufficient to predict a flip, and sample size is *not* necessary for one. Twelve
legs is too few to separate the two factors, and I am not going to propose a
third explanation to cover the residual — that is how the first wrong cause got
written.

**This does not change what the flag is FOR.** A flagged cell is one fold from a
different answer; that is arithmetic and holds regardless. What moved is my claim
about *which* flagged cells cash that in — from "the thin ones" to "we cannot yet
say."

That is a different conclusion from the one § 5 reaches on arithmetic alone, and
it is the reason the screen was worth running before proposing anything. **It
does not retire the fragility flag** — a flagged cell is still one fold from a
different answer, and § "How much of the corpus could the confound actually
reach" shows the AUC term has its own nuisance term of the same order. It changes
what the flag is *evidence of*: exposure, not instability.

### Measurement-environment note, recorded because it is not a caveat on the result

These arms ran while the trainer VM was thrashing (`replay_pregate_fleet.py` at
4.08 GB of a 6 GB box, 4.4 GB in swap —
`BL-20260815-TRAINER-VM-THRASHING-4GB-SWAP-DEGRADES-EVERY-JOB`). Arm wall-time
tracked it: 7.9 → 15.9 → 20.5 → **36.2** → 28.9 min, peaking with the swap and
recovering when that job finished (free RAM 97 MB → 4,214 MB; swap 4,375 → 262
MB). **The AUCs and verdicts are unaffected** — the computation is deterministic
and thrashing changes only throughput. Recorded so the timing trail in the relays
is not later mistaken for something about the arms.

### ✅ The donchian-1h screen finished — `trend_donchian_eth` MOVED (09:19Z, relay #9435)

Both pre-committed gates passed before I read a single number, which is the only
reason this screen is interpretable at all
(`BL-20260815-FOLD-DISPERSION-EVIDENCE-RUNS-ON-AN-UNMERGED-BRANCH`: the trainer
resets to `origin/main` every ~15 min and `--fold-offset` / `--total-sort` exist
only on this branch, so an arm silently running unpinned code is the live risk):

1. **Identical `sha256` across all four arms** — `m20_exit_head_round.py`
   `b197e75b4afb0fcd92326a19bc7208e5d74ef3d4599dd8d1b6f94cbcdbaf923a`,
   `train_exit_head.py`
   `6412613984a3812f5ff0e128817cab4ba3ea9ceaa5412c278c1f36fdd45612a6`, pinned at
   `f28348c8`. Every arm ran the same code as its control.
2. **The off0 control reproduces `auc 0.6079 / u 23 / candidate` exactly**
   (relay #9206). A permuted partition cannot reproduce an AUC to four decimals,
   so this is what makes the recorded baseline *checkable* rather than asserted.

This is a **`family_pooled`** round — the block unit exposed to the leg-order
confound — but `pooled_legs_ordered` is byte-identical across all four arms
(`['trend_donchian', 'trend_donchian_eth', 'trend_donchian_sol']`, `total_sort:
false`). **Leg order is held constant and only the fold boundary moves**, so the
screen isolates the boundary, which is what it claims to measure.

#### `trend_donchian_eth` — ETHUSDT 1h, the target

| offset | mean_auc | `beats_actual` | `beats_hard` | u | slack | verdict |
|--:|--:|--:|--:|--:|--:|---|
| **0 (control)** | **0.6079** | 16 | **16** | 23 | **+2** | candidate |
| 4 | 0.6077 | 17 | **13** | 23 | **−7** | **`honest_negative`** |
| 8 | 0.6008 | 16 | **16** | 23 | **+2** | candidate |
| 12 | 0.6094 | 19 | **16** | 23 | **+2** | candidate |

I recomputed E1 independently over all twelve rows in this round
(`u ≥ 2 ∧ auc > 0.55 ∧ 3·beats_actual ≥ 2u ∧ 3·beats_hard ≥ 2u`, `2u = 46`) and
it **reproduces every one of the twelve recorded verdicts**. The flip is the gate
working.

**This is the sharpest AUC/verdict decoupling in the whole screen, and it is
sharper than xrp's.** xrp flipped while its AUC *rose* 0.5681 → 0.5800. Here the
flipping arm's AUC is **0.6077 against a control of 0.6079 — a difference of
0.0002, flat to three decimals** — and the verdict still moves candidate →
`honest_negative`. The full spread across four draws is **0.0086**, essentially
identical to sol's 0.0088, and sol did not move. So AUC spread carries **no
information about verdict stability**: the two legs with the tightest AUC
dispersion in the corpus split one-and-one on whether they flip.

**The mover is also the DEEPEST leg screened so far** — `u = 23`, `n_oos = 566`,
against the `u = 9 / n_oos = 450` scalp legs and the `u = 4–11` 1d legs. That is
a second, stronger refutation of the retracted "thin books move" claim from
07:00Z: not only is a mover not necessarily thin, the largest book screened moved
while smaller ones held. I am recording this rather than replacing the cause with
a new one, for the reason given in that retraction.

#### The other two legs in the same round (pooled, so they came free)

| leg | AUC range (spread) | control slack | draws | verdict changes |
|---|---|--:|--:|--:|
| `trend_donchian` (BTC) | 0.5335–0.5619 (0.0284) | −4 | 4 | **0** — `honest_negative` ×4 |
| `trend_donchian_sol` | 0.6161–0.6359 (0.0198) | **−1** | 4 | **0** — `honest_negative` ×4 |

`trend_donchian_sol` is worth naming: at slack **−1** on `beats_actual`
(`3·15 = 45` against a bar of 46) it is a **fragile NEGATIVE** — one fold from
reading `candidate` — and it did not move in three off-control draws. The
fragility flag is symmetric and this is the first negative-side instance
observed; it behaved the same way the positive-side ones mostly do.

**`n_oos` itself shifts with the boundary** — BTC 311/310/310/310, sol
273/274/274/274 — while ETH is 566 on all four. That is the partition moving
which trades land OOS, exactly as intended, and it is a cheap sanity check that
the offset flag is doing something.

#### Running mover tally, updated

| leg | flag | draws | verdict changes |
|---|---|--:|--:|
| `gdx_pullback_1d` | fragile negative | 7 | **2** |
| `iaum_pullback_1d` | candidate, 0.0025 margin | 7 | **1** |
| `ict_scalp_xrp_15m` | **candidate, slack 0** | 4 | **1** |
| **`trend_donchian_eth`** | **candidate, slack +2** | 4 | **1** |
| `gld_pullback_1h` | fragile negative | 4 | 0 |
| `ict_scalp_sol_15m` | candidate, slack 0 | 4 | 0 |
| **`trend_donchian`** (BTC) | negative, slack −4 | 4 | 0 |
| **`trend_donchian_sol`** | **fragile negative, slack −1** | 4 | 0 |
| 7 other clean-control legs | — | 4 | 0 |

**4 of 15 re-partitioned legs have moved.** The direction of the update is
unchanged from 07:00Z — the flag marks cells that *could* move and most do not —
but the base is now large enough to state one thing at the confidence it earns:
**every leg that moved was flagged, and no unflagged leg has moved.** The flag
has produced no false negatives in 15 legs. Its false-positive rate is 11 of 15,
which is what "exposure, not instability" means in numbers.


### ✅ `trend_donchian_eth_prop` — UNANIMOUS, and it inverts the naive reading (09:34Z, relay #9438)

Both gates passed, and gate 2 was the **two-row** version this time — off0
reproduced `trend_donchian_eth_prop` at `auc 0.6138 / u 24 / candidate` **and**
`trend_donchian_sol_prop` at `auc 0.5635 / u 23 / honest_negative`, both exact.
That is what pins the partition on a pooled round: a wrong leg order can land one
row right by luck, not two. It also independently confirms the leg order
`trend_donchian_sol_prop,trend_donchian_eth_prop` (sol first) recovered from
launch relay #9156's own argv. One sha256 pair across all four arms, pinned
`8f90f435`.

| offset | mean_auc | `beats_actual` | `beats_hard` | u | bar (2u) | slack | verdict |
|--:|--:|--:|--:|--:|--:|--:|---|
| **0 (control)** | **0.6138** | 20 | **16** | 24 | 48 | **0** | candidate |
| 4 | 0.6081 | 20 | 19 | 24 | 48 | +9 | candidate |
| 8 | 0.6108 | 17 | 16 | 23 | 46 | +2 | candidate |
| 12 | 0.6076 | 18 | 17 | 23 | 46 | +5 | candidate |

**Unanimous `candidate` across four boundary draws.** `trend_donchian_sol_prop`
is likewise unanimous `honest_negative` (slack −13/−16/−23/−20, never close). I
recomputed E1 over all eight rows independently; it reproduces all eight recorded
verdicts.

#### This inverts the expectation the flag invites, and the pair is controlled

`trend_donchian_eth` (slack **+2**) **moved**. `trend_donchian_eth_prop` (slack
**0** — literally at the bar, `3·16 = 48` against 48) **did not**. Same family,
same timeframe, same symbol; the two differ by being the API leg and its prop
sibling. The cell that was *closer* to the bar was the *stabler* one.

So "closer to the bar ⇒ likelier to flip" is now measured and **false as a
predictor**. Across all three slack-0 cells tested — `ict_scalp_sol_15m` (held),
`ict_scalp_xrp_15m` (moved), `trend_donchian_eth_prop` (held) — **one of three**
moved.

**I am not proposing a mechanism for why this pair split.** Two candidate causes
have already been advanced and refuted in this document (sample size at 07:00Z,
and now bar-proximity), each on evidence that looked sufficient at the time. The
honest statement is that slack tells you a cell is *exposed* and does not tell
you whether it will *cash that in*, and 17 legs cannot separate what does.

#### A mechanism that IS visible here: the bar moves with the partition

`u` is not constant across arms — `eth_prop` runs `24, 24, 23, 23` and `sol_prop`
runs `23, 23, 22, 22`, and `n_oos` moves with it (902/900/859/858). Since the
threshold is `2u`, **the bar itself shifts between draws**: 48 at `u = 24`, 46 at
`u = 23`. A leg can therefore change verdict with its `beats_*` counts unchanged,
purely because the denominator moved. The donchian-1h round held `u = 23` on all
four arms and hid this; here it is explicit. Anyone comparing `beats_hard` across
arms must compare it against that arm's own bar, never a remembered one.

#### Running mover tally

**4 of 17 re-partitioned legs have moved.** Unchanged in direction, and the two
statements it now supports:

- **Every leg that moved was flagged; no unflagged leg has moved.** 0 false
  negatives in 17.
- **Slack magnitude does not rank the flagged legs.** The one slack-+2 cell
  tested moved; two of three slack-0 cells did not.


### 🔴 The 4h donchian round (09:41Z, relay #9440) — 4 of 5 legs moved, and it REFUTES the fragility flag itself

Both gates passed, on the strongest control run so far: one sha256 pair across
all four arms (pinned `5fe0943d`), and off0 reproduced **all five** recorded rows
exactly — `eth_4h 0.6285 hn` · `sol_4h 0.6119 hn` · `xrp_4h 0.6554 cand` ·
`ada_4h 0.6722 cand` · `avax_4h 0.6226 hn`, every one at `u 16`. Five rows cannot
reproduce by luck, so the leg order decoded from #9288's argv is confirmed. I
recomputed E1 over all **20** rows; it reproduces all 20 recorded verdicts.

| leg | control slack | flagged? | off0 | off4 | off8 | off12 | moved? |
|---|--:|:--:|---|---|---|---|:--:|
| `trend_donchian_ada_4h` | **+7** | **NO** | cand | cand | **hn** | **hn** | **YES** |
| `trend_donchian_avax_4h` | −2 | yes | hn | hn | **cand** | **cand** | **YES** |
| `trend_donchian_eth_4h` | **−5** | **NO** | hn | hn | **cand** | **cand** | **YES** |
| `trend_donchian_sol_4h` | −2 | yes | hn | hn | **cand** | hn | **YES** |
| `trend_donchian_xrp_4h` | **+1** | **yes** | cand | cand | cand | cand | **no** |

#### 🔴 RETRACTION — my 09:20Z claim is false

At 09:20Z I wrote, and repeated in the doc header and in operator-queue item 5:

> **every leg that moved was flagged, and no unflagged leg has moved.** Zero
> false negatives in 17.

**That is refuted.** `trend_donchian_ada_4h` moved from slack **+7** and
`trend_donchian_eth_4h` from **−5** — both comfortably outside the flag — while
the flagged `trend_donchian_xrp_4h` (slack +1) was the one leg in this round that
held. I published a clean-sieve claim on 17 legs and it did not survive the next
five.

Restated with the arithmetic, over the **15 screened legs whose control slack I
can name** (the 1d study's seven other clean-control legs are excluded — I have
not re-derived their slacks this session, so they stay out of the denominator):

| | moved | held |
|---|--:|--:|
| flagged (`|slack| ≤ 2`) | **6** | 5 |
| unflagged (`|slack| > 2`) | **2** | 2 |

**55% vs 50%. One-sided Fisher `p = 0.66`.** The flag does not separate movers
from non-movers on this evidence at all.

> ⚠️ **SUPERSEDED AT 10:05Z — do not quote the two numbers above.** That
> comparison pooled legs measured at *different numbers of draws* (7 for
> `gdx`/`iaum`, 4 for the rest); more draws is more chances to move, and the
> unflagged denominator was **four**. At matched draws over 17 legs it is
> flagged **3/6** vs unflagged **3/11**, **`p = 0.34`** — the direction the flag
> predicts, not established. See § "The SLACK-BLIND 2h round". The section below
> is kept as written because its *mechanism* argument still stands; only its
> statistics were wrong.

#### Why — and this is the part worth keeping

**Slack models ONE fold flipping inside a FIXED partition. The screen REDRAWS
every fold.** Those are different quantities, and I had been treating the first
as a proxy for the second without ever checking that it was one.

The magnitudes make it concrete: `eth_4h`'s `beats_hard` moves **9 → 12** between
off0 and off8 — three folds' worth of change, which no one-flip margin can
anticipate. `ada_4h`'s runs 13 → 10. A boundary shift is not a perturbation of
one fold's outcome; it re-draws the membership of all of them, and the counts
move by several at a time.

So the fragility flag is not wrong about what it says — a cell at slack 0 *is*
one fold from a different answer, which is arithmetic. It is wrong as a
**predictor of boundary sensitivity**, which is what this study measures, and I
was reading it as one.

#### The finding that replaces it

**8 of 15 screened legs (53%) changed verdict under re-partitioning, and margin
does not tell you which.** ⚠️ **Superseded: quote 35% (6 of 17 at matched draws),
not this 53%, which mixed draw counts — see § "The SLACK-BLIND 2h round".**

⚠️ **That 53% is NOT an unbiased fleet estimate**, and the reason is that I chose
the screened set to be flag-enriched. But the enrichment demonstrably did not
work — flagged and unflagged move at the same rate — which removes the usual
reason to distrust the number and is itself the argument that it may generalise.
Four unflagged legs is a thin basis for that, and I am not going to claim more
from it than that.

**The measurement that would settle it** is a screen over legs chosen **without
reference to slack** — a random or exhaustive sweep of the corpus — to get a rate
with a denominator nobody selected. That is the next thing to run, and it is now
a more valuable use of trainer time than screening the remaining flagged cells,
because the flag has stopped being a reason to prefer them.

#### Running tally

**8 of 22 re-partitioned legs have moved** (22 = the 15 above plus the 1d study's
7 clean-control legs). Of the 15 with named slack, **8 moved**. Movers:
`gdx_pullback_1d`, `iaum_pullback_1d`, `ict_scalp_xrp_15m`, `trend_donchian_eth`,
`trend_donchian_ada_4h`, `trend_donchian_avax_4h`, `trend_donchian_eth_4h`,
`trend_donchian_sol_4h`.

**Three heuristics have now been measured and refuted** — sample size (07:00Z),
AUC spread (09:20Z), and margin/slack (here). I am not proposing a fourth.


### What `--fold-offset` actually does — read 09:58Z, because I had been describing it loosely

I have drawn a large conclusion from this flag and had not read its
implementation this session. Doing so (`scripts/ml/train_exit_head.py:549`) turns
up a detail that belongs in the record:

```
if offset:
    ordered = ordered[offset:]     # skip the first N trades, THEN block
```

So an arm at offset N differs from its control in **two** ways, not one:

1. **the fold boundaries move** — the intended effect; block size is unchanged, and
2. **the first N trades are DISCARDED** — up to 12 of ~2,200 on these rounds,
   about **0.5%**.

**This does not change any conclusion, and I want to be precise about why rather
than wave it away.** A 0.5% truncation cannot plausibly move `beats_hard` by
three folds (`eth_4h`, 9 → 12); and the two effects are not really separable in
principle anyway, since dropping leading trades is *how* you move a sequential
block boundary at fixed block size. But it does explain something I had reported
as purely a partition effect: **`u` falling across arms** (24 → 23, 43 → 42) is
partly the round simply having fewer trades to fill its last block with.

Two guard rails in the same function are worth knowing, because they mean the
arms cannot be quietly incomparable:

- `0 <= offset < block_n` is **enforced with a raise**, not clamped — an offset
  at or beyond the block size would repeat a partition while discarding a whole
  block, and the code refuses rather than ignoring it. All four offsets used here
  ran, so `block_n > 12` on every round screened.
- `--fold-offset` with `--fold-mode=years` **raises** rather than being ignored,
  explicitly so "a dispersion run cannot report distinct offsets that were all
  the same partition". Every round here is `fold-mode=trades`.

Recorded because "re-partition" is the word I have used throughout, and it
implies the same data cut differently. It is *nearly* that, and the gap is
small, stated, and bounded — but it was not something I had checked before
building on it.


### ⚠️ The SLACK-BLIND 2h round (10:05Z, relay #9444) — 1 of 7, and it partly walks back my 09:45Z refutation

This is the screen I said at 09:45Z was the one worth running, chosen by a rule
that makes no reference to margin (**largest wholly-unscreened round**), with both
competing predictions written into #9441 **before** it started: *flag has
predictive value ⇒ ≈0 movers of 7; boundary sensitivity is broad ⇒ ≈3–4.*
All seven legs are **unflagged** (slacks +4, −6, +4, +13, −20, −7, +4).

**Result: 1 of 7 moved** — `eth_pullback_2h`. All 21 verdicts recomputed
independently and reproduced.

#### 🔴 First, a defect in MY OWN GATE — gate 1 is insufficient, and it cost an arm

**The off12 arm produced zero evidence rows.** Its log carries:

```
train_exit_head.py: error: unrecognized arguments: --fold-offset 12
```

That is the trainer's ~15-minute `Reset to origin/main` landing **between** the
driver's `git checkout` and its `train_exit_head.py` invocation, wiping the
branch-only flag — the precise hazard
`BL-20260815-FOLD-DISPERSION-EVIDENCE-RUNS-ON-AN-UNMERGED-BRANCH` names, and the
one gate 1 was built to catch.

**Gate 1 passed anyway, and that is the lesson.** It records `sha256` at *arm
start*; the reset arrived after. **A matching hash proves the file was correct
when it was hashed, NOT that the arm ran that code.** I have been quoting "one
sha256 pair across all four arms" as though it established the latter. It does
not, and every screen tonight carried the same hole — the others simply got
lucky on timing.

**⚠️ CORRECTION (10:14Z) — I first wrote "the arm never ran", and that was
wrong in a way worth keeping.** Verified against the box rather than inferred:
the arm *ran*. All seven backtests emitted (363/343/225/297/243/361/407 trades)
and the dataset built **72,725 rows**. Only the **training step** was rejected,
after which the driver printed `evidence rows -> …/rounds.jsonl (0 rows…)` and
`round done`. The accurate statement is: **the arm ran and wrote a 0-row evidence
file.**

**And it is worse than a missing file — it is an EMPTY one.** `versions.txt`
records `ARM off=12 exit=0`; `rounds.jsonl` **exists**, mtime `10:04:53Z`,
**0 lines**. So an existence check passes. Filed separately.

**🔴 Which broke MY OWN readout, in the class I have documented three times
tonight.** My relay loop is:

```
if [ -f "$a/rounds.jsonl" ]; then sed "s#^#$n #" "$a/rounds.jsonl"
else echo "$n (no rounds.jsonl yet)"; fi
```

The file exists, so the `else` never fires; `sed` over an empty file prints
**nothing**. The off12 arm therefore vanished from #9444's output with no line at
all — and I read that silence as "the directory does not exist yet". **An empty
result rendered identically to an absent one: CLAUDE.md § "Diagnostic
provenance" sub-class C, the unasserted denominator, committed inside the
diagnostic I was using to police exactly that.** The same `[ -f ]` test then made
#9445 *refuse to re-run the arm*, because a 0-row file counts as "already has
rounds.jsonl".

**The scientific conclusion is unchanged** — off12 contributed no rows either
way, so the round is genuinely 3 draws and every number below stands. What
changed is that I nearly corrected a right answer on the strength of a
contradiction between two of my own broken reads, and only avoided it by going
and looking at the mtime.

**So this round is 3 draws, not 4**, which matters for every comparison below
and is why I redid them at matched draw counts rather than quoting the old ones.

#### ⚠️ Second — my 09:45Z "the flag is REFUTED" was overstated, for a reason that is my error

At 09:45Z I reported flagged **6/11** vs unflagged **2/4**, `p = 0.66`, and
called the flag refuted. **That comparison pooled legs measured at DIFFERENT
numbers of draws** — 7 for `gdx`/`iaum`, 4 for the rest. More draws is more
chances to move, so "moved" is not comparable across them. I should have matched
the draw count the first time.

Redone over the four rounds where I hold per-arm data at the **same** offsets
(0/4/8), **17 legs**:

| | moved | held | rate |
|---|--:|--:|--:|
| flagged (`\|slack\| ≤ 2`) | 3 | 3 | **50%** |
| unflagged | 3 | 8 | **27%** |

One-sided Fisher **`p = 0.34`**. And by round:

| round | movers / legs | flagged legs in round |
|---|--:|--:|
| donchian 4h | **4/5** | 3 |
| donchian 1h | 1/3 | 2 |
| prop donchian 1h | 0/2 | 1 |
| **pullback 2h (slack-blind)** | **1/7** | **0** |

**The round with zero flagged legs has the lowest move rate of any round.** That
is the direction the flag predicts. So the honest position is **not** "refuted"
and **not** "validated": at matched draws the separation runs the predicted way
and is **not statistically established** (`p = 0.34`, n = 17). The `p = 0.66` I
gave you was driven by an unflagged denominator of **four**; this screen supplied
the missing sample, which is exactly what it was for.

#### Third — the one mover moved on a term the flag never measured

`eth_pullback_2h` flipped `candidate → honest_negative` at off4 **on the AUC
bar**, not on a fold-majority term: `0.5427` against the `0.55` line, with
`beats_actual`/`beats_hard` both comfortably clear. **Every previous mover in
this study moved on `beats_hard` or `beats_actual`.**

And its control AUC is **0.5506 — clearing the bar by `+0.0006`**, the thinnest
margin in the corpus, which **item 5 of the operator queue had already recorded**.
So this leg was *known* to be fragile, on a dimension my flag does not look at.
It is better described as a **false negative of a one-term criterion** than as
evidence that fragility is unpredictable.

The gate has two independent ways to fail. My flag measured one.

I tested the obvious repair — flag if `|slack| ≤ 2` **OR** `|auc − 0.55| ≤ 0.01`:

| criterion | flagged movers | unflagged movers | p | false negatives |
|---|---|---|--:|---|
| slack only | 3/6 | 3/11 | 0.34 | `ada_4h`, `eth_4h`, `eth_pullback_2h` |
| slack **or** AUC margin | 4/9 | 2/8 | 0.37 | `ada_4h`, `eth_4h` |

**It explains one false negative and does not improve prediction** — it flags
three more legs to catch one more mover, and `p` moves the wrong way. ⚠️ The
`0.01` was chosen **after** seeing these data and is **not adopted**; it is a
hypothesis for the next round to test, recorded as such precisely because three
post-hoc stories have already been advanced and retracted in this document.

**Two genuine false negatives survive**: `trend_donchian_ada_4h` (slack **+7**,
AUC margin **+0.12**) and `trend_donchian_eth_4h` (slack **−5**, margin
**+0.08**). Both are far from *both* bars and both moved. Nothing here explains
those, and I am not going to invent a third term to cover them.

#### Where the tally actually stands

**6 of 17 legs (35%) moved at three matched draws.** That is the number to quote
— not the earlier 53%, which mixed draw counts. The population is still
flag-enriched overall, but it now contains 11 unflagged legs rather than 4.


### ✅ The off12 arm recovered (10:19Z, relay #9448) — the 2h round is 4 draws, and the numbers moved AGAIN

The re-run landed with the gate fixed the way the finding demanded: **capability
pre-flight OK** (`--fold-offset` confirmed accepted immediately before invoking),
**sha256 stable BEFORE and AFTER** the run, **7 rows** asserted by count rather
than by an existence test. This is the first arm all night whose code identity
was actually established rather than assumed.

**`avax_pullback_2h` moved.** The 2h round is now **2 of 7**, not 1 of 7.

| leg | off0 | off4 | off8 | off12 | moved |
|---|---|---|---|---|:--:|
| `eth_pullback_2h` | cand | **hn** | cand | cand | **YES** |
| `avax_pullback_2h` | hn | hn | hn | **cand** | **YES** |
| `ada_pullback_2h` · `eth_pullback_prop_2h` · `htf_pullback_trend_2h` · `xrp_pullback_2h` · `sol_pullback_2h` | — | — | — | — | no |

**Both 2h movers flipped on the AUC bar**, neither on a fold bar. `avax` at off12
reads `auc 0.5509` against the `0.55` line — and its `beats_hard` lands on `84`
against a bar of exactly `84`. It passed by the narrowest available margin on
**both** terms simultaneously.

#### The honest headline is that these rates are not stable at this n

| | after the 3-draw read (10:05Z) | after ONE more arm (10:19Z) |
|---|--:|--:|
| 2h round movers | 1/7 | **2/7** |
| total movers | 6/17 | **7/17 (41%)** |
| slack-only separation | flagged 3/6 vs 2/11, `p = 0.34` | flagged 3/6 vs **4/11**, **`p = 0.48`** |

**A single additional arm moved `p` from 0.34 to 0.48.** I have now quoted this
statistic at 0.66, 0.34 and 0.48 within ninety minutes, and each time the change
came from adding data rather than from an error. **The correct reading is that
17 legs cannot resolve this question**, and I should present it as a range under
active measurement rather than as a number.

#### The two-term criterion: a real out-of-sample hit, on n = 1

| criterion | flagged | unflagged | p | false negatives |
|---|---|---|--:|---|
| slack only (`\|slack\| ≤ 2`) | 3/6 | 4/11 | **0.48** | `ada_4h`, `eth_4h`, `eth_pullback_2h`, `avax_pullback_2h` |
| **either term** (`\|slack\| ≤ 2` **or** `\|auc − 0.55\| ≤ 0.01`) | **5/9** | **2/8** | **0.22** | `ada_4h`, `eth_4h` |

**Why this is worth more than the earlier version of the same table.** I proposed
the `0.01` band at 10:05Z on the 3-draw data, where it did **not** help
(`p 0.34 → 0.37`) and I recorded it as untested and not adopted. The off12 arm is
data that did not exist when the band was chosen, and the new mover it produced —
`avax_pullback_2h`, control AUC margin **−0.0052** — falls **inside** that band.
That is a genuine out-of-sample confirmation.

**It is a confirmation on ONE leg.** `p = 0.22` is not significance, the band was
still chosen by eye, and I am not adopting it. What has changed is that it is now
a hypothesis with one prediction it did not fail, rather than a curve fitted to
the sample.

#### The structural reason, which is checkable and not a story

**Which gate term binds depends on the family.** The 2h pullback legs carry
control AUCs of **0.5342–0.6373**, clustered against the `0.55` bar; the donchian
legs carry **0.5403–0.6722**, mostly far above it. So in the pullback family the
**AUC** term is the live constraint, while in the donchian family the **fold**
terms are. A flag that measures only fold slack was measuring, for the 2h family,
the term that was not binding — which is why every 2h mover was invisible to it
and every donchian mover was not.

This is not a fourth post-hoc explanation of the same residual: it is the direct
observation that the gate has two terms and the flag read one. It predicts
something testable — **a family whose AUCs sit near 0.55 should produce movers
the slack flag misses, and a family whose AUCs sit well above it should not.**

**The two genuine false negatives still resist all of it**:
`trend_donchian_ada_4h` (slack **+7**, AUC margin **+0.12**) and
`trend_donchian_eth_4h` (slack **−5**, margin **+0.08**) are far from **both**
bars and moved anyway. Nothing here explains them and I am not adding a term to.


### The full picture across all 26 screened legs (10:25Z) — and the pooling trap, in the direction that FAVOURS my hypothesis

Adding the committed 1d arms (`m20-fold-dispersion-arms.jsonl`, 6 legs × 9 arms,
already on disk and never folded into this tally) gives **26 screened legs, 10
movers (38%)**.

**The 1d round alone is the strongest evidence the flag has:** flagged **2/2**
moved, unflagged **0/4** — and those four legs held across **NINE** arms each, so
they had more than twice the exposure of any other round and still did not budge.

| cut | criterion | flagged | unflagged | p |
|---|---|---|---|--:|
| **pooled, all 26** | slack only | 6/11 (55%) | 4/15 (27%) | 0.150 |
| **pooled, all 26** | either term | **8/14 (57%)** | **2/12 (17%)** | **0.042** |
| uniform 4-arm (20 legs) | slack only | 4/9 (44%) | 4/11 (36%) | 0.535 |
| **uniform 4-arm (20 legs)** | **either term** | 6/12 (50%) | 2/8 (25%) | **0.260** |
| 1d round only (9 arms) | slack only | 2/2 | 0/4 | 0.067 |

#### ⚠️ I am NOT quoting the p = 0.042

It is the **pooled** cut, and pooling legs with 9 arms against legs with 4 is the
**exact methodological error I identified and corrected at 10:05Z** — when it was
working against the flag. It is no more valid now that it works for it. *"Verify
your own output too, hardest when it confirms what you expected."*

**The valid cut is uniform 4-arm exposure**, and there the two-term criterion
gives **`p = 0.26`. Not significant.**

What can be said honestly:

- **The two-term criterion beats slack-only in EVERY cut** — 0.042 vs 0.150
  pooled, 0.260 vs 0.535 at uniform exposure. The direction is consistent across
  three independent slices; none of them individually establishes it.
- **10 of 26 legs (38%) moved.** That figure is stable-ish across cuts, unlike
  the separation statistic.
- **Two false negatives survive every criterion**: `trend_donchian_ada_4h`
  (slack +7, AUC margin +0.12) and `trend_donchian_eth_4h` (−5, +0.08), far from
  **both** bars.

#### The confirmatory test is running (#9449), with predictions fixed first

The 1h pullback family sits *below* the AUC bar and separates the two criteria
cleanly: `tlt_pullback_1h` is **slack-unflagged (+5)** but **AUC-flagged
(−0.0050)**; `gld_pullback_1h` is the reverse (slack −1, AUC margin +0.051) and
has **already been screened at 4 draws without moving**. `spy` (−0.0105) and
`qqq` (−0.0170) sit just outside the `0.01` band and test where it belongs.

Recorded before the run, so a null is reportable rather than reframed:
**falsification is `tlt` stable while `qqq`/`spy` move, or nothing moving at all
despite AUC margins this thin.**


### 🔬 The PRE-REGISTERED 1h pullback test (10:30Z, relay #9452) — my prediction FAILED, and the mechanism was confirmed anyway

All four arms landed clean under the corrected gate: **`preflight=OK`,
`exit=0`, `rows=4` on every arm**, and off0 reproduced all four recorded rows
exactly (`gld 0.6010` · `qqq 0.5330` · `spy 0.5395` · `tlt 0.5450`, all `u 26`,
all `honest_negative`).

**Result: 0 of 4 moved.**

#### Prediction 1 failed, and it was written down in advance

#9449 predicted `tlt_pullback_1h` would move — slack `+5` (invisible to the
slack flag) with an AUC margin of `−0.0050` (inside the two-term band). **It did
not move in four draws.** The falsification condition I recorded before the run
was *"`tlt` stable while `qqq`/`spy` move, or nothing moving at all"*, and the
second branch is what happened. **I am scoring it as a failed prediction, not
reframing it.**

#### But the MECHANISM claim was confirmed, and it does not depend on movement

The failure decomposition per arm is the evidence, and it is direct rather than
inferred. `tlt_pullback_1h`, bar `2u = 52`:

| arm | AUC | 3·beats_actual | 3·beats_hard | fails on |
|---|--:|--:|--:|---|
| off0 | 0.5450 | **57** | **57** | **AUC only** |
| off4 | 0.5399 | **60** | **60** | **AUC only** |
| off8 | 0.5444 | 54 | 51 | AUC + beats_hard |
| off12 | 0.5404 | **57** | **57** | **AUC only** |

**In three of four arms the fold terms clear the bar comfortably and the ONLY
thing keeping this leg negative is the AUC bar.** That is the "which term binds
depends on the family" claim, visible in the arithmetic. A slack-only reading
calls `tlt` a comfortable negative at `+5`; it is in fact one AUC point from
`candidate`, and **its own arm-to-arm AUC spread is `0.0051` against a margin of
`0.0050`** — the same size. It is genuinely on the edge; it simply did not
happen to cross in four draws.

The other three legs behaved as the framework says they should, which is worth
recording because a test that only "passes" on its headline is weak evidence:

- **`gld_pullback_1h`** is the reverse case — AUC fine (0.59–0.60), failing on
  **fold** terms, slack-flagged at `−1`. It had already been screened at 4 draws
  without moving, and **did not move again here**. An independent consistency
  check, passed.
- **`qqq` and `spy` fail on ALL THREE terms in most arms** (e.g. `qqq` off0:
  AUC 0.533, 3·ba 39, 3·bh 33 against 52). They were never near any bar, and both
  criteria correctly decline to flag them. **Correct negative controls.**

#### What it does to the numbers

Three more unflagged non-movers. Over **29 screened legs, 10 movers (34%)**:

| cut | criterion | flagged | unflagged | p |
|---|---|---|---|--:|
| **uniform 4-arm (23 legs)** | slack only | 4/9 (44%) | 4/14 (29%) | 0.367 |
| **uniform 4-arm (23 legs)** | **either term** | 6/13 (46%) | 2/10 (20%) | **0.195** |
| pooled 29 *(not quotable)* | slack only | 6/11 | 4/18 | 0.085 |
| pooled 29 *(not quotable)* | either term | 8/15 | 2/14 | 0.033 |

**The valid cut remains uniform 4-arm exposure**, and there the two-term
criterion sits at **`p = 0.195`** — still not significant, still better than
slack-only's `0.367`. The pooled numbers keep drifting toward significance and I
keep declining to quote them, for the reason given above: they mix 9-arm and
4-arm legs.

**`tlt` is now a false POSITIVE for the two-term criterion** (flagged, did not
move), which is the honest cost of that test — it is the first one the criterion
has bought.

#### Standing summary

- **34% of screened legs (10 of 29) change verdict under re-partitioning.**
- **Neither criterion is established.** Two-term leads slack-only in every cut
  and reaches significance in no valid one.
- **Two false negatives still resist everything** — `ada_4h` (slack +7, AUC
  margin +0.12) and `eth_4h` (−5, +0.08).
- **Four heuristics have now been advanced and tested overnight**: sample size
  (refuted), AUC spread (refuted), slack magnitude (weak), binding-term/two-term
  (mechanism confirmed, prediction failed, separation unproven). I am not
  proposing a fifth.


### ✅ Scope limit, established by reading the harness (11:25Z) — the finding does NOT reach the lever walk-forward

I raised, in an operator-facing comment on PR #9257, that the `trail_decay`
walk-forward *might* share the boundary sensitivity measured here because both
gates are majority-of-folds votes. **I then read the harness, and it does not.**

`scripts/research/m20_fleet_exit_sweep.py:118` — the lever walk-forward's folds
are a fixed list of **calendar years**:

```python
FOLDS = [("2021", "2021-01-01", "2022-01-01"), … , ("2026", "2026-01-01", None)]
```

run as `run_cell(..., start=fs, end=fe)`. There is **no sequential trade-block
partition**, so there is no boundary to slide.

`scripts/ml/train_exit_head.py:495` makes the same point as an enforced guard —
`--fold-offset` **raises** for year-mode folds:

> *"it shifts a sequential trade-block boundary and there is none to shift in a
> per-calendar-year cut. Refusing rather than ignoring it, so a dispersion run
> cannot report distinct offsets that were all the same partition."*

**So the scope of everything in this document is the `exit_head_ml` E1 gate**,
whose folds are sequential trade blocks starting at an arbitrary point. That
arbitrariness *is* the finding. A calendar-year cut is a natural partition, not a
chosen one; the two gates share the 2-of-3-majority **shape** but not the
property that made verdicts move.

**Recorded here because the reverse error is the expensive one.** A reader who
carried "fold-majority gates are boundary-sensitive" across to the walk-forward
would distrust evidence that has no such defect, and I had already published that
inference before checking it. Both the claim and its withdrawal are on the PR.

*(This does not say a year-fold cut has no arbitrary choices — WHICH years, and
where a year boundary falls relative to a regime, are choices. It says the
specific machinery and the specific finding here do not test them, and answering
that would need different tooling, not the existing flag.)*


### Screen runtime cost, measured — and why the 5m round is the last one queued tonight

Worth recording for anyone planning screens, because the spread is two orders of
magnitude and it is **not** proportional to leg count:

| round | legs | folds | per arm | 4 arms |
|---|--:|--:|--:|--:|
| donchian 4h | 5 | 16 | **~45 s** | ~3 min |
| donchian 1h | 3 | 23 | ~65 s | ~4 min |
| prop donchian 1h | 2 | 23 | ~45 s | ~3 min |
| pullback 1h | 4 | 26 | ~50 s | ~3.5 min |
| pullback 2h | 7 | 43 | ~2 m 40 s | ~11 min |
| **scalp 5m** | 4 | 22–29 | **~60–70 min** | **~4–5 h** |

The driver is **bar count, not leg count or fold count** — the 5m legs emit
1,209–1,255 trades each off 5-minute bars, and the dataset build over that is
where the time goes. A 7-leg 43-fold round finished in 11 minutes; a 4-leg 5m
round is projected at 4–5 hours on the 1-OCPU trainer.

**The trade-off I am making, stated rather than left implicit:** the 5m screen
occupies the box for the rest of the night, so the three paper-routed stale
decisions and the 15m scalp leg will **not** be re-swept this session. I am
letting it run anyway, for one reason — **it is a pre-registered test, and
abandoning a pre-registered test because it turned out slow is how inconvenient
results stop getting reported.** Its predictions and falsification condition are
fixed in #9454 and will be scored either way.

The cost is real and the deferred work is named, so a later session can weigh it
differently. If trainer time is contested, **screen the low-bar-count families
first** — the 4h/1h rounds return a whole pooled family in under four minutes.


### Interim: 5m scalp arm off0 complete (12:23Z) — gate 2 passed, and a structural point I had missed

**off0 finished in 73 minutes** (10:35:29 → 11:48:55), `preflight=OK`, `exit=0`,
**rows=3**. Gate 2 passes exactly on all three: `avax 0.6175 u29 candidate` ·
`sol 0.6184 u23 candidate` · `xrp 0.5987 u22 candidate`. off4 is running; four
arms projects to **~4.9 h**, finishing ~15:30Z.

#### These rows are `block_unit: per_leg`, and I had not registered that

The payload reads `"block_unit": "per_leg"` with `pooled_legs_ordered:
["ict_scalp_avax_5m"]` — **one leg per block**. So this round is **structurally
immune to the leg-order confound** (`BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER`),
because there is no pooled tie group for `--legs` order to reorder. Each leg's
partition is cut over its own trades alone.

**That matters for the whole study, and the corpus splits 27 `family_pooled` / 6
`per_leg`.** In a pooled round, moving the fold offset re-cuts a stream
*interleaved across legs*, so a leg's fold membership can change because a
*sibling's* trades moved across the boundary. In a `per_leg` round only that
leg's own trades move. **Those are different perturbations**, and I have been
pooling their results into one mover rate all night.

Tested on what exists:

| block unit | movers | rate |
|---|--:|--:|
| `family_pooled` | 9/27 | 33% |
| `per_leg` | **1/2** | — |

**The `per_leg` cell is vacuous at n = 2** and I am not going to read 50% off it.
That is the finding for now: **the comparison I would most want cannot be made
with the data I have.**

#### A better reason for the 5m screen than the one I recorded — found after the fact

The 5m round is `per_leg` and adds **three** legs to that column, taking it from
2 to 5. That is the only subset immune to the leg-order confound, and it is
currently the thinnest.

**I did not choose the round for this reason** — I picked it as the mirror-image
AUC/fold test, and noticed the `per_leg` stamp only when off0's rows landed. I am
recording it as a retrospective justification, not as foresight, because writing
it the other way round is how a lucky choice becomes a claimed method. It does
make the 4.9 hours easier to defend than my original note did.


### 🔴 The 5m screen is VOID — arm duration vs reset interval, and the AFTER-hash is what caught it (13:20Z)

**Reporting this as NOT RUN, not as a null result.** The distinction is the whole
point: a null would be evidence about fold dispersion, and this round produced
none.

#### What happened

`versions.txt` records BEFORE and AFTER hashes per arm. **They differ on every
arm:**

```
BEFORE   round b197e75b…   train 64126139…     (branch)
AFTER    round 6f6458ac…   train 08541341…     (origin/main)
```

The trainer's ~15-minute `Reset to origin/main` replaces both files **during**
each 73-minute arm. off4's log, all three legs:

```
train_exit_head.py: error: unrecognized arguments: --fold-offset 4
```

`exit=0`, `rows=0`. Caught by the **row-count** assertion; `exit=0` and a `[ -f ]`
check both passed, for the second time tonight.

#### Why off0 nonetheless produced three valid rows

`m20_exit_head_round.py:280` reads `if a.fold_offset: train_cmd += [...]`.
**Zero is falsy** — so the off0 arm never passes the flag and runs correctly on
`origin/main`. **The control arm is the only arm that does not need the branch.**
Every offset arm does, and none of them can get it.

That is why the failure looked like a partial success: a control that reproduced
its recorded values exactly (`avax 0.6175 u29` · `sol 0.6184 u23` ·
`xrp 0.5987 u22`) sitting beside arms returning nothing.

#### The constraint I had backwards

**It is arm duration against reset interval, not luck.** The 4h/1h/2h screens
succeeded because their arms are **45 s – 2 m 40 s** and fit inside the ~15-minute
window. A **73-minute** arm cannot, ever.

**No pre-flight at arm start can fix this.** The check passes; the files are
replaced fifteen minutes later, mid-arm. The pre-flight added after the previous
failure is necessary and **not sufficient** — **the AFTER hash is the check that
actually caught it**, and it exists only because the earlier empty-string bug
forced me to print hashes raw.

⚠️ **This retroactively explains the 2h `off12` failure** by the same mechanism,
and it means the honest reading of my gate history is: *the sha256 gate finally
did the job it was always claimed to do, on its third revision.*

#### What survives, and what does not

- **Survives:** off0's three rows independently re-confirm the recorded corpus
  values for all three 5m legs. Worth keeping as a re-measurement.
- **Does not:** any dispersion information. The 5m legs stay **unscreened**, and
  the `per_leg` column stays at **n = 2** — the comparison I most wanted is still
  unmakeable.
- **The pre-registered prediction is unscored.** `sol` vs `xrp`/`avax` was never
  tested. It is not a failed prediction and not a confirmed one.

#### What a re-run would need (not attempted tonight)

Not a retry — a different construction. Either pin the two files where the reset
cannot reach them, or **split each offset into its own short job** so no single
job outlives the reset window. The second is the smaller change and fits the
existing relay pattern.

#### 🔴 CORRECTION (14:50Z) — both remedies above are wrong, and one of them is impossible

The diagnosis in this section is right: **arm duration against reset interval,
not luck.** The remedies are not, and the second one cannot work at all.

**"Split each offset into its own short job" is unimplementable.** An arm *is*
73 minutes — seven backtests, a 72,725-row dataset build, and three trainings.
There is no seam to split it on. I wrote "the smaller change" about a change
that does not exist, because I was reasoning about the shape of the fix rather
than about what the arm actually does.

**And the whole framing was wrong.** I treated the reset as weather — something
to be detected after the fact, or hidden from. It is not:

```
scripts/ops/run_training_cycle.sh:124   take_trainer_heavy_lock "training_cycle" || exit 0
scripts/ops/run_training_cycle.sh:138   git checkout --quiet --force -B main origin/main
```

The reset runs **inside the trainer heavy-job queue**, and the cycle **skips
itself entirely** when the queue is held past `TRAINER_HEAVY_LOCK_WAIT_S`. An
arm holding that lock does not detect the reset — it **prevents** it.

The M20 exit-head path took the queue nowhere. Measured: `grep -c heavy_lock`
returns **0** for `m20_exit_head_round.py`, `scripts/ml/train_exit_head.py` and
`m20_fleet_exit_sweep.py`, and the `ml` CLI's enforced backstop cannot reach
them because these are research scripts that never import the CLI. So every
voided arm tonight was voided by a mechanism the repo already had the primitive
to stop, and `docs/claude/trainer-resource-protocol.md` § Rule 1 — *"Manual
sessions MUST use the queue too"* — was binding on me the whole time.

**What this costs the claim above.** The sentence *"No pre-flight at arm start
can fix this"* stands, and the AFTER-hash genuinely did catch it. But
`the sha256 gate finally did the job it was always claimed to do` was me
grading a **gauge** when the available move was to remove the cause. A better
gauge tells you the 73 minutes were wasted; the lock stops them being wasted.
That is the error worth recording — not that I chose a weaker fix, but that I
never asked whether the thing I was measuring was preventable.

It also explains the 05:33Z slowdown recorded earlier in this document. Four
arms of identical work differing only in `--fold-offset` — which cannot cost
time — ran 7.9 / 15.9 / 20.5 / 25.8+ min in launch order against an unqueued
4.08 GB `replay_pregate_fleet.py`. **Both sides of that collision were
unqueued**, and `replay_pregate_fleet.py` is absent from the heavy-lock caller
list too, so this is not one stray script: it is `scripts/research/` generally
(`BL-20260815-RESEARCH-TRAINERS-BYPASS-THE-HEAVY-JOB-QUEUE`, open).

**The re-run is therefore a genuinely different construction, not a retry**, and
it is running: the driver now acquires the queue (ordered *after* the capability
pre-flight, so a missing flag still fails in two seconds rather than after an
hour of queueing), and each arm re-checks-out the branch because a reset landing
*between* arms is legitimate and expected. What remains unfixed is the class —
the three sibling scripts, and the enumeration of how many others bypass the
queue, which has **not** been done.

#### 🔴🔴 SECOND CORRECTION (16:05Z) — the correction above is itself wrong

**Holding the heavy lock does not prevent the reset.** I asserted that it does —
in this document, in `m20_exit_head_round.py`, in a test docstring, in two
backlog rows, on the coordination board, and in two Telegram pings — and then
the very next arm measured it false.

Arm `off0` of the relaunched 5m screen ran its **full 74 minutes under a held
lock** (`{"status": "heavy_lock_acquired"}` in its own log) and came out with:

```
!! DRIVER HASH CHANGED 43eec43d0c83 -> 6f6458ac22d8
!! TRAIN  HASH CHANGED 6412613984a3 -> 08541341e093
```

Both AFTER hashes are byte-identical to `origin/main`'s copies of those files.
The reset landed **while the lock was held**. (`off0` still produced its three
rows only because `if a.fold_offset:` treats `0` as falsy, so the control arm
never forwards the flag and never needs the branch-only code.)

**Why the lock cannot help: there are TWO reset paths and I only found one.**

| path | cadence | takes the heavy lock? |
|---|---|---|
| `run_training_cycle.sh:138` | ~daily | **yes** — the one I found, and the lock does hold it off |
| `scripts/ops/trainer_git_sync.sh` via `ict-trainer-git-sync.timer` | `OnUnitActiveSec=15min` | **no — deliberately not** |

The second script's own header says it: *a tiny, frequent, **lock-free**
force-sync so "keep the code current" can never be blocked by "run training"*.
That is not an oversight to be fixed — it exists because gating sync behind the
heavy lock once left the trainer **495 commits behind** and froze the forecast
producer (`BL-20260718-TRAINER-GITSYNC-STALE`). Masking or locking that timer
would re-introduce a worse failure than the one it would prevent.

And the cadence was the tell I had in hand the whole time: I wrote "~15-min
reset" repeatedly while attributing it to a **daily** job. The two numbers never
matched, and I did not check.

**What was actually wrong with my reasoning.** `grep -c heavy_lock` over the
three M20 scripts returned 0, which is a true fact about the *callers*. I turned
it into a claim about the *reset* without ever enumerating the reset's
producers — a search that found one positive and was treated as complete. "A
search returning nothing is not proof of absence" has a mirror image that bit
here: **a search returning one thing is not proof there is only one.**

**The actual fix — a git worktree.** `git checkout -B main` in the main worktree
cannot touch a linked worktree, so the screen now runs its *code* from
`/tmp/m20_screen_wt` pinned at a detached HEAD. Proven, not assumed: a sync was
**forced** while the worktree stood, and the main tree moved to `6f6458ac22d8`
while the worktree stayed at `43eec43d0c83` / `6412613984a3`. Push-safety was
measured the same way — 15 pushes to the branch during a running arm left both
executed files byte-identical.

Two things the worktree cost, both now handled and both worth recording because
each read as a *completed* run:

1. A worktree checks out **tracked** files only, and the candle CSVs are
   untracked — so `--data-dir` defaulted to an empty `$WT/data` and all three
   legs skipped `data_missing:<SYM>` in four seconds. The driver now passes the
   main clone's data dir explicitly and **refuses to launch** unless it finds
   at least three 5m CSVs first.
2. An orphaned child (`backtest_ict_scalp.py`, pid 1550816) survived the stop
   holding the **inherited** flock fd, so the queue read HELD with nothing
   running.

**What still stands from the first correction:** the queue-bypass class is real
and open (`BL-20260815-RESEARCH-TRAINERS-BYPASS-THE-HEAVY-JOB-QUEUE`), the
05:33Z contention slowdown is exactly what the lock *is* for, and taking the
queue remains correct. What does not stand is the claim that taking it protects
a branch-only run from the reset. It does not, and the round's comment, its
test, and this section now say so.


---

## PRE-REGISTERED: the `per_leg` vs `family_pooled` comparison (written 15:10Z, before the 5m rows land)

The 5m screen relaunched at 14:47Z will take the screened `per_leg` column from
**n = 2 to n = 5**. Earlier tonight I called that comparison *"the one I would
most want and cannot make"*. Writing the analysis down **now**, while the arms
are still running, because twice tonight I read a result after seeing it and got
it wrong — the p = 0.66 "refutation" that pooled legs measured at different
numbers of draws, and the "zero false negatives in 17" that a later round
refuted 25 minutes on.

### The hypothesis, and why it is directional

The two block units are **different perturbations**, not two samples of one:

- **`family_pooled`** — the fold offset re-cuts a trade stream *interleaved
  across legs*, so a leg's fold membership can change because a **sibling's**
  trades crossed the boundary.
- **`per_leg`** — one leg per block. Only that leg's own trades move, and there
  is no pooled tie group for `--legs` order to reorder, so it is structurally
  immune to the leg-order confound (`BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER`).

`per_leg` is therefore the **weaker** perturbation, and the prediction is that it
moves **fewer** verdicts than `family_pooled`'s 9/27 = 33.3 %.

### ⚠️ The power calculation, done BEFORE the data — and it kills my own test

Two-tailed Fisher against the pooled 9/27, for every outcome n = 5 can produce:

| per_leg movers | rate | p | at α = 0.05 |
|--:|--:|--:|---|
| 0/5 | 0 % | 0.288 | not significant |
| 1/5 | 20 % | 1.000 | not significant |
| 2/5 | 40 % | 1.000 | not significant |
| 3/5 | 60 % | 0.338 | not significant |
| 4/5 | 80 % | 0.132 | not significant |
| **5/5** | **100 %** | **0.010** | **significant** |

**Exactly one of the six possible outcomes reaches significance, and it is the
one that contradicts my hypothesis.** The direction I predict — `per_leg` moving
*less* — cannot be established at this n **even if every single leg holds**:
0/5 lands at p = 0.288. Only `per_leg` moving *more* (5/5) could be established.

So this screen is **structurally incapable of confirming my hypothesis** and
capable only of refuting it. I would rather know that now than discover it while
writing up a 1/5 result as "consistent with the prediction" — which is what a
20 % rate against 33 % genuinely invites, and it would be worth nothing.

*(I first printed a summary line saying no outcome at all reaches p < 0.05 —
directly contradicting the table above it, which I had just generated. Caught by
re-deriving 0/5 and 5/5 from the hypergeometric by hand rather than re-running
the helper that produced the claim. Recorded because the near-miss is the point:
the wrong version was more convenient for me, since "underpowered in both
directions" needs no further thought and "underpowered in exactly the direction
I believe" is a finding about my own test.)*

### Pre-committed readings

- **5/5 movers** — my hypothesis is **refuted**, and significantly. The pooling
  confound is not what drives verdict instability; something the two block units
  share is. This would be the night's most useful single result.
- **0/5 or 1/5** — *consistent with* the hypothesis and **establishes nothing**.
  I will report it as a descriptive rate with the p value attached, and will not
  write it up as support. The honest sentence is "the direction is as predicted
  and the test cannot distinguish it from chance".
- **2/5–4/5** — nothing. Report the rate, stop.
- **Any arm returning `rounds.EMPTY` or a row count below 3** — the arm did not
  produce evidence and the denominator changes. I will state the reduced n and
  re-run this power table against it rather than quietly comparing a 4-arm
  design's numbers to a 3-arm result. That is the specific error the voided
  screens caused twice tonight.

### What is NOT being tested, so it is not claimed later

- **These are not paired.** The `per_leg` legs are 15m/5m scalp; the pooled ones
  are donchian/pullback across 1h–1d. Family, timeframe and trade count all vary
  with block unit, so a difference is **confounded three ways** and the
  comparison cannot attribute a cause even if it reaches significance.
- The pooled 9/27 baseline is itself an all-night accumulation across screens
  with different arm counts. It is the best denominator available, not a clean
  one.
- ⚠️ **AND IT IS 78 % PROSE.** Measured just now: `m20-fold-dispersion-arms.jsonl`
  holds **6 legs** (the 01:49Z 1d-pullback screen), while the pooled denominator
  is **27** — so **6/27 = 22.2 %** of the baseline is machine-readable and the
  other 21 legs exist only as tables in this document. The `9` movers cannot be
  re-derived from committed data, and neither can the night's headline
  *"10 of 29 screened legs (34 %)"*.

  The denominator itself checks out — all 27 `family_pooled` corpus legs were
  screened, so 27 is the right divisor and not a corpus-wide count standing in
  for a screened one (I checked, expecting to find exactly that error).
  What is missing is the *auditability*: this is the same shape as the defect
  `rounds.jsonl` was added to fix — *"its verdicts reach the repo only as
  hand-copied prose"* — and the same shape as the ten `trend_donchian` rows
  whose hand-transcribed `family` silently disagreed with `classify()` until a
  test was written for it. A transcription slip in the 21 prose legs would be
  invisible today. Filed as
  `BL-20260815-DISPERSION-SCREEN-ARMS-NOT-PROMOTED-TO-A-MACHINE-READABLE-RECORD`.
- **No pooling across block units.** Any combined "mover rate over 32 legs" is
  the mistake this section exists to stop, and it is the same shape as the
  earlier pooling of 7-draw and 4-draw legs into one rate.

---

## The 11 `exit_head_ml` blocked cells: a rebuild does not reach the bar (15:25Z)

`m20_coverage_rollup.py` reports `exit_head_ml 12 (blocked=12)` and stops there.
Each cell's own `ref` is thorough, but nothing puts the fleet side by side, so
the question `BL-20260813-EXIT-HEAD-ML-1D-LEGS-UNREACHABLE` left open — *is a
dataset rebuild over a longer window the remedy?* — had no cross-leg answer.

### First, the bar, verified rather than restated

`per_leg_summary` requires `u >= 2`, and `fold_blocks` yields
`u = len(range(b, N-b+1, b))`. Enumerated directly from that loop at `b = 50`:
`N = 100 → u = 1`, `N = 149 → u = 1`, **`N = 150 → u = 2`**. So the gate needs
**`N >= 3b = 150` lifetime harness trades**, not the 100 that produces a single
fold.

*(A footnote on the closed form: the refs state `u = floor(N/b) - 1`, which is
right for `N >= b` and returns **−1** at `N = 48` where the loop returns 0. It
never changes a verdict — both are below 2 — but anyone reusing the formula
below `N = b` gets a negative fold count. `max(0, floor(N/b) - 1)`.)*

### A near-miss worth recording before the result

Parsing each ref for its stated gap, I found **8 of 11 cells** quoting a
distance computed against the **100** bar — `squeeze_breakout_4h` "misses by TWO
trades" where the gate needs 52, `spy` "52" where it needs 102. A systematic
2×–26× optimistic error across the matrix.

**It does not exist.** All eight already carry the correction, appended and
marked, further down the same `ref`; my regex took the *first* stated gap, which
is the superseded one. I had a tidy systematic-defect finding and it was an
artifact of my own extraction. Recording it because it is precisely the case
CLAUDE.md's *"verify your own output too, hardest when it confirms what you
expected"* is about — I expected the corpus to be stale in that direction and my
parse obligingly produced it.

### The actual result: more history does not fix these legs

The measured rates are **trades per year of DATA**, not forward accrual, so the
lever is the dataset window. Taking each leg's earliest yfinance-served year
from its own ref and holding its measured rate constant:

| leg | N | rate/yr | yrs built | first avail | max yrs | est. max N | reaches 150? |
|---|--:|--:|--:|--:|--:|--:|:--|
| `splg_trend_long_1d` | 72 | 4.43 | 16.3 | 2005 | 21.6 | 96 | **NO** |
| `iwm_trend_long_1d` | 65 | 4.43 | 14.7 | 2000 | 26.6 | 118 | **NO** |
| `scha_trend_long_1d` | 63 | 4.15 | 15.2 | 2009 | 17.6 | 73 | **NO** |
| `qqq_trend_long_1d` | 60 | 4.15 | 14.5 | 1999 | 27.6 | 115 | **NO** |
| `spy_trend_long_1d` | 48 | 3.60 | 13.3 | 1993 | 33.6 | 121 | **NO** |
| `tqqq_trend_long_1d` | 32 | 7.47 | 4.3 | 2010 | 16.6 | 124 | **NO** |
| `qld_trend_long_1d` | 31 | 5.81 | 5.3 | 2006 | 20.6 | 120 | **NO** |

**Zero of seven.** Even `spy`, with 33 years of available history — the deepest
series in the group — estimates to ~121 against a bar of 150.

⚠️ **This CONFIRMS an existing conclusion; it does not establish one.**
`BL-20260813-EXIT-HEAD-ML-1D-LEGS-UNREACHABLE` already records the full-history
range as **50–104 trades**, reached by a different route. My estimate spans
73–124. Both land short of 150 and agree on the verdict, and I found the row
*after* computing this. The independent agreement is worth something — two
methods, same answer — but the finding is not mine and must not be cited as new.
The numeric gap between the two estimates (mine higher on every leg) is
unexplained and unreconciled; if a rebuild is ever run, its real counts settle
both.

⚠️ **The caveat travels with the number, not in a footnote.** The rate is
measured over the *current* window and assumed constant back to inception; an
earlier regime need not trade at the same rate, so `est. max N` is an
**estimate, not a count**. It answers *"is a full-history rebuild plausibly
worth running"* — and says no — never *"this leg will not grade"*. A rebuild
that measured the real counts would settle it; this says don't expect it to.

### The 3 futures legs split the other way — and their question is still open

The table above covers the seven equity 1d legs. The remaining three
(`insufficient_lifetime_trades` on futures series) behave differently and should
not be folded into the same verdict:

| leg | series | N | rate/yr | span built | yrs for 150 | extra yrs needed |
|---|---|--:|--:|--:|--:|--:|
| `mhg_pullback_1d` | `HG_F_1d.csv` | 80 | 8.0 | 9.7 | 18.8 | **9.1** |
| `mgc_pullback_1d` | `GC_F_1d.csv` | 74 | 7.4 | 9.7 | 20.3 | **10.6** |
| `mes_trend_long_1d` | `ES_F_1d.csv` | 33 | 3.7 | 9.0 | 40.5 | 31.5 |

**The equity question was "does the full known series suffice?" — answer no.
Here it is a different question**: the series is ~10 y *by construction* (each
ref calls it "the deepest available"), so what matters is whether a deeper one
**exists**. `mhg` and `mgc` need roughly **double** the span on hand, which is
well within what a continuous-contract daily series can plausibly cover. `mes`
needs ~40 y and is not in that category.

⚠️ **NOT MEASURED, and it is the entire question.** Whether ES_F / GC_F / HG_F
daily series longer than ~10 y are obtainable through the builder's source has
not been established this session. *"Deepest available"* in those refs describes
**what was built**, and I have not shown that is **what exists** — which is
precisely the distinction that made the equity-side `EQ_1D_START` *"~11.6 y
chosen window"* claim wrong (retracted 2026-08-13: field beats comment). I am
not repeating that error one series over.

**The concrete next step, and it is small:** ask the builder's source for the
earliest available daily bar on those three roots. If a ~20 y series exists,
`mhg` and `mgc` become rebuild-reachable and stop belonging in the same bucket
as the seven equity legs. Not run tonight — the trainer is holding my screen's
heavy lock, and this is a query, not an emergency.

### 🔴 The block-size route is already closed — my version of it is superseded

I drafted this section as *"if not history, then block size"*, with the
arithmetic `u >= 2 ⟺ N >= 3b ⟺ b <= N/3` and a table showing 0 of 11 legs clear
at `b = 50`, 2 at `b = 25`, 7 at `b = 20` — offered as a proposal with the
non-monotonicity hazard attached.

**That is superseded, and by something stronger that was already written down.**
`BL-20260813-EXIT-HEAD-ML-1D-LEGS-UNREACHABLE`'s `remaining_work` item 2 says it
outright:

> *STOP treating block size as the exit_head_ml unblock lever — the derivation
> closes that route. The 7 daily legs are not blocked by an arbitrary number: at
> 31–72 lifetime trades (50–104 on full history) NO block size yields a test
> that is both powered and specific. At N = 98 the only gradeable options give
> either 0.49 power or a 50 % single-condition false-positive rate.*

My table asks only whether a `b` produces `u >= 2`. The existing derivation asks
whether the resulting test is **powered and specific**, which is the question
that matters, and answers it for every `b` rather than for three sampled values.
A cell can clear `u >= 2` and still be a coin flip; that is the whole hazard, and
the derivation quantifies it where I only gestured at it.

So the arithmetic above is kept as the *mechanism* — it explains why `b` looks
like a lever — and the recommendation is withdrawn. **Do not lower `b` to
unblock these cells.**

*(Second time in one section. I checked the fleet before checking whether the
question was already answered, and the answer was in the row I was about to
update. Both misses share a shape: I verified my arithmetic carefully and my
NOVELTY not at all.)*

### What this changes for the matrix

Nothing, today — no status flipped and none should be. `blocked` is the honest
state for all eleven. What changes is the **reading**: `blocked:insufficient_
lifetime_trades` invites "revisit when it clears", and for the seven equity 1d
legs there is no window that clears it at `b = 50`. That belongs on
`BL-20260813-EXIT-HEAD-ML-1D-LEGS-UNREACHABLE`, whose remedy line currently
points at a rebuild.

---

## The record is now machine-readable — and it corrects the pre-registration above (16:50Z)

`docs/research/m20-fold-dispersion-arms-consolidated.jsonl` is committed: **234
rows, 61 arm files, 60 screens, 33 legs**, assembled from every surviving
`rounds.jsonl` under `runtime_logs/m20_exit_head/`. This replaces the 60-row,
6-leg `m20-fold-dispersion-arms.jsonl` as the record — a **3.9× increase in
machine-readable coverage** — and resolves the substance of
`BL-20260815-DISPERSION-SCREEN-ARMS-NOT-PROMOTED-TO-A-MACHINE-READABLE-RECORD`.

**Provenance, since the rows do not self-describe.** `fold_offset` is absent
from every emitted row (`BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`),
so it was taken from each arm's `round_report.json` → `_round_meta.fold_offset`:
**61 of 61 recovered, `meta_missing` 0**, cross-checked against the
directory-name offset kept as a separate `dir_offset` field — **0 mismatches**.
Every row carries `offset_source: "round_meta"`; none is a default. The transfer
was sha256-verified end-to-end (92,606 bytes, `0a2b7002…`) after a first
plain-text emit was **silently truncated by GitHub's comment limit at 137 of
234 rows** — caught only because the relay printed its own row count first.

### The prose was right. The denominator was off by one, and it matters.

| | prose | from the data |
|---|---|---|
| all screened legs | 10 of 29 = 34 % | **10 of 30 = 33.3 %** |
| `family_pooled` | 9/27 = 33 % | **9/27 = 33.3 %** — exact |
| `per_leg` | **1/2** | **1/3** |

The headline survives re-derivation, which is the reassuring part. The
`per_leg` count does not: it is **3 legs, not 2**. `ict_scalp_eth_15m` was
screened in `unanimity2_20260815T020802Z` and I had not counted it.

### 🔴 That kills the pre-registered test outright

The section above pre-registered `per_leg` going **2 → 5** and found that
exactly one outcome (5/5) could reach significance. The true arithmetic is
**3 → 6**, and the three existing legs already carry **one mover**, so only
outcomes 1–4 are reachable:

| per_leg movers | rate | p vs 9/27 | reachable? |
|--:|--:|--:|---|
| 1/6 | 17 % | 0.640 | ✅ |
| 2/6 | 33 % | 1.000 | ✅ |
| 3/6 | 50 % | 0.643 | ✅ |
| 4/6 | 67 % | 0.182 | ✅ |
| 6/6 | 100 % | **0.005** | ❌ — 1 mover is already banked |

**No reachable outcome reaches p < 0.05.** The test is *worse* than the version
I pre-registered, not better: at the imagined n = 5 there was one significant
outcome; at the real n = 6 there is none. **The 5m screen cannot settle
`per_leg` vs `family_pooled` in either direction, and I am recording that before
its rows land.**

It is not a reason to stop the screen. The three 5m legs are worth having on
their own terms — 5m is the thinnest-covered timeframe in the corpus — and the
run also re-measures whether the VOID screen's `off0` values reproduce.

### Push safety during a running screen — measured, not asserted

The trainer re-checks out this branch **per arm**, so every push during the 5m
screen is a candidate for changing the code between arms. I pushed **15 times**
while arm `off0` was running, which is exactly the thing my own gate exists to
catch, so it is measured rather than waved away:

```
launch sha 6340a012 → HEAD 338cee20
scripts/research/m20_exit_head_round.py   43eec43d0c837dea → 43eec43d0c837dea  STABLE
scripts/ml/train_exit_head.py             6412613984a3812f → 6412613984a3812f  STABLE
```

`43eec43d0c837dea` is the same value the arm log recorded as `BEFORE_DRIVER`, so
this is the file the running arm actually loaded, not a same-named file. Both
executed files are **byte-identical** across all 15 commits; everything changed
was docs, the backlog, a new script and a new test — nothing on the round's
execution path.

**The repo sha DOES move between arms and that is fine.** The arm log records
`sha` and the driver/trainer hashes *separately*, so "the repo advanced" and
"the code the arm runs changed" stay distinguishable — a single combined check
would have flagged all three remaining arms as contaminated when nothing they
execute had moved.

### A second-order finding the consolidation makes visible for the first time

**22 legs were measured by more than one screen, and on 2 of them the
*mover verdict itself* disagrees:**

- `gdx_pullback_1d` — MOVED in `dispersion_…012205Z`, held in `dispersion_clean_…012717Z`
- `trend_donchian_sol_4h` — MOVED in `donch4h_…093818Z`, held in `unanimity2_…020802Z`

So *"does this leg's verdict move under re-partitioning"* is itself unstable at
roughly **2/22 ≈ 9 %**. Every mover rate in this document — including the 33.3 %
headline — is one draw of a statistic that has its own dispersion, and nothing
before now could have shown that, because the two measurements of each leg lived
in separate prose tables.

The dedup rule was fixed before counting: a leg is a mover if it moved in **any**
screen (one observed flip demonstrates instability; a later hold does not
un-demonstrate it). The stricter *"moved in every screen"* rule gives
`family_pooled` **7/27 = 25.9 %**. Both are reported; neither is "the" rate.
