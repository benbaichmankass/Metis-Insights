# Architecture — Canonical (v2)

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot` — **renamed to `benbaichmankass/Metis-Insights`
> 2026-07-23** (the system is no longer ICT-only: ICT + pairs + macro/value +
> macro-events). The old name is redirect-safe and is deliberately KEPT as the
> GitHub-MCP scope string and in the VM clone paths (`/home/ubuntu/ict-trading-bot`,
> `/opt/ict-trading-bot`) — do NOT sweep those. See `CLAUDE.md` § "Repo identity"
> and `ROADMAP_MACRO.md` § 7.
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
3. **No runtime override layer (deleted).** The `_DRY_RUN_OVERRIDES`
   dict and `set_account_dry_run()` function in
   `src/units/accounts/__init__.py` (+ the
   `Coordinator.set_account_dry_run()` wrapper) **were deleted** in the
   2026-06-10 dead-code cleanup — they had no remaining automatic or
   operator caller once the breaker auto-flip and the legacy Telegram
   `/accounts dry|live` writer (#1933) were retired and all mode flips
   routed through the sanctioned `set_account_mode.sh` (item 2). There
   is no in-memory override layer: `_resolve_mode()` reads
   `config/accounts.yaml` `mode:` directly on every call. A regression
   test asserts their absence
   (`tests/test_exchange_rejection_circuit_breaker.py` —
   `assert not hasattr(_acc, "set_account_dry_run")` /
   `_DRY_RUN_OVERRIDES`).
4. **No auto-flip (verified).** No code path inside `src/` flips a mode.
   The 2026-05-12 silent-flip incident drove this: the breaker auto-flip
   that lived in `src/core/coordinator.py` ("3 consecutive exchange
   rejections → set_account_dry_run(True)") **has been removed** — the
   consecutive-rejection path in `src/core/coordinator.py` (the
   `_EXCHANGE_REJECTION_COUNTS` alert branch in `multi_account_execute`,
   anchored on the symbol not a line number so it can't re-drift) now
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
override dict + `set_account_dry_run()` themselves were subsequently
**deleted** in the 2026-06-10 dead-code cleanup (item 3 above; verified
still-absent 2026-07-09), leaving `_resolve_mode()` reading YAML directly.

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
- `ml/` (model training, datasets, registry, manifests — see [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md)),
- ~~backtest dispatch from `src/bot/test_strategy_consumer.py`~~ — **REMOVED
  2026-08-20.** The M5 `/test` consumer ran one hardcoded ICT engine
  regardless of the strategy named and wrote fabricated `0.0` metrics; it
  was default-off and had no consumer. Real backtests are the trainer
  sweeps, surfaced at `/api/bot/backtests/sweeps`.

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
`src/exchange/alpaca_connector.py::AlpacaMarketData`,
`src/exchange/oanda_connector.py::OandaMarketData`,
`src/exchange/ib_connector.py::IBMarketData`) and the market-data helpers
in `src/runtime/market_data.py` produce candles and tick state. The bot is
**multi-symbol**: `connector_for_symbol(symbol)` routes each symbol to the
right data source by its `config/instruments.yaml` exchange — BTCUSDT →
Bybit; MES (CME) / MGC + MHG (COMEX) → Interactive Brokers (delayed
futures bars via `reqHistoricalData`, no paid real-time subscription).
See `docs/runbooks/ib-integration.md`. **There are exactly four live
connectors — Bybit, Alpaca, OANDA, IBKR.** This list previously named a
`src/exchange/binance_connector.py`; **no such file has ever existed on
disk** (corrected 2026-08-13, verified by `ls src/exchange/`). Binance
appears in this repo ONLY as an **offline research archive**
(`scripts/ops/fetch_binance_vision.py`, pulling monthly klines from
`data.binance.vision` for backtests because Bybit's REST is 403-blocked from
the sandbox). It is never in the live market-data path and never in
`EXCHANGE_MAP` — do not re-add it here on the strength of a `binance`
grep hit in `scripts/`.

### Step 2 — Strategy evaluation
Strategy modules in `src/units/strategies/` consume market data and emit
signals. **STATE THE POPULATION — the roster has two different counts and
they are not interchangeable:** `config/strategies.yaml` registers **55
strategy cells**, of which **52 carry `enabled: true`** (the legs the live
trader loads) and **3 are `enabled: false`** — `vwap`, `trend_donchian_1h`,
`xauusd_trend_1h`. (Counted from the file 2026-08-13; the previous "54
registered" was stale and also collapsed the two populations. Note
`xauusd_trend_1h` is `enabled: false` *with* `execution: live` — the two
gates are orthogonal and a leg is dark if EITHER excludes it, which is why
"registered" never implies "trading". A 2026-08-13 audit guard flagged that
leg as a defect; **the config was right and the guard was wrong** — do not
"fix" it.) The +6 over the 2026-07-09
count are the M27 15m scalp legs `ict_scalp_{sol,xrp,avax}_5m` +
`ict_scalp_{xrp,eth,sol}_15m`, paper-soak on `bybit_1`; grown from the 12-cell
2026-06-10 crypto+futures core through the multi-symbol crypto alts
(ETH/SOL/XRP/ADA/AVAX + prop variants), the OANDA/Alpaca/IBKR equity-ETF
sleeves, and the sub-$100 real-money proxy cells; see the change log).
Each declares a
`symbols:` list and is **scoped to it** by the per-strategy symbol gate
(`intent_multiplexer._collect_intents`, 2026-06-02): a strategy is skipped
on any tick symbol not in its `symbols:` — so a strategy no longer
"evaluates every configured symbol each tick" (the earlier MES-mirror
behaviour is intentionally retired). **The authoritative per-cell
`execution:`/`enabled:`/`symbols:` matrix is `config/strategies.yaml`,
surfaced on `/api/bot/strategies` — it is deliberately NOT re-enumerated in
prose here** (an exhaustive per-cell list in this doc is exactly what drifted
the earlier "12-cell" snapshot). Current roster *shape*, by instrument family
(verified 2026-07-09):
- **Crypto** — BTCUSDT core (`trend_donchian`, `fvg_range_15m`,
  `htf_pullback_trend_2h` are live; `turtle_soup`/`ict_scalp_5m`/`fade_breakout_4h`
  shadow; `squeeze_breakout_4h` live — RE-PROMOTED 2026-06-23, operator-approved;
  `vwap`/`trend_donchian_1h` `enabled: false`) plus the multi-symbol alts
  ETH/SOL/XRP/ADA/AVAX (`trend_donchian_*`, `*_pullback_2h`, `*_4h` cells) and the
  `*_prop` shadow variants that feed the Breakout prop bridge.
- **Index/metals futures (IBKR `ib_paper`, paper money):** `mes_trend_long_1d`
  (MES), `mgc_pullback_1d` (MGC), `mhg_pullback_1d` (MHG) live; `mgc_trend_1h`
  shadow. Since the 2026-07-07 IBKR STK build, `ib_paper` ALSO trades the 4
  compat-matrix-approved equity ETFs SPY/QQQ/IWM/TLT — no longer futures-only;
  `IBClient._build_contract` resolves FUT vs STK per-symbol via `ib_instruments.py`.
- **Equity/ETF (Alpaca):** `alpaca_paper` (paper) runs the broad ETF sleeve
  (SPY/QQQ/GLD/IWM/TLT/IEF/SLV/USO/GDX + leveraged TQQQ/QLD + proxies
  SPLG/IAUM/SCHA); **`alpaca_live` is currently `mode: dry_run` — SHELVED to dry
  2026-07-15** (Tier-3, operator-directed; `config/accounts.yaml`), so it sends no
  real-money orders right now. It *ran* a real-money ETF subset 2026-06-25→07-14
  (operator-approved, incl. the sub-$100 proxies SPLG/IAUM at `risk_pct 0.02`); the
  re-arm is gated on the /performance-review portfolio-vs-bybit benchmark.
  `alpaca_options_paper` runs the defined-risk options overlay (SLV/GDX).
- **FX/metals (OANDA `oanda_practice`):** `xauusd_trend_1h` (currently
  `enabled: false`; account `mode: dry_run`).

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
bybit_1 (paper) and bybit_2 (real money) share the decider + gate
machinery but are **no longer identical-roster mirrors** — bybit_2 was
narrowed to a winners-only real-money subset on 2026-06-02 (fewer
strategies than the bybit_1 paper book; the authoritative per-account
routing is `config/accounts.yaml`, surfaced on `/api/bot/config`). MES is a
separate IBKR book, not a redundant split of the crypto fund. **Decider-v2** (research) makes the selection smart
(regime-rule or selection-model, highest P(profit)) once ≥2 members are
live — a naive greedy decider lets the high-frequency 2h trend hog the
book and forfeits ~half the blend's return + diversification, so v2's job
is genuine selection. Design + single-account simulation:
[`docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`](sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md).
The decider remains research-stage — no live simulator script is wired
yet; research harnesses live under `scripts/research/`.

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
for IBKR futures AND equity/ETF orders (native bracket: market entry + TP limit
+ SL stop, prices snapped to the instrument's tick grid — the 0.25 MES-futures
grid, or the equity **penny** tick for the SPY/QQQ/IWM/TLT STK cells `ib_paper`
runs since 2026-07-07, resolved by `ib_instruments.tick_size_for`; IB uses no
API keys — auth is the Gateway login session). A symbol→exchange dispatch
gate in `src/core/coordinator.py` ensures a BTCUSDT signal can never
route to a futures account and vice-versa. Per-account dry/live mode
is set in `config/accounts.yaml` (`mode: live | dry_run`) and is the
only canonical execution gate; the **only** sanctioned mutation path
for that field is the `set-account-mode` operator action (§ Mode
Mutation Contract). The real-money `ib_live` account is held
`mode: dry_run`; the `ib_paper` account runs `mode: live` (paper
money) and went **live for MES on 2026-05-22**. **MES/MGC/MHG (and,
since 2026-07-07, the SPY/QQQ/IWM/TLT equity ETFs) execute live on
`ib_paper`** — the 2026-05-24 "IBKR offline pending new-user approval"
window is closed; the broker session is reached over the private subnet
from the isolated IB-Gateway VM (`10.0.0.251`), post the 2026-06-14
Ampere migration + gateway-isolation redesign (see the change log +
`docs/runbooks/ib-integration.md`). **IBKR is the futures
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
The real-money `alpaca_live` account (its own `ALPACA_API_KEY_ID_LIVE`/
`ALPACA_API_SECRET_KEY_LIVE` pair + `alpaca_env: live`) went **live
2026-06-25→26** (operator-approved; the host-routing bug where `alpaca_env`
was dropped by the account loaders was fixed #4916) and ran a real-money ETF
subset — incl. the sub-$100 proxies SPLG/IAUM added 2026-07-07 at the
normalized 5% caps / `risk_pct 0.02` — until it was **SHELVED to `mode: dry_run`
on 2026-07-15** (Tier-3, operator-directed): it currently places no real-money
orders (runtime `live.alpaca_live=false`), and the re-arm is gated on the
/performance-review portfolio-vs-bybit benchmark. Runbook:
`docs/runbooks/alpaca-integration.md`.

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

Distinct from `ict-heartbeat.{service,timer}`, which WAS the
once-daily operator status digest at 13:00 UTC — **retired 2026-07-08**
(notification streamlining), superseded by the hourly snapshot
(`ict-hourly-snapshot.timer`, now with a Training/ML section) + the
once-an-hour consolidated prop pulse; `install_systemd_units.sh`
disables it and never re-enables it (unit files kept inert).

The watchdog *restarts* the trader — it does not change the account
mode, and the Mode Mutation Contract does not regulate it. Restarts
are expected and safe; the Prime Directive forbids only the
mode-flip part of an automated response.

### Step 8 — Operator visibility and control
The Telegram bot (`src/bot/telegram_query_bot.py`) plus the FastAPI
diag surface (`src/web/api/routers/diag.py`) expose status, halt and
resume actions, and pending requests. **Two** frontends live in the
`ict-trader-dashboard` repo, and they consume the same unauthenticated Tier-1
endpoints (documented in [`api-tier-policy.md`](api-tier-policy.md)) over
**one transport** since the other two consumers were retired (2026-09-01):

| frontend | host | transport to `:8001` | status |
|---|---|---|---|
| Svelte SPA (`webapp/`) | GitHub Pages | **browser-direct HTTPS via Caddy** on the live VM — `https://ict-bot.duckdns.org` → `reverse_proxy localhost:8001`, Let's Encrypt cert auto-provisioned for the DuckDNS hostname. `/ws/market` upgrades to **WSS** through the same proxy. CORS **is** load-bearing. | ✅ **LIVE — the only consumer** |
| Streamlit dashboard (`streamlit_app.py`) | Streamlit Community Cloud | was **server-side plain HTTP** direct to `http://141.145.193.91:8001`. No tunnel, no proxy. CORS not load-bearing. | 🗄️ **RETIRED 2026-09-01** |
| Android app | device | was plain HTTP to the same `:8001` default (`AppPrefs.DEFAULT_BOT_URL`), cleartext-allowlisted per-host. | 🗄️ **RETIRED 2026-09-01** |

