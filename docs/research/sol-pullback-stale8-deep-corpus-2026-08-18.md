# sol_pullback_2h `stale8_lt0R` on the deep corpus — the pre-registered rule PASSES

**Status: NOT shipped.** The rule passed; two things still block the Tier-3 declare.
Recorded here so tomorrow's session resumes from the numbers, not from a re-run.

## What was in question

My 2026-08-18 PASS (#9955) used base IS n=87. The coverage matrix records
`sol_pullback_2h / stale_stop = honest_negative` from 2026-08-15 at n=190. Same leg,
opposite verdicts, ~2 years of history apart — so the PASS was plausibly a shallow-corpus
artifact rather than a re-grade.

## The re-run (#9959, cells #9968)

The corpus genuinely deepened: **21,222 bars from 2021-10-15**, base **IS n=189** against
the matrix's 190. The population objection is resolved; the two runs are now comparable.

Base book: **IS +15.9232R (n=189) · OOS −2.5783R (n=49)**. The base is *unprofitable out of
sample* (`rate_ungradeable_why: "base_unprofitable"`), which is what makes the base+delta
half of the rule bite.

## Applying the rule fixed in #9959 BEFORE the result was known

> ships only if `stale8_lt0R` still passes Path A at comparable n — and only if
> base+delta stays positive on BOTH windows.

| cell | Path A | wf | ΔIS | ΔOOS | base+Δ IS | base+Δ OOS | rule |
|---|---|---|--:|--:|--:|--:|---|
| **`stale8_lt0R`** | PASS | 4/6 | +9.1618 | +3.7428 | **+25.085** | **+1.1645** | **PASSES** |
| `stale12_lt0R` | — | 3/6 | +3.999 | +2.4703 | +19.922 | −0.108 | fails |
| `gb1R_afterMFE1R` | PASS | 4/6 (eff 3/6) | +10.1392 | +2.1583 | +26.062 | −0.420 | fails |
| `gb1R_afterMFE2R` | PASS | 6/6 (eff 5/6) | +5.0583 | +0.99 | +20.982 | −1.588 | fails |

**`stale8_lt0R` is the only cell that clears both windows**, and it clears OOS by +1.16R —
it converts a losing out-of-sample book into a marginally profitable one. It also cuts mean
hold from 34.7 → 13.1 bars (IS) and 31.4 → 13.1 (OOS), i.e. it frees ~62% of capital-days,
which is the axis the operator has been pressing on. Note `gb1R_afterMFE2R`'s headline 6/6 is
**5/6 effective** — one fold is an `inert` win where the lever never fired; read
`walkforward_effective`, not `walkforward`.

## Why it is still NOT shipped

1. **The matrix disagreement is unreconciled.** The 2026-08-15 matrix run recorded
   `honest_negative` at n=190 — comparable depth to this n=189. Two runs at the same depth
   disagreeing is a different problem from the shallow-corpus one I opened #9959 for, and
   "my newer run wins" is not a reconciliation. Candidate causes not yet checked: a different
   split date, different cell parameterisation, or a different lever definition (the
   stale/giveback extraction into `src/runtime/exit_levers.py` landed on this branch, so the
   harness may not be running the same predicate the matrix run did).
2. **wf fell 5/6 → 4/6** on the deeper corpus. It still clears the harness's own bar (3/6 is
   `wf_fail`), but it moved the wrong way, and the two failing folds (2022 −2.83, 2024 −1.14)
   are both full years.
3. **It is Tier-3** (`config/strategies.yaml`), so it needs an explicit operator approval
   against *these* numbers. The earlier "push the sol pullback now" was given against the
   n=87 evidence and I reverted it; it should not be silently reused for a different run.

## First action next session

Reconcile (1) before anything else — diff this run's split/params/predicate against the
2026-08-15 matrix run. If they differ, the matrix row needs the newer measurement and the
declare proceeds to the operator. If they are identical and still disagree, the disagreement
itself is the finding and neither number should be shipped.

---

# RESOLVED 2026-08-19 — the verdict is split-sensitive, so nothing ships

## What the reconciliation found

The two runs differ in **`split_target_oos`**, not in the lever:

