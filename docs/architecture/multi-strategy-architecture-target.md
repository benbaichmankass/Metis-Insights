# Multi-Strategy Architecture Target
**Initiative:** MULTI-STRATEGY-ARCH-REFACTOR (M11)  
**Created:** 2026-05-20  
**Status:** S1 scaffolding complete; S2 wiring not started  
**Canonical rules:** `docs/CLAUDE-RULES-CANONICAL.md`  
**Sprint roadmap:** `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md`

> **Non-negotiable:** This refactor must not break the current live Bybit runtime at any stage.
> Every layer is introduced behind a feature flag or as isolated new code before being wired in.

---

## 1. Current Architecture (as-built, 2026-05-20)

### 1.1 Runtime entry point

```
src/main.py
  └─ src/core/coordinator.py (95KB, monolithic)
        ├─ src/units/strategies/*.py  (signal builders + order_package functions)
        ├─ src/runtime/strategy_signal_builders.py  (per-strategy dispatch)
        ├─ src/runtime/intent_multiplexer.py  (StrategyIntent → DesiredPosition → ExecutionDelta)
        ├─ src/units/accounts/execute.py  (Bybit API calls)
        └─ src/units/accounts/risk.py  (per-strategy risk sizing)
```

The `ict-trader-live.service` systemd unit runs `src/main.py` as a single process. All three strategies execute inside this process on the same tick loop.

### 1.2 Strategy execution layer

| Module | Category | Signal builder | Order builder |
|---|---|---|---|
| `src/units/strategies/vwap.py` | Mean reversion / dislocation | `build_vwap_signal()` | `order_package()` |
| `src/units/strategies/turtle_soup.py` | Trend pullback / continuation | `build_turtle_soup_signal()` | `order_package()` |
| `src/units/strategies/ict_scalp.py` | Breakout / expansion | `build_ict_scalp_signal()` | `order_package()` |

Base class exists at `src/units/strategies/_base.py` but is not consistently used — strategies use module-level functions.

### 1.3 Intent dispatch layer (S-MSE-1, done)

```
src/runtime/intents.py
  StrategyIntent → DesiredPosition → ExecutionDelta pipeline
  register_intent_builder() for new strategies
  DEFAULT_PRIORITIES for conflict resolution

src/runtime/intent_multiplexer.py
  Multi-strategy intent aggregation
  Same-direction reinforcement / conflict resolution
  Feature-flagged: MULTI_STRATEGY_INTENT_LAYER (default false in repo; pinned true in deploy)
```

### 1.4 Account and risk layer

```
config/accounts.yaml            — YAML source of truth for all accounts
config/account_state.yaml       — mutable runtime state (dry_run overrides, suspended flags)
src/units/accounts/account.py   — account dict loading (not yet typed-abstracted)
src/units/accounts/clients.py   — Bybit API client construction (30KB)
src/units/accounts/execute.py   — order placement, fill monitoring (42KB)
src/units/accounts/risk.py      — per-account risk sizing, daily loss limits (17KB)
src/units/accounts/prop_risk.py — prop-account mission rules
src/runtime/risk_counters.py    — daily risk counter persistence (SQLite)
src/runtime/positions.py        — current positions per account
```

### 1.5 ML layer (WS7-WS8, done)

```
ml/shadow/factory.py            — resolves shadow_model_ids from ML registry
src/runtime/shadow_adapter.py   — with_shadow_pred() / with_shadow_preds() helpers
ml/shadow/inspector.py          — streaming reader + CLI for shadow_predictions.jsonl
ml/shadow/drift.py              — KS + PSI drift detection
runtime_logs/shadow_predictions.jsonl  — audit log (on-VM)
```

All three strategies auto-wire every ML model at `shadow` deployment stage. Shadow predictions have **zero effect** on orders.

### 1.6 ICT detection layer

```
src/ict_detection/
  fvg_detector.py    — Fair Value Gap detection
  liquidity.py       — liquidity sweep detection
  order_blocks.py    — order block detection
  swing_points.py    — swing high/low detection
  trend.py           — trend direction assessment
  key_levels.py      — structural key level identification
```

Currently used by `ict_scalp.py` directly. Not formally exposed as a public API module.

### 1.7 Config layer

