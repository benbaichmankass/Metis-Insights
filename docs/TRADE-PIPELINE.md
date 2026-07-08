# Trade Process Pipeline — Canonical Step-by-Step Map

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Companion:** [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) — this doc is the deeper, stage-by-stage expansion of its `End-to-End Trade Pipeline` section.
> **Consumed by:** historically the `ict-trader-dashboard` Vercel app's **Trade Process** tab fetched this file at runtime and rendered each stage as a card. The dashboard migrated Vercel → Streamlit 2026-05-12 ([PR #32](https://github.com/benbaichmankass/ict-trader-dashboard/pull/32)) and the Trade Process tab was **not carried over** — it's listed under "Not (yet) ported from the old React app" in the dashboard's own `CLAUDE.md`. **No live consumer currently fetches this doc.** It remains the canonical human/Claude-readable pipeline map; re-port the tab (or drop this note) when/if that gap is closed. (Fixed 2026-07-08, doc-freshness sweep, `S-ALPACA-PIPELINE-AUDIT-2026-07-07`.)

---

## Purpose

A single, scannable map of the live trading pipeline. Every stage is documented here with its files, inputs, outputs, narrative, and known failure modes. If something happens between an exchange tick and a trade being booked, it lives in one of the stages below.

This doc is intentionally **structurally strict** — the dashboard parses it on `## Stage N:` boundaries and on the labeled blocks (`**Files:**`, `**Inputs:**`, `**Outputs:**`, `**Description:**`, `**Failure modes:**`). Do not change the heading format or block labels without also updating `src/lib/tradePipeline.ts` in the dashboard repo.

## How to update

Any sprint that touches a pipeline stage **must**:

1. Update the affected `## Stage N:` block in this file (files list, inputs/outputs, description, failure modes — whichever changed).
2. Bump the **Last verified** date in the relevant stage block.
3. ~~Open the dashboard's **Trade Process** tab after merge to `main` and visually confirm the change appears as expected.~~ **Currently N/A** — the tab isn't ported to the Streamlit dashboard (see "Consumed by" above); skip this step until it is, and don't fail a sprint's wrap-up checklist on it in the meantime.
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

**Files:** `src/exchange/bybit_connector.py`, `src/exchange/binance_connector.py`, `src/exchange/ib_connector.py` (`IBMarketData`), `src/runtime/market_data.py` (`connector_for_symbol`), `src/runtime/liquidity_state.py`

**Inputs:** Per-tick request from `src/main.py` (default tick interval 60s, configurable via `TICK_INTERVAL_SECONDS`); the active symbol set (BTCUSDT + MES today) from the multi-symbol orchestrator; per-symbol configuration from `config/strategies.yaml` + `config/instruments.yaml`.

**Outputs:** OHLCV candles (default 5-minute bars) and current tick prices held in cached market-data state, available to all downstream stages within the tick — fetched **per symbol**.

**Description:** The pipeline runs **multi-symbol**: each tick it iterates the configured symbols and `connector_for_symbol(symbol)` routes each to the right data source by its `config/instruments.yaml` exchange — BTCUSDT → Bybit, MES → Interactive Brokers (`IBMarketData.get_ohlcv` via `reqHistoricalData`, **delayed** CME bars by default so no paid real-time subscription is needed). Connectors normalize REST/websocket/TWS-API responses to the internal candle and tick format. Cached liquidity state (`liquidity_state.py`) preserves multi-tick context (e.g. running highs/lows) so detection stages don't re-derive it every tick.

**Failure modes:**
- Exchange API rate limit or 5xx — connector returns empty/stale data; the pipeline logs and skips the tick rather than acting on stale prices.
- Network partition — same as above; heartbeat (Stage 9) reflects the tick still ran but with no signals.
- Exchange-side symbol delisting — connector raises and the strategy for that symbol is skipped for the tick.
- **IB Gateway down / not logged in** — `IBMarketData.get_ohlcv` returns `None`; MES strategies skip the tick gracefully and the live crypto path is unaffected.

