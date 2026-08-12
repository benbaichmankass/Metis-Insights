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

## 4b. The scalp family (added 2026-08-11) — and the one thing in it you should look at

The 8 `ict_scalp` legs swept after the fleet run; the corpus is now **604 rows / 603
cells / 51 legs** (`f4225f7`). 7 legs produced 28 cells — `ict_scalp_mgc_15m` skipped
on missing 15m frames (`BL-20260810-SWEEP-DATA-DIR-MISSING-MGC`), recorded as a
`leg_status` row rather than vanishing.

**Neither floor verdict moved, and the reason is worth stating**: both re-ran at
exactly `no_separation` over the *same* 52 cells / 22 legs. The scalp family added
**zero** rows to the analysed population — 2 of its 28 cells reached a walk-forward,
and **both lack `base_rate_IS`** because their IS base books are unprofitable. The
family that most needed a permissive criterion is the one the existing
`base_unprofitable` guard refuses outright.

**Lever verdicts: 26 `is_oos_fail`, 1 `wf_fail`, 1 `path_b_wf_pass`.** The two cells
up on both windows:

| leg · cell | verdict | note |
|---|---|---|
| `ict_scalp_5m · gb1R_afterMFE1R` | `wf_fail` | cleared IS+OOS on **both** axes (ΔnetR +3.19/+0.08, ΔmaxDD −3.19/−0.08), then failed the folds. The OOS gain is +0.08R against +3.19R in-sample — the exact shape the walk-forward exists to reject |
| `ict_scalp_sol_5m · be_touch_arm` | `path_b_wf_pass` | IS base **−77.07R** ⇒ rate **ungradeable**; only OOS is graded (grant 24%, headroom +2.68). The `tlt_pullback_1h trail4` shape again |

**And the lowest base rate in the entire fleet is a scalp leg**: `ict_scalp_sol_15m`
at **0.111** (base +1.98R over a 17.82R drawdown) — a quarter of the 0.40 that
motivated the floor. It produced **no Path B candidate at all**, so the most
permissive book in the fleet never once exercised the permissiveness. With the
scalps in, 7 of 39 gradeable legs sit below rate 1.0.

### 🔴 The finding that is not about exit levers at all

Measuring every leg's base book surfaced something the sweep was not looking for.
**4 of the 50 legs with a readable base are negative on BOTH windows**, and exactly
one of those is enabled, `execution: live`, **and routed to a real-money account**:

| leg | IS | OOS | status |
|---|--:|--:|---|
| **`ict_scalp_5m`** | **−48.88R** | **−13.31R** | enabled · live · **REAL MONEY (`bybit_2`)** |
| `ict_scalp_avax_5m` | −37.98R | −23.17R | paper only |
| `trend_donchian_1h` | −34.19R | −21.21R | `enabled: false` + shadow — inert |
| `fvg_range_15m` | −1.88R | −4.68R | routed to `bybit_2` but `execution: shadow` — places no order |

It is the most negative leg in the fleet *and* the only one of the four that can lose
real money. For context: 32 of 50 legs are positive on both windows.

**No exit lever fixes this, and the sweep proved it directly** — all 4 of that leg's
cells are honest negatives, and its one both-axes candidate failed its walk-forward.

**Stated with its limit:** this is the config-exact **backtest** base on live-parity
geometry, *not* live realized PnL, and the two must not be conflated — the live
`ict_scalp` journal rows carry a known fabricated-provenance problem
(`PB-20260807-ICTSCALP-STOP-DID-NOT-CONTAIN-8R`). Filed as
**`PB-20260811-ICTSCALP5M-REALMONEY-NEGATIVE-BOTH-WINDOWS` (P1)** for a
`/performance-review` pass to compare it against `totalPnlMeasured`. **Tier-3 either
way** — whether a leg keeps real-money routing is your call, not mine, and both
remedies (route to `bybit_1` paper, or `execution: shadow`) are one-line declares.

### 🟢 RESOLVED — the P1 is REFUTED, with a mechanism (2026-08-11, measured)

**Do not act on the table above for `ict_scalp_5m`.** I ran the comparison the
limit-statement called for, and it does not merely fail to confirm the concern —
it inverts it, and names the reason.

