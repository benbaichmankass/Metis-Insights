# Trade Process Pipeline — Canonical Step-by-Step Map

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Companion:** [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) — this doc is the deeper, stage-by-stage expansion of its `End-to-End Trade Pipeline` section.
> **Consumed by:** the `ict-trader-dashboard` Vercel app fetches this file at runtime (raw URL on `main`) and renders each stage as a card in its **Trade Process** tab. Stale doc = stale operator UI.

---

## Purpose

A single, scannable map of the live trading pipeline. Every stage is documented here with its files, inputs, outputs, narrative, and known failure modes. If something happens between an exchange tick and a trade being booked, it lives in one of the stages below.

This doc is intentionally **structurally strict** — the dashboard parses it on `## Stage N:` boundaries and on the labeled blocks (`**Files:**`, `**Inputs:**`, `**Outputs:**`, `**Description:**`, `**Failure modes:**`). Do not change the heading format or block labels without also updating `src/lib/tradePipeline.ts` in the dashboard repo.

## How to update

Any sprint that touches a pipeline stage **must**:

1. Update the affected `## Stage N:` block in this file (files list, inputs/outputs, description, failure modes — whichever changed).
2. Bump the **Last verified** date in the relevant stage block.
3. Open the dashboard's **Trade Process** tab after merge to `main` and visually confirm the change appears as expected.
4. Tick the corresponding wrap-up checkbox in the sprint log (see [`SPRINT-LOG-TEMPLATE-CANONICAL.md`](SPRINT-LOG-TEMPLATE-CANONICAL.md)).

If a stage is added, removed, or reordered, also update the top-level diagram below and the matching summary in `ARCHITECTURE-CANONICAL.md`.

## Top-level flow

```
 [1] Market Data Ingestion
        |
        v
 [2] ICT Signal Detection
        |
        v
 [3] Strategy Evaluation
        |
        v
 [4] Signal Normalization & Audit
        |
        v
 [5] Risk Gating  ---(rejected)---> stop
        |
        v
 [6] Order Validation  ---(refused)---> stop
        |
        v
 [7] Broker Execution (live | dry_run)
        |
        v
 [8] Position Monitoring & Exit
        |
        v
 [9] Logging & State Persistence
        |
        v
[10] Operator Visibility
```

---

## Stage 1: Market Data Ingestion

**Files:** `src/exchange/bybit_connector.py`, `src/exchange/binance_connector.py`, `src/runtime/market_data.py`, `src/runtime/liquidity_state.py`

**Inputs:** Per-tick request from `src/main.py::run_one_tick()` (default tick interval 60s, configurable via `TICK_INTERVAL_SECONDS`); per-symbol configuration from `config/strategies.yaml`.

**Outputs:** OHLCV candles (default 5-minute bars) and current tick prices held in cached market-data state, available to all downstream stages within the tick.

**Description:** The pipeline begins each tick by pulling fresh market data from the configured exchange connectors. Connectors are exchange-specific adapters that normalize REST/websocket responses to the internal candle and tick format consumed by the rest of the pipeline. Cached liquidity state (`liquidity_state.py`) preserves multi-tick context (e.g. running highs/lows) so detection stages don't have to re-derive it every tick.

**Failure modes:**
- Exchange API rate limit or 5xx — connector returns empty/stale data; the pipeline logs and skips the tick rather than acting on stale prices.
- Network partition — same as above; heartbeat (Stage 9) reflects the tick still ran but with no signals.
- Exchange-side symbol delisting — connector raises and the strategy for that symbol is skipped for the tick.

**Last verified:** 2026-05-10

---

## Stage 2: ICT Signal Detection

**Files:** `src/ict_detection/fvg_detector.py`, `src/ict_detection/order_blocks.py`, `src/ict_detection/swing_points.py`, `src/ict_detection/liquidity.py`, `src/ict_detection/trend.py`, `src/ict_detection/key_levels.py`

**Inputs:** Cached candles and tick state from Stage 1.

**Outputs:** Structured ICT primitives: Fair Value Gaps (FVGs), Order Blocks (OBs), swing extremes, structure breaks, liquidity zones, trend bias. These are passed by reference into Stage 3.

**Description:** Reusable detection components extract the ICT (Inner Circle Trader) concepts that downstream strategies reason over. Detection is intentionally separate from strategy logic so multiple strategies can share the same primitives without re-computing them.

**Failure modes:**
- Insufficient candle history at startup — detectors emit empty results until enough data is buffered; not an error, just quiet.
- Numerical edge case (e.g. equal highs across a swing window) — handled with explicit tie-breakers; bugs here surface as missed setups, not crashes.

**Last verified:** 2026-05-10

---

## Stage 3: Strategy Evaluation

**Files:** `src/units/strategies/vwap.py`, `src/units/strategies/turtle_soup.py`, `src/units/strategies/smoke_test.py`, `src/core/signals.py`, `src/strategy_registry.py`, `config/strategies.yaml`

