# Sprint Log: S-TEST-CACHE-FLAKE-FIX

## Date Range
- Start: 2026-05-20
- End: 2026-05-20

## Objective
- Primary: Fix `test_reload_invalidates_cache` flake (FU-20260519-003). `Coordinator.reload_strategy_config` had a `FileNotFoundError` early-return that skipped the cache-clear.

## Tier
- Tier 1 (test infrastructure / non-live code path, self-merge)

## Starting Context
- Sprint 4 (S-VWAP-POLICY-LIVE-WIRE) just merged (PR #1579, 2026-05-20)
- FU-20260519-003 noted the flake appeared across 5 PRs in the 2026-05-19 session

## Repo State Checked
- Branch: `claude/setup-coding-session-qCToW`
- Merged origin/main (Sprint 4 squash at a4cc582)

## Files Changed

### `src/core/coordinator.py`

**Root cause:** `reload_strategy_config` at line 1355:
```python
# BEFORE (buggy)
try:
    cfg = load_strategy_config(path)
except FileNotFoundError:
    return {"reloaded": False, "error": "..."}   # ← early return

self._shadow_predictors_cache.clear()            # ← never reached on FileNotFoundError
```

**Fix:** Move the cache clear before the try/except:
```python
# AFTER
self._shadow_predictors_cache.clear()            # ← runs unconditionally

try:
    cfg = load_strategy_config(path)
except FileNotFoundError:
    return {"reloaded": False, "error": "..."}
```

The test deliberately calls `reload_strategy_config(config_path="no-such-yaml")` to exercise the early-return path and asserts `cache == {}`. This was failing whenever the cache had been primed before the call.

## Test Results
- `test_reload_invalidates_cache`: passes 3/3 consecutive runs post-fix
- `TestShadowPredictorCache` (all 9 tests): passing
- `test_vwap_strategy.py` (77 tests): passing
- 0 regressions

## Follow-up Items Closed
- FU-20260519-003: `test_reload_invalidates_cache` flake — CLOSED

## Next Sprints
- Sprint 5 (S-ML-REGIME-CLASSIFIER-FIX): f1_trend=0.0 regime classifier degeneracy
- Sprint 9 (S-BACKTEST-DOC-DRIFT-FIX): comment drift in backtest files
