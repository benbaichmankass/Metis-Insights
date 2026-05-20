# S-REFACTOR-S4: AllocatorInterface wiring into coordinator

**Date:** 2026-05-20  
**Tier:** 2 (runtime pipeline touch — merge review required)  
**PR:** #TBD (claude/refactor-s4-allocator-wiring → main)  
**Status:** Complete

---

## Objective

Wire `AllocatorInterface` / `PassthroughAllocator` (S1) into the coordinator
dispatch path. Pre-gate: read coordinator dispatch logic (`strategy_order_pkg`,
`multi_account_execute`) to identify safe insertion point. Neither hot-path
method is touched.

---

## Changes

### `src/core/coordinator.py`
Three additive changes (45 lines total):
1. `TYPE_CHECKING` block — import `Sequence`, `AllocatorInterface`, `SignalPackage`,
   `OrderPackage` (type-check only, zero runtime overhead)
2. `__init__` — add `self._allocator: Any = None` (lazy-init placeholder)
3. After `instrument_profiles` property:
   - `allocator` property: lazy-init + cache `PassthroughAllocator` instance
   - `build_order_packages(signals, portfolio_state)` method: delegates to
     `self.allocator.allocate()` — the typed opt-in entry point for the
     allocator path

`multi_account_execute`, `strategy_order_pkg`, `account_execute` — **unchanged**.
Live pipeline uses existing per-account sizing until S5/S6 opt-in.

### `tests/test_s4_allocator_wiring.py`
19 tests:
- `PassthroughAllocator.allocate()`: qty formula, none-side skip, missing-SL
  skip, zero-distance skip, multi-signal, default risk_pct fallback, attribution
- `coordinator.allocator`: type, identity (cached), `PassthroughAllocator` by default
- `coordinator.build_order_packages()`: return type, actionable/none-side, qty,
  account_id binding via `with_account()`

---

## Safety analysis

- **Live path impact:** Zero. `multi_account_execute` / `strategy_order_pkg` /
  `account_execute` are untouched. The new `build_order_packages` method is only
  called when a caller explicitly invokes it — never by the live loop.
- **Lazy init:** `_allocator` starts as `None`; `PassthroughAllocator` is only
  instantiated on first `coordinator.allocator` access (pure Python, no IO).
- **Tier-2 scope:** coordinator.py touched (runtime class) but no signal, sizing,
  or execution code changed. Merge requires Ben's review.

---

## Next: S-REFACTOR-S5

Read `src/runtime/pipeline.py` (or equivalent entrypoint) to understand where
signal builders are called and how results flow to `multi_account_execute`. Design
the opt-in `CENTRALIZED_ALLOCATOR` feature flag that routes through
`coordinator.build_order_packages()` instead of the existing per-strategy sizing.
Tier-3 — requires explicit PM approval before merge.