**The live real-money record** (`/api/bot/performance?window=all`, real-money only
by construction — paper and backtest rows excluded):

| | trades | win rate | totalPnl | totalPnlMeasured | pnlCoverage | totalR |
|---|--:|--:|--:|--:|--:|--:|
| `ict_scalp_5m` | **16** | **75.0%** | **+$13.53** | **+$13.53** | **1.00** | **+46.80R** |

It is the **best real-money leg in the book by PnL** (the `perStrategy` list is
sorted descending and it is first of 12), and **`pnlCoverage` is 1.00** — 16 of 16
rows MEASURED, zero fabricated, zero unverified. So the
`PB-20260807` fabricated-provenance caveat I attached **does not apply to this
population**; that was a reasonable hedge and it turns out to be unnecessary here.
The 30d window agrees in sign (4 trades, +$3.17, coverage 1.00).

**The mechanism — the two numbers measure DIFFERENT BOOKS.** This is not "n=16 got
lucky":

- `config/regime_policy.yaml` authors **two fully-off `trend_vol` cells** for this
  exact leg — `trending/volatile` and `chop/volatile`, both `long: false,
  short: false`. Live, it is **refused outright in volatile regimes**.
- It trades **BTCUSDT**, the one symbol where that gate actually bites: an advisory
  ML vol head resolves the label, `trend_vol` cells are authored, and BTC
  real-money enforce has been **live since 2026-06-28**.
- **My sweep measured it with the gate OFF.** `m20_fleet_exit_sweep` passes no
  `--regime-router`, so `backtest_system` takes its own default (`"off"`) and sets
  `REGIME_ROUTER_DISABLED=1`.

So **−48.88R is the ungated book — including precisely the volatile-regime trades
production refuses — while +46.80R is the gated book the leg actually trades.** The
gate was authored to remove that losing regime, and the live record is what it
looks like when it works. The low live trade count (16 lifetime) is the same fact
seen from the other side.

**What was wrong with the P1, precisely:** not the arithmetic — the base figures are
correct for what they measured — but the *population*. I compared a live routing
decision against a book that routing does not produce, and the corpus recorded
nothing that would have caught it (see § 4c). The limit I stated was the right
instinct aimed at the wrong risk: I hedged on provenance, and the defect was the
regime book.

**No Tier-3 action is warranted.** Neither remedy should be applied. The leg keeps
its real-money routing on this evidence. What remains open is the honest residual:
**16 trades is a thin sample**, so this refutes the concern rather than
establishing the leg as good — it belongs in the `/performance-review` cadence as a
leg to keep watching, not as a P1.

### 4c. Every base book in this corpus is the UNGATED book

The above generalises, so it is stated once here rather than per-leg. All 604 rows
were measured at `--regime-router off` while the live router is **baseline-on**, and
until 2026-08-11 nothing in the corpus recorded that — zero `regime` keys. That is
`diagnostic-provenance-guard` sub-class **B** (a function default substituted for
the live input) plus **C** (nothing in the output reveals it).

**What survives, and what does not:**

- ✅ **Every DELTA comparison survives intact.** Both arms of a cell share the same
  ungated base, so `d_net_r`, `d_max_dd`, Path A's `beats()` and the walk-forward
  are all comparisons over ONE consistent population. No lever verdict in this
  document changes.
- ❌ **Base-book LEVEL reads do not survive** for a policy-named leg. `base_net_r`,
  `base_rate`, and therefore **Path B's derived tolerance `D_b × (dN/N_b)`**,
  describe a book production refuses to trade. The § 4 table above is exactly such
  a read, which is how the P1 happened.

**Bounded: 6 of 51 legs / 56 of 604 rows** are named in `regime_policy.yaml`
(`fvg_range_15m`, `gld_pullback_1h`, `htf_pullback_trend_2h`, `ict_scalp_5m`,
`squeeze_breakout_4h`, `trend_donchian`).

