# The donchian stop sweep: 77/77 fail the gate, and the binding constraint is DRAWDOWN

**Date:** 2026-08-23 · **Tier-1, observe-only.** No `config/strategies.yaml`
change is made or proposed here as applied. Any stop change is **Tier-3**.

**Question asked (operator, 2026-08-23):** tighten the donchian family's
`atr_stop_mult` toward 1.5–2.0, **per-leg, with its own dispersion test** — never
on the argmax.

**Answer: the dispersion test never becomes reachable, and that is the finding.**
Zero cells clear the IS/OOS gate, so there is nothing to dispersion-test — but
**not** because no cell helps. Ten cells improve net R in *both* halves and every
one of them is refused on **max drawdown**.

**Tooling:** `scripts/research/e35_bracket_geometry_sweep.py` (unmodified) over
candles from `data.binance.vision` via the repo's own
`scripts/ops/fetch_backtest_candles.py`. Rows extracted by the new
`scripts/research/e35_corpus_extract.py` into
[`e35-bracket-corpus.jsonl`](./e35-bracket-corpus.jsonl) — **2,189 cells, 11 legs**,
so every number below is re-interrogable without a re-run.

---

## 0. Why this had to be re-run at all

The 2026-08-20 sweep measured 3,781 cells and **none of them were readable**:
its evidence went to Actions artifacts, which a PM-side session cannot download,
and `m20-sweep-corpus.jsonl` carries only LEVER cells (1,379 rows, 331 donchian,
**zero** naming a stop — counted). Filed as
`BL-20260823-E35-SWEEP-EVIDENCE-HAS-NO-DURABLE-PATH` and **fixed in the same
change**: `e35-bracket-sweep.yml` now has a `corpus` job that extracts and
commits. This document is the first consumer of that corpus.

**Substrate:** 6 symbols × 1h, **43,800–43,920 rows each, 2021-08-19 →
2026-08-22**. The original ran 2021-08-16 → 2026-08-19; SOL and XRP carry 120
fewer bars (5 days), the same gap the original reported.

**The re-run reproduces the original**, which is what licenses comparing them:
`trend_donchian` base 23.78 R vs the committed 20.28, spread **92.73 vs 90.72**,
and the **same argmax cell** `sm1.5_to400` (Δ +29.40 vs +27.07).

---

## 1. The family pattern replicates — independently

| | |
|---|---|
| argmax cells that move the **stop** | **11 / 11** |
| choosing `sm1.5` | **8** |
| choosing `sm2.0` | **3** |
| choosing `sm3.0` or `sm3.5` | **0** |

Same split the committed report shows, from a fresh fetch and a fresh run. The
donchian family's preference for the two tightest rungs is a real regularity, not
an artifact of one sweep.

⚠️ **It is still an argmax over 199 cells per leg.** Replication says the
*direction* is stable; it does not make any single cell a result. That is what
the gate below is for.

---

## 2. 77 of 77 gated cells fail — and the reason is not what it looks like

Every gated cell across all 11 legs returns `is_oos_fail`. Read alone, that says
"the geometry does not generalise". The failure **reasons** say otherwise:

| IS reason | OOS reason | n |
|---|---|---|
| `maxdd_worse` | `net_r_worse+maxdd_worse` | 17 |
| `tie_no_improvement` | `tie_no_improvement` | 12 |
| `net_r_worse` | *(none)* | 9 |
| `maxdd_worse` | *(none)* | 5 |
| *(none)* | `net_r_worse+maxdd_worse` | 5 |
| … | … | … |

`maxdd_worse` appears in the majority. Splitting the cells by *net R alone*:

| | n |
|---|---|
| worse on net R in **both** halves | 7 |
| worse in IS only | 16 |
| worse in OOS only | 29 |
| **better on net R in BOTH halves** | **10** |

⚠️ **Correction to my own first reading.** I initially counted 25 here, using
`>= 0`, which swept in ties (`tie_no_improvement` ×12 are inert cells that changed
nothing). Strictly better in both halves is **10**. The looser count would have
overstated the case by 2.5×.

**All 10 die on `maxdd_worse`.** Not one of them is refused on returns.

---

## 3. Pricing the trade on FULL history — superseded by § 3.5

⚠️ **Read § 3.5 before using this table.** It prices the same ten cells on the
per-half basis the gate actually judges on, and three of the readings below do
not survive that. This section is kept as the record of the coarser lens, not
as a result.

`d_max_dd > 0` means drawdown **grew** (verified against the stored base/cell
values, not assumed).