⚠️ **The retired rows are kept for transport archaeology only** — they are not
a consumer set. Operator decision 2026-09-01
(`BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED`): the SPA is
the only live consumer, which is the precondition for gating the read surface
in Phase H.

The SPA path is Caddy-fronted because a mixed-content plain-HTTP fetch from an
HTTPS page is hard-blocked by the browser. Server side:
`deploy/caddy/Caddyfile`, `scripts/ops/install_caddy.sh`, the
`vm-caddy-deploy` workflow. **The React+Vercel stack retired 2026-05-12 is a
different thing entirely** — do not read "the Vercel/CF stack was retired" as
"there is no proxy"; that was true of the retired Streamlit path and is false
of the SPA, which is now the only path there is.

**Observability caveat:** `caddy.service` is not an `ict-*` unit and ships no
`deploy/` unit file (it comes from the Caddy apt package), so it falls outside
`scripts/check_diag_unit_allowlist.py`'s `deploy/*.{service,timer}` glob and no
guard cross-checks it. It is listed in diag's `_CANONICAL_UNITS` **by hand** so
a Caddy outage shows on `/api/diag/services`. A Caddy failure takes the SPA and
the WSS market stream down — and since 2026-09-01 that is **the entire
consumer set**, where it used to leave Streamlit green. Cert expiry and the DuckDNS record are still
unwatched (`BL-20260813-CADDY-HTTPS-TRANSPORT-UNDOCUMENTED-AND-UNWATCHED`).

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
| Heartbeat timer | `deploy/ict-heartbeat.{service,timer}` — **RETIRED 2026-07-08** (was the once-daily 13:00 UTC operator digest; superseded by the hourly snapshot + consolidated prop pulse; installer disables it, files kept inert) |
| Liveness watchdog | `deploy/ict-liveness-watchdog.{service,timer}` — per-minute dead-man switch on `heartbeat.txt` mtime; alerts within 5 min and autoheals trader after 8 min stall (PRs #950/#956). Runbook: `docs/runbooks/liveness-watchdog.md`. Restarts only; does not change account mode. |
| Hourly snapshot | `deploy/ict-hourly-snapshot.{service,timer}` |
| Smoke once | `deploy/ict-smoke-once.service` |
| Claude bridge | `deploy/ict-claude-bridge.service` |
| Claude decision bot | `deploy/ict-claude-decision-bot.service` — POLLS the DEDICATED Claude bot (`TELEGRAM_CLAUDE_BOT_SECRET`) and registers the `wdec` `CallbackQueryHandler`, which is what makes a work-decision BUTTON answerable rather than merely delivered. ⚠️ **NOT the same bot as `ict-claude-bridge` above**, whose name is historical — that one polls the **PROP** token (and `TELEGRAM_CLAUDE_BOT_TOKEN` drives the prop bot despite its name). Its own unit rather than a second `Application` inside `ict-telegram-bot` because `run_polling()` owns an event loop and two do not compose, and one process is one event loop — a stall in the decision channel would otherwise become a stall in the operator's kill switch. A missing token exits `EX_CONFIG` (78) and `RestartPreventExitStatus` stops it in a visible `failed` state instead of crash-looping. |
| Env-check | `deploy/ict-env-check.service` |
| Web API watchdog | `deploy/ict-web-api-watchdog.{service,timer}` — restarts `ict-web-api.service` when the FastAPI surface is unreachable |
| IB Gateway (isolated VM) | `deploy/ict-ib-gateway-reset.{service,timer}` — headless IB Gateway (Docker + socat) now runs on its **own dedicated Ampere VM** (`ict-ib-gateway`, `10.0.0.251`), off the live trader's micro (gateway-isolation, Plan B, 2026-06-10). Recovery is one deterministic daily `docker restart` (**06:05 UTC**, retimed 2026-07-02 from 05:30 — the earlier time was inside IBKR's own ~03:45–05:45 UTC reset window and raced the outage it existed to fix, BL-20260623-002; gated to the gateway VM by `ConditionPathExists=/etc/ict/ib-gateway-docker.env`), **plus** the reactive ~5-min `ict-ib-gateway-watchdog.{service,timer}` (re-armed 2026-06-22, catches a mid-day wedge the daily reset alone would miss; carries `--suppress-window-utc 03:45-05:45` so it never restarts inside IBKR's own reset window either). The trader-side connect breaker (`IB_PROBE_TIMEOUT_S`/`IB_BREAKER_COOLDOWN_S`) keeps a gateway/network blip off the BTCUSDT loop. Runbook: `docs/runbooks/ib-integration.md` § "Gateway isolation redesign" |
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

Current workflows include CI guards. ⚠️ **There are THREE required
contexts, not nine, and most guard names are not workflow files** (corrected
2026-08-21 — this paragraph asserted both, and both were stale in the dangerous
direction). `REQUIRED_CONTEXTS` in `branch-protection-sync.yml` is
`["pytest-collect","pytest-run","guards"]`; **read it there, never here.**

The ~49 static guards (`ruff-lint`, `secret-scan`, `dry-run-guard`,
`env-gate-guard`, `silent-empty-guard`, `canonical-config-loaders`,
`canonical-db-resolver`, `strategy-coverage-guard`, `new-table-wiring-guard`,
`workflow-catalog`, …) are **ids in `scripts/ci/run_guards.py`'s registry**,
all executed by the single `guards.yml` job — consolidated from ~29 separate
workflows by BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES. Naming them with
a `.yml` suffix points at files that do not exist; `docs/github-actions-workflows.md`
carried the same confusion until 2026-08-21 and is now guarded against it in
both directions by `workflow-catalog`.

**A guard being outside `REQUIRED_CONTEXTS` no longer means it does not gate.**
`layer-guard`/import-linter (M0a/M0b platform-layering enforcement — see
`ROADMAP_MACRO.md § 1`) was described here as *"NOT yet a required check"*;
since the consolidation it carries no `allow_fail`, so a broken contract fails
the `guards` job, which **is** required — it gates `main` today. Advisory now
means a guard whose own script always exits 0 (`arch-doc-guard`) or one
declared `allow_fail`, not one merely absent from the required list. VM ops
(`system-actions`, `vm-diag-snapshot`,
`vm-web-api-recover`, `vm-net-diag`, `vm-net-fix`, `vm-cloud-fix`),
training (`training-run`, `training-rerun-5m`, `hf-cron`),
inventory/labels (`repo-inventory`, `bootstrap-labels`,
`branch-protection-sync`), and the autonomous follow-on driver
(`continue-work`).

## Repo Responsibility Map

| Area | Path | Notes |
|---|---|---|
| Runtime pipeline | `src/runtime/` | `pipeline.py`, `orders.py`, `validation.py`, `health.py`, `heartbeat.py`, `outcomes.py` |
| Strategies | `src/units/strategies/` | Strategy modules; wired via `config/strategies.yaml`. Two ISOLATED (non-`multi_account_execute`) sleeves live here: `pairs_executor.py`/`pairs_engine.py`/`pairs_sizing.py` (M22 2-leg pairs) and `macro_thesis/` (M28 macro/value thesis former — observe-only tick hook, no order path, import-linter-locked out of Execution). |
| Macro/value + sys-dynamics | `src/units/strategies/macro_thesis/`, `src/sysdyn/` | M28/M29 (NEW 2026-07): thesis former + valuation/event stores + FRED adapter + P4 thesis backtest (`macro_thesis/`); pure system-dynamics engine (`sysdyn/`, not yet tick-wired). Config `config/macro_*.yaml`; file-backed JSONL stores; import-linter-locked out of the order path. |
| Strategy registry | `src/strategy_registry.py` | Single source of truth for which strategies exist |
| Account / risk | `src/units/accounts/` | `risk.py`, `prop_risk.py`, `execute.py`, broker clients (`ib_client.py`/`alpaca_client.py`/`oanda_client.py`), `ib_instruments.py`, `__init__.py` (`load_accounts`). After the safeguards PR follow-on, `_DRY_RUN_OVERRIDES` and `set_account_dry_run()` are deleted; `_resolve_mode()` reads YAML directly. |
| Prop accounts | `src/prop/` | Breakout manual-bridge — `breakout_executor.py` (Telegram-ping prop executor), `account_rulesets.py`, `multi_account_ticket.py`, `montecarlo.py`, `prop_journal.py`, `prop_report.py` |
| Exchange connectors | `src/exchange/` | Bybit, Binance, IB market data (`ib_connector.py`) |
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
   - [`ml/configs/retired/baseline-trade-outcome-winrate.yaml`](../ml/configs/retired/baseline-trade-outcome-winrate.yaml)
     (WS5-A; per-strategy historical winrate). **Retired from the daily cycle
     2026-06-28** — an intentionally-trivial demo baseline whose `f1=0` is
     by-construction on a sub-50%-win-rate holdout; runnable ad hoc. See
     [`ml/configs/retired/README.md`](../ml/configs/retired/README.md).
   - [`ml/configs/retired/baseline-trade-outcome-global.yaml`](../ml/configs/retired/baseline-trade-outcome-global.yaml)
     (WS4-FU; global-mean sanity baseline). **Retired from the daily cycle
     2026-06-28** alongside its winrate twin (same by-construction `f1=0`).
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
   unchanged). Promotion past shadow (`shadow → advisory`) still
   requires `python -m ml promote-stage --by <name> --reason <text>`
   and operator approval. Models can be parked back at `candidate`
   via the same CLI when an operator wants them out of the shadow
   channel without retraining. (Stage ladder collapsed 7→3 on
   2026-06-16 — canonical `candidate → shadow → advisory`; legacy
   `research_only`/`backtest_approved → candidate` and
   `limited_live`/`live_approved → advisory` alias via
   `ml.manifest.canonical_stage`.)

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
[`docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`](sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md).
The decider remains research-stage (no simulator script wired yet;
research harnesses live under `scripts/research/`).