**Inputs:** ICT primitives from Stage 2; raw candles; per-symbol strategy configuration from `config/strategies.yaml`; strategy registry from `src/strategy_registry.py`.

**Outputs:** Zero or more `OrderPackage`-shaped intents per strategy/symbol pair: `{symbol, side (LONG/SHORT), entry, stop_loss, take_profit, confidence, strategy_name}`.

**Description:** Each strategy module consumes ICT primitives plus market data and decides whether to emit a trade intent. Strategies are stateless across ticks where possible; per-symbol enable/disable flags live in `config/strategies.yaml`. Strategy registry (`strategy_registry.py`) is the single source of truth for which strategy implementations exist.

**Failure modes:**
- Strategy raises uncaught exception — caught by the pipeline orchestrator and logged; the offending strategy is skipped for the tick, others still run.
- Misconfigured `config/strategies.yaml` (unknown strategy name) — registry lookup fails fast at startup.
- Strategy emits malformed intent — caught at Stage 4 normalization.

**Last verified:** 2026-05-10

---

## Stage 4: Signal Normalization & Audit

**Files:** `src/runtime/pipeline.py` (`run_pipeline()`), `src/utils/signal_audit_logger.py`

**Inputs:** Raw `OrderPackage` intents from Stage 3.

**Outputs:** Normalized internal intents ready for risk evaluation; one JSONL audit record per decision in `runtime_logs/signal_audit.jsonl` (fields: symbol, direction, entry/exit triggers, strategy name, timestamp).

**Description:** Multi-strategy and multi-timeframe outputs are normalized to the single internal representation that the rest of the pipeline operates on. Every decision — including no-action ticks — is written to the audit log so post-hoc analysis can reconstruct what the bot saw and chose.

**Failure modes:**
- Audit log disk full — write failure logged; pipeline continues but evidence trail is broken (alert via Stage 10).
- Malformed intent from Stage 3 — rejected with a logged reason; intent does not advance.

**Last verified:** 2026-05-10

---

## Stage 5: Risk Gating

**Files:** `src/units/accounts/risk.py` (`RiskManager.approve()`), `src/units/accounts/prop_risk.py`, `src/runtime/risk_counters.py`, `src/news/news_pipeline.py`, kill-switch flag at `/tmp/trader_halt.flag`

**Inputs:** Normalized intents from Stage 4; per-account state (open positions, daily PnL); per-account config from `config/accounts.yaml` (caps: `pos_size`, `daily_usd`, `max_dd_pct`); current news veto state.

**Outputs:** Approved intents (advance to Stage 6) or rejection reasons (logged, dropped).

**Description:** No intent reaches the broker without passing every applicable gate. The standard `RiskManager.approve()` enforces per-account size, daily-USD, and drawdown caps. Prop-account-specific logic adds the rules required by funded/prop firms. Runtime counters track in-flight risk that hasn't yet settled in the journal. The kill-switch flag (`/tmp/trader_halt.flag`) is a single file the operator can drop on the VM to halt all new orders without restarting the process. The news veto blocks orders during high-impact macro events.

**Failure modes:**
- Stale account state — risk caps may approve based on outdated PnL; mitigated by reconciling against the journal at the start of each tick.
- News feed outage — news veto fails open or closed depending on configured policy; current default is fail-open with an alert.
- Kill-switch present but unread (file-system permission issue) — pipeline alerts and refuses to trade.

**Last verified:** 2026-05-10

---

## Stage 6: Order Validation

**Files:** `src/runtime/orders.py` (`safe_place_order()`), `src/runtime/closed_flat_invariant.py`, `src/runtime/validation.py`

**Inputs:** Risk-approved intents from Stage 5; current open-position state.

**Outputs:** Validated orders ready for broker submission, or hard refusals (logged, dropped).

**Description:** Last-line validation before any order leaves the process. Checks include quantity sanity, symbol-side match, and the closed-flat invariant — the rule that prevents stacking conflicting positions on the same symbol unless explicitly allowed by strategy configuration. This stage exists separately from risk gating because its checks are about order well-formedness, not capital exposure.

**Failure modes:**
- Closed-flat invariant violation — order is hard-refused; bug here would let strategies double-dip the same symbol.
- Quantity below exchange minimum — refused with a clear log line; usually upstream sizing bug.

**Last verified:** 2026-05-10

---

## Stage 7: Broker Execution

**Files:** `src/units/accounts/execute.py`, exchange connectors from Stage 1, per-account `mode` field in `config/accounts.yaml`

**Inputs:** Validated orders from Stage 6.

**Outputs:** Live broker order acknowledgements (in `mode: live`) or simulated fills logged with the `dry_run` marker (in `mode: dry_run`). Either way, the result is recorded in the trade journal at Stage 9.

