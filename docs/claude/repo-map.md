# Repo map

This project is a Python ICT trading bot with ML/backtesting/live execution, Telegram control, exchange integrations, and cloud deployment.

## S-008 architecture (current): 9-unit Coordinator pattern

All cross-unit data flows through `src/core/coordinator.py` (the TRANSLATOR).
No unit calls another unit directly.

| Unit | Location | Purpose |
|------|----------|---------|
| 1. Strategies | `src/units/strategies/` | Generate `OrderPackage` objects |
| 2. Accounts | `src/units/accounts/` | Risk-size + execute orders. `risk.py` (base `RiskManager`), `prop_risk.py` (mission-aware `PropRiskManager` for prop accounts — see `docs/claude/prop-account-state.md`), `execute.py` (canonical live entry point), `integrator.py` (exchange API stubs incl. Velotrade). |
| 3. Dashboards | `src/units/dashboards/` | Stats + alerts ring buffer |
| 4. Return Commands | `src/core/coordinator.py` | halt/resume via `_PAUSED_ACCOUNTS` |
| 5. Telegram Bot | `src/bot/telegram_query_bot.py` | UI consumer of Coordinator |
| 6. App | (stub) | Extended UI with config |
| 7. Trading School | `src/units/trading_school/` | Strategy metric validation |
| 8. DB | `src/units/db/` (canonical) — was `src/data_layer/` pre-S-035 | Unified storage |
| 9. UI | `src/units/ui/` (canonical) — was `src/ui/` pre-S-035 | Single facade between any UI surface (bot, webapp) and the data/runtime/account units |
| 10. Workflows | `docs/workflows/` | Per-unit operating procedures |

**Config:** `config/units.yaml` — all 9 units declared here. Adding a strategy = 1 line.

**Full architecture diagram:** `docs/architecture.md`

**Per-unit workflows:** `docs/workflows/`

## Common areas

- `src/`: application code.
- `src/core/coordinator.py`: **TRANSLATOR** — all cross-unit routing. Read this first for any architectural question.
- `src/units/`: canonical home for every unit per CLAUDE.md § Architecture rules § 1 — strategies, accounts, dashboards, trading_school, db (S-035), ui (S-035).
- `src/units/db/`: SQLite `Database` class (`trade_journal.db` bootstrap + migrations + the three logs: trades, order_packages, signals). **Pre-S-035** lived at `src/data_layer/`; that path remains as a back-compat shim.
- `src/units/ui/`: UI processor + data_loaders. The single facade between any UI surface and the units. **Pre-S-035** lived at `src/ui/`; that path remains as a back-compat shim. `src/bot/data_loaders.py` is also a shim that aliases through to `src/units/ui/data_loaders.py` (S-032 + S-035).
- `src/bot/`: Telegram bot and command handlers — thin shells that call `src.units.ui.processor` helpers. Only `src/bot/data_loaders.py` is an alias shim (no business logic).
- `src/core/`: trading loop and core strategy flow.
- `src/runtime/`: runtime config/validation/pipeline pieces. **Stays under `src/runtime/`** (not moved to `src/units/runtime/`) — runtime is the orchestration layer that drives units, not a unit itself. **Post-S-022:** `outcomes.py` is the central error reporter (every silent-failure replacement uses `report()`); `health.py`, `heartbeat.py`, `hourly_report.py`, `api_reporting.py`, `market_data.py` (S-033), `liveness_watchdog.py` (S-029), `order_monitor.py` (S-030) are the supporting surfaces.
- `scripts/`: operational scripts. `scripts/init_db.py` bootstraps `trade_journal.db`. `scripts/check_heartbeat.py` (S-022 PR5) is the standalone watchdog.
- `tests/`: pytest suite. `tests/test_s008_*.py` + `tests/test_coordinator_flow.py` cover the 9-unit layer (178 tests).
- `config/`: `units.yaml` (9-unit declarations), `strategies.yaml`, `accounts.yaml` (per-account credentials → `api_key_env` contract), `master-secrets.template.yaml`.
- `data/`, `ml/data/`, `ml/models/`: data/model artifacts. Prefer remote storage for large files.
- `docs/`: human-readable project docs.
- `docs/operator/`: operator-facing setup walkthroughs (post-S-023). `setup-api-keys.md` for the manual sops flow; `colab-key-rotation.md` for the one-click Colab notebook flow (preferred path).
- `notebooks/operator/rotate_api_keys.ipynb`: Colab key-rotation notebook. Reads from Colab Secrets + Drive (`My Drive/ICT_Bot_Secrets/`), writes `.env` + `.env.live` to the VM, restarts trader + telegram bot. The operational path for rotating keys without SSH.

## systemd units (post-operator-onboarding)

The bot runs as multiple systemd units on the VM. Anything that reads
`os.environ` only does so at process start, so rotating env vars
requires restarting **every** unit that reads them.

| Unit | Reads | Surface |
|------|-------|---------|
| `ict-trader-live.service` | `.env` (systemd) + `.env.live` (via `pipeline.py::load_dotenv`) | trading loop, signal generation |
| `ict-telegram-bot.service` | `.env` | every `cmd_*` handler — including `/accounts_status` |
| `ict-web-api.service` | `/etc/ict-trader/web-api.env` | dashboard `/api/*` endpoints |
| `ict-heartbeat.service` | `.env.live` | daily heartbeat ping |
| `ict-liveness-watchdog.service` (+ `.timer`) | `.env` | per-minute dead-man switch (`scripts/check_heartbeat.py`); Telegrams + autoheals trader on stall. Runbook: `docs/runbooks/liveness-watchdog.md` |
| `ict-git-sync.timer` + `.service` | n/a | pulls main every 5 min, runs `deploy_pull_restart.sh` |

The Colab key-rotation notebook restarts trader + telegram bot.
If you add a new env-reading process, also update
`notebooks/operator/rotate_api_keys.ipynb::SERVICES_TO_RESTART`.

## Operator-facing surfaces (Telegram + Colab)

The operator runs the bot from Telegram, with Colab as the one-click
key-rotation tool. They never SSH to the VM after one-time setup.

| Surface | Purpose |
|---|---|
| `/start` / `/help` | Commands index |
| `/status` | Halt-flag + per-account daily PnL |
| `/accounts_status` | Per-account API health with specific failure reasons (S-023 PR2). Uses `parse_mode="HTML"` because legacy Markdown ate underscores in env-var names |
| `/balance` | Per-account live balance |
| `/halt` / `/resume` | Kill-switch toggle |
| `/smoke_test` | One-shot too-small order to each account, proves API integration |
| `/set_keys` | Returns the Colab open-in-Colab URL for the rotation notebook |
| `/alerts` | Last 200 alerts from the dashboards queue |
| Hourly report | Auto-fires at the top of each UTC hour with structured trades + accounts + strategies + health (S-022 PR2) |

## Rule

Before changing architecture, update this map if the structure changed.