**Last verified:** 2026-05-22 (MES go-live)

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

**Files:** `src/units/accounts/execute.py` (`_submit_order`), `src/units/accounts/ib_client.py` (`IBClient.place`), exchange connectors from Stage 1, the symbol→exchange dispatch gate in `src/core/coordinator.py`, per-account `mode` field in `config/accounts.yaml`

**Inputs:** Validated orders from Stage 6.

**Outputs:** Live broker order acknowledgements (in `mode: live`) or simulated fills logged with the `dry_run` marker (in `mode: dry_run`). Either way, the result is recorded in the trade journal at Stage 9.

**Description:** Per-account `mode: live | dry_run` in `config/accounts.yaml` is the canonical execution gate. `_submit_order` dispatches by the account's exchange: Bybit (`pybit`) for BTCUSDT, and the `interactive_brokers` branch (`IBClient.place`) for MES futures — a native bracket (market entry + TP limit + SL stop) snapped to the 0.25 tick grid; IB uses no API keys (auth is the Gateway login session). A symbol→exchange dispatch gate ensures a BTCUSDT signal never reaches the MES account and vice-versa. Dry-run mode logs the intended order but does not submit. There is no process-wide live/dry switch — every account is independent, so live and dry coexist within one bot instance (e.g. `ib_paper` live on paper money, `ib_live` held dry_run).

**Failure modes:**
- Exchange rejects (insufficient margin, symbol halted, etc.) — recorded with the rejection reason; no retry by default.
- IB Gateway unreachable / API handshake fails — `IBClient.place` raises `IBConnectionError`; the order is refused for that account this tick (crypto unaffected).
- Network blip mid-submit — order may have landed; reconciliation at the next tick relies on broker fills, not local optimism.
- `mode` typo in `config/accounts.yaml` — startup config validation rejects unknown values; the bot won't start with an invalid account mode.

**Last verified:** 2026-05-22 (MES go-live)

---

## Stage 8: Position Monitoring & Exit

**Files:** `src/runtime/order_monitor.py` (`run_monitor_tick()`, `_apply_update()`, `_apply_partial_close()`, `_send_modify_to_exchange()`, `_send_close_to_exchange()`), `src/units/strategies/vwap.py` (`monitor()`), `src/units/strategies/turtle_soup.py` (`monitor()`), `src/units/strategies/_base.py` (`monitor_breakeven_sl()`), `config/strategies.yaml`

