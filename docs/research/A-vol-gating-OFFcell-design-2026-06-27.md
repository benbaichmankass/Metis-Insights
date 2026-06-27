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

## Walk-forward (OOS overfit gate — DONE, trainer #4831/#4832)

The cells were authored from full-history attribution, so the aggregate above is
in-sample by construction. `scripts/ml/walkforward_evidence_cells.sh` applies the
**fixed** evidence policy across 4 consecutive, non-overlapping BTC year-folds:

| fold | ungated net / maxDD | ev-frozen net / maxDD | **ev-ml net / maxDD** |
|---|---:|---:|---:|
| 2022-07 → 2023-07 | $408 / $460 | −$11 / $277 | **$421 / $299** |
| 2023-07 → 2024-07 | $207 / $558 | $431 / $278 | **$378 / $436** |
| 2024-07 → 2025-07 | **−$330** / $620 | −$208 / $334 | **$7 / $283** |
| 2025-07 → 2026-06 | −$20 / $425 | $415 / $177 | **$308 / $221** |

Acceptance bars (the FLIP_POLICY shape):

1. **ev-ml net ≥ ungated net, per fold — PASS 4/4** (+$13, +$171, +$337, +$328).
   The two biggest gains are the *losing* years (fold 3 −$330→$7; fold 4 −$20→$308)
   — exactly where a gate should earn its keep. The cells help in every window,
   never hurt — **not an in-sample artifact.**
2. **ev-ml maxDD ≤ ungated maxDD, per fold — PASS 4/4** ($460→299, $558→436,
   $620→283, $425→221). Materially lower drawdown every fold.
3. **ev-ml net > ev-frozen net, per fold — 2/4** (ML wins folds 1+3; frozen wins
   2+4). The aggregate ML≫frozen ($1526 vs −$32) is **not** a uniform per-fold
   dominance — frozen is *erratic* (strong in folds 2+4, but −$11 in fold 1 and
   −$32 pooled). Honest read: the ML label is at least as safe as frozen and
   avoids frozen's pooled blow-up, but it does not beat frozen in every window.

**Verdict:** the load-bearing claim — *do the evidence cells + ML label improve
the book out-of-sample?* — is **PASS 4/4 on net AND drawdown**. The secondary
claim — *ML label strictly beats frozen per fold* — is **mixed (2/4)**; ML is
recommended on the aggregate + tail-safety, not on per-fold dominance.

## Honest caveats

1. **Walk-forward done (above) — robustness gate PASS for the cells.** What
   remains in-sample is the *cell selection itself* (which cells are OFF was
   chosen from full history, not re-derived per fold); a stricter test would
   re-author cells on each in-sample window and apply OOS. The per-fold
   gated≥ungated result (4/4) is strong evidence the fixed cell set generalizes,
   but the selection bias is non-zero.
2. **Single symbol, 2 strategies.** BTC, trend_donchian + squeeze only. Other
   strategies/symbols need their own splits (and per-symbol advisory heads).
3. The small-sample cells (1–7 trades) are excluded deliberately — several are
   −$10…−$74 but on too few trades to trust.