```
config/strategies.yaml    — per-strategy parameters (enabled, risk_pct, timeframe, symbols, strategy-specific)
config/accounts.yaml      — account definitions (type, endpoint, dry_run, risk limits)
config/account_state.yaml — mutable runtime overrides
config/units.yaml         — unit configuration
```

### 1.8 Dashboard layer

```
src/web/                          — FastAPI web app
  api/routers/shadow.py           — WS8 shadow prediction endpoints
  api/routers/trades.py           — trade history
src/runtime/api_reporting.py      — reporting data structures
```

Running as `ict-web-api.service`, behind a Cloudflare tunnel. Dashboard frontend lives in `benbaichmankass/ict-trader-dashboard` (separate repo, Vercel).

### 1.9 Identified gaps (target for M11)

1. No `AccountProfile` typed dataclass — raw YAML dicts passed around
2. No `InstrumentProfile` typed dataclass — symbol hardcoded in strategies
3. No centralized allocator — sizing done independently per strategy in `risk.py`
4. No formal `SignalPackage` contract — ad-hoc dicts
5. No formal `OrderPackage` contract with strategy attribution
6. No net portfolio position accounting — positions per account but not cross-strategy netted
7. `src/ict_detection/` has no public API — internal use only
8. ML decision layer not formally separated from shadow logging
9. ~~No IB/MES execution path~~ — **closed 2026-05-21.** IB connection +
   MES execution wired: `IBClient` (`src/units/accounts/ib_client.py`,
   ib_insync, no API keys), `ib_client_for()` factory, the
   `interactive_brokers` branch in `execute._submit_order`, the
   coordinator client-construction branch, and the `ib_paper` (mode: live →
   paper money) / `ib_live` (mode: dry_run) accounts in
   `config/accounts.yaml`. Strategy routing to MES is the remaining step
   (`strategies: []` on both today). See `docs/runbooks/ib-integration.md`.

---

## 2. Target Architecture Layers

The following diagram shows the target layer structure. Layers are implemented bottom-up across sprints S2-S8.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 9: Dashboard Transparency (S6)                               │
│  src/web/api/routers/ — attribution, allocator decisions, net pos   │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 8: Risk / Promotion Controls                                 │
│  src/units/accounts/risk.py + prop_risk.py (existing, extended)     │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 7: ML Decision Layer (S5)                                    │
│  src/runtime/shadow_adapter.py — shadow → advisory → (future) gate  │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 6: Net Portfolio Position Layer (S4)                         │
│  src/runtime/positions.py extended — cross-strategy net accounting  │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 5: Order Package Layer (S3-S4)                               │
│  src/core/order_contract.py — OrderPackage with attribution         │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4: Centralized Allocator (S4)                                │
│  src/core/allocator.py — PassthroughAllocator → adaptive (future)  │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3: Signal Package Layer (S3)                                 │
│  src/core/signal_contract.py — SignalPackage typed contract         │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2: Strategy Registry + Signal Builders (S3)                  │
│  src/strategy_registry.py extended — category, interface, builders  │
│  src/units/strategies/{vwap,turtle_soup,ict_scalp}.py (unchanged)   │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1: Feature Engine / ICT Filter Module (S8, deferred)         │
│  src/ict_detection/ — formalized as reusable public API module      │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 0: Account + Instrument Profile Layer (S2)                   │
│  src/core/account_profile.py — AccountProfile dataclass             │
│  src/core/instrument_profile.py — InstrumentProfile dataclass       │
│  config/accounts.yaml (existing) + config/instruments.yaml (new)    │
└─────────────────────────────────────────────────────────────────────┘
```

### Layer 0: Account + Instrument Profile Layer (S2)

**Purpose:** Typed, read-only views of `config/accounts.yaml` and `config/instruments.yaml`. No execution path change.

**Current state:** Accounts loaded as raw dicts in `src/units/accounts/account.py`. No typed abstraction.

**Target:**
```python
# src/core/account_profile.py — S1 scaffolding, wired in S2
AccountProfile(
    account_id="bybit_2",
    account_type="bybit_live",
    exchange="bybit",
    dry_run=False,
    base_currency="USDT",
    max_concurrent_positions=1,
)

