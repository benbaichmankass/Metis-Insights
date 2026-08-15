# M20 — what is waiting on you, 2026-08-15 (last updated ~07:45Z)

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
| 7 | ~~Stale SHIPPED lever on a real-money leg~~ | 🔴 **RETRACTED — the leg is PAPER.** I published a false real-money claim; read the 07:45Z correction. Re-sweep result stands, urgency does not |
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

### ⚠️ UPDATE 07:25Z — a REPRODUCIBILITY argument for merging, found by a crash

A screen launch died at 07:16 on `unrecognized arguments: --fold-offset`, three
hours after eight arms used that flag on the same box. Diagnosing it produced a
finding bigger than the launch:

**Every fold-dispersion screen this workstream has produced was computed by code
that exists only on THIS BRANCH.** Measured, not inferred — `--fold-offset` came
in with commit `43820a32`; `git merge-base --is-ancestor 43820a32 origin/main`
returns **NO**, and `origin/main`'s copies of BOTH
`scripts/research/m20_exit_head_round.py` and `scripts/ml/train_exit_head.py`
contain **zero** occurrences of the fold-offset / total-sort machinery (7 and 9
on the branch).

So: the 1d family screens, `gld_pullback_1h`, the completed 15m sol/xrp screen,
and the whole fragility finding are **not reproducible from `main`** while this
PR is unmerged, and the committed rounds corpus references offsets no released
code can generate.

**It also sharpens item 6.** I described fix (a) as "written and default-off
awaiting your call". More precisely: it is **not on `main` at all**. Leaving the
PR unmerged does not hold the fix at default-off — it holds it outside the
released code entirely.

**This is not an argument that the evidence is wrong** — the arms were run, the
controls reproduced, the numbers stand. It is an argument that they cannot
currently be re-derived by anyone starting from `main`, which is a different and
fixable problem.

checked: scripts/research/m20_exit_head_round.py and scripts/ml/train_exit_head.py
— the two files that carry the machinery. Method: `git merge-base --is-ancestor
43820a32 origin/main` returns non-zero (not an ancestor), and grepping each file
as it exists on `origin/main` yields **0** occurrences of `fold-offset`, against
7 and 9 respectively on this branch. A git-reachability fact about two named
files, not a judgement about the numbers.

