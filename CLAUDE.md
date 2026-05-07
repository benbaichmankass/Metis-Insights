# ICT Trading Bot — CLAUDE.md

## Project Overview
Automated ICT (Inner Circle Trader) futures trading bot running on a VPS.
Exposes a FastAPI REST API on port 8001 consumed by the Vercel React dashboard
(`ict-trader-dashboard`).

## Architecture
```
VPS (systemd)
  ├── ict-bot.service         ─── trading pipeline (pipeline.py)
  └── ict-web-api.service     ─── FastAPI :8001
                                   ├── /api/bot/stats    ← Vercel dashboard
                                   ├── /api/bot/logs     ← Vercel dashboard
                                   ├── /api/bot/positions← Vercel dashboard
                                   ├── /api/bot/signals  ← Vercel dashboard
                                   ├── /api/pnl
                                   ├── /api/status
                                   ├── /api/diag/*       ← PM-side read-only (S-051)
                                   └── /api/health
```

## Key Directories
```
src/
  runtime/
    pipeline.py         — main trading loop
    health.py           — 7-point health check suite
    outcomes.py         — structured logging helpers
  web/
    api/
      main.py           — FastAPI app, CORS middleware, router mounts
      auth.py           — session/token auth helpers
      routers/
        dashboard.py    — /api/bot/* endpoints (S-014)
        diag.py         — /api/diag/* endpoints (S-051, token-gated read)
        pnl.py          — /api/pnl
        pnl_history.py  — /api/pnl/history
        status.py       — /api/status
    runtime_status.py   — writes runtime_logs/status.json (DO NOT DELETE—imported by pipeline)
runtime_logs/
  signal_audit.jsonl    — structured pipeline audit log (primary log source for dashboard)
  heartbeat.txt         — mtime used to detect if bot is alive
trade_journal.db        — SQLite: trades, order_packages
```

## Dashboard REST API (S-014)

All endpoints are unauthenticated GET routes in `src/web/api/routers/dashboard.py`.

| Endpoint | Returns | Data source |
|----------|---------|-------------|
| `GET /api/bot/stats` | `BotStats` JSON | `trade_journal.db` + `psutil` + `heartbeat.txt` |
| `GET /api/bot/logs` | `LogEntry[]` | `runtime_logs/signal_audit.jsonl`, fallback `bot.log` |
| `GET /api/bot/positions` | open positions | `trade_journal.db` WHERE status='open' |
| `GET /api/bot/signals` | recent ICT detections | `runtime_logs/signal_audit.jsonl` filtered to buy/sell |

### `BotStats` shape
```json
{
  "pnl24h": 124.50,
  "totalPnL": 3200.00,
  "openTrades": 2,
  "winRate": 68.5,
  "status": "running",
  "datasource": "live",
  "vmHealth": { "cpu": 32.1, "memory": 48.5, "disk": 21.0 }
}
```

## CORS
CORS is configured in `src/web/api/main.py`. Allowed origins:
- `http://localhost:5173` (Vite dev)
- `http://localhost:3000`
- Value of `DASHBOARD_ORIGIN` env var (set to Vercel URL on the VPS)

## Environment Variables
| Variable | Purpose |
|----------|---------|
| `DASHBOARD_ORIGIN` | Vercel app URL — added to CORS allow-list |
| `DASHBOARD_API_TOKEN` | Optional bearer token for auth routes |
| `TRADE_JOURNAL_DB` | Override default `trade_journal.db` path |
| `DIAG_READ_TOKEN` | Bearer for `/api/diag/*` (read-only). Unset → endpoints return 503 |

## Diagnostic API (S-051)

Token-gated read-only surface for PM-side Claude / operator scripts. All
endpoints return 503 if `DIAG_READ_TOKEN` is unset, 401 on bad bearer.

| Endpoint | Returns |
|----------|---------|
| `GET /api/diag/snapshot?limit=N` | bundle: heartbeat, status, audit tail, order_packages, trades, vm_health, service states |
| `GET /api/diag/audit?limit=N` | tail of `runtime_logs/signal_audit.jsonl` |
| `GET /api/diag/journal?table={order_packages\|trades}&limit=N` | read-only SELECT |
| `GET /api/diag/status` | heartbeat + status.json + vm_health |
| `GET /api/diag/services` | `systemctl is-active` per allowlisted unit |
| `GET /api/diag/journalctl?unit=<name>&lines=N` | systemd journal tail |
| `GET /api/diag/log_file?name={audit\|status\|heartbeat\|bot_log}&lines=N` | log file tail |

See `docs/claude/vm-operator-mode.md` § 9 for the trust contract.

## Running Locally
```bash
pip install -r requirements.txt
uvicorn src.web.api.main:app --port 8001 --reload
```

## Important Notes
- `src/web/runtime_status.py` is imported by `src/runtime/pipeline.py` — do NOT delete it
- `heartbeat.txt` mtime determines bot status: <2 min → running, <10 min → paused, else → stopped
- The old HTMX UI (`web/static/`, `web/templates/`, `src/web/api/routers/ui.py`) has been removed
- The old Streamlit UIs (`src/web/backtest_ui.py`, `src/web/config_ui.py`) have been removed
