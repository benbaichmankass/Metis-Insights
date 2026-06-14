# Architecture — Canonical (v2)

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Authority:** This document supersedes the older
> [`docs/architecture.md`](architecture.md) and the architecture
> sections of the root `CLAUDE.md`. When this doc and an older note
> disagree, this doc wins.
> **Companion:** [`CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md)
> covers Claude's operating rules; this doc covers system design only.
> **AI scope:** AI-specific architecture (data → feature → model →
> orchestration → deterministic control) is documented in
> [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
> and is owned by ROADMAP.md milestones M9 + M10. When that doc and
> this one overlap on AI scope, the AI-platform doc wins for AI-only
> design questions; this doc remains canonical for system-wide design.

## Purpose

Canonical description of how the ICT trading bot project is structured
and how the major systems work together. Process policy lives in the
rules doc; this doc is system design.

Update this doc whenever real repo architecture changes, when subsystems
are introduced or moved, or when a sprint discovers that the docs no
longer match the implementation.

## Architectural Principles

- Live trading stability takes precedence over feature growth.
- The trader runs 24/7 in YAML-declared mode; the system never
  switches itself off. (Operator-facing rule:
  [`CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) § Prime
  Directive. Code-level contract: § Mode Mutation Contract below.)
- Research, staging, and live trading must remain clearly separable.
- Operator communications must remain isolated from core trading logic.
- Deployment behavior must be explicit and documented.
- Every production-critical subsystem must have a clear owner file
  path, validation path, and logging path.
- Duplicate files, unclear canonical entrypoints, and undocumented side
  effects are architecture problems and are treated as such.

## Mode Mutation Contract (2026-05-12)