| leg | cell | net R | maxDD | MAR base → cell |
|---|---|---|---|---|
| `trend_donchian_1h` | `sm2_to96` | 2.31 → **49.29** | 25.55 → 29.95 | 0.09 → **1.65** |
| `trend_donchian_ada_4h` | `sm1.5` | 23.01 → **55.47** | 12.88 → 19.01 | 1.79 → **2.92** |
| `trend_donchian_1h` | `sm2` | 2.31 → 28.59 | 25.55 → 27.64 | 0.09 → **1.03** |
| `trend_donchian_sol_4h` | `tp1.5_sm2_to96` | 33.59 → **58.34** | 8.35 → **6.92** | 4.02 → **8.43** |
| `trend_donchian` | `sm2` | 23.78 → 42.72 | 24.77 → 26.73 | 0.96 → **1.60** |
| `trend_donchian_ada_4h` | `sm2` | 23.01 → 36.14 | 12.88 → **11.04** | 1.79 → **3.27** |
| `trend_donchian_sol_4h` | `sm1.5` | 33.59 → 43.45 | 8.35 → 14.06 | 4.02 → 3.09 |
| `trend_donchian_sol_4h` | `sm2` | 33.59 → 41.56 | 8.35 → **7.92** | 4.02 → **5.25** |
| `trend_donchian_xrp_4h` | `sm1.5` | −15.83 → −12.55 | 21.54 → **19.85** | −0.73 → −0.63 |

**MAR improves on 9 of 10.** Three cells improve **both** return and
full-history drawdown — `sol_4h tp1.5_sm2_to96`, `sol_4h sm2`, `ada_4h sm2` —
and still failed, because the gate judges maxDD **within each half** and drawdown
grew in one of them.

This is the class `m20_banking_risk_adjusted.py` exists to disambiguate: its own
docstring records that `honest_negative` covers *"a return-for-smoothness trade,
a both-axes loss, and an inert rung"* and *"the gate reports all three
identically"*. Here the gate is reporting a **return-for-drawdown trade** in the
same voice it uses for a both-axes loss. They are not the same object.

⚠️ **MAR is not a promotion criterion in this repo**, and quoting it is not a
proposal. It is used here only to show that the refused cells are not junk — the
return gain outpaces the drawdown cost on 9 of 10.

---

## 3.5 Priced properly: the per-half read, which corrects § 3

⚠️ **§ 3 above prices these cells on FULL history. The gate does not.** It
judges each half separately, so a full-history MAR is a coarser lens than the
one the verdict came from — and on three cells the two lenses disagree in the
direction that matters.

Priced with the repo's own tool rather than a second implementation:
`scripts/research/e35_verdicts_adapter.py` reshapes the sweep's per-half rows
(absolutes from `results.jsonl`; verdict strings from `report.json`, each from
the file that owns it) and `scripts/research/m20_banking_risk_adjusted.py`
does the arithmetic. Population: the **10** cells whose net R improves in
**both** halves, out of 77 gated, out of 2,189 measured.

| leg | cell | IS net R b→c | IS MAR | OOS net R b→c | OOS MAR |
|---|---|---|---|---|---|
| `trend_donchian_sol_4h` | `tp1.5_sm2_to96` | 25.55 → **46.88** | 4.25 → **7.41** | 6.14 → **10.00** | 0.73 → **1.44** |
| `trend_donchian_ada_4h` | `sm2` | 10.42 → 20.50 | 0.81 → **1.86** | 10.08 → 12.76 | 1.09 → **1.23** |
| `trend_donchian_ada_4h` | `sm1.5` | 10.42 → 32.03 | 0.81 → **1.68** | 10.08 → 19.94 | 1.09 → **1.54** |
| `trend_donchian_ada_4h` | `sm1.5_to400` | 10.42 → 32.03 | 0.81 → **1.68** | 10.08 → 19.94 | 1.09 → **1.54** |
| `trend_donchian_1h` | `sm2` | 9.12 → 27.28 | 0.36 → **0.99** | −5.72 → **2.43** | −0.33 → **0.15** |
| `trend_donchian_1h` | `sm2_to96` | 9.12 → 44.48 | 0.36 → **1.49** | −5.72 → −1.45 | −0.33 → −0.07 |
| `trend_donchian` | `sm2` | 35.99 → 51.06 | 2.14 → 2.02 | −11.13 → −7.26 | −0.47 → −0.32 |
| `trend_donchian_sol_4h` | `sm1.5` | 25.55 → 26.73 | 4.25 → 1.90 | 6.14 → 13.54 | 0.73 → **1.72** |
| `trend_donchian_sol_4h` | `sm2` | 25.55 → 25.73 | 4.25 → 3.25 | 6.14 → 13.45 | 0.73 → **2.15** |
| `trend_donchian_xrp_4h` | `sm1.5` | −9.93 → −7.05 | −0.68 → −0.40 | −4.34 → −3.42 | −0.43 → −0.33 |

**Three corrections to § 3, all in the unflattering direction:**

1. **`trend_donchian_1h sm2_to96` is not the star** its full-history MAR
   (0.09 → 1.65) made it look. Its **out-of-sample book is still negative**
   (−5.72 → −1.45R, MAR −0.07). It makes a losing half lose less. That is not
   a candidate, and the full-history number was hiding it.
2. **`sol_4h sm2` and `sol_4h sm1.5` degrade IS MAR** (4.25 → 3.25 and
   4.25 → 1.90) even though `sm2`'s full-history MAR *improved*. In the larger
   half they buy **+10.7R and +6.8R of drawdown per +1R of return** — the
   return-for-drawdown trade in its worst form, and exactly what this pass was
   run to separate out.
