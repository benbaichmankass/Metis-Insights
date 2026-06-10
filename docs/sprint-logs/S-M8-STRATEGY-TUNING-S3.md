# Sprint Log: S-M8-STRATEGY-TUNING-S3

## Date Range
- Start: 2026-06-10
- End:   2026-06-10

## Objective
- Primary goal: add **k-fold anchored walk-forward** to the sweep harness (the
  robustness gate above S2's single split), and re-run the
  `trend_donchian.min_confidence` sweep across folds to confirm — or refute —
  the S2 plateau before any Tier-3 floor proposal firms.
- Secondary goals (batched, all Tier-1): **expand the `(harness, param)`
  registry** to trend's structural params, and ship a **dashboard read route**
  `GET /api/bot/strategies/{name}/tune` so tune results surface like M7 packets.

## Tier
- Tier 1 throughout. Harness + registry + the new read route write nothing to
  `config/`. The OOS+k-fold result feeds a **separate, draft Tier-3 PR** (the
  floor change) the operator approves — proposed here, not applied.

## Work Completed
- **k-fold anchored walk-forward** (`scripts/ml/strategy_tune_sweep.py`):
  `KFold(wf_start, wf_end, folds, train_frac)` + `--wf-folds/--wf-start/--wf-end/
  --wf-train-frac`. Single-split (`--oos-start`) and k-fold now share one
  folds-based path (`fold_windows` + `_aggregate_folds`); OOS metrics aggregate
  across folds (net Σ, expectancy μ, drawdown = worst fold), per-fold detail
  under each row's `folds`, and the recommendation gains a **`robust`** flag
  (net-positive in EVERY fold) on top of `train_oos_consistent`.
- **Registry expansion** — `trend_donchian.{trail_mult, atr_stop_mult, donchian,
  atr_period}` → `backtest_trend.py` flags (per-value), so the harness can tune
  trend's structural params, not just `min_confidence`.
- **Read route** — `src/web/api/routers/strategy_tune.py`
  (`GET /api/bot/strategies/{name}/tune`), mirroring `strategy_review.py`:
  returns every tuned param from the newest date under
  `runtime_logs/strategy_tunes/`, `present:false` cleanly when none. Mounted in
  `src/web/api/main.py`.
- Tests: +6 k-fold + 1 registry (`tests/test_strategy_tune_sweep.py`, 50 total) +
  3 route (`tests/test_strategy_tune_route.py`). Doc § k-fold added.

## Validation Performed — the k-fold result
Trainer VM (relay #3179), `data/btc_1h_multiyear.csv` (2021-01-01 → 2026-06-01),
live params, grid `uniform [0.3, 0.9]` (7 pts), **3 anchored folds**,
`train_frac 0.4`, fee 7.5 bps.

| min_conf | OOS trades | OOS net Σ | OOS exp μ | OOS maxDD | folds+ | train net Σ |
|---|---|---|---|---|---|---|
| **0.30 (live)** | 298 | 34.92 | 0.111 | 22.27 | **2/3** | 183.3 |
| 0.40 | 281 | 35.05 | 0.120 | 20.35 | 2/3 | 183.2 |
| 0.50 | 262 | 29.56 | 0.104 | 15.38 | 2/3 | 167.8 |
| **0.60** | 235 | 53.62 | 0.215 | 12.94 | **3/3** | 180.5 |
| 0.70 | 220 | 49.14 | 0.207 | 14.13 | 3/3 | 181.9 |
| 0.80 | 202 | 56.14 | 0.258 | 13.41 | 3/3 | 205.5 |
| 0.90 | 191 | 55.32 | 0.266 | 12.95 | 3/3 | 197.2 |

- **The live `0.30` is net-positive only in 2 of 3 folds** — it loses the recent
  regime (consistent with S2's single-split finding that 2025→ is negative at
  0.30). **3/3 robustness begins at `0.60`**, where OOS expectancy ~doubles
  (0.111 → 0.215) and OOS max drawdown drops ~40% (22.3 → 12.9 R).
- The **0.60–0.90 band is uniformly 3/3-robust**; the mechanical pick is 0.90
  (best OOS expectancy), but the **defensible, minimal-intervention value is
  `0.60`** — the onset of robustness, ~2× the live floor, biggest single jump in
  expectancy/drawdown, and not at the grid edge.
- This supersedes S1 (in-sample) and confirms + hardens S2 (single split): the
  floor raise is now **OOS + 3-fold validated**.

## Risks and Follow-Ups
- **The exact value within 0.60–0.90 is a Tier-3 operator decision.** The draft
  PR proposes `0.60` (conservative) with the full table; the operator may pick
  higher.
- Single symbol / single param. Other trend params (now registry-supported) +
  MES are future sweeps.

## Next Recommended Sprint
- Operator reviews the **draft Tier-3 PR** (trend floor 0.30 → 0.60) + this
  evidence. Then: dashboard Streamlit panel consuming the new `/tune` route
  (preview-branch flow), and registry-driven sweeps of trend's structural params.

## Wrap-Up Check
- [x] Code inspected directly, not inferred from summaries.
- [x] Documentation reviewed + updated.
- [x] No pipeline stage touched (research tooling + read route only).
- [x] Roadmap updated.
- [x] Contradictions recorded (S1 in-sample superseded; S2 confirmed).
- [x] Remaining unknowns stated (exact value = operator; single symbol/param).
