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

---

# Addendum — the authored-cell re-audit (2026-07-30, operator-directed)

**Runs:** [#7962](https://github.com/benbaichmankass/Metis-Insights/issues/7962) (`gld_pullback_1h`) ·
[#7963](https://github.com/benbaichmankass/Metis-Insights/issues/7963) (`trend_donchian`, `htf_pullback_trend_2h`, `squeeze_breakout_4h`),
both on `72712c9` — possible only because `resolve_strategy()` made already-celled
strategies measurable again.

## A1. `gld_pullback_1h` — the live cell **SURVIVES**. Estimate confirmed.

`faithful` row, 730d:

| regime | net-R | long-R (n) | short-R (n) |
|---|--:|--:|--:|
| **trending** | +23.31 | **+37.18 (54)** | **−13.88 (37)** |
| transitional | +20.08 | +21.87 (14) | −1.79 (10) |
| chop | +12.59 | +7.34 (4) | +5.25 (4) |
| **total** | **+55.97** | +66.39 | −10.42 |

Against the over-charged authoring evidence (#7920: trending short −15.68 @ 36, long +32.98 @ 54):

| leg | over-charged | corrected | Δ per trade | in predicted 0.04–0.12 band? |
|---|--:|--:|--:|:--|
| trending short | −15.68 @ 36 | **−13.88 @ 37** | **+0.049** | ✅ |
| trending long | +32.98 @ 54 | **+37.18 @ 54** | **+0.078** | ✅ |

**Verdict: `trending.gld_pullback_1h { long: on, short: off }` remains warranted.**
Trending short is **−0.375 R/trade** at n=37 — a large, unambiguous drag. The
prediction recorded in `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE` was
−0.32 to −0.40 R/trade; measured −0.375. **No revert. No Tier-3 PR needed.**

The predicted *understatement* of the long side also holds: **+37.18R / 54 =
+0.688 R/trade**, a stronger engine than the record showed. The cell is doing its
job — gating a real drag while leaving a real edge alone.

Nothing warranted in `transitional` (short −1.79 @ 10, thin) or `chop` (short
**positive** +5.25 @ 4).

## A2. `htf_pullback_trend_2h` — the authored cell looks **INVERTED** on current data

`faithful` row, 730d:

| regime | net-R | long-R (n) | short-R (n) |
|---|--:|--:|--:|
| **trending** | +1.05 | **−6.85 (42)** | **+7.89 (54)** |
| transitional | +8.70 | +7.85 (17) | +0.86 (18) |
| **chop** | **−4.63** | −0.39 (18) | −4.24 (10) |

The live cell is `trending { long: on, short: off }`, authored from *"long +30, short
flat (−0.05)"*. Both legs now read the **opposite sign** at adequate n — so the cell
**permits the negative leg and gates the positive one**. Two sibling cells also
disagree with their comments (`transitional` short now **+0.86** vs "−4"; `chop` long
now ~flat **−0.39** vs "−8"; `chop` short **−4.24**, still negative, still warranted).

**Not proposed as a change.** Walk-forward dispatched (**#7968**). #7915 already
walk-forward-**refuted** a full-sample directional read on this exact strategy family
on regime-of-sample grounds — that precedent is directly on point, so a full-sample
inversion is a *candidate*, never a verdict. Also **not** a fee artifact: BTCUSDT was
always correctly charged. This is **evidence age** — the authoring evidence is the
2026-06-01 matrix, a different window, before the harness modelled several levers.

## A3. Coverage — only **4 of 16** live-affecting cells are actually re-auditable

The honest tally, which matters more than either result above:

| Strategy | live cells | re-auditable? |
|---|--:|---|
| `gld_pullback_1h` | 1 | ✅ faithful — **done** |
| `htf_pullback_trend_2h` | 3 | ✅ faithful — **done**, walk-forward pending |
| `trend_donchian` | 3 × 1-D + 3 × 2-D | ⚠️ **`approximate` only** (omits `exit_head_*` + `trail_decay`) → cannot source or un-source a cell |
| `squeeze_breakout_4h` | 3 × 1-D + 1 × 2-D | ❌ **`errored`: "unclassifiable"** — neither Donchian nor pullback, so no harness maps to it |
| `ict_scalp_5m` | 2 × 2-D | ❌ no 1-D cell; 2-D unreachable (no vol-split) |

Three distinct reasons a live gate can't be re-checked, only one of which was known
before today:

1. **No vol-split** → the six 2-D `trend_vol` cells (`BL-20260730-2D-VOL-CELLS-UNAUDITABLE`).
2. **No harness mapping** → `squeeze_breakout_4h` errors as unclassifiable. **New.**
3. **Approximate-only** → `trend_donchian` can be measured but not *acted on*, because
   an unmodelled lever could explain any losing cell. **New.**

**So: 4 of 16 live-affecting cells re-audited; 12 cannot currently be.** Reporting
this re-audit as "the cells are re-checked" would be the same scope error the whole
exercise exists to prevent.

### A3a. And a cosmetic-cell instance found in passing

`trend_donchian` shows **zero short trades in every regime** (n=0 across 730d — it
runs long-only now), yet carries authored **short** cells (`chop { short: on }`,
`transitional { short: off }`, `trending { short: off }`). Those gate nothing: they
are cosmetic cells, the exact anti-pattern the no-cosmetic-cell rule names. Filed.

## A4. Follow-ups added

| Item | Owner |
|---|---|
| Walk-forward the `htf_pullback_trend_2h` trending inversion | #7968 (running) |
| `squeeze_breakout_4h` has no harness → 4 live cells unauditable | `BL-20260730-SQUEEZE-NO-HARNESS` |
| `trend_donchian` measurable only as `approximate` → 6 cells un-actionable | `BL-20260730-DONCHIAN-APPROX-ONLY` |
| `trend_donchian` carries short cells with zero short trades (cosmetic) | `BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS` |
| `gld_pullback_1h` — **closed**, cell survives | this addendum |

## A5. Walk-forward verdict on the `htf_pullback_trend_2h` inversion (#7968) — it HOLDS

The #7915 OOS-stability gate, 4 contiguous folds, 730d (2024-08-04..2026-07-27),
`faithful` fidelity, 96 regime trades:

| fold | dates | n | long-R (n) | short-R (n) | net-R |
|---|---|--:|--:|--:|--:|
| 1 | 2024-08-04..2025-02-04 | 24 | **+2.35** (10) | −2.21 (14) | +0.13 |
| 2 | 2025-02-26..2025-08-27 | 24 | −3.66 (10) | −2.49 (14) | −6.15 |
| 3 | 2025-09-23..2026-02-28 | 24 | −2.02 (10) | **+11.59** (14) | +9.56 |
| 4 | 2026-03-08..2026-07-27 | 24 | −3.51 (12) | **+1.01** (12) | −2.50 |
| **pooled** | | 96 | **−6.85 (42)** | **+7.89 (54)** | +1.05 |

Gate verdicts:

- **`long_stable_drag: True`** — long negative in **3 of 4** folds (strict majority)
  AND pooled long-R < 0. By the harness's own standard, **a long-side OFF cell is
  justified.** The live cell has long **ON**.
- **`short_stable_drag: False`** — short negative in only 2 of 4 folds (not a strict
  majority) AND pooled short-R > 0. **A short-side OFF cell is NOT justified.** The
  live cell has short **OFF**.

So the inversion is **not** the regime-of-sample artifact #7915 caught. It survives the
very gate that refuted the earlier full-sample read on this same strategy family. Both
halves of the live cell fail their own authoring standard, in opposite directions.

### A5a. The two halves have DIFFERENT evidential strength — do not conflate them

This distinction is load-bearing for the Tier-3 proposal and must not be flattened into
"the cell is backwards":

| Change | Direction | Justification | Strength |
|---|---|---|---|
| `long: on → off` | **restrictive** (less trading) | **Affirmative** — `long_stable_drag: True`, the gate's own positive standard | **Strong.** 3/4 folds, pooled −6.85, n=42. Also doctrinally the safe direction. |
| `short: off → on` | **permissive** (more trading) | **Negative only** — the OFF cell has no support; this is *not* evidence the short leg is profitable | **Weak.** Pooled +7.89 is ~147% attributable to fold 3 (+11.59); ex-fold-3 the short leg is **−3.70**. |

"We cannot justify keeping it OFF" and "we have evidence it makes money" are different
claims. Only the first is established. The Prime Directive does say a leg is gated only
by *explicit* evidence — which is the argument for removing the unjustified OFF cell —
but a permissive change resting on one fold is exactly the shape that should reach the
operator with its fragility stated, not buried.

### A5b. Disposition

**Tier-3, operator-gated, DRAFT.** No cell is authored by a walk-forward run and none is
merged autonomously. The proposal is sequenced *after* the Tier-1 PR #7970 lands so a
Tier-3 config edit is never mixed into a Tier-1 branch (which would strand the
`econ-calendar-backfill` runner behind an operator approval it does not need).

Recommended split when it is filed, because the two halves do not deserve one decision:

1. **`long: on → off`** — take it. Affirmatively justified at the gate's standard.
2. **`short: off → on`** — hold pending its own evidence pass, OR take it with the
   fold-3 concentration recorded in the changelog. My recommendation is **hold**: the
   long fix captures the measured drag, and pairing a strong restrictive change with a
   weak permissive one lets the weak half ride in on the strong half's evidence.

Owning rows: `BL-20260730-AUTHORED-CELL-REAUDIT-REGISTER` · `BL-20260730-FEE-AB-FIXED-WINDOW`.
