# CA-B05: CI Guards Part 1 Audit

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

## Summary

**Scope:** First 44 CI guards from `python3 scripts/ci/run_guards.py --list`  
**Date:** 2026-09-27  
**Analyzed:** 44/101 guards (through candle-fixture-variance-guard)

### Results

| Category | Count | Coverage |
|---|---:|---:|
| Proven to fail (have --self-test) | 29 | 65.9% |
| Exit-0-only (pytest, no --self-test) | 12 | 27.3% |
| Could not test (no test coverage) | 3 | 6.8% |
| **Total** | **44** | **100%** |

## Methodology

For each of the 44 guards, this audit:
1. **Parsed guard definition** from `scripts/ci/run_guards.py`
2. **Checked for --self-test** step (plants defect, asserts guard fails)
3. **Checked for pytest** unit test coverage (unproven as comprehensive)
4. **Classified** as:
   - `proven-fails`: Guard has `--self-test` that verifies failure path
   - `exit-0-only`: Guard has pytest but no `--self-test`
   - `could-not-test`: Guard has neither self-test nor pytest

## Key Findings

**Finding 1: Only 65.9% of guards are proven to catch failures**  
- 29 guards have built-in `--self-test` that plants defects
- 12 guards rely on pytest (may not cover all failure modes)
- 3 guards have no automated failure path verification

**Finding 2: 12 guards lack self-test verification**  
Guards with pytest but no --self-test:
- api-tier-policy-guard
- async-route-blocking-guard
- canonical-config-loaders
- canonical-db-resolver
- canonical-doc-coherence
- diagnostic-provenance-guard
- dry-run-guard
- env-gate-guard
- harness-lever-coupling-guard
- pytest-skip-attribution-guard
- test-schema-fidelity-guard
- workflow-catalog

**Finding 3: 3 guards have no automated test coverage**  
- exit-coverage-matrix-guard (script: scripts/research/m20_coverage_rollup.py)
- impossibility-claim-guard (script: scripts/check_impossibility_claims.py)
- workflow-push-target-guard (no script found)

## Impact Assessment

**Critical gaps:** The 3 guards with no test coverage represent a risk that:
- Defects in the guard logic would not be caught automatically
- Changes to guard behavior could silently fail without notice
- CI could report false negatives (guard passes when defects exist)

**Medium gaps:** The 12 guards with only pytest coverage may not have:
- Self-tests that plant intentional defects
- Comprehensive coverage of all failure modes
- Verification that the guard actually fails on the issues it targets

## Recommendations

### Priority 1: Add --self-test to unproven guards

For each of the 12 guards without --self-test:
```bash
# Suggested pattern:
guard_name="api-tier-policy-guard"
python3 scripts/ci/${guard_name/_/-}.py --self-test
```

For the 3 guards with no test coverage:
```bash
# Plant synthetic defects and verify guard catches them
python3 scripts/research/m20_coverage_rollup.py  # Exits 0 on clean tree
python3 scripts/check_impossibility_claims.py    # Check if it detects defects
```

### Priority 2: Review pytest coverage

For guards relying on pytest, verify tests:
- Plant intentional defects
- Assert the guard fails on planted defects
- Cover all failure modes the guard is designed to catch

### Priority 3: Document guard intent

Each guard should document:
- What defect it exists to catch
- How its self-test demonstrates the failure path
- Expected exit codes (0 = pass, non-zero = fail)

## Command Reference

```bash
# Run first 44 guards
python3 scripts/ci/run_guards.py --run account-class-guard roster-symbol-reachability ...

# Test individual guard
python3 scripts/ci/run_guards.py --run guard-name

# Check guard self-test coverage across all guards
python3 scripts/ci/check_guard_selftest_coverage.py
```

## Audit Files

- **CA-B05.findings.jsonl**: Detailed findings (44 entries, one per guard)
- **CA-B05.md**: This summary document
