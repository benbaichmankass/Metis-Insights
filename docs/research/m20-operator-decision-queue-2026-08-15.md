# M20 — what is waiting on you, 2026-08-15 ~02:30Z

Everything the overnight session queued rather than decided, in one place, newest
evidence first. Five items. **Nothing here has been acted on.** No matrix status
was flipped, no gate changed, no live lever touched.

Coverage is **373/376 = 99.2%**, unchanged all night (verified by running
`m20_coverage_rollup.py`, not by counting).

Each item states what I'd do and how confident I am, because Tier-3 is *propose,
you approve* — not *ask an open question*. Where I don't have a recommendation I
say so rather than manufacturing one.

---

## 1. PR #9257 — Tier-3, REAL MONEY. Merge?

**The ask:** two keys on `trend_donchian_xrp_4h`, which `accounts.yaml` routes to
**`bybit_2` (real_money / live)**:

```yaml
trail_decay_arm_r: 2.0
trail_decay_tight_mult: 2.5
```

**State:** still a draft. Rollback of the *behaviour* is deleting the two lines.

⚠️ **CORRECTION (04:55Z) — MERGING #9257 IS NOT A TWO-LINE MERGE.** Everything
above describes the config change accurately, and I checked it again: the
`config/strategies.yaml` diff is `+26 / −0`, of which **2 lines are the keys and
24 are the explanatory comment**, so the semantic change and the rollback really
are two lines. **But the PR carries 30 files**, because this branch is also where
all of tonight's M20 research landed:

| what | files |
|---|--:|
| the Tier-3 config change | 1 |
| research docs + evidence artifacts (incl. the memos in this queue) | 10 |
| research/CI tooling + guards + workflows | 9 |
| tests | 4 |
| **`src/runtime/regime_flip_exit.py`** (new) | 1 |
| sprint log, backlog, coverage matrix, corpus | 5 |

**On the one that would worry me:** `src/runtime/regime_flip_exit.py` is new and
sits in the live-runtime tree, but **nothing under `src/` imports it** — its only
importer is `scripts/research/m20_regime_flip_replay.py`. It lives there so the
research replay and any future live wiring share ONE predicate rather than
mirroring it. So it ships as dead code on the live path, not as a behaviour
change. I verified that by grep rather than by recalling the design intent.

I am flagging this because the queue as first written invited you to read
"merge #9257" as a two-line decision, and it is not. If you want the config
change isolated, say so and I will split it onto its own branch off `main` —
that is mechanical and I did not do it unasked because it would rewrite the PR
you were already pointed at.

**Evidence:** PASS on two independent windows (`base_OOS` 32 and 40), `wf 5/6`,
config-exact base — so the gain is over *what is live*, and the OOS base book is
profitable (+2.574), so this is not improvement-to-a-losing-book.

**A late datum in its favour, found after this queue was first written.** The
one-fold-flip fragility criterion from § 5 also applies to the walk-forward gate
these levers are graded by (same 2/3 majority, verified an exact fit on 78 of 78
corpus cells). **49% of all passing wf cells sit exactly at the bar and would
fail on one fold flip — this one does not.** It passes at `wf 5/6`, slack `+3`,
on both runs that measured it. So on the axis that turned out to be the night's
main finding, this change is in the robust half.

**Recommendation: merge, then let me verify the deploy.** Confidence: reasonably
high on the evidence, which is the strongest in the queue. **Deploy verification
is owed and unpaid** — merged is not deployed, and this one touches real money.

⚠️ **One thing not to conflate.** Overnight work found this same leg's
`exit_head_ml` cell is a knife-edge `candidate` (§ 5). **Different lever,
different column, no interaction with `trail_decay`.** Flagged only so it isn't
discovered mid-merge and read as a contradiction.

---

## 2. `--split-target-oos` default: 25 → 30?

**The ask:** flip the harness default. **Tier-3 because it moves recorded verdicts
fleet-wide** (`htf` 95→24, `tlt` 56→22, `mhg` 7→24 when measured both ways).

**Evidence** — [`m20-split-boundary-loss-2026-08-14.md`](./m20-split-boundary-loss-2026-08-14.md).
A matched A/B over the same 19 legs, differing in nothing but the target:

| | target 25 | target 30 |
|---|--:|--:|
| unmeasurable (`insufficient_base`) | **68/76 = 89.5%** | **4/74 = 5.4%** |
| graded on the merits | 8/76 | **70/74 = 94.6%** |
| passes | 0 | 2 (both Path B) |

checked: scripts/research/m20_fleet_exit_sweep.py — "unmeasurable" here is not an
inference but that sweep's own `insufficient_base` outcome, counted per cell from
both arms' run logs with the denominator `legs × 4` asserted.

