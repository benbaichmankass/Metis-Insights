# AI Traders Models Roadmap

> **Status:** Master plan adopted 2026-05-10. Through
> **S-AI-WS6-PART-1**. Sprint ladder this session: WS5 baseline-models
> closed (A → F), WS7 shadow harness closed (PART-1..6, both production
> strategies wired through `with_shadow_preds` + Coordinator-side
> cache), WS8 monitoring loop closed (PART-1 inspector CLI + PART-2
> dashboard endpoints + PART-3 drift detector — KS + PSI), WS9 ships
> the two-VM topology + provisioning workflow + `run_training_cycle.sh`,
> WS10 scaffolds the architecture-doc enforcement layer (arch-doc-guard
> + opt-in pre-commit + weekly drift audit), WS6 PART-1 stands up the
> open-source model layer framework (inventory + ABC, no concrete
> integration yet). **Operator-blocked**: provision the trainer VM
> (issue #791 dispatch), train + register the WS5 baselines on the new
> VM, then promote them past `backtest_approved` so `shadow_model_ids`
> has something live to load.
>
> **AI-scope canonical doc:**
> [`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).
> **Stage contracts:** [`docs/pipeline/stage-contracts.md`](pipeline/stage-contracts.md).
> **Pipeline types:** [`src/pipeline/types.py`](../src/pipeline/types.py).
> **Data layer:** [`docs/data/`](data/) + [`ml/datasets/`](../ml/datasets/).
> **`market_raw` adapters:**
> [`docs/ml/market-raw-adapters.md`](ml/market-raw-adapters.md).
> **Training center + registry + Predictor + splitters + compare:**
> [`docs/ml/`](ml/) + [`ml/`](../ml/).
> **First specialist baseline:**
> `ml/configs/baseline-trade-outcome-{winrate,global}.yaml`.

---

## Workstreams

| WS | Title | M | Status | Sprint plan |
|---|---|---|---|---|
| WS1 | Architecture baseline | M9 | ✅ DONE 2026-05-10 (S-AI-WS1) | [ws1-architecture-baseline.md](sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| WS2 | Canonical trade pipeline | M9 | ✅ DONE 2026-05-10 (S-AI-WS2) | [ws2-canonical-pipeline.md](sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| WS3 | Data foundation | M10 | ✅ DONE 2026-05-10 (S-AI-WS3) | [ws3-data-foundation.md](sprint-plans/ai-traders/ws3-data-foundation.md) |
| WS4 | Training center | M9 | ✅ DONE 2026-05-10 (S-AI-WS4 + S-AI-WS4-FU) | [ws4-training-center.md](sprint-plans/ai-traders/ws4-training-center.md) + [ws4-followups.md](sprint-plans/ai-traders/ws4-followups.md) |
| WS5 | Baseline models | M9 | ✅ DONE 2026-05-10 — A + B-PART-1 + B-PART-2 (PR 2A + 2B) + C (+ C-FU) + D + E + F all closed | [ws5-baseline-models.md](sprint-plans/ai-traders/ws5-baseline-models.md) |
| WS6 | Open-source model layer | M9 | 🔄 IN PROGRESS — PART-1 (inventory doc + `ExternalPredictor` ABC + `ProviderError`) done 2026-05-10; PART-2 (first concrete model) gated on a real use case + the approval criteria in `docs/architecture/model-inventory.md` | [ws6-open-source-models.md](sprint-plans/ai-traders/ws6-open-source-models.md) |
| WS7 | Deployment tiers | M9 | ✅ DONE 2026-05-10 — PART-1 (registry stage gate) + PART-2 (`with_shadow_pred` adapter) + PART-3 (vwap wiring) + PART-4 (multi-predictor + `shadow_model_ids` YAML source) + PART-5 (turtle_soup adoption) + PART-6 (Coordinator-side cache) + FU (audit-log rotation timer). **Operator-blocked**: train + register the WS5 baselines on the trainer VM so `shadow_model_ids` has something to load. | [ws7-deployment-tiers.md](sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| WS8 | Monitoring and feedback loops | M9 | ✅ DONE 2026-05-10 — PART-1 (inspector CLI) + PART-2 (dashboard `/api/bot/shadow/{predictions,stats}`) + PART-3 (drift detector: KS + PSI + `compute_drift` + `/api/bot/shadow/drift`). Filed: external-reference drift, Telegram alerter, per-feature drift. | [ws8-monitoring-feedback.md](sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| WS9 | Oracle / Hugging Face runtime split | M10 | 🔄 IN PROGRESS — two-VM topology shipped (provisioning workflow + cloud-init + runbook + `run_training_cycle.sh` body). **Operator-blocked**: dispatch the workflow to create the trainer VM (issue #791 stuck); cross-VM `trade_journal.db` sync filed. | [ws9-runtime-split.md](sprint-plans/ai-traders/ws9-runtime-split.md) |
| WS10 | Architecture-doc enforcement | M9 | ✅ DONE 2026-05-10 — arch-doc-guard CI workflow + PR template + checklist doc + change log + Known-gaps sections; plus opt-in pre-commit hook + weekly architecture-doc drift audit (`doc-audit-weekly.yml`). | [ws10-arch-doc-enforcement.md](sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

---

## Recommended implementation order

1. WS1–WS4 + WS4-FU — ✅ done.
2. WS5-A (outcome probability) — ✅ done.
3. WS5-B-PART-1 (`market_raw` adapter framework) — ✅ done 2026-05-10.
4. WS5-B-PART-2 PR 2A (Bybit off-VM fetch wiring via ccxt) — ✅
   done 2026-05-10.
5. WS5-B-PART-2 PR 2B (`market_features` family + 3-class regime
   classifier + multiclass evaluator) — ✅ done 2026-05-10.
6. WS5-C (`setup_labels` family + setup-quality R-multiple scorer
   + numeric-mean trainer + training-session workflow doc) — ✅
   done 2026-05-10. WS5-C-FU also done.
7. WS5-D..F (remaining baselines) — ✅ all done 2026-05-10.
8. **WS7 (shadow mode) — ✅ harness complete 2026-05-10.**
   PART-1 (registry stage gate) + PART-2 (`with_shadow_pred`
   adapter) + PART-3 (vwap wiring) + PART-4 (multi-predictor +
   `shadow_model_ids` YAML source + factory stage gate) + PART-5
   (turtle_soup adoption) + PART-6 (Coordinator-side cache) all
   shipped. Both production strategies (vwap + turtle_soup) can
   load N concurrent shadow predictors from YAML without any code
   change. **Operator unlock**: train + register the WS5 baselines
   to give the harness something to load.
9. **WS8 (monitoring) — ✅ closed 2026-05-10.**
   PART-1 (CLI inspector `python -m ml shadow-inspect` /
   `shadow-stats` over `runtime_logs/shadow_predictions.jsonl`),
   PART-2 (dashboard `/api/bot/shadow/{predictions,stats}`),
   PART-3 (drift detector `python -m ml shadow-drift` +
   `/api/bot/shadow/drift` — KS + PSI). External-reference drift
   variant + Telegram alerter filed.
10. **WS10 (architecture-doc enforcement) — ✅ closed 2026-05-10.**
    CI-level `arch-doc-guard` + PR template + `ARCHITECTURE-
    CHANGE-CHECKLIST.md` + Change log + Known gaps + opt-in
    pre-commit hook + weekly drift audit (`doc-audit-weekly.yml`).
11. **WS9 (Oracle / HF runtime split) — 🔄 in progress.** Two-VM
    topology + provisioning workflow + cloud-init + runbook +
    `run_training_cycle.sh` body all shipped. Operator dispatches
    `provision-training-vm` to bring up the trainer VM.
12. **WS6 (open-source model layer) — 🔄 PART-1 done.** Inventory
    + ABC + ProviderError shipped (no concrete model). PART-2 (first
    concrete integration) blocked on a real use case that justifies
    the approval criteria in `docs/architecture/model-inventory.md`.

---

## Non-negotiable rules

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
  (S-AI-WS4 — append-only).
- No promoting to `live-approved` or `champion` without operator
  approval recorded in `--by` + `--reason` (S-AI-WS4).
- No outcome columns as features against `won` on
  `trade_outcomes` (S-AI-WS5-A).
- **No `ICT_OFFVM_BUILD_HOST=1` on the Oracle live VM**
  (S-AI-WS5-B-PART-1).
- No forward-window or label columns as features when targeting
  `regime_label` against the `market_features` family
  (S-AI-WS5-B-PART-2 PR 2B). The trainer enforces this at
  `fit(...)` time.
- No loading a model into shadow mode below stage `shadow`
  (S-AI-WS7-PART-4). `ml.shadow.factory.LIVE_INFLUENCE_STAGES`
  enforces this at resolve time — `research_only`, `candidate`,
  and `backtest_approved` are refused with a logged skip.
  Operator must `promote_stage` past `backtest_approved` before
  any model can be wired, even as a side-channel observer.

---

## Change log

| Date | Sprint | Change | Operator impact |
|---|---|---|---|
| 2026-05-10 | S-AI-ROADMAP | Master plan adopted. | None. |
| 2026-05-10 | S-AI-WS1 | WS1: AI-platform doc. | None. |
| 2026-05-10 | S-AI-WS2 | WS2: stage names locked; typed schemas. | None. |
| 2026-05-10 | S-AI-WS3 | WS3: dataset framework + `backtest_results`. | None. |
| 2026-05-10 | S-AI-WS4 | WS4: training center. | None. |
| 2026-05-10 | S-AI-WS5-A | WS5-A: outcome probability + `trade_outcomes`. | None. |
| 2026-05-10 | S-AI-WS4-FU | WS4 follow-ups: Predictor + splitters + `compare` + global-only baseline. | None. |
| 2026-05-10 | S-AI-WS5-B-PART-1 | WS5-B Part 1: `market_raw` multi-source adapter framework (CSV adapter live; Bybit off-VM scaffold env-gated; fetch wiring filed for operator). New non-negotiable: no `ICT_OFFVM_BUILD_HOST=1` on the live VM. | None — additive; Bybit shell raises NotImplementedError until operator wires the fetch. |
| 2026-05-10 | S-AI-WS5-B-PART-2 PR 2A | WS5-B Part 2A: Bybit off-VM `_fetch_bars` wired via ccxt's `fetch_ohlcv`; paginated `since` cursor; canonical-row normalisation; CI mocks the exchange. Builder framework auto-forwards `symbol_scope` / `timeframe` into `iter_rows` kwargs. | None on the live VM (env-gate retained). |
| 2026-05-10 | S-AI-WS5-B-PART-2 PR 2B | WS5-B Part 2B: `market_features` family (3-class regime label, forward-window leakage discipline) + `RegimeClassifierTrainer` (per-bucket modal) + `MulticlassPredictor` + `MulticlassClassificationEvaluator` + `baseline-regime-classifier.yaml` manifest. New non-negotiable: no forward-window / label columns as features against `regime_label`. | None — additive; research-only baseline. |
| 2026-05-10 | S-AI-WS5-C | WS5-C: `setup_labels` family (CLOSED setup-tagged trades + r_multiple) + `PerStrategyWinRateTrainer` extended with `target_kind: numeric_mean` + `baseline-setup-quality.yaml` manifest (regression). Architecture-canonical doc gains an explicit "AI-traders training workflow" section anchored on the `/health-review` skill's per-trade decision grades as labelled feedstock. | None — additive; research-only baseline. Future training sessions follow the documented workflow. |
| 2026-05-10 | S-AI-WS5-C-FU | WS5-C follow-ups: rounded out the setup-quality regression surface (post-PR refinements). | None — additive. |
| 2026-05-10 | S-AI-WS5-D | WS5-D: r-multiple regression baseline. | None — additive; research-only. |
| 2026-05-10 | S-AI-WS5-E | WS5-E: slippage / fill-quality baseline. | None — additive; research-only. |
| 2026-05-10 | S-AI-WS5-F | WS5-F: closes the WS5 baseline-models family. | None — additive; research-only. |
| 2026-05-10 | S-AI-WS7-PART-1 | WS7-PART-1: model registry gains `target_deployment_stage` + `promote_stage()` + the canonical stage ladder (`research_only` → `candidate` → `backtest_approved` → `shadow` → `advisory` → `limited_live` → `live_approved`). Append-only `StatusEvent` history; promotion requires `--by` + `--reason`. | None on the live VM (registry not yet read by the runtime). |
| 2026-05-10 | S-AI-WS7-PART-2 | WS7-PART-2: `src/runtime/shadow_adapter.py::with_shadow_pred(decision, *, predictor, feature_row, logger)` helper. Pass-through on `None`; per-call `try/except` around `predictor.predict(...)` so a misbehaving model can't crash the tick. `ShadowPredictor`-only enforcement (raw `Predictor` rejected). | None — helper exists but no strategy wires it yet. |
| 2026-05-10 | S-AI-WS7-PART-3 | WS7-PART-3: vwap is the first production strategy threaded through `with_shadow_pred`. Decision returned byte-for-byte; new `_build_shadow_feature_row(package)` projects a signal-time feature dict (outcome columns excluded). Shadow predictor source still test-injection-only. | None on the live VM (no operator opt-in carries no predictor). |
| 2026-05-10 | S-AI-WS7-PART-4 | WS7-PART-4: multi-predictor concurrent + config-driven source. New `with_shadow_preds` plural helper (per-predictor failure isolation). New `ml/shadow/factory.py` resolves `shadow_model_ids` against the registry with a stage gate (`{shadow, advisory, limited_live, live_approved}` allowed; `{research_only, candidate, backtest_approved}` refused). New optional `shadow_model_ids: []` field on the vwap YAML block. Production rollout is a YAML edit, not a code change. | None unless operator opts in by setting `shadow_model_ids` to a non-empty list. |
| 2026-05-10 | S-AI-WS7-PART-5 | WS7-PART-5: turtle_soup adoption — same pattern as PART-3+4 ported to the second production strategy. Strategy-specific feature row exposes `atr` + `body_to_range`. WS7 acceptance criterion now satisfied for both production strategies. | None unless operator opts in. |
| 2026-05-10 | S-AI-WS7-PART-6 | WS7-PART-6: Coordinator-side cache. `Coordinator._shadow_predictors_cache` populated lazily per strategy; dispatcher injects the cached list as `cfg["_shadow_predictors"]` (resolution mode 1 in both strategies). `reload_strategy_config` clears the cache. Factory cost moves from O(ticks) to O(reloads). | None — cache is a transparent perf optimisation. |
| 2026-05-10 | S-AI-WS8-PART-1 | WS8-PART-1: shadow-prediction inspector. `ml/shadow/inspector.py` ships streaming JSONL reader (`iter_records`), filter helpers, `aggregate(records)` per-`(model_id, stage)` summariser, text formatters. New CLI subcommands `python -m ml shadow-inspect` + `shadow-stats`. Pure-logic; reused by PART-2. | None — operator-facing diagnostics. |
| 2026-05-10 | S-AI-WS8-PART-2 | WS8-PART-2: dashboard endpoints `GET /api/bot/shadow/predictions` + `GET /api/bot/shadow/stats`. Same filter surface as the CLI; envelope response (`log_present` + `records` + `count`). FastAPI `Query` validation; `SHADOW_PREDICTIONS_LOG` env override. | None until next deploy. |
| 2026-05-10 | S-AI-WS8-PART-3 | WS8-PART-3: drift detector. `ml/shadow/drift.py` ships `summarize` + `ks_statistic` + `psi` + `compute_drift` (`DriftReport` with `overall_verdict` ∈ `{no_change, minor, moderate, significant, insufficient_data}`). New `python -m ml shadow-drift` CLI + `GET /api/bot/shadow/drift` endpoint. Window-over-window self-comparison; no external reference required. Pure stdlib. | None until next deploy. |
| 2026-05-10 | S-AI-WS7-FU | WS7-FU: shadow audit-log rotation. `scripts/ops/rotate_shadow_log.py` + `deploy/ict-shadow-log-rotate.{service,timer}` (DISABLED BY DEFAULT — operator opts in). Size OR age threshold; atomic rename; optional `--gzip`. | None — opt-in timer. |
| 2026-05-10 | S-AI-WS9 | WS9: two-VM topology + provisioning. `scripts/ops/provision_training_vm.py` (OCI SDK, idempotent, quota-aware) + `.github/workflows/provision-training-vm.yml` (dispatch + issue-trigger) + `deploy/training-vm-cloud-init.yaml` + `docs/runbooks/training-vm.md`. Always Free Ampere A1; same compartment/subnet/SSH-key as the live VM; trainer service installed DISABLED. New non-negotiable enforced by topology. | Operator dispatches the workflow once to bring the VM up. |
| 2026-05-10 | S-AI-WS9-FU | WS9-FU: `scripts/ops/run_training_cycle.sh` — body of `ict-trainer.service`. `Type=oneshot` cycle: pull main, venv, train every manifest, JSONL event log. First-failure short-circuit. | Operator opts in via `systemctl enable --now ict-trainer.service`. |
| 2026-05-10 | S-AI-WS10 | WS10: architecture-doc enforcement scaffold. `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md` + `.github/PULL_REQUEST_TEMPLATE.md` + `scripts/arch_doc_guard.py` + `.github/workflows/arch-doc-guard.yml` (advisory `::warning`, always exits 0). New Change log + Known gaps sections on `ARCHITECTURE-CANONICAL.md`. New non-negotiable: no model loaded into shadow mode below stage `shadow`. | None — informational. |
| 2026-05-10 | S-AI-WS10-FU (pre-commit) | WS10-FU: opt-in pre-commit hook. `scripts/git-hooks/pre-commit` mirrors the CI guard locally and BLOCKS the commit on warning (CI stays advisory; local stronger). Installed by `bash scripts/install-hooks.sh`. Bypass: `git commit --no-verify`. | None — opt-in. |
| 2026-05-10 | S-AI-WS10-FU (doc-audit) | WS10-FU: periodic architecture-doc audit. `scripts/ops/audit_verification_checklist.py` + `.github/workflows/doc-audit-weekly.yml` (Mon 12:00 UTC + `workflow_dispatch` + `issues.opened` labelled `doc-audit-now`). Files `doc-drift`-labelled issue when checklist paths go stale (idempotent search-before-create). | None — informational. |
| 2026-05-10 | S-AI-WS6-PART-1 | WS6-PART-1: open-source model layer scaffolding. `docs/architecture/model-inventory.md` (rules + approval criteria + candidate models by use case) + `ml/predictors/external.py` (`ExternalPredictor` ABC + `ProviderError`). Provider-agnostic; no HF / vendor SDK pulled in. PART-2 (first concrete integration) blocked on a real use case. | None — framework only. |
| 2026-05-10 | S-AI-DOCS-SWEEP | End-of-session documentation sweep. Reconciles ROADMAP, AI-TRADERS-ROADMAP, and ARCHITECTURE-CANONICAL against the 17 sprints that landed today. Doc-only PR. | None — informational. |
