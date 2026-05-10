# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-10 (through S-AI-WS6-PART-1; WS5 closed A→F; WS7 closed PART-1..6; WS8 closed PART-1..3; WS9 + run-cycle FU shipped, trainer VM provisioning queued on operator; WS10 + 2 follow-ups (pre-commit, doc-audit) shipped).
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

---

## Core Principles

1. **Lean solutions.**
2. **Stability first.**
3. **Profitability focus.**

---

## M0..M10 Milestone Roadmap

| Milestone | Type | Focus | Status |
|---|---|---|---|
| **M0–M5** | auto-claude | Foundation → Strategy testing | ✅ CLOSED |
| **M6** | auto-claude | Web app UI | 🔄 IN PROGRESS (dashboard repo) |
| **M7** | pm-sprint | Strategy review gate | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | 🔄 IN PROGRESS — WS1+WS2+WS4+WS4-FU+WS5(A→F)+WS7(PART-1..6)+WS8(PART-1..3)+WS10+WS6-PART-1 closed 2026-05-10. WS6 PART-2 (first concrete external model) blocked on use case. |
| **M10** | auto-claude | HF / data pipeline | 🔄 IN PROGRESS — WS3 closed; WS5-B adds `market_raw` + `market_features`; WS5-C..F close baselines; WS9 ships two-VM topology + provisioning workflow + `run_training_cycle.sh` (operator triggers provisioning when ready). |

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
| **WS7** | Deployment tiers | 🔄 IN PROGRESS (PART-1..PART-6 done — shadow harness complete; model training + audit log rotation queued) | [ws7-deployment-tiers.md](docs/sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| **WS8** | Monitoring and feedback loops | 🔄 IN PROGRESS (PART-1 inspector CLI + PART-2 dashboard endpoints + PART-3 drift detector shipped; external-reference variant + alerting queued) | [ws8-monitoring-feedback.md](docs/sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| **WS9** | Oracle / Hugging Face runtime split | 🔄 IN PROGRESS — two-VM topology shipped (training-center VM provisioning workflow); cross-VM data flow + training-cycle script queued | [ws9-runtime-split.md](docs/sprint-plans/ai-traders/ws9-runtime-split.md) |
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
| S-AI-ROADMAP | AI traders models roadmap adopted | ✅ Done (`#693` `1eb59f6`) | M9, M10 |
| S-AI-WS1 | Architecture baseline | ✅ Done (`#694` `f453b89`) | M9 |
| S-AI-WS2 | Canonical trade pipeline | ✅ Done (`#701` `42a1e6f`) | M9 |
| S-AI-WS3 | Data foundation | ✅ Done (`#704` `60807f4`) | M10 |
| S-AI-WS4 | Training center | ✅ Done (`#719` `b910fd3`) | M9 |
| S-AI-WS5-A | Outcome probability baseline | ✅ Done (`#730` `6a9f5a0`) | M9 |
| S-AI-WS4-FU | WS4 follow-ups | ✅ Done (`#732` `8a69e97`) | M9 |
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
| **S-AI-WS8-PART-3** | **WS8-PART-3 — Shadow-prediction drift detector.** Closes the WS8 monitoring loop. New `ml/shadow/drift.py` ships `summarize` + `ks_statistic` + `psi` + plain-English verdict buckets + a compound `compute_drift` returning a `DriftReport`. Pure stdlib (no numpy / scipy). New CLI subcommand `python -m ml shadow-drift --model-id X [...]` + dashboard route `GET /api/bot/shadow/drift?...`. Window-over-window self-comparison (default 30-day reference vs 7-day current, non-overlapping, anchored at now) — meaningful without an external reference distribution. `verdict` ∈ `{no_change, minor, moderate, significant, insufficient_data}`. PSI thresholds match industry convention (<0.1 / 0.1–0.25 / >0.25); KS sensitive to any shape change. 27 new tests (23 unit + 4 CLI). Filed: external-reference variant (registry-stored training-set histogram), Telegram alerter, per-feature drift, Wasserstein, multi-stage comparison. Logged in `docs/sprint-logs/S-AI-WS8-PART-3.md`. | ✅ Done 2026-05-10 (`#797`) | M9 |
| **S-AI-WS9-FU** (run-cycle) | **WS9-FU — `run_training_cycle.sh`.** Body of `ict-trainer.service`. `Type=oneshot` bash + inline Python: pulls latest `main`, lazy-provisions venv, trains every manifest under `ml/configs/` sorted alpha (overridable via `TRAINING_MANIFESTS` env), short-circuits on first failure, JSONL events to `runtime_logs/training_cycle.jsonl`. After this PR the operator can `systemctl enable --now ict-trainer.service` on the trainer VM. Cadence (timer / cron) still operator decision; cross-VM DB sync still filed. Logged in `docs/sprint-logs/S-AI-WS9-FU-run-cycle.md`. | ✅ Done 2026-05-10 (`#793`) | M9/M10 |
| **S-AI-WS7-FU** (log rotation) | **WS7-FU — Shadow-prediction audit log rotation.** Resolves long-standing follow-up. New `scripts/ops/rotate_shadow_log.py` (pure-stdlib, size OR age threshold, atomic `os.replace`, date-suffixed filenames with numeric collision suffix, optional `--gzip`, always exits 0). New `deploy/ict-shadow-log-rotate.{service,timer}` — DISABLED BY DEFAULT, operator enables via `systemctl enable --now ict-shadow-log-rotate.timer`. Daily timer with 15-minute randomised delay. 13 unit tests. Filed: `--keep-days N` retention, generic rotation for other audit logs, auto-install, batch-gzip catch-up. Logged in `docs/sprint-logs/S-AI-WS7-FU-shadow-log-rotation.md`. | ✅ Done 2026-05-10 (`#795`) | M9 |
| **S-AI-WS10-FU** (pre-commit) | **WS10-FU — Opt-in pre-commit hook for arch-doc-guard.** Stronger local enforcement on top of the CI advisory: hook blocks the commit (exit 1) when high-impact paths are staged without arch-doc updates. Opt-in via `bash scripts/install-hooks.sh` (idempotent symlink installer); bypass via `git commit --no-verify`. Auto-discovers hooks under `scripts/git-hooks/`. `ruff.toml` now excludes `scripts/git-hooks/` (bash files would otherwise be lexed as Python). 6 integration tests against synthetic repos. Filed: pre-push hook, commit-msg sprint-tag enforcer, auto-install via make. Logged in `docs/sprint-logs/S-AI-WS10-FU-pre-commit.md`. | ✅ Done 2026-05-10 (`#798`) | M9 |
| **S-AI-WS10-FU** (doc-audit) | **WS10-FU — Periodic architecture-doc audit.** Catches drift the per-PR `arch-doc-guard` misses. New `scripts/ops/audit_verification_checklist.py` parses `ARCHITECTURE-CANONICAL.md` § Verification Checklist; resolves the first backtick-quoted path on each `[x]` line; reports missing paths as JSON. New `.github/workflows/doc-audit-weekly.yml` with three triggers (weekly Monday 12:00 UTC cron + `workflow_dispatch` + `issues.opened` labelled `doc-audit-now`). On drift, files a day-stamped `doc-drift`-labelled issue (idempotent search-before-create). New `doc-audit-now` + `doc-drift` labels. 12 tests including a live-repo smoke. Filed: change-log freshness audit, cross-doc audit, auto-PR-to-fix, Slack/Telegram alerter. Logged in `docs/sprint-logs/S-AI-WS10-FU-doc-audit.md`. | ✅ Done 2026-05-10 (`#799`) | M9 |
| **S-AI-WS6-PART-1** | **WS6-PART-1 — Open-source model layer scaffolding.** Stand up the WS6 framework without committing to any specific model. New `docs/architecture/model-inventory.md` documents non-negotiable rules + approval criteria (use case, measurable gain, latency, cost, rollback, provider lock-in) + candidate models by use case (text / embedding / tabular-time-series / reasoning). New `ml/predictors/external.py` ships `ExternalPredictor` ABC + `ProviderError` exception (provider-agnostic: HF, vLLM, local-Ollama, vendor APIs all conform). Re-exported from `ml.predictors`. 11 tests including drop-in composition with the WS7 `ShadowPredictor` + `with_shadow_preds` harness. No HF / vendor SDK pulled in. Filed: WS6-PART-2 first concrete integration, trainer/loader for HF pretrained, PEFT/LoRA workflow, latency/cost benchmarking. Logged in `docs/sprint-logs/S-AI-WS6-PART-1.md`. | ✅ Done 2026-05-10 (`#800`) | M9 |
| **S-AI-DOCS-SWEEP** | **End-of-session documentation sweep.** Reconciles `ROADMAP.md`, `docs/AI-TRADERS-ROADMAP.md`, and `docs/ARCHITECTURE-CANONICAL.md` against the 17 sprints that landed during the 2026-05-10 session (WS5-D..F + C-FU, WS7-PART-1..6, WS8-PART-1..3, WS9 + run-cycle FU, WS7-FU log rotation, WS10 + 2 follow-ups, WS6-PART-1). Updates workstream status rows, recommended-order section, change-log tables, and stale header markers. No code; doc-only PR. | ✅ Done 2026-05-10 (this PR) | M9 |
| **S-CFW-1** | **Cloudflare Worker proxy.** Stable `*.workers.dev` hostname fronting `/api/*` to the VM. **RETIRED 2026-05-10 in S-CFW-1-FU2** — Worker deployed cleanly but its outbound `fetch()` to a raw IPv4 host is rejected by Cloudflare with error 1003. Logged in `docs/sprint-logs/S-CFW-1-cloudflare-worker.md`. | 🪦 RETIRED 2026-05-10 (`#735`) | infra |
| **S-CFW-1-FU** | **cf-worker GitHub-Actions deploy.** `cf-worker-deploy` workflow that runs `wrangler deploy` from CI. Logged in `docs/sprint-logs/S-CFW-1-FU-gha-deploy.md`. **Workflow + label remain in the repo as a recipe;** unused now that the Worker layer is retired (S-CFW-1-FU2). | 🪦 RETIRED 2026-05-10 (`#740`) | infra |
| **S-CFW-1-FU2** | **Worker retired + tunnel verified.** Empirically retired the cf-worker layer after the deployed Worker hit Cloudflare error 1003 on raw-IP `fetch()`. Corrected the wrong claim in `docs/audit/vercel-edge-vs-cf-worker.md` (Workers do NOT allow raw-IPv4 targets — only DNS hostnames). Extended `pull_logs.sh` and verified the live `*.trycloudflare.com` URL (`planners-lbs-blind-trainer.trycloudflare.com`) — same as the 2026-05-10 wrap-up, so the dashboard's existing Vercel rewrite remains healthy. Logged in `docs/sprint-logs/S-CFW-1-FU2-worker-retired.md`. | ✅ Done 2026-05-10 | infra |

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

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`. AI-traders workstream sprint
plans live under `docs/sprint-plans/ai-traders/wsN-<slug>.md`.
Themed ids (`S-AI-WSN`, optionally `-A`/`-B`/.../`-FU` /
`-PART-N` for sub-sprints) parallel the numeric sequence.

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