At 25 the sweep returns *almost no verdicts* on a family whose legs carry up to
527 lifetime trades — because the target equals the floor, so any boundary loss
lands under it.

**Recommendation: flip it.** Confidence: high. It buys **answers, not winners** —
68 of the 70 graded cells still fail. Two caveats I'd want you to see: two cells
in the target-30 arm produced no outcome line and I could not account for them
(the conclusion is stated against its worst case, 7.9% vs 89.5%, so it does not
depend on them); and the margin of 5 is the smallest value covering every
observation in a 7-leg sample, not an optimum.

---

## 3. Ten cells whose live-parity evidence contradicts their recorded status

**The ask:** re-grade, or leave and annotate?

`eth`/`xrp`/`ada_pullback_2h` · `eth_pullback_prop_2h` ·
`trend_donchian_{xrp,ada}_4h` · `iaum_pullback_1d` ·
`ict_scalp_{avax_5m,xrp_15m}` · `ict_scalp_eth_15m`.

Each was re-measured at live parity and the new verdict disagrees in **sign**
with what the matrix records. They are **listed, deliberately not re-graded** —
a status flip is Tier-3.

**No recommendation.** This is the one item where I think the right answer
depends on something I don't have: whether you want the matrix to record *the
deciding measurement* or *the latest measurement*. Those give different answers
here, and it is a policy choice about what the matrix means, not a fact about
these ten legs.

---

## 4. `iaum_pullback_1d` — a `candidate` that survives 1 of 7 boundary draws

**Measured** — [`m20-fold-dispersion-2026-08-15.md`](./m20-fold-dispersion-2026-08-15.md).
Graded `candidate` at offset 0 on a **0.0025** AUC margin; `honest_negative` at
all six other pure-boundary offsets. Verdicts read from each arm, not inferred.
It carries `n_oos = 30` and `u = 4` — the thinnest book of its family.

**Recommendation: leave the status alone, and treat this as evidence about the
GATE rather than about the leg.** Confidence: high on leaving it. It passed the
gate as written, and the gate does not claim boundary-invariance; re-grading a
cell because six other partitions disliked it is the selection this programme
refuses.

The question I'd actually put to you is the third option in the memo: **is
`u >= 2` too permissive at `n_oos = 30`?** `iaum` clears a four-term conjunction
on four folds of thirty trades. That is a gate question, and changing a gate
after seeing which term it failed on is exactly what I won't do unprompted.

---

## 5. The fragile-margin population — the finding I'd most want you to read

**Arithmetic over the 33 committed rounds, not an extrapolation.** The gate is
`beats * 3 >= u * 2`, so one fold changing side moves the slack by 3: a slack of
0–2 means **one fold flip changes the verdict**.

| | one fold flip from the opposite verdict |
|---|---|
| **candidates** | **8 of 14 (57%)** |
| negatives | 4 of 19 (21%) |

Three candidates sit **exactly** at a fold-majority bar; `eth_pullback_2h` clears
the AUC bar by **+0.0006**.

**It is not an `exit_head_ml` quirk.** The same 2/3 majority governs the
walk-forward gate the *other* lever families are graded by — derived empirically
and an exact fit on **78 of 78** corpus cells. There, **24 of 49 passing cells
(49%) sit at slack zero**, 23 of them at `4/6`.

**And the distribution explains why.** Across the 75 six-fold cells, `4/6` is the
**mode** — 30.7% of all cells — and the mean is 3.81/6 = 0.636, just under the
0.667 bar. **The threshold sits on the peak of the statistic it thresholds**, so
the largest single group of cells has zero slack by construction. That is the
mechanical reason the exposure is ~half rather than a few percent, on two
independent gates.

In the gate's defence, a discriminating threshold often belongs near the middle
of a distribution and this one separates cleanly (78/78). The issue is that each
verdict is a ship-or-don't decision about one leg.

**Why this is the item that matters:** a fragile negative costs an unexplored
opportunity. A fragile candidate is a cell that would justify **shipping a lever
onto a live leg**.

**Recommendation: no decision yet — let the screen finish first.** Confidence:
high that this is the right sequence. An offset screen is running; after it,
**all 4 fragile negatives and 3 of 8 fragile candidates** will be measured, and
**three more rounds** cover the rest (donchian 1h covers two candidates at once,
scalp 15m ×2, scalp 5m ×1). Deciding before that would be deciding on the
arithmetic flag rather than on observed behaviour — and the flag is validated on
exactly one leg (`gdx`), where it selected correctly but did **not** predict the
mechanism.

