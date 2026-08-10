# M20 overnight: the Path B floor, and what the fleet sweep actually says

**Session** `session_011iUN3roukhbRWwuioX8pRD` · **2026-08-10 overnight** · Tier-1 throughout
(no `config/` change; the one Tier-3 change of the day — `eth_pullback_2h` — merged
earlier with operator approval and is verified live in § 5).

**Operator directive this answers** (2026-08-10): *"let's try and use any optimization
of the capital utilization and PnL to decide what the correct number is there …
database decisions and not arbitrary guesses."*

**Population for everything below:** the committed corpus
`docs/research/m20-sweep-corpus.jsonl` — **575 cells across 43 legs**, live-parity
geometry (capped TP 0.099), IS/OOS split `2025-07-01`. Every claim in this document
is reproducible from that file. The 8 `ict_scalp` legs are **not** in it (they need
their own dispatch — one full-history scalp run was timed at 955s).

---

## 1. The recommendation, in one line

**Do not set a base-rate floor.** It was measured over the fleet and it is
**unsupported** — and, more pointedly, the data leans the *opposite* way to the
premise. What the sweep did surface is a different and real exposure, quantified in
§ 3, whose remedy is a **structural bound**, not a fitted number.

Two things I claimed this morning from the 10-leg census are **false at fleet
scale**, and both were falsified by the tests I set up to check them:

| my claim (10 legs) | fleet result (43 legs) |
|---|---|
| "zero gradeable legs sit below rate 1.0 — the permissive case does not occur" | **6 of 36 do.** Lowest is `gdx_pullback_1d` at **0.454** — essentially the 0.40 that motivated the floor |
| "the trail-widen is a real IS effect (7 of 8, p=0.035)" | **25 of 42, p=0.14.** Not significant. The 7/8 was the census top-10 |

Reporting these plainly is the point of having run the test. The 10-leg answer was
not a lie, it was an **unstated denominator** — and the fix was more legs, which is
exactly what the run bought.

---

## 2. The floor: measured, and unsupported

### 2a. The case the floor exists for DOES occur

Six legs have a **profitable** base book that earns less than 1R per unit of
drawdown — precisely the band where the derived criterion (`allowed = D_b × dN/N_b`)
gets permissive:

| leg | base net_R (IS) | base maxDD (IS) | rate | n IS | n OOS |
|---|--:|--:|--:|--:|--:|
| `gdx_pullback_1d` | 5.18 | 11.41 | **0.454** | 83 | 7 |
| `mhg_pullback_1d` | 5.66 | 9.68 | **0.585** | 71 | 7 |
| `tlt_pullback_1h` | 20.90 | 27.78 | **0.752** | 468 | 56 |
| `eth_pullback_prop_2h` | 13.90 | 15.35 | **0.905** | 274 | 67 |
| `scha_trend_long_1d` | 5.57 | 5.78 | **0.964** | 63 | 5 |
| `trend_donchian_ada_4h` | 15.63 | 16.03 | **0.975** | 176 | 43 |

The remaining 7 of 43 legs have an unprofitable IS base and are refused outright by
`drawdown_exchange_rate` (`base_unprofitable` — **ungradeable is not a pass**), so
the gradeable denominator is 36.

### 2b. …but the rate does not predict whether a gain generalises

`scripts/research/m20_path_b_floor.py` against the fleet corpus:

```
analysed: 52 walk-forwarded cells across 22 legs   (515 never reached a
  walk-forward — no evidence about generalisation, excluded, NOT failures)
base_rate_IS: min 0.585 · median 2.41 · max 9.21 · overall WF pass rate 67%
15 floors tried · Bonferroni bar 0.00333 · best p = 0.608
VERDICT: no_separation
```

**WE LOOKED AND FOUND NOTHING** — distinct from `insufficient_population`, which is
what the 10-leg corpus returned. And read the grid's direction, not just its
p-values: at **14 of the 15 floors**, the arm the floor would REJECT generalises
*better* than the arm it would admit (63% vs 89% at 0.975; 43% vs 76% at 4.76). A
floor set at any of those removes the better-generalising cells.

*(Checked arithmetically rather than by eye, and the check caught me: I first wrote
"every one of the 15". The exception is floor **1.1619** — admitted 67.5%, rejected
66.7%, a 0.8-point gap over arms of 40 and 12, which is a tie in substance and not a
floor working. **14 of 15 is the honest number** and it does not change the
conclusion, but "every" was an overstatement of my own result and this is the kind of
number a reader would not re-derive.)*

### 2c. The second candidate — a `dN/N_b` cap — is also unsupported *as a predictor*

