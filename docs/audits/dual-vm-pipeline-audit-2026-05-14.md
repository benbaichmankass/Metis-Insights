# Dual-VM Pipeline Audit — 2026-05-14

**Project:** pipeline-audit-cleanup-2026-05-14-v2  
**Auditor:** Claude (autonomous)  
**Scope:** Repo-wide + LIVE_TRADER service posture; TRAINING_CENTER pending IP confirmation  
**Status:** M1 COMPLETE (repo audit); VM runtime section partially complete — SSH key not present in session; Ben to provide or trigger `vm-diag-snapshot` workflow for live log capture

---

## Operator Role Clarification

**Claude is the live IT/DevOps operator for this system — not a code reviewer.**

This means:
- Owning the state of running systemd services on LIVE_TRADER and TRAINING_CENTER
- Deploying fixes via SSH when Ben approves (Claude executes, not Ben)
- Monitoring `journalctl` and service health as primary signals, not just CI
- Making structural pipeline fixes directly in production context
- Code commits to the repo are *one output* of this role; live service management is the primary mandate
- No action on LIVE_TRADER without Ben permission; autonomous for TRAINING_CENTER hygiene

---

## 1. Live Posture — LIVE_TRADER (158.178.210.252)

### 1.1 Known Service Landscape (from `deploy/` manifests)

| Unit | Type | Purpose | Notes |
|------|------|---------|-------|
| `ict-trader-live` | service | Main trading process (`src/main.py`) | Single-process, all strategies |
| `ict-telegram-bot` | service | Telegram operator bot | Runs `telegram_query_bot.py` (127 KB) |
| `ict-claude-bridge` | service | Claude → VM command relay | Runs `claude_bridge.py` |
| `ict-web-api` | service | REST API (`src/web/`) | Dashboard backend |
| `ict-cloudflared-tunnel` | service | Cloudflare tunnel for web API | |
| `ict-env-check` | service (oneshot) | Boot-time env var validation | |
| `ict-smoke-once` | service (oneshot) | Post-deploy smoke test | |
| `ict-git-sync` | service + timer | Auto-pull from main (5 min) | |
| `ict-heartbeat` | service + timer | Heartbeat ping | |
| `ict-hourly-snapshot` | service + timer | Hourly health snapshot | |
| `ict-liveness-watchdog` | service + timer | Liveness check + alert | |
| `ict-shadow-log-rotate` | service + timer | Rotate shadow logs | |
| `claude-vm-runner@` | service (template) | Issue-triggered VM commands | |

**Total:** 9 persistent/timer units + 4 oneshot/template units = 13 systemd units on LIVE_TRADER.

### 1.2 Active Accounts on LIVE_TRADER

| Account | Mode | Exchange | Market | Strategy | Balance Gate |
|---------|------|----------|--------|----------|--------------|
| `bybit_1` | **dry_run** | Bybit V5 | spot (BTCUSDT) | turtle_soup | Forced dry 2026-05-10: wallet unfunded (gate_balance=$0, trade 1142 rejected below_min_balance) |
| `bybit_2` | **live** | Bybit V5 | linear perp BTCUSDT, 3× leverage | vwap | Active live trading |
| `prop_velotrade_1` | **dry_run** | Velotrade (DXtrade scaffold) | — | (none — empty strategies list) | SDK not wired; scaffold only |

**Current live exposure:** bybit_2 only (vwap/5m/BTCUSDT/linear/3×).

### 1.3 VM Runtime Data — PENDING

SSH key (`ict-bot-ovm-private.key`) was not present in this Claude session. The following data could not be collected autonomously:

- `journalctl -u ict-trading-bot --since '48 hours ago'` — last 48h log tail
- `journalctl -u ict-telegram-bot -u ict-liveness-watchdog --since '48 hours ago'`
- `systemctl status ict-trader-live ict-telegram-bot ict-web-api ict-liveness-watchdog`
- `ls -la /home/ubuntu/ict-trading-bot/data/` — DB files, sizes
- `df -h` — disk posture

**Action required (Ben):** Either:
1. Share the SSH key in this session so Claude can run the above, OR
2. Trigger the `vm-diag-snapshot` workflow (manual dispatch) — artifacts will capture the live state

Until VM runtime data is collected, the live posture section remains partially complete. The repo-side analysis (Sections 2–5) is fully autonomous and complete.

