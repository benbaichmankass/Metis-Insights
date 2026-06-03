# Sprint Log: S-MLOPT-S4

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
- Primary goal: turn `ml/promotion/gates.py` from advisory-only into a real
  champion-challenger gate with **pre-registered, quantitative criteria** that
  COMPUTE PASS/FAIL — so "is this model ready to promote past shadow?" is
  mechanical and non-discretionary.
- Criteria: min shadow volume, min days in shadow, **OOS edge vs the
  incumbent/baseline computed under the S-MLOPT-S1 purged WF-CV** (the
  no-leakage guardrail — never a holdout), and drift within KS/PSI bounds.
- `python -m ml gate-check <id>` returns a go/no-go packet; `/ml-review` cites it
  in `promotion_recommendations[]`.

## Tier
- Tier 1 to COMPUTE the gates (autonomous).
- Enforcement of the actual `shadow → advisory` flip stays **Tier-3**,
  operator-gated. This sprint adds **no** auto-promote and edits **no**
  `config/strategies.yaml` / `config/accounts.yaml` / order-path / live file.
  `gate-check` / `stage-guard` only READ the registry, datasets, and shadow logs
  and PRINT a report.

## Starting Context
- M14 Session 0.4, the last Phase-0 (discipline) sprint. Consumes S-MLOPT-S1
  (purged & embargoed WF-CV: `iter_folds`, `_aggregate_fold_metrics`), and closes
  gap **G7** ("`gates.py` is advisory-only — never blocks").
- The scaffolding already existed pre-S4: `gate-check` / `stage-guard` CLI,
  `evaluate_gates`, per-model live `attribution` (AUC + brier-lift), `stage_guard`
  proposals. The **missing piece** was an OOS-edge-vs-baseline criterion measured
  under purged WF-CV — the existing `beats_baseline` gate used *live* brier-lift,
  which is a different (live-calibration) signal, not an offline champion-challenger
  comparison.

## Files and Systems Inspected
- `ml/promotion/{gates,attribution,stage_guard,checklist,__init__}.py`,
  `ml/cli.py` (gate-check / model-attribution / stage-guard), `ml/registry/model_registry.py`
  (7-stage ladder, `RegistryEntry.manifest`), `ml/experiments/{splitters,runner}.py`
  (`iter_folds`, `split_purged_walk_forward`, `_aggregate_fold_metrics`, `_load_jsonl`,
  `_resolve_callable`), `ml/shadow/drift.py` (`DriftReport`, KS/PSI verdicts),
  `ml/manifest.py`, `ml/trainers/constant_baseline.py` + `ml/evaluators/{base,regression}.py`
  (baseline predictor + metric orientation), `scripts/ml/eval_split_compare.py`
  (the purged-WF-CV override pattern this reuses).

## Work Completed
- **`ml/promotion/oos_edge.py` (new):** `compute_oos_edge(entry, datasets_root, …)`
  reconstructs the candidate's manifest, forces `split_strategy=purged_walk_forward`
  (`build_cv_config`, the same override `eval_split_compare` applies), and runs BOTH
  the candidate trainer and a **baseline trainer** (default
  `ConstantPredictionTrainer` — the per-group-mean baseline the G4 decision models
  are measured against) through the **same** `iter_folds` folds, pooling each with the
  runner's `_aggregate_fold_metrics`. Returns an `OOSEdgeResult` with a metric-oriented
  `edge` (positive ⇔ candidate beats baseline; `orient_edge` handles lower-is-better
  mse/mae/brier vs higher-is-better f1/auc). **Never scored on a holdout.** Best-effort:
  missing dataset / unreconstructable manifest / too few rows / incompatible baseline →
  `None` (gate reports `insufficient_data`).
- **`ml/promotion/gates.py`:** added the required `oos_edge` gate (`_gate_oos_edge` —
  PASS iff `edge > min_oos_edge`, default 0.0 ⇒ must strictly beat the baseline).
  Reworked `drift_clean` to gate on **numeric KS ≤ 0.2 / PSI ≤ 0.25 ceilings** when a
  real `DriftReport` is supplied (falls back to the verdict bucket for the dict the CLI
  emits — keeps existing callers working). New `GateThresholds` knobs: `min_oos_edge`,
  `max_ks`, `max_psi`. `evaluate_gates` gained an `oos_edge=` param; all gates are
  `required` so `ready` only goes true when every criterion (offline + live) clears.
- **`ml/cli.py`:** `gate-check` gained `--datasets-root` (computes the OOS-edge gate when
  set, else `insufficient_data`), `--baseline-trainer`, `--n-folds`, `--label-horizon`,
  `--embargo-fraction`. `stage-guard` gained `--datasets-root` (computes the OOS-edge gate
  for every `shadow`-stage model so promote proposals carry champion-challenger evidence).
- **`ml/promotion/stage_guard.py`:** `propose_for_model` + `run_stage_guard` thread
  `oos_edge` through; without `datasets_root` a shadow model holds on `oos_edge`
  insufficient-data (you cannot certify readiness without the OOS evidence).
- **`.claude/skills/ml-review/SKILL.md`:** the `shadow → advisory` recommendation must now
  cite the computed `gate-check` packet (`ready` + `blocking[]`) and not recommend
  `promote` while `ready: false`.
