# Sprint Roadmap: Multi-Strategy Architecture Refactor
**Created:** 2026-05-20  
**Initiative ID:** MULTI-STRATEGY-ARCH-REFACTOR  
**ROADMAP.md milestone:** M11  
**Companion doc:** `docs/architecture/multi-strategy-architecture-target.md`  
**Status tracking:** See `docs/sprint-plans/CURRENT-SPRINT.md` for the active sprint.

---

## 1. Current-State Assessment (2026-05-20)

### Active trading path

`src/main.py` is the entry point, running as `ict-trader-live.service` (systemd) on the Oracle VM. Three strategies are live:

| Strategy | Module | Architectural Category | Timeframe | Risk | Status |
|---|---|---|---|---|---|
| `vwap` | `src/units/strategies/vwap.py` | Mean reversion / dislocation | 5m BTCUSDT | 1.0% | Live |
| `turtle_soup` | `src/units/strategies/turtle_soup.py` | Trend pullback / continuation | 15m/1m BTCUSDT | 0.5% | Live |
| `ict_scalp_5m` | `src/units/strategies/ict_scalp.py` | Breakout / expansion | 5m BTCUSDT | 0.3% | Live |

Accounts: `bybit_1` (demo, api-demo.bybit.com), `bybit_2` (live, api.bybit.com).

### What is already working

- Three strategies live on Bybit BTCUSDT linear perps with a single-symbol runtime
- `src/runtime/intents.py` + `intent_multiplexer.py` — typed StrategyIntent → DesiredPosition → ExecutionDelta pipeline (S-MSE-1, done)
- `src/runtime/shadow_adapter.py` + `ml/shadow/` — WS7 shadow prediction ladder with per-strategy wiring
- `src/units/accounts/risk.py` — per-account risk management and daily counters
- `src/runtime/boot_audit.py` — boot-time journal/exchange reconciliation
- `src/strategy_registry.py` — model registration (ML models, not trading strategy registry)
- `src/ict_detection/` — fvg_detector, liquidity, order_blocks, swing_points, trend, key_levels (used by ict_scalp)
- `src/core/coordinator.py` (95KB) — central coordinator managing the tick loop
- `src/units/strategies/_base.py` — partial base class (exists but not consistently used)

### What is missing for the target architecture

| Gap | Location | Sprint |
|---|---|---|
| No formal AccountProfile type | `src/units/accounts/account.py` exists but not typed-abstracted | S2 |
| No formal InstrumentProfile type | BTCUSDT hardcoded; multi-symbol aspirational | S2 |
| No centralized allocator | Each strategy sizes independently via risk.py | S4 |
| No normalized signal package contract | Ad-hoc dicts between signal builders and executor | S3 |
| No normalized order package contract | StrategyIntent/DesiredPosition exist but attribution is incomplete | S3 |
| No net portfolio position accounting | Positions tracked per account but not cross-strategy netted | S4 |
| ICT/FVG/OB not formalized as filter module | `src/ict_detection/` exists but has no clean public API | S8 |
| ML decision layer not formally separated | shadow_adapter exists but advisory/decision modes not wired | S5 |
| No IB/MES execution path | Bybit-only | S7 |

### Strategy category naming

The three live strategies already represent the three target architectural categories. This refactor gives them proper scaffolding — it does not replace or rename the strategies themselves.

- `vwap` → **mean reversion / dislocation**: price deviates from VWAP by σ-threshold; strategy trades the reversion
- `turtle_soup` → **trend pullback / continuation**: sweeps structural highs/lows then enters the reversal in the direction of the prevailing trend; this is a pullback-entry pattern
- `ict_scalp_5m` → **breakout / expansion**: displacement bar + FVG mitigation following a liquidity sweep; displacement IS the breakout, FVG mitigation IS the expansion entry

The fourth component (ICT/FVG/OB-style signal/filter module) is planned as S8 — a formalization of `src/ict_detection/` as a reusable module available to all strategies. It is **not** a standalone fourth strategy.

---

## 2. Refactor Goals

### Non-negotiable constraints (all sprints)

- Do not break the current live Bybit runtime
- Do not reset the VM
- Do not stop the live trader
- Do not overwrite existing deployment state
- Do not paste secrets into commits, docs, or chat
- Do not promote any new strategy or model behavior to live
- Do not change strategy parameters or thresholds without explicit PM approval
- Do not do long training or heavy backtests inside Claude Code