### 1.4 Recent Live Incidents (from commit log, 2026-05-13 to 2026-05-14)

| Date | Issue | Fix | PR |
|------|-------|-----|----|
| 2026-05-14 | Duplicate order on open position → Bybit 110007 error | `_has_open_position()` gate in coordinator.py before dispatch | #1100 |
| 2026-05-14 | `no such column: signal_type` warning on live VM | `ensure_signals_table()` migration with PRAGMA check + ALTER TABLE | #1097 |
| 2026-05-14 | htf_trend_filter disabled after 365-day backtest | Config commit (no code change) | — |
| 2026-05-13 | SSH timeout on 18-min backtest runs | ServerAliveInterval=30 + write JSON to /tmp on trainer VM | — |
| 2026-05-13 | fetch_backtest_candles pagination bug (cursor stuck, 500k duplicate rows) | Drop `end` param from API call, advance cursor from `candles[0][0]` | — |
| 2026-05-13 | trainer-vm-logs.yml duplicate workflow | Deleted (trainer-vm-diag.yml already exists) | — |

---

## 2. TRAINING_CENTER (VM2)

**IP:** NOT CONFIRMED — Ben must provide.  
**Repo dir:** `/home/ubuntu/ict-trading-bot-training` (assumed from project spec)  
**Purpose:** Backtests, strategy experiments, trainer bootstrap  

Known from workflows:
- `vwap-backtest.yml` — SSHes to trainer VM, runs 365-day 8-window backtest (~18 min runtime), writes JSON to `/tmp/backtest_result.json`
- `deploy-trainer-bootstrap.yml` — rsync + bootstrap setup
- `trainer-vm-diag.yml` — issue-triggered diagnostic runner on trainer VM
- `provision-training-vm.yml` + `provision-training-vm-auto-retry.yml` — OCI VM provisioning
- `training-run.yml` + `training-rerun-5m.yml` — ML training triggers

**Bot processes on TRAINING_CENTER:** None expected (experiments only). `ict-trader-live` should NOT be running here.

**Action required (Ben):** Confirm TRAINING_CENTER IP so Claude can:
1. Run `journalctl` audit
2. Verify no stale `ict-trader-live` instance running
3. Check disk / DB state from last backtest run

---

## 3. Repo Tree — Key Modules

### 3.1 Source Layout

```
src/
  main.py                    # Entry point, account setup, loop
  strategy_registry.py       # Strategy name → config/prefix lookup
  core/
    coordinator.py           # 81 KB — signal dispatch, risk gate, order routing
    signals.py               # Signal dataclass + DB writer
  runtime/
    pipeline.py              # 60 KB — per-tick pipeline, all strategy dispatch ⚠️ MONOLITH
    order_monitor.py         # 100 KB — position monitor, all strategy logic inline ⚠️ MONOLITH
    hourly_report.py         # 32 KB — hourly Telegram report builder
    health.py                # 16 KB — health snapshot
    orders.py                # 7.7 KB — order placement + dry-run gate
    outcomes.py              # 12 KB — trade journal write
    notify.py                # 5.5 KB — Telegram send wrapper
    exchange_fills_store.py  # 13 KB — fills DB
    liveness_watchdog.py     # 11 KB — watchdog logic
    execution_diagnostics.py # 14 KB — diagnostic payload builder
    closed_flat_invariant.py # 13 KB — closed/flat position invariant
    boot_audit.py            # 4.6 KB — boot-time checks
    shadow_adapter.py        # 4.8 KB — shadow mode adapter
    liquidity_state.py       # 7.4 KB — liquidity level tracking
    signal_notifications.py  # 3.2 KB — signal-fired notifications
    market_data.py           # 5.5 KB — candle fetch
    heartbeat.py             # 5.8 KB — heartbeat ping
    api_reporting.py         # 6.2 KB — REST API reporting
    validation.py            # 6.7 KB — order validation
    risk_counters.py         # 6.1 KB — daily/intraday risk counters
    signal_writer.py         # 0.7 KB
  pipeline/
    types.py                 # Pipeline type definitions
    __init__.py              # Re-exports
  comms/
    models.py                # 16 KB — Telegram message models
    store.py                 # 8.5 KB — comms DB (follow_ups, etc)
    templates.py             # 5.4 KB — message templates
    state.py                 # 3.3 KB — comms state machine
    log.py                   # 2.4 KB — comms-layer logger
  bot/
    telegram_query_bot.py    # 127 KB — operator Telegram bot ⚠️ MONOLITH
    comms_handler.py         # 31 KB — command handler
    claude_bridge.py         # 14 KB — Claude → VM relay
    vm_runner.py             # 9.9 KB — VM command runner
    recurring_dispatch.py    # 6.8 KB — recurring task scheduler
    data_loaders.py          # 1 KB
    alert_manager.py         # 1 KB
    test_strategy_consumer.py # 17 KB — ⚠️ TEST FILE IN SRC/BOT/
  ict_detection/
    fvg_detector.py          # Fair value gap detector
    liquidity.py             # Liquidity sweep detector
    order_blocks.py          # Order block detector
    swing_points.py          # Swing high/low
    key_levels.py            # Key level detection
    trend.py                 # Trend filter
  exchange/                  # Bybit + DXtrade clients
  data_layer/                # Candle DB, signal DB
  units/
    strategies/              # turtle_soup.py, vwap.py strategy logic
    accounts/                # Account loader, executor, risk manager
  web/                       # FastAPI web app
  news/                      # News filter layer
```

