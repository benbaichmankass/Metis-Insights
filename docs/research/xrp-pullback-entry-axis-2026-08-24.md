# xrp_pullback_2h — the ENTRY axis, and what it says about the family's premise

**Date:** 2026-08-24 · **Tier:** 1 (research only — `config/strategies.yaml` NOT touched)
**Question:** the third and last untested axis on this leg. Geometry (37 cells) and
trail (20 cells) both came back **zero-positive with the live config the interior
optimum**, so TUNE-BEFORE-DEMOTE was unsatisfied and no disposition could honestly
be proposed. This closes that.

---

## 0. The headline, and the caveat that guts half of it

**The entry axis is the one that moves.** Unlike the other two, the live cell is
*not* the interior optimum — it is in the bottom half of the surface.

The single change that survives out of sample is **`pullback_frac` 0.5 → higher**,
and pushed to its limit the finding is uncomfortable: **`pullback_frac = 1.0`
removes the pullback requirement entirely, and that is the best-performing,
split-stable configuration on this leg** (−13.02R → **+32.87R**, 5/5 splits).

⚠️ **THE 5/5 SPLIT RESULT AND THE "GAIN IS RECENT" OBSERVATION ARE THE SAME FACT,
NOT TWO.** Every OOS window in a split test *is* the recent tail, and the per-fold
breakdown (§ 4) shows the gain concentrated in 2025–2026. Counting both as
independent confirmation would be double-counting the only evidence there is.

⚠️ **AND IT DOES NOT REPLICATE OUT OF SAMPLE ON THE FAMILY.** The *direction*
replicates on all five legs; the *generalization* replicates on one (§ 5).

---

## 1. Baseline reproduces exactly — and how I nearly mis-stated it

    trades=296  net_r=-13.0236  maxdd_r=33.4184  MAR=-0.390

My first attempt returned `trades=224 net_r=-18.4795` because I omitted
`--tp-cap-pct 0.099`. That is worth recording rather than quietly fixing, because
the omission *is* § 6.5's finding showing up in the baseline: with the venue clamp
**off** the same cell is **+18.52R**; with it **on**, −13.02R, and **96 of 296
exits become take-profits**. On this leg the 9.9% clamp is not a guardrail — it is
the operative exit.

## 2. The surface: 100 cells attempted, 97 with a book

`trend_lookback` × `pullback_lookback` × `pullback_frac` = 5 × 4 × 5, everything
else pinned at live (stop 2.5 / trail 6.0 / tp_r 3.0 / tp-cap 0.099 / arm 4.49).

**3 cells produced `trades=0`** — `pullback_lookback=20` with `trend_lookback=20`,
where the pullback window equals the trend window. Not a harness failure and not a
loss; they are simply not a book. **Denominator: 97.**

**59 of 97 beat live · 23 of 97 positive.**

Marginals, which is where the direction lives:

| knob | live | live's rank | best |
|---|---|---|---|
| `trend_lookback` | **40** | **best** (median −4.79) | 40 — live is right |
| `pullback_lookback` | **10** | **worst** (median −17.86) | 20 (median +0.03) |
| `pullback_frac` | **0.5** | **worst** (median −13.02) | 0.7 (median +0.34, 10/20 positive vs 3) |

## 3. The argmax fails; the third-best generalizes

Split dispersion via the canonical `m20_split_dispersion.py` (imports the live gate;
`harness_agreement.ok = true` on all three base metrics, so it graded rather than
refusing), targets 35/40/45/50/60:

| arm | change | full-history net_R | IS Δ | OOS Δ | splits |
|---|---|---|---|---|---|
| **B** | `pullback_frac` → 0.7 only | +4.07 (3rd) | +7.27 | **+9.83** | **5/5** |
| A | `pullback_lookback` → 20 only | +1.74 | +16.20 | −1.44 | 0/5 |
| C | both | +9.10 | +26.25 | −4.12 | 0/5 |
| D | pl 20 + pf 0.4 — **best full-history, best MAR (1.52)** | **+14.25** | +27.73 | −0.46 | **0/5** |

**The best cell on full history fails at every split.** This is the
`never-on-the-argmax` rule producing a live counter-example rather than a caution.

⚠️ **`split_sensitive: false` here means stably PASSING** — the mirror of the
donchian case where the same boolean meant stably FAILING. Read `pass_fraction`;
the boolean cannot be filtered on in either direction.

## 4. Not a knife edge — a plateau, and a monotone approach to it

| pf | 0.55 | 0.60 | 0.65 | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 | 0.95 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|---|
| net_R | −15.41 | −6.84 | −3.88 | +4.07 | +13.57 | +13.20 | +20.94 | +28.91 | **+35.31** | +32.87 |
| splits | 0/5 | 0/5 | 0/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| trades | 306 | 318 | 342 | 357 | 380 | 403 | 423 | 446 | 456 | 456 |