**MECHANISM (07:18Z, trainer-diag #9430) — sharper than "the checkout drifted".**
The trainer's reflog shows `branch: Reset to origin/main` **every ~15 minutes**
(06:28:52 · 06:44:14 · 06:59:25 · 07:14:34), with HEAD on `main` throughout and
the script unmodified. The box was never on this branch. The 15m screen ran on a
locally-placed copy that survived *between* resets; my 07:16 launch landed **two
minutes after** the 07:14:34 reset wiped it.

So: **any research run on that box depending on unmerged code is racing a
15-minute timer.** Re-checking-out the branch does not fix it — the next reset
wipes that too. Merging does. (The alternative, running from a copy outside the
git tree, reintroduces the same version ambiguity from the other side unless it
carries its own stamp.)

Filed `BL-20260815-FOLD-DISPERSION-EVIDENCE-RUNS-ON-AN-UNMERGED-BRANCH` (high),
whose durable half is not the merge but a **git SHA stamped into each round's
`_round_meta`**, so a future version drift shows up in the artifact rather than
in a crash. Today it crashed loudly because argparse REJECTS an unknown flag; had
the flag been silently ignored, four arms would have run at offset 0 and reported
as a spread.

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

checked: scripts/research/m20_fleet_exit_sweep.py — each cell's grade is that
sweep's own `insufficient_base` outcome, counted from both arms' run logs.

checked: scripts/research/m20_fleet_exit_sweep.py — that grading here is not an
inference but the sweep's own `insufficient_base` outcome, counted per cell from
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

> ### 📍 WHERE THIS ITEM ACTUALLY LANDED — read this box, then the history below only if you want it
>
> **This item was revised five times overnight as screens came in, and the
> finding CHANGED.** The original framing below is preserved because you may
> have read it, but do not act on it. Current state, 10:00Z:
>
> **⚠️ This box was itself corrected at 10:10Z when the slack-blind screen
> landed. I have made TWO claims to you on this item tonight that I then had to
> walk back — at 09:20Z ("the flag is a clean sieve") and at 09:45Z ("the flag is
> refuted"). Both were overstated in opposite directions. What follows is the
> version with the measurement that was designed to settle it.**
>
> **1. The flag's status: SUGGESTIVE, NOT ESTABLISHED — and not refuted.**
> At matched draw counts (17 legs, same three offsets): flagged **3/6 = 50%**
> moved, unflagged **3/11 = 27%**, one-sided Fisher **p = 0.34**. The direction
> the flag predicts, at a sample that cannot establish it.
>
> **2. What was wrong with my 09:45Z refutation, because it was my error and not
> a data change:** I pooled legs measured at *different numbers of draws* — 7 for
> two of them, 4 for the rest. More draws is more chances to move, so "moved" was
> not comparable across them, and the unflagged denominator was **four**. The
> slack-blind screen supplied the missing unflagged sample, which is what it was
> for.
>
> **3. The slack-blind screen (#9441) is the reason this moved.** A round with
> **zero** flagged legs, picked by a rule that ignores margin, moved **1 of 7** —
> the lowest rate of any round screened. Both competing predictions were written
> down before it ran.
>
> **4. The one mover flipped on a term the flag never measured.** It failed the
> **AUC** bar (`0.5427` vs `0.55`), not a fold bar — and its control clears that
> bar by **+0.0006**, which is the thinnest margin in the corpus and is *already
> recorded in this very item, below*. The gate has two independent failure terms;
> the flag measures one. I tested the obvious two-term repair: it explains that
> case and does **not** improve prediction, so it is written down as an untested
> hypothesis and **not adopted**.
>
> **5. What stands, updated 10:30Z after two more screens: 10 of 29 legs (34%)
> changed verdict.** I also ran a **pre-registered** test (#9449) whose
> prediction **FAILED** — 0 of 4 moved where I predicted 1 would — and scored it
> as a failure rather than reframing it. Its *mechanism* claim was confirmed
> regardless: `tlt_pullback_1h` clears both fold bars in 3 of 4 arms and is held
> negative **only** by the AUC bar, one point away, with an arm-to-arm AUC spread
> the same size as its margin. At the valid (uniform-exposure) cut the two-term
> criterion sits at **p = 0.195** against slack-only's 0.367 — better, still not
> significant. **Four heuristics have now been tested overnight and none is
> established.** Earlier in this box I said 7 of 17 (41%); And the most useful thing I can tell you about that
> number is that **it is not stable at this sample size.** One extra arm took the
> slack-flag separation from `p = 0.34` to `p = 0.48`; I have now quoted that
> statistic at **0.66, 0.34 and 0.48 inside ninety minutes**, each change from
> adding data rather than from an error. Treat it as a range under measurement,
> not a result.
>
> **5b. One genuine piece of forward progress.** The two-term criterion
> (`|slack| ≤ 2` **or** `|auc − 0.55| ≤ 0.01`) was proposed at 10:05Z on data
> where it did **not** help, and recorded as untested. The recovered arm produced
> a new mover — `avax_pullback_2h`, AUC margin **−0.0052** — falling **inside a
> band chosen before that data existed**. That is one out-of-sample hit, `p`
> improves 0.48 → 0.22, and false negatives drop 4 → 2. **Still not adopted**:
> n = 1, and the band was chosen by eye.
>
> **5c. The structural reason, which is checkable rather than a story.** The gate
> has **two** independent failure terms and my flag read one. The 2h pullback
> legs' AUCs cluster against the `0.55` bar (0.534–0.637) so the **AUC** term
> binds there; the donchian legs sit well above it so the **fold** terms bind.
> Every 2h mover flipped on AUC; every donchian mover on a fold term. It predicts
> something falsifiable: a family with AUCs near 0.55 should produce movers the
> slack flag misses.
>
> **5d. Two false negatives resist all of it** — `ada_4h` (slack +7, AUC margin
> +0.12) and `eth_4h` (−5, +0.08) are far from **both** bars and moved anyway. I
> am not adding a third term to cover them; three post-hoc stories have already
> been advanced and retracted tonight.
>
> **6. A defect in my own instrument, found by the same screen.** One of the four
> arms **never ran** — the trainer's 15-minute reset wiped the branch-only flag
> mid-arm — and my sha256 gate **passed anyway**, because it hashes at arm start,
> before the training call. I had been quoting that gate as proof each arm ran
> the pinned code. It is not. Every screen tonight carried the same hole and
> differed only in timing luck; the affected round is 3 draws, not 4, and is
> reported that way throughout. Filed as
> `BL-20260815-EXIT-HEAD-ROUND-EXITS-ZERO-WHEN-TRAINING-SUBPROCESS-FAILS`.
>
> **5. What I would actually ask of you:** nothing on this item tonight. It needs
> no Tier-3 decision. When you read it, the ask is a *posture* change — treat a
> single E1 `exit_head_ml` verdict as unreplicated until it has been screened,
> and screen by ROUND (4 arms, ~45 s each, returns every leg in the round) rather
> than by cell. Three heuristics for shortening that list have now been measured
> and refuted (sample size, AUC spread, margin). I am not proposing a fourth.
>
> **6. One thing NOT claimed:** PR #9257 (item 1) rests on a walk-forward, a
> different harness from anything measured here. It shares the *structure* that
> proved boundary-sensitive, but **I have not measured walk-forward
> re-partitioning and this is not a reason to hold that merge.** Flagged so it is
> not discovered later and mistaken for something I knew and withheld.

---


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

### ✅ UPDATE 09:20Z — the donchian-1h screen landed; a FOURTH mover, and it kills one more shortcut

**`trend_donchian_eth` moved** — 3 of 4 boundary draws `candidate`, off4 flips to
`honest_negative` on `beats_hard` (13 against a bar of 16 at `u = 23`). Both
pre-committed gates passed *before* I read any number: identical `sha256` across
all four arms, and the off0 control reproducing `auc 0.6079 / u 23 / candidate`
exactly. Full result + the two legs the pooled round gave free:
`docs/research/m20-fold-dispersion-2026-08-15.md`.

**Two things in it change what you can safely shortcut, so they are here and not
only in the memo:**

1. **AUC spread tells you NOTHING about verdict stability.** The flipping arm's
   AUC is **0.6077 against a control of 0.6079** — flat to three decimals — and
   the verdict still moves. Its four-draw spread is 0.0086; `ict_scalp_sol_15m`,
   which did **not** move, spreads 0.0088. The two tightest-AUC legs in the
   corpus split one-and-one on whether they flip. If you were going to triage
   this population by "how much does the headline number wobble", that heuristic
   is now measured and it does not work.
2. **The mover is the DEEPEST leg screened** (`u = 23`, `n_oos = 566`, against
   `u = 9` scalps and `u = 4–11` 1d legs). That is a second and stronger
   refutation of the struck sample-size story above — not merely "a mover can be
   thick", but "the thickest book screened moved while thinner ones held".

**The tally is now 4 of 15**, and the base is finally large enough to state one
thing positively rather than only as a caveat: **every leg that moved was
flagged, and no unflagged leg has moved.** Zero false negatives in 15 legs;
11 of 15 false positives.

**My recommendation on this item is STILL unchanged** — and it has now survived
two screens that each refuted a *different* explanation I had attached to it.
That is the useful property: the recommendation never depended on either story.
Re-measure before shipping; do not distrust wholesale.

One new observation worth a line, because it is the first of its kind:
`trend_donchian_sol` sits at slack **−1** — a **fragile NEGATIVE**, one fold from
reading `candidate` — and held across all four draws. The flag is symmetric, and
this is the first negative-side cell actually tested. It behaved like the
majority of positive-side ones: flagged, exposed, stable.

### ✅ UPDATE 09:40Z — the prop screen landed and it REFUTES the other obvious heuristic

`trend_donchian_eth_prop` is **unanimous `candidate` across all four boundary
draws** — and it sits at slack **0**, literally at the bar (`3·16 = 48` against
48). Its API sibling `trend_donchian_eth`, at slack **+2**, is the one that
moved. Same family, same timeframe, same symbol.

**So the cell CLOSER to the bar was the STABLER one, and "rank the population by
slack" does not work.** Across all three slack-0 cells now tested —
`ict_scalp_sol_15m` (held), `ict_scalp_xrp_15m` (moved), `trend_donchian_eth_prop`
(held) — **one of three** moved.

That is now **two heuristics measured and refuted** in this population: AUC
spread (09:20Z above) and bar-proximity (here). Both are the natural ways one
would triage a 15-cell re-measure list down to a shorter one, and neither
survives contact with the data. **I am not offering a third.** Two causes have
already been advanced and refuted overnight, each on evidence that looked
sufficient when I wrote it, and proposing a replacement is precisely the move
that produced both.

**This makes the item-5 recommendation stronger, not weaker, and for a reason
worth stating plainly:** the recommendation is *re-measure the flagged cells
before shipping any of them*. Every attempt to find a cheaper rule — screen only
the thin ones, only the wobbly-AUC ones, only the at-the-bar ones — has failed.
Re-measuring is not the conservative option here; it is the only one with
evidence behind it. **Gate 2 passed both rows exactly**, so the control is sound.

Cost, so the ask is concrete: each screen is **4 arms × ~45 s ≈ 3 minutes** of
trainer time on an idle box, and it covers every leg in the pooled round at once.
The 4h donchian round now running (#9439) clears **three** flagged cells in one
go. This is not an expensive recommendation.

**One mechanism the screen did surface**, and it is arithmetic rather than a
story: `u` is **not constant across arms** (`eth_prop` ran 24, 24, 23, 23), and
the gate threshold is `2u` — so the bar itself moves, 48 → 46. A leg can change
verdict with its `beats_*` counts unchanged, purely because the denominator
moved. Anyone reading these tables must compare each arm's counts against **that
arm's own bar**, never a remembered one.

### 🔴 UPDATE 09:45Z — READ THIS ONE. The 4h round refuted the FLAG, and I am retracting a claim I made to you 25 minutes earlier.

**What I told you at 09:20Z:** *"every leg that moved was flagged, and no
unflagged leg has moved — 0 false negatives in 17."* **That is false.** The next
screen (#9440, 4h donchian, five legs) moved **4 of 5**, and two of the movers
were **unflagged**: `trend_donchian_ada_4h` from slack **+7** and
`trend_donchian_eth_4h` from **−5**. The one leg that held was the *flagged* one
(`trend_donchian_xrp_4h`, slack +1). Both gates passed — off0 reproduced all five
recorded rows exactly, which is the strongest control any of these screens has
had — so this is not a bad run.

**The flag does not predict boundary sensitivity.** Over the 15 screened legs
whose control slack I can name:

| | moved | held |
|---|--:|--:|
| flagged (`\|slack\| ≤ 2`) | **6** | 5 |
| unflagged | **2** | 2 |

55% vs 50%, one-sided Fisher **p = 0.66**.

> ⚠️ **I CORRECTED THIS AT 10:10Z — the box at the top of item 5 has the current
> version.** The comparison above pooled legs measured at different numbers of
> draws over an unflagged denominator of four. At matched draws: flagged **3/6**
> vs unflagged **3/11**, **p = 0.34**. The flag is **suggestive, not refuted**.
> Everything below this line is kept as written so you can see what I told you
> and when, but do not act on its numbers.

**Why, because the reason matters more than the number:** slack measures how far
a cell is from ONE fold flipping *inside a fixed partition*. This screen
**re-draws every fold**. `eth_4h`'s `beats_hard` moves 9 → 12 between two arms —
three folds' worth — which no one-flip margin can anticipate. I had been using
one quantity as a proxy for the other without checking that it was one.

#### What this does to item 5

**The finding is bigger and the scoping is worse than I told you.** What stands
is: **8 of 15 screened legs (53%) changed verdict under re-partitioning, and
nothing yet identifies which.** So the re-measure list is *not* "the flagged
cells" — that scoping is what the evidence just removed.

**My recommendation changes accordingly.** Not "re-measure the flagged
population" but: **do not treat any single E1 `exit_head_ml` verdict as
replicated until it has been screened**, and screen by *round* rather than by
cell — a pooled round costs 4 arms × ~45 s and returns every leg in it, which is
how this one delivered five legs for three minutes of trainer time.

⚠️ **The 53% is not an unbiased fleet rate** and I want you to see the caveat, not
just the number: I *chose* the screened set to be flag-enriched. The reason to
still take it seriously is that the enrichment provably did not work. The clean
version of this measurement is a screen over rounds chosen **without reference to
slack**, and that is what I would spend the next trainer hours on rather than
finishing the flagged list.

#### ⚠️ An untested question this raises for item 1 — flagged, NOT claimed

PR #9257's evidence is a **walk-forward** `wf 5/6`, not an E1 `exit_head_ml`
verdict. Different harness, different lever, and **nothing measured here touches
it.** But the two gates share a *structure* — a majority-of-folds vote over a
partition someone had to choose — and that structure is what turned out to be
boundary-sensitive.

**I have not measured whether walk-forward verdicts move under re-partitioning.
I am not asserting that they do, and this is not a reason to hold the merge on
its own.** It is a question I would want asked before the *next* Tier-3 lever
ships on a fold-majority result, and it is cheap to answer with the same
`--fold-offset` machinery. Recording it now so it is not discovered later and
mistaken for something I knew and did not say.

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

## 7. ~~One SHIPPED lever on a real-money leg~~ — RETRACTED: it is PAPER

# 🔴 CORRECTION FIRST (07:45Z) — I published a false real-money claim here

**This item told you a live real-money lever was running on 29-day-stale
evidence. That is WRONG. `htf_pullback_trend_2h` is a PAPER leg.**

`config/accounts.yaml` declares it in **`bybit_1.strategies`** (paper) and
nowhere else. `bybit_2` (real_money) trades BTCUSDT but **does not list this
leg**. I resolved routing from the account's `symbols` list — which answers
*"does some live real-money account trade this instrument"*, not *"is this leg
routed to one"* — and published the inference as a measurement.

**The corrected picture: ALL FOUR stale decisions are paper. ZERO are
real-money.** Nothing in the stale-decision set is money-at-risk.

checked: config/accounts.yaml — every account declares `strategies` explicitly,
so the leg→account edge is exact; `htf_pullback_trend_2h` appears only under
`bybit_1` (`account_class: paper`). Also scripts/research/m20_coverage_rollup.py,
whose resolver now keys on that list.

**This is the same defect I opened the item by criticising**, one level up: the
banner asserted routing it never computed, and my fix computed routing by the
wrong key. Recording that because "I caught the tool doing it" and "I then did
it myself" are the same lesson, and the second half is the useful one.

**Two further corrections that follow from it:**
- **`account_class` has THREE values, not two** — paper ×7, real_money ×3, and
  **prop ×1** (`breakout_1`, live). My two-state resolver graded
  `eth_pullback_prop_2h`, a prop-only leg, as `real_money`. Prop is a class this
  repo never blends into either bucket.
- **The stale-population split I was about to hand you was also wrong.** Correct
  figures, leg-declared: **paper 132 · real_money 24 · prop 12** of 168 — not
  the "55 real-money" a symbol-keyed count produced. The 24 real-money stale
  cells are **all `honest_negative`** (stale knowledge, not stale live
  behaviour), across 5 legs: `trend_donchian_eth_4h` 7 · `xrp_pullback_2h` 6 ·
  `eth_pullback_2h` 5 · `trend_donchian_xrp_4h` 5 · `trend_donchian` 1.

**What SURVIVES unchanged:** the re-sweep itself and its result. `trail_mult: 4.0`
still beat both neighbours at live parity, both arms still graded, and item 2's
boundary argument still reproduced end-to-end on a real cell. The measurement was
sound; the URGENCY framing around it was not. **It was never a real-money
decision, so there is even less for you to do here than the item claimed.**

Everything below is the original item, kept so the correction is legible rather
than silently rewritten.

---

## 7 (as originally written). One SHIPPED lever rests on pre-cutover evidence

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
checked: scripts/research/m20_fleet_exit_sweep.py — the grade below is that
sweep's own `insufficient_base` verdict, with its own `base_trades_oos` and
`min_oos_trades_floor` fields; not my inference.

same leg is **unmeasurable at target 25 and graded at 30** — OOS base 24 vs 31
against a floor of 25, on a leg with **407 lifetime trades**.

checked: scripts/research/m20_fleet_exit_sweep.py — that grading is not my
inference but the sweep's own `insufficient_base` verdict, read from
`verdicts.json` for both cells of the target-25 arm (relay #9427), beside the
target-30 arm's `is_oos_fail` on the identical cells. The base OOS counts (24 /
31) and the floor (25) are the run's own `base_trades_oos` /
`min_oos_trades_floor` fields, not a reconstruction. The sweep's own
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