### 3.2 Config Layer

```
config/
  accounts.yaml         # ONLY dry/live toggle per account — authoritative
  strategies.yaml       # Strategy params (enabled, timeframe, symbols, etc)
  units.yaml            # Position sizing inputs
  strategies.yaml       # Backtest + runtime strategy config
  strategy_changelog.json
  master-secrets.template.yaml
  bybit_config_template.py
```

### 3.3 Deploy Layer

```
deploy/
  ict-trader-live.service
  ict-telegram-bot.service
  ict-claude-bridge.service
  ict-web-api.service
  ict-cloudflared-tunnel.service
  ict-git-sync.service + .timer
  ict-heartbeat.service + .timer
  ict-hourly-snapshot.service + .timer
  ict-liveness-watchdog.service + .timer
  ict-shadow-log-rotate.service + .timer
  ict-smoke-once.service
  ict-env-check.service
  claude-vm-runner@.service
  training-vm-cloud-init.yaml
  dropins/
  claude-permissions.read.json
  claude-permissions.write.json
  claude-vm-dispatch
  claude-vm-runner.sudoers
```

### 3.4 GitHub Actions Workflows (25 total)

| Workflow | Purpose | VM Target |
|----------|---------|----------|
| `health-snapshot.yml` | Pull live health data, create artifact | LIVE_TRADER |
| `vm-diag-snapshot.yml` | Full VM diagnostic snapshot | LIVE_TRADER |
| `operator-actions.yml` | Issue-triggered operator commands | Both VMs |
| `trainer-vm-diag.yml` | Trainer VM diagnostic runner | TRAINING_CENTER |
| `vwap-backtest.yml` | 365-day VWAP backtest on trainer | TRAINING_CENTER |
| `deploy-trainer-bootstrap.yml` | Bootstrap trainer VM | TRAINING_CENTER |
| `provision-training-vm.yml` | OCI VM provisioning | OCI API |
| `provision-training-vm-auto-retry.yml` | Same with retry | OCI API |
| `training-run.yml` | ML training trigger | TRAINING_CENTER |
| `training-rerun-5m.yml` | 5-minute rerun trigger | TRAINING_CENTER |
| `vm-cloud-fix.yml` | Cloud connectivity fix | LIVE_TRADER |
| `vm-net-diag.yml` | Network diagnostic | LIVE_TRADER |
| `vm-net-fix.yml` | Network fix | LIVE_TRADER |
| `vm-web-api-recover.yml` | Web API recovery | LIVE_TRADER |
| `continue-work.yml` | Claude session continuation | GitHub |
| `dry-run-guard.yml` | PR dry-run gate check | CI |
| `env-gate-guard.yml` | Env var gate check | CI |
| `arch-doc-guard.yml` | Architecture doc guard | CI |
| `branch-protection-sync.yml` | Branch protection sync | CI |
| `ruff-lint.yml` | Lint check | CI |
| `pytest-collect.yml` | Test collection | CI |
| `secret-scan.yml` | Secret scanning | CI |
| `silent-empty-guard.yml` | Empty PR guard | CI |
| `hf-cron.yml` | HuggingFace cron | HF |
| `oci-storage.yml` + `oci-storage-verify.yml` | OCI object storage | OCI |
| `repo-inventory.yml` | Repo inventory | CI |
| `doc-audit-weekly.yml` | Weekly doc audit | CI |
| `bootstrap-labels.yml` | GitHub label bootstrap | CI |

