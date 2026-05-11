# ICT Trading Bot — CLAUDE.md

> **Canonical documentation (adopted 2026-05-10 in S-CANON-1):**
> - [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) — Claude operating rules, permission tiers, workflow routing.
> - [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md) — system architecture, trade pipeline, comms pipeline, deployment flow.
> - [`ROADMAP.md`](ROADMAP.md) — current work plan and status.
> - [`docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`](docs/SPRINT-LOG-TEMPLATE-CANONICAL.md) — mandatory sprint-log format.
> - [`docs/github-actions-workflows.md`](docs/github-actions-workflows.md) — canonical GitHub Actions reference.
>
> When this file disagrees with a canonical doc, the canonical doc wins.
> This file is now scoped to the **dashboard-API quick reference** only;
> Claude operating rules and full architecture have moved to the
> canonical docs above.
>
> **Repo identity:** `benbaichmankass/ict-trading-bot`. Older
> `the-lizardking/ict-trading-bot` references in historical sprint
> summaries are preserved as record.

## Project-level skills (`/health-review`)

This repo ships a **project-level Claude Code skill** at
[`.claude/skills/health-review/SKILL.md`](.claude/skills/health-review/SKILL.md).
It is the manual replacement for the autonomous Claude routine — when
the operator types `/health-review` (or asks for "the health review"
/ "the layer-2 review"), Claude reads `artifacts/health/latest.json`
and `artifacts/health/health_snapshot.txt` from the current `main`
HEAD and emits a JSON response per
[`comms/schema/health_review_response.template.json`](comms/schema/health_review_response.template.json).

**This is NOT a code-quality audit** — do not invoke it for
codebase review, security scan, or dependency check. Use the `review`
or `security-review` skills for those instead. The
[`SessionStart` hook in `.claude/settings.json`](.claude/settings.json)
emits the same directive into every web-session's context at init so
this can't be missed.

When to invoke `/health-review`:
- A Telegram ping arrives saying *"auto-merge queued — run /health-review for the layer-2 review"*.
- A `comms/requests/REQ-*.json` file is sitting unanswered on `main`.
- The operator asks for the health review, the layer-2 review, or to
  sanity-check the live bot's runtime state.

The full review procedure (inputs, decision rubric, output schema,
"don't write files / don't ask scoping questions") lives in the skill
file. See also [`docs/runbooks/health-check.md`](docs/runbooks/health-check.md)
for the two-workflow (collect → review) design.

## Project Overview
Automated ICT (Inner Circle Trader) futures trading bot running on a VPS.
Exposes a FastAPI REST API on port 8001 consumed by the Vercel React dashboard
(`ict-trader-dashboard`).

## Architecture
```
VPS (systemd)
  ├── ict-trader-live.service ─── trading pipeline (pipeline.py via src/main.py)
  └── ict-web-api.service     ─── FastAPI :8001
                                   ├── /api/bot/stats    ← Vercel dashboard
                                   ├── /api/bot/logs     ← Vercel dashboard
                                   ├── /api/bot/positions← Vercel dashboard
                                   ├── /api/bot/signals  ← Vercel dashboard
                                   ├── /api/bot/liquidity← Vercel dashboard (S-064)
                                   ├── /api/bot/config   ← Vercel dashboard (S-064)
                                   ├── /api/bot/trades/closed ← Vercel dashboard (#557)
                                   ├── /api/bot/backtests← Vercel dashboard (M5 P4)
                                   ├── /api/bot/shadow/predictions ← Vercel dashboard (S-AI-WS8-PART-2)
                                   ├── /api/bot/shadow/stats       ← Vercel dashboard (S-AI-WS8-PART-2)
                                   ├── /api/bot/shadow/drift       ← Vercel dashboard (S-AI-WS8-PART-3)
                                   ├── /api/pnl/history  ← Vercel dashboard (S-063, no-session)
                                   ├── /api/pnl
                                   ├── /api/status
                                   ├── /api/diag/*       ← PM-side read-only (S-051)
                                   └── /api/health
```