**§ 1's answer is ROBUST to this — measured, not asserted.** Only 2 of the 22
analysed legs are policy-named (`gld_pullback_1h`, `htf_pullback_trend_2h`;
`ict_scalp_5m` is not among them, since its unprofitable IS book already left it
ungradeable). Re-running both predictors with those legs excluded — 52 → 45 cells,
22 → 20 legs — leaves **both at `no_separation`**. **Do not set a floor** stands.

The corpus, the sweep and the floor analysis now all record and report this;
`regime_router` joins the measurement-identity key so a future gated run can never
be averaged together with an ungated one.

---

## 4d. 🔴 THE GATE HAS NO MINIMUM TRADE COUNT, and 82% of what it passes is thin

This is the largest general caveat on this whole document, and it is not about any one
leg. Measured over the 603 corpus cells with a readable OOS base:

| OOS base trades | cells | share |
|---|--:|--:|
| **< 10** | **216** | **35.8%** |
| < 25 | 226 | 37.5% |
| < 50 | 393 | 65.2% |
| < 100 | 533 | 88.4% |
| < 168 | 559 | 92.7% |

median **34** · mean 50.0 · max 358.

**Of the 40 cells this sweep PASSES (Path A or `path_b_wf_pass`), 33 — 82% — sit on an
OOS base under 50 trades, and 13 sit on fewer than 10.** Path A's `beats()` has **no
minimum trade count**, so a cell clears it by improving net_R and maxDD over a book
that may contain **three trades**, and can then post a "6/6 walk-forward" over folds
that are nearly empty.

Concrete instances from § 5's PASS list: `spy_trend_long_1d vt_hot90_t2` passes on an
OOS base of **3 trades** (ΔnetR +0.80R, ΔmaxDD **0.0**, wf 6/6), and
`qqq_trend_long_1d vt_hot90_t2` on **4 trades** (ΔnetR **+0.10R**, ΔmaxDD 0.0, wf 6/6).
A ΔmaxDD of exactly 0.0 is the tell that the lever never touched the drawdown path.
**A 6/6 walk-forward over 4 trades is not evidence; it is the shape that manufactures a
confident wrong conclusion** — the same thing the regime-selectivity skill's Rule 3a
says about a verdict over zero trades ("not a negative finding; it is no finding").

**This calls for a min-trades floor, and — unlike the Path B rate floor in § 1 — that
is NOT a fitted threshold.** § 1 refused to fit a floor because a *rate* floor is a
prediction claim and the data refused to support one. A minimum trade count is a
different kind of object: a **denominator requirement**, the same shape as
`research_results_gate.min_trades`, which the repo already ships. It needs no
separation test.

**Per this repo's own procedure for an unset threshold, the distribution was REPORTED
and the value was the operator's** (`exit-refinement` SKILL: *"The first sweep REPORTS
the distribution; the operator sets the two values from it"*). Fitting one from the same
corpus it will judge would be the exposure-ceiling mistake.

### ✅ SET: `MIN_OOS_TRADES = 25` (operator decision 2026-08-11)

The full cost curve, which is what the value was chosen from — **not** a fit:

| floor | legs surviving | cells | passes |
|---|--:|--:|--:|
| 0 (as shipped) | 50 / 51 | 603 | 40 |
| **10** | 34 / 51 | 387 | **27** |
| **25 ← SET** | 32 / 51 | 377 | **27** |
| 50 | 20 / 51 | 210 | 7 |
| 100 | 10 / 51 | 70 | 2 |

**The curve is not smooth, because `base_trades_OOS` is a property of the LEG, not the
cell** — a floor deletes whole legs (~12 cells each) rather than filtering weak cells.
Two things follow, and they decided the value:

- **10 → 25 is free**: two legs, **zero** passes. And floor 10 already kills all 13
  thin passes — every one sits on ≤ 7 OOS trades (`spy`/`qqq`/`scha_trend_long_1d`,
  `slv`/`mhg_pullback_1d`) at ΔnetR +0.10R…+1.75R with ΔmaxDD of **exactly 0.0**.
- **25 → 50 is the cliff**: 32 → 20 legs, 27 → 7 passes. And a floor of 50+ would
  **structurally exclude every daily-timeframe leg**, which cannot reach 50 trades in a
  ~1y OOS window — rejecting them for bar size, not for a bad lever.