---

## 6. The harness has an undeclared degree of freedom — HOW to fix it is yours

**Added 05:10Z. This landed after items 1–5 were written and it is the most
important thing found overnight.**

**An E1 `exit_head_ml` verdict depends on the ORDER the legs were typed on the
command line.** `--legs` order becomes the row order in `rows.jsonl`, which
becomes the tie-break in a *stable* sort over `bars[0]["bar_t"]`; on a 2h family
every leg entering on the same bar carries an identical `bar_t`, so the tie groups
span every pooled leg.

**Measured, not inferred** (relays #9403 / #9406). Same 7 legs, two orders:

| | recorded round | off0 arm |
|---|---|---|
| harness trades | 2220 | 2220 |
| total rows | 71199 | 71199 |
| fold shape | 43 × 50 | 43 × 50 |
| **folds with differing composition** | — | **8 of 43** |
| AUC movement | — | up to **0.0331** |
| legs that lost a usable fold | — | **2** (`avax` 43→42, `sol` 43→41) |

**0.0331 is about two-thirds of the 0.0515 median dispersion this entire study
was built to measure.** 27 of the 33 committed rows sit in multi-leg
`family_pooled` rounds and are exposed; the 6 `per_leg` rows are structurally
immune.

**The reassuring half, and it is genuinely reassuring:** the pre-registered off0
control *passing* is proof the leg order matched, so the primary 1d measurement
is unaffected — a permuted order cannot reproduce six of six AUCs exactly. The
control caught a failure mode nobody had named. That is also why the nine
mismatched legs were never quoted as a result.

**The decision is which fix, because each has a different cost:**

| | what it does | cost |
|---|---|---|
| **(a) total sort key** — add `trade_key` as a secondary key | makes every future round reproducible; the real fix | **changes recorded AUCs**, so the corpus needs a re-measure or a vintage marker |
| **(b) sort `emits` in the driver** | canonicalises the order whatever the operator types | narrower; does not fix a genuinely tied sort |
| **(c) stamp the ordered leg set on the row** | makes two rows differing by order *detectable* | none — additive metadata, no number moves |

**Recommendation: (a) + (c).** Confidence: high on (c), which I have **already
shipped** because it changes no numbers and the confound was previously
undetectable from the evidence file. **(a) I have NOT touched** — it rewrites
recorded verdicts across the corpus, and doing that unasked is exactly the
"drive-by that changes the record" this programme refuses. Say the word and it is
a one-line change plus a re-measure.

**What this does to item 5:** read the fragility numbers knowing that 27 of 33
rows carry a nuisance term of up to 0.0331. It does not invalidate them — the
one-flip arithmetic is exact and independent of AUC — but `usable_folds` is an
input to it and `usable_folds` is what moved on two legs.

Filed `BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER` (high).

---

## What I would do first, if you only have ten minutes

1. **Merge #9257** (item 1) — best-evidenced, and it unblocks the deploy
   verification that is already owed. **Read the 04:55Z correction on it first**:
   it is not the two-line merge the item originally described.
2. **Say yes or no to the split-target flip** (item 2) — it gates whether future
   sweeps produce verdicts at all.
3. **Read item 6.** It is the night's real finding, and the only decision in it
   is *which* fix — nothing is broken while you decide, because the control
   already refuses to pool mismatched runs.
4. Leave 3, 4 and 5 alone until the screen reports; 5 is the one to read properly
   when you have longer, and read it *after* 6.

---

## One more thing, outside M20's scope but on its mandate

**The decoupled exit loop's worst pass is 58.9s against your 60s "no live trade
goes unevaluated" ask — a 1.8% margin — and nothing alerts on it.** At the 55
passes the decouple sprint measured, the worst was 34.1s (43% margin); at 625
passes it is 58.9s. Nothing regressed — a larger sample found the tail, exactly
as that sprint log warned when it called its own defaults "CHOSEN, NOT MEASURED".

Lowering `EXIT_LOOP_INTERVAL_SECONDS` cannot help: the interval is
`max(floor, pass)` and the pass is binding.

`exit_loop_health` records `max_pass_ms` but thresholds only *staleness*, so the
loop reads `fresh` while approaching a breach of the ask it exists to satisfy.
I have not touched the knobs — Tier-2, and it is the other session's subsystem.
Filed `BL-20260815-EXIT-LOOP-MAX-PASS-NEAR-THE-60S-ASK-AND-NOTHING-WATCHES-IT`
(high). Stated limit: a max over 625 passes is one order statistic and the writer
keeps no percentiles, so I cannot yet say rare tail vs thickening distribution.
