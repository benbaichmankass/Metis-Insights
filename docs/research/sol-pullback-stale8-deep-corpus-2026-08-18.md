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
