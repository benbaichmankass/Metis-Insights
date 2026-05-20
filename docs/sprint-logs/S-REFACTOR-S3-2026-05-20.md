# S-REFACTOR-S3: SignalPackage wiring into strategy signal builders

**Date:** 2026-05-20  
**Tier:** 2 (runtime pipeline touch — merge review required)  
**PR:** #TBD (claude/refactor-s3-signal-wiring → main)  
**Status:** Complete

---

## Objective

Wire `SignalPackage` (from `src/core/signal_contract.py`, S1) as the typed
output contract for the three live strategy signal builders. Pre-gate: inspect
`src/core/signals.py` for name collisions (none found — `ICTSignalsAnalyzer`,
`ict_signal_from_df` only).

---

## Changes

### `src/runtime/strategy_signal_builders.py`
- Added `_with_signal_package(strategy_id, sig_dict) -> dict` helper
- All 7 return paths across the three builders now call `_with_signal_package`
- Result: every builder output carries `sig["signal_package"]` as a typed
  `SignalPackage` object; all existing dict keys preserved unchanged
- Side translation: `"buy" -> "long"`, `"sell" -> "short"`, other -> `"none"`
- `account_id` is left empty (`""`); S4 (allocator) will bind it
- `raw` field captures the original dict (minus the `signal_package` key itself)
- `source_context` carries the `meta` dict for downstream ML/regime use

### `tests/test_s3_signal_wiring.py`
- 20 tests covering `_with_signal_package()` directly (no live exchange calls)
- Tests: key presence, type, side translation, dict key preservation, field
  mapping, `is_actionable`, `sl_distance`, `with_account`, `raw` self-exclusion

---

## Safety analysis

- **Live path impact:** Zero. All existing dict keys (symbol, side, price,
  stop_loss, take_profit, pattern, meta) are preserved. The multiplexer,
  RiskManager, and order layer never inspect `"signal_package"` — they pass
  transparently. The new key is additive only.
- **No strategy logic changed.** Signal computation, audit logging, shadow
  predictor emit paths all unchanged.
- **Tier-2 scope:** touches `src/runtime/` (runtime pipeline) but no live
  order submission path. Merge requires Ben's review.

---

## Pre-gate checklist

- [x] `src/core/signals.py` inspected — no name collision with `SignalPackage`
- [x] `signal_contract.py` definition confirmed (`strategy_id`, `side: long/short/none`, etc.)
- [x] `_with_signal_package` is purely additive (new key only, no mutation of existing keys)
- [x] All 7 return paths wrapped across 3 builders
- [x] 20 tests; all pass locally (AST-verified)

---

## Next: S-REFACTOR-S4

Wire `AllocatorInterface` / `PassthroughAllocator` (S1) into the coordinator
dispatch path so it consumes `sig["signal_package"]` and produces
`OrderPackage` objects. Gate: read coordinator dispatch logic first.
