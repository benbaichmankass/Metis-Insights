# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-20 (S-REFACTOR-S3 complete)

---

## STATUS: AWAITING BEN'S REVIEW

**LAST_COMPLETED (this session):**
- S-REFACTOR-S0 — Multi-strategy architecture planning docs created. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. 17 tests. (2026-05-20, Tier-1)
- S-REFACTOR-S2 — Account + instrument profile wiring. `config/instruments.yaml` added. `src/core/profile_loader.py` added. `AccountProfile.from_dict()` fixed for actual accounts.yaml schema. `coordinator.account_profiles` + `coordinator.instrument_profiles` properties added. 15 tests. (2026-05-20, Tier-2)
- S-REFACTOR-S3 — SignalPackage wired into all 3 strategy signal builders. `_with_signal_package()` helper added. 20 tests. Purely additive — live dict shape unchanged. (2026-05-20, Tier-2)

**OPEN ITEMS FROM PRIOR ROADMAP (`ROADMAP-2026-05-19.md`):**
- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only

**READY_TO_CONTINUE:**
Next: **S-REFACTOR-S4** — Wire `AllocatorInterface` / `PassthroughAllocator` into the
coordinator dispatch path so it consumes `sig["signal_package"]` and produces
`OrderPackage` objects. Gate: read coordinator dispatch logic before touching it.
Tier-2 review before merge.

**Key planning docs:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`
- `docs/architecture/multi-strategy-architecture-target.md`
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` through `S-REFACTOR-S3-2026-05-20.md`

---

## S3 Verification Checklist

- [x] `src/core/signals.py` inspected — no `SignalPackage` name collision
- [x] `_with_signal_package("turtle_soup", {...})` wraps both turtle_soup return paths
- [x] `_with_signal_package("ict_scalp_5m", {...})` wraps all 3 ict_scalp return paths
- [x] `_with_signal_package("vwap", sig)` wraps vwap final return
- [x] All existing dict keys preserved (symbol, side, price, stop_loss, take_profit, meta)
- [x] Side translation correct: buy→long, sell→short, none→none
- [x] `account_id=""` (S4 will bind)
- [x] `raw` excludes `signal_package` key itself
- [x] 20 tests written covering all helper behaviors

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| S1-NOTE-003 | `src/units/strategies/_base.py` alignment with StrategyInterface | Before S5 |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review |
