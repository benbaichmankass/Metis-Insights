# Regime-Debt Matrix — corrected-cost re-grade (2026-07-30)

**Run:** [issue #7955](https://github.com/benbaichmankass/Metis-Insights/issues/7955) ·
workflow `regime-debt-matrix` run
[30519330796](https://github.com/benbaichmankass/Metis-Insights/actions/runs/30519330796)
(free runner, 730d, on `f67df73`)
**Upstream (over-charged) baseline:** [`regime-debt-matrix-equity-futures-2026-07-29.md`](regime-debt-matrix-equity-futures-2026-07-29.md) (#7918) +
[`regime-cell-walkforward-2026-07-29.md`](regime-cell-walkforward-2026-07-29.md) (#7920–#7924)
**Tracking:** `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE` ·
`BL-20260730-REGIME-CELL-UNAUDITABLE`
**Tier:** analysis only. **No cell is authored or revoked by this document.**

## Why this re-run

`regime_debt_matrix.build_harness_cmd` passed a hardcoded `--fee-bps-roundtrip 7.5`
to **every** symbol, including all 14 commission-free `(alpaca, spot)` instruments;
`regime_cell_walkforward` inherited it. #7930 fixed the identical bug in the live
close path on 2026-07-29 but missed this harness — the one that *sources Tier-3
regime cells*. Fixed as T1 in #7944 (`f67df73`); this is the re-grade.

Over-charging can only make a strategy look **worse**, so the bug's signature is
**false OFF cells** — never a fabricated edge.

---

## 1. ⚠️ The comparison is NOT a clean fee attribution

State this before the numbers, because it bounds what they can prove.

The matrix window is a **rolling 730 days**, and the two runs are ~a day apart. So
each delta below mixes **two** effects: the fee correction, and a ~1-day window
slide that can swap a trade in or out of a cell. **These are not separable from these
two runs alone.**

The evidence that the fee effect nonetheless dominates at usable n:

- The predicted phantom drag was **0.04–0.12 R/trade**
  (`fee_r = (bps/1e4) × price / risk`, with `risk = atr_stop_mult × ATR`).
- **Four of five** measurable cells land inside that band, all in the predicted
  (improving) direction.
- The **one** cell that moved the wrong way has **n=10**, where a single swapped
  trade is worth ~±1R — i.e. drift noise of the same order as the whole delta.

**The clean experiment has not been run:** one matrix pass, same window, two fee
settings. That is cheap (a second `--fee-bps-roundtrip` arm) and is the only way to
attribute the delta rather than infer it. **Filed as a follow-up — do not treat the
per-cell deltas below as measured fee amounts.**

## 2. The diff — corrected vs over-charged (matrix cells, identical n)

| Strategy · cell | Fidelity | Over-charged (#7918) | Corrected (#28) | Δ net-R | Δ per trade |
|---|---|--:|--:|--:|--:|
| `qqq_pullback_1h` trending **short** | faithful | −2.99 @ 41 | **−0.85 @ 41** | **+2.14** | **+0.052** ✅ in band |
| `qqq_pullback_1h` trending **long** | faithful | −3.08 @ 39 | **−0.35 @ 39** | **+2.73** | **+0.070** ✅ in band |
| `qqq_pullback_1h` trending **total** | faithful | −6.06 @ 80 | **−1.20 @ 80** | **+4.86** | **+0.061** ✅ in band |
| `slv_trend_1h` trending **short** | faithful | −5.61 @ 23 | **−4.70 @ 23** | **+0.91** | **+0.040** ✅ in band |
| `spy_pullback_1h` trending **short** | *approximate* | −9.38 @ 25 | **−7.96 @ 25** | **+1.42** | **+0.057** ✅ in band |
| `slv_trend_1h` chop **short** | faithful | −5.07 @ 10 | **−5.63 @ 10** | **−0.56** | **−0.056** ❌ wrong sign |

The wrong-signed row is the honest one to dwell on: a fee *reduction* cannot make a
cell worse, so that −0.56R is **window drift, not fees** — and at n=10 it is roughly
one trade. It is the direct evidence for §1's caveat.

## 3. Dispositions

### 3a. `qqq_pullback_1h` trending short — **WITHDRAW the offered cell** ✅ prediction confirmed

The prediction recorded in `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE` was that
qqq's entire reported drag sat inside the phantom-fee band and was therefore likely a
**false OFF**. Confirmed:

- reported **−0.073 R/trade** → corrected **−0.021 R/trade** (a ~3.5× shrink)
- the walk-forward that let it "survive weakly" was run on the over-charged pooled
  −2.84R; at −0.021 R/trade the drag is **inside noise**
- the **long** side also improved (−3.08 → −0.35), so the whole trending cell is
  ~flat rather than a −6R hole

**A −0.021 R/trade drag is not a defensible basis for a Tier-3 live gate.** The cell
was *offered*, never shipped, so withdrawing it requires no revert — just this
recorded no-cell disposition. **No walk-forward needed: nothing is being authored.**

### 3b. `slv_trend_1h` — **stays refuted, no cell** ✅ prediction confirmed

Short remains negative after correction (−4.70 @ 23 trending; −5.63 @ 10 chop), but
#7922 already **refuted** it on walk-forward *stability*, which corrected costs do
not change — removing an over-charge makes a short look *better*, so a refutation
can only get more secure. No cell.

### 3c. `spy_pullback_1h` trending short — **blocked on a FAITHFUL re-run, not yet a candidate**

At **−7.96 R / 25 = −0.318 R/trade** this is the largest surviving directional drag
in the roster, and it survives the fee correction with room to spare. But the row is
**`approximate`** — the harness omits its declared `skip_hours` lever — so per the
rec #5 no-cosmetic-cell rule it **cannot source a cell**: a losing cell on an
approximate row may be an artifact of the missing lever.

Note the irony worth recording: `spy_pullback_1h` is the strategy whose net-R
sign-flip *started* the whole fee investigation (#7930). Its **total** is +7.31R —
so this is a **directional** drag inside a profitable strategy, not a bad strategy.

**Next step is a faithful re-run (model `skip_hours`), then walk-forward. Not a draft
PR.**

### 3d. `gld_pullback_1h` — **NOT MEASURED. The live cell's evidence is still un-re-checked.**

The one **live, shipped Tier-3 cell** the fee fix most called into question was
**not in this run at all** — authoring the cell in #7923 paid the strategy down *out*
of `coverage_debt`, and the matrix iterates that roster
(`BL-20260730-REGIME-CELL-UNAUDITABLE`). The run reported *34 rows, 0 errored, 0
skipped*; the omission was invisible.

Its over-charged evidence was pooled short **−15.68 R @ 36 = −0.436 R/trade**.
Subtracting the band (0.04–0.12) leaves ~**−0.32 to −0.40 R/trade** — so it very
likely **survives**, at 10–27% reduced magnitude, with its `+32.98R` long side
*understated*. **That is an estimate, not a measurement**, and it must not be treated
as a re-check.

**Blocked on:** PR #7958 (`resolve_strategy`, which makes an already-celled strategy
measurable again) reaching `main`. Then a targeted `--only gld_pullback_1h` matrix
run + walk-forward. If it no longer clears the gate → a **Tier-3 DRAFT** revert PR,
operator-gated, never self-merged.

### 3e. `splg_trend_long_1d` — a vacuity instance inside a successful run

The row returned **all zeros** (no trades in any regime) with **no error**, in a run
reporting `0 errored, 0 skipped`. Same class as
`BL-20260730-PRODUCER-VACUITY-GUARD`: a row that measured nothing, presented as a
measured row. Either the strategy legitimately produced no signals in 730 days —
which is itself worth knowing about a *live* strategy — or its feed/params resolve to
nothing. **Filed; not diagnosed here.**

## 4. The asset-class question, deliberately not answered

The fourth recorded prediction was that *any marginal-negative equity/ETF leg may be
positive* once costs are right — an asset-class-level distortion, since 12 of the 32
debt strategies are equity/ETF. The corrected run is **consistent** with it: every
measurable equity/ETF delta improved by ~0.04–0.07 R/trade.

But **§1 forbids the strong claim**: without the fixed-window A/B, "improved by
roughly the phantom fee" is inference. And the legs that would actually flip sign are
the *small-n* ones, which are exactly where window drift dominates. **The
asset-class re-read should wait for the controlled A/B.**

Also unchanged and still binding: **#7915 walk-forward-refuted the 2h-pullback
long-drag** on regime-of-sample grounds. The corrected matrix again shows that
family's long side as a drag (`avax` −14.08/44, `sol` −12.39/40, `ada` −3.27/36) —
that is **not** grounds to re-propose a refuted cell. A full-sample number looking
bad again is precisely what the walk-forward gate exists to override.

## 5. Follow-ups

| # | Item | Blocker |
|---|---|---|
| 1 | **Fixed-window fee A/B** — one pass, two `--fee-bps-roundtrip` arms — the only clean attribution | none; cheap |
| 2 | Re-measure + walk-forward **`gld_pullback_1h`** | PR #7958 → `main` |
| 3 | Re-run **`spy_pullback_1h`** faithfully (model `skip_hours`), then walk-forward | harness lever support |
| 4 | Diagnose **`splg_trend_long_1d`** all-zeros | none |
| 5 | Asset-class re-read | follow-up 1 |

## 6. What changed in the debt register

**Nothing yet.** `qqq_pullback_1h` gains a recorded **no-cell** disposition (it stays
tracked debt — a no-cell verdict is a *measured* disposition, not a paydown), and
`slv_trend_1h` stays refuted. No `config/regime_policy.yaml` edit is proposed by this
document.