**Inputs:** Open packages from the `order_packages` table (status='open') queried per-strategy each tick; fresh candles per package at its `meta.timeframe` (falling back to `config/strategies.yaml`'s per-strategy timeframe when legacy rows lack the key); per-strategy cfg map passed through to each `monitor()` call.

**Outputs:** Strategy verdicts written back to the journal and (for live accounts) routed to the exchange. Shapes: `{"sl": float}` / `{"tp": float}` modify the package row and call `_send_modify_to_exchange` (Bybit `set_trading_stop`); `{"action": "close", "reason", "exit_price"}` closes both package and linked trade row and calls `_send_close_to_exchange` (reduce-only market order); `{"action": "close", "close_qty_pct": <1.0, "reason", "exit_price", "next_tp"?}` reduces `trades.position_size` and optionally rolls `order_packages.tp` forward without closing the package. Each per-pkg dispatch emits one INFO log line (`pkg_id`, `symbol`, `candles`, `verdict`) so the operator can confirm the loop reached each open position.

**Description:** The trader's main loop (`src/main.py`) invokes `run_monitor_tick()` after every entry-side tick. For every open package the loop fetches fresh candles, decodes the package row, and calls the owning strategy's `monitor(cfg, candles_df, open_pkg)` hook. Each strategy implements its own exit ladder; the operator is hands-off — every adjustment to a live position originates from `monitor()`.

`vwap.monitor()` close-path priority (first match wins): (1) TP-cross — `close ≥ tp` long / `close ≤ tp` short → full close; (2) SL-cross → full close; (3) VWAP-cross — live VWAP recomputed from fresh candles; price reverted across the live VWAP line → full close; (4) Time-decay — package older than `cfg.monitor_hold_window_minutes` (default 240) → full close; (5) SL-to-break-even — price moved past `cfg.be_at_r × 1R` in our favour (default `be_at_r=1.0`) → slide SL to entry (one-shot, idempotent on subsequent ticks).

`turtle_soup.monitor()` close-path priority (first match wins): (1) SL-cross → full close; (2) TP1 partial — price reached the package's `tp` (= TP1 at signal time) and `meta.tp2` is present → emit `{action: close, close_qty_pct: cfg.partial_close_pct (default 0.25), reason: tp1_partial, next_tp: meta.tp2}`. `_apply_partial_close` reduces `trades.position_size` and rolls `order_packages.tp` forward to TP2 so the next tick targets the runner; (3) TP2 full close — package's tp has been rolled to TP2 and price hit it → full close; (4) SL-to-break-even — price moved past `cfg.be_at_r × 1R` (default `be_at_r=0.75`) → slide SL to entry.

All thresholds live in `config/strategies.yaml` (`strategies.<strategy>.{be_at_r, monitor_hold_window_minutes, partial_close_pct, tp1_at_r, tp2_at_r}`) and are picked up on the next config reload — no code changes required to tune the monitor. The break-even one-R distance is computed from the original `abs(entry - sl)` at signal time; once SL has slid to entry, the helper is idempotent and does not continue trailing (that's a deliberate scope boundary — there is no ATR-based progressive trail today).

**Failure modes:**
- Strategy `monitor()` raises — caught in `_call_strategy_monitor`, logged at WARNING; the package keeps its previous state for the tick.
- OHLCV fetcher returns `None` (timeframe missing in `meta`, exchange read failure, etc.) — monitor short-circuits to `None` and the diagnostic log line surfaces `candles=None` so the silent-no-data case is visible to the operator instead of looking like a working but inert monitor.
- Stop-loss / take-profit price drift between detection and submission — broker may fill at a different price; recorded faithfully in the journal.
- Bot restart between detection and submission — closed-flat invariant + journal reconciliation handle the resume case.

**Last verified:** 2026-05-13

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

**Inputs:** Logs and journal from Stage 9; operator commands via Telegram or via the system-actions GitHub Actions workflow.

**Outputs:** Hourly Telegram summaries (per-strategy and per-account); FastAPI diagnostic endpoints (live status, halt/resume, pending alerts); dashboard tabs consuming the unauthenticated Tier 1 endpoints documented in [`api-tier-policy.md`](api-tier-policy.md).

**Description:** The pipeline isn't done until the operator can see what happened and intervene if needed. Telegram is the synchronous channel (queries, alerts, halt commands). The FastAPI diag router is the async channel (dashboard reads). Historically the dashboard's **Trade Process** tab fetched *this* document at runtime so operators could see the current pipeline structure alongside live status — that tab was not carried over in the Vercel→Streamlit migration (see the doc header); today this document's readers are operators/Claude sessions directly, not a rendered UI tab.

**Failure modes:**
- Telegram outage — bot continues trading; operator visibility degraded; alert when the channel comes back.
- Diag API down — dashboard shows degraded state; recovery handled by `vm-web-api-recover.yml` workflow.
- This doc out of sync with the code — misleads whoever reads it next (no live UI tab today to surface staleness visually). Caught only by sprint-time review; that's why the wrap-up checklist exists.

**Last verified:** 2026-05-10

---

## Reference

- [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) — overall system design.
- [`SPRINT-LOG-TEMPLATE-CANONICAL.md`](SPRINT-LOG-TEMPLATE-CANONICAL.md) — sprint template (includes the pipeline-doc verification checkbox).
- [`api-tier-policy.md`](api-tier-policy.md) — which dashboard endpoints are unauthenticated and why.
- [`news_layer.md`](news_layer.md) — Stage 5 news-veto details.