The floor is aimed at `base_rate`; the mechanism is the ratio. `allowed = D_b ×
(dN/N_b)` grants a cell that **fraction of the base book's entire drawdown**, so the
permissive case is not "the book is inefficient", it is "the cell's gain is large
relative to the book's". The two come apart cleanly in the data, and unlike the rate
the ratio varies **per cell**, so it is far better conditioned (52 distinct values vs
22).

I added it as a derived axis and tested it with the arms inverted (a cap keeps the
low side):

```
dn_over_nb_IS: min 0.003 · median 0.246 · max 1.697
42 caps tried · Bonferroni bar 0.00119 · best p = 0.421
VERDICT: no_separation
```

So **neither** candidate predicts generalisation. Any floor or cap chosen to improve
the walk-forward hit-rate would be the arbitrary guess the directive forbids.

---

## 3. The exposure that IS real — and it is a risk question, not a data one

The grant is not a hypothesis about generalisation; it is an **amount of drawdown**.
Measured over the 18 `path_b_wf_pass` rows — the exact population a Path B threshold
would promote from:

| leg · cell | rate | dN/N_b | **granted, as % of the base book's whole drawdown** |
|---|--:|--:|--:|
| `tlt_pullback_1h · trail4` | 0.752 | 1.697 | **170%** |
| `eth_pullback_prop_2h · decay_stall10_t1.8` | 0.905 | 1.085 | **108%** |
| `scha_trend_long_1d · decay_stall6_t2` | 0.964 | 0.974 | 97% |
| `mhg_pullback_1d · stale8_lt0R` | 0.585 | 0.834 | 83% |
| …13 more | | | 45% down to 2% |

**Four of eighteen are granted more than half the base book's entire drawdown, and
one is granted 1.7× of it.** At that point the allowance has stopped being a *share*
of the book's risk budget and become an *expansion* of it.

**This does not need a statistical separation to justify acting on, and it must not
pretend to have one.** It is a risk-appetite statement. What the data supplies is the
distribution to choose against — which is the same discipline
`gross-exposure-governance-DESIGN.md` § 6/§ 7 imposes on the exposure ceiling: never
ship a value with no measurement behind it, and put the bound above normal operation
and below the thing you actually fear.

**If you want one bound tonight, the non-arbitrary value is `dN/N_b ≤ 1.0`.** It is
structural rather than fitted — it is the exact point where a cell stops asking for a
fraction of the base drawdown and starts asking for more than all of it — and it is
the only value in the range with a justification that isn't "it looked about right".
It binds **1 of 18** measured rows (`tlt_pullback_1h trail4`), so it costs almost
nothing today and bounds the tail. Anything tighter (0.5, 0.3) has **no** evidence
behind it — § 2c measured exactly that and found none.

Tier-3, your call. My recommendation: **set 1.0, and only 1.0.**

---

## 4. Two defects in the sweep's own reporting, found by reading the fleet output

**`path_b_wf_pass` does not mean the drawdown-rate gate passed — and 6 of 18 rows
don't pass it.** The verdict name says only "the net_R gain generalised across
folds"; the code comment disclaims the rest explicitly. But at fleet scale the two
visibly diverge:

- `slv_pullback_1d stale8_lt0R`, `slv_trend_1h decay_stall10_t2`,
  `qqq_pullback_1h trail6`, `uso_trend_1h vt_cold10_t2` — **negative IS headroom**
  (rate gate FAILS in-sample), verdict still `path_b_wf_pass`.
- `ada_pullback_2h vt_hot80_t2.5` — negative OOS headroom, same verdict.
- `tlt_pullback_1h trail4` — OOS base is **unprofitable**, so its OOS rate is
  **ungradeable**, and it is nonetheless the largest grant in the fleet (170%).

Nobody reading a table of `path_b_wf_pass` rows would guess a third of them fail the
gate the surrounding prose describes. That is a label not describing what was
computed — the same class the repo's `diagnostic-provenance-guard` exists for.

**FIXED in this PR** (Tier-1): the sweep now carries `path_b_rate_ok` on every Path B
entry as a **three-state** value — `True` / `False` (a window was graded and said no)
/ `None` (no window was gradeable at all) — splits the roll-up count three ways
(`path_b_wf_pass_rate_{ok,failed,ungradeable}`), states the divergence in the report
header, and adds a **`grant%`** column (`allowed` as a share of the base book's entire
drawdown), which is the number § 3 turns on and which was derivable from the existing
columns but never derived. `False` and `None` are deliberately not merged, and the
extractor passes the value through rather than `bool()`-ing it — a row written before
the field existed reads as `None`, never as passing.

**Path A has no minimum trade count, and it shows.** 9 of the 21 Path A PASSes sit on
an OOS base under 20 trades:

| leg | OOS base n | PASSes |
|---|--:|--:|
| `spy_trend_long_1d` | **3** | 4 |
| `qqq_trend_long_1d` | **4** | 2 |
| `scha_trend_long_1d` | **5** | 3 |
| `mgc_trend_1h` | 97 | 1 |
| `trend_donchian_1h` | 168 | 1 |