### Architecture goals (priority order)

1. Account and instrument profile abstraction (S2)
2. Normalized signal and order package contracts (S3)
3. Centralized allocator with adaptive sizing (S4)
4. Net portfolio position accounting (S4)
5. ML as decision layer, not signal generator (S5)
6. IB/MES as a second account class — design now, implement later (S7)
7. Minimal Streamlit / dashboard transparency additions (S6)
8. ICT/FVG/OB as a reusable filter module — formalize existing code (S8)

---

## 3. Sprint Roadmap

### S0 — Planning and documentation
**ID:** S-REFACTOR-S0  
**Type:** Tier-1 autonomous (documentation only)  
**Status:** ✅ COMPLETE (2026-05-20)  
**Objective:** Create planning and documentation artifacts before any code refactor begins.

**Deliverables:**
- `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md` (this file)
- `docs/architecture/multi-strategy-architecture-target.md`
- `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md`
- `ROADMAP.md` updated with M11 milestone
- `docs/sprint-plans/CURRENT-SPRINT.md` updated

**Constraints:** No production behavior changes. No code refactor. No strategy logic touched.

---

### S1 — Architecture audit + scaffolding start
**ID:** S-REFACTOR-S1  
**Type:** Tier-1 autonomous (new files only; no existing file modified)  
**Status:** ✅ COMPLETE (2026-05-20)  
**Objective:** Add foundational abstract types and interfaces as new files in `src/core/`. No existing code touched.

**Deliverables:**
- `src/core/account_profile.py` — typed `AccountProfile` dataclass
- `src/core/instrument_profile.py` — typed `InstrumentProfile` dataclass
- `src/core/signal_contract.py` — normalized `SignalPackage` dataclass
- `src/core/order_contract.py` — normalized `OrderPackage` dataclass
- `src/core/strategy_interface.py` — `StrategyInterface` ABC
- `src/core/allocator.py` — `AllocatorInterface` ABC + `PassthroughAllocator` stub
- `tests/test_s1_abstractions.py` — 12 tests covering all new types
- `docs/sprint-logs/S-REFACTOR-S1-2026-05-20.md`

**Explicit non-goals for S1:**
- Do not modify any existing file in `src/units/strategies/`
- Do not modify `src/runtime/` modules
- Do not modify `src/core/coordinator.py`
- Do not wire new abstractions into the live Coordinator
- Do not add new config fields that change runtime behavior
- Do not change any `config/` files
- Do not promote new live logic
- Do not add prop-specific behavior
- Do not fully implement allocator intelligence
- Do not let ML generate independent signals

**Verification:** All existing tests pass. New S1 tests pass. No existing file was modified.

---

### S2 — Account and instrument abstraction foundations
**ID:** S-REFACTOR-S2  
**Type:** Tier-1 / Tier-2 (config loading; read-only Coordinator accessor only)  
**Status:** ✅ COMPLETE (2026-05-20)  
**Objective:** Load `config/accounts.yaml` into typed `AccountProfile` objects. Load instruments from a new `config/instruments.yaml`. Provide read-only accessors from `Coordinator` without changing execution behavior.

**Key files to touch:**
- `config/accounts.yaml` — read but not modified; new typed loader
- `config/instruments.yaml` — new file defining known instruments
- `src/units/accounts/account.py` — add `AccountProfile.from_account_dict()`
- `src/units/accounts/__init__.py` — export loader
- `src/core/coordinator.py` — add `account_profiles` and `instrument_profiles` read-only properties

**Gate:** `Coordinator` getter only — no execution path uses the new types yet. The accounts list the Coordinator actually executes against remains driven by the existing `accounts.yaml` loading code.

**Definition of done:** `Coordinator` exposes typed account and instrument profiles. Existing test suite passes with no regressions. New tests for the typed loaders.

---

### S3 — Strategy registry + signal/order contracts
**ID:** S-REFACTOR-S3  
**Type:** Tier-1 (new types only; no live path change)  
**Status:** ✅ COMPLETE (2026-05-20)  
**Objective:** Formalize the strategy registry so each strategy is registered with an ID and exposes typed signal and order package builders. Introduce `SignalPackage` and `OrderPackage` as the canonical exchange format between pipeline stages.