So 25 is the last point before coverage is paid for.

**It has its own verdict, `insufficient_base` — never folded into `is_oos_fail`.** "We
did not look at enough trades" and "we looked and the lever failed" are opposite
findings; the cell's numbers are still recorded (they are evidence), `would_have_been`
records what the verdict would have been so the floor's effect is auditable rather than
invisible, and the walk-forward is skipped (it would measure the same too-thin book, and
it is the expensive step).

**It does NOT retroactively question anything live.** `eth_pullback_2h` — the leg
carrying the operator-approved Tier-3 trail lever — has an OOS base of **65 trades**,
clearing every floor considered.

**The existing 604-row corpus keeps `min_oos_trades_floor: null`**, which is *"ungraded
by any floor"* and NOT floor 0; recording 0 would assert that every thin cell in it had
been considered and admitted. The field joins the measurement-identity key, because the
same cell graded unfloored and graded at 25 can carry **different verdicts**.

**HONEST LIMIT, restated because it survives the decision:** this floor is a proxy for
the statistic that actually matters — how many trades the **lever** fired on, and
whether the effect exceeds its own noise. ΔmaxDD of exactly 0.0 is the lever reporting
that it barely fired, and a base-trade floor would **not** catch a cell on a 200-trade
base that modified two exits. The corpus records no per-cell fire counts; that gap is
open.

**Until a floor is set, read every PASS in § 5 beside its OOS base.** The one PASS in
this document on a genuinely solid denominator is `trend_donchian_1h vt_hot90_t2.5`
(OOS base **168**) — and that leg is `enabled: false` / `execution: shadow`, retired
and unshippable, which is why § 5 already flags it.

## 4e. Regime-conditioned exits: suggestive, NOT established (task #6)

`vol_trail` (`vt_hot90` / `vt_hot80` / `vt_cold10`) already *is* regime-conditioned exit
selection — it keys the trail multiple on a **causal ATR percentile rank** over a
200-bar window (`scripts/backtest_pullback.py:263`), computed per bar. Measured across
41 legs / 123 cells:

| population | cells | Path A PASS | PASS or `path_b_wf_pass` |
|---|--:|--:|--:|
| vol-conditional (`vt_*`) | 123 | 8 (**6.5%**) | 13 (10.6%) |
| all other exit cells | 480 | 13 (**2.7%**) | 27 (5.6%) |

**Fisher exact p = 0.052** (Path A) / **0.065** (either path) — and that is *before* any
correction for the fact that this comparison was chosen after seeing the data. **So:
not supported at this n.** A ~2.4× pass-rate ratio is a lead worth a purpose-built
test; it is not a result.

**A hypothesis I had for the gap, and it is REFUTED.** I expected conditioning to thin
the population each cell acts on, inflating spurious passes. It does not: thin OOS
bases are **fleet-wide**, not specific to conditioned cells — median OOS base 32
(`vt_*`) vs 34 (everything else), 68.3% vs 64.4% under 50 trades. Among *passing* cells,
29 vs 32 median. Conditioning is not measurably thinner, so thinness does not explain
the gap. It is § 4d's problem, and § 4d applies to both arms equally.

**Two designs exist and are not interchangeable** — worth recording before anyone
builds:

- **(A) condition on realized vol** (what `vol_trail` already does). Self-contained,
  causal, no ML dependency, implementable in the live monitor from candles. **No
  axis-fidelity problem**, because it is not trying to match the router's axis.
- **(B) condition on the router's regime label** (`ml_vol_regime_for_symbol` →
  `predict_proba(row)["volatile"]`). Matches the *entry* gate's axis, but inherits the
  advisory head's per-symbol availability (BTC + SOL only) and carries the full Rule-3
  axis-fidelity burden.

**Recommendation: neither, yet.** (A) is already measured and does not clear a
significance bar; (B) adds an ML dependency to the exit path to chase an effect (A)
has not established. #6 should stay parked until § 4d's floor is set, because **the
floor decides whether any of these passes are real** — 12 of the 13 `vt_*` passes sit
under 50 OOS trades.

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