Four PASSes on a three-trade out-of-sample window is not evidence of anything.
Reported, not changed — `beats()` governs promotion and is Tier-3-adjacent, and this
is a decision about promotion standards rather than a bug.

---

## 5. Nothing from this sweep is recommended for promotion

- **Path A PASS: 21 rows / 9 legs** — but see § 4: nearly half sit on a
  single-digit OOS base. The one on a real denominator (`trend_donchian_1h
  vt_hot90_t2.5`, OOS n=168) is on a leg that reads `enabled: false` /
  `execution: shadow` — **retired, not shippable.** Flagged loudly because reading
  that PASS off the table and proposing it is the obvious mistake.
- **`path_b_wf_pass`: 18 rows** — no Path B threshold is set, and § 2 says none
  should be fitted. Six of them fail the rate gate anyway (§ 4).
- **The trail-widen**: at n=42 the IS effect is **25/42, p=0.14** (not significant)
  and OOS is **16/42, p=0.96** (leaning negative). The three `trail6` candidates that
  looked like a cross-leg signal at n=8 are what chance produces. **Leave them.**

A sweep whose output is "nothing ships" is a successful sweep. The alternative —
promoting a trio on a pattern that dissolves the moment its denominator is stated —
is how a fleet acquires levers that backtest well and do nothing.

---

## 6. Verification: the Tier-3 change from earlier today is live

`eth_pullback_2h` gained `trail_decay_stall_bars: 10` + `trail_decay_tight_mult: 2.5`
(merged `712274c`, operator-approved). Three independent confirmations, all passed:

1. **`/api/diag/version` → `git_sha: 712274ca`** — the deploy rolled forward.
2. **The sweep's own base moved.** `eth_pullback_2h decay_stall10_t2.5` now reads
   `tie_no_improvement` with **0.0 on every axis** — a cell that re-declares a lever
   the leg already carries scoring as exactly nothing is the signature that the
   deploy landed *and* the sweep base is config-exact. The same signature appears
   independently on `trend_donchian_eth` and `trend_donchian_eth_prop`.
3. **`exit_lever_soak` annotate rows for that leg stopped — while the position is
   still OPEN** (diag #8758). This was the check I refused to claim earlier, because
   absent annotate rows are consistent with two opposite causes. The diag resolves
   it: trades `4134` (`bybit_2`, **real money**) and `4135` (paper) are still open,
   and the 21:39Z/22:06Z soak tail carries rows for five other legs and **none** for
   `eth_pullback_2h`. Open + stopped is only consistent with the declaration landing.

---

## 7. What I would do next, in order

1. ~~Fold `rate_ok` into the sweep verdict~~ — **done in this PR** (§ 4).
2. ~~Sweep the 8 `ict_scalp` legs — the one where the capped-TP geometry bites
   hardest~~ — **dispatched, and the second half of that sentence is WRONG.**

   The scalp family has **no capped TP to bite**: `_TP_SENTINEL_CAP_PCT` lives in
   exactly four units (donchian, pullback, squeeze, fade) and
   `src/units/strategies/ict_scalp.py` contains **zero** occurrences of it —
   which is why `scalp` is deliberately absent from the sweep's
   `LIVE_TP_CAPPED_FAMILIES`. I wrote the dispatch premise ("the `Live TP reach`
   table is the PRIMARY output — does the 9.9% clamp bind on tight frames?") from
   how the pullback/trend legs behave, without reading the scalp unit. **That
   run cannot answer the question `.github/exit-lever-sweep-request` says it is
   for**, and the sentinel still says it — it is left uncorrected on purpose,
   because touching that file RE-FIRES the sweep.

   The run is still worth its cost: 8 legs of lever verdicts and base books
   extend the corpus, and `ict_scalp_xrp_15m` (IS n=227 / OOS n=134) and
   `ict_scalp_sol_15m` (280 / 142) are among the thickest denominators in the
   fleet — the opposite of the 3-trade equity windows in § 4.

   The same gap produced a live reporting defect, now fixed: the PR-comment
   banner read the RUN-LEVEL `--tp-cap-pct` flag and printed *"LIVE-PARITY
   (capped TP 0.099)"* on all 9 un-capped legs (8 scalp + `fvg_range_15m`),
   asserting a geometry the code never applied — on the one line whose entire job
   is to say which geometry produced the numbers below it.
3. **Decide the `dN/N_b ≤ 1.0` bound** (§ 3, Tier-3, your call).
4. **Leave `beats()` alone** until the minimum-n question in § 4 is decided
   deliberately rather than as a side effect of a floor discussion.

---

*Nothing in this document changes `config/`. Every Tier-3 call above is a proposal.*
