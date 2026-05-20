# Sprint Log: S-REFACTOR-S2

**Sprint ID:** S-REFACTOR-S2
**Sprint Title:** Account + Instrument Profile Wiring
**Date:** 2026-05-20
**Status:** COMPLETE
**Tier:** Tier-2 (coordinator.py touched — merge review required)
**Roadmap:** [ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md](../sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md)

---

## Objectives

Wire the S1 abstract types (`AccountProfile`, `InstrumentProfile`) into actual config
loading. Add `config/instruments.yaml`. Expose typed profile views through the
coordinator as read-only properties.

---

## Files Created

| File | Purpose |
|------|-------|
| `config/instruments.yaml` | Instrument specifications for BTCUSDT/Bybit + MES/IB |
| `src/core/profile_loader.py` | Standalone `load_account_profiles()` + `load_instrument_profiles()` |
| `tests/test_s2_profile_wiring.py` | Tests for schema mapping, loader, and coordinator properties |

## Files Modified

| File | Change |
|------|------|
| `src/core/account_profile.py` | Fixed `from_dict()` for actual accounts.yaml schema (S2 schema fix) |
| `src/core/coordinator.py` | Added `account_profiles` + `instrument_profiles` properties; added `instruments_path` param to `__init__` |

---

## S2 Schema Fix: accounts.yaml → AccountProfile mapping

The S1 `from_dict()` implementation used `data.get("dry_run", True)` (a bool field),
but actual `config/accounts.yaml` uses `mode: live | dry_run` (a string field) and
a separate `demo: true` bool for the Bybit demo endpoint.

Fixed mapping (S2):
- `mode: live` → `dry_run=False`; `mode: dry_run` → `dry_run=True`
- `demo: true` → `demo=True` (new field on AccountProfile)
- `demo=True` overrides account_type to `bybit_demo` regardless of `mode`

Account mapping for current config:
| account_id | demo | mode | → dry_run | account_type |
|---|---|---|---|---|
| bybit_1 | true | live | False | bybit_demo |
| bybit_2 | (none) | live | False | bybit_live |
| prop_velotrade_1 | (none) | dry_run | True | unknown |

---

## Design Decisions

### profile_loader.py as standalone module
The profile loaders are a standalone module (`src/core/profile_loader.py`) rather
than inlining the logic directly in coordinator.py. This makes them:
- Independently importable and testable without coordinator instantiation
- Reusable in scripts, Streamlit pages, and future allocator code (S4/S5)
- The coordinator properties are thin delegations to the loader (3 lines each)

### Fallback behavior for missing instruments.yaml
`load_instrument_profiles()` returns the pre-built `BTCUSDT/Bybit` profile when
`instruments.yaml` is not found. This preserves current behavior during deployment
gaps (e.g., if instruments.yaml is not yet on the VM).

### coordinator.__init__ instruments_path parameter
Added as a keyword-only default parameter: `instruments_path: str = _INSTRUMENTS_YAML`.
Fully backward-compatible — all existing Coordinator instantiation sites are unaffected.

---

## Test Summary

| Test Class | Count | Coverage |
|---|---|---|
| `TestAccountProfileSchemaFix` | 4 | mode/demo field mapping, velotrade unknown, missing mode default |
| `TestLoadAccountProfiles` | 4 | three accounts loaded, bybit_1 demo, bybit_2 live, missing file |
| `TestLoadInstrumentProfiles` | 4 | two instruments, BTCUSDT spec, MES spec, missing file fallback |
| `TestCoordinatorProfileProperties` | 3 | account types, instrument fallback, instrument from yaml |
| **Total** | **15** | |

---

## Definition of Done — S2

- [x] `config/instruments.yaml` committed (BTCUSDT + MES)
- [x] `src/core/profile_loader.py` committed
- [x] `src/core/account_profile.py` updated (mode/demo schema fix)
- [x] `src/core/coordinator.py` updated (account_profiles + instrument_profiles properties)
- [x] `tests/test_s2_profile_wiring.py` committed (15 tests)
- [x] No existing public method signatures changed
- [x] coordinator `__init__` change is backward-compatible (keyword default)
- [x] Sprint log committed

---

## Next Sprint

**S-REFACTOR-S3** — Strategy Signal Builder Wiring (Tier-2)

Objective: Wire `SignalPackage` as the output type for the three strategy signal
builders (`vwap`, `turtle_soup`, `ict_scalp_5m`) in `src/runtime/strategy_signal_builders.py`.
Must inspect `src/core/signals.py` first to confirm no export-name overlap with
`signal_contract.py` before wiring.

Gate: Ben reviews S2 PR before S3 begins.
