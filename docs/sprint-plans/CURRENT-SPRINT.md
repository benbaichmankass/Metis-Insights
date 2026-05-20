# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-20 (S-REFACTOR-S7 in progress)

---

## STATUS: AWAITING BEN'S REVIEW

**LAST_COMPLETED (this session):**
- S-REFACTOR-S0 — Multi-strategy architecture planning docs created. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. 17 tests. (2026-05-20, Tier-1)
- S-REFACTOR-S2 — Account + instrument profile wiring. `config/instruments.yaml` added. `src/core/profile_loader.py` added. `AccountProfile.from_dict()` fixed for actual accounts.yaml schema. `coordinator.account_profiles` + `coordinator.instrument_profiles` properties added. 15 tests. (2026-05-20, Tier-2)
- S-REFACTOR-S3 — SignalPackage wired into all 3 strategy signal builders. `_with_signal_package()` helper. 20 tests. Purely additive — live dict shape unchanged. (2026-05-20, Tier-2)
- S-REFACTOR-S4 — AllocatorInterface wired into coordinator. `coordinator.allocator` property + `coordinator.build_order_packages()` method. 19 tests. Live path unchanged. (2026-05-20, Tier-2)
- S-REFACTOR-S5 — CENTRALIZED_ALLOCATOR feature flag (shadow mode). `_centralized_allocator_enabled()` in `runtime_flags.py`. Shadow audit block in `pipeline.py` multi-account dispatch. Default off — live runtime unaffected. 10 tests. (2026-05-20, Tier-3, PM-approved)
- S-REFACTOR-S6 — CENTRALIZED_ALLOCATOR primary path. When flag is on + signal has typed SignalPackage: build coordinator OrderPackage from typed fields (not raw dict). Allocator qty logged. Fallback to raw dict path when flag off or signal_package absent. 14 tests. (2026-05-20, Tier-3, PM-approved)
- S-REFACTOR-S7 — `multi_account_execute_typed()` on Coordinator + pipeline S7 typed dispatch. PR #1604 open. 13 tests. (2026-05-20, Tier-2, awaiting review)

**OPEN ITEMS FROM PRIOR ROADMAP (`ROADMAP-2026-05-19.md`):**
- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only

**READY_TO_CONTINUE:**
Next: **S-REFACTOR-S8** — net position accounting / multi-strategy sizing. Requires S7 merged first.

**Key planning docs:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`
- `docs/architecture/multi-strategy-architecture-target.md`
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` through `S-REFACTOR-S7-2026-05-20.md`

---

## S7 Verification Checklist

- [x] `Coordinator.multi_account_execute_typed()` added — converts typed OrderPackage → legacy, delegates to `multi_account_execute`
- [x] Flat/`side=none` packages skipped; allocator qty stored in `meta['allocator_qty']`
- [x] Pipeline S7 dispatch block: typed path when `CENTRALIZED_ALLOCATOR=true` + `signal_package.is_actionable`; fallback to legacy path otherwise
- [x] 13 tests (8 unit + 5 pipeline integration)
- [ ] CI green (ruff, pytest-collect)

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| S1-NOTE-003 | `src/units/strategies/_base.py` alignment with StrategyInterface | Before S8 |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review |