# src/core/instrument_profile.py — S1 scaffolding, wired in S2
InstrumentProfile(
    symbol="BTCUSDT",
    exchange="bybit",
    asset_class="crypto_perp",
    tick_size=0.10,
    min_qty=0.001,
)
```

**IB consideration:** The `InstrumentProfile.mes_cme()` pre-built profile is added in S1. The `AccountProfile` type already supports `exchange="interactive_brokers"`. The IB execution path (S7) builds on these types without requiring schema changes.

### Layer 3: Signal Package Layer (S3)

**Purpose:** Normalize signal output from all strategies into a typed contract that the allocator can consume.

**Current state:** Each strategy's `build_*_signal()` returns an ad-hoc dict. The `Coordinator` reads specific keys by convention.

**Target:**
```python
SignalPackage(
    strategy_id="vwap",
    symbol="BTCUSDT",
    account_id="bybit_2",
    side="long",              # or "short", "none"
    entry_price=95_000.0,
    stop_loss=94_500.0,
    take_profit=96_000.0,
    timestamp_utc="2026-05-20T12:00:00Z",
    raw={...},                # original strategy output preserved for attribution
)
```

**Key invariant:** `side="none"` signals are filtered by `is_actionable` before reaching the allocator. The allocator never sees a none-signal.

### Layer 4: Centralized Allocator (S4)

**Purpose:** Single point that decides which signals become orders and at what size.

**Current state:** Each strategy sizes independently via `risk.py::position_size()`. There is no central view of combined portfolio exposure.

**Target (S4 — PassthroughAllocator):**
```python
allocator = PassthroughAllocator()  # identity: current behavior exactly
orders = allocator.allocate(signals, portfolio_state)
```

**Target (future — AdaptiveAllocator, post-S4):**
- Adaptive sizing based on portfolio correlation
- Exposure cap enforcement across all concurrent positions
- Signal priority queue when capital is constrained

**Feature flag:** `CENTRALIZED_ALLOCATOR=false` (default) preserves existing intent multiplexer path. Flip to `true` in tests to verify `PassthroughAllocator` produces identical decisions.

### Layer 5: Order Package Layer (S3-S4)

**Purpose:** Typed order with strategy attribution for PnL tracking.

**Current state:** `StrategyIntent` / `DesiredPosition` / `ExecutionDelta` in `src/runtime/intents.py` handle the dispatch layer. Order attribution is implicit.

**Target:**
```python
OrderPackage(
    strategy_id="turtle_soup",
    symbol="BTCUSDT",
    account_id="bybit_2",
    side="long",
    qty=0.01,
    entry_price=95_000.0,
    stop_loss=94_500.0,
    take_profit=96_500.0,
    order_type="limit",
    timestamp_utc="2026-05-20T12:00:00Z",
    attribution={"source_signal_raw": {...}, "strategy_category": "trend_pullback"},
    net_position_context={},   # populated by Layer 6 in S4
)
```

**Key invariant:** Every fill can be traced to a `strategy_id`. PnL accounting is possible both per-strategy and as aggregated net outcome.

### Layer 6: Net Portfolio Position Layer (S4)

**Purpose:** Net exposure per account+instrument across all concurrent strategy positions.

**Current state:** `src/runtime/positions.py` tracks positions per account. No cross-strategy netting.

**Target:** Position state stores:
- Per-strategy open positions (attribution layer)
- Net position per account+instrument (portfolio layer)
- These are computed views of the same underlying fills, not two separate systems

**Key design decision:** Strategies remain separate at the attribution layer (each trade knows its strategy). Portfolio accounting nets them for risk view. Both views must be correct simultaneously.

### Layer 7: ML Decision Layer (S5)

**Current state:** `shadow_adapter.py` runs models in shadow mode (log only, zero order effect).

**Target:**
```
Shadow mode  → log predictions, no coordinator effect (current, done)
Advisory mode → log + set advisory_flag on signal (S5 wires the flag; noop effect)
Decision gate → advisory_flag blocks/modifies order (Tier-3, requires PM approval; not in M11)
```

**Key invariant:** ML generates **no** raw signals. Hard-rule strategies remain the signal source. ML acts only as a decision layer on top of hard-rule signals. Advisory mode is observable but has zero order effect until PM explicitly approves promotion to decision gate.

---

## 3. Account Model

### 3.1 Current Bybit accounts

| Account ID | Type | Endpoint | Purpose |
|---|---|---|---|
| `bybit_1` | demo | api-demo.bybit.com | Shadow / demo execution |
| `bybit_2` | live | api.bybit.com | Live trading |

Both accounts run all three strategies on BTCUSDT linear perps. Account config is in `config/accounts.yaml`.

### 3.2 IB/MES accounts (wired 2026-05-21)

IB accounts are first-class. As-built `config/accounts.yaml` (IB uses **no
API keys** — auth is the IB Gateway login session; identity is
host/port/clientId/account code):
```yaml
ib_paper:                       # paper account → live mode (paper money)
  exchange: interactive_brokers
  mode: live
  market_type: futures
  ib_host: 127.0.0.1
  ib_port: 4002                 # host loopback → gnzsnz socat relay (→ gateway 4002)
  ib_account: DUQ325724
  ib_client_id: 497
  strategies: [turtle_soup, vwap, ict_scalp_5m]   # all 3, symbol-parameterized on MES