`ict-web-api.service` runs from `/opt/ict-trading-bot` (a symlink to
`/home/ubuntu/ict-trading-bot`, the only working tree). The symlink is
created on first run by `scripts/deploy_diag.sh`; if it goes missing,
the API CHDIRs to a non-existent path and crashloops.

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
        dashboard.py    — /api/bot/{stats,logs,positions,signals} (S-014)
        bot_config.py   — /api/bot/config (S-064)
        liquidity.py    — /api/bot/liquidity (S-064)
        trades_closed.py — /api/bot/trades/closed (#557)
        backtests.py    — /api/bot/backtests (M5 P4)
        shadow.py       — /api/bot/shadow/{predictions,stats} (S-AI-WS8-PART-2)
        diag.py         — /api/diag/* endpoints (S-051, token-gated read)
        pnl.py          — /api/pnl
        pnl_history.py  — /api/pnl/history (S-063, no-session)
        status.py       — /api/status
    runtime_status.py   — writes runtime_logs/runtime_status.json (DO NOT DELETE—imported by pipeline)
runtime_logs/
  signal_audit.jsonl    — structured pipeline audit log (primary log source for dashboard)
  validation.jsonl      — M5 backtest-run audit log (one NDJSON row per /test invocation)
  heartbeat.txt         — mtime used to detect if bot is alive
trade_journal.db        — SQLite: trades, order_packages, backtest_results (M5)
```

## Dashboard REST API (S-014)

Unauthenticated GET routes — Tier 1 read surface. See
`docs/api-tier-policy.md` for the complete tier inventory (Tier 1 / 2 /
2.5 / 3) and the rules for adding routes.

| Endpoint | Returns | Data source |
|----------|---------|-------------|
| `GET /api/bot/stats` | `BotStats` JSON | `trade_journal.db` + `psutil` + `heartbeat.txt` |
| `GET /api/bot/logs` | `LogEntry[]` | `runtime_logs/signal_audit.jsonl`, fallback `bot.log` |
| `GET /api/bot/positions` | open positions | `trade_journal.db` WHERE status='open' |
| `GET /api/bot/signals` | recent ICT detections | `runtime_logs/signal_audit.jsonl` filtered to buy/sell |
| `GET /api/bot/liquidity?symbol=X` | per-symbol liquidity zones (S-064) | `runtime_logs/liquidity_state.json` (pipeline writes per-tick) |
| `GET /api/bot/config` | effective config view (S-064) | `config/accounts.yaml` + `config/strategies.yaml` + `runtime_logs/runtime_status.json`; secrets redacted |
| `GET /api/bot/trades/closed?limit=N&since=ISO_TS` | `ClosedTrade[]` (#557) | `trade_journal.db::trades` filtered to closed + non-backtest, joined to `order_packages` for the closed-at proxy |
| `GET /api/bot/backtests?limit=N&strategy=X` | `BacktestRun[]` (M5 P4) | `trade_journal.db::backtest_results` (M5 consumer writes one row per `/test <strategy>`); newest-first by id; headline metrics only |
| `GET /api/bot/shadow/predictions?limit=N&model_id=X&stage=X&since=ISO` | envelope `{log_present, log_path, records[], count}` (S-AI-WS8-PART-2) | `runtime_logs/shadow_predictions.jsonl` (WS7 audit log); newest-first; reuses `ml.shadow.inspector` |
| `GET /api/bot/shadow/stats?model_id=X&stage=X&since=ISO` | envelope `{log_present, log_path, records[], count}` per-`(model_id, stage)` aggregate (S-AI-WS8-PART-2) | same log; aggregated via `ml.shadow.inspector.aggregate` |
| `GET /api/bot/shadow/drift?model_id=X&stage=X&reference_days=N&current_days=N&bins=N&score_min=F&score_max=F` | drift envelope `{log_present, log_path, model_id, stage, reference_window_start, current_window_start, reference_count, current_count, verdict, ks, ks_verdict, psi, psi_verdict, reference_mean, current_mean, reference_stdev, current_stdev}` (S-AI-WS8-PART-3) | same log; window-over-window score-distribution comparison via `ml.shadow.drift.compute_drift` (KS + PSI) |
| `GET /api/pnl/history?days=N` | `PnlHistoryPoint[]` (S-063) | `trade_journal.db` (closed trades, realised PnL per UTC day) |

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
| `M5_CONSUMER_ENABLED` | Auto-install the M5 backtest consumer in the comms poll loop. Default off; set to `1`/`true` on the VM systemd unit. Operator runbook: `docs/runbooks/strategy-testing.md` |
| `M5_BACKTEST_TIMEOUT_S` | Wall-clock cap per backtest subprocess (default 120s) |
| `BACKTEST_DATA_PATH` | Override the candle CSV the M5 backtest runner reads |
| `VALIDATION_LOG_PATH` | Override the M5 validation NDJSON path (default `runtime_logs/validation.jsonl`) |

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

### Reaching `/api/diag/*` from a PM-side / web-sandbox session

The web sandbox can't egress to `158.178.210.252:8001`. The
`vm-diag-snapshot` GitHub Actions workflow is the relay: open an
issue titled `[diag-request] <path>` with label `vm-diag-request`,
the workflow runs the diag fetch over SSH + curl, posts the JSON
back as an issue comment, and closes the issue. Full flow + failure
modes in `docs/claude/diag-relay.md`. Don't paste the
`DIAG_READ_TOKEN` into chat or commit it; it lives in repo secrets
(`VM_SSH_KEY`, `DIAG_READ_TOKEN`) and on the VM only.

## PM-side session capabilities (Claude Code on the web)

What the sandbox session can and can't do directly. Future sessions
should not re-derive this — if the contract changes, edit here.

**MCP tools available** — `mcp__github__*` (subset: issue
read/write, PR read/write/merge, file read/create/update, branch
create, secret scanning, **but no `create_label`, no `run_workflow`,
no artifact download, no run-log read**), Google Drive (file search
+ read), Hugging Face (hub search, doc fetch), Bigdata.com (market
data), Gmail (read-only labels).

**Network from inside the sandbox** — outbound is allowlisted to
`*.github.com`, `*.vercel.app`, `*.anthropic.com`, and a small set
of platform-managed hosts. Arbitrary IPs (incl. the Oracle VM) are
firewalled. `dangerouslyDisableSandbox: true` does **not** help —
the egress restriction is enforced one layer below the Bash sandbox.

**No custom MCP servers.** Claude Code on the web doesn't honour
project `.mcp.json` and can't run `claude mcp add`. To get richer
GitHub powers (workflow_dispatch, run artifacts, label CRUD), the
operator has to either (a) wait for Anthropic to expand the hosted
GitHub MCP, or (b) move the ops session to Claude Code desktop / CLI
and install `github/github-mcp-server`. Until then, the workarounds
below are the contract.

**Workarounds shipped:**

- **VM diag access (read-only)** — issue-driven, see § "Reaching
  `/api/diag/*` from a PM-side / web-sandbox session" above and the
  full doc at `docs/claude/diag-relay.md`.
- **VM operator actions (narrow mutating)** —
  `.github/workflows/operator-actions.yml` exposes a fixed
  allowlist (`status-check`, `pull-latest-logs`, `pull-and-deploy`,
  `restart-bot-service`, `reboot-vm`). Tier-1 actions are
  autonomous; Tier-2 actions require an operator ack first
  (in-conversation approval is sufficient). Two dispatch paths,
  identical allowlist + audit:
  - `workflow_dispatch` — operator clicks "Run workflow" in the
    Actions UI.
  - **Issue-driven** — open a labelled issue (`operator-action`)
    with body `action: <name>\nreason: <text>`. Workflow runs,
    comments back, closes the issue. Use this when the sandbox needs
    to dispatch autonomously after operator ack. Body parsing rides
    through env (`ISSUE_BODY`), not inline interpolation.

  Full contract: `docs/claude/operator-actions.md`. **Never** route
  strategy / risk / account-mode changes through this workflow —
  those remain Tier-3 PRs.
- **Web-API self-heal (autonomous, single-purpose)** —
  `.github/workflows/vm-web-api-recover.yml` is the issue-driven
  recovery path for `ict-web-api.service`. When the diag relay
  starts returning curl exit 7 (`Failed to connect to 127.0.0.1`),
  the FastAPI process serving `/api/diag/*` is down and Claude is
  blinded. Open a labelled issue (`vm-web-api-recover`) to fire a
  fixed-form `systemctl restart ict-web-api.service` + health
  probe; the workflow comments back and closes. Restart-only, no
  edits, no other unit touched. Wrapper:
  `scripts/ops/restart_web_api.sh`.
- **Repo label creation** — `.github/workflows/bootstrap-labels.yml`
  self-creates the labels other workflows filter on. Edit the
  `LABELS` array in that file and merge; the next push runs the
  sync. No `create_label` MCP needed.
- **Workflow dispatch** — there's no general-purpose workaround.
  Workflows that need to be Claude-driven from a session must use
  an `issues.opened` (or `pull_request.opened`) trigger filtered to
  a label. Pattern is the diag relay (`vm-diag-snapshot.yml`),
  `vm-web-api-recover.yml`, and now `operator-actions.yml` (whose
  Tier-2 ack is the operator's in-conversation approval — Claude
  carries that approval into the issue body).

## Running Locally
```bash
pip install -r requirements.txt
uvicorn src.web.api.main:app --port 8001 --reload
```

## Important Notes
- `src/web/runtime_status.py` is imported by `src/runtime/pipeline.py` — do NOT delete it
- `heartbeat.txt` mtime is the canonical "is the trader process responsive" signal. Refreshed every `HEARTBEAT_INTERVAL_SECONDS` (default 60 s) from inside `src/main.py`'s sleep loop — so it fires between ticks too, not just at tick completion. A pipeline hang stops the heartbeat (the loop is on the main thread, no daemon) so liveness still reflects pipeline health. Thresholds derived from the same cadence: `< cadence × 3` → running, `< cadence × 10` → paused, else stopped. Helper at `src/runtime/heartbeat.py::heartbeat_label`. Prior history: 2 min threshold (way too tight for a 15-min tick) → 10 min in 2026-05-07 → 18 min (tick × 1.2) on 2026-05-08 → finally cadence-based with 60 s heartbeat the same day, after the tick-coupled basis kept under-counting healthy idleness.
- The old HTMX UI (`web/static/`, `web/templates/`, `src/web/api/routers/ui.py`) has been removed
- The old Streamlit UIs (`src/web/backtest_ui.py`, `src/web/config_ui.py`) have been removed