**Key files to touch:**
- `src/strategy_registry.py` — extend to register strategy module + category
- `src/runtime/strategy_signal_builders.py` — wrap existing signal builders to emit `SignalPackage`
- `src/units/strategies/_base.py` — align with `StrategyInterface` from S1
- `src/runtime/signal_writer.py` — consume `SignalPackage` (typed write path)

**Gate:** Existing strategies continue to work through the existing path. The `SignalPackage` wrapper is additive — it does not replace the dict flow until S4.

**Definition of done:** All three live strategies produce `SignalPackage` instances. Signal writer consumes typed packages. Order packages carry strategy attribution. Tests cover attribution fields.

---

### S4 — Allocator + net position accounting
**ID:** S-REFACTOR-S4  
**Type:** Tier-2 (touches dispatch path; feature-flagged)  
**Status:** ✅ COMPLETE (2026-05-20)  
**Objective:** Wire `PassthroughAllocator` as the default allocator (identity behavior). Introduce net position accounting so portfolio exposure can be netted per account+instrument across all strategies.

**Key files to touch:**
- `src/core/allocator.py` — `PassthroughAllocator` becomes the wired default
- `src/runtime/positions.py` — add net position aggregation across strategies
- `src/runtime/intent_multiplexer.py` — integrate with allocator output
- `src/core/coordinator.py` — `CENTRALIZED_ALLOCATOR` feature flag

**Gate:** Feature flag `CENTRALIZED_ALLOCATOR` (default `false` in production). Pinned `true` in tests. With flag false, existing intent multiplexer path is identical to today.

**Definition of done:** With flag false, current behavior unchanged. With flag true, `PassthroughAllocator` produces identical decisions. Net position accounting emits per-trade attribution to `runtime_logs/allocator_decisions.jsonl`. Tier-2 risk summary provided to operator before merge.

---

### S5 — ML decision-layer refactor hooks
**ID:** S-REFACTOR-S5  
**Type:** Tier-3 (feature flag; PM-approved)  
**Status:** ✅ COMPLETE (2026-05-20, PM-approved)  
**Objective:** Refactor `src/runtime/shadow_adapter.py` to formally distinguish shadow mode (logging only, zero effect) from advisory mode (log + flag to coordinator, still no order change). Add coordinator hooks for the advisory path so the wiring exists when a model reaches advisory stage.

**Key files to touch:**
- `src/runtime/shadow_adapter.py` — add `advisory_flag` output
- `ml/shadow/factory.py` — stage gate for advisory vs shadow
- `src/core/coordinator.py` — advisory hook (noop in production until PM approves)

**Definition of done:** Shadow mode unchanged. Advisory mode flag propagates through coordinator with no order effect. New tests for advisory flag path. No model promotion without operator approval.

---

### S6 — Streamlit / dashboard transparency pass
**ID:** S-REFACTOR-S6  
**Type:** Tier-3 (CENTRALIZED_ALLOCATOR primary path; PM-approved)  
**Status:** ✅ COMPLETE (2026-05-20, PM-approved)  
**Objective:** Add backend-facing structures so the dashboard can show strategy attribution per trade, allocator decisions, net positions per instrument, and shadow vs live state.

**Key files to touch:**
- `src/web/api/routers/` — new router for attribution and allocation endpoints
- `src/runtime/api_reporting.py` — extend with attribution fields

**Non-goal:** No Streamlit component changes. No new dashboard pages. API only.

**Definition of done:** `GET /api/bot/strategy/attribution` returns per-trade strategy attribution. `GET /api/bot/positions/net` returns net positions per account+instrument. Shadow state endpoint extended with attribution fields (builds on WS8 PART-2).

---