ib_live:                        # real-money account → held dry_run
  exchange: interactive_brokers
  mode: dry_run
  ib_port: 7496                 # live gateway
  ib_account: U25907316
  ib_client_id: 496
  strategies: []
```

MES paper trading is **live** as of 2026-05-22: all three strategies fetch
and evaluate MES candles every tick (delayed CME data via
`IBMarketData.get_ohlcv`), alongside BTCUSDT on the Bybit accounts. The
gateway runs as a Docker container with a socat relay (host `127.0.0.1:4002`
→ container `4004` → gateway `4002`); the paper account logs in with no 2FA.
See `docs/runbooks/ib-integration.md` for the full operational detail.

`AccountProfile.is_ib` and `exchange="interactive_brokers"` are in place
from S1. The IB client is real: `IBClient`
(`src/units/accounts/ib_client.py`, ib_insync) connects to the Gateway
and places a market-entry MES bracket; `ib_client_for()` constructs it;
`execute._submit_order` routes the `interactive_brokers` branch. The
`mode: live` paper account executes against IB paper money exactly as
`bybit_1` runs `mode: live` against Bybit's demo endpoint; the real-money
`ib_live` account is held `dry_run` and never opens a socket. No
prop-account IB configs are included.

### 3.3 Account profile design rules

- Account profiles are **immutable** (frozen dataclasses) — no runtime mutation
- Account profiles are **read-only views** of `config/accounts.yaml` — the YAML remains the source of truth
- The live execution path continues to read `config/accounts.yaml` via existing loaders until S2 wires the typed loader
- `account_state.yaml` (mutable overrides) is a separate concern and is NOT part of `AccountProfile`

---

## 4. Strategy Category Mapping

### 4.1 Mean reversion / dislocation — `vwap`
**Module:** `src/units/strategies/vwap.py`  
**Config:** `config/strategies.yaml::vwap`  
**Mechanism:** Price deviates from VWAP by ≥ σ-threshold; strategy enters expecting reversion. Regime policy gate (wired 2026-05-20, S-VWAP-POLICY-LIVE-WIRE) skips weak/sideways setups.  
**ICT elements used:** None — VWAP-specific  
**Architecture note:** VWAP is the highest-volume strategy (1.0% risk). Its mean-reversion thesis is the most sensitive to entry σ calibration.

### 4.2 Trend pullback / continuation — `turtle_soup`
**Module:** `src/units/strategies/turtle_soup.py`  
**Config:** `config/strategies.yaml::turtle_soup`  
**Mechanism:** Sweeps structural highs/lows at 15m, then enters the reversal at 1m. In trending markets this is a pullback entry in the dominant direction.  
**ICT elements used:** Liquidity sweep concept (sweep of EQH/EQL), structural swing points  
**Architecture note:** Lowest risk (0.5%) among the three. Sweep logic shares conceptual DNA with `src/ict_detection/liquidity.py` — that module is not wired into turtle_soup yet.

### 4.3 Breakout / expansion — `ict_scalp_5m`
**Module:** `src/units/strategies/ict_scalp.py`  
**Config:** `config/strategies.yaml::ict_scalp_5m`  
**Mechanism:** Liquidity sweep → displacement bar → FVG formation → price returns to mitigate FVG. The displacement bar IS the breakout; FVG mitigation IS the expansion entry.  
**ICT elements used:** `src/ict_detection/fvg_detector.py`, `src/ict_detection/liquidity.py`, `src/ict_detection/swing_points.py`  
**Architecture note:** This strategy already uses `src/ict_detection/` directly. When S8 formalizes that module, `ict_scalp` is the primary consumer.

### 4.4 ICT/FVG/OB signal/filter module (fourth component — S8, deferred)
**Module (seed):** `src/ict_detection/`  
**Status:** Exists as internal utility; not a standalone strategy  
**Plan:** Formalized in S8 as a reusable filter module with a clean public API. Available to all strategies as an optional filter/signal layer. Not a trading strategy by itself — it enriches other strategies' signals.  
**Key principle:** ICT/FVG/OB logic improves signal quality when used as a filter on top of hard-rule strategies, but is too noisy as a standalone signal generator without a higher-timeframe context rule.

---

## 5. Invariants (must hold throughout the entire refactor)

1. **Live behavior frozen:** `src/units/strategies/vwap.py`, `turtle_soup.py`, `ict_scalp.py` — no logic changes without Tier-3 PM approval
2. **Current execution path default:** New layers are added alongside the existing path; they do not replace it until a feature flag is explicitly flipped
3. **Signal attribution preserved:** Every order placed by the refactored system must carry the same `strategy_id` attribution as today
4. **No silent sizing changes:** The `PassthroughAllocator` must produce identical position sizing to the current `risk.py` path for all actionable signals
5. **Shadow predictions unaffected:** WS7 shadow logging continues to work unchanged throughout the refactor
6. **ML models never generate raw signals:** Hard-rule strategies remain the signal source; ML acts only as a scoring/filtering layer
7. **Real-money IB account stays dry_run:** The live-money IB account
   (`ib_live`, port 7496) is `mode: dry_run` and opens no socket; promoting
   it to live requires explicit operator approval via the `set-account-mode`
   action (Tier-3). The paper account (`ib_paper`, host port 4002) runs
   `mode: live` — "live" there means IB *paper money*, no real-money risk.

---

## 6. Sprint-to-Layer Mapping

| Sprint | Layers implemented | Existing code changed? |
|---|---|---|
| S1 (done) | Scaffolding only — typed dataclasses, ABCs, PassthroughAllocator stub | No |
| S2 | Layer 0 (account + instrument profiles wired) | Yes — coordinator.py (read-only accessor) |
| S3 | Layers 2-3 (strategy registry + signal contract) | Yes — strategy_signal_builders.py, strategy_registry.py |
| S4 | Layers 4-6 (allocator, order contract, net positions) | Yes — coordinator.py, positions.py (feature-flagged) |
| S5 | Layer 7 (ML decision hooks) | Yes — shadow_adapter.py, coordinator.py |
| S6 | Layer 9 (dashboard transparency endpoints) | Yes — src/web/api/routers/ |
| S7 | Layer 0 extension (IB account profile + **live client**) | Yes — ib_client.py, clients.py, execute.py, coordinator.py (wired 2026-05-21) |
| S8 | Layer 1 (ICT filter module public API) | Yes — ict_detection/__init__.py, ict_scalp.py |

---

## 7. Change Log

| Date | Change | Sprint |
|---|---|---|
| 2026-05-20 | Initial architecture target document created | S-REFACTOR-S0 |
| 2026-05-20 | S1 scaffolding: AccountProfile, InstrumentProfile, SignalPackage, OrderPackage, StrategyInterface, AllocatorInterface, PassthroughAllocator | S-REFACTOR-S1 |
| 2026-05-21 | IB/MES execution path wired (gap #9 closed): IBClient (ib_insync, no keys), ib_client_for, execute._submit_order IB branch, coordinator branch, ib_paper (mode: live) + ib_live (mode: dry_run) accounts | S7 |
| 2026-05-22 | **MES paper trading LIVE.** Multi-symbol pipeline runs BTCUSDT + MES every tick (all 3 strategies, symbol-parameterized; `connector_for_symbol` market-data routing + symbol→exchange dispatch gate). Headless gnzsnz Docker gateway with socat relay (host 4002 → container 4004 → gateway 4002); paper logs in without 2FA; delayed CME data. Fixes: gateway socat port-map (#1706), persistent asyncio event loop in IBClient (#1712). `ib_paper.ib_port` 7497→4002, strategies assigned. | S-MES-GOLIVE |