The per-account live/dry mode is governed by exactly one contract
from 2026-05-12 onward. See
[`CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) § Prime
Directive for the operator-facing rule; this section is the
system-design counterpart.

1. **Source of truth.** `config/accounts.yaml` `mode:` per account.
   `_resolve_mode(cfg, name)` in `src/units/accounts/__init__.py`
   reads it on every call.
2. **Only mutation path.** The `set-account-mode` operator action
   (`scripts/ops/set_account_mode.sh`, allowlisted in
   `.github/workflows/system-actions.yml`, landed in PR #978).
   Edits YAML, restarts the trader, Telegram-pings the operator with
   the diff via `scripts/ops/notify_run.sh`.
3. **No runtime override layer (behaviour remediated; residual dead
   code).** The `_DRY_RUN_OVERRIDES` dict and `set_account_dry_run()`
   function in `src/units/accounts/__init__.py` (+ the
   `Coordinator.set_account_dry_run()` wrapper at
   `src/core/coordinator.py:1760`) **still exist on disk as of
   2026-06-10**, but have **no remaining automatic or operator caller**:
   the legacy Telegram `/accounts dry|live` writer was removed in #1933
   (see closing paragraph) and mode flips now route through the
   sanctioned `set_account_mode.sh` (item 2). The promised
   safeguards-PR deletion never landed, so this is orphaned shim code,
   not a live override path. **Cleanup proposed** (delete the dict +
   both functions + the `account_state.yaml` dry-only override read at
   `coordinator.py:1100`); tracked in the Known-gaps row below.
4. **No auto-flip (verified).** No code path inside `src/` flips a mode.
   The 2026-05-12 silent-flip incident drove this: the breaker auto-flip
   that lived in `src/core/coordinator.py` ("3 consecutive exchange
   rejections → set_account_dry_run(True)") **has been removed** — the
   consecutive-rejection path at `src/core/coordinator.py:1669-1689` now
   only pushes a critical Telegram alert ("Account stays live —
   investigate and use set-account-mode") and never mutates mode. The
   rejection counter remains as RiskManager input only.
5. **Transient issues route through RiskManager.** When exchange
   rejections cluster, data quality degrades, or risk signals
   trip, `RiskManager.approve()` returns
   `reject(reason=…, trade=…)` for an individual trade. The account
   mode is never touched. The next signal is evaluated fresh.
6. **Every rejection is its own Telegram ping.** Per-trade:
   account, symbol, side, qty, reason, raw exchange error if any.
   Not aggregate. The operator sees each refusal as it happens so
   they can intervene fast.
7. **Boot always starts the trader live (per YAML).** `src/main.py`
   reads `accounts.yaml`, resolves modes, and starts ticking. No
   refuse-to-start logic. If state is inconsistent vs. YAML, log
   loudly and Telegram-alert — but the trader runs.
8. **Mechanically enforced.** CI guards (`dry-run-guard.yml` plus the
   follow-on safeguards-PR rule) block new code from writing to
   account modes outside the sanctioned wire.

The operator's account-mode surface is the menu-driven kill switch in
`src/bot/telegram_query_bot.py` (the 2026-05 bot overhaul, #1933): a
confirmed flip persists by invoking `scripts/ops/set_account_mode.sh`
— the sanctioned writer — so there is exactly one on-disk mutation
surface. The legacy `/accounts dry|live <name>` command, which wrote
the in-memory `_DRY_RUN_OVERRIDES` dict, was removed in #1933; the
override dict + `set_account_dry_run()` themselves remain queued for
deletion (item 3 above).

## System Layers

### 1. Runtime trading layer
Market-driven execution behavior:
- market-data intake (`src/exchange/`, `src/runtime/market_data.py`),
- strategy evaluation (`src/units/strategies/`),
- runtime pipeline orchestration (`src/runtime/pipeline.py`,
  entrypoint via `src/main.py`),
- order construction and validation (`src/runtime/orders.py`,
  `src/runtime/validation.py`),
- per-account risk gating (`src/units/accounts/risk.py`,
  `src/units/accounts/prop_risk.py`,
  `src/runtime/risk_counters.py`),
- broker execution (`src/units/accounts/execute.py`),
- runtime logs and state outputs (`runtime_logs/`, `trade_journal.db`).

### 2. Research and validation layer
Idea generation, backtesting, dry-run qualification:
- `notebooks/`,
- `experiments/`,
- `src/backtest/`,
- `src/ml/` (where present),
- backtest dispatch from `src/bot/test_strategy_consumer.py`
  (auto-consumed `test_strategy:<name>` requests).

### 3. Operator control and communication layer
Asynchronous Claude ↔ operator channel:
- repo-backed request artifacts (`comms/requests/`),
- archive (`comms/archive/`),
- schemas (`comms/schema/{request,response}.schema.json`),
- bot polling and writeback (`src/bot/comms_handler.py`,
  `src/bot/telegram_query_bot.py`,
  `src/comms/{models,state,store,templates,log}.py`).

### 4. Deployment and environment layer
Repo sync, services, timers, and runtime application of repo changes:
- systemd unit files in `deploy/`,
- deploy scripts in `scripts/` and `scripts/ops/`,
- environment scaffolding (`.env.example`,
  `scripts/render_env_from_master.py`,
  `config/master-secrets.template.yaml`),
- VM bootstrap (`scripts/vm_bootstrap.sh`),
- web API self-heal (`scripts/ops/restart_web_api.sh`,
  `.github/workflows/vm-web-api-recover.yml`).

### 5. Governance and documentation layer
Canonical docs, sprint logs, roadmap, audits, workflow docs, evidence
trails (`docs/`, `ROADMAP.md`, `CLAUDE.md` root pointer).

## End-to-End Trade Pipeline

The trade pipeline is implemented in `src/runtime/pipeline.py` and
driven from `src/main.py`. The intended sequence is summarised below;
the **canonical step-by-step map**, with files, inputs/outputs, and
failure modes for every stage, lives in
[`TRADE-PIPELINE.md`](TRADE-PIPELINE.md). The dashboard's **Trade
Process** tab fetches that document at runtime — keep it current.

### Step 1 — Market data intake
Exchange connectors (`src/exchange/bybit_connector.py`,
`src/exchange/binance_connector.py`,
`src/exchange/ib_connector.py::IBMarketData`) and the market-data helpers
in `src/runtime/market_data.py` produce candles and tick state. The bot is
**multi-symbol**: `connector_for_symbol(symbol)` routes each symbol to the
right data source by its `config/instruments.yaml` exchange — BTCUSDT →
Bybit; MES (CME) / MGC + MHG (COMEX) → Interactive Brokers (delayed
futures bars via `reqHistoricalData`, no paid real-time subscription).
See `docs/runbooks/ib-integration.md`.

### Step 2 — Strategy evaluation
Strategy modules in `src/units/strategies/` consume market data and emit
signals. The roster has **12 strategies registered** in
`config/strategies.yaml` (verified 2026-06-10). Each declares a
`symbols:` list and is **scoped to it** by the per-strategy symbol gate
(`intent_multiplexer._collect_intents`, 2026-06-02): a strategy is skipped
on any tick symbol not in its `symbols:` — so a strategy no longer
"evaluates every configured symbol each tick" (the earlier MES-mirror
behaviour is intentionally retired). Current roster, by instrument:
- **Crypto (BTCUSDT):** `turtle_soup`, `trend_donchian`, `ict_scalp_5m`,
  `fvg_range_15m`, `htf_pullback_trend_2h` (all `execution: live`);
  `fade_breakout_4h`, `squeeze_breakout_4h` (`execution: shadow` —
  DEMOTED live→shadow 2026-06-01); `vwap` (`enabled: false` — M7
  kill, no net-of-fee edge); `trend_donchian_1h` (`enabled: false` —
  retired, config adopted into `trend_donchian`).
- **Index/metals futures (IBKR `ib_paper`, paper money):**
  `mes_trend_long_1d` (MES), `mgc_pullback_1d` (MGC),
  `mhg_pullback_1d` (MHG) — all `execution: live`, validated params
  per the 2026-06-02 WS-A metals sleeve.

Each strategy carries a per-strategy `execution: live | shadow` gate in
`config/strategies.yaml` (S9, 2026-05-24): `live` is eligible to execute;
`shadow` runs + LOGS order packages everywhere (data collection) but never
sends a live order. The separate per-strategy `enabled:` flag is the
"does it run at all" switch (an explicit operator decision when set
false). Strategy logic is kept separate from broker execution. The full
per-strategy gate/symbol matrix is sourced from `config/strategies.yaml`
and surfaced on `/api/bot/strategies`; the Change log below records each
promotion/demotion.

### Step 3 — Strategy output normalization & the decider (intent aggregation)
Signals are normalised to the internal order/intent representation used
by the runtime pipeline. The runtime audit logger
(`src/utils/signal_audit_logger.py`) writes
`runtime_logs/signal_audit.jsonl` for every decision.

The execution layer holds **one net position per symbol per account**
(`src/runtime/intents.py::aggregate_intents`), so a single account running
several strategies already routes every tick through a **decider** — today
crude: static priority (highest-priority strategy wins a conflict;
same-direction takes max `target_qty`). This is the **single-account
design** (operator direction 2026-05-24): one pot of capital used
maximally — NOT a per-strategy capital split — with the decider
concentrating the fund on the highest-probability trade each tick.
bybit_1 (demo) and bybit_2 (live) are mirrors (same roster, same decider,
same gates); MES is a separate IBKR book, not a redundant split of the
crypto fund. **Decider-v2** (research) makes the selection smart
(regime-rule or selection-model, highest P(profit)) once ≥2 members are
live — a naive greedy decider lets the high-frequency 2h trend hog the
book and forfeits ~half the blend's return + diversification, so v2's job
is genuine selection. Design + single-account simulation:
[`docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`](sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md),
`scripts/research_decider.py`.

### Step 4 — Risk gating
Before any order reaches broker execution, risk controls decide whether
to allow the signal:
- `RiskManager.approve()` in `src/units/accounts/risk.py` (per-account
  caps: `pos_size`, `daily_usd`, `max_dd_pct`),
- prop-account logic in `src/units/accounts/prop_risk.py`,
- runtime counters in `src/runtime/risk_counters.py`,
- the kill-switch flag (`HALT_FLAG_PATH = /tmp/trader_halt.flag`,
  consumed in `pipeline.py`),
- news veto via `src/news/news_pipeline.py`.

Post-Mode-Mutation-Contract (2026-05-12, see § above): the
RiskManager is also the place that consumes runtime-distress signals
(exchange rejection clusters, etc.) and refuses individual trades for
cause. Account mode is never touched as a side effect of a rejection;
the trader stays live and the next signal is evaluated fresh.

### Step 5 — Runtime order validation
`src/runtime/orders.py::safe_place_order` validates quantities, sizing,
and execution prerequisites. Hard refusal paths exist for invalid or
disallowed orders. Closed-flat invariant lives in
`src/runtime/closed_flat_invariant.py`.

### Step 6 — Broker execution
Only after the steps above does the broker-specific executor send a
live order or simulate one in dry-run mode. `execute._submit_order`
dispatches per account exchange — Bybit (`pybit`) for BTCUSDT, the
`interactive_brokers` branch (`IBClient.place`, `src/units/accounts/ib_client.py`)
for MES futures (native bracket: market entry + TP limit + SL stop, prices
snapped to the 0.25 tick grid; IB uses no API keys — auth is the Gateway
login session). A symbol→exchange dispatch
gate in `src/core/coordinator.py` ensures a BTCUSDT signal can never
route to a futures account and vice-versa. Per-account dry/live mode
is set in `config/accounts.yaml` (`mode: live | dry_run`) and is the
only canonical execution gate; the **only** sanctioned mutation path
for that field is the `set-account-mode` operator action (§ Mode
Mutation Contract). The real-money `ib_live` account is held
`mode: dry_run`; the `ib_paper` account runs `mode: live` (paper
money) and went **live for MES on 2026-05-22**. NOTE (2026-05-24):
the IBKR account is currently **offline pending new-user approval**,
so MES is not executing right now even though the config still
declares it live — the data, edge, and cross-asset diversification
(corr 0.009 vs the BTC book) are validated and
`data/SPX500_1m.parquet` (1m S&P 500, 2020–2026, Dukascopy) is cached
on the trainer, so only the broker login waits. **IBKR is the futures
broker** (a Tradovate sleeve was evaluated and retired; its dead wiring
was purged 2026-06-10). **OANDA v20** joined as the FX/metals broker in
M15 Phase 2 (2026-06-10, S-M15-PHASE2-OANDA): an `oanda` branch in
`execute._submit_order` dispatches to
`src/units/accounts/oanda_client.py::OandaClient` (market order with
broker-side `stopLossOnFill`/`takeProfitOnFill`; one bearer token +
account id from env — `OANDA_API_TOKEN`/`OANDA_ACCOUNT_ID`, practice
host unless `OANDA_ENV=live`). The `oanda_practice` account went **live-on-practice
2026-06-11** (operator-approved; paper money — fxpractice host):
`xauusd_trend_1h` executes practice orders with broker-side
`stopLossOnFill`/`takeProfitOnFill`. Real-money OANDA remains gated
(new keys + `OANDA_ENV=live` + set-account-mode). Runbook:
`docs/runbooks/oanda-integration.md`. **Alpaca** joined as the
US stocks/ETFs broker in M15 Phase 2b (2026-06-11): an `alpaca` branch
dispatches to `src/units/accounts/alpaca_client.py::AlpacaClient`
(**bracket** market orders — entry + TP limit + SL stop atomic; key pair
`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`; paper host unless
`ALPACA_ENV=live`). The `alpaca_paper` account went **live-on-paper 2026-06-11**
(operator-approved): `spy_trend_long_1d` / `qqq_trend_long_1d` /
`gld_pullback_1d` execute paper bracket orders during the US session
(builders gate on `market_hours.is_market_open("us_equity")`).
Real-money Alpaca remains gated (new keys + `ALPACA_ENV=live` +
set-account-mode). Runbook: `docs/runbooks/alpaca-integration.md`.

### Step 7 — Logging and state updates
The runtime records:
- structured signal audit (`runtime_logs/signal_audit.jsonl`),
- pipeline outcomes (`src/runtime/outcomes.py`),
- heartbeat (`runtime_logs/heartbeat.txt`, refreshed every
  `HEARTBEAT_INTERVAL_SECONDS`, default 60s),
- runtime status (`runtime_logs/status.json`),
- trades and order packages (SQLite `trade_journal.db`).

### Step 7.1 — External liveness watchdog (2026-05-11)
The heartbeat file is also watched externally by
[`ict-liveness-watchdog.{service,timer}`](../deploy/), a separate
systemd unit that runs `scripts/check_heartbeat.py` every 60 s.
This is the per-minute dead-man switch on top of the in-process
heartbeat:

- Telegram `[CRITICAL] Trader heartbeat stale` after 5 min of
  stale mtime.
- Autoheal: after 3 consecutive stale checks (~8 min total stall),
  the watchdog dispatches `sudo -n systemctl restart ict-trader-live.service`
  and Telegrams the systemctl exit code. Opt-in via
  `--auto-restart-after N` (currently ON with N=3).
- Boot-grace (`--boot-grace-seconds 600`, 2026-05-28): for the first
  10 min after a host boot the watchdog suppresses the stale/missing
  alert AND autoheal (the trader is expected to be starting under
  systemd) and emits no "recovered" ping — so a VM reboot yields only
  the reboot-vm ping, not heartbeat-stale/recovered spam. Uptime read
  from `/proc/uptime`, fail-open (never suppresses a real post-boot
  stall). A heartbeat still stale once the window closes alerts as a
  genuine failure-to-recover.
- Stdlib-only — runs even when the trader's venv is wedged.
- Full operator runbook: [`docs/runbooks/liveness-watchdog.md`](runbooks/liveness-watchdog.md).

Distinct from `ict-heartbeat.{service,timer}`, which is the
once-daily operator status digest at 13:00 UTC.

The watchdog *restarts* the trader — it does not change the account
mode, and the Mode Mutation Contract does not regulate it. Restarts
are expected and safe; the Prime Directive forbids only the
mode-flip part of an automated response.

### Step 8 — Operator visibility and control
The Telegram bot (`src/bot/telegram_query_bot.py`) plus the FastAPI
diag surface (`src/web/api/routers/diag.py`) expose status, halt and
resume actions, and pending requests. The Streamlit dashboard
(`ict-trader-dashboard`, Streamlit Community Cloud — the React+Vercel
stack was retired 2026-05-12) consumes the unauthenticated Tier 1
endpoints documented in [`api-tier-policy.md`](api-tier-policy.md).

## Research and Validation Pipeline

1. **Concept generation** — notes / Colab notebooks / exploratory
   backtests in `notebooks/`.
2. **Backtest harness** — `src/backtest/` and dispatched runs via
   `scripts/run_backtest.sh`.
3. **Multi-symbol / multi-timeframe validation** — `experiments/`.
4. **Evidence capture** — `experiments/<sprint>/results/*.json` and
   `runtime_logs/validation.jsonl`.
5. **Repo port** — strategy modules under `src/units/strategies/`,
   wired into `config/strategies.yaml`.
6. **Dry-run / staging** — per-account `mode: dry_run` in
   `config/accounts.yaml`, mutated only via `set-account-mode`.
7. **Promotion decision** — Tier 3, requires explicit operator approval.

## Operator Communication Pipeline

The flow is repo-driven and auditable.

### Flow
1. Claude writes a structured request artifact in `comms/requests/`.
2. The VM pulls (`ict-git-sync.timer`, default 5min interval).
3. The Telegram bot detects pending requests and sends them.
4. Operator answers in Telegram (button, "Other" + text, or free text).
5. Bot writes the answer back, sets `status` to
   `answered` / `partially_answered`, and commits.
6. The VM pushes; Claude reads on the next sync.

### Required properties
- isolated from trading logic (no `src/runtime/` or `src/units/` code
  imports `src.comms`),
- atomic file writes (tmp + rename),
- idempotent and safe on restart,
- resistant to duplicate sends,
- resilient to malformed files (unknown `schema_version` is rejected),
- documented for a non-technical operator
  ([`comms/README.md`](../comms/README.md)).

### State model
Statuses: `pending`, `sent`, `partially_answered`, `answered`,
`acknowledged`, `expired`, `cancelled`. Stuck-request alert and
final pre-expiry alert (M1 P1-B) prevent silent expiry.

## Deployment and Sync Pipeline

### Flow
1. Changes merge to `main` on `benbaichmankass/ict-trading-bot`.
2. `ict-git-sync.service` (triggered by `ict-git-sync.timer`, every
   5 min) pulls into `/home/ubuntu/ict-trading-bot` (the working tree).
3. `/opt/ict-trading-bot` is a symlink to the working tree, created by
   `scripts/deploy_diag.sh` on first run.
4. Services reload as designed
   (`ict-trader-live`, `ict-web-api`, `ict-telegram-bot`).
5. Logs in `runtime_logs/` and journalctl confirm whether the update
   applied cleanly.
6. Operator-driven actions go through
   `.github/workflows/system-actions.yml`
   (allowlisted: `status-check`, `pull-latest-logs`, `pull-and-deploy`,
   `restart-bot-service`, `reboot-vm`, `set-account-mode`, plus
   env-toggle and tunnel actions; full list in
   [`claude/system-actions.md`](claude/system-actions.md)).

### Components
| Concern | File |
|---|---|
| Canonical branch | `main` |
| Sync service | `deploy/ict-git-sync.service` |
| Sync timer | `deploy/ict-git-sync.timer` (every 5 min) |
| Trader service | `deploy/ict-trader-live.service` |
| Web API service | `deploy/ict-web-api.service` |
| Telegram bot service | `deploy/ict-telegram-bot.service` |
| Heartbeat timer | `deploy/ict-heartbeat.{service,timer}` — once-daily operator status digest (13:00 UTC) |
| Liveness watchdog | `deploy/ict-liveness-watchdog.{service,timer}` — per-minute dead-man switch on `heartbeat.txt` mtime; alerts within 5 min and autoheals trader after 8 min stall (PRs #950/#956). Runbook: `docs/runbooks/liveness-watchdog.md`. Restarts only; does not change account mode. |
| Hourly snapshot | `deploy/ict-hourly-snapshot.{service,timer}` |
| Smoke once | `deploy/ict-smoke-once.service` |
| Claude bridge | `deploy/ict-claude-bridge.service` |
| Env-check | `deploy/ict-env-check.service` |
| Web API watchdog | `deploy/ict-web-api-watchdog.{service,timer}` — restarts `ict-web-api.service` when the FastAPI surface is unreachable |
| IB Gateway (isolated VM) | `deploy/ict-ib-gateway-reset.{service,timer}` — headless IB Gateway (Docker + socat) now runs on its **own dedicated Ampere VM** (`ict-ib-gateway`, `10.0.0.251`), off the live trader's micro (gateway-isolation, Plan B, 2026-06-10). Recovery is one deterministic daily `docker restart` (05:30 UTC, gated to the gateway VM by `ConditionPathExists=/etc/ict/ib-gateway-docker.env`); the reactive 5-min restart-loop watchdog is retired — `ict-ib-gateway-watchdog.{service,timer}` is now a once-daily **alert-only** probe. The trader-side connect breaker (`IB_PROBE_TIMEOUT_S`/`IB_BREAKER_COOLDOWN_S`) keeps a gateway/network blip off the BTCUSDT loop. Runbook: `docs/runbooks/ib-integration.md` § "Gateway isolation redesign" |
| Health snapshot | `deploy/ict-health-snapshot.{service,timer}` — cron health-check report consumed by `/health-review` |
| Insights generators (M13) | `deploy/ict-insights-generator.{service,timer}` (fast, 15 min) + `deploy/ict-insights-generator-strategies.{service,timer}` (slow, 60 min) — AI-Analyst cache writers behind `/api/bot/insights/*` |
| Shadow-log rotation | `deploy/ict-shadow-log-rotate.{service,timer}` — size/age rotation of `shadow_predictions.jsonl` |
| Claude VM runner | `deploy/claude-vm-runner@.service` — self-hosted-runner wiring for VM-side workflows |
| Deploy script | `scripts/deploy_diag.sh`, `scripts/deploy_pull_restart.sh` |
| VM bootstrap | `scripts/vm_bootstrap.sh` |
| Web API restart wrapper | `scripts/ops/restart_web_api.sh` |
| Mode-flip wrapper | `scripts/ops/set_account_mode.sh` (PR #978, 2026-05-12) |

Rollback / recovery steps and the live-trading deploy procedure live in
[`DEPLOYMENT_LIVE_TRADING.md`](../DEPLOYMENT_LIVE_TRADING.md) and
[`docs/claude/deployment-ops.md`](claude/deployment-ops.md).

## Data Persistence Model (canonical store)

Adopted S-PERSIST-CANON (2026-05-23). Every piece of data the system
produces — on the LIVE trader VM and the TRAINER VM — is persisted into
one central, canonical store that is browsable from the dashboard's
**Data Explorer**. The store is federated across two SQLite files on the
OCI block volume (`/data/bot-data`):

| DB | Producer | Tables | Path resolver |
|---|---|---|---|
| `trade_journal.db` | LIVE trader | `trades`, `order_packages`, `signals` (dual-write, gated by `SIGNAL_DUAL_WRITE_DISABLED`), `backtest_results` (on-demand `/test` runs only), `daily_risk_state`, `strategy_versions` | `src.utils.paths.trade_journal_db_path()` (Python) / `scripts/ops/_lib.sh::runtime_db_path` (shell) |
| `trainer_store.db` | TRAINER (ingested) | `training_cycle`, `dataset_builds`, `db_pulls`, `model_registry`, `experiment_runs`, `backtest_sweeps` | `src.utils.paths.trainer_store_db_path()` |

**Single canonical DB-path resolver.** Both DB paths resolve env-first
(`TRADE_JOURNAL_DB` / `TRAINER_STORE_DB`) → `$DATA_DIR/<file>` →
repo-root, and are **never** a CWD-relative basename. The historical
`os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"` idiom resolved
relative to each process's working directory, so any process that started
without the systemd env wrote a fresh DB under its CWD — that is how the
stray duplicate journals appeared on the live VM
(`/home/ubuntu/ict-trading-bot/trade_journal.db`, `…/src/bot/…`). The
`canonical-db-resolver` CI guard (`scripts/check_canonical_db_resolver.py`)
forbids both the CWD-relative fallback and inline `TRADE_JOURNAL_DB`
env-reads outside the resolver, in both shell wrappers and Python.

**Trainer data flow.** The trainer VM rsyncs its JSONL/JSON telemetry
into `runtime_logs/trainer_mirror/` on the live VM
(`scripts/ops/publish_trainer_mirror.sh`). `src/units/db/trainer_store.py`
ingests that mirror into `trainer_store.db` — an idempotent full rebuild,
lazily triggered (mtime-gated) on each Data Explorer read, so the sidecar
is always fresh without a dedicated timer. The sidecar is read-mostly and
deliberately separate from `trade_journal.db` so ingest never contends
with the 24/7 trader. The pre-existing file-based endpoints
(`/api/bot/ml/*`, `/api/bot/backtests/sweeps`) remain; the sidecar makes
the same data SQL-queryable in one place.

**`daily_risk_state` is self-healing.** `RiskManager` rebuilds today's
per-account `daily_pnl` (summed from `trades`) and equity high (from
`runtime_logs/balance_snapshots.json`) on init and on every gate check,
then persists. Before this, `record_trade_result()`/`update_equity()` had
no runtime callers, so the table stayed empty and the daily-loss /
max-drawdown caps reset to 0 on every restart (and never accumulated).
See [`CLAUDE.md`](../CLAUDE.md) § Canonical persistence model.

## GitHub Actions and Automation Layer

GitHub Actions are part of the architecture, not a side note.
The canonical reference is
[`docs/github-actions-workflows.md`](github-actions-workflows.md). It
catalogues every workflow under `.github/workflows/` with trigger,
purpose, secrets, outputs, and the rules for when Claude may modify it.

Current workflows include CI guards (`pytest-collect`, `ruff-lint`,
`secret-scan`, `dry-run-guard`, `env-gate-guard`,
`silent-empty-guard`), VM ops (`system-actions`, `vm-diag-snapshot`,
`vm-web-api-recover`, `vm-net-diag`, `vm-net-fix`, `vm-cloud-fix`),
training (`training-run`, `training-rerun-5m`, `hf-cron`),
inventory/labels (`repo-inventory`, `bootstrap-labels`,
`branch-protection-sync`), and the autonomous follow-on driver
(`continue-work`).

## Repo Responsibility Map

| Area | Path | Notes |
|---|---|---|
| Runtime pipeline | `src/runtime/` | `pipeline.py`, `orders.py`, `validation.py`, `health.py`, `heartbeat.py`, `outcomes.py` |
| Strategies | `src/units/strategies/` | Strategy modules; wired via `config/strategies.yaml` |
| Strategy registry | `src/strategy_registry.py` | Single source of truth for which strategies exist |
| Account / risk | `src/units/accounts/` | `risk.py`, `prop_risk.py`, `execute.py`, `__init__.py` (`load_accounts`). After the safeguards PR follow-on, `_DRY_RUN_OVERRIDES` and `set_account_dry_run()` are deleted; `_resolve_mode()` reads YAML directly. |
| Exchange connectors | `src/exchange/` | Bybit, Binance |
| ICT detection | `src/ict_detection/` | Reusable signal-detection components |
| News layer | `src/news/` | `news_pipeline.py` |
| Bot / comms code | `src/bot/`, `src/comms/` | Telegram handlers, comms store, schemas |
| Web API | `src/web/api/` | FastAPI app + routers; runtime status writer at `src/web/runtime_status.py` |
| Comms artifacts | `comms/` | Operator request/response artifacts and schemas |
| Config | `config/` | `accounts.yaml`, `strategies.yaml`, `units.yaml`, env templates. `accounts.yaml` `mode:` mutated only via `set-account-mode`. |
| Deploy | `deploy/` | systemd unit + timer files |
| Scripts / ops | `scripts/`, `scripts/ops/` | Deploy, diag, ops wrappers (incl. `set_account_mode.sh`) |
| Tests | `tests/` | Unit + integration |
| Docs | `docs/` | Canonical docs (this dir), claude operating notes, sprint logs |
| AI-platform doc | [`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md) | AI-specific architecture (M9 + M10). Subordinate canonical doc; covers the model layer + deployment tiers + Oracle/HF runtime split. |
| GitHub Actions | `.github/workflows/` | All CI / VM ops / training workflows |

## AI-traders training workflow (separate from live trading)

Live trading is fully deterministic — no model is in the live
path. The AI-traders training pipeline is a **separate concern**
that produces research-only baselines under `ml/`. Operator-driven
training sessions follow the established workflow:

1. **Collect feedstock.** The autonomous `/performance-review` skill
   (split out of `/health-review` 2026-05-26) emits per-decision
   `trade_decision_grades[]` for every order package since the last
   review and persists each one — keyed by `order_package_id` — to
   [`comms/claude_strategy_scores.jsonl`](../comms/claude_strategy_scores.jsonl)
   (the durable, repo-tracked score log; supersedes the older
   `comms/claude_trade_scores.jsonl` keyed by `trade_id`, which carried
   no real rows). These labelled grades flow into the `trade_outcomes`
   family
   ([`ml/datasets/families/trade_outcomes.py`](../ml/datasets/families/trade_outcomes.py))
   and the `setup_labels` family
   ([`ml/datasets/families/setup_labels.py`](../ml/datasets/families/setup_labels.py))
   as their primary label source.
2. **Build datasets.** `python -m ml.datasets build <family>` writes
   versioned artifacts under `<output>/<family>/<scope>/<tf>/<version>/`
   with mandatory metadata + leakage discipline. Buildable families:
   `trade_outcomes`, `backtest_results`, `market_raw`,
   `market_features`, `setup_labels`. Family taxonomy:
   [`docs/data/dataset-taxonomy.md`](data/dataset-taxonomy.md).
3. **Train baselines.** `python -m ml train <manifest>` runs a YAML
   manifest end-to-end (split → fit → evaluate → register). Established
   manifests:
   - [`ml/configs/baseline-trade-outcome-winrate.yaml`](../ml/configs/baseline-trade-outcome-winrate.yaml)
     (WS5-A; per-strategy historical winrate).
   - [`ml/configs/baseline-trade-outcome-global.yaml`](../ml/configs/baseline-trade-outcome-global.yaml)
     (WS4-FU; global-mean sanity baseline).
   - [`ml/configs/baseline-regime-classifier.yaml`](../ml/configs/baseline-regime-classifier.yaml)
     (WS5-B-PART-2; 2-class range/volatile regime classifier on `market_features`).
   - [`ml/configs/baseline-setup-quality.yaml`](../ml/configs/baseline-setup-quality.yaml)
     (WS5-C; setup-quality scorer on `setup_labels`).
4. **Compare runs.** `python -m ml compare <id-a> <id-b>` surfaces
   shared-metric deltas as JSON.
5. **Promotion is gated past shadow.** Since 2026-05-19 every
   baseline manifest declares `target_deployment_stage: shadow` and
   `_DEFAULT_STAGE` is `shadow`, so a clean training run lands a model
   ready for shadow consumption (predictions logged, decisions
   unchanged). Promotion past shadow (advisory → limited_live →
   live_approved) still requires
   `python -m ml promote-stage --by <name> --reason <text>` and
   operator approval. Models can be parked back at `research_only`
   via the same CLI when an operator wants them out of the shadow
   channel without retraining.

Training sessions MUST use these established baselines + manifests
rather than reinventing. Adding a new baseline follows the
"Adding a new family" / "Adding a new trainer" rules in
[`docs/data/dataset-taxonomy.md`](data/dataset-taxonomy.md) and
[`docs/ml/training-center.md`](ml/training-center.md).

**New strategies + the cycle (S9, 2026-05-24).** The recurring
`run_training_cycle.sh` trains every manifest in `ml/configs/` each
cycle. The `trade_outcomes` manifests are roster-agnostic —
`baseline-trade-outcome-global.yaml` is `symbol_scope: all` (all rows,
strategy ignored) and `baseline-trade-outcome-winrate.yaml` groups by
`strategy_name` — so trades from the new members (`trend_donchian`,
`fade_breakout_4h`, and `squeeze_breakout_4h` once merged) **feed the
datasets automatically with no manifest change**. No per-strategy
manifest is scoped to the new strategies yet, and none is required for
ingestion. The one **new training target** is the cross-strategy
**decider-v2 selection model** ("which signal to trust now" — the
models-in-the-loop belongs here, NOT the per-strategy entry filter,
which failed because the trend edge is exit-driven): it is research-
stage and should be added as a manifest only **once ≥2 members are
live** (until then it has insufficient multi-member feedstock). Design:
[`docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`](sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md);
simulator: `scripts/research_decider.py`.

The full AI-platform architecture (five-layer model, leakage rules,
forbidden behaviors, model registry append-only invariant) lives in
[`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).

### VM topology (S-AI-WS9, updated 2026-06-10 gateway-isolation Plan B)

The "no heavy training on the Oracle live VM" non-negotiable
([`AI-TRADERS-ROADMAP.md`](AI-TRADERS-ROADMAP.md)) is enforced by
**topology**, not just policy. The system now spans **three** VMs in the
same compartment + VCN — the money box is deliberately the smallest and
most isolated:

| VM | Shape | Role | Systemd units | Marker file |
|---|---|---|---|---|
| **Live trader** (`158.178.210.252`) | `VM.Standard.E2.1.Micro` — **1 OCPU / 1 GB, x86** (a separate AMD Always-Free allocation, NOT the Ampere pool; fixed shape) | Deterministic trade execution; FastAPI dashboard surface | `ict-trader-live.service`, `ict-web-api.service` | (none today; pre-WS9) |
| **Training-center** (`158.178.209.121`) | `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere) | Model training, dataset builds, registry writes, experiment runs | `ict-trainer.service` (disabled by default), `ict-trainer.timer` (disabled by default) | `/etc/ict-trainer-vm.role` → `training-center` |
| **IB Gateway** (`ict-ib-gateway`, private IP `10.0.0.251`) | `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere) | Headless IB Gateway (Docker + socat) for MES/IBKR — isolated off the money box | `ict-ib-gateway-reset.{service,timer}` (daily 05:30 UTC `docker restart`) | `/etc/ict/ib-gateway-docker.env` (gates the reset.service) |

**Ampere Always-Free budget:** trainer 1 + gateway 1 = **2 of 4 OCPUs.**
The gateway was moved onto its own VM (Plan B) because the heavy
Java/Xvfb/IBC gateway sharing the 1 GB micro with the trader caused the
2026-06-10 CPU-wedge cascade; the trader now reaches it across the private
subnet (`config/accounts.yaml::ib_paper.ib_host = 10.0.0.251`). The
**live→3-OCPU Ampere migration is PAUSED** — with the gateway gone the
micro may hold trader + web-api + sidecars on its 2 vCPU; measure first,
migrate only if it can't. Full topology + rationale:
[`docs/runbooks/ib-integration.md`](runbooks/ib-integration.md)
§ "Gateway isolation redesign" and `CLAUDE.md` § "VM authority split".

The training-center VM is provisioned via
[`.github/workflows/provision-training-vm.yml`](../.github/workflows/provision-training-vm.yml)
+ [`scripts/ops/provision_training_vm.py`](../scripts/ops/provision_training_vm.py).
Cloud-init bootstraps it from
[`deploy/training-vm-cloud-init.yaml`](../deploy/training-vm-cloud-init.yaml)
with the repo cloned to `/home/ubuntu/ict-trading-bot` and the
trainer systemd unit installed but **disabled** — the operator
opts in to training cycles explicitly, so the Always Free quota
isn't consumed by idle compute.

The **authority split** is documented in
[`docs/claude/trainer-vm-mode.md`](claude/trainer-vm-mode.md): trainer
VM is autonomous-Claude (provision, SSH, install, train, register,
promote up to `live_approved`); live VM stays under the restrictive
contract in [`docs/claude/vm-operator-mode.md`](claude/vm-operator-mode.md).

The boundary that prevents trainer autonomy from leaking into live
**decisions** is the **stage** boundary, not the YAML wire-up
(2026-05-19 update). As of the default-flip, any model registered
at `target_deployment_stage: shadow` is auto-wired onto every
strategy's shadow channel — predictions are logged on signals
without operator approval. Operator approval is still required for
the cross into live influence: the
`shadow → advisory → limited_live → live_approved` promotion chain
remains gated, and the live-trader's order package is unaffected by
shadow predictions per the WS7 non-negotiable. An operator who
wants a strategy *not* to log against the auto-wired set sets
`shadow_model_ids: []` (explicit opt-out) or provides an explicit
non-empty list in `config/strategies.yaml` to pin specific models.

**Cross-VM data flow** (filed for follow-up — not yet wired):
- Live VM owns `trade_journal.db`. The training center needs
  read access for label feedstock. Options: scheduled rsync from
  live VM, or read via the `/api/diag/*` surface over HTTPS. No
  decision yet.
- Training center owns the registry-store + experiment runs.
  Promoted models flow back to the live VM via
  `git pull` + the operator's deploy workflow (existing
  `system-actions.yml::pull-and-deploy`).

**Cross-VM SSH**: both VMs accept the same `VM_SSH_KEY` (operator
chose key-reuse — same private key, simpler rotation). If the
threat model later requires isolated keys, the workflow accepts
a `TRAINER_VM_SSH_KEY` secret override.

## Evidence and Documentation Flow

Every major code change must produce or update at least one of:
- a sprint log (`docs/sprint-logs/<id>.md`),
- the roadmap (`ROADMAP.md`),
- a canonical doc (this doc, the rules doc, the AI-platform doc, or
  `docs/github-actions-workflows.md`),
- subsystem docs under `docs/claude/`, `docs/operator/`, etc.,
- evidence in `tests/`, `experiments/`, or `runtime_logs/`.

Chat memory and PR descriptions are not the system of record.

## Known Architecture Risks (verified 2026-05-10)

The following risks are observed in the current repo and tracked in the
roadmap rather than silently ignored:

- **Stale repo references**: a number of active docs and scripts still
  reference `the-lizardking/ict-trading-bot`. Inventory is maintained
  in the audit section of `docs/sprint-logs/S-CANON-1.md`.
- **Doc proliferation under `docs/claude/`**: 50+ working notes; the
  canonical-doc set above is the new authoritative apex. Older notes
  remain useful but non-authoritative on policy.
- **Sprint summary divergence**: sprint summaries and sprint prompts
  exist in two folders (`docs/sprint-summaries/`, `docs/sprint-plans/`).
  New work uses `docs/sprint-logs/` with the canonical template.
- **No GitHub Actions reference doc** (now resolved by
  [`github-actions-workflows.md`](github-actions-workflows.md)).
- **Empty / spurious sqlite-connection-named files** in the repo root
  (`<sqlite3.Connection object at 0x...>`). Diagnosed in this audit.
- **AI-scope known gaps** — see
  [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
  § Known Gaps. The current `ml/` tree is vestigial; WS3–WS10 deliver
  the target dataset / training / registry / monitoring stack.

## Architecture Update Rule

This document must be reviewed whenever a sprint changes:

- runtime flow,
- subsystem boundaries,
- deployment behavior,
- operator communication behavior,
- GitHub Actions automation,
- or any canonical file path used as part of the operating model.

When the change touches any stage of the trade pipeline (any block in
[`TRADE-PIPELINE.md`](TRADE-PIPELINE.md)), that document must be
updated in the same sprint and the dashboard's **Trade Process** tab
visually verified after merge to `main`. The dashboard fetches the
pipeline doc directly from this repo, so a stale doc means a stale
operator UI.

For AI-scope changes (data → feature → model → orchestration →
control layer boundaries, dataset families, model registry, deployment
tiers, Oracle/HF split) the corresponding doc to update is
[`architecture/ai-model-platform.md`](architecture/ai-model-platform.md).

## Verification Checklist (current state)

> **Note (2026-06-10):** the checklist below is the 2026-05-10 foundational
> snapshot — the named entrypoints/modules are still accurate, but the
> **live strategy roster and service inventory have grown substantially
> since** (5→12 strategies; IB-gateway / web-api / insights / shadow-rotate
> units added). For current state see § "Step 2 — Strategy evaluation" and
> the Deployment "Components" table above, both re-verified against
> `config/strategies.yaml` + `deploy/` on 2026-06-10.

Confirmed against the repo on 2026-05-10:

- [x] Runtime entrypoint: `src/main.py` → `src/runtime/pipeline.py`
- [x] Risk manager: `src/units/accounts/risk.py`
- [x] Order execution: `src/runtime/orders.py` and
      `src/units/accounts/execute.py`
- [x] Strategy registry: `src/strategy_registry.py` driven by
      `config/strategies.yaml`
- [x] Telegram bot entrypoint: `src/bot/telegram_query_bot.py`
- [x] Comms directory: `comms/` with `requests/`, `archive/`, `schema/`
- [x] Deploy scripts: `scripts/deploy_diag.sh`,
      `scripts/deploy_pull_restart.sh`
- [x] systemd files: `deploy/ict-*.{service,timer}`
- [x] Existing GitHub Actions: enumerated in
      [`github-actions-workflows.md`](github-actions-workflows.md)
- [x] Trade pipeline canonical map:
      [`TRADE-PIPELINE.md`](TRADE-PIPELINE.md)
- [x] AI-scope architecture doc:
      [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
      (S-AI-WS1, 2026-05-10)
- [x] Mode Mutation Contract (§ above): `scripts/ops/set_account_mode.sh`
      operator action shipped in PR #978 (2026-05-12). Doc-level contract in
      this commit; code-level cleanup of `_DRY_RUN_OVERRIDES` +
      `set_account_dry_run` + the breaker auto-flip pending in the
      safeguards PR follow-on.

---

## Change log

Architecture-impacting changes (per the rubric in
[`architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`](architecture/ARCHITECTURE-CHANGE-CHECKLIST.md))
land a row here. Per-PR ledger sits in
[`ROADMAP.md`](../ROADMAP.md); the table below is curated and
filtered to architecture-level deltas only.

| Date | Sprint | Change | Files touched | Operator impact |
|---|---|---|---|---|
| 2026-05-10 | S-CANON-1 | Canonical-doc adoption: this file supersedes the older `docs/architecture.md` and the architecture sections of root `CLAUDE.md`. Companion rules doc + sprint-log template + canonical workflows doc all stand. | `docs/ARCHITECTURE-CANONICAL.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `docs/github-actions-workflows.md` | None — informational. |
| 2026-05-10 | S-AI-WS1..WS4 | AI platform baseline: pipeline stage contracts (`docs/pipeline/stage-contracts.md`), typed dataclasses (`src/pipeline/types.py`), dataset framework (`ml/datasets/`), training center + registry + Predictor + splitters + compare (`ml/`). | `ml/`, `src/pipeline/`, `docs/pipeline/`, `docs/architecture/ai-model-platform.md` | None — research-only. |
| 2026-05-10 | S-AI-WS5-A..F | Six baseline models registered + paired manifests. None promoted past `candidate`. | `ml/configs/*.yaml`, `ml/trainers/`, `ml/datasets/families/`, `ml/registry-store/` | None — research-only. |
| 2026-05-10 | S-AI-WS7-PART-1 | Model registry gains `target_deployment_stage` + canonical stage ladder (`research_only` → `candidate` → `backtest_approved` → `shadow` → `advisory` → `limited_live` → `live_approved`). Append-only `StatusEvent` history; `promote_stage()` requires `--by` + `--reason`. | `ml/registry/`, `ml/promotion/` | None — registry unread by runtime. |
| 2026-05-10 | S-AI-WS7-PART-2..6 | Shadow harness complete. `src/runtime/shadow_adapter.py::with_shadow_pred` + `with_shadow_preds` helpers (per-predictor failure isolation). `ml/shadow/factory.py` resolves `shadow_model_ids` against the registry with a stage gate (`{shadow, advisory, limited_live, live_approved}` allowed; `{research_only, candidate, backtest_approved}` refused). Both production strategies (`vwap` + `turtle_soup`) wired. `Coordinator._shadow_predictors_cache` lifts the factory call to O(reloads). | `src/runtime/shadow_adapter.py`, `ml/shadow/*`, `src/units/strategies/vwap.py`, `src/units/strategies/turtle_soup.py`, `src/core/coordinator.py`, `config/strategies.yaml` | None unless operator sets a non-empty `shadow_model_ids`. |
| 2026-05-10 | S-AI-WS8-PART-1 | Shadow-predictions audit log gains an operator surface: `ml/shadow/inspector.py` (streaming reader + filters + per-(model_id, stage) aggregate + text formatters) + `python -m ml shadow-inspect`/`shadow-stats` CLI subcommands. | `ml/shadow/inspector.py`, `ml/cli.py` | None — diagnostic tooling, read-only. |
| 2026-05-10 | S-AI-WS8-PART-2 | Public API surface: `/api/bot/shadow/{predictions,stats}` Tier-1 endpoints over `runtime_logs/shadow_predictions.jsonl`. Envelope `{log_present, log_path, records[], count}` distinguishes "no records yet" from "log missing". Same `ml.shadow.inspector` backing as the CLI — zero duplicate parsing. | `src/web/api/routers/shadow.py`, `src/web/api/main.py`, CLAUDE.md | None — read-only, additive. Dashboard consumes once UI lands. |
| 2026-05-10 | S-AI-WS8-PART-3 | Drift detector: `ml/shadow/drift.py` (KS statistic + PSI score + window-over-window summary stats). New `GET /api/bot/shadow/drift?model_id=X` endpoint with reference / current window parameters. New `python -m ml shadow-drift` CLI subcommand. | `ml/shadow/drift.py`, `src/web/api/routers/shadow.py`, `ml/cli.py` | None — read-only, additive. |
| 2026-05-10 | S-AI-WS7-FU | Shadow audit-log rotation: `scripts/ops/rotate_shadow_log.py` + `deploy/ict-shadow-log-rotate.{service,timer}` (disabled by default). Size-OR-age thresholds (default 100 MiB / 7 days) with atomic rename + same-day collision handling. | `scripts/ops/rotate_shadow_log.py`, `deploy/ict-shadow-log-rotate.*`, `tests/test_rotate_shadow_log.py` | Operator enables timer when shadow mode activates. |
| 2026-05-10 | S-AI-WS9 | Two-VM topology: training-center VM provisioning via OCI Always Free Ampere A1. New `scripts/ops/provision_training_vm.py`, `.github/workflows/provision-training-vm.yml` (dispatch + issue-trigger), `deploy/training-vm-cloud-init.yaml`, operator runbook. Makes "no heavy training on the live VM" enforced by topology, not just policy. New VM bootstraps with `ict-trainer.service` DISABLED — operator opts in. | `scripts/ops/provision_training_vm.py`, `.github/workflows/provision-training-vm.yml`, `deploy/training-vm-cloud-init.yaml`, `docs/runbooks/training-vm.md`, this file | Operator triggers workflow once to spin up the trainer VM; no impact on live trader. |
| 2026-05-10 | S-AI-WS9-FU | `scripts/ops/run_training_cycle.sh` lands — the body of `ict-trainer.service`. Pulls main, manages venv, iterates `ml/configs/` manifests, emits JSONL events. Stops at `research_only` (the per-PR follow-up `train_and_register_ws5_baselines.sh` walks the ladder). | `scripts/ops/run_training_cycle.sh`, `tests/test_run_training_cycle_sh.py` | Operator can now enable `ict-trainer.service` without the unit failing on missing ExecStart. |
| 2026-05-10 | S-AI-WS10 | Architecture-doc enforcement scaffold. New `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`, `.github/PULL_REQUEST_TEMPLATE.md` with arch-impact checkboxes, advisory `.github/workflows/arch-doc-guard.yml` (soft `::warning`, never fails). | `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/workflows/arch-doc-guard.yml`, `scripts/arch_doc_guard.py`, this file | None — informational. |
| 2026-05-10 | S-AI-WS10-FU | WS10 follow-ups: opt-in pre-commit hook `scripts/git-hooks/pre-commit` wrapping the same `arch_doc_guard.py`, weekly `.github/workflows/doc-audit-weekly.yml` that audits the Verification Checklist for broken paths. Both shipped with their own tests. | `scripts/git-hooks/pre-commit`, `scripts/install-hooks.sh`, `.github/workflows/doc-audit-weekly.yml`, `scripts/ops/audit_verification_checklist.py` | None — informational. |
| 2026-05-11 | S-AUTH-SPLIT | Two-VM trust-contract split adopted. New `docs/claude/trainer-vm-mode.md` (autonomous-Claude charter — provision / SSH / train / register / promote without operator-in-the-loop, bounded by hard limits on cross-VM and live-config writes). `docs/claude/vm-operator-mode.md` scoped explicitly to the live VM. Operator-approval gate on model promotions clarified: applies only at the live-VM `shadow_model_ids` YAML wiring, not at registry stage promotion. | `docs/claude/trainer-vm-mode.md` (NEW), `docs/claude/vm-operator-mode.md`, `CLAUDE.md`, `docs/AI-TRADERS-ROADMAP.md`, `docs/runbooks/training-vm.md`, `.github/workflows/provision-training-vm.yml` | None on live VM behaviour. Claude can now operate the trainer end-to-end. |
| 2026-05-11 | S-AI-WS9-AUTORETRY | Inter-process contract: new `.github/workflows/provision-training-vm-auto-retry.yml` fires every 10 min, checks via OCI whether `ict-trainer-vm` exists, dispatches the provision workflow if not. On first detection of `exists=true`, files a one-shot `[trainer-vm-up]` GitHub issue so the operator gets a notification via repo subscription. Bypasses the "OCI Always Free A1 capacity is intermittent" wall without operator polling. | `.github/workflows/provision-training-vm-auto-retry.yml` (NEW) | None — autonomous retry until the trainer VM lands. |
| 2026-05-11 | S-AI-WS5-BOOTSTRAP | New `scripts/ops/train_and_register_ws5_baselines.sh` — the trainer's "first action" once the VM is up. Trains every `baseline-*.yaml`, walks each new model id up the promotion ladder to `TARGET_STAGE` (default `shadow`, the minimum the WS7 factory will load). Emits JSONL to `runtime_logs/trainer/ws5_baseline_kickoff.jsonl`. Distinct from the recurring `run_training_cycle.sh`. | `scripts/ops/train_and_register_ws5_baselines.sh`, `tests/test_train_and_register_ws5_baselines_sh.py`, `docs/runbooks/training-vm.md` | None until the trainer VM is up + the operator runs the script there. |
| 2026-05-11 | S-AI-WS10-CLOSEOUT | WS10 explicitly closed. Change log refreshed to reflect today's S-AUTH-SPLIT, S-AI-WS9-AUTORETRY, S-AI-WS5-BOOTSTRAP plus the previously-missing S-AI-WS8-PART-2/3, S-AI-WS7-FU, S-AI-WS9-FU, S-AI-WS10-FU rows. Known Gaps section pruned (resolved entries removed; new gaps added) so the section reflects today's queue. Roadmap WS10 row marked DONE. | `docs/ARCHITECTURE-CANONICAL.md`, `docs/AI-TRADERS-ROADMAP.md`, `docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md` | None — the close-out is itself the verification that WS10 prevents drift. |
| 2026-05-12 | (post-S-CANON) | **Mode Mutation Contract enshrined** (§ above). `set-account-mode` operator action (PR #978) becomes the only path to mutate `config/accounts.yaml` `mode:`. Prime Directive added to CLAUDE-RULES-CANONICAL.md. Follow-on safeguards PR queued to remove the remaining auto-flip vectors: `_DRY_RUN_OVERRIDES` + `set_account_dry_run()` in `src/units/accounts/__init__.py`, the breaker auto-flip in `src/core/coordinator.py:1048-1068`, and the Telegram `/accounts dry\|live` handler (refactored to dispatch `set-account-mode`). | `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`, `docs/claude/trading-mode-flags.md`, `CLAUDE.md`, `.github/workflows/system-actions.yml` (PR #978), `scripts/ops/set_account_mode.sh` (PR #978), `docs/claude/system-actions.md` (PR #978) | The trader stays live by design. Operator dispatches `set-account-mode` to flip mode; per-trade Telegram on every RiskManager rejection arrives in the safeguards PR. |
| 2026-05-19 | (shadow-default-flip) | **Shadow becomes the default deployment stage; auto-wire replaces per-strategy `shadow_model_ids` lists.** `_DEFAULT_STAGE` flipped from `research_only` → `shadow` in `ml/registry/model_registry.py`; all 9 baseline manifests (`ml/configs/baseline-*.yaml`) updated to declare `target_deployment_stage: shadow`; direct one-hop edges added in `_STAGE_TRANSITIONS` (`research_only`/`candidate` → `shadow` plus rollbacks). `ml.shadow.factory.discover_shadow_stage_model_ids()` returns every shadow-stage model id; `Coordinator._get_shadow_predictors` falls back to that discovery when a strategy has no `shadow_model_ids` (or explicit `None`). Strategies opt out with `shadow_model_ids: []` or pin with a non-empty list. New `python -m ml promote-stage` CLI subcommand (with `--all-pre-shadow` bulk helper) for legacy-registry migration. `turtle_soup` and `ict_scalp_5m` flipped to the auto-wire default in `config/strategies.yaml`. The boundary that prevents trainer-VM autonomy from leaking into live decisions moves from the YAML wire-up to the `shadow → advisory` promotion gate; the latter still requires operator approval. | `ml/registry/model_registry.py`, `ml/configs/baseline-*.yaml`, `ml/cli.py`, `ml/shadow/factory.py`, `ml/shadow/__init__.py`, `src/core/coordinator.py`, `config/strategies.yaml`, `docs/ARCHITECTURE-CANONICAL.md`, `scripts/ops/train_and_register_ws5_baselines.sh`, `scripts/ops/run_training_cycle.sh`, plus matching tests. | Live-VM impact: once the trainer-VM registry-store is migrated (separate diag relay) and the live VM next reloads strategy config, every shadow-stage model starts logging predictions on every strategy's signals to `runtime_logs/shadow_predictions.jsonl`. Order package is unaffected (WS7 non-negotiable). |
| 2026-05-19 | (post-flip rollout — PRs #1521 / #1529 / #1530 / #1538 / #1548) | **Five follow-on PRs that landed in the same session, post-shadow-default flip.** PR #1521: `ShadowPredictor` now writes the full signal-time `feature_row` (`strategy_name`, `symbol`, `direction`, `confidence`, `setup_type`, `killzone`, `bias`) alongside the existing `row_keys`; `/api/bot/trades/scores` join filters by `feature_row.symbol == trade.symbol` so concurrent BTC/ETH trades no longer cross-pollinate. PR #1529: `_close_trade_from_order_status` backfills `trade.entry_price` from Bybit's `avg_price` (closes the `execution-quality-baseline-v0` mae=0.0 degeneracy by giving the dataset real signed slippage); `scripts/ops/sync_trainer_data.sh::LIVE_VM_AUDIT_PATH` default updated to `/data/bot-data/runtime_logs/signal_audit.jsonl` (the canonical post-2026-05-12 DATA_DIR path) so `setup_labels_audit` stops freezing. PR #1530: `baseline-backtest-mean.yaml` + `baseline-post-trade-review.yaml` renamed to `.yaml.disabled` until their feedstock pipelines (Telegram `/test` runs + `/health-review` skill output) accumulate enough rows to train on. PR #1538: new `python -m ml backfill-shadow-predictions` CLI replays every historical trade (1,565 on the trainer's synced DB) through every shadow-stage model and writes the results to `runtime_logs/shadow_predictions_backfill.jsonl`; records carry `backfill_kind: "retroactive_decision"` + `trade_id` so `/api/bot/trades/scores` joins them by `trade_id` (deterministic, regardless of timestamp), and the existing real-time symbol+timestamp-window fallback handles non-backfill records. The endpoint envelope gains `backfill_log_present`, `backfill_log_path`, and a per-score `backfill_kind` field. PR #1548: `Coordinator._get_shadow_predictors` resolves the default audit log path through `runtime_logs_dir()` so the trader writes to the same canonical location `trade_scores.py` reads from (closing the writer-vs-reader split where the live trader wrote to `/home/ubuntu/ict-trading-bot/runtime_logs/` while the endpoint read from `/data/bot-data/runtime_logs/`). | `ml/predictors/shadow.py`, `ml/shadow/inspector.py`, `ml/shadow/backfill.py` (NEW), `ml/cli.py`, `src/web/api/routers/trade_scores.py`, `src/core/coordinator.py`, `src/runtime/order_monitor.py`, `scripts/ops/sync_trainer_data.sh`, `ml/configs/baseline-{backtest-mean,post-trade-review}.yaml.disabled`, `CLAUDE.md` (`/api/bot/trades/scores` shape), plus matching tests. | Live-VM impact: the dashboard's `/api/bot/trades/scores` now serves 10,955 retroactive scores (7 shadow models × 1,565 trades) joined deterministically to every historical trade in the trainer-synced DB; future closed trades get the real Bybit fill price recorded; future signals write shadow predictions at the canonical path so they show alongside the backfill. Order package still unaffected. |
| 2026-05-22 | S-MES-GOLIVE | **MES paper trading went LIVE — the bot now trades two symbols (BTCUSDT + MES) every tick.** Market-data intake is multi-source: `connector_for_symbol()` routes MES to `src/exchange/ib_connector.py::IBMarketData` (delayed CME bars via `reqHistoricalData(3)`, no paid real-time sub) and BTCUSDT to Bybit. All three strategies are symbol-parameterized and evaluate both symbols; a symbol→exchange dispatch gate in `coordinator.py` keeps signals on the right account. Execution: `execute._submit_order` `interactive_brokers` branch → `IBClient.place` (native MES bracket, no API keys — auth is the Gateway login session). The IB Gateway runs headless as the gnzsnz Docker image with a **socat relay** (host `127.0.0.1:4002` → container `4004` → gateway `4002`); the paper account logs in with **no 2FA**. Two fixes made it work end-to-end: #1706 mapped the host port to the socat relay (4004) instead of the gateway's localhost-only 4002 (was `TimeoutError`), and #1712 gave `IBClient` a persistent asyncio loop re-asserted on every `connect()` (Telegram alerts' `asyncio.run` nulls the thread loop, which had broken `reqHistoricalData` with "no current event loop"). `ib_paper.ib_port` 7497→4002; `ib_paper.strategies` now lists all three. `ib_live` (real money) stays `mode: dry_run`. | `src/units/accounts/ib_client.py`, `src/exchange/ib_connector.py`, `config/accounts.yaml`, `scripts/install_ib_gateway_docker.sh`, `docs/runbooks/ib-integration.md`, `docs/architecture/multi-strategy-architecture-target.md`, this file, `tests/test_ib_integration.py` | Live VM: MES paper orders now execute against IB paper money alongside live BTCUSDT; real-money `ib_live` untouched (dry_run). Gateway re-provision (`provision-ib-gateway`) is autonomous for paper (no 2FA); IB Python-path changes need only `pull-and-deploy`. |
| 2026-05-21 | (shadow-live-wiring + CI-hardening + triage) | **Shadow predictions made real on the live path; CI turned into a genuine merge gate; ~94 stale tests fixed + real bugs surfaced.** (1) **Shadow auto-wire fix** (#1630): the live multiplexed pipeline runs strategies through `src/runtime/strategy_signal_builders.py`, not `Coordinator.order_package()`, so the 2026-05-19 auto-wire never fired — zero shadow predictions despite 7 shadow-stage models. Added a generic `_resolve_shadow_predictors`/`_emit_shadow_preds` (mirrors `Coordinator._get_shadow_predictors` tri-state) wired into all three builders; made `/api/bot/ml/registry`'s `deployment_bucket` auto-wire-aware so shadow-stage models render SHADOW not OFFLINE. Verified live: all 7 models now log on every actionable signal. (2) **Diag/admin observability relays**: `/api/diag/log_file` allowlist gained `shadow_predictions` + `_backfill` (#1634); new `branch-protection-report.yml` (read GitHub admin state) and `delete-merged-branches.yml` (runner-side branch cleanup — the sandbox proxy blocks `git push --delete`). (3) **`backfill-shadow-predictions` operator action** (#1635/#1639) — replays all history through shadow models onto the live VM. (4) **CI now executes tests**: new `pytest-run.yml` (advisory) runs the full suite (`pytest-collect` only imported); `branch-protection-sync` set to `enforce_admins: true` + promoted `env-gate-guard`/`silent-empty-guard`/`canonical-config-loaders`/`canonical-db-resolver` to required (8 total) — admin/API merges no longer bypass checks. (5) **Test-backlog triage** (#1648/#1649/#1650/#1651): ~94 stale-test fixes across telegram/web-api/order-monitor/accounts; fixed a real bug (`run_monitor_tick` returned `None` despite its dict contract). (6) **Real bugs flagged + fixed**: removed dead `/ui/fragments/{status,pnl}` routers that 500'd in prod (#1654); corrected Bybit V5 spot order semantics in `execute.py` (#1655, dormant path — all live accounts are linear). (7) Deleted 757 merged-PR branches. Dashboard repo (`ict-trader-dashboard`) got its first CI (ruff + import-smoke, #60). | `src/runtime/strategy_signal_builders.py`, `src/web/api/routers/training_center.py`, `src/web/api/routers/diag.py`, `src/units/accounts/execute.py`, `.github/workflows/{pytest-run,branch-protection-sync,branch-protection-report,delete-merged-branches}.yml`, `scripts/ops/backfill_shadow_predictions_action.sh`, `.github/workflows/system-actions.yml`, `docs/claude/{ci-status-checks,system-actions}.md`, `docs/api-tier-policy.md`, `CLAUDE.md`, many `tests/` | Live VM: shadow predictions now flow (real-time + full backfill) with zero order-package effect; CI genuinely gates merges (incl. admins); the `/ui/fragments` 500 is gone. `pytest-run` stays advisory until the remaining ~150-test backlog clears, then it joins `REQUIRED_CONTEXTS`. #1655 (spot semantics) is the only behavioural change to live-order code and is dormant (no spot account). |
| 2026-05-22 | (pytest-run promotion) | **`pytest-run` promoted from advisory to a required status check (9 required contexts total).** The full-suite gate (added 2026-05-21, advisory) had its baseline driven green, then `"pytest-run"` was added to `REQUIRED_CONTEXTS` in `branch-protection-sync.yml` (#1721). Path to green: #1658-1667 cleared the original failure backlog; #1681 fixed order-dependent test-isolation failures + post-IB-merge contract drift (`ib-gateway.service` in `EXPECTED_SERVICES`; the `enable-mes`/`disable-mes`/`gateway-logs` system-action allowlist + wrapper/notify/doc contracts); #1717 fixed the last CI-only failure — `test_deploy_pull_restart_enumeration`'s `sudo` stub (`exit 0`) assumed root uid, so it passed on root dev containers but failed on GitHub's non-root runner, diagnosed via a temporary `pytest-diag` workflow run on the real runner. Closes the gap `pytest-collect` left open (imports only, never executed an assertion). | `.github/workflows/{pytest-run,branch-protection-sync}.yml`, `docs/claude/ci-status-checks.md`, `docs/github-actions-workflows.md`, this file, several `tests/` | None on live-VM behaviour — CI-gating only. Future PRs must keep the full suite green to merge. |
| 2026-05-23 | S-PERSIST-CANON | **Persistence centralized into one federated canonical store + daily_risk_state fixed.** (1) Single canonical Python DB-path resolver `src.utils.paths.trade_journal_db_path()` (env → $DATA_DIR → repo-root, never CWD-relative); ~20 inline `os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"` idioms consolidated onto it; `Database()` defaults to it; `canonical-db-resolver` guard extended to forbid the CWD-relative fallback + inline env-reads in Python (not just shell). This eliminates the root cause of the stray duplicate journals. (2) New federated sidecar `trainer_store.db` (`src/units/db/trainer_store.py`) ingests the trainer-mirror JSONL/JSON (training_cycle, dataset_builds, db_pulls, model_registry, experiment_runs, backtest_sweeps); the Data Explorer federates both DBs. (3) `daily_risk_state` self-healing rebuild in `RiskManager` (was empty because record_trade_result/update_equity had no runtime callers) — makes the per-account daily-loss / max-drawdown caps persist across restarts AND actually enforce. (4) `strategy_versions` (was dead) wired to a boot-time content-hashed snapshot of `config/strategies.yaml`. Draft PR; risk-logic change operator-gated. | `src/utils/paths.py`, `src/units/db/{database,trainer_store}.py`, `src/units/accounts/risk.py`, `src/runtime/{boot_audit,risk_counters,…}.py`, `src/web/api/routers/db_explorer.py`, `src/main.py`, `scripts/check_canonical_db_resolver.py`, this file, `CLAUDE.md`, many `tests/` | Live VM: behaviour-preserving for DB-path resolution (systemd env unchanged); the risk-cap change makes configured daily_usd/max_dd_pct enforce for the first time (operator merges when ready); Data Explorer gains the trainer tables. |
| 2026-05-23/24 | S-STRAT-IMPROVE-S8/S9 | **Multi-member book + per-strategy execution gate + single-account decider.** (1) **Per-strategy `execution: live \| shadow` gate** (S9, operator-approved 2026-05-24): a second declared, default-permissive execution gate beside accounts.yaml `mode:`. `shadow` runs + LOGS order packages everywhere (data collection) but never sends a live order — enforced in `Coordinator.multi_account_execute` by folding into the same `effective_dry` resolution as `mode:` (no new order path); fails OPEN on a registry-read error (treats as dry). Read from the registry via `src/strategy_registry.py::execution_mode`; surfaced on `/api/bot/config`. Codified in CLAUDE.md + CLAUDE-RULES-CANONICAL.md Prime Directive. (2) **Roster grew 3 → 5** (squeeze = pending 6th): `trend_donchian` (Donchian-breakout trend-follower) went live on bybit_2 (real money) at 1h (S8) then **migrated 1h → 2h** (S9, +52.5R/6yr, net-positive every year, walk-forward validated); `fade_breakout_4h` (failed-breakout fade, uncorrelated complement, monthly_corr 0.035) wired `execution: shadow` → bybit_1 (demo); `squeeze_breakout_4h` (volatility-squeeze breakout, corr 0.30) built `shadow`, PRs #1907/#1908 pending operator merge. (3) **Single-account decider design** (operator direction 2026-05-24): one pot of capital used maximally, all strategies running, a decider concentrating the fund on the best opportunity each tick — NOT a per-strategy capital split. Supersedes the multi-account-blend design (PR #1902, closed). The intent aggregator IS the decider (crude static-priority today); decider-v2 makes it smart once ≥2 members live. (4) **MES / cross-asset data** sourced + cached: clean 1m S&P 500 via Dukascopy (`data/SPX500_1m.parquet`, 2020–2026, on the trainer), SPX-trend net-positive + near-uncorrelated with BTC (corr 0.009). | `config/strategies.yaml`, `config/accounts.yaml`, `src/strategy_registry.py`, `src/core/coordinator.py`, `src/units/strategies/{trend_donchian,fade_breakout_4h,squeeze_breakout_4h}.py`, `src/runtime/{strategy_signal_builders,pipeline,intent_multiplexer,intents}.py`, `scripts/{backtest_trend,backtest_fade,backtest_squeeze,research_decider}.py`, `scripts/ops/fetch_dukascopy_index.py`, `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`, `docs/sprint-logs/S-STRAT-IMPROVE-S9-2026-05-24.md`, `docs/audits/{fade,squeeze}-breakout-complement-2026-05-24.md`, README, `.claude/skills/{new-strategy,health-review}/SKILL.md`, this file | Live VM: `trend_donchian` trades real money on bybit_2 (2h); `vwap` + `fade_breakout_4h` are `execution: shadow` (data-only, no money). bybit_1 (demo) mirrors the full roster for shadow data. Squeeze begins shadow on operator merge of #1907/#1908. MES execution waits on IBKR new-user approval (data + edge already validated). |
| 2026-05-22 | (regime-shadow-wiring + MES-mirror + promotion-tracker) | **Regime shadow models made informative; MES mirror activated config-driven (second gate removed); promotion tracker shipped.** (1) **Regime feature wiring** (#1722): the four regime classifiers (`{btc,mes}-regime-{5m,15m}`) were live-shadowing but emitting a *constant* score — the strategy feature row carried no `vol_bucket`, so each predictor fell back to its training marginal. `RegimeClassifierTrainer` now freezes the quantile bucket edges + vol window + symbol/timeframe into `model_state`; new `src/runtime/regime_shadow.py` computes the live `vol_bucket` per tick from candles and feeds it only to the matching `(symbol, timeframe)` model; the strategy-monocle open-package gates were scoped by symbol. All four models retrained with edges; verified live (`btc-regime-5m` now scores 0.835 on `vol_b2`, not the old 0.7164 constant). Stale 1d v0 regime models demoted to `research_only`. (2) **MES mirror** (#1761): all three strategies now trade BTCUSDT + MES every tick. Tick symbols are derived from `config/accounts.yaml` (`_resolve_tick_symbols` unions every configured account's `symbols`); **`MULTI_SYMBOL_ENABLED` removed** — it was a forbidden second gate that left `ib_paper` (`mode: live`, all three strategies) idle. `mode:` is now the only runtime gate; the `enable-mes`/`disable-mes` operator actions + wrappers were deleted, and "no second gate; nothing defaults to off" was codified as Prime Directive rule 6. Verified live: every tick logs both symbols × three strategies, MES data flows from the IB paper gateway, per-symbol isolation holds (BTC's open vwap package no longer suppresses MES). (3) **Promotion-readiness tracker** (ict-trader-dashboard #62): new 🚦 Promotion page grades each shadow model (prediction volume, days-in-shadow, score range, wired check, KS/PSI drift, win/loss score edge) toward the operator-gated shadow→advisory promotion. | `ml/trainers/regime_classifier.py`, `ml/predictors/{per_bucket_multiclass,shadow}.py`, `src/runtime/{regime_shadow,strategy_signal_builders,intents,pipeline,strategy_monocle}.py`, `src/units/db/database.py`, `src/units/accounts/{account,__init__}.py`, `src/main.py`, `config/{accounts,strategies}.yaml`, `ml/configs/*regime*.yaml`, `.github/workflows/system-actions.yml`, `docs/{CLAUDE-RULES-CANONICAL,claude/system-actions,runbooks/ib-integration}.md`, `CLAUDE.md`, ict-trader-dashboard `streamlit_app.py`, many `tests/` | Live VM: regime shadow predictions now carry a real `vol_bucket` (still observe-only — never touches orders); MES paper trades as a full mirror of BTC across all three strategies; the only runtime gate is each account's `mode:`. |
| 2026-05-25 | (strategy-promotion + watchdog-fix) | **fade/squeeze promoted shadow→live + symmetric bybit roster + timeframe-aware stuck-watchdog.** (1) **Roster fully live** (PR #1995, operator-approved Tier-3): `fade_breakout_4h` + `squeeze_breakout_4h` flipped `execution: shadow`→`live`; both `bybit_1` (demo) and `bybit_2` (real money) now carry the **identical** six-strategy list `[trend_donchian, turtle_soup, ict_scalp_5m, fade_breakout_4h, squeeze_breakout_4h, vwap]` — everything live except `vwap` (stays `execution: shadow`, data-only). Supersedes the 2026-05-23/24 row's "fade/squeeze shadow on demo only" state. fade/squeeze are unvalidated-live (OOS expectancy ~half of train) — accepted operator risk; verify-they-trade-correctly logged as backlog BL-20260525-008. (2) **Timeframe-aware stuck-strategy watchdog** (PR #2002): a package's `updated_at` only advances on a non-None monitor verdict (a Chandelier-trail ratchet), so on a multi-hour strategy a healthy position routinely exceeded the flat 30-min threshold and false-fired the position-alive "still stuck" alert. The position-alive quiet window now scales by the package's own bar interval — `max(floor, mult × timeframe)`, floor `STUCK_STRATEGY_THRESHOLD_MINUTES` (30), mult `STUCK_STRATEGY_TIMEFRAME_MULT` (3); genuine orphans (position flat) still force-clear at the floor. Ghost zero-sized reinforcement packages (the deeper cause) logged as BL-20260525-009. (3) **Docs**: corrected the absolute "never merge" Tier-3 rule in CLAUDE.md + deploy/dropins/README.md to "without explicit operator approval" (matches the canonical tier gate); documented the trend_donchian 50R sentinel TP as expected (BL-20260525-007). | `config/accounts.yaml`, `config/strategies.yaml`, `src/runtime/order_monitor.py`, `tests/test_monitor_reconciler.py`, `CLAUDE.md`, `deploy/dropins/README.md`, `docs/claude/health-review-backlog.json`, this file | Live VM (deployed 2026-05-25): bybit_2 executes `fade`/`squeeze` on real money when their setups fire; both bybit accounts run the same roster; the watchdog no longer false-alerts on healthy 2h/4h positions. No `mode:` flips; `vwap` still data-only. |
| 2026-05-31 | (flip-policy default flip) | **Live execution invariant changed: opposite-side conflict in `compute_execution_delta` now noops by default ("hold") instead of close-and-reopening ("reverse").** Walk-forward verified PASS on both pre-agreed criteria across 24 cells (2 anchored folds × 2 halves × 2 rosters × 3 policies) — `docs/audits/walkforward-flip-policy-2026-05-30.md`. 4-member: `hold` beats `reverse` on net AND maxDD% across all four cells, with OOS lift > train lift on both folds. 6-member: `hold` halves the bleed vs `reverse` OOS in both folds (+$5,171 / +$5,329) — but the 6-member book still bleeds because turtle_soup + ict_scalp_5m lose in-system regardless of conflict policy; decider-v2 selection layer remains the prerequisite for those two members' shadow→live promotion. The policy knob itself (`flip_policy` kwarg + `FLIP_POLICY` env-var resolver + tri-state `{reverse, hold, flat}`) was shipped earlier the same day by the operator-led Cross Zero PR #2441; this row covers the operator-approved default flip in PR #2451 (single-line change at `_DEFAULT_FLIP_POLICY`). Aggregator (`aggregate_intents`) is unchanged — it still produces the same `DesiredPosition` with the same `dropped_intents` audit trail; the hold lives downstream at `compute_execution_delta`, the per-account meet-point of aggregator output + current position, exactly where the walk-forward harness modeled it. Coordinator logs every hold as `intent_noop:flip_suppressed_hold_policy:…` in the trade journal (Prime Directive's "no silent state" rule). Operator rollback path: `FLIP_POLICY=reverse` on the systemd unit, no redeploy needed (same escape valve as `MULTI_STRATEGY_INTENT_LAYER`). New Tier-1 research tooling also landed: `scripts/backtest_system.py` (system/portfolio backtester with `--flip-policy` + 6-member roster coverage) + `scripts/walkforward_flip_policy.py` (24-cell walk-forward driver with pass-criteria checker) + `fade_breakout_4h` 48-bar time-stop (parity/safety fix, system-inert per the audit — not an alpha lever). | `src/runtime/intents.py` (`_DEFAULT_FLIP_POLICY: "reverse" → "hold"`), `scripts/backtest_system.py` (NEW), `scripts/walkforward_flip_policy.py` (NEW), `src/units/strategies/fade_breakout_4h.py`, `config/strategies.yaml` (`fade_breakout_4h.timeout_bars: 48`), `docs/audits/{walkforward-flip-policy,system-portfolio-backtest}-2026-05-30.md`, `docs/sprint-plans/CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md`, `docs/sprint-logs/S-STRAT-FVG-RANGE-2026-05-30.md`, `CLAUDE.md` (`FLIP_POLICY` env-var entry), `docs/claude/performance-review-backlog.json::PERF-20260530-001`, plus matching tests in `tests/test_intent_delta_dispatch.py::TestFlipPolicy` + the coordinator-level integration test + the existing flip-dispatch test pinned to `FLIP_POLICY=reverse`. | Live VM (deployed 2026-05-31 via pull-and-deploy issue #2458, HEAD `8d17fdd`): every multi-strategy intent tick where the aggregator's winner opposes the currently-held side now noops at the executor instead of dispatching the close+open flip legs. bybit_1 (demo) and bybit_2 (live) both inherit the new default immediately. No other path changes; PRs #2410 / #2433 / #2439 (Tier-1 research) merged earlier in the same session. **No promotion of `turtle_soup` / `ict_scalp_5m` to `execution: live`** — the 6-member-bleeds finding stands; selection-layer prerequisite per [`DECIDER-SINGLE-ACCOUNT-2026-05-24.md`](sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md) v2 step 2/3 remains the next Tier-3 prize. |
| 2026-05-26 | (trade↔package link many-to-one) | **`trades.order_package_id` column closes the "(unlinked)" reconciler-sweep gap on multi-leg fanouts.** Triggered by the 2026-05-26 07:13Z `ict_scalp_5m` short whose 3 fanned-out trade rows (real `bybit_2` entry 1726 + demo `bybit_1` mirror 1724 + `intent_reduce` flip-leg 1725 on `bybit_2`) all came from package `pkg-6ad338849aa345a2` but only 1726 surfaced in the orphan-sweep ping with its package id — 1724/1725 came through as `(unlinked)` and were skipped by `_resolve_linked_package_id` in `src/runtime/order_monitor.py`. Root cause: the only journal-side link was `order_packages.linked_trade_id` (one slot), and every leg of `execute_pkg`'s fanout called `update_order_package(linked_trade_id=<its own row id>)` so only the last writer survived. (1) Additive `trades.order_package_id TEXT` column + idempotent `_migrate_add_order_package_id` (mirrors `_migrate_add_is_demo` / `_migrate_add_account_id` pattern) + reverse-lookup index. Pre-existing rows are left NULL — the resolver falls back to the legacy `order_packages.linked_trade_id` query for them. (2) `src/units/accounts/execute.py::_log_trade_to_journal` now passes `order_package_id` into every `insert_trade` payload; the package's `linked_trade_id` slot is written only by the **primary real-money entry** (`status=='open' AND not intent_reduce AND not is_demo`), keeping `strategy_monocle`'s "primary entry trade" semantics deterministic instead of last-writer-wins. (3) `_resolve_linked_package_id` reads `trades.order_package_id` first and falls back to the legacy lookup for pre-column rows. Tier 2; PR #2046 draft pending CI (held by 2026-05-26 GitHub Actions auth incident). Adjacent to the Known-gap "Reduce-only fill correlation in S-030 monitor" entry but doesn't close it — that gap is about `intent_reduce → parent` join for `position_size` updates, which this PR doesn't touch. | `src/units/db/database.py`, `src/units/accounts/execute.py`, `src/runtime/order_monitor.py`, `tests/test_trades_order_package_id_link.py`, this file | Live VM (after deploy + restart): future multi-leg fanouts have every trade row resolve to its package id, the orphan reconciler can cascade all legs not just the primary, and the orphan-sweep ping shows `Package: pkg-…` on every leg instead of `(unlinked)`. Pre-existing rows keep working via the legacy lookup. No behaviour change for single-leg trades or for the order-path itself. |
| 2026-06-02 | (WS-A metals sleeve + per-strategy symbol scope) | **Meantime Expansion Program WS-A: two backtest-validated futures diversifiers paper-trading on IBKR, plus a per-strategy symbol-scope gate that supersedes the "all account strategies trade all account symbols" behaviour from the 2026-05-22 MES-mirror row.** (1) **Real-money roster → winners-only** (PR #2630, Tier-3 operator-approved): `bybit_2` set to `[trend_donchian, ict_scalp_5m, fvg_range_15m, htf_pullback_trend_2h]` — graduated fvg_range + htf_pullback from demo, dropped turtle_soup/vwap/fade/squeeze from real money (they stay on bybit_1 demo). (2) **WS-A research** (PR #2634, Tier-1): wide futures sweep → re-tune → bootstrap significance → fee-headroom (`docs/research/ws-a-s{1,2,3,3b}-*`, `scripts/research/ws_a_*`). Outcome: **Copper/pullback + Gold/pullback** are statistically-vetted (block-bootstrap p05 expectancy > 0 over 27y), fee-robust (>30bps), BTC-uncorrelated. (3) **Metals paper sleeve** (PR #2634, Tier-3): `mgc_pullback_1d` (MGC) + `mhg_pullback_1d` (MHG) wired on `ib_paper`, `execution: live`, reusing `htf_pullback_trend_2h.order_package` with the validated params; `SUPPORTED_SYMBOLS += {MGC, MHG}`; `instruments.yaml` + `ib_client._build_contract` (COMEX `ContFuture`) extended. COMEX market-data entitlement verified live (candles fetching). (4) **Per-strategy symbol scope** (PR #2643, Tier-3): `intent_multiplexer._collect_intents` skips a strategy on any tick symbol not in its `config/strategies.yaml::symbols:` (permissive on no-`symbols`/unknown/config-error). Gated at intent EMISSION (not dispatch) so a higher-priority off-symbol strategy can't win a symbol's per-symbol aggregation and starve the legitimate owner. Net: each strategy trades only its declared instrument(s) — `mgc_pullback_1d`→MGC, `mhg_pullback_1d`→MHG, `mes_trend_long_1d`→MES; **crypto strategies (`symbols:[BTCUSDT]`) no longer trade MES** (the 2026-05-22 "MES mirror across all three strategies" behaviour is intentionally retired). Real-money `bybit_2` (BTCUSDT-only) is a no-op under the gate. Verified live post-deploy: `mgc_pullback_1d` evaluates MGC-only. Resolves health-backlog `BL-20260602-001`. | `config/{accounts,strategies,instruments}.yaml`, `config/strategy_descriptions.json`, `src/runtime/{intent_multiplexer,intents,pipeline,strategy_signal_builders}.py`, `src/units/accounts/ib_client.py`, `scripts/research/ws_a_*.py`, `docs/research/ws-a-*`, `tests/test_{mgc_mhg_pullback_1d,intent_symbol_scope}.py`, this file | Live VM (deployed 2026-06-02, HEAD `3d35e24`): winners-only roster on real-money `bybit_2`; Copper/Gold pullback paper-trading on `ib_paper` (IBKR paper money) collecting forward-validation data; each strategy scoped to its declared symbol(s); no real-money behaviour change beyond the roster. NinjaTrader as the eventual live-futures venue is a separate future session. |
| 2026-06-04 | (regime router 2-D vol axis + train/serve parity) | **Regime-router observe-only expansion: a second `vol` axis and full train/serve feature parity for every regime head — both observe-only, no order-path reach.** (1) **S15b / PR #2788 (Tier-2, observe-only):** a parallel volatility-regime axis alongside the live ADX-14 trend axis. `src/runtime/regime/vol_detector.py` (2-class `calm`/`volatile`, matching the classifier's existing scheme); the three trend-axis touchpoints gain a vol sibling — `_stamp_regime_on_meta` now tags `vol_regime`/`rolling_log_return_vol`/`vol_regime_source` onto `signal.meta`, `intent_from_signal` lifts them onto `StrategyIntent`, and `aggregate_intents._shadow_regime_gate` logs a 2-D `trend × vol` would-gate (`config/regime_policy.yaml` `schema_version: 2` + empty `trend_vol` block = no enforcement). (2) **S17 / PR #2790 (Tier-2, observe-only):** closes the train/serve gap (`MB-20260604-005`) that blocked promoting ANY regime head past `shadow`. `src/runtime/regime_shadow.feature_row_for_predictor` now emits the full `market_features` superset (four range-vol estimators + log-return + two lags + hour/day-of-week) and buckets `vol_bucket` against the head's own `vol_feature_column` estimator (fixing the yz-head close-to-close mis-bucketing). Shared by both the signal-time `_emit_shadow_preds` and the per-bar `regime_bar_scoring` (S13) callers. | `src/runtime/regime/vol_detector.py` (NEW), `src/runtime/regime/{detector,policy,__init__}.py`, `src/runtime/{regime_shadow,regime_bar_scoring,strategy_signal_builders,intents}.py`, `config/regime_policy.yaml`, `ml/datasets/families/market_features.py`, `ml/trainers/lightgbm_multiclass.py`, `tests/`, `docs/sprint-logs/S-MLOPT-S15b.md` + `S-MLOPT-S17.md`, this file | Live VM (deployed 2026-06-04): order packages now carry `vol_regime`/`vol_regime_source: vol-bucket-edges:btc-regime-5m-baseline-v1` in `signal_logic` (verified in the live diag order_packages pull 2026-06-06); observe-only — no order/risk behaviour change. Promotion of any regime head past `shadow` remains a Tier-3 operator gate. |
| 2026-06-05 | (IB-Gateway wedge isolation — restart-loop incident fix) | **Trader-loop liveness invariant hardened so a wedged/down IB Gateway can never block the BTCUSDT money loop or starve the heartbeat.** Root incident (2026-06-05 ~04:23–17:17Z, issues #2793–#2847): a logged-out IB Gateway accepted sockets then hung; per-tick MES fetches blocked the whole tick; the heartbeat (written only at tick-end) went stale; the liveness watchdog autohealed the trader before it could complete a first tick → a self-perpetuating ~4.5h restart loop, compounded by a ~6.5s VM clock skew breaking Bybit signed requests (pybit ErrCode 10002). Fixes shipped + deployed: (1) **PR #2814** — bound every IB market-data fetch with `IB_FETCH_TIMEOUT_S` (default 8s) so a hung gateway can't stall the tick; added a `sync-clock` system-action for the VM clock skew. (2) **PR #2827** — a post-connect **liveness probe** (`IB_PROBE_TIMEOUT_S`, default 5s, `reqCurrentTime` round-trip) + **circuit breaker** (`IB_BREAKER_COOLDOWN_S`, default 120s) in `IBClient.connect()`: a socket-accept is no longer treated as a usable session; on failure `connect()` raises and the breaker stays open, fast-failing subsequent IB calls so Bybit/BTCUSDT is fully isolated — **and the heartbeat is now written at tick START as well as tick-end** so a slow tick can't starve liveness. (3) **PRs #2806 / #2838** — `pause-autoheal`/`resume-autoheal` system-actions (operator lever to break a loop without disabling the unit by hand) + a `vm-ib-gateway-stop` workflow. | `src/exchange/ib_connector.py`, `src/units/accounts/ib_client.py`, `src/main.py` (heartbeat-at-tick-start), `.github/workflows/{system-actions,vm-ib-gateway-stop}.yml`, `CLAUDE.md` (`IB_FETCH_TIMEOUT_S`/`IB_PROBE_TIMEOUT_S`/`IB_BREAKER_COOLDOWN_S` env entries), `docs/claude/health-review-backlog.json::BL-20260605-00{1..6}`, this file | Live VM (deployed 2026-06-05, HEAD `fde45288`): verified in the 2026-06-06 /health-review diag pull — with the gateway down (port 4002 ConnectionRefused) the breaker trips and suppresses IB calls for 120s while the BTCUSDT loop keeps ticking + heartbeating every 60s; no pybit ErrCode 10002 lines remain. MES/MGC/MHG stay dark (gateway stopped) pending IBKR-login re-provision; autoheal re-armed (`ict-liveness-watchdog.timer` active). |
| 2026-06-11 | (config-driven intent symbol whitelist + /api/bot/config symbols) | **Intent-layer symbol validation is config-driven — adding an instrument to `config/accounts.yaml` never needs a code edit again.** The hand-maintained `SUPPORTED_SYMBOLS` frozenset had drifted behind accounts.yaml: the M15 instruments (XAUUSD on `oanda_practice`, SPY/QQQ/GLD on `alpaca_paper`, all `mode: live` since #3336/#3340) were declared in config but absent from the whitelist, and `intent_from_signal` sits outside the builder try/except in `_collect_intents`, so the first actionable signal from `xauusd_trend_1h` / `spy_trend_long_1d` / `qqq_trend_long_1d` / `gld_pullback_1d` would have raised `ValueError` out of intent collection. Fix: validation goes through `supported_symbols()` = static base ∪ every symbol declared on an account in accounts.yaml (60s cache; fail-safe to the static base on config-load error — never narrower than before; a typo'd symbol is still rejected because no account declares it). Companion Tier-1 change: per-account `symbols` joins `_ACCOUNT_PUBLIC_FIELDS` on `/api/bot/config`, giving consumers the canonical dynamic symbol enumeration — the Streamlit dashboard (ict-trader-dashboard#89) and Android app (ict-trader-android#46) drop their hardcoded chart-symbol lists and derive selectors from the API in the same session. | `src/runtime/intents.py` (`supported_symbols()`), `src/web/api/routers/bot_config.py`, `tests/test_supported_symbols_config_driven.py`, `tests/test_web_api_bot_config.py`, `CLAUDE.md` (API-table row), this file | PR #3358 (Tier-2, order-path adjacent — draft pending operator OK). Once deployed: an actionable XAUUSD/SPY/QQQ/GLD signal constructs its intent instead of raising; future instruments need only the accounts.yaml + instruments.yaml wiring. |
| 2026-06-14 | (per-trade ML scores persisted on the order package) | **The ML decisions a trade was made with are now persisted ON the order package, so consumers read them with a cheap SELECT instead of recompiling per-trade aggregates from `runtime_logs/shadow_predictions.jsonl` on every request.** Before this, per-model scores existed only in the shadow-prediction JSONL and `/api/bot/trades/scores` reconstructed them via a full-log time-window join (the dashboard's "compiled in real time" slowness). Change: (1) `shadow_adapter.capture_shadow_preds()` — score-returning sibling of `with_shadow_preds` (same observe-only contract: one `predict` per model, per-model try/except, **WS7 audit log unchanged**) that RETURNS `{model_id:{stage,score}}`. (2) `strategy_signal_builders._emit_shadow_preds` (the central per-signal scorer for all strategies) captures the scores onto `sig["meta"]["model_scores"]`, which flows signal → intent (`intents.py` copies `dict(signal.meta)`) → `OrderPackage.meta`. (3) New additive `order_packages.model_scores TEXT` column (idempotent `_migrate_add_order_package_model_scores`, mirrors the `_migrate_add_*` pattern); `_log_new_order_package` writes it (kept out of the `meta` blob to avoid duplication). (4) `/api/bot/order-packages` projects it as `modelScores`. **WS7 non-negotiable preserved: the persisted scores are observe-only METADATA on the journal row — the order DECISION / risk path is still byte-identical and never reads them back** (capturing for persistence is fine; acting on them is not). The Streamlit dashboard (ict-trader-dashboard#96) + Android app (ict-trader-android#49) read `modelScores` directly (always-on + fast); the older `/api/bot/trades/scores` JSONL path stays as-is for historical/pre-column trades. | `src/runtime/shadow_adapter.py`, `src/runtime/strategy_signal_builders.py`, `src/units/db/database.py`, `src/core/coordinator.py`, `src/web/api/routers/order_packages.py`, `tests/test_{web_api_order_packages,capture_shadow_preds,real_schema_db_fixture}.py`, `CLAUDE.md` (API-table row), this file | PR #3535 (Tier-2, live signal/order path — observe-only). Deployed 2026-06-14, HEAD `e4a2e54`: trades opened after deploy carry their ML decisions on the order-package row; pre-existing rows stay NULL (consumers render "No model scores recorded"). Order/risk behaviour unchanged. |
| 2026-06-14 | (orphan re-attach to originating strategy + aliased-strategy monitor resolution) | **Two coupled fixes so an adopted exchange orphan is returned to its originating strategy for ACTIVE monitoring instead of sitting on static SL/TP — surfaced by an `orphan_adopt` MHG position on `ib_paper`.** (1) **Aliased-strategy monitor resolution.** The order-monitor loop imports a strategy's module by name to call `monitor()`, but the WS-A metals + M15 equity/fx sleeves (`mgc/mhg/gld/eth_*`, `mes/mgc_trend/xauusd/spy/qqq_*`) and `ict_scalp_5m` have **no same-name module** — they reuse a base unit via the signal builder, so their open positions were never actively monitored (break-even trail, level-cross/thesis exit, time-decay) — only static SL/TP. Each aliased strategy's builder is tagged with its base unit module (a `monitor_unit` attribute, co-located with the builders in `strategy_signal_builders` — the source of truth for which unit they reuse); `pipeline.monitor_unit_for()` derives the mapping from the builder registry (no duplicated map), and `order_monitor._call_strategy_monitor` resolves through it (plain strategies are their own module). Drift guard `tests/test_strategy_monitor_unit_resolution.py` fails CI if a future aliased strategy lacks a resolvable `monitor()`. (2) **Orphan re-attach.** `_adopt_orphan_position` now first recovers the order package that opened the orphan (`_recover_orphan_order_package`: confident symbol + normalised-direction + entry-within-2% match against `get_recent_order_packages_for_symbol`), attributes the adopted trade row to that **originating strategy** carrying the package's stored SL/TP, and **reopens + re-links** the package (`status='open'`, `linked_trade_id`) so the next `run_monitor_tick` runs that strategy's `monitor()` on it. Falls back to the bare `orphan_adopt` row (NULL SL/TP, no fabricated attribution) only when no confident match exists. **No env gate** — re-attach + monitoring are baseline correctness, always on under `ORPHAN_POSITION_POLICY=adopt`; never default-off (Prime Directive). | `src/runtime/pipeline.py` (`monitor_unit_for`), `src/runtime/strategy_signal_builders.py` (per-builder `monitor_unit` tags), `src/runtime/order_monitor.py` (`_call_strategy_monitor`, `_adopt_orphan_position`, `_recover_orphan_order_package`, `_canon_dir`), `src/units/db/database.py` (`get_recent_order_packages_for_symbol`), `tests/test_{orphan_reattach,strategy_monitor_unit_resolution}.py`, this file | PR #3543 (Tier-3 — changes live exit behaviour across the aliased sleeve + adopted orphans). Once deployed: aliased strategies' positions get their real `monitor()` exits, and an adopted orphan with a recoverable package returns to full strategy monitoring instead of static SL/TP. |
| 2026-06-14 | (orphan_adopt self-heal — repair existing rows every reconcile pass) | **`orphan_adopt` is a problem indicator, not a legitimate resting status — the reverse reconciler now repairs EXISTING orphan_adopt rows every pass, not just at adoption time.** PR #3547 re-attached an orphan at *adoption*; a row already sitting as `orphan_adopt` (adopted before the fix, e.g. the live MHG position) stayed unmonitored. New `order_monitor._reattach_adopted_orphans(db, summary)` runs at the top of `_reconcile_orphan_exchange_positions` (whenever `MONITOR_RECONCILE_ENABLED`, independent of `ORPHAN_POSITION_POLICY` — repair is always correct): it scans open `strategy_name='orphan_adopt'` trades, and for each whose originating package is now recoverable (`_recover_orphan_order_package`, confident match) restores the trade's strategy + SL/TP and reopens/re-links the package so `run_monitor_tick` governs it. Idempotent; confident-match-or-skip (an unrecoverable orphan is left to keep surfacing as an orphan, never mis-attributed). Summary gains `reattached_existing`. | `src/runtime/order_monitor.py` (`_reattach_adopted_orphans`, called from `_reconcile_orphan_exchange_positions`), `tests/test_orphan_reattach.py`, this file | PR #3549 (Tier-3 — self-healing live-exit repair). Once deployed: the existing MHG orphan (and any pre-fix orphan_adopt with a recoverable origin) re-attaches to its strategy on the next reconcile pass — no manual DB action. Prevention of the false-orphaning that creates these (forward reconciler treating "broker unreachable" as "position flat") is a separate follow-up. |

---

## Known gaps

Deliberate omissions and queued work. An entry here is a
**contract** between the team and future maintainers: the
architecture doc does not yet reflect this state, by design,
because the work is in flight or out of scope for the current
milestone.

| Gap | Why deferred | Tracking |
|---|---|---|
| **Orphaned mode-override dead code in `src/`** | **Behaviour remediated; residual dead code only (verified 2026-06-10).** The two Prime-Directive-violating vectors are gone: the breaker auto-flip is removed (rejection path at `src/core/coordinator.py:1669-1689` is alert-only) and the Telegram `/accounts dry\|live` handler was removed in #1933. What remains is **orphaned shim code with no caller** — `_DRY_RUN_OVERRIDES` + `set_account_dry_run()` (`src/units/accounts/__init__.py:33,36`), `Coordinator.set_account_dry_run()` (`coordinator.py:1760`), and the `account_state.yaml` dry-only override read (`coordinator.py:1100`). The promised safeguards-PR deletion never landed. | Cleanup PR (delete the dead dict + functions + override read). Low risk — no live caller. |
| **Per-trade RiskManager rejection → per-trade Telegram** | The Prime Directive (§ rules doc) requires every refusal to emit its own Telegram with account/symbol/side/qty/reason/exchange-error. Today's path uses aggregate alerts when conditions cluster. The per-trade wiring ships in the safeguards PR. | Safeguards PR. |
| ~~**WS5 baselines not yet at `shadow` in any registry**~~ | **Resolved.** The trainer VM is provisioned and the M14 program has been running training cycles for weeks; baseline + regime heads sit at `shadow` and log predictions live (`runtime_logs/shadow_predictions.jsonl`, surfaced on `/api/bot/shadow/*` + `/api/bot/trades/scores`). | Closed (M14 in progress). |
| **`shadow_model_ids` empty in production strategy YAML** | ~~Operator step~~. **Resolved 2026-05-19 by the default-flip + auto-wire.** Strategies that omit `shadow_model_ids` (or set it to `None`) auto-discover every model at `target_deployment_stage: shadow` and attach them as shadow predictors. The boundary between trainer-VM Claude and live trading moves from `shadow_model_ids` wiring to the `shadow → advisory` promotion gate; the latter still requires operator approval. | Closed. |
| ~~**Trainer VM not yet provisioned**~~ | **Resolved.** `ict-trainer-vm` (`158.178.209.121`) is up and running the ML lifecycle; read it via the `trainer-vm-diag` relay. The M14 ML-Optimization Program (S0–S18) has executed numerous training cycles on it. | Closed. |
| ~~**Trainer VM ↔ live VM data flow not yet wired**~~ | **Resolved.** The trainer pulls live `trade_journal.db` (read-only sync) and publishes its lifecycle artifacts back to the live VM via the trainer mirror (`runtime_logs/trainer_mirror/`), ingested into the federated `trainer_store.db` sidecar (`src/units/db/trainer_store.py`) and surfaced through `/api/bot/ml/*` + `/api/bot/backtests/sweeps`. | Closed (S-PERSIST-CANON). |
| **No open-source model layer (HF transformers as `Predictor`)** | WS6 not started. Per the master plan, defer until the WS8 feedback loop is observable end-to-end (drift detector + dashboard panels are live as of 2026-05-11; missing piece is real shadow predictions in production, which lands when the trainer + YAML wiring resolve). | WS6. |
| **`arch-doc-guard` is advisory, not blocking** | Hard-failing would push the team to bypass it. Upgrade path is a follow-up workstream once the workflow is fluent. The PR-template "Architecture impact: Not applicable" checkbox is the documented escape hatch when a change is contract-preserving. | Filed in S-AI-WS10 sprint log; revisit after ~20 successful PR cycles without bypass. |
| **`arch_doc_guard.py` does not validate a Change-log row was added** | The current heuristic checks "did any arch-doc path get touched"; it does not check "was a new row appended to ARCHITECTURE-CANONICAL.md's Change log". Easy to add but premature without the upgrade-to-blocking decision above. | Filed against WS10; would also need to enforce row-shape. |
| **No automated audit of the AI-TRADERS-ROADMAP.md Change log** | The doc-audit-weekly workflow audits the Verification Checklist for broken paths; it does not yet audit roadmap consistency (e.g., a workstream marked DONE in the roadmap but referenced as in-progress in a sprint plan). | Filed under WS10 follow-ups. |
| **Reduce-only fill correlation in S-030 monitor (Phase-2 follow-up)** | S-MSE-2 (PR #1138) wires reduce / close / flip legs through `execute_pkg(reduce_only=True)` and the dispatcher stamps `setup_type='intent_reduce'` on the journal row so reduce legs are distinguishable. The S-030 monitor loop in `src/runtime/order_monitor.py` still reconciles fills by `symbol + qty + side + timestamp` — a reduce leg lands as its own row in `trade_journal.db::trades` rather than updating the parent open trade's `position_size`. P&L attribution can briefly double-count the same exposure on the tick a reduce fires before the reconciler catches up. Distinguishable via `setup_type='intent_reduce'` and `notes.intent_reduce=True`. **The next `/performance-review` should explicitly grade whether any double-count appeared in the first live conflict between Turtle Soup and VWAP** (or in any session once ICT scalp activates). Fix is an `intent_reduce → parent` join in the reconciler. | S-MSE-3 — file the join + matching tests once a real conflict surfaces it (don't pre-emptively guess the parent-matching heuristic). |
