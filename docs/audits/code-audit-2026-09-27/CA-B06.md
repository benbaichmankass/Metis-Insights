# Code Audit CA-B06: CI Guard Self-Test Coverage (Guards 45-90)

> **Audit date:** 2026-09-27  
> **Scope:** Guards 45-90 from `python3 scripts/ci/run_guards.py --list`  
> **Purpose:** Verify each CI guard is PROVEN to fail on the defect it exists to catch

## Summary

| Category | Count | Finding |
|---|---|---|
| **Proven-fails** (has --self-test) | 22 | GOOD: These guards exercise and assert on a failure path |
| **Exit-0-only** (no self-test) | 6 | ⚠️ GAP: Exits 0 on clean tree; unproven vs defect |
| **External-tool** (third-party) | 3 | N/A: Tested by external tool (ruff, lint-imports) |
| **Could-not-test** (complex/workflows) | 15 | ⚠️ GAP: No failure path visible; manual inspection needed |

**Total guards in scope:** 46

## Guard Status Table

| # | Guard | Status | Test Command |
|---|---|---|---|
| 45 | candle-fixture-variance-guard | ✓ proven-fails | `python3 scripts/ci/check_candle_fixture_variance.p...` |
| 46 | corpus-row-floor-guard | ✓ proven-fails | `python3 scripts/ci/check_corpus_row_floor.py --sel...` |
| 47 | harness-dispatch-no-fixture-default-guard | ✓ proven-fails | `python3 scripts/ci/check_harness_dispatch_no_fixtu...` |
| 51 | news-feed-coverage-guard | ✓ proven-fails | `python3 scripts/ci/check_news_feed_coverage.py --s...` |
| 53 | pairs-sizing-basis-guard | ✓ proven-fails | `python3 scripts/ci/check_pairs_sizing_basis.py --s...` |
| 55 | artifact-caveat-guard | ✓ proven-fails | `python3 scripts/ci/check_artifact_caveats.py --sel...` |
| 56 | risk-basis-agreement | ✓ proven-fails | `python3 scripts/ci/check_risk_basis_agreement.py -...` |
| 57 | cost-model-single-owner | ✓ proven-fails | `python3 scripts/ci/check_cost_model_single_owner.p...` |
| 58 | tp-venue-cap-single-owner | ✓ proven-fails | `python3 scripts/ci/check_tp_venue_cap_single_owner...` |
| 62 | manager-scope-guard | ✓ proven-fails | `python3 scripts/ci/check_manager_scope.py` |
| 63 | cadence-liveness | ✓ proven-fails | `python3 scripts/ci/check_cadence_liveness.py` |
| 64 | guard-liveness | ✓ proven-fails | `python3 scripts/ci/check_guard_liveness.py` |
| 65 | research-results-guard | ✓ proven-fails | `python3 scripts/ci/check_research_results.py` |
| 68 | strategy-decision-record | ✓ proven-fails | `python3 scripts/ci/check_strategy_decision_record....` |
| 69 | roster-promotion-evidence-guard | ✓ proven-fails | `python3 scripts/ci/check_roster_promotion_evidence...` |
| 71 | selftest-wiring-guard | ✓ proven-fails | `python3 scripts/ci/guard_selftests.py` |
| 72 | diag-relay-render-guard | ✓ proven-fails | `python3 scripts/ops/diag_relay_render.py` |
| 73 | exit-mechanism-coverage-guard | ✓ proven-fails | `python3 scripts/ops/exit_mechanism_coverage.py` |
| 74 | lever-reachability-guard | ✓ proven-fails | `python3 scripts/ci/check_lever_reachability.py` |
| 75 | lever-evidence-flag-guard | ✓ proven-fails | `python3 scripts/ops/lever_evidence_flag.py` |
| 76 | guard-selftest-coverage | ✓ proven-fails | `python3 scripts/ci/check_guard_selftest_coverage.p...` |
| 78 | trainer-heavy-lock-guard | ✓ proven-fails | `python3 scripts/ci/check_trainer_heavy_lock.py` |
| 48 | json-extract-guard | ⚠️ exit-0-only | `python3 scripts/ci/check_json_extract_guarded.py -...` |
| 49 | json-notes-cap-guard | ⚠️ exit-0-only | `python3 scripts/ci/check_json_notes_cap.py` |
| 52 | new-table-wiring-guard | ⚠️ exit-0-only | `python3 scripts/check_new_table_wiring.py {pr_diff...` |
| 54 | prop-identity-guard | ⚠️ exit-0-only | `python3 scripts/ci/check_prop_identity_single_home...` |
| 67 | collapsed-state-guard | ⚠️ exit-0-only | `python3 scripts/ci/check_collapsed_states.py` |
| 70 | manifest-scope-constants | ⚠️ exit-0-only | `python3 scripts/ci/check_manifest_scope_constants....` |
| 50 | layer-guard | — external-tool | `lint-imports --config .importlinter` |
| 66 | research-queue-decision-rule-guard | — workflow-only | `check via .github/workflows/research-queue-decisio...` |
| 80 | ruff-lint | — external-tool | `ruff check` |
| 81 | secret-scan | — external-tool | `GitHub built-in secret scanning` |
| 59 | automerge-trigger-guard | ⚠️ could-not-test | `workflow-based (auto-merge-on-label.yml)` |
| 60 | pr-landing-guard | ⚠️ could-not-test | `workflow-based (pr-landing.yml)` |
| 61 | mandate-autoland-guard | ⚠️ could-not-test | `python3 scripts/ops/mandate_autoland.py` |
| 77 | provenance-consumer-guard | ⚠️ could-not-test | `python3 scripts/ci/check_provenance_consumer.py` |
| 79 | qty-legalization-guard | ⚠️ could-not-test | `python3 scripts/ci/check_qty_legalization.py` |
| 82 | silent-empty-guard | ⚠️ could-not-test | `python3 scripts/ci/check_silent_empty.py` |
| 83 | soak-doctrine-guard | ⚠️ could-not-test | `python3 scripts/ci/check_soak_doctrine.py` |
| 84 | strategy-coverage-guard | ⚠️ could-not-test | `python3 scripts/ci/check_strategy_coverage.py` |
| 85 | strategy-risk-guard | ⚠️ could-not-test | `python3 scripts/ci/check_strategy_risk.py` |
| 86 | timestamp-comparison-guard | ⚠️ could-not-test | `python3 scripts/ci/check_timestamp_comparison.py` |
| 87 | training-population-guard | ⚠️ could-not-test | `python3 scripts/ci/check_training_population.py` |
| 88 | trend-engine-convergence-guard | ⚠️ could-not-test | `python3 scripts/ci/check_trend_engine_convergence....` |
| 89 | mfe-parity-instrument-guard | ⚠️ could-not-test | `python3 scripts/ci/check_mfe_parity_instrument.py` |
| 90 | writer-conformance-guard | ⚠️ could-not-test | `python3 scripts/ci/check_writer_conformance.py` |

