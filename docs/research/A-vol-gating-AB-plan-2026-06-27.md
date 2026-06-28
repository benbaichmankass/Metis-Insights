# Design-A vol-gating A/B — run plan (2026-06-27)

The harness + the option-2 lever are validated (`docs/research/A-regime-router-ml-vol-verdict-DESIGN-2026-06-27.md`
§ "Evidence BEFORE promotion"). This is the plan for the **full gated A/B** that
decides whether the ML head's vol verdict beats the frozen-edge detector — the
evidence that gates the Tier-3 live OFF-cell authoring + `REGIME_ML_VERDICT_MODE=use`.

## Candidate policy (the OFF-cells under test)

`docs/research/regime_policy_trend_vol_candidate-2026-06-27.yaml` — the live 1-D
blocks copied verbatim + a populated `trend_vol` block of CANDIDATE 2-D OFF-cells
(a hypothesis, not a truth). `_hard_regime_gate` drops an intent on
`trend_gated OR vol_gated`, and the harness stamps each intent's `vol_regime` with
the frozen **or** ML label per `--vol-verdict`, so the cells produce a measurable
book difference wherever the ML and frozen labels disagree inside an OFF-cell.

## Arms (run on the trainer, full BTC history, `data/backtest_BTCUSDT_5m.csv`)

Roster: `trend_donchian,squeeze_breakout_4h,htf_pullback_trend_2h` (the strategies
the candidate cells touch). `CAND=docs/research/regime_policy_trend_vol_candidate-2026-06-27.yaml`.

| Arm | Flags | Measures |
|---|---|---|
| **0 ungated** | (no `--regime-router`) | baseline book |
| **1 1-D-only router** | `--regime-router on --regime-policy config/regime_policy.yaml` | trend-axis gating alone (trend_vol empty) |
| **2 frozen-vol-gated** | `--regime-router on --regime-policy $CAND --vol-verdict frozen` | vol-gating with the frozen-edge label |
| **3 ML-vol-gated** | `--regime-router on --regime-policy $CAND --vol-verdict ml --ml-stage shadow --ml-model-id btc-regime-15m-lgbm-v2` | vol-gating with v2's `predict_proba` label |

```bash
# on the trainer (PYTHONPATH=. ML_REGISTRY_ROOT=ml/registry-store), per arm:
python scripts/backtest_system.py --data data/backtest_BTCUSDT_5m.csv \
  --roster trend_donchian,squeeze_breakout_4h,htf_pullback_trend_2h \
  <arm flags> --json /tmp/arm_<n>.json
```

## Read-out + gate criterion

Compare **net PnL**, **maxDD%**, ret/DD, win rate, and per-(strategy,regime) fills:

- **Does vol-gating help at all?** Arm 2/3 vs Arm 1 — vol-gating should not be
  net-worse and should reduce maxDD% (it only ever *removes* trades in bad
  vol sub-regimes).
- **Does the ML label beat the frozen label? (the A verdict)** Arm 3 vs Arm 2 —
  **enable Phase-2/3 live only if the ML-gated book ≥ the frozen-gated book on
  net AND not worse on maxDD%** (mirrors the `FLIP_POLICY` walk-forward
  acceptance). `evidence.ml_vol_scored_bars` must be > 0 in Arm 3 (else the ML
  arm silently degraded to frozen — the four bugs fixed 2026-06-27 were exactly
  this failure mode; check it every run).

## Honest caveats

1. **The OFF-cells are a hypothesis.** A negative A/B may mean the *cells* are
   wrong, not that ML-vol-gating is useless — re-author the cells (a vol-split of
   the regime-roster matrix would target them properly) and re-run before
   concluding. Authoring the LIVE cells is a separate Tier-3 step gated on a
   positive A/B.
2. **v2 must be resolvable at the requested stage.** Arm 3 pins v2 via
   `--ml-stage shadow --ml-model-id`; if v2 is later promoted to advisory, drop
   `--ml-stage shadow` to score it from advisory (matching live).
3. **Single symbol (BTCUSDT).** v2 is a BTC 15m head; a multi-symbol verdict
   needs per-symbol heads + per-symbol cells.
4. Backtest-local policy only — `$CAND` must NEVER be pointed at by the live
   trader (`REGIME_POLICY_PATH` on the VM stays unset / on the live YAML).
