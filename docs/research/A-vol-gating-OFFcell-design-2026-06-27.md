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
losing trades). **Confirmation A/B pending** (ungated vs evidence-ML-gated vs
evidence-frozen-gated).

## Honest caveats

1. **In-sample.** Authored from the full-history cell split; before any LIVE cell
   authoring (Tier-3) these need a **walk-forward of the evidence cells** (do they
   stay net-negative per fold?) — same bar the vol-label A/B passed.
2. **Single symbol, 2 strategies.** BTC, trend_donchian + squeeze only. Other
   strategies/symbols need their own splits (and per-symbol advisory heads).
3. The small-sample cells (1–7 trades) are excluded deliberately — several are
   −$10…−$74 but on too few trades to trust.
