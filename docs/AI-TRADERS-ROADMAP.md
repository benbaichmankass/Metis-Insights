# AI Traders Models Roadmap

> **Status:** Master plan adopted 2026-05-10. Through S-AI-WS7-PART-6
> (shadow harness complete; both production strategies + Coordinator
> caching wired). WS5 baseline-models family closed (A → F). Next:
> WS8 monitoring + WS6 open-source models. Operator-blocked: train +
> register the WS5 baselines so the shadow harness has something
> live to score against.
>
> **2026-05-11 authority-model update:** Trainer-VM scope (`ict-trainer-vm`)
> is now autonomous-Claude per [`docs/claude/trainer-vm-mode.md`](claude/trainer-vm-mode.md).
> Operator-approval gates **only** apply at the live-VM YAML wiring
> step (adding model ids to `shadow_model_ids` in a strategy YAML);
> registry-stage promotions up to `live_approved` are autonomous.
> The "operator-blocked" status above is now Claude-blocked — once
> the trainer VM is provisioned (auto-retry loop running), Claude
> trains + registers via `scripts/ops/train_and_register_ws5_baselines.sh`
> without further operator action.
>
> **2026-05-11 WS10 close-out:** Architecture-doc enforcement is now
> live (advisory guard + pre-commit hook + weekly doc audit + Change
> log + Known Gaps in `docs/ARCHITECTURE-CANONICAL.md`). See the
> S-AI-WS10-CLOSEOUT row in the change log below.
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
| WS6 | Open-source model layer | M9 | 📋 Not started | [ws6-open-source-models.md](sprint-plans/ai-traders/ws6-open-source-models.md) |
| WS7 | Deployment tiers | M9 | 🔄 IN PROGRESS (shadow harness complete) — PART-1 (registry stage gate) + PART-2 (`with_shadow_pred` adapter) + PART-3 (vwap wiring) + PART-4 (multi-predictor + `shadow_model_ids` YAML source) + PART-5 (turtle_soup adoption) + PART-6 (Coordinator-side cache) all done 2026-05-10. **Claude-blocked**: trainer VM still pending OCI capacity; bootstrap script `train_and_register_ws5_baselines.sh` is ready to run on first boot. | [ws7-deployment-tiers.md](sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| WS8 | Monitoring and feedback loops | M9 | ✅ DONE 2026-05-10 — PART-1 (CLI inspector + stats) + PART-2 (dashboard endpoints over shadow_predictions.jsonl) + PART-3 (drift detector via KS + PSI; CLI + endpoint). Dashboard "Shadow Models" tab shipped 2026-05-11 surfaces all three. | [ws8-monitoring-feedback.md](sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| WS9 | Oracle / Hugging Face runtime split | M10 | 🔄 Continuous — trainer-VM topology shipped 2026-05-10 (S-AI-WS9). `run_training_cycle.sh` body shipped (S-AI-WS9-FU 2026-05-10). Auto-retry workflow + first-VM-up notification shipped 2026-05-11 (S-AI-WS9-AUTORETRY). Awaiting OCI A1 capacity. | [ws9-runtime-split.md](sprint-plans/ai-traders/ws9-runtime-split.md) |
| WS10 | Architecture-doc enforcement | M9 | ✅ DONE 2026-05-11 (S-AI-WS10 + S-AI-WS10-FU + S-AI-WS10-CLOSEOUT). Advisory guard + opt-in pre-commit + weekly doc audit + Change log + Known Gaps live. Upgrade-to-blocking deliberately filed (see Known Gaps in `docs/ARCHITECTURE-CANONICAL.md`). | [ws10-arch-doc-enforcement.md](sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

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
   change. **Claude-unlock (2026-05-11):** once the trainer VM is
   provisioned (tracked by the `[provision-training-vm]` auto-retry
   loop), Claude trains + registers the WS5 baselines via
   `scripts/ops/train_and_register_ws5_baselines.sh` and promotes
   them through the ladder up to `live_approved`. Adding any of
   those model ids to a strategy's `shadow_model_ids` field — the
   actual live-trading switch — remains operator-controlled.
9. **WS8 (monitoring) — ✅ DONE 2026-05-10 + 2026-05-11 dashboard.**
   PART-1 CLI (`python -m ml shadow-inspect / shadow-stats`).
   PART-2 dashboard endpoints (`/api/bot/shadow/predictions`,
   `/api/bot/shadow/stats`). PART-3 drift (`ml/shadow/drift.py`
   + `/api/bot/shadow/drift`). Dashboard "Shadow Models" tab
   (2026-05-11) renders all three with empty-state explainer
   for the pre-trainer phase.
10. WS6 (open-source model layer) — HF transformers as a `Predictor`
    class. Significant new ground; defer until the trainer VM is up
    AND real shadow predictions are flowing to confirm the WS8
    feedback loop is observable end-to-end.
11. **WS10 (architecture-doc enforcement) — ✅ DONE 2026-05-11.**
    S-AI-WS10 shipped the scaffold (checklist + PR template + advisory
    guard). S-AI-WS10-FU added the opt-in pre-commit hook + weekly
    doc audit. S-AI-WS10-CLOSEOUT (2026-05-11) refreshed the Change
    log + Known Gaps to match today's state — the close-out itself
    being the proof that the workflow catches drift.

---

## Non-negotiable rules

- Live trading safety > feature growth.
- No heavy training on the Oracle live VM (WS9).
- **No model id added to a strategy's `shadow_model_ids` YAML field
  without operator approval** (2026-05-11 clarification). This is
  the live-trading wiring step — the live `Coordinator` reads the
  YAML and loads the listed models. Registry stage promotion
  (`research_only → … → live_approved`) is autonomous-Claude on
  the trainer VM and does **not** wire anything; it's metadata.
  See [`docs/claude/trainer-vm-mode.md`](claude/trainer-vm-mode.md) § 5
  for the full step-by-step.
- AI output cannot bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code updates the architecture docs in
  the same PR.
- No auto-publishing datasets to HF (S-AI-WS3).
- No editing past `StatusEvent` entries in the registry
  (S-AI-WS4 — append-only).
- Registry promotions still require `--by` + `--reason` for
  audit (S-AI-WS4). Autonomous-Claude promotions use
  `--by=claude-trainer` with the training-summary rationale in
  `--reason`. Promotions past `advisory` (i.e. to `limited_live`
  or `live_approved`) additionally require a sprint-log entry
  under `docs/sprint-logs/S-AI-WS5-PROMOTION-*` per the trainer
  charter § 3.b.
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
| 2026-05-11 | S-AUTH-SPLIT | Authority-model clarification: trainer VM is autonomous-Claude per new `docs/claude/trainer-vm-mode.md`; operator-approval gate on model promotions now applies only at the `shadow_model_ids` YAML wiring step on the live VM, not at registry stage promotion. Sibling doc `docs/claude/vm-operator-mode.md` scoped explicitly to the live VM. CLAUDE.md surfaces the split at the top. | None on live VM behaviour. Claude can now train + register + promote autonomously once the trainer VM is up. |
| 2026-05-11 | S-AI-WS9-AUTORETRY | Cron-driven trainer-VM auto-retry workflow. `.github/workflows/provision-training-vm-auto-retry.yml` fires every 10 min, checks via OCI whether `ict-trainer-vm` exists, and dispatches `provision-training-vm.yml` if not. On first detection of `exists=true`, files a one-shot `[trainer-vm-up]` GitHub issue (operator notification). Resolves the "OCI Always Free A1 capacity is intermittent" friction without operator polling. | None — autonomous retry until the trainer VM lands. |
| 2026-05-11 | S-AI-WS5-BOOTSTRAP | New `scripts/ops/train_and_register_ws5_baselines.sh` — the trainer's "first action" once the VM is up. Trains every `baseline-*.yaml`, walks each new model id up the promotion ladder to `TARGET_STAGE` (default `shadow`, the minimum the WS7 factory will load). | None until the trainer VM is up + the operator runs the script there. |
| 2026-05-11 | S-AI-WS8-DASHBOARD | New "Shadow Models" tab in the Vercel dashboard consuming `/api/bot/shadow/{predictions,stats,drift}`. Drift verdict pill (KS + PSI), per-(model,stage) aggregate table, recent-predictions feed. Empty-state explainer points at the trainer-VM prerequisite chain so the tab is informative even before any model lands. | None — dashboard-only, additive. |
| 2026-05-11 | S-AI-WS10-CLOSEOUT | WS10 explicitly closed. Refreshed Change log + Known Gaps in `docs/ARCHITECTURE-CANONICAL.md` to reflect today's state (S-AUTH-SPLIT, S-AI-WS9-AUTORETRY, S-AI-WS5-BOOTSTRAP, S-AI-WS8-DASHBOARD plus the previously-missing 2026-05-10 follow-ups). WS10 row in this roadmap marked DONE. The close-out itself is the proof that the WS10 workflow catches drift. | None — informational. |
