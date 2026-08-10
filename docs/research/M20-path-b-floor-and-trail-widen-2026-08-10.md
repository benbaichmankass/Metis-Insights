# M20 overnight: the Path B floor, and what the fleet sweep actually says

**Session** `session_011iUN3roukhbRWwuioX8pRD` · **2026-08-10 overnight** · Tier-1 throughout
(no `config/` change; the one Tier-3 change of the day — `eth_pullback_2h` — merged
earlier with operator approval and is verified live in § 4).

**Operator directive this answers** (2026-08-10): *"let's try and use any optimization
of the capital utilization and PnL to decide what the correct number is there …
database decisions and not arbitrary guesses."*

---

## 1. The headline: do not set a Path B floor yet, and here is the number that says so

**The floor cannot be derived from the current corpus, and the blocker is not sample
size in the usual sense — it is that the predictor is the wrong shape for the data we
have.**

A Path B floor would sit on the base book's **net_R-per-drawdown rate**. That rate is a
property of the **LEG**, not the cell: every cell swept on `eth_pullback_2h` carries that
leg's single value. Tonight's sweep produced roughly **11 walk-forwarded cells across 9
legs** — which is ~11 rows over **nine distinct predictor values**, and the cells within a
leg are re-measurements of one book under different levers, sharing its trades, its regime
and its drawdown.

Feeding those rows to a test that assumes independence inflates the effective sample by
the cells-per-leg ratio and returns a confident-looking p for what is really a nine-point
comparison. **That failure would be worse than guessing the floor, because it would arrive
dressed as significance.** `scripts/research/m20_path_b_floor.py` therefore refuses a floor
that does not separate ≥ 4 distinct legs per side and reports `insufficient_population` —
*we did not look*, explicitly distinguished from `no_separation` (*we looked and found
nothing*).

**What would change the answer:** the census default sweeps **10 of the 44 trail legs**.
Sweeping the remaining ~34 is free (GitHub runners, ~5 min wall-clock, $0) and would take
the leg count from 9 to ~40 — enough for a floor to be testable at all. That is the
recommendation: **widen the population before setting the number, not the number before
the population.**

**Corollary worth stating plainly:** the case that motivated the floor has weakened. The
motivating example was `eth_pullback_2h vt_cold10_t2.5` clearing with **+43.59R of
headroom** on a book earning 6.62R against a 16.41R drawdown (rate **0.40**) — "almost
anything clears a book that inefficient." On tonight's corrected, config-exact base that
leg's IS book reads **30.63R against 12.69R (rate 2.42)**. The permissive case may largely
be an artifact of the base defect that was fixed today rather than a standing property of
the criterion. **I have not isolated that** — the intervening change to that leg was the
Tier-3 decay declaration, and I did not run the counterfactual — so treat it as a
hypothesis with a clear test, not a finding.

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
