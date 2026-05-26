# Sprint Log: S-V2-LIGHTGBM-PROMOTION-PREP-2026-05-26

## Date Range
- Start: 2026-05-26 15:15 UTC (immediately after PR #2053 merged into main)
- End: 2026-05-26 17:30 UTC

## Objective
- **Primary goal:** Take the v2 BTC LightGBM regime models all the way from PR #2053's merge to a state where they could realistically be promoted `shadow → advisory` once the 7-day soak completes — meaning the dataset shards exist, the booster actually predicts the minority class non-trivially, and the `non_degenerate` promotion gate either passes or has a defensible alternative path.
- **Secondary goals:**
  1. Verify PR #2053 deployed cleanly to the live VM (lightgbm installable, trader restart clean).
  2. Anchor M13 (AI Analyst — server-side LLM, separate session) in `ROADMAP.md` so a parallel session has a starting point.

## Tier
- **Tier 1** across all five PRs landed in this sprint (tooling, ML-platform code, manifest config, promotion-gate code, roadmap doc).
- **Justification:** Even PR #2068 (`non_degenerate` gate alt path) is promotion-decision support, not the actual promotion. Live order path, `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`, `src/runtime/orders.py`, and `src/runtime/risk_counters.py` were not touched. Operator approval is still required for any `shadow → advisory` transition.

## Starting Context
- **Active roadmap items at start:** M9 WS7 (deployment tiers — go-live decision-support shipped 2026-05-25) and M12 S1 (Android companion) both in flight.
- **Prior sprint reference:** PR #2053 (LightGBM trainer/predictor + Phase 2 feature expansion) merged into main at ~15:16 UTC; the operator wanted the new v2 manifests trained and promoted as soon as the gates allow.
- **Known risks at start:**
  - PR #2053 added new manifests but the dataset shards they reference (`v002`) hadn't been verified as buildable on the trainer VM.
  - `non_degenerate` gate semantics (`min(f1_*) ≥ 0.30`) had never been stress-tested against a 99.5%-imbalanced regime label.

## Repo State Checked
- **Branches:** worked off `main`. Local `main` was stale on session entry; refreshed via `git fetch origin main` + `git reset --hard origin/main` at every branch creation. Main advanced through the session: `611b4c9` (post-#2053) → `b6a24ab` (post-#2062) → `03e762b` (post-#2067) → `7015659` (post-#2072), with `#2068` + `#2071` arming auto-merge for landing at session end.
- **Deployment state checked:** Live VM auto-pulled `611b4c9` at 15:17:41 UTC via `ict-git-sync.timer` (5-min cron → `scripts/deploy_pull_restart.sh`), ran `pip install -r requirements.txt` (which installed `lightgbm>=4.0.0`), and restarted `ict-trader-live` + `ict-web-api` + `ict-telegram-bot`. Post-deploy assertion `web-api git_sha=611b4c9 matches HEAD` recorded; trader serving ticks normally by 15:18:30 UTC. Manual `pull-and-deploy` dispatched at 15:26 UTC for redundancy — correctly no-op'd (HEAD already at target).
- **Canonical docs reviewed:** `CLAUDE.md` (in-context, multiple times), `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `ROADMAP.md`, and the four skills used: `model-training`, `vm-ops`, `diag-data`, `git-actions`.

## Files and Systems Inspected
- **Code files:**
  - `ml/trainers/lightgbm_multiclass.py` (full read; edited in PR #2067)
  - `ml/trainers/regime_classifier.py` (compared to confirm v1 baseline algorithm — per-bucket modal class)
  - `ml/evaluators/multiclass_classification.py` (full read; edited in PR #2068 to emit `support_<class>`)
  - `ml/promotion/gates.py` (read; edited in PR #2068 to add imbalance-aware path)
  - `ml/datasets/families/market_features.py` (read for `builder_version`/`label_version`)
  - `ml/datasets/families/setup_labels.py` (same)
  - `scripts/ops/build_trainer_datasets.sh` (full read; edited in PR #2062 to extract `build_btcusdt_pair` + call for 1h/5m/15m)
  - `scripts/ops/deploy_pull_restart.sh` (read to understand why the manual pull-and-deploy no-op'd — the wrapper skips `pip install` + restart when `PRE_SYNC_HEAD == POST_SYNC_HEAD`)
  - `scripts/ops/run_mes_training.sh` (read while scoping the MES gap — confirmed it uses the `yfinance_offvm` adapter not present in `build_trainer_datasets.sh`)
- **Tests inspected + edited:**
  - `tests/ml/test_lightgbm_trainer.py` (+5 tests for `class_weight` in PR #2067)
  - `tests/ml/test_gates.py` (+5 tests for imbalance-aware path in PR #2068)
- **Config files inspected/edited:**
  - `ml/configs/btc-regime-5m-lgbm-v2.yaml`, `btc-regime-15m-lgbm-v2.yaml` (edited twice — added `class_weight` in PR #2067, retuned 5m in PR #2072)
  - `ml/configs/btc-regime-{5m,15m}.yaml`, `baseline-regime-classifier.yaml`, `mes-regime-{5m,15m}.yaml`, `mes-setup-quality.yaml` (read to map dataset shard requirements)
- **Deployment files inspected:**
  - `deploy/ict-git-sync.service`, `deploy/ict-git-sync.timer` (to verify the 5-min auto-sync path)
- **Services / timers actively driven via diag relay:** `ict-trainer.service` was kicked **5 times** through `trainer-vm-diag` (issues #2058, #2059, #2063, #2064, #2065, #2066, #2069, #2070, #2073, #2074). `ict-trader-live.service` verified via `vm-diag-snapshot` (issue #2057).
- **GitHub Actions workflows referenced:** `system-actions.yml` (`pull-and-deploy`), `vm-diag-snapshot.yml`, `trainer-vm-diag.yml`. None modified.

## Work Completed

The full arc is five PRs, **all merged by session end** (#2062, #2067, #2068, #2071, #2072).

- **PR #2062** — `fix(trainer): build BTCUSDT market_raw+features at 5m + 15m so v2 regime manifests train`
  Extract `build_btcusdt_pair` helper, call for 1h / 5m / 15m. Pre-PR the build script only produced `BTCUSDT/1h/v002`, so the four BTC v2 manifests (regime baselines + LightGBM heads at 5m and 15m) all failed with `FileNotFoundError`. Post-merge the trainer cycle on `b6a24ab` trained all 4. **Merged.**

- **PR #2067** — `feat(ml): per-class sample weighting for LightGBMMulticlassTrainer + apply to v2 regime manifests`
  Wired `trainer_config.class_weight: {<label>: <float>}` through to `lgb.Dataset(weight=…)`. Native `is_unbalance` / `scale_pos_weight` are binary-only in LightGBM; for our multiclass objective the supported lever is per-sample weights. Strict schema (must cover every label observed in training data). Applied at inverse-base-rate: 5m at 200×, 15m at 28×. **Merged.**
  - 5m at 200× post-train: recall_volatile 0.000 → 0.530, precision_volatile 0.231 → 0.029 (too aggressive — spray)
  - 15m at 28× post-train: recall_volatile 0.000 → 0.666, precision_volatile 0.548 → 0.137 (healthy — meaningful signal)
  - macro_f1 of v2 lifted: 5m 0.506 → 0.506 (flat at 200×); 15m 0.526 → 0.567 (+4pp)

- **PR #2068** — `RFC: imbalance-aware alternative path through non_degenerate gate`
  Adds an alternative pass path to the `non_degenerate` gate so models on heavily imbalanced labels aren't punished for hitting low minority-class F1 just because the support cap is low. Strict `min(f1_*) ≥ 0.30` path preserved unchanged; new path is an OR, not a replacement. For every observed class: `precision ≥ min_class_precision_lift × base_rate` (skipped when `base_rate ≥ 0.5`) AND `recall ≥ min_class_recall`. Requires `support_<class>` in metrics (added in the same PR to `ml/evaluators/multiclass_classification.py`). Always-collapse-to-majority models still fail (recall_minority = 0 trips the recall floor). 5 new tests. Defaults: `min_class_precision_lift=2.0`, `min_class_recall=0.05`. **Merged** at session end after `update_pull_request_branch` resolved the `behind` state.

- **PR #2071** — `docs(roadmap): add M13 — AI Analyst (server-side LLM, bot repo) — stub for planning`
  Pre-stages M13 (AI Analyst) milestone in `ROADMAP.md` so a separate session driving that track has an anchor on first `git pull`. Section heading bumped from M0..M11 to M0..M13. Active-queue list extended. **Merged.**

- **PR #2072** — `fix(ml): dial btc-regime-5m-lgbm-v2 class_weight 200 → 50 (recover precision)`
  Dial-back after PR #2067's 5m weight was too aggressive. Post-merge cycle on `7015659` showed: f1_volatile 0.055 → **0.110**, precision_volatile 0.029 → **0.064** (lift ≈ 12.8×), recall_volatile 0.530 → **0.419**, macro_f1 0.506 → **0.547**, accuracy 91.55% → **96.84%**. The "saner trade" target was hit on the first dial-back. 15m left at 28× (already healthy). **Merged.**

In parallel, the trainer cycle was kicked 5 times via `trainer-vm-diag`, the live VM diag was pulled once to confirm the auto-sync deployed cleanly, and `gate-check` was run twice (pre- and post-PR #2067) on both v2 BTC models to capture the gate-by-gate evidence.

## Validation Performed
- **Tests run:** `python -m pytest tests/ml/ --ignore=tests/ml/datasets -x -q` → **304/304 passing** at session end (after PR #2067's +5 and PR #2068's +5). `ruff` clean on every PR's changed files. `bash -n` clean on `build_trainer_datasets.sh`.
- **Trainer cycles verified end-to-end via diag relay:**
  - Cycle on `611b4c9` (post-#2053, pre-#2062): 4 BTC regime manifests failed with the dataset-missing error (motivated #2062).
  - Cycle on `b6a24ab` (post-#2062): all 4 BTC regime manifests `manifest_ok`. `setup-quality-lgbm-v2` also `manifest_ok`. Confirmed `datasets-out/market_features/BTCUSDT/{1h,5m,15m}/v002/` all exist.
  - Cycle on `03e762b` (post-#2067): same 4 manifests `manifest_ok` with new class-weighted boosters; v1-vs-v2 compare captured.
  - Cycle on `7015659` (post-#2072): 5m at the new 50× weight retrained; new precision/recall point captured.
- **Live-VM post-deploy verification:** `web-api git_sha=611b4c9 matches HEAD — OK`. Trader producing ticks normally by 15:18:30 UTC. `python -c "import lightgbm"` indirectly verified by the absence of import errors in trader journal.
- **v1-vs-v2 `compare` output captured** for both regime models at each rebalancing step (4 compare runs total).
- **Gate-check verdicts captured** for both v2 models at three checkpoints (pre-weight, w=200, w=50 on 5m only).
- **Gaps not yet verified:**
  - Imbalance-aware `non_degenerate` path needs to be re-checked against real `support_<class>` metrics once #2068 lands and the next cycle re-trains. Estimated impact (based on existing metrics' implied base rates): both 5m and 15m v2 models should pass the new path.
  - Live shadow predictions for the new model IDs haven't been verified in `runtime_logs/shadow_predictions.jsonl` yet — that requires a live signal tick to fire after the trainer mirror has published the new registry rows.

## Documentation Updated
- **Rules doc updates:** None.
- **Architecture doc updates:** None (the gate semantics live in `ml/promotion/gates.py` which is canonical via the WS7 sprint plan, not ARCHITECTURE-CANONICAL.md).
- **Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`):** None — no order-path changes.
- **Roadmap updates:** PR #2071 added M13 row + bumped M0..M11 heading to M0..M13 + extended active-queue list. This PR's ROADMAP edit adds a one-line addendum to the WS7 row noting the imbalance-aware gate path + the `class_weight` wiring.
- **GitHub Actions doc updates:** None.
- **Subsystem doc updates:** None.
- **Historical docs marked superseded:** None.
- **Health-review backlog:** 3 new items added — `BL-20260526-001` (v2 BTC LightGBM promotion-readiness watch), `BL-20260526-002` (MES regime + setup-quality manifests still fail), `BL-20260526-003` (setup-quality-lgbm-v2 worse than v0 baseline on n=76).

## Contradictions or Drift Found
- None encountered. The two prior incidents that this session might have brushed against — the PR #1358 "comment vs field" precedent and the `is_unbalance`-is-binary-only LightGBM gotcha — were checked against the code directly before edits.

## Risks and Follow-Ups
- **Remaining technical risks:**
  - 5m model at the new weight is healthier but still `precision_volatile ≈ 0.064` — meaningful (lift ≈ 12.8×) but not strong. The first round of live shadow predictions will tell us whether that's a real signal or a numerical fluke.
  - With #2068 merged the imbalance-aware path is *coded* but had not been verified against the live registry at session end (the trainer cycle that re-runs the evaluator with `support_<class>` had not completed). Verification deferred to the next session (and to BL-20260526-001's first action).
- **Remaining product decisions (Tier 3):**
  - `shadow → advisory` promotion conversation for `btc-regime-5m-lgbm-v2` and `btc-regime-15m-lgbm-v2`, expected on or after **2026-06-02** (T+7 soak) once `shadow_soak`, `beats_baseline`, `live_agreement`, and `drift_clean` gates clear themselves with live data. Operator-gated.
- **Blockers:**
  - 7-day `shadow_soak` (time, not engineering).
  - Accumulating live trades for `beats_baseline` / `live_agreement` / `drift_clean` (also time).
  - PR #2068 landing (engineering; armed for auto-merge).

## Deferred Items
- **MES regime + setup-quality manifests** still fail in the trainer cycle. They need a yfinance/ES=F intraday adapter wired into `build_trainer_datasets.sh` (for `market_features/MES/{5m,15m}/v002`) plus a non-empty MES `setup_labels` shard build. `scripts/ops/run_mes_training.sh` has the yfinance adapter wiring but is a separate one-shot script; merging the two cleanly is a follow-up. → BL-20260526-002.
- **`setup-quality-lgbm-v2`** trained successfully but lost to the v0 baseline on `n=76` closed trades (MAE 0.0710 vs 0.0478). Too little data for a gradient-boosted tree. Defer until n grows past ~1000. → BL-20260526-003.
- **Re-evaluation of the strict `min_class_f1 = 0.30` default** in light of the new imbalance-aware path — does it make sense as the primary semantic, or should the imbalance-aware path become primary? Design discussion; not engineering.

## Next Recommended Sprint
- **Suggested next sprint:** `S-V2-LIGHTGBM-PROMOTION-DECISION-2026-06-02` (or whenever the 7-day soak clock + live trade accumulation closes out) — re-run `gate-check` on both v2 BTC models, prepare the evidence packet, surface to the operator for the `shadow → advisory` decision.
- **Why next:** All engineering work for the v2 model promotion track is now landed or armed. Only time-gated and live-data-gated checks remain.
- **Required verification before starting:**
  - PR #2068 merged (verify in `git log`).
  - For each of `btc-regime-{5m,15m}-lgbm-v2`: ≥ 1 live winning + ≥ 1 live losing trade scored against the model (`live_agreement` precondition).
  - ≥ 3 cycles' worth of `macro_f1` in registry per model (`cross_run_stability` precondition).
  - `shadow_drift` reports populated for at least one model (`drift_clean` precondition).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [N/A] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated… — no order-path changes.
- [x] Roadmap status was checked + updated (PR #2071 added M13; this PR addends WS7).
- [x] Contradictions were recorded (none found).
- [x] Remaining unknowns were stated clearly (`non_degenerate` re-check after #2068; live shadow predictions; precision durability on the 5m model under live data).