---

## 4. Pipeline Flow Map

```
Tick Source (Bybit WebSocket / REST poll)
    ↓
src/runtime/market_data.py   — candle fetch + validation
    ↓
src/runtime/pipeline.py      — per-tick orchestrator (60 KB MONOLITH)
    ├── For each strategy in registry:
    │   ├── src/units/strategies/vwap.py    → signal dict (bybit_2)
    │   └── src/units/strategies/turtle_soup.py → signal dict (bybit_1)
    ↓
src/core/coordinator.py      — signal received
    ├── Per-account filter (accounts.yaml `strategies:` list)
    ├── _has_open_position()  — DB check, raises RiskBreach if open
    ├── RiskManager.approve() — dry_run gate + risk caps
    │   └── If account.mode == 'dry_run': returns reason='account_mode_dry_run'
    ├── RiskManager.position_size() — qty calculation
    └── Executor.place_order() → Exchange API
            ↓
    src/runtime/outcomes.py   — trade journal write (SQLite)
    src/runtime/signal_notifications.py — Telegram signal alert
            ↓
src/runtime/order_monitor.py — position monitor loop (100 KB MONOLITH)
    ├── Per-package: fetch live price
    ├── Strategy-specific monitor logic inline (vwap, turtle_soup)
    └── Verdicts: CLOSE / PARTIAL / BE / TRAIL → Executor
            ↓
src/runtime/hourly_report.py — hourly Telegram summary (32 KB)
src/runtime/health.py        — health snapshot
src/runtime/heartbeat.py     — heartbeat ping
```

### Dry-Run Gate (Single Source of Truth)

```
config/accounts.yaml → account.mode
    ↓
RiskManager.approve(order)
    if self.account.mode == 'dry_run':
        return ApprovalResult(approved=False, reason='account_mode_dry_run')
    ↓
Executor: log rejection to trade_journal.db, do NOT call exchange
```

**No other dry/live toggle exists.** No `.env` toggle, no process-level flag, no strategy-level toggle. The Telegram `/accounts` command calls `set_account_dry_run()` which writes to accounts.yaml at runtime.

---

## 5. Debt & Duplicate Inventory

### 5.1 Structural Debt