3. **`trend_donchian sm2` and `xrp_4h sm1.5` operate on books that are negative
   out-of-sample either way.** Improving them is not the same as making them
   work.

**What survives both halves on MAR *and* leaves a positive OOS book: four
distinct cells** — `ada_4h sm1.5` (≡ `sm1.5_to400`, the 400-bar timeout is
inert on this leg), `ada_4h sm2`, `sol_4h tp1.5_sm2_to96`, `trend_donchian_1h
sm2`. Not nine, and not the nine § 3 named.

### The cell that matters for the bracket question

**`sol_4h tp1.5_sm2_to96` is the only one of the ten that declares a real
target.** Every other cell moves the stop and/or the timeout and leaves
`tp_r = 50.0` — the sentinel — untouched. It is also the best risk-adjusted
cell in the corpus: IS MAR 4.25 → **7.41**, OOS 0.73 → **1.44**, and it costs
essentially no drawdown to get there (**+0.015R of drawdown per +1R of return**
in-sample; drawdown **improves** out-of-sample, −0.371).

That is one leg and one cell, and it is not a promotion proposal. But it is the
first measured instance of the governing claim in this thread: the cell that
**constructs** a bracket — real target, tighter stop, bounded hold — outperforms
every cell that only widens or tightens one axis around a sentinel. The stop and
the target are not independent choices, and the arithmetic says why: the venue
clamp is `cap_r = 0.099 × entry / (atr_stop_mult × ATR)`, so tightening the stop
from 2.5 to 2.0 is not merely a return lever — it is what **raises the reachable
target** and makes a 1.5R expectation placeable at all.

⚠️ **`dd_per_r` is null for every cell in this table**, and that is the tool
behaving correctly, not a gap. It is defined only in the banking direction
(surrender return, buy smoothness). These cells gain return and pay drawdown —
the mirror object — so the ratio is quoted the other way up above.

---

## 4. Widening the grid would not address this

The operator's pre-registered fallback was *"widen the grid and re-run"* if
nothing survived. Measured against this corpus, that does not attack the binding
constraint:

| | n |
|---|---|
| cells beating base on net R | **533 of 2,187** (24.4%) |
| …and not worsening full-history maxDD | **272** (51.0% of the improvers) |

**Candidates are not scarce.** A pool of 533 improvers, half of which don't cost
full-history drawdown, is not a search that failed for want of grid points. More
cells would add more candidates to the same refusal and worsen the
multiple-comparisons problem the gate exists to control.

**What is actually undecided is a risk-appetite question**: whether a
return-for-drawdown trade is acceptable on these legs. No amount of sweeping
answers that, and it is Tier-3.

---

## 5. What I am NOT claiming

- **Not** that the gate is wrong. Refusing a drawdown increase is a defensible
  rule; the point is that its verdict string does not distinguish *this* refusal
  from a cell that simply does not work.
- **Not** that any cell is shippable. None has been dispersion-tested — none
  could be, since dispersion runs on gate survivors and there are none.
- **Not** that `sm1.5` is the answer. On the per-half basis (§ 3.5) **three**
  of the ten degrade in-sample MAR — `sol_4h sm1.5`, `sol_4h sm2`, and
  `trend_donchian sm2` — and two more operate on books that stay negative
  out-of-sample either way.
- **Not** that the four § 3.5 survivors are shippable. They are the cells
  worth an operator decision, not cells that have earned one.
- **Not** a demotion or promotion of anything.

## 6. What would move this forward

1. **An operator decision on the drawdown trade** — the only genuinely blocked
   item, and Tier-3.
2. If that decision is "yes, within a bound", the gate's Path-A allowance is the
   parameter to argue about, and `m20_path_b_floor.py` already exists to test
   whether a floor is supportable rather than guessed.
3. Dispersion becomes runnable **only** once cells clear the gate. Running it on
   a gate-failed cell would be measuring the stability of something already
   refused.

4. **The bracket half is separable from the drawdown question, and cheaper.**
   `sol_4h tp1.5_sm2_to96` (§ 3.5) is the only measured cell here that replaces
   a sentinel with a declared target, and it is the best risk-adjusted cell in
   the corpus. Whether the fleet's stops should be tightened for *return* is the
   Tier-3 drawdown argument; whether a leg should carry a **declared expectation
   at all** is the question this thread opened with, and this cell is evidence
   the two are the same lever seen from two sides — the stop sets the reachable
   target through `cap_r`, so a leg cannot be given a real bracket without also
   moving its stop.

5. **A declared entry bracket is the PRECONDITION for active management, not an
   alternative to it.** `_base.monitor` has declared a `{"tp": float}` verdict —
   *move the take-profit* — since it was written, and no strategy has ever
   produced one; `target_extension_soak` (2026-08-23) is now the annotate-only
   producer, and its `sentinel_no_expectation` / `no_expectation_declared`
   states are precisely the sentinel legs. A leg running a sentinel bracket
   behind a trail is not *managing* the trade — it has no expectation to
   revise, so the revision machinery is inert on it by construction. Declaring
   the entry expectation is what turns the trail from *the whole exit policy*
   into *one input to a revisable one*.