The full AI-platform architecture (five-layer model, leakage rules,
forbidden behaviors, model registry append-only invariant) lives in
[`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).

### VM topology (CANONICAL — single source of truth; live→Ampere migration COMPLETE 2026-06-14)

> **This table is the single canonical VM topology.** Every other doc,
> skill, runbook, and script should LINK here rather than re-state a VM IP
> or shape. When the topology changes, update THIS table and its mirror in
> `CLAUDE.md` § "VM authority split" — nothing else should hardcode a VM IP.

The "no heavy training on the Oracle live VM" non-negotiable
([`AI-TRADERS-ROADMAP.md`](AI-TRADERS-ROADMAP.md)) is enforced by
**topology**, not just policy. The system spans **three** Ampere VMs in the
same compartment + VCN — the money box is deliberately isolated:

| VM | Shape | Role | Systemd units | Marker file |
|---|---|---|---|---|
| **Live trader** `ict-bot-arm` (`141.145.193.91`) | `VM.Standard.A1.Flex` — **2 OCPU / 12 GB, Ampere aarch64** | Deterministic trade execution; FastAPI dashboard surface (`:8001`) | `ict-trader-live.service`, `ict-web-api.service` | `data-dir-nomount.conf` drop-in (`/data/bot-data` on boot volume) |
| **Training-center** (`158.178.209.121`) | `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere) | Model training, dataset builds, registry writes, experiment runs | `ict-trainer.service` (disabled by default), `ict-trainer.timer` (disabled by default) | `/etc/ict-trainer-vm.role` → `training-center` |
| **IB Gateway** (`ict-ib-gateway`, private IP `10.0.0.251`) | `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere) | Headless IB Gateway (Docker + socat) for MES/IBKR — isolated off the money box | `ict-ib-gateway-reset.{service,timer}` (daily **06:05 UTC** `docker restart`, retimed 2026-07-02 from 05:30 — BL-20260623-002) | `/etc/ict/ib-gateway-docker.env` (gates the reset.service) |

**Ampere Always-Free budget: trainer 1 + gateway 1 + live 2 = 4 of 4 OCPUs
(6 + 6 + 12 = 24 of 24 GB) — the pool is now FULL.** The **live→Ampere
migration COMPLETED 2026-06-14** (`.github/workflows/cutover-live.yml`); the
retired x86 micro `158.178.210.252` (a *separate* AMD Always-Free allocation,
display name `ict-bot`) was **terminated 2026-06-16** and is no longer a
rollback target. The gateway was moved onto its own VM (gateway-isolation,
Plan B) because the heavy Java/Xvfb/IBC gateway sharing the old 1 GB micro
with the trader caused the 2026-06-10 CPU-wedge cascade; the trader now
reaches it across the private subnet
(`config/accounts.yaml::ib_paper.ib_host = 10.0.0.251`). Full topology +
rationale:
[`docs/runbooks/live-vm-migration-ampere.md`](runbooks/live-vm-migration-ampere.md),
[`docs/runbooks/ib-integration.md`](runbooks/ib-integration.md)
§ "Gateway isolation redesign", and `CLAUDE.md` § "VM authority split".

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
promote up to `advisory`); live VM stays under the restrictive
contract in [`docs/claude/vm-operator-mode.md`](claude/vm-operator-mode.md).

The boundary that prevents trainer autonomy from leaking into live
**decisions** is the **stage** boundary, not the YAML wire-up
(2026-05-19 update). As of the default-flip, any model registered
at `target_deployment_stage: shadow` is auto-wired onto every
strategy's shadow channel — predictions are logged on signals
without operator approval. Operator approval is still required for
the cross into live influence: the
`shadow → advisory` promotion (the single live-influence gate; the
former `limited_live`/`live_approved` tiers collapsed into `advisory`
2026-06-16) remains gated, and the live-trader's order package is
unaffected by shadow predictions per the WS7 non-negotiable. An operator who
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
      operator action shipped in PR #978 (2026-05-12). Code-level cleanup of
      `_DRY_RUN_OVERRIDES` + `set_account_dry_run` + the breaker auto-flip
      **done** (2026-06-10 dead-code cleanup); a regression test
      (`tests/test_exchange_rejection_circuit_breaker.py`) asserts their
      absence.

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
| 2026-06-16 | (unified-confidence redesign — foundations + ML stage collapse) | **Two coupled changes: the ML deployment ladder collapsed 7→3, and the unified-confidence architecture's observe-only foundations shipped.** (1) **Stage collapse (PR #3748):** the 7-stage ladder had only 3 distinct runtime behaviours, so it is now canonical `candidate → shadow → advisory`. Backward-compatible — `ml.manifest.canonical_stage` + a permanent `STAGE_ALIASES` map normalize the legacy names (`research_only`/`backtest_approved → candidate`, `limited_live`/`live_approved → advisory`) on every read/write, so existing registry rows / manifests never strand; `advisory` is the single influence stage. (2) **Unified-confidence P0/P1 (PRs #3744, #3739, #3752):** design `docs/unified-confidence-risk-DESIGN.md` — collapse the per-model gates into composite confidence **lenses** that *advise* sizing/arbitration. Shipped offline + observe-only: `ml/calibration/` (per-strategy isotonic/Platt/decile confidence→P(win) calibrators, pure-Python predict), `src/runtime/conviction.py` (v1 blend) + `conviction_inputs.py` (model_scores+confidence→calibrated lens inputs), an **observe-only `conviction` stamp** in `strategy_signal_builders._emit_shadow_preds` (stamps `order_packages.meta.conviction` with provenance — **WS7-style: never read back into the order**, fail-permissive), and the **v2 `conviction-meta-v1`** dataset family + manifest (LightGBM stacker, `candidate` stage) that trains on the soak. Real per-strategy calibrators fit over multiyear data (ECE→~0). Operator decisions: per-trade risk budget 2%, size = conviction × per-trade-risk × available-margin (ceiling + throttle), drop the (dormant) `pos_size` cap, daily-loss + max-dd as the only account guards. | `ml/manifest.py`, `ml/registry/model_registry.py`, `ml/shadow/factory.py`, `ml/promotion/stage_guard.py`, `src/runtime/advisory_sizing.py`, `src/web/api/routers/training_center.py`, `ml/calibration/`, `src/runtime/{conviction,conviction_inputs}.py`, `src/runtime/strategy_signal_builders.py`, `ml/datasets/families/conviction_meta.py`, `ml/configs/conviction-meta-v1.yaml`, `scripts/ml/{build_calibration_corpus,fit_confidence_calibrators}.py`, `docs/unified-confidence-risk-DESIGN.md`, `CLAUDE.md`, `docs/claude/trainer-vm-mode.md`, this file | Deployed to live (`git_sha 022d4332`, verified healthy): stage collapse is live (behaviour identical for every currently-deployed model — only the advisory model influences); the conviction stamp is observe-only (no order/risk change) and now soaks `(lens inputs → outcome)` labels for the v2 meta-model. P2+ live influence (conviction actually driving sizing/arbitration) remains Tier-3, operator-gated, pending the soak. |
| 2026-06-14 | (orphan adopt→close→re-adopt churn — close-on-disappear blip guard) | **An adopted exchange orphan can no longer be closed (and then re-adopted as a fresh orphan) by a single empty exchange-position read — the close-on-disappear pass now requires a SECOND confirming absent observation.** Root-cause investigation of the recurring `ib_paper` MHG orphan (trades #2531→#2539→#2540→#2575→#2578, 2026-06-11..14) traced the churn to a chain whose upstream causes were already fixed and deployed — fractional-contract sizing (`BL-20260611-001`, whole-contract futures sizing + `IB_PLACE_CONFIRM_S`), Error-10349 spurious-cancel (`fix(ib)` explicit TIF on bracket legs), and the **MHGN6-localSymbol / notional-`averageCost` corruption** (`#3490 BL-20260613-IBPOS`: `IBClient.positions()` now emits `contract.symbol` + per-unit `averageCost/multiplier`, so an owned MHG trade reconciles against its own exchange position) — plus our re-attach + self-heal (#3543/#3549). The residual, matching the operator's "orphaned when the IBKR connection was down" hypothesis: a logged-out IB Gateway returns an **empty portfolio (`[]`, not a read failure → not `None`)**, and `_reconcile_orphan_exchange_positions`'s close-on-disappear pass closed the `orphan_adopt` row on that single empty read; the next healthy read re-adopted it as a NEW orphan (the #2540→#2575 adopt→close→re-adopt flip-flop). Fix (`BL-20260614-ORPHANBLIP`): mirror the forward reconciler's `_PENDING_CLOSE_CONFIRM` 2-observation pattern — a disappeared `orphan_adopt` (symbol,side) ARMS a per-trade close-confirm (`_PENDING_ORPHAN_DISAPPEAR_CONFIRM`) on the first absent pass and only closes once it reads absent across a second pass `>= RECONCILER_CLOSE_CONFIRM_SECONDS` later; a snapshot that brings the position back clears the pending close. `positions is None` (read failure) still skips the whole account untouched. Summary gains `pending_disappear`. **No env gate** — the confirm is always on (the env var is a tuning knob for the window, not an enable gate); fail-safe (in-process state, a restart re-arms from scratch and never closes early). | `src/runtime/order_monitor.py` (`_PENDING_ORPHAN_DISAPPEAR_CONFIRM`, close-on-disappear 2-observation guard, `pending_disappear` counter), `tests/test_reverse_reconciler.py`, this file | Tier-2 (reconciler close behaviour, observe-adjacent). Once deployed: a transient empty IB portfolio no longer closes + re-orphans an adopted position; a genuinely-gone position still closes one reconcile cycle later. Closes the "Prevention of the false-orphaning" follow-up flagged in the 2026-06-14 self-heal row. |
| 2026-06-14 | (orphan_adopt self-heal — repair existing rows every reconcile pass) | **`orphan_adopt` is a problem indicator, not a legitimate resting status — the reverse reconciler now repairs EXISTING orphan_adopt rows every pass, not just at adoption time.** PR #3547 re-attached an orphan at *adoption*; a row already sitting as `orphan_adopt` (adopted before the fix, e.g. the live MHG position) stayed unmonitored. New `order_monitor._reattach_adopted_orphans(db, summary)` runs at the top of `_reconcile_orphan_exchange_positions` (on every reconcile pass — the `MONITOR_RECONCILE_ENABLED` gate was removed 2026-06-15, BL-20260615-MGCNAKED, so the reconciler is now unconditional — independent of `ORPHAN_POSITION_POLICY`; repair is always correct): it scans open `strategy_name='orphan_adopt'` trades, and for each whose originating package is now recoverable (`_recover_orphan_order_package`, confident match) restores the trade's strategy + SL/TP and reopens/re-links the package so `run_monitor_tick` governs it. Idempotent; confident-match-or-skip (an unrecoverable orphan is left to keep surfacing as an orphan, never mis-attributed). Summary gains `reattached_existing`. | `src/runtime/order_monitor.py` (`_reattach_adopted_orphans`, called from `_reconcile_orphan_exchange_positions`), `tests/test_orphan_reattach.py`, this file | PR #3549 (Tier-3 — self-healing live-exit repair). Once deployed: the existing MHG orphan (and any pre-fix orphan_adopt with a recoverable origin) re-attaches to its strategy on the next reconcile pass — no manual DB action. Prevention of the false-orphaning that creates these (forward reconciler treating "broker unreachable" as "position flat") is a separate follow-up. |
| 2026-06-15 | (account_class funding-category axis + velotrade purge) | **A config-driven `account_class: paper \| real_money` funding category, orthogonal to the `mode:` execution gate, replaces the overloaded `demo` field as the paper/real reporting axis — AND the dead velotrade / DXtrade / `prop_velotrade_1` prop integration is purged.** (1) **account_class** is declared on every account in `config/accounts.yaml` (CI-guarded by `scripts/check_account_class.py` + `account-class-guard.yml`), parsed in `load_accounts` onto `TradingAccount.account_class` (invalid → coerced `real_money` with a WARNING, never raises), forwarded through the coordinator's `account_cfg`, and stamped by the executor onto a new additive `trades.account_class TEXT` column (idempotent `_migrate_add_account_class`); `is_demo` is kept in sync (= paper) for back-compat. **Fixes the `ib_paper` bug** where paper trades were journaled `is_demo=0` (real money) because the account carried no category stamp. `demo:` stays a **Bybit-ONLY transport flag** (api-demo.bybit.com) — `oanda_practice` / `alpaca_paper` dropped their `demo: true` stamp (superseded by `account_class: paper`). The API additively emits `accountClass` on `/positions`, `/trades/closed`, `/order-packages` (alongside legacy `isDemo`) + exposes it on `/config`; the real-money aggregates (`/stats`, `/pnl/history`, `/performance`) use an `account_class`-aware "not paper" predicate (falls back to `is_demo` for un-backfilled rows); the include-flag is now `include_paper` (`include_demo` = deprecated alias); `/performance` gains a `paper` sub-block (back-compat `demo` alias). One-shot `scripts/ops/backfill_account_class.py` (dry-run default, `--apply` to write) backfills + corrects existing rows. (2) **Velotrade purge**: deleted `dxtrade_client.py`, `velotrade_client_for`, the executor + coordinator + integrator velotrade branches/`EXCHANGE_MAP` entry, the `prop_velotrade_1` accounts.yaml block + `account_state.yaml` + master-template entries, and `tests/test_velotrade_infrastructure.py`. The generic `PropRiskManager` / prop-state infra is KEPT (not velotrade-specific). `breakout` stays a deprecated inert stub. | `config/accounts.yaml`, `config/account_state.yaml`, `config/master-secrets.template.yaml`, `src/units/accounts/{account,__init__,execute,integrator,clients}.py`, `src/units/db/database.py`, `src/core/coordinator.py`, `src/web/api/routers/{dashboard,trades_closed,order_packages,pnl_history,performance,bot_config}.py`, `scripts/check_account_class.py` (NEW), `scripts/ops/backfill_account_class.py` (NEW), `.github/workflows/account-class-guard.yml` (NEW), `CLAUDE.md`, this file, + matching tests | Tier-3 (touches `config/accounts.yaml` + live order-path stamp). Once deployed: every trade row carries its paper/real category; real-money dashboards/PnL exclude paper correctly even before backfill; the dead velotrade integration is gone. Run the backfill (`--apply`) once post-deploy to correct historical `ib_paper` rows. |
| 2026-06-15 | (exit-coverage rebuild — every open trade always has a live exit) | **The invariant that every open trade is, at all times, governed by a live strategy `monitor()` and/or a broker-side backstop — and that the system continuously detects + self-heals any trade out of coverage — is now structurally enforced, with no feature flags in front of it (`BL-20260615-MGCNAKED`; full design `docs/exit-coverage-architecture.md`).** (1) **Sleeve monitor() resolution** (#3662): `pipeline.monitor_unit_for` falls back to the intent-layer roster so the IBKR/FX symbol sleeves (`mgc_trend_1h`, `xauusd_trend_1h`, `spy/qqq/gld/eth_*`) resolve to their engine module's `monitor()` instead of failing to import a same-name module — the root cause of an unmanaged MGC `orphan_adopt`. (2) **Unconditional backstop** (#3674): the naked-position SL/TP re-arm (`_check_naked_positions` → `_attempt_naked_autoprotect` → `IBClient.place_protective`) is baseline — the `NAKED_POSITION_AUTOPROTECT` toggle was removed. (3) **De-gate** (#3676 Phase 1): the `MONITOR_RECONCILE_ENABLED` env gate was REMOVED entirely — the monitor reconciler / self-heal (orphan detect → adopt → reattach → stuck-watchdog → sweeps) runs unconditionally every tick like the core `monitor()` loop. (4) **Reattach-or-close** (#3676 Phase 2): an `orphan_adopt` row with no live monitor first tries to reattach to a recoverable order package; if none exists it is CLOSED (flattened, reusing the reduce-only close path, 2-observation-confirmed, position-aware so a gone position isn't double-handled) — a trade with no rational exit strategy is exited, not rested on a static stop (`_close_unattributable_orphan`). **This SUPERSEDES the earlier "unrecoverable orphan left to keep surfacing" behaviour described in the 2026-06-14 orphan_adopt self-heal row above.** (5) **Monitor-blindness** (#3703 Phase 3): `_call_strategy_monitor` returns `(verdict, status)`; a position whose `monitor()` persistently can't run (module-missing / raised / candles-`None`) fires a one-shot alert (`MONITOR_BLINDNESS_ALERT_TICKS`, default 3) instead of degrading silently. | `src/runtime/order_monitor.py`, `src/runtime/pipeline.py`, `src/runtime/execution_diagnostics.py`, `scripts/render_env_from_master.py`, `docs/exit-coverage-architecture.md` (NEW), `CLAUDE.md`, `docs/runbooks/monitor-reconciler.md`, `docs/claude/trading-mode-flags.md`, this file, + tests | PRs #3662 / #3674 / #3676 / #3703 (Tier-3 — live order/monitor path; operator-approved). Once deployed: no open trade rests without a live exit; un-attributable orphans are flattened; a position losing its dynamic exit is alerted. |
| 2026-06-16 | (live-trade management contract — PnL + management + reconcile made uniform across integrations) | **The two-sided "live-trade management" contract — every strategy monitors + updates its live trade; every integration applies those updates AND reconciles live state — is now real for the non-Bybit live integrations, replacing a Bybit-only implementation. Triggered by `ib_paper` MGC/MHG trades showing `$0.00`; the fix was scoped (operator direction) to the whole-repo structural gap, not the symptom.** Each axis becomes a **declared per-integration capability** (mirroring the 2026-06-15 exit-coverage "baseline, no flag" stance — no kill-switches). (1) **PnL resolution** (#3761): `src/runtime/local_pnl.py` + `order_monitor._sweep_local_pnl_for_unpriced` — *prefer broker truth, else local compute* (`(exit−entry)×qty×contract_value_usd`, mark-to-market exit when no broker fill). Source is declared via `clients.BROKER_PNL_READER_EXCHANGES` (`{bybit}`), default-local; dashboard `_local_unrealised_for_trade` does the same for open positions (`unrealizedPnlSource="markprice_local"`); `coordinator.py` always stamps `order_package_id`. Verified live: orphaned MGC #2596 → +$2600. (2) **Strategy verdict contract** (#3785): `src/runtime/strategy_verdict.py` schema + validator + the `new-strategy` skill step. (3) **Management capability layer** (#3787): `clients.EXCHANGE_MANAGEMENT_CAPS` + `account_supports_management`; the monitor senders return honest `unsupported_op:<op>` instead of `no_client`; Bybit byte-unchanged. (4) **IB + Alpaca close wiring** (#3792): `IBClient.close` (cancel-bracket + opposing reduce sized to the live position) + Alpaca native flatten, routed via `execute.close_open_position` + `_build_account_client`; a strategy's exit verdict now reaches IB/Alpaca. (5) **Universal position-snapshot reconciliation** (#3795): the reverse reconciler closes a DB-open strategy-attributed row on a non-order-status integration when its `(symbol,side)` is confirmed absent from a SUCCESSFUL `account_open_positions` snapshot (2-observation confirm, never on read-failure); Bybit stays on the forward order-status reconciler (the optional declared enhancement — there is NO per-broker default). **IB gateway-health gate**: `account_open_positions` now returns `None` (not `[]`) for a logged-out IB gateway (`net_liquidation` unpopulated), so a sustained logout can't false-close an open position. (6) **De-gate** (#3789): removed the `LOCAL_PNL_COMPUTE_DISABLED` kill-switch — baseline correctness ships ON (Prime Directive). | `src/runtime/{local_pnl,order_monitor,strategy_verdict}.py`, `src/units/accounts/{clients,execute,ib_client}.py`, `src/core/coordinator.py`, `src/web/api/routers/dashboard.py`, `.claude/skills/{new-broker,new-strategy}/SKILL.md`, `docs/audits/{live-trade-management-contract,local-pnl-fallback}-2026-06-16.md`, `docs/sprint-logs/S-LIVETRADE-MGMT-2026-06-16.md`, `CLAUDE.md`, this file, + tests; dashboard #101 + android #53 (paper-open visibility) | Tier-2/3 (live order-management path; each phase operator-reviewed). Deployed via git-sync. Once live: non-Bybit (IB/Alpaca) positions are PnL-priced, managed (close), and reconciled like Bybit's. **Remaining (improvements, not gaps):** trailing-SL `modify` for IB/Alpaca; real/paper metric separation (P4); CI guards enforcing the contract (P5); OANDA management before it leaves dry_run. |
| 2026-06-17 | (prop-accounts scalable architecture — account→ruleset + mandatory compat matrix + per-account ticket) | **Prop-firm accounts made first-class, multi-account, and folded into the STANDARD strategy flow (design: `docs/integrations/prop-accounts-architecture-DESIGN.md`).** (1) **account→ruleset binding** (`src/prop/account_rulesets.py`): every account resolves to a backtest unit — prop accounts → their prop ruleset (`config/prop_rulesets/*.yaml`, + `economics`/BANK-ASAP withdrawal), all others → a `standard` ruleset synthesized from the account `risk` block; reads via the canonical `load_accounts_dict`. (2) **mandatory per-account compatibility matrix** (`scripts/prop/account_compat_matrix.py`, required by the `backtesting` + `new-strategy` skills): one strategy × EVERY account's ruleset → ROUTE/skip, so a strategy is never routed to an account it wasn't evaluated against (prop → cost-aware EV+survival `src/prop/montecarlo.py::run_ev_montecarlo`; standard → net-of-fee performance). (3) **per-account ticket** (`src/prop/multi_account_ticket.py`): one signal → per-account legs + an explicit discrepancy banner so the executing assistant uses the right variation; multi-account from day one. (4) **Telegram-ping execution + cost-aware EV engine** (merged): `src/prop/{montecarlo,breakout_notify}.py`, `event_kinds.PROP_SIGNAL`, `scripts/ops/fetch_binance_vision.py`. Research finding: `trend_donchian` is +EV on high-vol Bybit alts (loser on BTC) — backlog `PB-20260616-004`. | `src/prop/{account_rulesets,multi_account_ticket,montecarlo,breakout_notify,ruleset}.py`, `scripts/prop/{account_compat_matrix,montecarlo_prop}.py`, `config/prop_rulesets/breakout.yaml`, `.claude/skills/{backtesting,new-strategy}/SKILL.md`, `docs/integrations/prop-accounts-architecture-DESIGN.md`, `CLAUDE.md`, this file | Tier-1 (tooling/docs/skills — merged + on PR #3825); the live wiring (alt-variant strategies, prop account in `accounts.yaml`, the Telegram-ping executor) is **Tier-3, DRAFT** — gated on real-venue validation + operator approval. |
| 2026-06-17 | (Breakout prop WIRING — the Tier-3 DRAFT the architecture row above left open: alt variants + prop account + Telegram-ping executor) | **The live wiring that the prop-accounts architecture (row above) deferred is now built on top of that scaffolding** — `breakout` is NO LONGER a deprecated inert stub (SUPERSEDES the "breakout stays a deprecated inert stub" note in the 2026-06-15 account_class row). (1) **Executor** (`src/prop/breakout_executor.py` + `EXCHANGE_MAP["breakout"]`): a prop-routed strategy builds its per-account leg via `src/prop/multi_account_ticket.build_account_leg` and emits `prop_signal` (Telegram/FCM, routed to the prop bot) INSTEAD of a broker API call; `execute_pkg` (breakout branch, before the client-None dry forcing, gated on the caller's resolved dry-ness so `execution: shadow` still suppresses) returns a `prop-manual-<uuid>` marker so the order package journals WITHOUT a phantom exchange position the monitor would manage (it's attributed → never orphan-closed; management honestly no-op'd by the capability layer). POC = Telegram pings only, no DXTrade API. (2) **Alt variants**: `trend_donchian_sol` (execution: live) + `trend_donchian_eth` (execution: shadow) reuse the `trend_donchian` unit on SOLUSDT/ETHUSDT, BOTH-SIDES to match the validation. (3) **Prop account** `config/accounts.yaml::breakout_1`: `exchange: breakout`, `type: prop` (mission-aware `PropRiskManager` tracking the `account_state: evaluation→funded` lifecycle), `account_class: prop` (a THIRD funding category — added to `_VALID_ACCOUNT_CLASSES` + the account-class guard; EXCLUDED from the real-money NOT-paper predicate in all 5 routers so prop never contaminates real-money OR paper KPIs), `mode: live` (ALWAYS-LIVE ping; the operator decides per-signal whether to place — no programmatic order), `backtest_ruleset: prop_rulesets/breakout.yaml`. (4) **Validation** (additive to #3833's Binance-perp run): re-validated on REAL Bybit linear-perp 5m + a Breakout **daily-swap** cost model (~0.09%/day vs Bybit 8h funding) + a 4-fold walk-forward (`src/prop/funding.py`, `scripts/prop/validate_alt_prop.py`) — SOL robust PASS, ETH marginal PASS, BNB excluded. | `src/prop/{funding,breakout_executor}.py`, `src/units/accounts/{execute,integrator,account}.py`, `scripts/check_account_class.py`, `src/runtime/{pipeline,intents,intent_multiplexer,strategy_signal_builders}.py`, `src/web/api/routers/{dashboard,performance,order_packages,pnl_history,trades_closed}.py`, `config/{strategies,accounts}.yaml`, `config/prop_rulesets/breakout_routing.yaml`, `config/strategy_{descriptions,changelog}.json`, `scripts/prop/{validate_alt_prop,run_real_validation.sh}`, `scripts/ops/fetch_bybit_funding.py`, `runtime_logs/prop_eval/2026-06-17-validate-real/`, this file, + tests | Tier-3 (config/strategies.yaml + accounts.yaml + order-path executor). PR #3840; operator-approved 2026-06-17. |
| 2026-06-17 | (prop alt variants — directional A/B → SOL long-only, ETH two-sided) | **The Breakout alt variants are no longer both BOTH-SIDES — `trend_donchian_sol` is now LONG-ONLY (SUPERSEDES the "BOTH-SIDES to match the validation" note in the prop-WIRING row above for SOL).** A Breakout daily-swap long-only A/B (re-running the same gate with `--long-only`, `runtime_logs/prop_eval/2026-06-17-validate-real/`) split the two: **SOL's** edge HOLDS long-only (pre-swap +$1,325 → post +$1,158; funded 12-mo EV +$1,131 @1.5%, 86% P>0; 4/4 OOS folds, cleaner per-fold dispersion), so `trend_donchian_sol` gets `long_only: true`; **ETH's** edge is short-side-dependent — long-only FLIPS it negative (pre +$23 → post −$181; EV@1.5% +$415/66%, only 3/4 folds, fold-1 −$166), so `trend_donchian_eth` stays two-sided (no `long_only`). Enforced live: `_trend_donchian_variant_builder` now honours a per-variant `long_only` flag (mirroring the flagship `trend_donchian` builder — the variant builder previously had no long-only branch, so the YAML flag would have been inert), suppressing shorts + journalling a `short_suppressed_long_only` eval row. Also: the prop ticket drops the DXTrade-symbol scaffolding (`src/prop/breakout_ticket.py` — we trade in the Breakout terminal); and `backtest_system.generate_signal_stream` gains an opt-in `long_only` override so the engine can A/B a strategy's directional discipline (off by default, no change to existing runs). | `config/strategies.yaml`, `src/runtime/strategy_signal_builders.py`, `src/prop/breakout_ticket.py`, `scripts/backtest_system.py`, `scripts/prop/{validate_alt_prop,run_real_validation.sh}`, `runtime_logs/prop_eval/2026-06-17-validate-real/NOTE.md`, `tests/test_trend_donchian_long_only.py`, `tests/test_breakout_prop_wiring.py`, this file | Tier-3 (config/strategies.yaml + order-path builder). PR #3848; operator-approved 2026-06-17. |
| 2026-06-17 | (prop alt variant `trend_donchian_eth` promoted shadow → live) | **`trend_donchian_eth` moved `execution: shadow` → `live` (SUPERSEDES the "execution: shadow" disposition for ETH in the prop-WIRING + directional-A/B rows above).** Operator-approved after the directional A/B confirmed ETH stays two-sided (its edge is short-side-dependent). It now emits supervised prop tickets like `trend_donchian_sol` — for a prop account "live" means the Telegram/FCM ping fires automatically; the operator still gates each placement in the Breakout terminal (no broker API), so promoting a marginal-but-consistent edge (EV@1.5% +$1,050, 92% P>0, 4/4 folds, 57% swap drag) onto a manual-bridge ping is low-risk and reversible (flip back to `shadow` + restart). Both prop variants are now `execution: live`; neither needs a `shadow-guard: allow` marker. SWAP-RATE: operator delegated the call to Claude — keep the validated 0.0009/day (~0.09%/day) model assumption (it doesn't gate the supervised-ping POC); confirm the real per-symbol swap from the Breakout instrument spec before sizing real capital (tracked in `PB-20260616-004`). | `config/strategies.yaml`, `config/strategy_{descriptions,changelog}.json`, `tests/test_breakout_prop_wiring.py`, `docs/claude/performance-review-backlog.json`, this file | Tier-3 (config/strategies.yaml live-execution flip). PR #3852; operator-approved 2026-06-17. |
| 2026-06-27 | (Alpaca options Slice 4+5 — options-expression lifecycle close + defined-risk surfacing) | **The Alpaca debit-vertical options-expression account (`alpaca_options_paper`, Slice 3b) gains a CLOSE path and on-screen surfacing — completing the paper pilot.** (4) **Options-lifecycle reconciler** (`order_monitor._reconcile_options_expiry_and_assignment`, scoped to accounts where `options_overlay.account_expresses_options` is truthy): each monitor tick polls Alpaca `/v2/account/activities` (`AlpacaOptionsExecutor.account_activities`, types EXP/OPASN/OPEXC, lookback `OPTIONS_LIFECYCLE_LOOKBACK_DAYS` default 4d) + the open-option snapshot, and closes a journal row whose structure has **concluded** (`options_lifecycle.structure_concluded`: a broker-confirmed lifecycle event AND no remaining open option position — never mere position-absence, the guard against the 2026-06-27 shared-login adoption incident). Realised PnL is sourced from the activities' cash (`realized_pnl_from_activities`: close cash − open debit; a fully-OTM expiry → full debit loss); these rows are now **deferred** in `_sweep_local_pnl_for_unpriced` so the equity entry×exit×qty formula never mis-prices them (the incident's phantom-PnL class). Ambiguous rows (two open rows share an underlying) are left for manual resolution. An active pre-expiry close path exists (`AlpacaOptionsExecutor.close_structure`, per-leg liquidation) but is not auto-invoked (spreads ride to expiry). (5) **Defined-risk surfacing**: `options_overlay.options_structure_dict` persists the leg/strike/net-debit/max-loss/breakeven/expiration geometry in the trade's `notes.options` at open (`execute._log_trade_to_journal` gains `extra_notes` + an adaptive notes cap), and `/api/bot/positions` exposes it as a nested `options` block (`null` for non-options rows), **connection-free** — per-leg live greeks/PnL are a deliberate follow-up (the endpoint never opens a broker socket). System-review note `BL-20260627-OPTIONS-SEPARATE-ACCT`: revisit a dedicated Alpaca paper login vs the shared `alpaca_paper` keys. | `src/units/accounts/{options_lifecycle,alpaca_options_exec,options_overlay,execute}.py`, `src/runtime/order_monitor.py`, `src/web/api/routers/dashboard.py`, `CLAUDE.md`, `docs/claude/health-review-backlog.json`, this file, + tests; dashboard #125 + android #72 (render the `options` block) | Tier-2 (order-path journaling + monitor close path + additive public-API field). PR #4867. |
| 2026-07-02 | (IB Gateway daily-reset retime — BL-20260623-002 root-cause fix) | **`ict-ib-gateway-reset.timer` retimed 05:30 → 06:05 UTC — the one deterministic restart the gateway's whole recovery design relies on was firing INSIDE IBKR's own documented ~03:45–05:45 UTC overnight reset window, not after it, so it raced the outage it existed to fix (confirmed recurring 2026-06-23 and 2026-07-02 by a `/system-review` session that read the timer's own header comment against its actual `OnCalendar`).** `scripts/check_ib_gateway.py` gains `--suppress-window-utc HH:MM-HH:MM` (wired into `ict-ib-gateway-watchdog.service` as `03:45-05:45`): a wedge detected inside that window is still logged/alerted (visibility preserved) but the streak/restart bookkeeping is frozen — not reset — so it resumes counting the instant the window closes instead of burning a restart attempt (and its 20-min cooldown) on an attempt that can't succeed. Narrowly scoped to this one flag; the mid-day reactive auto-heal (BL-20260622-GATEWAY-MIDDAY-WEDGE) is unaffected outside the window. | `deploy/ict-ib-gateway-reset.timer`, `deploy/ict-ib-gateway-watchdog.{service,timer}`, `scripts/check_ib_gateway.py`, `tests/test_ib_gateway_watchdog.py`, `docs/runbooks/ib-integration.md`, `CLAUDE.md`, `deploy/ict-mes-ibkr-pull.timer`, this file | Tier-2 (timer/service change on the gateway VM). Self-deploys via `ict-git-sync` on merge. |
| 2026-07-02 | (Trainer checkpoint/resume + memory guard — BL-20260702-TRAINER-OOM root-cause fix) | **A same-day OOM kill of `ict-trainer.service` used to strand every not-yet-trained manifest until tomorrow's timer fire — the daily cycle globbed the full `ml/configs/*.yaml` fresh every run with no record of prior progress, and the unit had no memory cap (`OOMPolicy=stop` default kills the whole cgroup on any single manifest's OOM).** `scripts/ops/run_training_cycle.sh` now reads/writes `runtime_logs/trainer/cycle_progress_<UTC-date>.json` (per-manifest pending/running/done/skipped/failed, updated before+after each `python -m ml train` call) and only trains manifests not already `done`/`skipped` today; a `flock` on `runtime_logs/trainer/.cycle.lock` prevents a concurrent invocation from racing the checkpoint file. New `ict-trainer-catchup.timer` (`05:00 UTC`, disabled by default in cloud-init, enabled autonomously by Claude) re-invokes the script same-day — a near-no-op on a clean day, a same-day resume on a killed one. `ict-trainer.service` gains `MemoryHigh=4G`/`MemoryMax=5G`/`OOMPolicy=continue` so a single expensive manifest costs one `failed` row instead of the whole cycle. Verified via `TestCheckpointResume` (4 new tests: same-day no-op, killed-subprocess resume, force-restart, concurrent-lockout). | `scripts/ops/run_training_cycle.sh`, `deploy/training-vm-cloud-init.yaml`, `tests/test_run_training_cycle_sh.py`, `docs/ml/training-center.md`, `docs/runbooks/training-vm.md`, this file | Tier-1 (trainer-VM only, no live-VM/order-path impact). Script change ships via the trainer's normal self-pull of `main`; unit/timer activation (`daemon-reload`, `enable --now ict-trainer-catchup.timer`, the new `ict-trainer.service` resource properties) via an autonomous `trainer-vm-diag-request` post-merge — no operator approval needed per the VM-authority split. |
| 2026-06-07 | S-MLOPT-CLOSEOUT-2026-06-07 | **Retroactive consolidated row for the 2026-06-07 closeout (deferred at session-end per `BL-20260607-006`, filed 2026-07-06 by `/system-review` weekly).** Four items shipped that session without their own arch-log row: (1) **S-MLOPT-S18** — champion-challenger promotion-readiness orchestrator (trainer-side, standalone systemd unit) that surfaces shadow/advisory promotion candidates. (2) **S-MLOPT-S16** — ADWIN drift-triggered retraining tooling (trainer-side): detects feature/score drift and schedules an out-of-cycle retrain instead of waiting for the daily timer. (3) **S-MLOPT-S12 Part B** — new `trade_journal.db::account_context_snapshots` table + a best-effort `Coordinator` writer hook (`src/units/accounts/context_snapshot.py`) capturing per-signal pre-decision account state (equity, daily PnL, daily equity-high, drawdown%, open-trade count); observe-only, gated by `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED` (default on). (4) **PERF-20260601-006 Phase 3** — the regime-router hard-gate runtime path (`config/regime_policy.yaml` OFF-cell enforcement), at the time gated by `REGIME_ROUTER_ENABLED`/shadow-log-only default (later flipped baseline-on at the Design-A vol-gate go-live, 2026-06-28 — see that row). All four are detailed in `docs/sprint-logs/S-MLOPT-CLOSEOUT-2026-06-07.md`; `CLAUDE.md` Env Variables + API tables were updated in-session for both new env-var gates and the new table. | `ml/` (trainer-side S18/S16 tooling), `src/units/accounts/context_snapshot.py`, `src/core/coordinator.py`, `trade_journal.db::account_context_snapshots` (new table), `config/regime_policy.yaml`, `docs/sprint-logs/S-MLOPT-CLOSEOUT-2026-06-07.md`, this file | No new operator impact beyond what shipped 2026-06-07 (already deployed and stable) — this row only closes the doc-freshness gap so the change log matches what's actually running; `arch-doc-guard` did not block any of the four PRs since none touched an arch-doc path directly. |
| 2026-07-07 | (IBKR equity/ETF STK contract support, steps 1-5) | **New per-symbol IBKR instrument-type resolver — `ib_paper` can now build BOTH futures and equity/ETF contracts on one account/clientId.** `docs/integrations/ibkr-equity-etf-support-DESIGN.md` (operator-approved 2026-07-07: all 10 alpaca ETFs, reuse `ib_paper` rather than a new account, keep the existing Alpaca/yfinance signal-candle source and route only execution to IB). (1) New `config/instruments.yaml::instruments.<SYM>.ib` block (config-driven `{sec_type: FUT\|STK, exchange, primary_exchange, currency}`) + resolver `src/units/accounts/ib_instruments.py::ib_instrument_spec()` — the single source of truth for "is this symbol a FUT or a STK on IBKR, and on which exchange", falling back to the legacy hardcoded `{MES:CME, MGC:COMEX, MHG:COMEX}` map for back-compat. (2) `IBClient._build_contract` gains a STK branch (`Stock(sym,'SMART','USD',primaryExchange=...)` + `qualifyContracts`) alongside the unchanged futures path; the per-symbol contract cache now matters more since one clientId can hold both a FUT and a STK contract. `tick_size_for` resolves the standard equity penny tick instead of falling through to the MES 0.25 default. (3) `Coordinator.multi_account_execute` resolves `market_type`/`whole_units` PER ORDER (symbol-aware, via `ib_instruments.ib_order_market_type()`) instead of trusting the account's static `market_type: futures` — an IB equity order now takes the whole-SHARE sizing path (round-up-to-1-share relaxation + margin/buying-power cap), MES/MGC/MHG keep the unchanged strict whole-contract path. NOT included: the `accounts.yaml` wiring that actually routes ETF strategies onto `ib_paper` (Tier-3, pending the mandatory `scripts/prop/account_compat_matrix.py` evidence run + explicit operator approval) and live paper verification — tracked in `docs/claude/performance-review-backlog.json::PB-20260707-IBKR-STK-ETF-SUPPORT`. | `config/instruments.yaml`, `src/units/accounts/ib_instruments.py` (NEW), `src/units/accounts/ib_client.py`, `src/core/coordinator.py`, `tests/test_ib_instruments.py` (NEW), `tests/test_ib_integration.py`, `tests/test_ib_sizing_and_data.py`, `docs/claude/performance-review-backlog.json`, this file | None on live-VM behaviour yet — purely additive contract-building + sizing-resolution capability; `ib_paper` doesn't route any ETF strategy until the separate Tier-3 accounts.yaml PR merges. MES/MGC/MHG unaffected (same `_build_contract`/whole-contract-sizing path, just resolved from the new per-symbol map instead of a hardcoded dict literal). |
| 2026-07-07 | (IBKR equity/ETF STK contract support, step 6 — `ib_paper` ETF wiring) | **`ib_paper` now trades 4 equity-ETF cells alongside MES/MGC/MHG, gated by the mandatory per-account compatibility matrix.** Completes step 6 of the design (operator-authorized "merge and continue" after the pre-established decisions). `scripts/ops/etf_account_compat.sh`'s `CELLS` array was missing `slv_pullback_1d` + `gdx_pullback_1d` (2 of the 16 alpaca_paper ETF cells) — added, params mirrored from `gld_pullback_1d`. Ran `ACCOUNTS=ib_paper bash scripts/ops/etf_account_compat.sh` on the trainer VM (issue #5908): 13/16 cells scored (3 skipped — no trainer-VM candle CSV for TQQQ/QLD/GDX), and **only 4 scored ROUTE against `ib_paper`'s own ruleset** (`risk_pct: 0.015`, far more aggressive than `alpaca_paper`'s 0.3% per-cell harness assumption): `spy_trend_long_1d`, `qqq_trend_long_1d`, `iwm_trend_long_1d`, `tlt_pullback_1d` (P(breach) 0.005–0.042, survival 1.0 on all four). The other 9 scored cells SKIPPED at that risk_pct — the same failure class as the TQQQ/QLD-on-`alpaca_live` gate in `PB-20260630-002` (a real edge still trips the breach cap at a risk_pct this high). Wired **only the 4 ROUTE cells** onto `ib_paper` (`config/accounts.yaml::ib_paper.strategies` += the 4 names, `.symbols` += `[SPY, QQQ, IWM, TLT]`); the 9 SKIP cells and 3 no-data cells stay off `ib_paper` this pass — full rationale recorded inline in the `accounts.yaml` comment and in the backlog item. Live paper verification (design step 7 — place → journal → monitor → close a real fill) is the remaining follow-up. | `config/accounts.yaml`, `scripts/ops/etf_account_compat.sh`, `tests/test_ib_integration.py`, `docs/claude/performance-review-backlog.json`, this file | Live VM (next deploy): `ib_paper` starts evaluating SPY/QQQ/IWM/TLT signals and can place real IBKR paper-money equity orders for the 4 wired cells, in parallel with the same 4 already trading on `alpaca_paper` — cross-broker fill validation, no new strategy logic. MES/MGC/MHG unaffected. |
| 2026-07-09 | (Full-system audit 2026-07-09 — canonical-doc reconciliation, S-AUDIT-A) | **Reconciled this doc + `ROADMAP.md` against config/code on disk after Phase-0 of the full-system audit found material drift in the #2/#3 yardsticks.** Fixes: the "12 strategies registered (verified 2026-06-10)" count → **48 cells**, and the exhaustive per-cell enumeration in Step 2 replaced with a family-level *shape* + a pointer to the authoritative source (`config/strategies.yaml` / `/api/bot/strategies`) so the prose can't re-drift; the stale per-cell gate claims corrected (`squeeze_breakout_4h` is `execution: live`, re-promoted 2026-06-23 operator-approved, NOT shadow; `turtle_soup`/`ict_scalp_5m` are shadow, not "all live"); the 2026-05-24 "IBKR offline pending approval" note retired (MES/MGC/MHG + the 2026-07-07 ETFs execute live on `ib_paper`); the Step-3 "bybit_1/bybit_2 are mirrors" claim corrected to the 2026-06-02 winners-only divergence; the Step-6 "Real-money Alpaca remains gated" note superseded (`alpaca_live` is a live real-money book, operator-approved). The `_DRY_RUN_OVERRIDES`/`set_account_dry_run()` "deletion never landed" self-contradiction resolved in favour of reality (deleted; regression test asserts absence). Residual tail completed in the follow-up S-AUDIT-A PR: the Step-6 Alpaca prose (still reading "Real-money Alpaca remains gated") corrected to `alpaca_live` live-real-money; the stale breaker line-number (`coordinator.py:1669-1689` → symbol-anchored `_EXCHANGE_REJECTION_COUNTS` reference); the equity-penny-tick note added for `ib_paper`'s STK/ETF cells; the repo-map omissions filled (IB market-data connector, the broker clients, a `src/prop/` prop-executor row); plus the `ROADMAP.md` M15 `alpaca_live` SPLG/IAUM sub-$100-proxy detail (D9) and the stale "next is WS5-B-PART-2" AI-traders queue note (D10 — it closed 2026-05-10). No code/config changed — documentation-hygiene only (field-beats-comment; the field changes were all operator-approved). Audit program doc: `docs/audits/full-system-audit-2026-07-09.md`. | `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`, `docs/CLAUDE-RULES-CANONICAL.md` (Phase-0 rule fixes R1-R5), `docs/audits/full-system-audit-2026-07-09.md` | Tier-1 (docs only; no runtime/order-path/config change). |
| 2026-07-15 | M22 D2 — market-neutral pairs sleeve (NEW isolated 2-leg order path) | **A NEW order path distinct from `multi_account_execute`.** The winner of the M22 small-TF research is a market-neutral crypto cointegration-pairs sleeve — a pair is TWO simultaneous opposite legs, which does not fit the single-symbol intent model. So it runs as its own once-per-tick hook `src.units.strategies.pairs_executor.run_pairs_tick` (prop-bridge pattern), called from `src/main.py` after `run_monitor_tick` — NOT through the coordinator. Config is a dedicated `config/pairs.yaml` (the 4 validated pairs SOL/BTC, BNB/BTC, ETH/BTC, SOL/ETH; each carries the sanctioned `execution: shadow|live` gate, `account_id`, params). Per closed 1h bar per pair: fetch both legs' candles → reconstruct open-state from journal-durable `order_packages.meta` → `decide_pair` (pure) → for an `execution: live` pair place BOTH legs via `_log_new_order_package` + `execute_pkg` (reusing the validated placement + atomic SL/TP + journal-write), linked by a shared `meta.pairs_group_id`, with a leg-imbalance unwind if the 2nd leg fails; close flattens both legs. Legs are journalled with per-leg strategy names `pairs_<name>_a`/`_b`, which `pipeline.monitor_unit_for` resolves to `pairs_executor` (whose `monitor()` returns None — the executor owns the joint spread-exit; the per-package monitor cleanly no-ops on a pairs leg, the wide per-leg catastrophe SL/TP is the last-resort net). Observe-only soak `src.runtime.pairs_soak` → `runtime_logs/pairs_soak.jsonl`, surfaced Tier-1 read-only at `GET /api/bot/pairs/soak`. `BNBUSDT` added to `config/instruments.yaml` (BNB/BTC leg; reporting/routing only, not in any account's `symbols` roster). Shipped foundation at `execution: shadow` (#6519) then flipped all 4 to `execution: live` on `bybit_1` (Bybit demo / paper venue) operator-approved (#6521). | `src/units/strategies/pairs_executor.py` (NEW), `src/units/strategies/pairs_engine.py` (NEW, #6518), `src/units/strategies/pairs_sizing.py` (NEW, #6518), `src/runtime/pairs_soak.py` (NEW), `src/web/api/routers/pairs.py` (NEW), `src/web/api/main.py`, `src/main.py`, `src/runtime/pipeline.py`, `config/pairs.yaml` (NEW), `config/instruments.yaml`, `CLAUDE.md`, this file | Live VM (next deploy): the pairs sleeve places real 2-leg orders on `bybit_1` **paper** money on a cointegration entry signal — a NEW order path, isolated from the single-symbol flow. Real-money/paper KPIs unaffected (paper account). Rollback: set a pair back to `execution: shadow` in `config/pairs.yaml` (or delete the file) — one-line, no redeploy. Tier-3, operator-approved. |
| 2026-07-16 | S-PAPER-PORTFOLIO — live-portfolio-mirror paper accounts + `paper_role` axis | **Two new PAPER accounts that mirror the *actual live-traded portfolio*, so the real portfolio's performance + risk-management plumbing can be read on paper money without the small-account trading constraints of the real books and without the full soak-roster noise.** (1) `config/accounts.yaml::bybit_portfolio` — Bybit DEMO (paper), `account_class: paper`, mirrors `bybit_2`'s winners-only roster + symbols + linear-perp/3× profile; keys `BYBIT_API_KEY_3`/`_SECRET_3` (reutilized "bybit 3" demo keys, secret auto-derived). (2) `config/accounts.yaml::alpaca_portfolio` — Alpaca PAPER host, `account_class: paper`, dedicated login `ALPACA_API_KEY_PAPER_PORTFOLIO`/`ALPACA_API_SECRET_KEY_PAPER_PORTFOLIO`, mirrors `alpaca_live`'s roster + symbols **minus the affordability proxies** `splg_trend_long_1d`/`iaum_pullback_1d` (SPLG/IAUM) — operator-approved divergence: those proxies exist only for the ~$150 real account's whole-share affordability, so running them alongside the primaries (SPY/GLD) on a large paper balance would double S&P/gold exposure. Both are `mode: live` (paper money executes). No new tick-loop symbols (all already in the union) and no `strategies.yaml` change (rosters are all `execution: live`, except `ict_scalp_5m` which shadow-logs on `bybit_2` too). (3) NEW optional per-account field **`paper_role: portfolio | soak`** (absent ⇒ soak) — distinguishes the live-PORTFOLIO mirror from a data-only SOAK book (`bybit_1`/`alpaca_paper`/`ib_paper`/`alpaca_options_paper`); surfaced on `/api/bot/config` (added to `_ACCOUNT_PUBLIC_FIELDS`). Consumer semantic (operator directive): the dashboard/Android **"Paper" view scopes to `paper_role: portfolio`**; soak books appear only on the Accounts page. (4) `sync-vm-secrets` OPTIONAL set + steps extended with the 4 new secret names. | `config/accounts.yaml`, `src/web/api/routers/bot_config.py`, `.github/workflows/sync-vm-secrets.yml`, this file (+ dashboard/Android UI PRs) | Live VM (next deploy): after `sync-vm-secrets` lands the 4 keys in `.env`, then `pull-and-deploy`, the two paper-portfolio accounts start trading the mirrored rosters on paper money. Real-money KPIs unaffected (paper). Rollback: delete the two account blocks. **Bybit key IP-allowlist: add the live-trader egress IP `141.145.193.91`.** Tier-3 (config/accounts.yaml) — operator-approved. |
| 2026-07-22 | M27 P1 — 15m scalp legs (paper soak) | **Six new ICT-scalp cells wired for a paper soak on `bybit_1`.** The M27 small-TF research added `ict_scalp_{sol,xrp,avax}_5m` + `ict_scalp_{xrp,eth,sol}_15m` (roster 48 → 54). All are `execution: live` + `enabled: true` but routed **only** to `bybit_1` (Bybit demo / **paper**) — absent from real-money `bybit_2`/`bybit_portfolio`, so it is a deliberate paper-soak, not a real-money change. Signal builders registered via the named `_ict_scalp_variant_builder` (`strategy_signal_builders.py`), in the intent roster (`intent_multiplexer.py`) + monitor registry; symbol pinned from each cell's `symbols:`, no hidden gate. Operator-approved paper soak 2026-07-22 (`docs/research/M27-P1-15m-promotion-proposal-2026-07-22.md`). | `config/strategies.yaml`, `config/accounts.yaml` (bybit_1 roster), `src/runtime/strategy_signal_builders.py`, `src/runtime/intent_multiplexer.py`, this file | Live VM: `bybit_1` (paper) evaluates the 6 new scalp legs; real-money KPIs unaffected. Rollback: `enabled: false` on the cells. Tier-3, operator-approved paper soak. |
| 2026-07-23 | M28/M29 — macro/value thesis subsystem + system-dynamics engine (NEW, OFF-VM/observe-only) | **A substantial new, fully ISOLATED subsystem** (no live order path; import-linter-locked out of Execution). **M28** (`src/units/strategies/macro_thesis/` — `thesis_engine`, `thesis_store`, `event_store`, `event_calendar`, `valuation_feed`/`valuation_store`, `fred_adapter`, `thesis_backtest`/`thesis_replay`, `thesis_tick`): a macro/value trade-thesis former — point-in-time valuation snapshots (FRED + non-FRED sources), an event subsystem, a rule-based thesis former, and a no-lookahead P4 thesis backtest. Wired live ONLY as the best-effort once-per-tick `run_macro_thesis_tick` hook in `src/main.py` (places no order — P5 executor unbuilt). Gate `config/macro_theses.yaml::sleeve.execution: shadow`; eventual venue `alpaca_options_paper` (paper). Stores are file-backed JSONL (`comms/macro/`, `runtime_logs/*_soak.jsonl`) via `src.utils.paths` — not yet DB tables. **M29** (`src/sysdyn/`): a pure system-dynamics engine (identify/structure/gas-storage seed) — shipped, import-linter-locked, tested, NOT yet tick-wired (P1a pure package; wiring is backtest-gated P3+). Grading workflows: `m28-value-grade` + `m31/m32/m33/m34-*-grade` + `macro-valuation-{snapshot,backfill}`. Full design: `ROADMAP_MACRO.md`. | `src/units/strategies/macro_thesis/*` (NEW), `src/sysdyn/*` (NEW), `config/macro_{events,theses,valuation}.yaml` (NEW), `scripts/macro/*`, `.importlinter` (macro_thesis/sysdyn purity contracts), `.github/workflows/{m28-value-grade,m3{1,2,3,4}-*-grade,macro-valuation-*}.yml`, `ROADMAP_MACRO.md` | **None on live-VM order path** — observe-only tick hook + off-VM research/grading only. No web-API router (so no `CLAUDE.md` API-table entry). Rollback: the tick hook is best-effort try/except; `sleeve.execution` flip is Tier-3 + P4-gated. |
| 2026-07-26 | Full-system audit 2026-07-26 — canonical-doc + observability reconciliation | **Periodic full-system audit (all 3 repos, both VMs, canonical store).** Verdict: recent infra (M28/M27/M26/M0a-b) is **compliant** (zero Prime-Directive/Tier-3/isolation violations; 19/19 CI guards + ruff green; layer-guard enforcing). Doc/observability fixes landed this pass: (A2) this M28/M29 + M27 change-log documentation + Repo-Map row; (A4) `alpaca_live` corrected to its shelved `dry_run` state (was overstated as live real-money); (A1) strategy count 48 → 54; (A3) `layer-guard`/import-linter + `REQUIRED_CONTEXTS` added to the CI-guard list; (WS-B) diag `_CANONICAL_UNITS` gained the 3 data-ingest timers (`ict-exchange-{fills,funding}-pull`, `ict-mes-ibkr-pull`) + dropped retired `ict-heartbeat.service`; `replay-pregate-nightly` ref → `main`; 2 missing labels bootstrapped. Surfaced to operator (not self-fixed): `ict-mes-ibkr-pull.service` in `failed` state; 352 Claude-pings stranded in the pre-migration repo-path inbox; recurring bybit_2 smoke-test `place_order` NoneType failures. Program doc + per-file coverage: `docs/audits/full-system-audit-2026-07-26.md`. | `docs/ARCHITECTURE-CANONICAL.md`, `src/web/api/routers/diag.py`, `.github/workflows/{replay-pregate-nightly,bootstrap-labels}.yml`, `docs/audits/full-system-audit-2026-07-26.md`, `ROADMAP.md`, the three review backlogs | Tier-1 (docs + diag read-surface observability + CI workflow config). `diag.py` change ships via `ict-git-sync`; makes the 3 ingest timers queryable on `/api/diag/services`. No order-path/config-gate change. |
| 2026-08-12 | M20 exit-loop decouple — **exit evaluation leaves the trader tick for its own daemon thread** | **The trader is no longer a single-threaded loop.** Exit evaluation (`monitor.strategy_monitor_loop`) moved off `src/main.py`'s tick onto a **daemon loop** (`_exit_loop`), period-targeting at `EXIT_LOOP_INTERVAL_SECONDS` (default 30 s). ⚠️ **THE GO-LIVE HEADROOM NO LONGER HOLDS — DO NOT QUOTE IT** (corrected 2026-08-16, `BL-20260816-EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT`). This row read "re-evaluated every **30.2 s observed** … worst observed interval **34.1 s**, meeting the operator's standing 60 s exit-evaluation ask with 43.1% headroom"; that was the go-live measurement and is now **stale in the dangerous direction**. Measured 2026-08-16T05:39Z over **n=694** passes on one process: `monitor.strategy_monitor_loop` **28852.0 ms mean / 58940.8 ms max**. Since the interval is `max(interval, pass)`, the worst observed inter-evaluation interval is **58.9 s against the 60 s ask — 1.1 s of margin, not 43%**. ⚠️ **The "trend across every reading" this row briefly asserted does NOT hold** (corrected 2026-08-16, same day, against a second process): that series **mixed means with maxima**, and **compared maxima across different n** where a maximum grows with sample size from a fixed distribution anyway. Separated, the means are 22.4 s (n=55) → 30.2 s → 26.6 s (n=219) → 28.9 s (n=694) → **21.6 s (n=32)** — flat and non-monotonic, newest lowest; the maxima are three points (34.1 s, 58.9 s, **41.5 s at n=38**). The 58940.8 ms observation stands and is the reason this matters; the supported reading is a **fat tail finally sampled**, not degradation. **Grade this on the MAX, never the mean** — the earlier row `BL-20260814-EXIT-PASS-SLOWER-THAN-M20` was closed on a mean that looked settled while the max kept growing, and until 2026-08-16 `exit_loop_health` could not catch it either (it graded only `fresh`/`stale` at 180 s, so a 59 s pass read healthy). **It now grades the requirement explicitly**, as a SECOND threshold beside liveness: `EXIT_EVAL_MAX_INTERVAL_SECONDS` (default 60 s) against a MEASURED completion-to-completion `max_interval_ms`, published as a four-state `requirement_state` (`within`/`breached`/`not_measured`/`unknown`, `collapsed-state-guard`-registered) that can read `breached` while liveness reads `fresh`, with a once-per-process alert. The pass is **fetch-bound**, so it shares a root with the tick regression and any TTL/fetch change reaches exit decisions. **Only the EXIT half moved**; the 13 reconcilers/sweeps stay inline on the tick, so `order_monitor` remains a tick hook and the exit-coverage invariant (2026-06-15 row) is unchanged in substance — what changed is the THREAD it is enforced from. **Two structural consequences a future session must not re-derive:** (1) **exit-loop liveness is now its own surface.** The external liveness watchdog measures the tick's heartbeat, and its coverage of exit evaluation was never a probe — it was the fact that exit evaluation ran INLINE on that tick. A stalled exit loop is therefore a condition nothing else observes, so `src/runtime/exit_loop_health.py` writes a four-state liveness record (`unknown`/`never_ran`/`fresh`/`stale`, read via `/api/diag/log_file?name=exit_loop_health`); `never_ran` is emphatically NOT healthy and is also what the rollback flag produces. (2) **per-hook cost accounting is now thread-aware** — `tick_cost` segregates off-thread hooks into a separate `offloop_hooks` table by **thread identity**, excluded from `attributed_pct`, because dividing another loop's duration by the main tick's clock is meaningless. Four coupled prerequisites shipped in the same PR because the split is unsafe without them: the active-close marker was per-TICK and crossed both halves at four sites (now lock-guarded + time-bounded); a `tick_cost` accumulator lock (**defensive only — the race was not reproduced**); the thread-identity segregation above; and an IB **`RLock`** (12 of 17 public `IBClient` methods call `connect()`; a plain `Lock` demonstrably hangs the suite). **Rollback is one env flip, no redeploy:** `EXIT_LOOP_DECOUPLE_DISABLED=1` → no thread, the tick calls `run_monitor_tick`, byte-for-byte the prior behaviour. Live-verified on `git_sha 64ee435f` (`offloop_hooks` populated at n=55 with the child absent from the main table — the direct proof segregation works rather than silently no-op'ing; `attributed_pct` 98.4). ⚠️ The design's own margin figures were derived from n=7/n=18 and are **corrected** at n=172 — whole-monitor-on-a-thread peaks at **61.82 s and FAILS the 60 s ask**, the exit half alone cleared by **41.8%** (not 53%) *at that sample — superseded, see the ⚠️ correction above: at n=694 it clears by 1.8%*, and the live loop measured 34.13 s, within 2.2% of the correction. Record: `docs/sprint-logs/S-M20-EXIT-LOOP-DECOUPLE-2026-08-12.md` + `docs/research/M20-exit-monitor-decouple-evidence-2026-08-10.md` §§ 4e–4f. **This does NOT make the tick faster** — it is still 96.5 s mean / 115.7 s max with `pipeline.signal_build` at 53.7% (`BL-20260810-TICK-CHAIN-260S-PER-TICK`, open). | `src/main.py`, `src/runtime/order_monitor.py`, `src/runtime/tick_cost.py`, `src/units/accounts/ib_client.py`, new `src/runtime/exit_loop_health.py`, `src/web/api/routers/diag.py`, `CLAUDE.md`, `ROADMAP.md` | **Tier-2** (live-service change on the money box — merging IS deploying via `ict-git-sync`), operator-approved in-conversation 2026-08-12. No `config/*.yaml`, no strategy params, no risk caps, no account-mode change; no new order path — the same `run_exit_evaluation_tick` runs, on a different thread. |
| 2026-08-27 | R3 offload transport — **a model trained off-box now has a durable path home, and it is not armed** | `.github/workflows/trainer-offload-train.yml` gains an **opt-in `publish` job** (default `false`, so the existing path is byte-for-byte unchanged) that commits the trained run into a NEW tracked artifact, `ml/offload-inbox/` (`index.jsonl` + `<model_id>/<run_id>/{model_state,metrics,manifest}.json`). The trainer's existing 15-min `trainer_git_sync.sh` force-checkout delivers it; a NEW trainer unit `ict-offload-drain.{service,timer}` consumes it. ⚠️ **The drain carries NO registry entry across machines — it RE-REGISTERS on the trainer**, so `model_state_path.resolve()` is trainer-local by construction. That is the fix for Blocker 1 (`ml/experiments/runner.py:422` records the training machine's absolute path; a copied entry would read `/home/runner/work/...`). It is the same shape `scripts/ml/gpu_burst/ingest_bundle.py` already uses for a rented GPU pod. ⚠️ **UNARMED ON BOTH AXES**: the timer is enabled by no installer or cloud-init, and `OFFLOAD_DRAIN_APPLY` must be exactly `1`. Once armed it registers under a NAMESPACED id (`<model_id>-offload`) at a FORCED `candidate` stage — which `ml.shadow.factory` refuses — because a fresh registration defaults to `shadow`, which auto-wires onto every strategy's predictor list. It refuses to refresh an id an operator has advanced past candidate. **No stage-ladder, registry-contract, mirror or order-path change.** | `.github/workflows/trainer-offload-train.yml`, `scripts/ml/offload/{emit_bundle,drain_inbox}.py`, `scripts/ops/run_offload_drain.sh`, `deploy/trainer/ict-offload-drain.{service,timer}`, `ml/offload-inbox/`, `scripts/ci/run_guards.py`, `tests/scripts/test_offload_transport.py` | **None until deliberately armed.** Publishing is per-run opt-in; the drain writes nothing by default. ⚠️ The moment a runner-trained model first joins the live shadow fleet is **TIER-2** and needs an explicit operator OK — it is not part of this plumbing. ⚠️ Nothing here has run outside a sandbox: see `OI-20260827-OFFLOAD-TRANSPORT-HAS-NEVER-CARRIED-A-MODEL`. The first real dispatch on `main` is EXPECTED to report `pending_merge` (exit 1) because the push retargets off a protected branch. |

---
| 2026-09-02 | — | **Decision prompts move to the dedicated Claude bot, and that bot becomes POLLED.** New `ict-claude-decision-bot.service` + `src/bot/claude_decision_bot.py` poll `TELEGRAM_CLAUDE_BOT_SECRET` and dispatch `wdec` taps into the SAME `telegram_decisions.handle_decision_callback` (imported, not forked; the API route stays the one owner of every refusal). New cross-process contract `src/runtime/telegram_poll_registry.py`: a heartbeat-backed, three-state answer to *is a tap on this bot received?* — `polled_with_handler` / `token_only_not_polled` (we looked) / `unknown` (we could not look), registered with `collapsed-state-guard`. `answerable_route()` prefers the Claude bot ONLY on positive evidence and otherwise falls back to the trader bot loudly, so the wrong chat is never traded for an outage. **Delivery and answerability are different properties**: a token suffices to SEND, but an inline button is inert unless some process polls that bot — a tap then yields a `callback_query` nobody collects, with no error on any surface. | `src/bot/claude_decision_bot.py`, `deploy/ict-claude-decision-bot.service`, `src/runtime/telegram_poll_registry.py`, `src/runtime/telegram_decisions.py`, `src/bot/telegram_query_bot.py`, `src/web/api/routers/diag.py`, `scripts/install_systemd_units.sh` | **A new unit must be installed + enabled on the live VM, and `TELEGRAM_CLAUDE_BOT_SECRET` must be in its `.env`** — an operator action. Until then decision prompts keep arriving on the trader bot (reported, not silent). UNPROVEN on the fleet: no tap has been made on ClaudeBot. |

## Known gaps

Deliberate omissions and queued work. An entry here is a
**contract** between the team and future maintainers: the
architecture doc does not yet reflect this state, by design,
because the work is in flight or out of scope for the current
milestone.

| Gap | Why deferred | Tracking |
|---|---|---|
| **Orphaned mode-override dead code in `src/`** — **RESOLVED (verified 2026-07-09, full-system audit S-AUDIT-E).** The Prime-Directive-violating vectors are gone (breaker auto-flip removed → the rejection path is alert-only; Telegram `/accounts dry\|live` removed in #1933), AND the shim code itself is now **deleted**: `_DRY_RUN_OVERRIDES` + `set_account_dry_run()` (+ the `Coordinator` wrapper) survive only as a removal docstring in `src/units/accounts/__init__.py`, with a regression test asserting their absence (`tests/test_exchange_rejection_circuit_breaker.py` — `assert not hasattr(...)`). The earlier "deletion never landed" note was itself stale. **The `account_state.yaml` dry-only override read (`src/runtime/orders.py::account_state_dry_run`, consumed in `coordinator.py`) is NOT dead code** — it is a live, intentional, Prime-Directive-compliant belt-and-suspenders gate that can only *increase* dryness (never forces a dry account live), fail-open on a missing file. It stays. | Closed — no action (the "two execution gates" wording should acknowledge `account_state.yaml` as a dry-only override; tracked in the audit findings doc as E1-F3). |
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
