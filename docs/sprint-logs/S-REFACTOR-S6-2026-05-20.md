# Sprint Log: S-REFACTOR-S6

**Date:** 2026-05-20  
**Sprint:** S6 — CENTRALIZED_ALLOCATOR primary path  
**Tier:** 3 (pipeline entrypoint change, PM-approved)  
**Branch:** `claude/refactor-s6-centralized-allocator-primary`

## Objective

Promote `CENTRALIZED_ALLOCATOR` from shadow mode (S5) to primary dispatch path.
When the flag is on and the signal carries a typed `SignalPackage` (S3 wiring),
build the coordinator `OrderPackage` from the typed fields instead of the raw
signal dict. `multi_account_execute` dispatch is unchanged — per-account
`RiskManager` sizing still runs.

## What Changes

| File | Change |
|---|---|
| `src/runtime/pipeline.py` | Replace S5 shadow block with S6 primary path: when flag is on + `signal_package` present + actionable → build `_CoordOrderPackage` from typed fields; also call `build_order_packages` for allocator qty logging. Else: fall back to `_signal_to_order_package`. |
| `tests/test_s6_centralized_allocator_primary.py` | 14 tests: `SignalPackage` contract (8) + `PassthroughAllocator` chain (8) |

## Key Design Decisions

1. **Fallback preserved**: If flag is off, or signal has no `signal_package`,
   the code falls back to `_signal_to_order_package(signal, settings)` exactly
   as before. Zero behaviour change when flag is off.

2. **Allocator qty is informational**: `build_order_packages` is called and the
   resulting qty is logged. `multi_account_execute` re-sizes per-account via
   `RiskManager` as before. This is intentional — replacing the per-account
   sizing is S7 scope.

3. **`_CoordOrderPackage` imported locally** inside the dispatch `try:` block,
   same pattern as the existing `Coordinator` import. No circular import risk.

4. **`_raw` guard**: `_sig_pkg.raw` is checked with `isinstance(..., dict)`
   before indexing. Graceful if `raw` is None.

## Safety

- `CENTRALIZED_ALLOCATOR` still defaults to `false` — live runtime unaffected.
- Allocator call is wrapped in `try/except` — failure is logged and dispatch continues.
- `multi_account_execute` is called regardless of typed path vs fallback.
- Existing `_signal_to_order_package` + `multi_account_execute` path is the fallback.

## Follow-up (S7)

S7 would replace `multi_account_execute` per-account RiskManager sizing with
the pre-computed `allocator_qty` from `build_order_packages`, making the
centralized allocator the single sizing authority. Requires new
`multi_account_execute_typed()` method + significant Tier-3 review.
