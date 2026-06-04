# Sprint Log: S-MLOPT-S14

## Date Range
- Start: 2026-06-04
- End: 2026-06-04

## Objective
- Primary goal: ship a **causal (filtered) Gaussian-HMM regime family** (M14
  Phase 3.2) — an alternative regime model to the LightGBM heads, fit on the S9
  range-based volatility features, scored with the **filtered (forward-only)**
  posterior so it cannot leak the future.
- Secondary goals: keep the predictor dependency-light (it runs on the live
  trader if ever promoted), make the causal discipline a tested property, and
  ship the manifest as a Tier-3 research_only A/B vs the 1h LightGBM champion.

## Tier
- **Tier 1** for the trainer/predictor/tests (trainer-VM tooling, no live-path
  file touched). **Tier 3** for the manifest (`ml/configs/btc-regime-1h-hmm-v1.yaml`)
  — a research_only proposal; promotion past shadow is operator-gated.
- Justification: matches every prior M14 model sprint — trainer-side tooling is
  autonomous Tier-1; new `ml/configs/*.yaml` manifests are Tier-2/3.

## Starting Context
- Active roadmap items: M14 Phase 3. S13 (per-bar scoring) merged earlier this
  session (PR #2778). S14 is the Phase-3.2 regime-modeling experiment.
- Prior sprint reference: [`S-MLOPT-S9.md`](S-MLOPT-S9.md) (the range-vol
  features the HMM consumes), [`S-MLOPT-S13.md`](S-MLOPT-S13.md).
- Known risks at start: (1) `hmmlearn`/`sklearn` are not deps and the predictor
  may run live → must be self-contained; (2) the evaluator scores **per row**,
  so a sequential filter needs a state-management contract; (3) the "illusion of
  regimes" dissent — an HMM can look good in-sample and add nothing OOS.

## Repo State Checked
- Branch reviewed: `claude/m14-progress-next-u260C`, re-synced onto `main` after
  the S13 squash-merge (PR #2778) so this work stacks cleanly.
- Canonical docs reviewed: `ROADMAP.md` M14 table, `docs/ml/optimization-roadmap.md`
  Phase 3.2, the trainer/predictor/evaluator contract.

## Files and Systems Inspected
- Code files inspected: `ml/trainers/base.py` (`Trainer.fit` + `PREDICTOR_CLASS`),
  `ml/predictors/base.py` + `multiclass.py` + `per_bucket_multiclass.py`
  (predictor contract + the regime-spec pattern), `ml/trainers/regime_classifier.py`
  + `ml/trainers/lightgbm_multiclass.py` (model_state shape + regime-spec freeze),
  `ml/evaluators/multiclass_classification.py` (the per-row `predict_label`
  scoring loop — the constraint that shaped the filter contract),
  `ml/datasets/volatility_estimators.py` (S9 features), `ml/manifest.py`
  (manifest load).
- Config files inspected: `ml/configs/btc-regime-1h-lgbm-v2.yaml` (the A/B
  champion).
- Dependencies checked: `requirements.txt` — NumPy present; `hmmlearn`,
  `ruptures`, `scikit-learn` **absent** → self-contained implementation chosen.

## Work Completed
- **`ml/trainers/causal_hmm_regime.py`** — `CausalHMMRegimeTrainer(Trainer)`
  (`PREDICTOR_CLASS = CausalHMMRegimePredictor`). NumPy, trainer-VM only:
  diagonal-Gaussian **GMM EM** over `feature_columns` (deterministic quantile
  init, seeded) → per-state `means`/`variances` + soft responsibilities;
  **transition matrix** from soft consecutive-bar responsibilities
  (`resp[:-1].T @ resp[1:]`, Laplace-smoothed, row-stochastic); **start_prob** =
  mean responsibility; **state_label_proba** = responsibility-weighted
  regime-label frequencies. Degenerate guards (n=0 → scorable uniform 1-state
  model; `n_states` clamped to `n`).
- **`ml/predictors/causal_hmm_regime.py`** — `CausalHMMRegimePredictor(MulticlassPredictor)`,
  pure-stdlib (`math`). Runs the **filtered** forward recursion only
  (`alpha_t(k) ∝ e_k(x_t)·Σ_i alpha_{t-1}(i)·A[i,k]`, `alpha_0(k) ∝ e_k(x_0)·pi(k)`),
  diagonal-Gaussian emissions (a missing feature is skipped, not imputed). It is
  **stateful** across a chronological pass and **auto-resets** when a row's `ts`
  is not strictly increasing (fold boundary / replay), so there is no cross-fold
  state leak. `reset()` forces it.
- **`ml/configs/btc-regime-1h-hmm-v1.yaml`** — research_only A/B manifest vs
  `btc-regime-1h-lgbm-v2` (same `market_features` BTCUSDT 1h v002 dataset, same
  `regime_label` target + `time_aware_holdout` split; observation =
  `yang_zhang_vol` + `rolling_log_return_vol`, `n_states: 3`).
- **`tests/ml/test_causal_hmm_regime.py`** (11) — predictor basics (proba sums to
  1, low/high-vol → range/volatile, missing-feature tolerance, required-key
  guards); **causality** (the filtered posterior at each step is byte-identical
  with vs without 3 appended future bars; auto-reset on non-monotonic ts); trainer
  (valid stochastic state, determinism, fit→predict separates regimes, empty→uniform);
  and evaluator integration (>0.9 acc through `MulticlassClassificationEvaluator`
  via the standard `PREDICTOR_CLASS` resolution).

## Validation Performed
- Tests run: `tests/ml/test_causal_hmm_regime.py` → 11 passed.
- Lint: `ruff check` clean on trainer, predictor, test.
- Manifest: loads via `ml.manifest.TrainingManifest.from_yaml` (model_id,
  trainer dotted-path, dataset, evaluator, `research_only` stage all resolve).
- Gaps not yet verified: the **trainer-VM run on the real BTCUSDT 1h
  `market_features` shard** + the purged WF-CV A/B vs `btc-regime-1h-lgbm-v2`
  (`scripts/ml/eval_split_compare.py`). That is the follow-up that decides
  whether the HMM earns a `shadow` proposal — synthetic-data tests prove the
  mechanics, not the real-data edge.

## Documentation Updated
- Roadmap updates: `ROADMAP.md` S-MLOPT-S14 row → IN REVIEW (and the S13 row
  → MERGED, reflecting PR #2778).
- Subsystem doc updates: `docs/ml/optimization-roadmap.md` Session 3.2 → shipped.

## Contradictions or Drift Found
- None new. (The S13 feature-parity gap `MB-20260604-005` also applies to a
  future live HMM — its observation features would have to be computed live —
  noted in the manifest's TIER block.)

## Risks and Follow-Ups
- Remaining technical risks: the per-row evaluator interface means the filter
  relies on chronological row order within a `score()` pass; the auto-reset on
  non-monotonic `ts` is the guard, and the causal-invariance test pins the
  contract. If a future eval path shuffles rows, the reset keeps it correct
  per-contiguous-run but a shuffled fold would degrade (documented).
- Remaining product decisions (Tier 3): adopt the manifest / propose `shadow`
  only if the purged-CV A/B shows OOS edge over the LightGBM head.
- Blockers: none for the Tier-1 tooling.

## Deferred Items
- Trainer-VM purged WF-CV A/B vs `btc-regime-1h-lgbm-v2` → follow-up (the
  edge decision).
- 5m/15m/MES HMM A/B manifests → only if the 1h A/B is positive.
- Live per-bar HMM scoring → depends on `MB-20260604-005` (feature parity) since
  the live regime row would need `yang_zhang_vol`.

## Next Recommended Sprint
- Suggested next sprint: run the **S14 trainer-VM A/B** (decides the HMM's fate),
  then **S15 (3.3) regime-router phase-4 detector wiring** (`MB-20260601-002`) —
  which can wire whichever regime head wins (LightGBM or HMM) once it has a
  shadow track record (now unblocked by the merged S13 per-bar path).
- Why next: S14 produces a candidate; S15 is where a winning regime head becomes
  a router detector.
- Required verification before starting: the trainer-VM A/B result.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched (trainer + predictor + manifest
      + tests only); `docs/TRADE-PIPELINE.md` unchanged.
- [x] Roadmap status checked + updated (S14 IN REVIEW; S13 MERGED).
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns stated: the real-data purged-CV A/B vs the LightGBM
      head is the open question; synthetic tests prove mechanics only.
