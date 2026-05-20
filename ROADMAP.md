# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-20 (M11 complete — S0–S11 all merged).
>
> **Canonical authority:**
> 1. [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
> 2. [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md)
> 3. This file (`ROADMAP.md`)
> 4. Current sprint log in `docs/sprint-logs/`
>
> **Scope-specific master plans:**
> - M9 + M10 — [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>   AI-scope canonical [`ai-model-platform.md`](docs/architecture/ai-model-platform.md);
>   pipeline types + stage contracts;
>   data layer [`docs/data/`](docs/data/) + [`ml/datasets/`](ml/datasets/) +
>   [`docs/ml/market-raw-adapters.md`](docs/ml/market-raw-adapters.md);
>   training center [`docs/ml/`](docs/ml/) + [`ml/`](ml/);
>   first specialist baselines under `ml/configs/`.
> - M11 — [`docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`](docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md).
>   Architecture target: [`docs/architecture/multi-strategy-architecture-target.md`](docs/architecture/multi-strategy-architecture-target.md).

---

## Core Principles

1. **Lean solutions.**
2. **Stability first.**
3. **Profitability focus.**

---

## M0..M11 Milestone Roadmap

| Milestone | Type | Focus | Status |
|---|---|---|---|
| **M0–M5** | auto-claude | Foundation → Strategy testing | ✅ CLOSED |
| **M6** | auto-claude | Web app UI | 🔄 IN PROGRESS (dashboard repo) |
| **M7** | pm-sprint | Strategy review gate | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | 🔄 IN PROGRESS — WS1+WS2+WS4+WS4-FU+WS5-A closed; WS5-B-PART-1 + PART-2 (PR 2A + PR 2B) + WS5-C closed 2026-05-10. |
| **M10** | auto-claude | HF / data pipeline | 🔄 IN PROGRESS — WS3 closed; WS5-B-PART-1 adds `market_raw`; WS5-B-PART-2 PR 2A wires Bybit off-VM fetch; PR 2B adds `market_features`; WS5-C adds `setup_labels` (fifth buildable family); WS9 continuous. |
| **M11** | auto-claude | Multi-strategy architecture refactor | ✅ COMPLETE 2026-05-20 — S0–S11 all merged (PRs #1604–#1610). Typed abstractions, allocator, advisory ML hooks, attribution API, ICT filter module, health-review update. S7 IB/MES deferred pending credentials. See [`ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`](docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md). |

### M9 / M10 — AI traders workstreams (WS1–WS10)

> Master plan: [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>
> Implementation order: WS1 → WS2 → WS3 → WS4 + WS4-FU → WS5
> baselines (sub-sprints A..F; WS5-B further split into PART-1 +
> PART-2) → shadow mode (WS7) → WS6 → WS8 + WS10.
> WS9 is continuous from WS3 onwards.

| WS | Title | Status | Sprint plan |
|---|---|---|---|
| **WS1** | Architecture baseline | ✅ DONE | [ws1-architecture-baseline.md](docs/sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| **WS2** | Canonical trade pipeline | ✅ DONE | [ws2-canonical-pipeline.md](docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| **WS3** | Data foundation | ✅ DONE | [ws3-data-foundation.md](docs/sprint-plans/ai-traders/ws3-data-foundation.md) |
| **WS4** | Training center | ✅ DONE (S-AI-WS4 + S-AI-WS4-FU) | [ws4-training-center.md](docs/sprint-plans/ai-traders/ws4-training-center.md) + [ws4-followups.md](docs/sprint-plans/ai-traders/ws4-followups.md) |
| **WS5** | Baseline models | ✅ DONE (A through F) — S-AI-WS5-A + S-AI-WS5-B-PART-1 + PART-2 (PR 2A + PR 2B) + S-AI-WS5-C + S-AI-WS5-C-FU + S-AI-WS5-D + S-AI-WS5-E + S-AI-WS5-F | [ws5-baseline-models.md](docs/sprint-plans/ai-traders/ws5-baseline-models.md) |
| **WS6** | Open-source model layer | 📋 NOT STARTED | [ws6-open-source-models.md](docs/sprint-plans/ai-traders/ws6-open-source-models.md) |
| **WS7** | Deployment tiers | 🔄 IN PROGRESS (PART-1..PART-6 + ict_scalp shadow wiring done — all three production strategies wired; model training + audit log rotation queued) | [ws7-deployment-tiers.md](docs/sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| **WS8** | Monitoring and feedback loops | 🔄 IN PROGRESS (PART-1 inspector CLI + PART-2 dashboard endpoints + PART-3 drift detector shipped; external-reference variant + alerting queued) | [ws8-monitoring-feedback.md](docs/sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| **WS9** | Oracle / Hugging Face runtime split | 🔄 IN PROGRESS — two-VM topology shipped (training-center VM provisioning workflow + `run_training_cycle.sh`); training cycle stabilised end-to-end on 2026-05-14 (S-AI-WS9-FU-2): case-insensitive setup matching, empty-dataset clean-skip path, run-versioned registry with per-run history, on-disk reconciler. Daily-cadence cycle now lands all trainable manifests with `overall_rc=0`. HF lifecycle queued. | [ws9-runtime-split.md](docs/sprint-plans/ai-traders/ws9-runtime-split.md) |
| **WS10** | Architecture-doc enforcement | 🔄 IN PROGRESS (advisory guard + checklist + PR template + changelog/known-gaps sections shipped; hard-fail upgrade queued) | [ws10-arch-doc-enforcement.md](docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

**Non-negotiable rules:**

- Live trading safety > feature growth.
- No heavy training on the Oracle live VM (WS9).
- No model in live strategy logic without staged promotion +
  operator approval.
- AI output cannot bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code updates the architecture docs in
  the same PR.
- No auto-publishing datasets to HF (S-AI-WS3).
- No editing past `StatusEvent` entries in the registry
  (S-AI-WS4).
- No promoting to `live-approved` or `champion` without operator
  approval recorded in `--by` + `--reason` (S-AI-WS4).
- No outcome columns as features against `won` on `trade_outcomes`
  (S-AI-WS5-A).
- **No `ICT_OFFVM_BUILD_HOST=1` on the Oracle live VM**
  (S-AI-WS5-B-PART-1).

### Active milestone queue (next 3)

1. **M6 — Web app UI (dashboard repo).**
2. **(M5 P4 closed 2026-05-10).**
3. **Closed-flat invariant auto-flatten promotion** — gated on ≥ 7 days clean alert-only soak.

> **AI-traders queue note:** WS1+WS2+WS3+WS4+WS5-A+WS4-FU+WS5-B-PART-1 closed
> 2026-05-10. **Next on AI-traders track is WS5-B-PART-2** — regime
> classifier + Bybit off-VM fetch wiring (operator owns the wiring;
> needs read-only Bybit V5 creds + a non-VM build host with
> `ICT_OFFVM_BUILD_HOST=1`).

### Repo and hosting boundary (MANDATORY)

Dashboard web app **lives in a separate repository** and **runs
on Vercel** — NOT on the Oracle VM.

---

## Historical Sprint Ledger

Full detail preserved in git history. Recent AI-traders sprints:

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| **S-REFACTOR-S0** | **S0 — Multi-strategy architecture planning (2026-05-20).** Created phase roadmap `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`, architecture target `docs/architecture/multi-strategy-architecture-target.md`, sprint logs. Strategy category mapping: vwap=mean-reversion, turtle_soup=trend-pullback, ict_scalp=breakout-expansion. Three initial core strategies confirmed; ICT filter module deferred as fourth component. Documentation-only (Tier-1). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S1** | **S1 — Architecture scaffolding (2026-05-20).** Six new abstract types in `src/core/`: `AccountProfile` (typed YAML-backed, IB/Bybit-aware), `InstrumentProfile` (pre-built BTCUSDT/Bybit + MES/IB profiles), `SignalPackage` (normalized signal contract with is_actionable + attribution), `OrderPackage` (typed order with from_signal() attribution builder), `StrategyInterface` ABC (build_signal + build_order_package + category), `AllocatorInterface` ABC + `PassthroughAllocator` (identity allocator preserving current sizing behavior). 12 new tests in `tests/test_s1_abstractions.py`. No existing files modified. Zero live path changes. (Tier-1). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S2** | **S2 — Account + instrument profile wiring (2026-05-20).** `config/instruments.yaml` added (BTCUSDT/Bybit + MES/IB placeholder). `src/core/profile_loader.py` added — loads `accounts.yaml` into typed `AccountProfile` objects, instruments.yaml into `InstrumentProfile` objects. `AccountProfile.from_dict()` corrected for actual `mode: live\|dry_run` schema. `Coordinator.account_profiles` + `Coordinator.instrument_profiles` read-only properties added. 15 new tests in `tests/test_s1_abstractions.py` (extended). Execution path unchanged. (Tier-2). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S3** | **S3 — Signal/order package contracts wired (2026-05-20).** `SignalPackage` wired into all three live strategy signal builders via `_with_signal_package()` helper in `src/runtime/strategy_signal_builders.py`. Attribution fields (`strategy_name`, `category`, `instrument`) populated on every outgoing signal. 20 new tests. Additive only — live dict shape unchanged; existing callers unaffected. (Tier-2). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S4** | **S4 — Allocator + net position accounting (2026-05-20).** `AllocatorInterface` wired into `Coordinator` via `coordinator.allocator` property; `coordinator.build_order_packages()` delegates to allocator. `PassthroughAllocator` is default — identical sizing behavior as before. 19 new tests. Live path unchanged. (Tier-2). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S5** | **S5 — CENTRALIZED_ALLOCATOR feature flag shadow mode (2026-05-20).** `_centralized_allocator_enabled()` added to `src/runtime/runtime_flags.py`. Shadow audit block added to `pipeline.py` multi-account dispatch — logs typed path decisions to `allocator_decisions.jsonl` when flag is on, without affecting orders. Default off — live runtime unaffected. 10 new tests. (Tier-3, PM-approved). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S6** | **S6 — CENTRALIZED_ALLOCATOR primary path (2026-05-20).** When `CENTRALIZED_ALLOCATOR=true` and signal has a typed `SignalPackage`, coordinator builds an `OrderPackage` from typed fields (not raw dict). Allocator qty logged. Fallback to raw dict path when flag off or signal_package absent — zero behavior change in production (flag defaults false). 14 new tests. (Tier-3, PM-approved). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S7** | **S7 — Typed multi-account dispatch (2026-05-20).** `Coordinator.multi_account_execute_typed()` added — typed dispatch path that builds `OrderPackage` via allocator and logs attribution. Pipeline `S7 typed dispatch` path hooked in. PR #1604. 13 new tests. (Tier-2, merged). Note: IB/MES shadow integration (original S7 in roadmap) deferred — no IB credentials in scope. | ✅ Done 2026-05-20 (`#1604`) | M11 |
| **S-REFACTOR-S8** | **S8 — PortfolioState typed snapshot + net position accounting (2026-05-20).** New `src/core/portfolio_state.py`: `PortfolioState` dataclass snapshots all open positions cross-strategy; `net_positions_by_symbol()` aggregates signed qty per symbol. Coordinator exposes `portfolio_state` read-only property. PR #1605. 26 new tests. (Tier-2, merged). | ✅ Done 2026-05-20 (`#1605`) | M11 |
| **S-REFACTOR-S9** | **S9 — StrategyBase aligned with StrategyInterface (2026-05-20).** `src/units/strategies/_base.py` updated so `StrategyBase` formally inherits `StrategyInterface` (S1-NOTE-003). `strategy_id` + `_category` class attributes; `category` property; static helpers delegate to module-level functions. `build_signal` / `build_order_package` raise `NotImplementedError` on base. 29 new tests. All existing module-level helpers unchanged (backward-compat). (Tier-1). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S10** | **S10 — ML decision-layer advisory hooks (2026-05-20).** `Coordinator.log_advisory_scores()` method added — writes structured advisory decision records to `runtime_logs/advisory_decisions.jsonl` whenever a shadow/advisory model's score influences dispatch logging. Advisory flag propagates through coordinator with no order effect (read-only until PM promotes a model to advisory stage). `src/web/api/routers/diag.py` `_LOG_FILES` allowlist extended with `advisory_decisions` key. (Tier-1). | ✅ Done 2026-05-20 | M11 |
| **S-REFACTOR-S11** | **S11 — Attribution API (2026-05-20).** New `src/web/api/routers/attribution.py`: `GET /api/bot/positions/net` returns net open positions per symbol (calls `net_positions_by_symbol()`); `GET /api/bot/strategy/attribution` returns closed-trade P&L grouped by strategy_name from `trade_journal.db`. Both endpoints are best-effort read (DB errors return empty, never 5xx). 19 tests (direct router-function call pattern — TestClient avoided due to pyo3 panic). PR #1608. Health-review skill + schema updated to add four new M11 dimensions: `net_positions`, `strategy_attribution`, `advisory_scores`, `allocator_path`. ICT filter module public API (`src/ict_detection/__init__.py`) + 20 module tests landed in same window. PR #1609 (S2 + S8-ICT combined). PR #1610 (health-review doc update). (Tier-1). | ✅ Done 2026-05-20 (`#1608` `#1609` `#1610`) | M11 |
| **S-AI-ROADMAP** | AI traders models roadmap adopted | ✅ Done (`#693` `1eb59f6`) | M9, M10 |
| **S-AI-WS1** | Architecture baseline | ✅ Done (`#694` `f453b89`) | M9 |
| **S-AI-WS2** | Canonical trade pipeline | ✅ Done (`#701` `42a1e6f`) | M9 |
| **S-AI-WS3** | Data foundation | ✅ Done (`#704` `60807f4`) | M10 |
| **S-AI-WS4** | Training center | ✅ Done (`#719` `b910fd3`) | M9 |
| **S-AI-WS5-A** | Outcome probability baseline | ✅ Done (`#730` `6a9f5a0`) | M9 |
| **S-AI-WS4-FU** | WS4 follow-ups | ✅ Done (`#732` `8a69e97`) | M9 |
| **S-AI-WS5-B-PART-1** | **WS5-B Part 1 — `market_raw` multi-source adapter framework.** Canonical row shape pinned. CSV adapter live; Bybit off-VM scaffold (env-gated) with the actual exchange call filed for operator wiring. WS9 enforced via `ICT_OFFVM_BUILD_HOST=1` env-gate. Logged in `docs/sprint-logs/S-AI-WS5-B-PART-1.md`. | ✅ Done 2026-05-10 (`#733`) | M10 |
| **S-AI-WS5-B-PART-2 PR 2A** | **WS5-B Part 2 PR 2A — Bybit off-VM fetch wiring.** `BybitOffvmMarketRawAdapter._fetch_bars` wired via ccxt's `fetch_ohlcv`; paginated `since` cursor over `[start, end]`; CI mocks the exchange. Builder framework auto-forwards `symbol_scope` / `timeframe` into `iter_rows` kwargs. Env-gate retained. Logged in `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2A.md`. | ✅ Done 2026-05-10 (`#742`) | M10 |
| **S-AI-WS5-B-PART-2 PR 2B** | **WS5-B Part 2 PR 2B — Regime classifier baseline.** `market_features` family (rolling vol + 3-class regime label, forward-window leakage discipline) + `RegimeClassifierTrainer` (per-bucket modal) + `MulticlassPredictor` + `MulticlassClassificationEvaluator` + `baseline-regime-classifier.yaml` manifest. New non-negotiable: no forward-window / label columns as features against `regime_label`. Logged in `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2B.md`. | ✅ Done 2026-05-10 (`#745`) | M9 |
| **S-AI-WS5-C** | **WS5-C — Setup quality scorer.** `setup_labels` family (CLOSED, non-backtest, non-empty `setup_type` trades; emits `r_multiple = pnl_percent / risk_pct` capped at `±r_cap`) + `PerStrategyWinRateTrainer` extended with `target_kind: numeric_mean` knob (per-bucket sample mean of any numeric target) + `baseline-setup-quality.yaml` manifest using `RegressionEvaluator`. Architecture-canonical doc gains an explicit "AI-traders training workflow" section anchored on the `/health-review` skill's per-trade decision grades as labelled feedstock. Training-center doc gains a "Training session workflow" + table of established manifests so future training sessions follow the documented path. Logged in `docs/sprint-logs/S-AI-WS5-C.md`. | ✅ Done 2026-05-10 (`#754`) | M9 |
| **S-AI-WS5-C-FU** | **WS5-C follow-up — setup-quality V2 (audit-joined source).** New `setup_labels_audit` family: joins `runtime_logs/signal_audit.jsonl` recorded setups with the matching CLOSED trade by composite key `(strategy, symbol, timestamp ± window)` (no stable signal_id exists). Emits the same `r_multiple` label as v1 plus audit-time features (`audit_pattern`, `audit_side`, `audit_confidence`, `audit_bars_back_of_setup`). Rejected audits (`stage_rejections` non-empty, or no `entry`/`price`) are dropped — survivorship documented. Paired manifest `baseline-setup-quality-audit.yaml` using `audit_pattern` as the feature column for direct comparison against v1's `setup_type` baseline. Logged in `docs/sprint-logs/S-AI-WS5-C-FU.md`. | ✅ Done 2026-05-10 (`#759`) | M9 |
| **S-AI-WS5-D** | **WS5-D — Execution quality scorer.** New `execution_quality` family joining `trades` ↔ `order_packages` on `linked_trade_id`. Emits `entry_slippage_bps = ((actual_entry - intended_entry) / intended_entry) * 10_000` signed by direction (positive = trader paid worse than intended), capped at `±slippage_cap_bps` (default 200 bps). Carries `fill_latency_seconds` as bookkeeping. Paired manifest `baseline-execution-quality.yaml` reuses `PerStrategyWinRateTrainer` (numeric_mean) with `RegressionEvaluator` and time-aware holdout on `trade_created_at`. Same chassis as WS5-A / WS5-C / WS5-C-FU. Logged in `docs/sprint-logs/S-AI-WS5-D.md`. | ✅ Done 2026-05-10 (`#760`) | M9 |
| **S-AI-WS5-E** | **WS5-E — Post-trade review baseline.** First WS5 baseline whose label is reviewer-derived (not P&L-derived). New `review_journal` family scans `comms/requests/REQ-*.json` + `comms/archive/REQ-*.json`, parses the embedded health-review JSON payload from `.response.answers[*].free_text`, and emits one row per `trade_decision_grades[]` entry. Letter grade A/B/C/D/F maps to `decision_grade_score` 4/3/2/1/0; unknown letters drop the row. Paired manifest `baseline-post-trade-review.yaml` predicts per-`setup` mean grade-score using the same `PerStrategyWinRateTrainer` (numeric_mean) chassis. Empty-state acceptable until operator answers prompts with the JSON template. Logged in `docs/sprint-logs/S-AI-WS5-E.md`. | ✅ Done 2026-05-10 (`#766`) | M9 |
| **S-AI-WS5-F** | **WS5-F — Prop mission policy baseline.** Closes the WS5 baseline series. New `account_context` family joins `trade_journal.db::trades` with prop-account mission rules in `config/accounts.yaml` (filtered to `type: prop`). Emits one row per non-backtest prop trade (taken AND rejected) with binary label `was_taken` derived from `trades.status` (case-insensitive). Skip reason parsed from `entry_reason` for diagnostics. Mission rules (`max_dd_pct`, `daily_usd_cap`, `pos_size_cap`, `target_profit_pct`, `account_state`, `overnight_restricted`) attached per row. Paired manifest `baseline-prop-mission-policy.yaml` predicts per-`strategy_name` acceptance rate via binary `PerStrategyWinRateTrainer` + `ClassificationEvaluator`. Per-trade equity / drawdown snapshots not yet recorded — filed as instrumentation follow-up. Logged in `docs/sprint-logs/S-AI-WS5-F.md`. | ✅ Done 2026-05-10 (`#769`) | M9 |
| **S-AI-WS7-PART-1** | **WS7-PART-1 — Deployment-stage metadata + ShadowPredictor scaffold.** Opens WS7. `RegistryEntry` gains a new orthogonal `target_deployment_stage` field (7-state WS7 ladder: `research_only` → `candidate` → `backtest_approved` → `shadow` → `advisory` → `limited_live` → `live_approved`) plus `stage_history` of `StageEvent` records. New `ModelRegistry.promote_stage()` method enforces forward + rollback edges, refuses no-op transitions, requires non-blank `by` + `reason`. Backward-compat: old entries default to `research_only` on load. New `ShadowPredictor` wrapper composes any `Predictor` with a structured JSONL audit logger (`predicted_at_utc`, `model_id`, `stage`, `score`, `row_keys` — values intentionally not captured). **No live pipeline code touched** — pipeline-side shadow-call wiring is PART-2 (queued, will need operator review of integration point). Logged in `docs/sprint-logs/S-AI-WS7-PART-1.md`. | ✅ Done 2026-05-10 (`#771`) | M9 |
| **S-AI-WS7-PART-2** | **WS7-PART-2 — Shadow-mode per-strategy adapter.** Lands the per-strategy integration glue: `src/runtime/shadow_adapter.py::with_shadow_pred(decision, *, predictor, feature_row, logger=None)`. Stateless. Returns the deterministic `decision` byte-for-byte regardless of predictor outcome (defence-in-depth test verifies the package's keys are unchanged). Wraps `predictor.predict(...)` in `try/except` so model failure cannot crash the tick. `predictor=None` is pass-through so strategy authors can thread the helper through unconditionally. Bare `Predictor` (non-`ShadowPredictor`) is rejected with `TypeError` — `ShadowPredictor` is the audit-log surface and must not be bypassed. No production strategy is wired in this PR — strategy adoption is PART-3. Logged in `docs/sprint-logs/S-AI-WS7-PART-2.md`. | ✅ Done 2026-05-10 (`#772`) | M9 |
| **S-AI-WS7-PART-3** | **WS7-PART-3 — Wire vwap through `with_shadow_pred`.** First production-strategy adoption of the PART-2 helper. Operator-chosen target: `vwap` (highest-volume strategy in the bot). Operator-chosen first predictor: a constant placeholder (no trained model needed for this PR). `vwap.order_package` builds its package as a local var, then threads it through `with_shadow_pred(predictor=cfg.get("_shadow_predictor"), feature_row=...)` before returning. New private `_build_shadow_feature_row(package)` projects to a signal-time feature dict aligned with WS5-C / WS5-D feature surface; outcome columns (`pnl`, `r_multiple`) explicitly excluded. WS7 acceptance criterion *"a new model can run in shadow mode without changing live trading behavior"* now SATISFIED. `config/strategies.yaml` unchanged — production runs without operator opt-in carry no predictor. PART-4 (predictor source / factory) filed. Logged in `docs/sprint-logs/S-AI-WS7-PART-3.md`. | ✅ Done 2026-05-10 (`#774`) | M9 |
| **S-AI-WS7-PART-4** | **WS7-PART-4 — Multi-predictor shadow + config-driven source.** Operator-stated spec (2026-05-10): vwap can run multiple shadow predictors concurrently, sourced from `config/strategies.yaml`. New `with_shadow_preds` plural helper alongside the singular form (per-predictor failure isolation — one broken model never blocks the others). New `ml/shadow/factory.py` resolves `shadow_model_ids` against the registry with a stage gate (`{shadow, advisory, limited_live, live_approved}` allowed; `{research_only, candidate, backtest_approved}` refused). New optional `shadow_model_ids: []` field on the vwap YAML block — production rollout is a YAML edit, not a code change. Three-mode resolution priority in vwap (`_shadow_predictors` injection > `_shadow_predictor` singular legacy > `shadow_model_ids` factory > empty). Logged in `docs/sprint-logs/S-AI-WS7-PART-4.md`. | ✅ Done 2026-05-10 (`#776`) | M9 |
| **S-AI-WS7-PART-5** | **WS7-PART-5 — turtle_soup adoption of shadow harness.** Apply the proven PART-3 / PART-4 pattern to the second production strategy. `turtle_soup.order_package` now threads through `with_shadow_preds`, with the same 3-mode `_resolve_shadow_predictors(cfg)` priority and a strategy-specific `_build_shadow_feature_row` (exposes `atr` + `body_to_range` for setup-quality models). `config/strategies.yaml` adds `turtle_soup.shadow_model_ids: []` (empty default — no live runtime impact). 12 new tests mirror the vwap shadow surface. After this PR, both production strategies (vwap + turtle_soup) can run concurrent shadow predictors from YAML; WS7 acceptance criterion satisfied for both. Filed: PART-6 Coordinator-side resolution, shared resolver helper if a 3rd strategy adopts. Logged in `docs/sprint-logs/S-AI-WS7-PART-5.md`. | ✅ Done 2026-05-10 (`#778`) | M9 |
| **S-AI-WS7-PART-6** | **WS7-PART-6 — Coordinator-side shadow predictor cache.** Lift `ml.shadow.factory.resolve_predictors(...)` out of the strategy hot path. New `Coordinator._shadow_predictors_cache: dict[str, list]` populated lazily per strategy on first dispatch; the dispatcher merges `_shadow_predictors` into the cfg passed to `mod.order_package`, and the strategies' resolution mode 1 short-circuits the per-tick factory call. `reload_strategy_config` clears the cache so a YAML edit re-resolves on the next tick. Factory cost moves from O(ticks) to O(reloads). Strategies still support modes 2/3/4 for direct `mod.order_package(cfg, ...)` callers. 5 new tests cover cache identity, reload invalidation, per-strategy independence, and dispatcher injection. Logged in `docs/sprint-logs/S-AI-WS7-PART-6.md`. | ✅ Done 2026-05-10 (`#779`) | M9 |
| **S-AI-WS8-PART-1** | **WS8-PART-1 — Shadow predictions inspector CLI.** First WS8 deliverable. Makes `runtime_logs/shadow_predictions.jsonl` (the WS7 audit log) observable. New `ml/shadow/inspector.py` ships `ShadowRecord` (typed dataclass + `record_from_dict` validator), `iter_records` (streaming JSONL reader; per-line failures logged + skipped), `filter_records`, `aggregate` (per-`(model_id, stage)` stats: count, score mean/min/max, first/last seen), and two text formatters. New CLI subcommands `python -m ml shadow-inspect` (newest-N records with filters) and `python -m ml shadow-stats` (per-model aggregate). Filter surface: `--model-id`, `--stage`, `--since` (ISO-8601, naïve assumed UTC). Pure-logic module; the WS8-PART-2 dashboard endpoint will reuse it without duplicating parsing. 35 new tests (29 inspector unit + 6 CLI end-to-end). Filed: PART-2 dashboard route, PART-3 drift detector, audit log rotation. Logged in `docs/sprint-logs/S-AI-WS8-PART-1.md`. | ✅ Done 2026-05-10 (`#782`) | M9 |
| **S-AI-WS10** | **WS10 — Architecture-doc enforcement scaffold.** Stop the architecture doc drifting away from the codebase. New `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md` (rubric for what counts), `.github/PULL_REQUEST_TEMPLATE.md` (architecture-impact checkboxes), `scripts/arch_doc_guard.py` (pure-stdlib classifier; always exits 0), `.github/workflows/arch-doc-guard.yml` (PR-trigger; emits `::warning` annotation when high-impact paths change without an arch-doc update). `docs/ARCHITECTURE-CANONICAL.md` gains a curated **Change log** (architecture-deltas only) + **Known gaps** sections. **Advisory, not blocking** — hard-fail upgrade is filed as a follow-up once the workflow has track record. 18 unit tests for the classifier. Logged in `docs/sprint-logs/S-AI-WS10.md`. | ✅ Done 2026-05-10 (`#786`) | M9 |
| **S-AI-WS9** | **WS9 — Training-center VM provisioning + two-VM topology.** Makes "no heavy training on the Oracle live VM" enforced by topology, not just policy. New `scripts/ops/provision_training_vm.py` (OCI SDK, idempotent, quota-aware, JSONL events), `.github/workflows/provision-training-vm.yml` (workflow_dispatch + issue-trigger label `provision-training-vm`), `deploy/training-vm-cloud-init.yaml` (bootstraps Always Free Ampere A1 with `ict-trainer.service` DISABLED — operator opts in to training cycles). New operator runbook `docs/runbooks/training-vm.md`. Same SSH key as live VM (`VM_SSH_KEY`); shared compartment + subnet (IMDS-discovered from live VM). `docs/ARCHITECTURE-CANONICAL.md` gains a "Two-VM topology" subsection + change-log row + two new Known-gap entries. 7 mocked-OCI-client tests. Filed: `run_training_cycle.sh` body, cross-VM `trade_journal.db` sync, teardown workflow. Logged in `docs/sprint-logs/S-AI-WS9.md`. | ✅ Done 2026-05-10 (`#789`) | M9/M10 |
| **S-AI-WS8-PART-2** | **WS8-PART-2 — Shadow-predictions dashboard endpoints.** Makes the WS7 audit log observable from the Vercel dashboard, not just SSH-and-CLI. New `src/web/api/routers/shadow.py` ships `GET /api/bot/shadow/predictions` (newest-N records, filtered) + `GET /api/bot/shadow/stats` (per-`(model_id, stage)` aggregate). Both reuse `ml.shadow.inspector` from PART-1 — zero duplicate parsing. Response envelope `{log_present, log_path, records[], count}` distinguishes "no matches yet" from "log absent". Same filter surface as the CLI (`limit`, `model_id`, `stage`, `since`). FastAPI `Query` validation: `422` on out-of-range `limit`, `400` on bad `since`. `SHADOW_PREDICTIONS_LOG` env override. CLAUDE.md dashboard-API table + directory map updated. 14 endpoint tests. Filed: PART-3 drift detector, pagination cursor, WebSocket stream, auth tier upgrade if scores ever become sensitive. Logged in `docs/sprint-logs/S-AI-WS8-PART-2.md`. | ✅ Done 2026-05-10 (`#790`) | M9 |
| **S-AI-WS8-PART-3** | **WS8-PART-3 — Shadow-prediction drift detector.** Closes the WS8 monitoring loop. New `ml/shadow/drift.py` ships `summarize` + `ks_statistic` + `psi` + plain-English verdict buckets + a compound `compute_drift` returning a `DriftReport`. Pure stdlib (no numpy / scipy). New CLI subcommand `python -m ml shadow-drift --model-id X [...]` + dashboard route `GET /api/bot/shadow/drift?...`. Window-over-window self-comparison (default 30-day reference vs 7-day current, non-overlapping, anchored at now) — meaningful without an external reference distribution. `verdict` ∈ `{no_change, minor, moderate, significant, insufficient_data}`. PSI thresholds match industry convention (<0.1 / 0.1–0.25 / >0.25); KS sensitive to any shape change. 27 new tests (23 unit + 4 CLI). Filed: external-reference variant (registry-stored training-set histogram), Telegram alerter, per-feature drift, Wasserstein, multi-stage comparison. Logged in `docs/sprint-logs/S-AI-WS8-PART-3.md`. | ✅ Done 2026-05-10 (this PR) | M9 |
| **S-CFW-1** | **Cloudflare Worker proxy.** Stable `*.workers.dev` hostname fronting `/api/*` to the VM. **RETIRED 2026-05-10 in S-CFW-1-FU2** — Worker deployed cleanly but its outbound `fetch()` to a raw IPv4 host is rejected by Cloudflare with error 1003. Logged in `docs/sprint-logs/S-CFW-1-cloudflare-worker.md`. | 🖤 RETIRED 2026-05-10 (`#735`) | infra |
| **S-CFW-1-FU** | **cf-worker GitHub-Actions deploy.** `cf-worker-deploy` workflow that runs `wrangler deploy` from CI. Logged in `docs/sprint-logs/S-CFW-1-FU-gha-deploy.md`. **Workflow + label remain in the repo as a recipe;** unused now that the Worker layer is retired (S-CFW-1-FU2). | 🖤 RETIRED 2026-05-10 (`#740`) | infra |
| **S-CFW-1-FU2** | **Worker retired + tunnel verified.** Empirically retired the cf-worker layer after the deployed Worker hit Cloudflare error 1003 on raw-IP `fetch()`. Corrected the wrong claim in `docs/audit/vercel-edge-vs-cf-worker.md` (Workers do NOT allow raw-IPv4 targets — only DNS hostnames). Extended `pull_logs.sh` and verified the live `*.trycloudflare.com` URL (`planners-lbs-blind-trainer.trycloudflare.com`) — same as the 2026-05-10 wrap-up, so the dashboard's existing Vercel rewrite remains healthy. Logged in `docs/sprint-logs/S-CFW-1-FU2-worker-retired.md`. | ✅ Done 2026-05-10 | infra |
| **S-MSE-1** | **Multi-strategy execution Phase 1 — intent layer + delta-aware dispatch for Bybit2 / BTC/USDT.** Replaces the first-wins multiplexer + binary `_has_open_position` guard with a typed `StrategyIntent` → `DesiredPosition` → `ExecutionDelta` pipeline. Same-direction reinforcement keeps the larger valid target (no double-counting); conflicts resolve deterministically by priority → earliest timestamp → strategy-name alphabetical; at-target dispatch becomes a noop; below-target sends only the delta. Per-account `RiskManager.position_size` remains the single sizing site and the authoritative cap on every order — the intent layer never bypasses risk. Future strategies (ICT scalping, etc.) plug in via `register_intent_builder()` + a `DEFAULT_PRIORITIES` row with no aggregator code change. New modules: `src/runtime/intents.py`, `src/runtime/intent_multiplexer.py`, `src/runtime/positions.py`. Wired into `src/runtime/pipeline.py` and `src/core/coordinator.py::multi_account_execute`. Default `MULTI_STRATEGY_INTENT_LAYER` is **false**; pinned `true` in `deploy/ict-trader-live.service`. 66 new tests across `tests/test_multi_strategy_intents.py` (42) and `tests/test_intent_delta_dispatch.py` (24). Phase 2 (reduce / close / flip delta legs — currently refused with `intent_<action>_not_yet_wired_v1`) filed as follow-up. Logged in `docs/sprint-logs/S-MSE-1.md`. | ✅ Done 2026-05-14 (`#1125` `#1129` `#1130`) | execution |
| **ict-scalp-shadow** | **WS7 Phase 1 — ict_scalp_5m shadow-predictor wiring.** Apply the proven WS7 PART-3/4/5 pattern to the third production strategy. `ict_scalp.order_package` threads through `with_shadow_preds`; `_resolve_shadow_predictors(cfg)` provides 3-mode resolution; `_build_shadow_feature_row` projects the shared WS5 surface plus three ict_scalp-specific features (`sweep_depth_atr`, `fvg_size_norm`, `displacement_idx_from_end`). `config/strategies.yaml::ict_scalp_5m.shadow_model_ids` remains `[]` — no live runtime impact. 14 new tests in `tests/test_ict_scalp_shadow.py`. Phase 2 (train on ≥200 live signals, issue #1161) and Phase 3 (bind model in YAML, issue #1162) blocked until signal count threshold is met. | ✅ Done 2026-05-14 (`#1160`) | M9 |
| **S-AUDIT-PIPELINE-2026-05-17** | **Full trading-pipeline structural audit + findings execution.** 742-line audit across 13 scope areas (`docs/audits/full-pipeline-structural-audit-2026-05-17.md`). Sprints executed: **A-1** daily risk state persisted to SQLite (`daily_risk_state` table, keyed `account_id + date`); **A-2** halt flag moved to `/data/bot-data/` (survives tmpfs); **A-3** boot-time journal/exchange reconciliation (`reconcile_journal_vs_exchange_on_boot` — ghost rows → Telegram WARNING, untracked positions → INFO log, 6 new tests, `#1361`); **B-1** `STRATEGY_RISK_PCT` replaced with registry-driven loader, vwap corrected 0.5 → 1.0 (operator confirmed); ~~**B-2** `ict_scalp_5m` disabled in `config/strategies.yaml`~~ **— REVERTED in `#1364` (2026-05-17): the B-2 finding was a false positive, the disable was an unauthorised Tier-3 change. Full record in `docs/sprint-logs/S-AUDIT-PIPELINE-2026-05-17.md` § Addendum**; **D-1** intent layer promoted to default-on (fixes turtle_soup suppressing vwap on bybit_2 same-tick); **D-3** WAL mode enabled at startup (`src/utils/db_init.py`); **E** dead Binance code path removed from `src/main.py` (`#1360`). Sprints C / E-2 / E-3 / E-5 / F cancelled (false findings or already fixed). Key decisions: vwap risk_pct halved silently for months; `ict-git-sync.service` is the deploy mechanism (must not be disabled); `test_strategy_consumer.py` is production code. | ✅ Done 2026-05-17 (`#1360` `#1361` `#1364`) | hardening |
| **S-TRAINER-BT-1** | **Trainer-VM backtest sweep infrastructure + post-incident validation + vwap revert.** Follow-up to the PR #1358 incident. Built the missing trainer-VM venv + qashdev/btc parquet cache + ablation harness + orchestrator + runbook (`#1366`, Tier 1). Ran a 3.16-year ablation across vwap PR #1175 / #1183 / #1205 and a turtle_soup `min_sweep_buffer_bps` sweep on the trainer VM (issue #1370). Findings: PR #1175 HTF gate is the dominant winning factor (-73 R → +412 R Total R), PR #1183 (SL widening) + PR #1205 (entry threshold raise) each net-degraded performance vs V_1175_htf_only. **`#1372` reverts PR #1183 + PR #1205 to restore the empirically-best vwap config** (Tier 3, operator approval in chat, draft-then-merge). ict_scalp_5m v2 re-validated on 90 days BTCUSDT 5m: 54 trades, 59.3% win, +0.382 R expectancy, +20.6 R total — clears the PR #1156 pre-live gate. Production deploy via `pull-and-deploy` (#1374) + `restart-bot-service` (#1375); journalctl confirms new `sl_std_mult: 0.5` + ict_scalp evaluating signals. Full record: `docs/sprint-logs/S-TRAINER-BT-1.md`. | ✅ Done 2026-05-17 (`#1366` `#1372` + `#1364` for ict_scalp restore) | hardening |

> **Sprint number note:** S-067 is in flight as the silent-empty
> audit; AI traders track uses themed `S-AI-*` ids with
> sub-sprint suffixes for multi-part work.

---

## Standing / Recurring Sessions

| Type | Cadence | Cap |
|---|---|---|
| Hardening & Stability Audit | Bi-daily | 3h |
| Strategy Improvement Review | Weekly | 4h |
| Model Training & Evaluation | Weekly (HF cron) | 6h (offloaded) |

---

## Items Under Consideration (Not Yet Scheduled)

- Recurring-Session Triggers + `/roadmap` Command.
- Exchange Failover / Multi-Exchange Support.
- Deployment Automation.
- Tier 2 follow-up: live-path migration onto WS2 types.
- **Cloudflare named tunnel migration** — replace the
  ephemeral `*.trycloudflare.com` quick tunnel with a named
  tunnel at `bot.<our-domain>` (now the only viable
  stable-URL path after S-CFW-1-FU2 retired the Worker layer).
  **Prereq:** operator adds a domain to Cloudflare (zone with
  nameservers pointed at CF). When met, ~30 min sprint.
- **CFI auto-flatten promotion** — if `runtime_logs/invariant_violations.jsonl`
  stays at zero through 2026-05-17 (7-day soak from the
  alert-only enable on 2026-05-10, issue #683), file the PR that
  promotes the invariant from alert-only to auto-flatten.
- **Tunnel-URL auto-refresh** — VM-side hook that pushes the
  new `*.trycloudflare.com` URL into Vercel (via API) every
  time `setup_cloudflare_tunnel.sh` produces a new URL.
  Eliminates the operator-update step on tunnel restart.
  Smaller scope than the named-tunnel migration; useful
  in-between if the named tunnel keeps slipping.
- Per-family dataset builders for `market_features`, `setup_labels`,
  `account_context`, `review_journal`.
- `python -m ml.datasets publish` HF subcommand.
- Aggregated walk-forward.
- Per-strategy detail metrics artifact.
- Registry concurrent-writer locking.
- **WS5-B-PART-1 follow-ups:** `yfinance` adapter; `binance_offvm`
  adapter; on-disk Parquet adapter; the actual Bybit off-VM
  fetch wiring (filed under WS5-B-PART-2).
- **M11 IB/MES shadow integration (S7-IB):** Deferred from M11 — no IB credentials in scope.
  When credentials are available, wire `IB_SHADOW_ENABLED=true` per `ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md § S7`.

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`. AI-traders workstream sprint
plans live under `docs/sprint-plans/ai-traders/wsN-<slug>.md`.
Themed ids (`S-AI-WSN`, optionally `-A`/`-B`/.../`-FU` /
`-PART-N` for sub-sprints) parallel the numeric sequence.
M11 refactor sprint ids: `S-REFACTOR-SN`.

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ Done | Completed and merged |
| 🔜 Next | Immediate next sprint |
| 🔄 In Progress | Currently being executed |
| ⚠️ Reopened | Verification revealed drift |
| ⛔ Blocked / Scratched | Cannot proceed or cancelled |
| 📋 Backlog | Defined but not yet started |
