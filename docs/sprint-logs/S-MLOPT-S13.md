# Sprint Log: S-MLOPT-S13

## Date Range
- Start: 2026-06-04
- End: 2026-06-04

## Objective
- Primary goal: build the **per-bar regime scoring path** (M14 Phase 3.1) — a
  tick-cadence hook that scores every `shadow`-stage regime head on its own
  `(symbol, timeframe)` bar, independent of whether a strategy emits an
  actionable buy/sell signal. Closes `MB-20260529-001` (option a).
- Secondary goals: keep it strictly observe-only (no order-path reach), add a
  per-bar write-rate dedup + an env kill-switch, and document the train/serve
  feature-parity gap the work surfaced (a Phase 4.2 follow-up).

## Tier
- **Tier 2** (live-runtime path — adds a module the live trader's
  `run_pipeline` calls every tick).
- Justification: the hook executes inside the live trading loop. It is
  observe-only (only appends to `runtime_logs/shadow_predictions.jsonl`, never
  touches an order package or the risk manager — same WS7 contract as the
  existing signal-time shadow emitter), but because it runs in the live tick it
  is Tier-2: open the PR as **draft**, get operator approval, then merge +
  deploy.

## Starting Context
- Active roadmap items: M14 Phase 2 (S9–S12) shipped; S13 (3.1) was the
  recommended next sprint and the highest-leverage regime unblock. The S9
  range-vol (yz) heads were promoted `research_only → shadow` on 2026-06-04 but
  cannot earn `shadow → advisory` without an order-influencing track record.
- Prior sprint reference: [`S-MLOPT-S9.md`](S-MLOPT-S9.md) (range-vol heads;
  its "Next Recommended Sprint" names S13 explicitly).
- Known risks at start: live-runtime change; must not flood the shadow log;
  must reuse the frozen `regime_spec` bucketing so live features match training.

## Repo State Checked
- Branch reviewed: `claude/m14-progress-next-u260C` (off `main`).
- Deployment state reviewed: live trader runs `run_pipeline` per tick (~15 min)
  via `src/main.py`; shadow predictions today are written only by
  `strategy_signal_builders._emit_shadow_preds` on an actionable signal.
- Canonical docs reviewed: `ROADMAP.md` M14 table, `docs/ml/optimization-roadmap.md`
  Phase 3.1, `CLAUDE.md`, `CLAUDE-RULES-CANONICAL.md` (tiers).

## Files and Systems Inspected
- Code files inspected: `src/runtime/pipeline.py` (tick entry `run_pipeline`),
  `src/runtime/strategy_signal_builders.py` (`_emit_shadow_preds`,
  `_resolve_shadow_predictors`), `src/runtime/regime_shadow.py`
  (`feature_row_for_predictor`, `rolling_log_return_vol`, `regime_spec_of`),
  `src/runtime/shadow_adapter.py` (`with_shadow_preds`),
  `src/runtime/market_data.py` (`connector_for_symbol`, `fetch_candles`),
  `ml/predictors/shadow.py` (`ShadowPredictor` writer + record schema),
  `ml/predictors/lightgbm.py` (missing-feature → NaN handling),
  `ml/trainers/lightgbm_multiclass.py` (regime-spec freeze),
  `ml/shadow/factory.py` (`discover_shadow_stage_model_ids`, `resolve_predictors`).
- Config files inspected: `ml/configs/btc-regime-5m-lgbm-yz-v1.yaml` (frozen
  `vol_feature_column: yang_zhang_vol`, full `feature_columns` list).
- Docs inspected: `S-MLOPT-S9.md`, optimization-roadmap Phase 3.1 / 4.2.

## Work Completed
- **New module `src/runtime/regime_bar_scoring.py`** —
  `emit_regime_bar_predictions(settings)`:
  1. resolves the `shadow`-stage model set from the registry (same auto-wire
     source as the signal path), cached per `(root, ids)`;
  2. keeps only **regime** heads (those carrying a frozen `regime_spec`);
  3. fetches each head's own market candles via `connector_for_symbol`
     (BTC → Bybit, MES → IBKR);
  4. builds the feature row with the **existing**
     `regime_shadow.feature_row_for_predictor` so the per-bar row is identical
     to the signal-time one (live `vol_bucket` against the frozen edges);
  5. writes one shadow prediction to the same
     `runtime_logs/shadow_predictions.jsonl` via `with_shadow_preds`.
  - **Write-rate control:** dedup by `(model_id, last_bar_timestamp)` → at most
    one record per closed bar, so calling it every tick never floods between
    bars.
  - **Observe-only + never raises:** only calls `ShadowPredictor.predict`; every
    per-head failure is isolated and logged; the whole call is wrapped so it can
    never break a tick.
  - **Kill-switch:** `REGIME_BAR_SCORING_DISABLED` (default off → path on).
  - Per-bar feature rows carry `event_source: "per_bar"` so per-bar records are
    distinguishable from signal-time records in later analysis.