Monotone improvement with a stable plateau — much stronger than one winning cell.
**Trade count saturates at 0.95 → 1.00 (456 both)**, which is the arithmetic
signature of the filter becoming vacuous: harness line 370 is
`pos <= pullback_frac`, so at 1.0 the test is always true and the entry reduces to
*uptrend + ADX ≥ 25 + confirmation bar*. Verified in the source, not inferred.

**Per-calendar-year folds, base vs pf=1.0:**

| year | 2021 | 2022 | 2023 | 2024 | 2025 | 2026* |
|---|---|---|---|---|---|---|
| Δ net_R | −1.77 | +10.85 | **−8.19** | +8.37 | +17.37 | +19.27 |

**4 of 6 folds better**, total +45.90R, gain concentrated in the last two.
*2026 is partial (data ends 2026-08-23).*

## 5. Family replication — direction yes, generalization no

Each leg at its OWN live params (read from config, never assumed), live `pf` vs 1.0:

| leg | live net_R | pf=1.0 net_R | Δ | splits |
|---|---|---|---|---|
| **xrp_pullback_2h** | −13.02 | +32.87 | +45.89 | **5/5** |
| eth_pullback_2h | −26.99 | +41.41 | +68.40 | 0/5 |
| sol_pullback_2h | +18.09 | +36.92 | +18.83 | 0/5 |
| ada_pullback_2h | −12.74 | +3.29 | +16.02 | 0/5 |
| avax_pullback_2h | −1.20 | +27.76 | +28.96 | 0/5 |

**5/5 on direction. 1/5 on generalization.** The four failures have the same shape
as the donchian Path B cells: a large in-sample gain that does not survive the
split. This is evidence about the family's *premise*, and evidence for a change on
exactly one leg.

⚠️ **Multiple comparisons.** XRP is the one leg of five that passes, after ~120
cells were measured. One survivor out of five siblings is what a lucky draw also
looks like. The plateau (§ 4) and the mechanism (§ 6) are what argue it is not —
not the 5/5 by itself.

## 6. What the mechanism would have to be

`pullback_frac` gates *how deep a retrace is required before entering a trend*. A
strict value (0.5 = lower half of the recent range) means the leg is filled only
when price has already given back half its range — disproportionately when the
trend is failing rather than continuing. Relaxing it admits continuations that
never retrace that far.

That is coherent, and it is also **the strategy's own defining premise**. A leg
named `htf_pullback_trend_2h` measuring that its pullback filter is anti-predictive
is a claim that deserves more scepticism than a favourable number usually gets.

## 7. What this does NOT claim

- **Not** that any config should change. This is Tier-3 and it is a proposal at
  most; nothing here was applied.
- **Not** that the family should drop the filter — 4 of 5 legs fail out of sample.
- **Not** that `pf = 0.95` is the value. It is the argmax of the plateau, and § 3
  is a live demonstration of what selecting an argmax costs.
- **Not** independent of the recency caveat in § 0.

## 8. Disposition

**TUNE-BEFORE-DEMOTE is now satisfied for `xrp_pullback_2h`** — all three axes
measured (geometry 37 cells, trail 20 cells, entry ~120 cells). The entry axis
found a split-stable improvement, so **no demotion is proposed**; the leg is not a
dead loss, it is mis-parameterised on one knob.

**Tier-3, proposed with evidence, NOT applied:** `xrp_pullback_2h.pullback_frac`
0.5 → a value in the plateau interior. Deciding *which* value, and whether one leg
clearing a gate that four siblings fail is a real edge or a lucky draw, is the
operator's call and wants a criterion fixed **before** the next measurement — the
§ 6.0b lesson from the donchian work applied forward rather than re-learned.

## Reproduce

```bash
# baseline — note --tp-cap-pct, without which the leg reads +18.5R instead of -13.0R
python3 scripts/backtest_pullback.py --data data/XRPUSDT_1h.csv --symbol XRPUSDT \
  --resample 2h --trend-lookback 40 --pullback-lookback 10 --pullback-frac 0.5 \
  --atr-period 14 --atr-stop-mult 2.5 --trail-mult 6.0 --tp-r 3.0 --tp-cap-pct 0.099 \
  --trail-decay-arm-r 4.49 --trail-decay-tight-mult 2.5 --min-confidence 0.0 \
  --adx-min 25 --adx-period 14 --strategy-name xrp_pullback_2h \
  --emit-trades /tmp/xrp/base.jsonl
# then vary --pullback-frac, and grade each arm:
python3 scripts/research/m20_split_dispersion.py --base /tmp/xrp/base.jsonl \
  --cell /tmp/xrp/<arm>.jsonl --targets 35,40,45,50,60 \
  --base-reported '{"net_total_r":-13.0236,"max_drawdown_r":33.4184,"total_trades":296}'
```
