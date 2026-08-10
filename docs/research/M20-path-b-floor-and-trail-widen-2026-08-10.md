# M20 overnight: the Path B floor, and what the fleet sweep actually says

**Session** `session_011iUN3roukhbRWwuioX8pRD` · **2026-08-10 overnight** · Tier-1 throughout
(no `config/` change; the one Tier-3 change of the day — `eth_pullback_2h` — merged
earlier with operator approval and is verified live in § 4).

**Operator directive this answers** (2026-08-10): *"let's try and use any optimization
of the capital utilization and PnL to decide what the correct number is there …
database decisions and not arbitrary guesses."*

---

## 1. The headline: **the case the floor was meant to fix does not occur in the measured fleet**

This is now measured, not predicted — the corpus committed (`140 rows`,
`docs/research/m20-sweep-corpus.jsonl`) and the analysis ran against it.

### 1a. The formal verdict is `insufficient_population`

```
corpus rows: 140 (140 cells + 0 leg-status)
cells with no walk-forward: 129   (no generalisation evidence — excluded, NOT failures)
walk-forwarded: 11, of which missing base_rate_IS: 3
ANALYSED: 8 cells across 6 legs
base_rate_IS: min 1.08 · median 3.46 · max 6.95 · overall WF pass rate 38%
```

Eight cells over **six** distinct predictor values. The predictor is a property of the
**LEG**, not the cell, so those 8 rows are a six-point comparison — and a test assuming
independence would inflate the effective n by the cells-per-leg ratio and return a
confident-looking p for it. `m20_path_b_floor.py` refuses and says
`insufficient_population` — *we did not look* — explicitly distinct from `no_separation`.

### 1b. But the per-leg distribution answers the real question anyway

The floor exists to stop the criterion being permissive on a weak book. Here is every
swept leg's IS base:

| leg | net_R | maxDD | rate | |
|---|--:|--:|--:|---|
| `spy_pullback_1h` | 66.63 | 9.58 | **6.95** | |
| `qqq_pullback_1h` | 70.94 | 15.43 | **4.60** | |
| `trend_donchian_sol` | 39.85 | 11.53 | **3.46** | |
| `trend_donchian` | 46.17 | 17.88 | **2.58** | |
| `eth_pullback_2h` | 30.63 | 12.69 | **2.41** | |
| `avax_pullback_2h` | 16.86 | 8.78 | **1.92** | |
| `htf_pullback_trend_2h` | 23.47 | 21.70 | **1.08** | ← lowest |
| `trend_donchian_1h` | −34.19 | 61.14 | — | `base_unprofitable` |
| `trend_donchian_eth` | −1.06 | 26.37 | — | `base_unprofitable` |
| `trend_donchian_eth_prop` | −24.37 | 50.69 | — | `base_unprofitable` |

**Zero of the 7 gradeable legs sit below rate 1.0.** The lowest is **1.08** — 2.7× the
**0.40** that motivated the floor.

**And the weak-book case is already guarded on the other side.** Verified against the
producer (`drawdown_exchange_rate`), not from memory: a base book that loses money returns
`passes: None` / `reason: base_unprofitable` — **ungradeable is not a pass**. That covers
**3 of the 10 legs**.

So the permissive case can only arise in the narrow band between those two: a book that is
*profitable but earns little per unit of drawdown*. The 0.40 example sat exactly there, and
**on the corrected config-exact base no leg in the measured fleet does.**

### 1c. What I am and am not claiming

- **Claimed:** across 10 swept legs, 0 of 7 gradeable rates fall below 1.0, and the other 3
  are refused by an existing guard. On this population the criterion's known asymmetry is
  **not reachable**, so a floor would currently constrain nothing.
- **Not claimed:** that it can never be reached. **10 of 44 trail legs** are measured. A
  low-rate profitable leg could sit in the other 34.
- **Not isolated:** `eth_pullback_2h` moved 0.40 → 2.41 between sweeps, and *two* things
  changed — the config-exactness fix and the Tier-3 decay declaration. I did not run the
  counterfactual. The fleet-wide observation above does not depend on resolving it.

**Recommendation: do not set a floor. Sweep the remaining 34 legs first** (free, ~5 min,
$0) — if no profitable leg below ~1.0 exists across the full fleet, the floor is
unnecessary rather than merely underdetermined, and that is a stronger and cheaper answer
than a number.

---

## 2. The more actionable finding: the fleet-wide trail-widen is an IN-SAMPLE effect

Three legs produced a `trail6` Path B candidate (`trail_mult` 5.0 → 6.0), which reads like
a robust cross-leg signal. **It is not, and the arithmetic says so.**

Eight of the ten swept legs had a wider trail tested (two use a different base trail, so
their widen-cell is not `trail6`):

| leg | verdict | ΔnetR IS | ΔnetR OOS | ΔmaxDD IS | ΔmaxDD OOS |
|---|---|--:|--:|--:|--:|
| `trend_donchian_sol` | `path_b_wf_pass` | +11.95 | **+4.16** | +0.26 | −2.33 |
| `eth_pullback_2h` | `path_b_wf_pass` | +5.39 | **+2.36** | −0.93 | +0.15 |
| `qqq_pullback_1h` | `path_b_wf_pass` | +4.97 | **+3.82** | +1.63 | −1.91 |
| `trend_donchian_eth` | `is_oos_fail` | +16.99 | −0.71 | −0.29 | −2.31 |
| `trend_donchian` | `is_oos_fail` | +9.72 | −1.93 | −0.54 | +1.70 |
| `trend_donchian_1h` | `is_oos_fail` | +5.40 | −1.15 | −4.81 | +3.65 |
| `spy_pullback_1h` | `is_oos_fail` | +1.35 | −2.63 | +3.59 | +0.74 |
| `avax_pullback_2h` | `is_oos_fail` | −0.85 | +0.58 | +0.03 | 0.00 |

