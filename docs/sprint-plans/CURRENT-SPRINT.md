# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-20 (S-REFACTOR-S9 in progress)

---

## STATUS: IN PROGRESS

**LAST_COMPLETED (this session):**
- S-REFACTOR-S0 — Multi-strategy architecture planning docs created. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. 17 tests. (2026-05-20, Tier-1)
- S-REFACTOR-S2 — Account + instrument profile wiring. `config/instruments.yaml` added. `src/core/profile_loader.py` added. `AccountProfile.from_dict()` fixed for actual accounts.yaml schema. `coordinator.account_profiles` + `coordinator.instrument_profiles` properties added. 15 tests. (2026-05-20, Tier-2)
- S-REFACTOR-S3 — SignalPackage wired into all 3 strategy signal builders. `_with_signal_package()` helper. 20 tests. Purely additive — live dict shape unchanged. (2026-05-20, Tier-2)
- S-REFACTOR-S4 — AllocatorInterface wired into coordinator. `coordinator.allocator` property + `coordinator.build_order_packages()` method. 19 tests. Live path unchanged. (2026-05-20, Tier-2)
- S-REFACTOR-S5 — CENTRALIZED_ALLOCATOR feature flag (shadow mode). `_centralized_allocator_enabled()` in `runtime_flags.py`. Shadow audit block in `pipeline.py` multi-account dispatch. Default off — live runtime unaffected. 10 tests. (2026-05-20, Tier-3, PM-approved)
- S-REFACTOR-S6 — CENTRALIZED_ALLOCATOR primary path. When flag is on + signal has typed SignalPackage: build coordinator OrderPackage from typed fields (not raw dict). Allocator qty logged. Fallback to raw dict path when flag off or signal_package absent. 14 tests. (2026-05-20, Tier-3, PM-approved)
- S-REFACTOR-S7 — `multi_account_execute_typed()` on Coordinator + pipeline S7 typed dispatch. PR #1604. 13 tests. (2026-05-20, Tier-2, merged)
- S-REFACTOR-S8 — `PortfolioState` typed snapshot + net position accounting. PR #1605. 26 tests. (2026-05-20, Tier-2, merged)
- S-REFACTOR-S9 — `StrategyBase` class in `_base.py` aligned with `StrategyInterface` (S1-NOTE-003). 29 tests. (2026-05-20, Tier-1, in progress)

**OPEN ITEMS FROM PRIOR ROADMAP (`ROADMAP-2026-05-19.md`):**
- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only

**READY_TO_CONTINUE:**
Next: **S-REFACTOR-S10** — ML decision-layer advisory hooks (ROADMAP S5). Shadow adapter `advisory_flag` output + coordinator advisory hook (noop in production).

**Key planning docs:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`
- `docs/architecture/multi-strategy-architecture-target.md`
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` through `S-REFACTOR-S9-2026-05-20.md`

---

## S9 Verification Checklist

- [x] `StrategyBase` class added to `src/units/strategies/_base.py` — inherits `StrategyInterface`
- [x] `strategy_id` and `_category` class attributes; `category` property
- [x] Static helper methods delegate to module-level functions (zero duplication)
- [x] `build_signal` / `build_order_package` raise `NotImplementedError` on base
- [x] All existing module-level helpers unchanged (backward-compatible)
- [x] 29 tests (helpers regression + class inheritance + concrete subclass)
- [ ] CI green (ruff, pytest-collect)

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review |
