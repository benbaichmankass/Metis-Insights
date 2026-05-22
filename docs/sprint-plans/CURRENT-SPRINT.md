# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`  
**Last updated:** 2026-05-22 (MES paper trading live)

---

## STATUS: COMPLETE

**M11 COMPLETE.** All planned sprints (S0–S11) merged to main. **S7-IB closed:**
IB/MES execution wired 2026-05-21 and **MES paper trading went live 2026-05-22**
(multi-symbol BTCUSDT + MES, all 3 strategies, delayed CME data; PRs #1706, #1712).
See `docs/sprint-logs/S-MES-GOLIVE-2026-05-22.md`.

---

## LAST_COMPLETED (this session)

- S-REFACTOR-S0 — Multi-strategy architecture planning docs created. ROADMAP.md updated with M11. (2026-05-20, Tier-1)
- S-REFACTOR-S1 — Core abstractions scaffolded in `src/core/`: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator. 17 tests. (2026-05-20, Tier-1)
- S-REFACTOR-S2 — Account + instrument profile wiring. `config/instruments.yaml` added. `src/core/profile_loader.py` added. `AccountProfile.from_dict()` fixed for actual accounts.yaml schema. `coordinator.account_profiles` + `coordinator.instrument_profiles` properties added. 15 tests. (2026-05-20, Tier-2)
- S-REFACTOR-S3 — SignalPackage wired into all 3 strategy signal builders. `_with_signal_package()` helper. 20 tests. Purely additive — live dict shape unchanged. (2026-05-20, Tier-2)
- S-REFACTOR-S4 — AllocatorInterface wired into coordinator. `coordinator.allocator` property + `coordinator.build_order_packages()` method. 19 tests. Live path unchanged. (2026-05-20, Tier-2)
- S-REFACTOR-S5 — CENTRALIZED_ALLOCATOR feature flag (shadow mode). `_centralized_allocator_enabled()` in `runtime_flags.py`. Shadow audit block in `pipeline.py` multi-account dispatch. Default off — live runtime unaffected. 10 tests. (2026-05-20, Tier-3, PM-approved)
- S-REFACTOR-S6 — CENTRALIZED_ALLOCATOR primary path. When flag is on + signal has typed SignalPackage: build coordinator OrderPackage from typed fields (not raw dict). Allocator qty logged. Fallback to raw dict path when flag off or signal_package absent. 14 tests. (2026-05-20, Tier-3, PM-approved)
- S-REFACTOR-S7 — `multi_account_execute_typed()` on Coordinator + pipeline S7 typed dispatch. PR #1604. 13 tests. (2026-05-20, Tier-2, merged)
- S-REFACTOR-S8 — `PortfolioState` typed snapshot + net position accounting. PR #1605. 26 tests. (2026-05-20, Tier-2, merged)
- S-REFACTOR-S9 — `StrategyBase` class in `_base.py` aligned with `StrategyInterface` (S1-NOTE-003). 29 tests. (2026-05-20, Tier-1, merged)
- S-REFACTOR-S10 — ML decision-layer advisory hooks. `Coordinator.log_advisory_scores()` → `advisory_decisions.jsonl`. Diag endpoint extended with `advisory_decisions` log key. (2026-05-20, Tier-1, merged)
- S-REFACTOR-S11 — Attribution API: `GET /api/bot/positions/net` + `GET /api/bot/strategy/attribution`. PR #1608. ICT filter module public API (`src/ict_detection/__init__.py`, 20 tests) landed alongside (PR #1609). Health-review skill + template updated with 4 new M11 dimensions (PR #1610). (2026-05-20, Tier-1, merged)

---

## OPEN ITEMS FROM PRIOR ROADMAP

- Sprint 8 (S-OPS-COMMENT-RACE-FIX) — draft PR open; Ben's ack required before merge
- PR #1026 (circuit breaker removal + linear perps margin fix) — Ben's approval required (Tier-3)
- FU-20260518-001 — VWAP performance tracking post-policy-gate; monitoring only
- **M11 S7 IB/MES** — ✅ done; MES paper trading live 2026-05-22. Follow-ups: MES-specific ML models (needs accumulated MES paper trades), live `ib_live` promotion (Tier-3).

---

## READY_TO_CONTINUE

M11 is done. No pending M11 work.

Next milestone TBD (likely M6 dashboard — BTCUSDT + MES performance tabs with
TradingView-style signal/TP/SL/PnL overlays — and MES model training now that
MES paper trades are accumulating).

---

## Key planning docs

- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`
- `docs/architecture/multi-strategy-architecture-target.md`
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` through `S-REFACTOR-S11-2026-05-20.md`

---

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking post policy gate | Watch only |
| PR #1026 | Circuit breaker removal + linear perps margin fix | Needs Ben's review |
| M11-S7-IB | IB/MES execution + MES paper go-live | ✅ Done 2026-05-22 (#1706, #1712) |
