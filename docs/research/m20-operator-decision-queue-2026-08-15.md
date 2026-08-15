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

**State:** all four checks green, `mergeable_state: clean`, still a draft.
Rollback is deleting the two lines.

**Evidence:** PASS on two independent windows (`base_OOS` 32 and 40), `wf 5/6`,
config-exact base — so the gain is over *what is live*, and the OOS base book is
profitable (+2.574), so this is not improvement-to-a-losing-book.

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

## What I would do first, if you only have ten minutes

1. **Merge #9257** (item 1) — best-evidenced, and it unblocks the deploy
   verification that is already owed.
2. **Say yes or no to the split-target flip** (item 2) — it gates whether future
   sweeps produce verdicts at all.
3. Leave 3, 4 and 5 alone until the screen reports; 5 is the one to read properly
   when you have longer.
