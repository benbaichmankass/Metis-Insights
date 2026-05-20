# Sprint Log: S-REFACTOR-S5

**Date:** 2026-05-20  
**Sprint:** S5 — CENTRALIZED_ALLOCATOR feature flag (shadow mode)  
**Tier:** 3 (pipeline entrypoint change, PM-approved before merge)  
**Branch:** `claude/refactor-s5-centralized-allocator`

## Objective

Add a `CENTRALIZED_ALLOCATOR` env-flag (default `false`) that shadow-audits the
typed allocator path inside the live multi-account dispatch block. When the flag
is on, `coordinator.build_order_packages()` runs in parallel with the existing
`multi_account_execute` call and logs what the allocator computed; dispatch is
unchanged. The flag is off by default so live runtime is not affected.

## Files Changed

| File | Change |
|---|---|
| `src/runtime/runtime_flags.py` | Add `import os`; add `_centralized_allocator_enabled(settings) -> bool` |
| `src/runtime/pipeline.py` | Import `_centralized_allocator_enabled`; insert S5 shadow block after `coord = Coordinator()` |
| `tests/test_s5_centralized_allocator_flag.py` | 10 tests for flag parsing (default, env, settings override, case-insensitive, non-dict) |

## Safety

- `CENTRALIZED_ALLOCATOR` defaults to `false` — zero behaviour change in production.
- Shadow block is wrapped in its own `try/except` — any allocator failure is logged
  at WARNING and dispatch continues normally.
- `multi_account_execute` is called regardless of flag state.
- `is_strategy_paused` is unaffected (same module, independent function).

## Tests

10 tests in `tests/test_s5_centralized_allocator_flag.py`:
- Default off, env true/1/yes/on, env false
- Settings dict overrides env (false wins over env=true)
- Settings dict on directly
- Non-dict settings falls back to env
- Case-insensitive env parsing

## Follow-up (S6)

S6 will promote the shadow path to the primary dispatch path when
`CENTRALIZED_ALLOCATOR=true`, replacing `_signal_to_order_package` entirely.
Requires: portfolio_state sourced from a real balance snapshot; Tier-3 review.