- **Tests:** `tests/ml/test_gates.py` — new boundary tests for `oos_edge` (edge==0 fails,
  edge>0 passes, negative/lose fails, missing → insufficient_data blocks `ready`) and the
  numeric KS/PSI ceiling (at-ceiling passes, just-over fails). `tests/ml/test_oos_edge.py`
  (new) — orientation helper both directions; constant-vs-constant → edge 0.0; missing
  dataset / bad manifest / too-few-rows → None. `tests/ml/test_stage_guard.py` — promote
  test now supplies OOS edge; added a "healthy-but-no-OOS-edge holds" test.

## Validation Performed
- `tests/ml/test_gates.py tests/ml/test_oos_edge.py tests/ml/test_stage_guard.py` →
  26 passed. Related suites (attribution, splitters, runner) → green. Full `tests/ml/`
  (excluding the 4 pandas-only dataset tests the sandbox can't import — pre-existing env
  gap, unrelated) → **481 passed, 1 skipped**. `py_compile` clean.
- **No-leakage:** the OOS edge reuses the S-MLOPT-S1 `iter_folds` /
  `split_purged_walk_forward` (whose regression test pins that no future-dated row enters
  any train fold) and forces `split_strategy=purged_walk_forward` — it is structurally
  impossible to score the edge on a holdout.
- **CLI smoke (sandbox):** `gate-check` without `--datasets-root` → `oos_edge:
  insufficient_data`, `ready: false`. With a synthetic dataset + `--datasets-root` and the
  constant trainer as both candidate and baseline → `oos_edge: fail` (`edge +0.00000` over
  3 purged WF-CV folds) — the correct champion-challenger verdict (no edge ⇒ not ready).

## Real-model gate-check packet (trainer VM)
Ran `python -m ml gate-check … --datasets-root datasets-out --db trade_journal.db`
on two real registry models via the `trainer-vm-diag` relay (this branch fetched
into a detached worktree; the running trainer checkout untouched). Issues #2686
(discovery) + #2687 (packet). **The gate mechanically reproduced the G4 finding** —
the OOS-edge gate is doing exactly its job:

**`setup-quality-lgbm-v2`** (the LightGBM regressor — G4's prime suspect),
target `advisory`, `ready: false`:
- `oos_edge` → **FAIL**: OOS edge on `mae` = **−0.00954** over 5 purged WF-CV folds
  (candidate LGBM **0.08460** vs constant per-group-mean baseline **0.07506**). **The
  LightGBM head LOSES to the trivial baseline on honest, leak-free folds** — the exact
  MB-20260527-003 / G4 result ("lost to a per-group-mean baseline at n=80"), now
  computed automatically instead of discovered by hand.
- `sample_sufficiency` → FAIL (eval n=80 < 1000); `shadow_soak` → FAIL (4.1d at
  research_only < 7); `cross_run_stability` → PASS (std mae 0.0037 over 5 runs);
  `non_degenerate` / `beats_baseline` / `live_agreement` / `drift_clean` →
  insufficient_data (no live shadow predictions joined for this model).

**`setup-quality-baseline-v0`** (a real **shadow**-stage model), `ready: false`:
- `oos_edge` → **PASS**: edge `+0.00123` (candidate 0.07383 vs baseline 0.07506) — the
  per-setup_type grouped mean narrowly beats the global constant mean, so the gate
  correctly clears it; `shadow_soak` → PASS (15.2d); `cross_run_stability` → PASS.
- Still not ready: blocked on `sample_sufficiency` (n=80) + the live-evidence gates
  (insufficient_data) — honest, since there's no real-time shadow-prediction track
  record joined yet.

Read: the packet is **mechanical and non-discretionary** — it failed the model that
loses to baseline, passed the one that beats it, and held both back on volume / live
evidence without any human judgement. The shadow→advisory flip stays **Tier-3**: the
packet is evidence; the operator pulls the lever.

## Documentation Updated
- `ROADMAP.md` (M14 milestone summary + S-MLOPT-S4 row → DONE; Phase 0 now S0–S4),
  `docs/ml/optimization-roadmap.md` (Session 0.4 shipped-block + G7 closed),
  `.claude/skills/ml-review/SKILL.md` (cite the gate packet), this sprint log.

## Risks and Follow-Ups
- The default constant per-group-mean baseline is correct for the **regression**
  decision models (setup-quality / trade-outcome). For the **multiclass regime** heads a
  constant-mean baseline is not a sensible class predictor — `compute_oos_edge` returns
  `None` (→ `insufficient_data`) on the incompatibility rather than a misleading number;
  a majority-class baseline trainer (`--baseline-trainer`) is the clean follow-up.
- "Incumbent" comparison is currently the naive baseline; comparing a challenger against
  the **current champion's** re-scored folds is a natural Phase 4.3 extension
  (full champion-challenger automation).
- Computing the OOS edge re-trains the model 2×N folds — fine for a manual `gate-check`,
  but `stage-guard --datasets-root` over the full registry is heavier; run it deliberately.
- Enforcement stays Tier-3: nothing here flips a stage or edits a live file.

## Next Recommended Sprint
- Phase 0 (discipline) is complete. Per the recommended order: **S-MLOPT-S13 (3.1)**
  per-bar regime scoring (unblocks the jammed regime promotion pipeline) or
  **S-MLOPT-S5 (1.1)** triple-barrier `setup_candidates` (the decision-model data wall).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched.
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns were stated clearly.
