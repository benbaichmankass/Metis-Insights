# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-20 (S-REFACTOR-S0 + S-REFACTOR-S1 complete)

---

## STATUS: AWAITING BEN'S REVIEW

**LAST_COMPLETED (this session):**
- S-REFACTOR-S0 — Multi-strategy architecture planning docs created: phase roadmap, architecture target doc, sprint logs. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. Tests written. (2026-05-20, Tier-1)

**OPEN ITEMS FROM PRIOR ROADMAP (`ROADMAP-2026-05-19.md`):**
- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge (Tier-2 CI workflow change)
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only

**READY_TO_CONTINUE:**
Next: **S-REFACTOR-S2** — Load `config/accounts.yaml` into typed `AccountProfile` objects. Add `config/instruments.yaml`. Add read-only `coordinator.account_profiles` and `coordinator.instrument_profiles` properties. Ping Ben for Tier-2 review before merging.

**Key planning docs for this initiative:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md` — phase roadmap (S0-S8)
- `docs/architecture/multi-strategy-architecture-target.md` — architecture target reference
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` — S0 sprint log
- `docs/sprint-logs/S-REFACTOR-S1-2026-05-20.md` — S1 sprint log

---

## What was done in this session

### S-REFACTOR-S0 (documentation only, Tier-1)
- Inspected full repo structure: `src/`, `config/`, `docs/`, all strategy modules, runtime modules, ICT detection modules, ML layer
- Created `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md` — master phase roadmap with S0-S8 sprints, risk register, DoD for S1, decision-tier rules
- Created `docs/architecture/multi-strategy-architecture-target.md` — architecture target grounded in actual repo file paths
- Updated `ROADMAP.md` — added M11 milestone, S-REFACTOR-S0/S1 sprint ledger entries
- Updated `CURRENT-SPRINT.md` (this file)

### S-REFACTOR-S1 (scaffolding, Tier-1)
- Created 6 new abstract type files in `src/core/`:
  - `account_profile.py` — `AccountProfile` frozen dataclass with `from_dict()`, IB/Bybit detection
  - `instrument_profile.py` — `InstrumentProfile` frozen dataclass with pre-built BTCUSDT + MES profiles
  - `signal_contract.py` — `SignalPackage` with `is_actionable`, `with_account()`
  - `order_contract.py` — `OrderPackage` with `from_signal()` attribution builder
  - `strategy_interface.py` — `StrategyInterface` ABC with `build_signal()`, `build_order_package()`, `category` property
  - `allocator.py` — `AllocatorInterface` ABC + `PassthroughAllocator` (identity allocator preserving current sizing behavior)
- Created `tests/test_s1_abstractions.py` — 12 tests covering all new types
- No existing files modified. Zero live path changes.

---

## S1 Verification Checklist

- [x] Only new files added — no existing file modified
- [x] `AccountProfile` correctly detects Bybit vs IB exchange from raw account dict
- [x] `InstrumentProfile` provides pre-built BTCUSDT/Bybit and MES/IB profiles
- [x] `SignalPackage.is_actionable` gates on side != "none" AND entry_price not None
- [x] `OrderPackage.from_signal()` preserves raw signal attribution
- [x] `PassthroughAllocator` filters out non-actionable signals
- [x] `PassthroughAllocator` computes qty = balance * risk_pct / sl_distance (current behavior)
- [x] Frozen dataclasses raise AttributeError on mutation attempt

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| S1-NOTE-001 | `src/core/signals.py` content not fully inspected — verify no semantic overlap with `signal_contract.py` before S3 wiring | Before S3 |
| S1-NOTE-002 | `src/strategy_registry.py` is ML model registry not strategy registry — new name or file needed in S3 | Before S3 |
| S1-NOTE-003 | `src/units/strategies/_base.py` partially aligned with `StrategyInterface` — full alignment is S3 work | Before S3 |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review (Tier-3) |