| | matrix 2026-08-15T22:22:59Z | mine 2026-08-18 |
|---|---|---|
| `split_target_oos` | 35 | 50 |
| resolved split | 2025-08-23 | 2025-06-14 |
| base IS / OOS | 190 / 34 | 189 / 49 |
| `stale8_lt0R` | `is_oos_fail` | `PASS wf 4/6` |

**The lever predicate is exonerated.** `exit_levers.py` has 14/14 equivalence tests
passing, and — the stronger evidence — both giveback cells held their PASS across the two
runs. Had the extraction altered the shared `since_entry`/verdict code, giveback would have
moved with stale8. It didn't.

## Correcting my own claim from 2026-08-18

I wrote that the deep corpus made the runs "comparable" because base IS n was 189 vs 190.
**That was wrong.** My corpus starts earlier (2021-10-15) *and* my split lands earlier, and
the two offset to produce a near-identical IS **count** over a materially different IS
**window**. The giveback IS deltas prove it: +12.027 (matrix) vs +10.1392 (mine) on the same
cell. **n matching is not population matching**, and I asserted comparability from a single
scalar.

## The controlled experiment (#9971)

Re-ran at the matrix's `split_target_oos=35` on the **same** deep corpus, **same** commit
(`7942a4d`), same day. Note it did **not** reproduce the matrix's date — targeting 35 landed
on **2025-10-04**, not 2025-08-23, because the underlying trade series differs — so depth and
split are still not fully isolated *against the matrix*. But the two deep-corpus runs differ
in **nothing but the split target**, and that is the comparison that decides:

| run | split | IS n | OOS n | base OOS | ΔOOS | base+Δ OOS | rule |
|---|---|--:|--:|--:|--:|--:|---|
| target 50 | 2025-06-14 | 189 | 49 | −2.5783 | **+3.7428** | **+1.1645** | PASS |
| target 35 | 2025-10-04 | 204 | 35 | −2.4185 | **+0.7277** | **−1.6908** | FAIL |

**Same corpus, same code, same lever — ΔOOS differs by 5.14×, and the pre-registered rule
flips from PASS to FAIL on the choice of split target alone.** `gb1R_afterMFE1R` flips the
same way (PASS at 50 → `is_oos_fail` at 35), so this is not one unlucky cell.

The walk-forward folds are **byte-identical** across both runs (2021 +2.303 · 2022 −2.8333 ·
2023 +8.0772 · 2024 −1.1441 · 2025 +4.7968 · 2026 +4.8897) — they are calendar-year based and
independent of the IS/OOS boundary. So the split-invariant evidence is **4/6**, with both
failures being full years.

## Verdict: NOT shipped, and the matrix row stands

Neither branch of the pre-registered rule applies. The rule anticipated "they differ → my
numbers win" or "identical and still disagree → the disagreement is the finding". Reality is
a third case: they differ in a **methodological choice with no principled preference**, and
the verdict is a function of that choice. There is no reason to prefer 50 OOS trades over 35.

**A cell whose verdict flips on where an arbitrary boundary falls is not evidence of an
edge** — it is the fold-dispersion problem this repo already measured
(`m20_dispersion_rate`, `BL-20260815-EVIDENCE-ROWS-DO-NOT-RECORD-FOLD-OFFSET`), showing up on
the IS/OOS axis instead of the fold axis. The honest reading is that `sol_pullback_2h`'s OOS
window (34–49 trades) is too thin for the gate to resolve, and the matrix's `honest_negative`
should stand unchanged.

**This closes the pullback family at zero shippable lever cells.**

## What this says about the method, not just this leg

The gate reports a binary verdict from one split. Nothing in the sweep records how sensitive
that verdict is to the split, so a PASS and a coin-flip look identical downstream — which is
how a 5.14× swing in ΔOOS reached a Tier-3 declare proposal. The generalisable fix is to
report a **split-dispersion band** (sweep the split target, report the verdict distribution)
rather than a single cut, exactly as `m20_dispersion_rate` does for fold offsets. Filed as
`BL-20260819-SWEEP-VERDICT-NOT-TESTED-FOR-SPLIT-SENSITIVITY`.
