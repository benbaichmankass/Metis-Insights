# Design-A vol-gating A/B — evidence (2026-06-27)

Ran the 4-arm A/B from `docs/research/A-vol-gating-AB-plan-2026-06-27.md` on the
trainer (full BTC history `data/backtest_BTCUSDT_5m.csv`, roster
`trend_donchian + squeeze_breakout_4h + htf_pullback_trend_2h`, candidate policy
`regime_policy_trend_vol_candidate-2026-06-27.yaml`; v2 resolved from **advisory**
after promotion). Clean single run (per-arm JSON `/tmp/a0..a3.json`).

## Result — the ML vol label beats the frozen-edge label decisively

| Arm | Net PnL | maxDD% | ret/DD | trades | WR |
|---|---|---|---|---|---|
| 0 ungated (no router) | $353 (3.53%) | 8.24% | 0.39 | 561 | 30.3% |
| 1 1-D-only router (live policy) | $353 (3.53%) | 8.24% | 0.39 | 561 | 30.3% |
| 2 **frozen-vol-gated** | **$59** (0.59%) | **10.1%** | **0.05** | 537 | 29.1% |
| 3 **ML-vol-gated** | **$424** (4.24%) | **8.07%** | **0.47** | 532 | 29.7% |

Arm 3 carried `ml-vol: stage=advisory head=btc-regime-15m-lgbm-v2 available=True
reason=ok scored=1123 fell_back_to_frozen=0` — every gated-bar decision used v2's
live `predict_proba`, zero fallback over the whole history.

### Reading

- **A's gate criterion — MET, strongly.** "Enable Phase-2/3 only if the ML-gated
  book ≥ the frozen-gated book on net AND not worse on maxDD%." Arm 3 vs Arm 2:
  **net $424 vs $59** (+$365) and **maxDD 8.07% vs 10.1%** (better). Same OFF-cells,
  same data — the *only* difference is which vol label drives the cells. The ML
  head is a materially better vol classifier than the frozen-edge detector, and
  it flips a *harmful* gate into a *beneficial* one.
- **Vol-gating with the ML label also beats no gating:** Arm 3 vs Arm 0/1 — net
  +$71, maxDD −0.17pp, ret/DD 0.39 → 0.47.
- **The frozen-edge label is actively bad here:** Arm 2 vs Arm 0 — net $353 → $59,
  maxDD 8.24% → 10.1%. Consistent with the RG4 finding that the frozen detector
  mislabels live vol; this is the same weakness measured end-to-end on PnL.
- **Arm 1 == Arm 0** because the live 1-D `regime_policy.yaml` cells gate none of
  this trend roster (the OFF cells are for fade/fvg/vwap + some shorts) — so the
  1-D router is a no-op here and the vol axis is the whole story.

## Walk-forward (out-of-sample per fold) — CONFIRMS the result

`scripts/ml/walkforward_vol_gating.sh` ran frozen-vs-ML across 4 consecutive,
non-overlapping BTC year-folds (trainer-vm-diag #4821/#4823):

| Fold | frozen (net / maxDD% / retDD) | ML (net / maxDD% / retDD) | ML−frozen net |
|---|---|---|---|
| 2022-07→2023-07 | $302 / 4.35 / 0.67 | $345 / 4.84 / 0.68 | **+$43** |
| 2023-07→2024-07 | $247 / 5.01 / 0.47 | $283 / 4.62 / 0.58 | **+$36** |
| 2024-07→2025-07 | −$365 / 6.41 / −0.55 | −$212 / 5.68 / −0.36 | **+$153** |
| 2025-07→2026-06 | −$198 / 4.45 / −0.43 | −$193 / 4.40 / −0.43 | **+$5** |

**ML beats frozen on net in ALL 4 folds**, and on maxDD% in 3 of 4 (only fold 1 is
marginally worse on DD while still better on net). The label-quality advantage is
**consistent out-of-sample**, not one window's luck — this is the FLIP_POLICY-style
acceptance bar, essentially met. Note folds 3–4 are net-negative for BOTH arms: the
candidate OFF-cells are not themselves a money-maker in 2024–26; the robust finding is
purely **ML vol label > frozen-edge label given the cells** (even in losing periods, ML
gates less badly).

## Honest caveats (do NOT over-read into a live flip)

1. **Single symbol; walk-forward DONE, multi-symbol still pending.** The BTC
   walk-forward above confirms the result out-of-sample per fold (ML > frozen every
   fold). What remains before a Phase-2/3 live flip is a **multi-symbol** check —
   which needs per-symbol advisory heads (only v2/BTC is at advisory today). The
   directional finding (ML label > frozen label) is now robust on BTC; extending it
   to other symbols requires promoting their heads first.
2. **The OFF-cells are a hypothesis.** The result shows ML-vol > frozen-vol *given
   these cells*; the magnitudes depend on the cells. The label-quality conclusion
   (ML head > frozen detector) is what's robust; the specific live cells still need
   their own authoring + evidence (a vol-split of the regime-roster matrix).
3. **v2 only.** A BTC 15m head; a multi-symbol vol-router needs per-symbol advisory
   heads.

## Implication for the rollout

The evidence **supports advancing A's wiring** — the ML vol verdict is the right
signal source, and the live Phase-1 `regime_ml_vol_shadow` agreement log (now
accruing on the live VM after the 113b8522 deploy + v2→advisory) gives the live
cross-check. **Next steps before any order influence (each Tier-3, operator-gated):**
(1) a purged walk-forward + multi-symbol re-run of this A/B; (2) author the live
`config/regime_policy.yaml` `trend_vol` OFF-cells; (3) flip `REGIME_ML_VERDICT_MODE=use`
then `REGIME_ROUTER_ENABLED` — gated on (1)+(2) + sane Phase-1 agreement.
