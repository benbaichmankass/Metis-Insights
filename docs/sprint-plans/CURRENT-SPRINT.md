# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-20 (S-REFACTOR-S4 complete)

---

## STATUS: AWAITING BEN'S REVIEW

**LAST_COMPLETED (this session):**
- S-REFACTOR-S0 — Multi-strategy architecture planning docs created. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. 17 tests. (2026-05-20, Tier-1)
- S-REFACTOR-S2 — Account + instrument profile wiring. `config/instruments.yaml` added. `src/core/profile_loader.py` added. `AccountProfile.from_dict()` fixed for actual accounts.yaml schema. `coordinator.account_profiles` + `coordinator.instrument_profiles` properties added. 15 tests. (2026-05-20, Tier-2)
- S-REFACTOR-S3 — SignalPackage wired into all 3 strategy signal builders. `_with_signal_package()` helper. 20 tests. Purely additive — live dict shape unchanged. (2026-05-20, Tier-2)
- S-REFACTOR-S4 — AllocatorInterface wired into coordinator. `coordinator.allocator` property + `coordinator.build_order_packages()` method. 19 tests. Live path unchanged. (2026-05-20, Tier-2)

**OPEN ITEMS FROM PRIOR ROADMAP (`ROADMAP-2026-05-19.md`):**
- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only

**READY_TO_CONTINUE:**
Next: **S-REFACTOR-S5** — `CENTRALIZED_ALLOCATOR` feature flag: opt-in routing through
`coordinator.build_order_packages()` in the pipeline entrypoint. **Tier-3 — requires
explicit PM approval before merge.** Gate: read pipeline.py first to understand
the exact call site before proposing any change.

**Key planning docs:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`
- `docs/architecture/multi-strategy-architecture-target.md`
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` through `S-REFACTOR-S4-2026-05-20.md`

---

## S4 Verification Checklist

- [x] `multi_account_execute` unchanged (live order path preserved)
- [x] `strategy_order_pkg` unchanged
- [x] `account_execute` unchanged
- [x] `coordinator.allocator` lazy-inits `PassthroughAllocator` on first call
- [x] `coordinator.allocator` returns same instance on repeated calls
- [x] `coordinator.build_order_packages()` delegates to allocator.allocate()
- [x] `PassthroughAllocator` qty formula: `(balance * risk_pct) / sl_distance`
- [x] Non-actionable signals return empty list
- [x] 19 tests written

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| S1-NOTE-003 | `src/units/strategies/_base.py` alignment with StrategyInterface | Before S6 |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review |
| S5-GATE | Read pipeline.py before any S5 code; Tier-3 approval required | **Before S5** |
