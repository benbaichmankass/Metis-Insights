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

## 3. Pricing the trade: MAR improves on 9 of 10

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
- **Not** that `sm1.5` is the answer. `sol_4h sm1.5` is the one cell of the ten
  whose MAR gets **worse** (4.02 → 3.09), and `sm2` beats it on that leg.
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