### S7 — Typed multi-account dispatch
**ID:** S-REFACTOR-S7  
**Type:** Tier-2 (coordinator + pipeline typed dispatch path)  
**Status:** ✅ COMPLETE (2026-05-20, PR #1604)

### S7-IB — IB/MES shadow integration (later sprint)
**ID:** S-REFACTOR-S7-IB  
**Type:** Tier-2 (new exchange adapter; dry_run only)  
**Status:** ⛔ DEFERRED — no IB credentials in scope yet  
**Objective:** Add an IB `AccountProfile` and a dry-run IB market data adapter. Allow the coordinator to dispatch a shadow copy of Bybit decisions to an IB dry-run account for observation.

**Key files to touch:**
- `src/units/accounts/clients.py` — IB client stub
- `src/core/account_profile.py` — IB profile type already defined in S1

**Gate:** `IB_SHADOW_ENABLED=false` in production. Operator must set true explicitly. No live IB orders until operator explicitly promotes.

**Note:** No prop-account configs in S7. Leave room for them in the account profile schema but do not implement prop-specific behavior.

---

### S8 — PortfolioState typed snapshot
**ID:** S-REFACTOR-S8  
**Type:** Tier-2 (net position accounting; coordinator property)  
**Status:** ✅ COMPLETE (2026-05-20, PR #1605)

### S8-ICT — ICT/FVG/OB signal/filter module
**ID:** S-REFACTOR-S8-ICT  
**Type:** Tier-1 (refactor of existing code; no live behavior change)  
**Status:** ✅ COMPLETE (2026-05-20, PR #1609)  
**Objective:** Formalize `src/ict_detection/` as a reusable signal/filter module with a clean public API. Extract shared ICT detection logic from `ict_scalp.py` into the module. Make the module available to all strategies as a filter layer, not a standalone strategy.

**Key files to touch:**
- `src/ict_detection/__init__.py` — public API surface
- `src/ict_detection/fvg_detector.py`, `liquidity.py`, `order_blocks.py`, `swing_points.py` — clean API
- `src/units/strategies/ict_scalp.py` — migrate to use module public API

**Definition of done:** `src/ict_detection/` exposes a clean public interface. `ict_scalp.py` uses it without behavior change. Other strategies can optionally import FVG/liquidity filters. All existing tests pass.

---

## 4. Decision-Tier Rules

Follows the repo's existing tier definitions from `docs/CLAUDE-RULES-CANONICAL.md`:

### Tier 1 — Autonomous (Claude may implement and self-merge)
- Docs, sprint logs, architecture plans, sprint roadmaps
- New files that are not wired into the live execution path
- Test additions for new scaffolding
- Cleanup of duplicate/stale code with no live path effect
- ML manifest changes at shadow stage only
- API endpoint additions that don't affect order path

### Tier 2 — Merge review required (Claude implements, pings Ben with risk summary)
- Any wiring into the runtime pipeline (even feature-flagged)
- Allocator wiring into Coordinator (S4)
- Services/timers changes
- New exchange adapters (S7)
- Shadow → advisory promotion gate logic changes
- CI/workflow changes affecting operator-action surface

### Tier 3 — Do not merge without PM approval
- Strategy logic changes (`vwap.py`, `turtle_soup.py`, `ict_scalp.py`)
- Signal rules, entry/exit/SL/TP logic in any live strategy
- Position sizing formulas in live strategies
- Promotion logic (`shadow → advisory → limited_live → live_approved`)
- Any ML model promotion
- Dry-run to live promotion for any account
- `config/strategies.yaml` parameter changes that alter trade behavior
- Any strategy enabled/disabled flip

---

## 5. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Breaking current Bybit runtime during wiring | Medium | Critical | Gate all new wiring behind feature flags (S4+); no existing file modified in S1-S2 |
| Mixing signal attribution with portfolio accounting | Medium | High | Keep `SignalPackage` and net position layer strictly separate until S4 |
| Allocator scope creep — trying to implement adaptive sizing too early | High | Medium | `PassthroughAllocator` is the only S4 implementation; adaptive sizing is a separate sub-sprint |
| ML overreach — advisory mode accidentally affecting orders | Low | Critical | Advisory flag is a read-only output; no order path reads it until explicit PM approval |
| UI overbuild before architecture stabilizes | Low | Low | S6 is API-only; zero Streamlit component changes until S7+ |
| Strategy drift during refactor — parameters changing silently | Medium | High | All three live strategies remain unchanged by default in S1-S3; Tier-3 approval required for any parameter change |
| IB integration creating parallel execution confusion | Low | High | IB path has `dry_run=True` enforced; no live IB orders ever without explicit promotion |
| ICT detection module refactor breaking ict_scalp | Medium | High | S8 is a refactor with full test coverage; `ict_scalp.py` kept as-is until S8 is proven in CI |
| Account profile abstraction introducing config loading regressions | Low | High | S2 adds a typed loader alongside the existing loader; existing loader remains active |

---

## 6. Definition of Done for S1

S1 is complete when:

**Scope verification:**
- [ ] Only new files were added to `src/core/` — no existing file was modified
- [ ] Only new test file was added to `tests/` — no existing test was modified
- [ ] All existing tests pass (zero regressions)
- [ ] New S1 tests pass
- [ ] No changes to `config/`, `src/units/strategies/`, `src/runtime/`, or `src/core/coordinator.py`

**Content verification:**
- [ ] `AccountProfile` instantiates correctly from a YAML-dict-like input for both Bybit and IB profile types
- [ ] `InstrumentProfile` provides correct pre-built profiles for BTCUSDT/Bybit and MES/IB
- [ ] `SignalPackage.is_actionable` correctly gates on side + entry_price
- [ ] `OrderPackage.from_signal` preserves attribution from the source signal
- [ ] `PassthroughAllocator` produces zero orders for a `side=none` signal
- [ ] `PassthroughAllocator` produces a correctly-sized order for an actionable signal

---

## 7. Session Continuity

| File | Purpose |
|---|---|
| `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md` | This file — master sprint sequence for the refactor initiative |
| `docs/sprint-plans/CURRENT-SPRINT.md` | Active sprint ID + handoff state (single-file source of truth) |
| `docs/architecture/multi-strategy-architecture-target.md` | Architecture target reference — grounded in actual repo file paths |
| `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` | S0 sprint log |
| `docs/sprint-logs/S-REFACTOR-S1-2026-05-20.md` | S1 sprint log |

### How to mark a sprint complete
1. Write a sprint log to `docs/sprint-logs/S-REFACTOR-SN-YYYY-MM-DD.md` following `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`
2. Update the sprint's status in this file to `✅ COMPLETE` with date
3. Update `CURRENT-SPRINT.md` to point to the next sprint
4. Add any new follow-up items to `comms/follow_ups.json`

---

## 8. Recommended Execution Order for S2 (next)

After S1 is reviewed and merged:

1. Read `config/accounts.yaml` — understand the exact YAML schema for accounts
2. Create `config/instruments.yaml` with BTCUSDT/Bybit entry + MES/IB placeholder
3. Add `AccountProfile.from_account_dict()` loader to `src/units/accounts/account.py`
4. Add `InstrumentProfile.from_dict()` loader to `src/core/instrument_profile.py`
5. Add read-only `coordinator.account_profiles` and `coordinator.instrument_profiles` properties
6. Write tests for the new loaders (do not test live execution path)
7. Ping Ben for Tier-2 review before merging (Coordinator change)

---

## 9. Sprint Status Tracker

| Sprint | ID | Type | Status |
|---|---|---|---|
| S0 | S-REFACTOR-S0 | Tier-1 docs | ✅ COMPLETE (2026-05-20) |
| S1 | S-REFACTOR-S1 | Tier-1 scaffolding | ✅ COMPLETE (2026-05-20) |
| S2 | S-REFACTOR-S2 | Tier-1/2 abstraction wiring | ✅ COMPLETE (2026-05-20) |
| S3 | S-REFACTOR-S3 | Tier-1 contracts | ✅ COMPLETE (2026-05-20) |
| S4 | S-REFACTOR-S4 | Tier-2 allocator | ✅ COMPLETE (2026-05-20) |
| S5 | S-REFACTOR-S5 | Tier-3 feature flag shadow | ✅ COMPLETE (2026-05-20, PM-approved) |
| S6 | S-REFACTOR-S6 | Tier-3 feature flag primary path | ✅ COMPLETE (2026-05-20, PM-approved) |
| S7 | S-REFACTOR-S7 | Tier-2 typed dispatch (PR #1604) | ✅ COMPLETE (2026-05-20) |
| S7-IB | S-REFACTOR-S7-IB | Tier-2 IB/MES shadow | ⛔ DEFERRED — no IB credentials in scope |
| S8 | S-REFACTOR-S8 | Tier-2 PortfolioState (PR #1605) | ✅ COMPLETE (2026-05-20) |
| S8-ICT | S-REFACTOR-S8-ICT | Tier-1 ICT filter module (PR #1609) | ✅ COMPLETE (2026-05-20) |
| S9 | S-REFACTOR-S9 | Tier-1 StrategyBase alignment | ✅ COMPLETE (2026-05-20) |
| S10 | S-REFACTOR-S10 | Tier-1 ML advisory hooks | ✅ COMPLETE (2026-05-20) |
| S11 | S-REFACTOR-S11 | Tier-1 attribution API (PR #1608) | ✅ COMPLETE (2026-05-20) |
