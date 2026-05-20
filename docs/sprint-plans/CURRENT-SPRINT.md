# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-20 (S-REFACTOR-S2 complete)

---

## STATUS: AWAITING BEN'S REVIEW

**LAST_COMPLETED (this session):**
- S-REFACTOR-S0 — Multi-strategy architecture planning docs created. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. 17 tests. (2026-05-20, Tier-1)
- S-REFACTOR-S2 — Account + instrument profile wiring. `config/instruments.yaml` added. `src/core/profile_loader.py` added. `AccountProfile.from_dict()` fixed for actual accounts.yaml schema. `coordinator.account_profiles` + `coordinator.instrument_profiles` properties added. 15 tests. (2026-05-20, Tier-2)

**OPEN ITEMS FROM PRIOR ROADMAP (`ROADMAP-2026-05-19.md`):**
- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only

**READY_TO_CONTINUE:**
Next: **S-REFACTOR-S3** — Wire `SignalPackage` as output type for the three strategy signal builders in `src/runtime/strategy_signal_builders.py`. Must inspect `src/core/signals.py` first. Tier-2 review before merge.

**Key planning docs:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`
- `docs/architecture/multi-strategy-architecture-target.md`
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` through `S-REFACTOR-S2-2026-05-20.md`

---

## S2 Verification Checklist

- [x] `config/instruments.yaml` added with BTCUSDT + MES specs
- [x] `load_account_profiles()` returns typed `AccountProfile` objects from accounts.yaml
- [x] `load_instrument_profiles()` falls back to pre-built BTCUSDT when instruments.yaml missing
- [x] `AccountProfile.from_dict()` maps `mode: live` → `dry_run=False` correctly
- [x] `AccountProfile.from_dict()` maps `demo: true` → `account_type=bybit_demo` correctly
- [x] bybit_1 (demo=true, mode=live) → `bybit_demo`, `is_live=True`, `dry_run=False`
- [x] bybit_2 (mode=live) → `bybit_live`, `is_live=True`, `dry_run=False`
- [x] coordinator `__init__` change is backward-compatible
- [x] coordinator properties are read-only delegation — no side effects

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| S1-NOTE-001 | Inspect `src/core/signals.py` before S3 wiring | **Before S3** |
| S1-NOTE-002 | `src/strategy_registry.py` naming — distinct name needed in S3 | Before S3 |
| S1-NOTE-003 | `src/units/strategies/_base.py` alignment with StrategyInterface | Before S3 |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review |