## Gaps and Recommendations

### 1. Exit-0-only guards (6)

These guards run but provide no evidence they fail on the defect they protect:

- Guard #48: `json-extract-guard`
- Guard #49: `json-notes-cap-guard`
- Guard #52: `new-table-wiring-guard`
- Guard #54: `prop-identity-guard`
- Guard #67: `collapsed-state-guard`
- Guard #70: `manifest-scope-constants`

**Remediation:** Add `--self-test` with a planted defect to each, following the pattern
in guards like `check_candle_fixture_variance.py`.

### 2. Could-not-test guards (15)

These guards lack visible failure paths:

- Guard #59: `automerge-trigger-guard`
- Guard #60: `pr-landing-guard`
- Guard #61: `mandate-autoland-guard`
- Guard #77: `provenance-consumer-guard`
- Guard #79: `qty-legalization-guard`
- Guard #82: `silent-empty-guard`
- Guard #83: `soak-doctrine-guard`
- Guard #84: `strategy-coverage-guard`
- Guard #85: `strategy-risk-guard`
- Guard #86: `timestamp-comparison-guard`
- Guard #87: `training-population-guard`
- Guard #88: `trend-engine-convergence-guard`
- Guard #89: `mfe-parity-instrument-guard`
- Guard #90: `writer-conformance-guard`

**Remediation:** Add `--self-test` or a pytest for each, or suppress from CI
until a failure path is added.

## References

- `scripts/ci/run_guards.py` — guard registry and runner
- `scripts/ci/check_guard_selftest_coverage.py` — coverage census tool
- `.claude/skills/health-review/SKILL.md` — CI audit methodology