- **IS: 7 of 8 positive.** Under a null of "the sign is a coin flip", P(≥7 of 8) = **0.035**.
  A real in-sample effect — the trail genuinely is too tight across the fleet *in-sample*.
- **OOS: 4 of 8 positive.** P(≥4 of 8) = **0.637**. **Exactly chance.**

That is the textbook overfit signature at the fleet level. The three `path_b_wf_pass`
candidates are the legs where an in-sample effect and a coin flip happened to align, and
"the same cell won on three independent legs" is the wrong reading — **eight legs were
tested and the OOS sign is noise, so three alignments is what chance produces.**

**Recommendation: do not promote any `trail6` cell on the cross-leg pattern.** If one is
promoted it must be on its own leg's walk-forward standing alone, with the 4-of-8 OOS
denominator stated. My own view is that none of the three clears that bar tonight.

---

## 3. What is genuinely shippable from this sweep: nothing

- **Path A PASS: 1** — `trend_donchian_1h vt_hot90_t2.5`, and it is **not shippable**: that
  leg reads `enabled: false` / `execution: shadow` (retired). Its numbers are strong (ΔnetR
  IS +11.92 / OOS +15.53, ΔmaxDD IS −8.15 / OOS −14.54 — both axes, both windows). Recorded
  so it is the first thing re-measured if the leg is ever revived; **flagged loudly because
  reading the PASS off the sweep table and proposing it is the obvious mistake.**
- **Path B candidates: 4**, all discussed in § 2. None recommended.
- **Everything else: honest negatives.**

A sweep whose output is "nothing ships" is a successful sweep. The alternative — promoting
the `trail6` trio on a pattern that is 4-of-8 coin flips — is how a fleet acquires levers
that backtest well and do nothing.

---

## 4. Verification: the Tier-3 change from earlier today is live, self-verifyingly

`eth_pullback_2h` gained `trail_decay_stall_bars: 10` + `trail_decay_tight_mult: 2.5`
(merged `712274c`, operator-approved). Three independent confirmations:

1. **`/api/diag/version` → `git_sha: 712274ca`** — the deploy rolled forward to the merge
   commit.
2. **The sweep's own base moved.** `eth_pullback_2h decay_stall10_t2.5` now reads
   `tie_no_improvement` with **0.0 on every axis** — a cell that merely re-declares a lever
   the leg already carries scoring as exactly nothing is the signature that the deploy
   landed *and* the sweep base is config-exact. The same signature appears independently on
   `trend_donchian_eth` (`stale8_lt0R`, `vt_cold10_t2.5`) and `trend_donchian_eth_prop`
   (`stale12_lt0R`, `decay_stall10_t1.8`).
3. **`exit_lever_soak` annotate rows for that leg stop** after the deploy — *pending*: the
   annotate rows are absent from the 21:08Z cycle but present at 18:45Z, which is
   consistent with the declaration landing AND with the position simply having closed.
   Diag request **#8758** resolves that ambiguity. **Not claimed as verified until it does.**

---

## 5. Two defects found and fixed tonight, both in my own work

**A green CI job that preserved nothing.** The corpus job added to stop discarding sweep
evidence ran on all 10 legs — 12 jobs, 0 failed, 10 artifacts, 10 SUMMARYs — and committed
**nothing**, printing *"corpus unchanged — nothing to commit."* `git diff --quiet -- <path>`
compares the worktree to the **index** and is silent about an untracked file, so on the run
that *creates* the corpus it reads "unchanged" and exits 0. Reproduced in a scratch repo
before changing anything. Fixed by staging first and diffing `--cached`, plus a hard failure
when the extractor reports success while the file is absent. This is the unasserted-
denominator shape inside the job written to prevent exactly that.

**A structural guard that could not fail.** `test_tp_is_checked_after_the_stop_in_source`
protects the SL-first intrabar convention (a bar through both levels takes the STOP — invert
it and losers become winners). It scanned a fixed window **forward** from the stop test,
which is escapable in the one direction that matters: hoisting the TP test *above* the stop
moves it out of the window. Verified by planting the inversion — **the old form passed.** Now
anchors on both comparisons and requires each to appear exactly once.

**And a measurement bug in the floor analysis itself:** rounding p to 5 decimal places made
distinct p-values collide at exactly `0.0`, manufacturing ties and moving the reported floor
from **2.0 to 0.41** — a wrong recommendation out of a formatting choice.

---

## 6. What I would do next, in order

1. **Sweep the remaining ~34 trail legs** (free, ~5 min, $0). This is the only thing that
   makes the Path B floor answerable, and it also gives the § 2 IS/OOS split a real
   denominator instead of 8.
2. **Re-run the floor analysis on that corpus** and report whichever of the three verdicts
   the data gives — `insufficient_population` remains an acceptable outcome.
3. **Isolate the § 1 corollary**: was `eth_pullback_2h`'s base-rate move from 0.40 to 2.42
   caused by the decay declaration, or by the config-exactness fix? One counterfactual run
   answers it, and the answer decides whether a floor is needed at all.
4. **Leave the `trail6` trio alone** unless one of them clears on its own leg's evidence.

---

*Nothing in this document changes `config/`. Every Tier-3 call above is a proposal.*
