# M20 — what is waiting on you, 2026-08-15 (last updated ~07:35Z)

Everything the overnight session queued rather than decided, in one place.
**Seven items**, plus one coda outside M20's scope. **No decision here has been
taken for you.** No matrix status was flipped, no gate changed, no live lever
touched, no config written.

*(Precise as of 07:35Z: MEASUREMENTS were taken — item 7's re-sweep is mine to
run, since measuring is Tier-1 and only changing a lever is Tier-3. What was
withheld is every DECISION, not every action. An earlier version of this line
said "nothing has been acted on", which would have read as "no sweeps were run"
once item 7 reports a number.)*

Coverage is **373/376 = 99.2%**, unchanged all night (verified by re-running
`m20_coverage_rollup.py` each cycle, not by repeating the number).

**Read order if you are short of time:** the ten-minute list at the bottom. If you
read only one item in full, make it **6** — it landed after items 1–5 were written
and it is the night's actual result.

| # | item | my recommendation |
|--:|---|---|
| 1 | PR #9257 merge (Tier-3, real money) | **merge** — but read the 04:55Z correction; it is not a two-line merge |
| 2 | `--split-target-oos` 25 → 30 | **flip it** |
| 3 | Ten contradicting cells | *no recommendation* — it is a policy choice about what the matrix means |
| 4 | `iaum_pullback_1d` | **leave the status**; the real question is about the gate |
| 5 | The fragile-margin population | **no flip needed** — unchanged, but read the 07:00Z update: one mover refuted my stated *cause* |
| 6 | **The leg-order defect** | **(a) + (c)**; (c) already shipped, (a) written and default-off, awaiting your call |
| 7 | **Stale SHIPPED lever on a REAL-MONEY leg** | ✅ **RESOLVED — no action needed.** I ran it; the live value survives at live parity (both neighbours fail OOS) |
| — | Exit-loop 58.9 s vs your 60 s ask | outside M20; filed, nothing alerts on it |

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

### UPDATE 06:40Z — I re-derived this list from committed data, and it is NOT ten silent contradictions

Two corrections, both to my own framing, and the second is a correction to an
analysis I nearly put in front of you.

**1. Seven of the ten already say so in their own `ref`.** The matrix cells carry
`LIVE-PARITY COUNTER-EVIDENCE ALREADY IN THE CORPUS` (the string
`check_matrix_corpus_agreement.py` recognises as an acknowledgement). So they are
**documented, deliberate retentions** with the newer measurement recorded beside
them — not undisclosed disagreements. That is a materially different item from
the one this section originally described.

**2. My own "the re-measurements are systematically favourable" finding was an
artifact, and I am reporting it because I nearly reported the opposite.** Scoring
direction over the ten gave **9 of 10 moving the favourable way** (p = 0.0215
against a fair-coin null) — which reads as a garden-of-forking-paths warning.
Reading the refs killed it:

- The refs **already name that exact mechanism**, measured by an earlier session:
  *"3-fold years-mode was systematically optimistic across the fleet: 2
  downgrades, 0 upgrades."* So the asymmetry is a known, corrected effect, not a
  new discovery.
- The cells are **not exchangeable**, which a binomial test assumes. Three of the
  ten are `ict_scalp` rows whose own provenance string says **"NOT comparable to
  the capped donchian/pullback rounds"** — a geometry difference recorded at the
  time.
- For those three the **matrix is the more-folds measurement** (`2026-08-13`
  re-run, `fold-mode=trades`, the `fold_blocks` fix) and the rounds row is a
  later run under a different configuration. `ict_scalp_xrp_15m` has now read
  candidate → downgraded → candidate across three measurements.

**What survives, and what it means for your decision:** the honest description is
**"ten cells with competing measurements, seven already annotated as such"**, and
at least three of them are a *fold-count/geometry* disagreement rather than a
same-experiment reversal. The policy question above is unchanged — but the item
is smaller and better-documented than it looked, and **the fleet-wide
favourable-direction alarm I was about to raise is already-known and
already-corrected**, so please do not read one into it.

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

### UPDATE 06:25Z / 07:00Z — the hardest test reported; sol does not budge, xrp DOES

`ict_scalp_sol_15m` finished all four arms. It is the cleanest available test of
this item: graded `candidate` at **slack ZERO** on the fold-majority term
(`6 × 3 = 18 = 9 × 2`), so one fold changing side fails it.

| offset | mean_auc | u | verdict |
|--:|--:|--:|---|
| **0 (control)** | **0.5808** — reproduces the recorded value exactly | 9 | candidate |
| 4 | 0.5777 | 9 | candidate |
| 8 | 0.5729 | 9 | candidate |
| 12 | 0.5720 | 9 | candidate |

**Unanimous, spread 0.0088 — 5.9× tighter than the 1d family's 0.0515 median.**
`ict_scalp_xrp_15m`'s control is also exact (0.5681, `u = 9`); three arms pending.

Running total over clean-control legs: **12 of 14 unanimous**, and the only two
that moved (`gdx`, `iaum`) are both **1d** — the thinnest books in the corpus
(`u` 4–11 vs 9–26 elsewhere).

