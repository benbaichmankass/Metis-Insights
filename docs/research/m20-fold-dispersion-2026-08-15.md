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

Nine legs' arms do not reproduce their recorded round while `u` matches exactly.

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

**One piece is still unexplained, and is left open rather than tidied:** inside
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

**What that leaves is a narrower and more uncomfortable question**, stated
without a replacement explanation because I do not have one: same legs, same
trade population, deterministic training, unedited rows — and four of the seven
still differ, by 0.0009 to 0.0331. Every difference-generating input I have been
able to name has now been measured and excluded.

The same read also found the round records **no flags** — `_round_meta` printed
nothing. I am not yet reporting that as "empty": an absence read from a probe not
shown capable of finding a positive is precisely the error I made on `provenance`
earlier tonight, so relay #9400 re-runs it with a positive control (the same
reader on `per_leg`, known populated) and asks which 3 legs reproduce. **The leg
identity is the discriminating datum** — if the reproducing three are the ones an
ETH-denomination transform would leave untouched, that is a lead; if they are an
arbitrary three, it is not.

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
