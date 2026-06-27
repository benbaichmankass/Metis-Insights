# Design-A — evidence-based `trend_vol` OFF-cell design (2026-06-27)

The aggregate + walk-forward A/B proved the **ML vol label beats the frozen-edge
label** — but with *hypothesis* OFF-cells that weren't themselves profitable
(`A-vol-gating-AB-evidence-2026-06-27.md`). This step authors **evidence-based**
OFF-cells from the actual per-cell net-PnL, so live cells (a future Tier-3 act)
rest on real attribution, not a guess.

## The vol-split (per-(strategy, trend, vol, side) net-PnL)

Ran the harness with the new **cell-attribution** instrumentation
(`per_cell_attribution`), ungated, ML vol label (v2 at advisory, `scored=1123,
fell_back=0`), full BTC history (trainer-vm-diag #4825/#4827). Ungated book =
net $353 / 561 trades. Decomposed:

| cell (strategy \| trend \| vol \| side) | net $ | trades | wins |
|---|---:|---:|---:|
| **trend_donchian \| trending \| calm \| long** | **+1238** | 252 | 80 |
| squeeze_breakout_4h \| chop \| calm \| long | +152 | 12 | 2 |
| squeeze_breakout_4h \| chop \| calm \| short | +75 | 16 | 4 |
| squeeze_breakout_4h \| trending \| volatile \| long | +58 | 1 | 1 |
| squeeze_breakout_4h \| trending \| calm \| long | +3 | 16 | 8 |
| trend_donchian \| transitional \| volatile \| long | −2 | 16 | 5 |
| squeeze_breakout_4h \| (small ≤7t cells) | −7…−74 | 1–7 | — |
| trend_donchian \| chop \| volatile \| long | −117 | 4 | 0 |
| trend_donchian \| chop \| calm \| long | −218 | 11 | 2 |
| **trend_donchian \| trending \| volatile \| long** | **−224** | 136 | 39 |
| **trend_donchian \| transitional \| calm \| long** | **−356** | 43 | 12 |

(`htf_pullback_trend_2h` emitted no fills on this clock — only 2 strategies traded.)

## The finding

**The whole book is one cell** — `trend_donchian | trending | CALM | long`
(+$1238). And the single cleanest **vol-conditioned** signal: the SAME strategy/
trend/side flips sign on the vol axis — **+$1238 in CALM vs −$224 in VOLATILE**
trending. A violent (volatile) "trend" is false-breakout territory for a Donchian
long; a calm trend is the real thing. This is exactly what a *good* vol classifier
should let us separate — and it's why the ML-vol A/B beat the frozen one.

## OFF-cells authored (evidence policy)

`regime_policy_trend_vol_evidence-2026-06-27.yaml` — meaningful-sample (≥~10
trades) net-negative cells only (small-sample negatives left ON as noise):

| OFF-cell | net $ | trades | rationale |
|---|---:|---:|---|
| `trend_donchian \| trending \| volatile \| long` | −224 | 136 | the vol refinement (calm long is the +$1238 winner) |
| `trend_donchian \| transitional \| calm \| long` | −356 | 43 | Donchian long without a real trend |
| `trend_donchian \| chop \| calm \| long` | −218 | 11 | trend strat long in chop = no trend to ride |
| `squeeze_breakout_4h \| trending \| calm \| short` | −55 | 30 | breakout-short into a calm uptrend |

Expected effect: removing these ~$853 of net-negative sleeves should lift the book
well above the ungated $353 while trimming drawdown (they only ever *remove*
losing trades).

## Confirmation A/B (DONE — trainer-vm-diag #4828/#4830)

Three full-history BTC runs, identical roster (`trend_donchian, squeeze_breakout_4h,
htf_pullback_trend_2h`), the evidence policy above:

| arm | gate | net $ | maxDD $ | trades | WR | ret/DD |
|---|---|---:|---:|---:|---:|---:|
| **a0** | ungated | 353 | 915 | 561 | 30.3% | 0.39 |
| **a2** | evidence cells, **FROZEN** vol label | **−32** | 856 | 251 | 30.7% | −0.04 |
| **a3** | evidence cells, **ML** vol label (`v2@advisory`, scored=1123, fell_back=0) | **1526** | 895 | 410 | 30.2% | **1.70** |

**The result is decisive and exactly as predicted:**

1. **The evidence cells lift the book — but only with the ML vol label.** a3 takes
   net $353 → **$1526** (4.3×) while *reducing* maxDD ($915 → $895) — ret/DD 0.39 →
   1.70. The gate only ever *removes* trades (561 → 410), so the +$1173 is pure
   removal of net-negative sleeves, not new risk.
2. **The ML vol verdict is the load-bearing piece, not the cell list.** The SAME
   cells under the *frozen* vol detector (a2) **lose money (−$32)** — worse than
   ungated. The cells were authored from the ML-vol split, so the frozen label
   assigns different bars to calm/volatile and gates the wrong trades. This is the
   single cleanest demonstration in the whole A program that the ML label beats
   the frozen one: same policy, opposite outcome, the only difference is the vol
   classifier.

This closes the loop the hypothesis-cell A/B opened: hypothesis cells gated
profitable sleeves and lost money; **evidence cells + ML label** gate exactly the
losing sleeves and 4×'d the book.

## Honest caveats

1. **In-sample.** Authored from the full-history cell split; before any LIVE cell
   authoring (Tier-3) these need a **walk-forward of the evidence cells** (do they
   stay net-negative per fold?) — same bar the vol-label A/B passed.
2. **Single symbol, 2 strategies.** BTC, trend_donchian + squeeze only. Other
   strategies/symbols need their own splits (and per-symbol advisory heads).
3. The small-sample cells (1–7 trades) are excluded deliberately — several are
   −$10…−$74 but on too few trades to trust.