**Description:** Per-account `mode: live | dry_run` in `config/accounts.yaml` is the canonical execution gate. Live mode dispatches to the exchange via the same connectors that fetched the data. Dry-run mode logs the intended order but does not submit; this is how staging and qualification runs work without touching real capital. There is no process-wide live/dry switch — every account is independent, so live and dry can coexist within one bot instance.

**Failure modes:**
- Exchange rejects (insufficient margin, symbol halted, etc.) — recorded with the rejection reason; no retry by default.
- Network blip mid-submit — order may have landed; reconciliation at the next tick relies on broker fills, not local optimism.
- `mode` typo in `config/accounts.yaml` — startup config validation rejects unknown values; the bot won't start with an invalid account mode.

**Last verified:** 2026-05-10

---

## Stage 8: Position Monitoring & Exit

**Files:** `src/runtime/order_monitor.py` (`run_monitor_tick()`)

**Inputs:** Open positions from the journal; fresh candles each tick from Stage 1; strategy `monitor()` hooks per open position.

**Outputs:** Exit verdicts (close, partial close, trail, hold) applied to `trade_journal.db`; close orders submitted via Stage 7's executor.

**Description:** After Stage 7 places an order, position management runs every tick on every open position. The monitor calls each strategy's `monitor()` hook with the latest candles; the hook returns `None` (hold) or an exit verdict. Verdicts cover take-profit / stop-loss fills, time-based exits, and strategy-specific signals like a VWAP cross. Non-`None` verdicts are applied to the journal and (for live accounts) routed back through the executor as close orders.

**Failure modes:**
- Strategy `monitor()` raises — caught and logged; the position keeps the previous verdict for the tick.
- Stop-loss / take-profit price drift between detection and submission — broker may fill at a different price; recorded faithfully in the journal.
- Bot restart between detection and submission — closed-flat invariant + journal reconciliation handle the resume case.

**Last verified:** 2026-05-10

---

## Stage 9: Logging & State Persistence

**Files:** `trade_journal.db` (SQLite), `runtime_logs/signal_audit.jsonl`, `runtime_logs/heartbeat.txt`, `runtime_logs/status.json`, `src/runtime/outcomes.py`

**Inputs:** Verdicts and acknowledgements from Stages 4, 7, and 8; tick-level outcomes from `pipeline.py`; periodic heartbeat refresh.

**Outputs:** Persistent record of every signal, every order, every fill, every account-balance snapshot. Heartbeat file refreshed every `HEARTBEAT_INTERVAL_SECONDS` (default 60s) as a liveness signal.

**Description:** The journal is the system of record. The signal audit JSONL is the chronological decision log. The heartbeat file is what external supervisors (systemd timers, dashboard health checks) use to confirm the bot is alive. `status.json` is a snapshot summary written for the dashboard.

**Failure modes:**
- SQLite write failure (disk, permissions) — alert via Stage 10; bot may continue serving but evidence is broken.
- Heartbeat stale > 2 ticks — supervisor alerts; the bot may be hung or the system clock may be off.
- `status.json` missing or stale — dashboard shows a degraded state badge.

**Last verified:** 2026-05-10

---

## Stage 10: Operator Visibility

**Files:** `src/bot/telegram_query_bot.py`, `src/web/api/routers/diag.py`, `src/runtime/hourly_report.py`, `src/web/runtime_status.py`, dashboard repo `benbaichmankass/ict-trader-dashboard`

**Inputs:** Logs and journal from Stage 9; operator commands via Telegram or via the operator-actions GitHub Actions workflow.

**Outputs:** Hourly Telegram summaries (per-strategy and per-account); FastAPI diagnostic endpoints (live status, halt/resume, pending alerts); dashboard tabs consuming the unauthenticated Tier 1 endpoints documented in [`api-tier-policy.md`](api-tier-policy.md).

**Description:** The pipeline isn't done until the operator can see what happened and intervene if needed. Telegram is the synchronous channel (queries, alerts, halt commands). The FastAPI diag router is the async channel (dashboard reads). The dashboard's **Trade Process** tab fetches *this* document at runtime so operators can see the current pipeline structure alongside live status.

**Failure modes:**
- Telegram outage — bot continues trading; operator visibility degraded; alert when the channel comes back.
- Diag API down — dashboard shows degraded state; recovery handled by `vm-web-api-recover.yml` workflow.
- This doc out of sync with the code — dashboard tab misleads operators. Caught only by sprint-time review; that's why the wrap-up checklist exists.

**Last verified:** 2026-05-10

---

## Reference

- [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) — overall system design.
- [`SPRINT-LOG-TEMPLATE-CANONICAL.md`](SPRINT-LOG-TEMPLATE-CANONICAL.md) — sprint template (includes the pipeline-doc verification checkbox).
- [`api-tier-policy.md`](api-tier-policy.md) — which dashboard endpoints are unauthenticated and why.
- [`news_layer.md`](news_layer.md) — Stage 5 news-veto details.
