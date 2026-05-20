# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-20 (S-REFACTOR-S2 complete)

---

## STATUS: AWAITING BEN'S REVIEW

**LAST_COMPLETED (this session):**
- S-REFACTOR-S0 — Multi-strategy architecture planning docs created: phase roadmap, architecture target doc, sprint logs. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. Tests written. (2026-05-20, Tier-1)
- S-REFACTOR-S2 — Account + instrument profile wiring. `config/instruments.yaml` added. `src/core/profile_loader.py` added. `AccountProfile.from_dict()` fixed for actual accounts.yaml schema (mode/demo fields). `coordinator.account_profiles` + `coordinator.instrument_profiles` read-only properties added. 15 tests. (2026-05-20, Tier-2)

**OPEN ITEMS FROM PRIOR ROADMAP (`ROADMAP-2026-05-19.md`):**
- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge (Tier-2 CI workflow change)
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only

**READY_TO_CONTINUE:**
Next: **S-REFACTOR-S3** — Wire `SignalPackage` as output type for the three strategy signal builders in `src/runtime/strategy_signal_builders.py`. Must inspect `src/core/signals.py` first to confirm no name overlap. Tier-2 review before merge.

**Key planning docs for this initiative:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md` — phase roadmap (S0-S8)
- `docs/architecture/multi-strategy-architecture-target.md` — architecture target reference
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` — S0 sprint log
- `docs/sprint-logs/S-REFACTOR-S1-2026-05-20.md` — S1 sprint log
- `docs/sprint-logs/S-REFACTOR-S2-2026-05-20.md` — S2 sprint log

---

## What was done in this session

### S-REFACTOR-S0 (documentation only, Tier-1)
- Inspected full repo structure: `src/`, `config/`, `docs/`, all strategy modules, runtime modules, ICT detection modules, ML layer
- Created `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md` — master phase roadmap with S0-S8 sprints, risk register, DoD for S1, decision-tier rules
- Created `docs/architecture/multi-strategy-architecture-target.md` — architecture target grounded in actual repo file paths
- Updated `ROADMAP.md` — added M11 milestone, S-REFACTOR-S0/S1 sprint ledger entries

### S-REFACTOR-S1 (scaffolding, Tier-1)
- Created 6 new abstract type files in `src/core/`:
  - `account_profile.py` — `AccountProfile` frozen dataclass with `from_dict()`, IB/Bybit detection
  - `instrument_profile.py` — `InstrumentProfile` frozen dataclass with pre-built BTCUSDT + MES profiles
  - `signal_contract.py` — `SignalPackage` with `is_actionable`, `with_account()`
  - `order_contract.py` — `OrderPackage` with `from_signal()` attribution builder
  - `strategy_interface.py` — `StrategyInterface` ABC
  - `allocator.py` — `AllocatorInterface` ABC + `PassthroughAllocator`
- Created `tests/test_s1_abstractions.py` — 17 tests
- No existing files modified.

### S-REFACTOR-S2 (profile wiring, Tier-2)
- `config/instruments.yaml` — BTCUSDT/Bybit + MES/IB instrument specs
- `src/core/profile_loader.py` — standalone `load_account_profiles()` + `load_instrument_profiles()`
- `src/core/account_profile.py` — S2 schema fix: `from_dict()` now uses `mode: live|dry_run` + `demo: true` fields (matching actual accounts.yaml); added `demo` field to dataclass
- `src/core/coordinator.py` — added `account_profiles` + `instrument_profiles` read-only properties (delegate to profile_loader); added `instruments_path` param to `__init__` (backward-compatible)
- `tests/test_s2_profile_wiring.py` — 15 tests (schema fix, loaders, coordinator smoke)

---

## S2 Verification Checklist

- [x] `config/instruments.yaml` added with BTCUSDT + MES specs
- [x] `load_account_profiles()` returns typed `AccountProfile` objects from accounts.yaml
- [x] `load_instrument_profiles()` falls back to pre-built BTCUSDT profile if instruments.yaml missing
- [x] `AccountProfile.from_dict()` maps `mode: live` → `dry_run=False` correctly
- [x] `AccountProfile.from_dict()` maps `demo: true` → `account_type=bybit_demo` correctly
- [x] bybit_1 (demo=true, mode=live) → `bybit_demo`, `is_live=True`, `dry_run=False`
- [x] bybit_2 (mode=live) → `bybit_live`, `is_live=True`, `dry_run=False`
- [x] coordinator `__init__` change is backward-compatible (keyword default)
- [x] coordinator properties are read-only delegation — no side effects

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| S1-NOTE-001 | `src/core/signals.py` content not fully inspected — verify no semantic overlap with `signal_contract.py` before S3 wiring | **Before S3** |
| S1-NOTE-002 | `src/strategy_registry.py` is ML model registry not strategy registry — distinct name needed in S3 | Before S3 |
| S1-NOTE-003 | `src/units/strategies/_base.py` partially aligned with `StrategyInterface` — alignment is S3 work | Before S3 |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review (Tier-3) |