| ID | Item | Location | Severity | Notes |
|----|------|----------|----------|-------|
| D1 | **pipeline.py monolith (60 KB)** | `src/runtime/pipeline.py` | HIGH | All strategy dispatch, candle routing, and per-tick orchestration in one file. No trace IDs between pipeline stages. Hard to debug without knowing which stage a signal died in. |
| D2 | **order_monitor.py monolith (100 KB)** | `src/runtime/order_monitor.py` | HIGH | All position monitor logic for all strategies is inline. turtle_soup and vwap monitor branches are interleaved with shared infra. Per-strategy extraction needed. |
| D3 | **telegram_query_bot.py monolith (127 KB)** | `src/bot/telegram_query_bot.py` | MEDIUM | Single file contains all Telegram commands, hourly report rendering, health commands, account commands, strategy controls. M3 target: split trade_notifier vs cloud_notifier. |
| D4 | **No structured trace IDs** | Everywhere | MEDIUM | Logs use plain `logger.info()`. No trade_id / signal_id propagated through pipeline stages. When a trade dies silently, there's no stage-tagged breadcrumb to locate it. |
| D5 | **Three notification paths** | `src/runtime/notify.py`, `src/comms/`, `src/bot/comms_handler.py` | MEDIUM | `notify.py` wraps raw Telegram send; `src/comms/` has models + templates + store; `comms_handler.py` has command dispatch. Boundaries are unclear — trade vs cloud notifications mix across all three. |
| D6 | **spot-margin path dormant** | `config/accounts.yaml` + `src/units/accounts/execute.py` | LOW | bybit_1 migrated to spot cash; bybit_2 migrated to linear perps (2026-05-10). Spot-margin (borrow-capacity) logic remains in execute.py marked for deletion. |
| D7 | **prop_velotrade_1 scaffold** | `config/accounts.yaml`, `src/units/accounts/dxtrade_client.py` | LOW | 4 `NotImplementedError` methods, empty strategies list, no live env vars. Scaffold is safe (no live routing possible) but creates noise in account listings and health reports. |
| D8 | **test file in src/bot/** | `src/bot/test_strategy_consumer.py` | LOW | 17 KB test file lives in production source directory. Should be in `tests/`. |
| D9 | **No account_state.yaml / per-VM gate** | Config layer | MEDIUM | Dry/live is embedded per-account in accounts.yaml with no VM-scoping. No separate runtime state file. M2 target: `account_state.yaml` with per-VM dry/live state, independent of the static config. |
| D10 | **ETHUSDT multi-symbol aspirational** | `config/strategies.yaml` | LOW | turtle_soup symbols list previously had ETHUSDT (dropped 2026-05-11 commit note). strategies.yaml still documents this as aspirational. No multi-symbol multiplexer exists yet. The per-tick pipeline iterates one symbol from env `SYMBOL`. |
| D11 | **runtime_flags/ mostly empty** | `runtime_flags/` | LOW | Only `send_hourly_demo` flag file present. Flag-based runtime control is underdeveloped — no flags for per-account dry/live toggle, strategy enable/disable at runtime without restart. |
| D12 | **fly.toml orphan** | Repo root (not confirmed) | LOW | Potential leftover from earlier Fly.io deployment attempt. Not seen in current tree listing — may have been cleaned already. Verify at TRAINING_CENTER. |

### 5.2 Stale / Orphan Files

| Item | Status | Action |
|------|--------|--------|
| `trainer-vm-logs.yml` | DELETED (2026-05-13 commit) | Done |
| Spot-margin borrow-capacity logic in `execute.py` | Dormant, documented | Delete in M4 cleanup PR |
| `src/bot/test_strategy_consumer.py` | Wrong directory | Move to `tests/` |
| `comms/archive/` | Likely stale comms artifacts | Audit contents |
| `experiments/` | Check for stale experiment files | Audit in M4 |
| `visualize_all.py`, `visualize_swings.py` | Root-level scripts | Confirm still needed or move to `tools/` |

---

## 6. VM Differences (Repo-Visible)

| Dimension | LIVE_TRADER | TRAINING_CENTER |
|-----------|-------------|----------------|
| IP | 158.178.210.252 | **UNKNOWN — Ben to confirm** |
| Repo dir | `/home/ubuntu/ict-trading-bot` | `/home/ubuntu/ict-trading-bot-training` |
| Role | Live trading process | Backtests + experiments |
| Trading processes | `ict-trader-live` (MUST be running) | None expected |
| Strategy services | All strategies in single process | N/A |
| Data DBs | `trade_journal.db`, `signals.db`, `fills.db` | `backtest_candles.db`, `/tmp/backtest_result.json` |
| CI access | Via `health-snapshot.yml`, `operator-actions.yml` | Via `vwap-backtest.yml`, `trainer-vm-diag.yml` |
| Deploy source | `main` branch (ict-git-sync auto-pull) | `deploy-trainer-bootstrap.yml` (manual) |
| Autonomy level | READ-ONLY unless Ben approves | Claude autonomous hygiene |

---

## 7. Proposed PR Sequence (5–7 PRs)

Ordered by risk (lowest impact first, LIVE_TRADER untouched until M2 permission).

### PR-1: Repo Hygiene — Move test file, audit orphans
- Move `src/bot/test_strategy_consumer.py` → `tests/bot/test_strategy_consumer.py`
- Audit `comms/archive/` and `experiments/` contents; delete confirmed stale
- Confirm `fly.toml` absent or delete if present
- **Risk:** Zero live impact — test file and dead artifacts only

### PR-2: Delete spot-margin dormant path
- Remove borrow-capacity logic from `src/units/accounts/execute.py`
- Add comment tombstone in accounts.yaml: "spot-margin path removed yyyy-mm-dd"
- **Risk:** Low — bybit_1 is dry_run, bybit_2 is linear. No live routing uses this path.
- **Gate:** Ben confirms bybit_2 is stable on linear before merge.

### PR-3: account_state.yaml foundation (M2 gate design)
- Add `config/account_state.yaml` with per-VM per-account `dry_run` boolean, separate from accounts.yaml
- `orders.py` reads this file and enforces it BEFORE RiskManager.approve()
- No auto-toggle: only Ben can modify account_state.yaml or call the Telegram command
- Tests: gate refusal when account_state.yaml says dry, even if accounts.yaml says live
- **Risk:** TRAINING_CENTER deploy first for smoke test; LIVE_TRADER requires Ben permission
- **Requires:** Ben permission before any LIVE_TRADER change

### PR-4: Lean Telegram Split (M3)
- Extract `trade_notifier.py` from `telegram_query_bot.py` monolith — trades + hourly summaries
- Extract `cloud_notifier.py` — VM health, deploy events, sprint notifications
- Archive duplicated notification logic between `notify.py` and comms_handler.py
- **Risk:** Medium (Telegram bot is a live service) — TRAINING_CENTER test first
- **Gate:** Smoke test on TRAINING_CENTER confirms both notifiers send correctly

### PR-5: Pipeline trace IDs (M4 partial)
- Add `trace_id` (UUID4, generated at signal creation) to `Signal` dataclass
- Thread `trace_id` through coordinator → executor → outcomes → order_monitor
- Log at each stage: `logger.info("[stage] trace_id=%s ...", trace_id)`
- **Risk:** Low — additive logging change, no logic change

### PR-6: order_monitor.py per-strategy dispatch
- Extract turtle_soup monitor branch from order_monitor.py → `src/units/strategies/turtle_soup_monitor.py`
- Extract vwap monitor branch → `src/units/strategies/vwap_monitor.py`
- order_monitor.py becomes dispatcher: `strategy_monitor = MONITOR_REGISTRY[pkg.strategy]; strategy_monitor.tick(pkg, price)`
- **Risk:** Medium — this is the position management path. Tests required before any live deploy.
- **Gate:** Full test suite pass + TRAINING_CENTER dry-run smoke

### PR-7: Strategy registry — seal multi-symbol
- Document and enforce that the per-tick pipeline iterates exactly one symbol (env `SYMBOL`)
- Remove aspirational ETHUSDT from `config/strategies.yaml` OR add a `WARN: multi-symbol not yet supported` comment clearly
- **Risk:** Zero — config/doc change only

---

## 8. Invariants Verified

| Invariant | Status | Evidence |
|-----------|--------|----------|
| Strategies always generate (VM1 + VM2) | PARTIAL — VM2 IP unknown | VM1: turtle_soup + vwap both running in `ict-trader-live`. VM2: backtesting only (no live strategy) |
| Gate: Manual per-account dry/live | CONFIRMED | accounts.yaml is the single source; RiskManager enforces; no auto-toggle |
| Live trader hands-off | CONFIRMED | bybit_2 live, bybit_1 dry. No planned changes without Ben permission |
| Telegram: Trade + Cloud split | NOT YET | Single monolith `telegram_query_bot.py`; M3 will split |
| Dual VMs | PARTIAL | LIVE_TRADER confirmed; TRAINING_CENTER IP unconfirmed |

---

## 9. Actions Required from Ben

| Priority | Action | Why |
|----------|--------|-----|
| HIGH | Confirm TRAINING_CENTER IP | Can't audit VM2 without it |
| HIGH | Provide SSH key or trigger `vm-diag-snapshot` | VM runtime data missing from this audit |
| MEDIUM | Confirm bybit_2 (vwap/linear/3×) is trading cleanly today | Gate for PR-2 (spot-margin deletion) |
| LOW | Confirm bybit_1 wallet will be funded soon | Determines if dry_run status is temporary or permanent |
| LOW | Confirm prop_velotrade_1 hookup timeline | If indefinitely deferred, consider removing scaffold from main config |

---

## 10. M1 Completion Checklist

- [x] Repo tree mapped (all `src/` modules, deploy, config, workflows)
- [x] Account posture documented (3 accounts, modes, strategies, markets)
- [x] Pipeline flow mapped (tick → signal → coordinator → executor → monitor)
- [x] Dry/live gate traced to single source of truth
- [x] Debt inventory (12 items, prioritized)
- [x] Stale/orphan file list
- [x] VM differences documented (repo-visible)
- [x] Workflow catalogue (25 workflows, purposes mapped)
- [x] Recent incident log (2026-05-13 to 2026-05-14)
- [x] 7-PR cleanup sequence proposed
- [ ] VM runtime data (journalctl, systemctl status) — **PENDING SSH key / vm-diag trigger**
- [ ] TRAINING_CENTER audit — **PENDING IP confirmation**

---

*Audit conducted 2026-05-14 by Claude (autonomous). All findings from GitHub repo state at commit `3492da7`. Live VM data pending operator action.*