~~**So the sharper reading is that SAMPLE SIZE, not proximity to the bar, predicts
instability.**~~

### ⚠️ UPDATE 07:00Z — the xrp arms landed and REFUTED the paragraph above

Struck rather than deleted, because you may have already read it.

**`ict_scalp_xrp_15m` moved.** Its off4 draw returns **`honest_negative`**
(`beats_hard` 6 → 5, i.e. `15 < 18`), against `candidate` on the other three. So
the third mover is **not 1d and not thin** — `u = 9`, `n_oos = 450`, a 524-trade
book, the same depth as the sol leg that did *not* move.

**And it failed while its AUC ROSE** — 0.5681 (control, passes) → 0.5800 (off4,
fails). The verdict is not monotone in the headline number; read by AUC alone,
the failing arm outranks the passing one. I recomputed E1 over all 8 arms
independently and it reproduces every recorded verdict, so this is the gate
working, not a harness artifact.

**Corrected reading, at the confidence it earns:** of the two slack-0 cells
tested at equal depth (same family, same `u`, same `n_oos`, same `block_unit`),
**one moved and one did not**. Proximity to the bar is not sufficient to predict
a flip; small samples are not necessary for one. Twelve legs cannot separate the
two factors, and I am not going to offer a third explanation to cover the
residual — writing a causal story over two data points is exactly what produced
the struck sentence.

**My recommendation on this item is UNCHANGED**, and that is the point worth
noting: it never rested on the sample-size story. It rests on 3 of 12 flagged
legs actually moving, which is what makes the population *a re-measure list
rather than a distrust list*.

**It does not retire the flag.** A flagged cell is still one fold from a different
answer, and item 6 shows the AUC term carries its own nuisance term of the same
order. What changes is what the flag is evidence *of*: **exposure, not
instability**. If you want one sentence for the decision — *the fragile-margin
population is a list of cells to re-measure before shipping, not a list of cells
to distrust.*

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

**How far it could reach, bounded from committed data.** Leg order moved
`mean_auc` by 0.0009–0.0331, and the AUC term is graded at 0.55, so **between 1
and 13 of the 27 exposed rows** sit closer to the bar than the movement (13 at
the largest observed movement, 1 at the smallest). Two caveats travel with that:
crossing the AUC bar is *necessary but not sufficient* — the gate is a four-term
conjunction — and `usable_folds` moved too, which feeds the other three terms. So
it bounds one term of four, not verdict changes.

**The one line I would want you to see:** `eth_pullback_2h` is graded
`candidate` on `mean_auc` **0.5506** — clearing the bar by **0.0006** — while the
order-noise measured *on its own family, in its own round* reaches **0.0331**.
The margin is about **55× smaller than the nuisance term**. That is the concrete
reason I would not want a `candidate` at that margin acted on before the fix.

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

## 7. One SHIPPED lever on a real-money leg rests on pre-cutover evidence

Found 07:0xZ by running `m20_coverage_rollup.py --stale-decisions` and then
**checking the routing the banner had been asserting** — which nothing had done.

`htf_pullback_trend_2h` · `trail_geometry` · **`shipped`** · newest evidence ref
**2026-07-12**, against that lever's cutover of **2026-08-10**. That is 29 days
older than the TP-geometry fix, so the `trail_mult: 4.0` now shaping exits was
tuned on a book production does not run
(`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`).

**It is money-at-risk.** Verified from the field, not inferred: the leg declares
`symbols: [BTCUSDT]` + `execution: live` in `config/strategies.yaml`, and
BTCUSDT is routed by **`bybit_2`** (`mode: live`, `account_class: real_money`)
alongside the `bybit_1` / `bybit_portfolio` paper mirrors.

**The other three stale decisions are PAPER, and that is a correction to my own
tool, not a softening of the finding:**

| leg | lever | status | newest ref | routing |
|---|---|---|---|---|
| `htf_pullback_trend_2h` | `trail_geometry` | shipped | 2026-07-12 | **real_money** |
| `mes_trend_long_1d` | `trail_geometry` | shipped | 2026-08-09 | paper |
| `mhg_pullback_1d` | `stale_stop` | passed_unshipped | 2026-08-09 | paper |
| `mhg_pullback_1d` | `trail_geometry` | shipped | 2026-08-09 | paper |

The roll-up's ⛔ banner had asserted *"it changes exit behaviour on a real-money
leg now"* over **all four** rows while nothing in that script had ever read
`config/accounts.yaml` — sub-class **A** (a label naming a quantity the code
never computed), inside the tool written to stop that class. Fixed rather than
reworded: the banner now prints a `routing` column resolved from both gates
(`account_class: real_money` **and** `mode: live`, so the `dry_run` `ib_live`
cannot make MES read as money-at-risk), with `unresolved` kept distinct from
`paper`. Pinned in `tests/test_stale_decision_routing.py`, mutation-checked.

**This sharpens the item rather than shrinking it:** one real-money cell 29 days
stale is more actionable than four cells of unstated funding.

