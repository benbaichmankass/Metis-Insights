# Sprint Log: S-MLOPT-S12 (cross-asset/macro features for MES + account_context wiring)

## Date Range
- Start: 2026-06-04
- End: 2026-06-04

## Objective
M14 Phase 2.4 — the macro-conditioning feature lever, targeted at the **weakest
leg**. MES is a macro-driven index instrument, yet its regime heads see only
same-OHLC vol + log-return features; the S9 range-vol A/B was **mixed on MES**
(positive on BTC). The roadmap's hypothesis: a *different kind* of input — the
volatility complex (VIX + term structure), the dollar (DXY), and the rates curve
— has the best shot at the MES `f1_volatile` separation that vol estimators on
the same bars could not deliver (gap G5).

Two deliverables in the roadmap's Session 2.4:
- **Part A — cross-asset/macro features for MES.** DXY / VIX-term-structure /
  rates conditioning, joined into the decision models. **SHIPPED this sprint.**
- **Part B — wire the existing-but-unused `account_context` family** (equity
  curve, daily PnL, open-trade count) into the decision models. **Scoped + logged
  as a follow-up** (`MB-20260604-003`) — see Deferred Items for the honest why.

## Tier
- **Tier-1** for the estimator module, the yfinance macro fetch adapter, the
  `market_features` columns (`builder_version v5→v6`), the fetch CLI, the opt-in
  build wiring, and the tests — additive, read-only over built `market_raw` + a
  public daily side-stream; past-only + one-day-lagged features → leakage-safe.
  No `src/runtime/`, order-path, or live file touched. The fetch adapter is
  off-VM-guarded (`ICT_OFFVM_BUILD_HOST=1`) so it can never run on the live VM.
- **Tier-3** for `ml/configs/mes-regime-5m-lgbm-macro-v1.yaml` and any promotion
  past `research_only` — operator-gated. The manifest ships at `research_only`;
  this sprint **proposes** + provides the A/B harness.

## Starting Context
- M14 Phase 2 ("better features"). S9 (range-vol) shipped + promoted to shadow
  on BTC (positive A/B); MES range-vol was mixed → held. S11 (funding/OI) shipped
  with an honest negative (OI retention-limited). S10 (order-flow) capture is
  live on the trainer, accruing forward.
