# Sprint Log: S-REFACTOR-S1

**Sprint ID:** S-REFACTOR-S1  
**Sprint Title:** Core Architecture Abstractions (Scaffold)  
**Date:** 2026-05-20  
**Status:** COMPLETE  
**Tier:** Tier-1 Autonomous (new files only, zero live-path modification)  
**Roadmap:** [ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md](../sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md)

---

## Objectives

Add six foundational abstract types to `src/core/` that will underpin the
multi-strategy architecture. No existing files modified. No live behavior
changed. All new files are import-isolated until S3–S4 wiring.

---

## Files Created

| File | Purpose |
|------|---------|
| `src/core/account_profile.py` | `AccountProfile` frozen dataclass; Bybit + IB exchange types; `from_dict()` builder from `accounts.yaml` entries |
| `src/core/instrument_profile.py` | `InstrumentProfile` frozen dataclass; pre-built `btcusdt_bybit_linear()` + `mes_cme()` |
| `src/core/signal_contract.py` | `SignalPackage` — normalized signal contract between strategy signal builders and allocator |
| `src/core/order_contract.py` | `OrderPackage` — normalized order contract with `from_signal()` attribution builder |
| `src/core/strategy_interface.py` | `StrategyInterface` ABC — contract for strategy modules; `build_signal()`, `build_order_package()`, `category` |
| `src/core/allocator.py` | `AllocatorInterface` ABC + `PassthroughAllocator` — replicates current `risk.py` sizing formula exactly |
| `tests/test_s1_abstractions.py` | 12 tests covering all six new types; no network, no secrets, CI-safe |

---

## Files Modified

_None._ This sprint adds only new files.

---

## Key Design Decisions

### AccountProfile
- Frozen dataclass — immutable after construction
- `exchange` field supports `"bybit"` | `"interactive_brokers"` | `"unknown"` from S1
- `account_type` auto-inferred from exchange + dry_run if not set explicitly
- `from_dict()` is defensive: unknown exchange maps to `"unknown"`, not an error
- IB support wired at type level even though IB execution is S7

### InstrumentProfile
- `btcusdt_bybit_linear()` reflects current live instrument parameters (tick=0.1, min_qty=0.001)
- `mes_cme()` reflects MES contract spec ($5/point, tick=0.25, min_qty=1 contract)
- `round_qty()` helper rounds down to nearest qty_step (floor, not round)

### SignalPackage
- `side: Literal["long", "short", "none"]` — `"none"` is the explicit flat/no-signal value
- `is_actionable` requires both `side != "none"` AND `entry_price is not None`
- `sl_distance` property returns `None` if either price is missing (safe for allocator)
- `with_account()` uses `dataclasses.replace()` — produces a clean shallow copy
- Named `signal_contract.py` (not `signals.py`) to avoid conflict with existing `src/core/signals.py`

### OrderPackage
- `attribution` dict carries full signal provenance for audit trail and future Streamlit transparency
- `from_signal()` is the canonical constructor — never build manually from scratch
- `net_position_context` is reserved for S4 cross-strategy netting (empty dict until then)

### StrategyInterface
- ABC only — not yet enforced on existing live strategies (vwap, turtle_soup, ict_scalp)
- `category` property defaults to `"unknown"`; concrete subclasses override
- Three expected category values documented in docstring matching architecture target

### PassthroughAllocator
- Exactly replicates `src/units/accounts/risk.py` formula: `qty = (balance * risk_pct) / sl_distance`
- Default `risk_pct = 0.005` (0.5%) when strategy not in map — conservative fallback
- Signals with `sl_distance <= 0` or `None` are skipped, not errored
- Feature flag `CENTRALIZED_ALLOCATOR` (env var, default false) will gate S4 wiring

---

## Contradictions / Notes Carried Forward

1. **`src/strategy_registry.py` naming collision**: The existing file is an ML model registry,
   not a strategy execution registry. S3 must use a distinct name (e.g., `src/core/strategy_registry.py`
   as a typed dict/class, or `STRATEGY_CATALOG`) to avoid import confusion.

2. **`src/core/signals.py` content not yet inspected**: S3 must read `src/core/signals.py` fully
   before importing or referencing `SignalPackage` in the live pipeline to confirm there is no
   overlap in exported names.

---

## Test Summary

| Test Class | Count | Coverage |
|------------|-------|----------|
| `TestAccountProfile` | 4 | from_dict (live/demo), frozen check, IB profile |
| `TestInstrumentProfile` | 3 | btcusdt pre-built, mes_cme pre-built, frozen check |
| `TestSignalPackage` | 4 | is_actionable (3 cases), with_account copy |
| `TestOrderPackage` | 1 | from_signal preserves attribution |
| `TestPassthroughAllocator` | 5 | empty→[], flat signal skipped, qty formula, attribution, multi-signal |
| **Total** | **17** | |

---

## Definition of Done — S1

- [x] `src/core/account_profile.py` committed
- [x] `src/core/instrument_profile.py` committed
- [x] `src/core/signal_contract.py` committed
- [x] `src/core/order_contract.py` committed
- [x] `src/core/strategy_interface.py` committed
- [x] `src/core/allocator.py` committed
- [x] `tests/test_s1_abstractions.py` committed (17 tests)
- [x] No existing files modified
- [x] No live runtime imports of new files
- [x] Sprint log committed
- [x] `CURRENT-SPRINT.md` updated (done in S0 commit)

---

## Next Sprint

**S-REFACTOR-S2** — Account + Instrument Profile Wiring (Tier-2)

Objective: Load `config/accounts.yaml` into typed `AccountProfile` objects;
add `config/instruments.yaml`; expose read-only `coordinator.account_profiles`
and `coordinator.instrument_profiles` properties. Requires merge review before
merging to main (touches `src/core/coordinator.py`).

Gate: Ben reviews S1 PR before S2 begins.
