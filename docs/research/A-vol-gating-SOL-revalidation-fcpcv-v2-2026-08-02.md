# Design-A — SOL `trend_vol` OFF-cell RE-VALIDATION under the new advisory head (2026-08-02)

Re-runs the SOL vol-gate cell-selection walk-forward under the **current SOL
advisory head** `sol-regime-15m-lgbm-fc-pcv-v2` (promoted shadow→advisory
2026-08-02T04:10Z, operator-approved; confirmed `target_deployment_stage=advisory`
via trainer-diag #8322). Supersedes the 2026-07-06 SOL study
(`A-vol-gating-ETH-SOL-OFFcell-evidence-2026-07-06.md`), which was a **clean
FAIL under the older plain head `sol-regime-15m-lgbm-v1`** and explicitly said
*"Re-visit when … a retrained head changes the picture."* The retrained head has
now changed the picture — but not enough to clear the go-live bar.

**Bottom line: NO cell is authored. The strict operator-set bar is still not met
(net PASS 2/3, not 3/3). This is a marginal Tier-3 judgment call for the operator,
materially better than 07-06 but not a clean pass — presented, not enacted.**

## What changed since 07-06

- The SOL advisory head is now `sol-regime-15m-lgbm-fc-pcv-v2` (fc-pcv family, six
  frozen chronos-bolt-tiny quantile-forecast `fc_*` features on the nightly-fresh
  v002 dataset), not the 07-06 study's plain `sol-regime-15m-lgbm-v1`.
- The head **scores cleanly offline** in the vol-gate harness: a 3-month probe
  returned `scored=47 fell_back_to_frozen=0` (trainer-diag #8317), so the offline
  cell-selection study is feasible under it (the `fc_*` features are computed by
  the offline pure fns; not a no-op replay).

## Method (identical to the 07-06 / BTC study — the strict test)

`scripts/ml/walkforward_cell_selection.py` — expanding-window, cells RE-DERIVED
per fold from only the prior in-sample window (≥10t net-negative), applied OOS:

```
python scripts/ml/walkforward_cell_selection.py data/SOLUSDT_5m.csv \
    --symbol SOLUSDT \
    --roster trend_donchian_sol,trend_donchian_sol_4h,sol_pullback_2h \
    --model-id sol-regime-15m-lgbm-fc-pcv-v2 --clock-tf 15m
```

Run: trainer-diag #8320 (launch) / #8325 (full log), `WF_EXIT_RC=0`.
Acceptance bar (operator-set, the BTC shape): **ev-ml net ≥ ungated net AND
ev-ml maxDD ≤ ungated maxDD in EVERY fold.**

## Result — cell-selection walk-forward

| OOS fold | cells authored in-sample | ungated net / maxDD | ev-ml net / maxDD | net | DD |
|---|---|---:|---:|:-:|:-:|
| 2023-07 → 2024-07 | 3 | $478 / $346 | **$577 / $238** | ✔ | ✔ |
| 2024-07 → 2025-07 | 2 | $456 / $407 | **$780 / $283** | ✔ | ✔ |
| 2025-07 → 2026-06 | 2 | $8 / $459 | **−$82 / $353** | ✘ (−$90) | ✔ |

**SOL fc-pcv-v2 cell-selection: net PASS 2/3, maxDD PASS 3/3.**

Cells re-derived (the selection is now STABLE — two cells re-discovered in every
fold, vs the 07-06 "small and unstable 1/3/4 cells"):

- `sol_pullback_2h | trending | calm | long` — all 3 folds (in-sample −$100/27t,
  −$140/50t, −$243/63t)
- `trend_donchian_sol | transitional | calm | long` — all 3 folds (in-sample
  −$47/16t, −$160/27t, −$138/38t)
- `sol_pullback_2h | chop | calm | long` — fold-1 only (not stable)

## Verdict — improved, but STILL does not clear the strict bar

| | net WF | maxDD WF | cell stability | gate |
|---|---|---|---|---|
| **BTC** (ref, live since 06-28) | 3/3 | 3/3 | stable | **PASS** |
| **SOL under v1** (2026-07-06) | 1/3 | 1/3 | unstable (1/3/4 cells) | **FAIL (clean)** |
| **SOL under fc-pcv-v2** (this run) | **2/3** | **3/3** | **stable (2 cells)** | **near-miss — fold-3 net** |

The fc-pcv-v2 head is a genuine improvement: drawdown improves in **every** fold
now (not 1/4), and the selection re-discovers the **same two** load-bearing cells
each fold. The single miss is the last fold (2025-07→2026-06): ev-ml −$82 vs a
near-flat ungated +$8 — a ~$90 net giveback while still cutting maxDD $459→$353.

Under the operator-set BTC bar (ev-ml ≥ ungated net **in every fold**) this does
**not** pass — the same "gives back net in a flat/good year" shape that failed SOL
in 07-06, now confined to one fold instead of two.

### Two reasons NOT to author the cell autonomously

1. **The strict bar is not met.** Authoring the cell as a "passing" proposal would
   misrepresent a 2/3 result as a clean pass. It is a marginal judgment call, and
   authoring a `trend_vol` OFF cell is **Tier-3** (live BTC-style order routing —
   SOL now resolves the ML vol label per-symbol off the advisory head, so these
   cells WOULD enforce live under `REGIME_ML_VERDICT_MODE=use`).
2. **Fidelity caveat — not decision-grade at this margin.** The offline replay's
   `P(volatile)` differs from the served value (~88% label agreement, median
   |ΔP| 0.043, max 0.412 — `BL-20260730-OFFLINE-VS-SERVE-PVOLATILE-GAP`). A fold
   that fails by $90 in a near-flat year can flip on a handful of near-threshold
   bars, so the fold-3 miss is **not** decision-grade on its own.

## The exact cells, if the operator elects to author them (Tier-3, NOT merged here)

Were the operator to accept the 2/3 result (drawdown-positive in every fold, two
stable cells, one flat-year net giveback), the exact `config/regime_policy.yaml`
`trend_vol` addition would be:

```yaml
trend_vol:
  trending:
    calm:
      sol_pullback_2h:     { long: off }   # WF: OFF all 3 folds; in-sample −$100/−$140/−$243
  transitional:
    calm:
      trend_donchian_sol:  { long: off }   # WF: OFF all 3 folds; in-sample −$47/−$160/−$138
```

Prerequisites for these to be more than a live no-op (all currently TRUE):
`REGIME_ML_VERDICT_MODE=use` (live for BTC), the regime hard gate active
(baseline-on, kill-switch `REGIME_ROUTER_DISABLED`), and a SOL advisory head
(restored 2026-08-02). `sol_pullback_2h` (2h) and `trend_donchian_sol` (1h) both
resolve the SOL 15m advisory head's per-SYMBOL label (per `intents._decision_vol_regime`).

## Recommendation

**Hold — do not author.** The retrained head moved SOL from a clean fail to a
near-miss, but it does not clear the strict every-fold bar, and the one miss is
inside the offline-vs-serve fidelity band. Re-visit when (a) the fold-3 window
extends with more live data, or (b) the offline-vs-serve `P(volatile)` gap is
closed so a marginal verdict becomes decision-grade. The two candidate cells are
recorded above for a future operator decision. This keeps SOL routing unchanged
(permissive), which is the correct default for an unproven cell.