- `market_features` (v5) carried close-to-close vol + log-returns + S9 range-vol
  + S11 funding/OI + S10 order-flow columns — all crypto-leaning. No cross-asset
  / macro family existed (gap G5's MES half).
- Operator picked S12 from the "what's next" fork (over S13 per-bar regime
  scoring and a take-stock pass) — i.e. strengthen the weakest leg autonomously.

## Repo State Checked
- Branch `claude/mlopt-s12-macro-mes` cut from `main` @ `f267089` (the S10
  deploy-record merge — S9/S11/S10 all already on `main`).
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ml/optimization-roadmap.md` (§ Phase 2, Session 2.4), `ROADMAP.md`
  (§ M14), `docs/sprint-logs/S-MLOPT-S{9,10,11}.md`,
  `docs/claude/ml-review-backlog.json`.

## Files and Systems Inspected
- `ml/datasets/funding_oi_features.py` + `ml/datasets/volatility_estimators.py`
  (the estimator-module shape mirrored), `ml/datasets/families/market_features.py`
  (the as-of-join core + schema + the S9/S11/S10 column-add pattern),
  `ml/datasets/adapters/{bybit_funding_oi,yfinance_offvm}.py` (off-VM guardrail
  + the yfinance `_download` test hook mirrored), `scripts/ml/fetch_funding_oi.py`
  (the fetch-CLI shape), `scripts/ops/build_trainer_datasets.sh`
  (`build_mes_market` / `build_mes_features_tf` / `build_mes_1d`),
  `ml/configs/mes-regime-5m-lgbm-{v2,yz-v1}.yaml` (the A/B manifest shape),
  `ml/datasets/families/account_context.py` + `src/units/accounts/risk.py`
  (`daily_risk_state` schema — for the Part B feasibility call).

## Work Completed
**Part A — cross-asset/macro features for MES (shipped):**
- **`ml/datasets/macro_features.py`** — pure transforms: `rolling_zscore`,
  `term_structure_slope` (VIX/VIX3M−1, >0=backwardation/stress), `level_spread`
  (rates slope), `rolling_return` (DXY momentum), and `compute_macro_feature_rows`
  which computes the 7-column feature set **at daily cadence** and **stamps each
  day's row at the START of the next day** (the one-day leakage lag that keeps a
  daily close out of same-day intraday bars). `MACRO_FEATURE_COLUMNS` is the
  single source of truth for the schema/producer/tests.
- **`ml/datasets/adapters/yfinance_macro.py`** — off-VM-guarded fetcher: pulls
  daily closes for `^VIX`/`^VIX3M`/`DX-Y.NYB`/`^TNX`/`^IRX`, merges by date,
  hands to `compute_macro_feature_rows`. `_download` is the test hook (CI never
  hits the network).
- **`scripts/ml/fetch_macro.py`** — Tier-1 CLI mirror of `fetch_funding_oi.py`;
  writes `data.jsonl` + `metadata.json`.
- **`market_features` v5→v6** — optional `macro_path` → 7 as-of-carried columns
  (`vix_level`/`vix_zscore`/`vix_term_slope`/`dxy_zscore`/`dxy_return`/
  `ust10y_level`/`ust_slope_3m10y`). **Default-preserving** (omit → 0.0; every
  existing build, BTC and MES, unchanged). Because the producer pre-computes +
  lags, the builder only carries forward — no re-windowing of a daily step
  function across 5m bars.
- **A/B manifest** `ml/configs/mes-regime-5m-lgbm-macro-v1.yaml` (`research_only`)
  — identical to `mes-regime-5m-lgbm-v2` except the added macro feature group
  (clean isolation; same frozen regime spec on `rolling_log_return_vol`).
- **Orchestrator** `build_trainer_datasets.sh` — `ensure_mes_macro` fetches the
  side-stream once per cycle (best-effort, NON-FATAL, `MES_MACRO=1` default-on);
  `build_mes_features_tf` joins it via `macro_path` on 5m/15m **and** the deep 1d
  head (which reuses the same function) for free.

**Part B — account_context wiring:** scoped, not shipped — `MB-20260604-003`.

## Validation Performed
- `pytest tests/ml/test_macro_features.py tests/ml/test_yfinance_macro.py
  tests/ml/datasets/test_market_features.py` → **50 passed**. New coverage:
  pure-transform correctness (z-score, term-slope sign, leakage-lag stamping,
  missing-series→0.0), adapter merge + off-VM guard, and on the builder:
  columns-zero-without-`macro_path` (default-preserving), populated-with-path,
  **as-of past-only** (a macro stream entirely after the bars yields all-0.0),
  and schema/validate round-trip on a `symbol_scope=MES` build.
- Broader `tests/ml` (ex-pandas-dependent `test_resample`/`test_yfinance_offvm`,
  not installed in the sandbox) → **224 passed**; the shared-module imports
  (funding/orderflow/vol) still green.
- `ruff check` on all new + changed Python → clean.
- End-to-end producer smoke (no network): `compute_macro_feature_rows` emits
  sensible values + the correct D+1 leakage stamp; `bash -n` on the orchestrator
  passes.

## Documentation Updated
- This sprint log.
- `ROADMAP.md` § M14 — S12 row (Part A done, Part B follow-up).
- `docs/ml/optimization-roadmap.md` § Session 2.4 — status + shipped artifacts.
- `docs/claude/ml-review-backlog.json` — `MB-20260604-004` (the macro A/B,
  review when MES `market_features` rebuilds on v6 with a macro stream) and
  `MB-20260604-003` (the Part B account_context wiring follow-up + its blockers).

## Contradictions or Drift Found
- None. The macro family is purely additive; the `market_features` doc table +
  version comment were updated in lockstep with the schema (field-beats-comment).

## Risks and Follow-Ups
- **Macro alpha for an intraday regime head is plausibly thin.** The A/B may
  return a negative — that is an acceptable, documented outcome (logged, not
  buried), exactly as S11 was.
- **Daily-vs-intraday leakage is the load-bearing risk** — mitigated by the
  one-day lag (a day-D row stamped D+1) + the as-of past-only join, both unit-
  tested. Any future change to the macro cadence must preserve the lag.
- The macro stream is daily, so it conditions the regime at day granularity; it
  cannot capture intraday macro shocks (acceptable for a regime label).

## Deferred Items
- **Part B — `account_context` wiring (`MB-20260604-003`).** The family exists
  but (a) has **no manifest consuming it**, and (b) the "equity curve / daily
  PnL / open-trade count" the roadmap wants are **not recorded as as-of-signal-
  time snapshots** — `daily_risk_state` holds only end-of-interval daily running
  values, so a post-hoc join would **leak** (it includes PnL from trades after
  the signal). The prop accounts that `account_context` targets are also sparse/
  dry, so a manifest today would likely be untrainable. Honest path: instrument
  per-signal account-context snapshots first (or confirm prop-account volume via
  the next `/ml-review` diag pull), THEN wire. Logged with both sub-options.

## Next Recommended Sprint
- **S13 — per-bar regime scoring (Phase 3.1)** remains the roadmap's single
  highest-leverage *unblock* (lets strong regime heads earn shadow→advisory
  evidence on their own bar cadence). Tier-2 (live runtime) → operator-gated to
  deploy. Or the Part B instrumentation above once prioritised.

## Wrap-Up Check
- Tier-1 code + Tier-3 manifest-proposal only; no live-path/order/config file
  touched. Tests + ruff green. Docs updated. Branch pushed; draft PR opened.