- **Hook in `src/runtime/pipeline.py::run_pipeline`** — one guarded call near
  the top of the tick (before strategy routing), wrapped in try/except.
- **Tests** `tests/runtime/test_regime_bar_scoring.py` (19) — enable/kill-switch,
  last-bar extraction, single-head scoring + log write, dedup (same bar scored
  once, new bar re-scores), non-regime head ignored, fetch-failure + None-candle
  skips, multiple heads each on their own bar, one-head-failure-isolation, and
  the uncomputable-vol skip. Predictor list / candle fetcher / dedup cache are
  injectable so the path runs with no registry or live market data.

## Validation Performed
- Tests run: `tests/runtime/test_regime_bar_scoring.py` → 19 passed;
  `tests/runtime/test_regime_shadow.py` + `tests/ml/test_regime_classifier.py`
  → 39 passed (no regression in the reused helpers).
- Manual code verification: confirmed the `ShadowPredictor` record schema
  (`predicted_at_utc, model_id, stage, score, row_keys, feature_row`) is written
  unchanged, so `/api/bot/shadow/*`, `/api/bot/trades/scores` and the
  `gate-check` `shadow_soak` criterion consume the new records with no change.
- Lint: `ruff check` clean on the new module, the pipeline hook, and the test.
- Gaps not yet verified: **on-VM behaviour** — that a live tick actually writes
  per-bar records for the shadow regime heads (incl. the 1h + MES heads), and
  the realized per-day record volume. Verify via the diag relay
  (`/api/diag/log_file?name=shadow_predictions`) after deploy. This is the
  Tier-2 post-merge verification step.

## Documentation Updated
- Roadmap updates: `ROADMAP.md` M14 table — S-MLOPT-S13 row → DONE; M14
  narrative line.
- Subsystem doc updates: `docs/ml/optimization-roadmap.md` Session 3.1 → shipped;
  `CLAUDE.md` env-var table (`REGIME_BAR_SCORING_DISABLED`).
- Backlog: `docs/claude/ml-review-backlog.json` — `MB-20260529-001` resolved
  (plumbing landed); new `MB-20260604-005` for the train/serve feature-parity
  gap (Phase 4.2) that must close before any head is promoted shadow→advisory on
  per-bar evidence.

## Contradictions or Drift Found
- **Train/serve feature parity gap (key finding).** The live regime feature row
  carries `vol_bucket` + one vol value; the heads also train on the range-vol
  estimators, log-return lags and time features, which are **absent live** and
  become NaN. For the S9 yz heads the frozen `vol_feature_column` is
  `yang_zhang_vol`, but the live value is plain close-to-close vol
  (`regime_shadow.rolling_log_return_vol`). This is **pre-existing** — it affects
  the signal-time path and the v2 heads exactly as much — so S13 deliberately
  reuses the same computation (per-bar == signal-time) rather than diverge. The
  gap is logged as `MB-20260604-005` (Phase 4.2). S9's promotion note
  ("`freeze_regime_spec` on `yang_zhang_vol` so the live path buckets ticks
  against the trained edges") assumed the live path computes yang-zhang; it does
  not yet.

## Risks and Follow-Ups
- Remaining technical risks: extra market-data fetches per tick (≈ one per
  shadow regime head; ~5 today) — trivial at the ~15-min tick cadence, and the
  MES heads reuse the existing IBKR connector. Kill-switch is the rollback.
- Remaining product decisions (Tier 3): the `shadow → advisory` flip for any
  regime head stays operator-gated and is **additionally** blocked on
  `MB-20260604-005` (feature parity) so the per-bar evidence is trustworthy.
- Blockers: none for the plumbing.

## Deferred Items
- Train/serve feature parity for the regime heads (compute the range-vol
  estimator named by `vol_feature_column` + the log-return/time features live) →
  Phase 4.2 / `MB-20260604-005`.
- On-VM record-volume verification → post-merge (diag relay).

## Next Recommended Sprint
- Suggested next sprint: **S-MLOPT-S14 (3.2) causal HMM/GMM regime family**
  (Tier-1, can proceed autonomously) — OR close **`MB-20260604-005`** (feature
  parity) first if the operator wants the yz heads promotable sooner, since that
  is the gate that makes the per-bar evidence count.
- Why next: S13 unblocks the *cadence*; parity unblocks the *validity* of that
  cadence's evidence. Both precede the next regime-router wiring (S15).
- Required verification before starting: confirm via the diag relay that the
  shadow regime heads are now logging per-bar after deploy.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No order-path / pipeline *stage* logic changed — the hook is observe-only;
      `docs/TRADE-PIPELINE.md` stages are unchanged (no Trade Process tab change).
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (the feature-parity gap → `MB-20260604-005`).
- [x] Remaining unknowns stated: on-VM per-bar record volume (verify post-deploy
      via diag relay); parity gap blocks shadow→advisory promotion.