**My recommendation: re-sweep `htf_pullback_trend_2h` / `trail_geometry` under
live TP geometry before anything else in the stale backlog** — it is one arm and
the only stale decision touching real money. The three paper rows can wait for
the general re-sweep.

### UPDATE 07:30Z — I am running the measurement. It was mine to take.

This item first said *"I did not run it: a re-sweep that comes back worse is an
argument for changing a live exit parameter, which is Tier-3 and yours."* That
conflated two different things. **Measuring is Tier-1 research; only CHANGING
the lever is Tier-3.** Withholding the measurement did not protect the gate — it
just handed you an item that asked permission to look, which is the opposite of
how the autonomy mandate splits the work. Launched on the now-idle trainer
(relay #9426); **it writes no config, and the sweep tool never can.**

**One flag in it is load-bearing: `--tp-cap-pct 0.099`, passed explicitly.** The
default is `0.0` — *not* live parity — so a re-sweep left on defaults would
reproduce the very geometry gap the cell is stale for and still look like a
clean re-measurement. That is the trap this whole item is about, and it is one
argument away.

Running **both** split targets (25 = today's default, 30 = your undecided item
2), because for one leg it is cheap, it gives item 2 a real cell instead of a
fleet aggregate, and it pre-empts neither choice.

**What the result will and will not settle.** It will say whether `trail_mult:
4.0` still beats its neighbours under the geometry the bot actually places. It
will **not** settle whether to change the live value — that stays yours, and a
worse result is an argument, not a mandate. **Pre-committing to the reading now,
before I have the number, so the conclusion is not fitted to whatever comes
back:** a PASS means the shipped value is re-confirmed and the staleness is
retired; a FAIL means the cell should go to `pending` and the live value gets a
Tier-3 decision from you; an `insufficient_base` at both targets means the leg
cannot be judged at this depth and the honest status is `blocked`, not either
verdict.

### ✅ RESULT (07:35Z) — the live value SURVIVES re-measurement at live parity

Both arms finished (relay #9427). **Against the pre-committed reading, this is
the PASS branch: nothing to change, and the staleness is retired.**

| target | OOS base n | `trail3` | `trail5` |
|--:|--:|---|---|
| 25 | 24 | `insufficient_base` | `insufficient_base` |
| **30** | **31** | **`is_oos_fail`** | **`is_oos_fail`** |

At target 30 both neighbours were **graded on the merits and both lost to the
config-exact base** — i.e. to the live `trail_mult: 4.0`, under
`--tp-cap-pct 0.099`:

| cell | Δ net_R IS | Δ net_R OOS | Δ maxDD IS | Δ maxDD OOS | shape |
|---|--:|--:|--:|--:|---|
| `trail3` | −12.63 | −5.48 | +11.97 | +1.22 | worse on everything |
| `trail5` | +8.39 | −4.69 | −3.19 | +1.63 | **IS-only — the overfit shape** |

`trail5` is the instructive one: it looks good in-sample and fails out. That is
precisely the pattern the IS/OOS split exists to catch, and it is why a
re-measurement that only reported the in-sample number would have argued for
*loosening* a live stop.

**What this does NOT establish.** It tests `4.0 ± 1` only, so it says no
neighbour beats the shipped value — **not** that 4.0 is optimal. A wider grid
was not run and I am not going to imply one from two cells.

⚠️ **Caveat that belongs on the number, not in a footnote:** `verdicts.json`
reports `regime_router: "off"` while listing `htf_pullback_trend_2h` in
`regime_policy_off_legs`, with `regime_gate_delta: "narrower_live"`. So **live
trades a NARROWER book than this backtest** — the measurement is over a superset
of what the leg actually takes. That does not invalidate the comparison (base
and cells share the identical book, so the *delta* is sound), but the absolute
net_R figures are not the live book's.

**Bonus datum for item 2, from a real cell rather than a fleet aggregate.** The
same leg is **unmeasurable at target 25 and graded at 30** — OOS base 24 vs 31
against a floor of 25, on a leg with **407 lifetime trades**. The sweep's own
`insufficient_base_why` says it plainly: *"THE BOUNDARY IS MISPLACED, NOT THE
LEG."* That is item 2's argument reproduced end-to-end on one cell.

**Also worth correcting while I am here:** the SUMMARY measures the live TP
reach on this leg at **median 3.51R IS / 4.27R OOS**, not the **1.3–2.0R** range
quoted when `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP` was filed.
That range was explicitly illustrative and ATR-derived; these are measurements.
The 9.9% clamp is a more ordinary target on a 2h BTC frame than the backlog row
implies.

**So: no action needed from you on this item.** The cell's evidence should be
re-stamped to this run (matrix bookkeeping, Tier-1 — but a status write is a
matrix edit I have deliberately not made overnight, so it is queued rather than
done). The three paper stale decisions are untouched.

⚠️ **What this does NOT say:** that the lever is wrong. Pre-cutover evidence is
unreproduced, not refuted — the re-sweep may confirm `trail_mult: 4.0`. The
defect is that nobody can currently tell which, on a real-money leg.

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
