# API tier policy

> **Purpose:** single source of truth for which `ict-web-api.service` routes
> are unauthenticated reads (Tier 1), session-gated reads / mutations
> (Tier 2), and operator-controls / risk surface (Tier 3 — explicit gates,
> never auto-callable).
>
> **Authority:** this file is the human-facing inventory. The runtime gate
> is the actual `Depends(require_session)` (or token check) wired in each
> router. If they disagree, fix the code OR fix this file in the same PR
> — they must move together.
>
> **Origin:** S-063 (2026-05-09). Created when `/api/pnl/history` was
> dropped from the JWT gate so the Vercel dashboard's Performance tab
> could consume it without a login flow (login is S-065). The tier split
> existed implicitly before then; this file makes it explicit.

---

## Tier 1 — public read, no session required

Read-only endpoints the Vercel dashboard hits directly without a JWT.
CORS is enforced (only `DASHBOARD_ORIGIN` + localhost dev URLs are
allowed); the Vercel rewrite proxies `/api/*` to the bot's `:8001`
so the dashboard doesn't have to know the VPS IP.

| Endpoint | Source | Notes |
|---|---|---|
| `GET /api/health` | `src/web/api/main.py` | Liveness check. Always public. |
| `GET /api/bot/stats` | `src/web/api/routers/dashboard.py` | Aggregated bot stats — pnl24h, totalPnL, openTrades, winRate, status, datasource, vmHealth. |
| `GET /api/bot/logs` | `src/web/api/routers/dashboard.py` | Tail of `runtime_logs/signal_audit.jsonl`, fallback `bot.log`. |
| `GET /api/bot/positions` | `src/web/api/routers/dashboard.py` | Open positions from `trade_journal.db`. |
| `GET /api/bot/signals` | `src/web/api/routers/dashboard.py` | Recent ICT detections from `signal_audit.jsonl`. |
| `GET /api/pnl/history?days=N` | `src/web/api/routers/pnl_history.py` | **Added S-063 (2026-05-09).** Per-day realised P&L for the dashboard Performance tab. Returns `PnlHistoryPoint[]` (`{date, pnl, trades}`). `days` clamped to 1..90. |
| `GET /api/bot/trades/closed?limit=N&since=ISO_TS` | `src/web/api/routers/trades_closed.py` | **Added S-557 (2026-05-09).** Closed (live, non-backtest) trades for the dashboard Journals tab. `trade_journal.db::trades WHERE status='closed'` LEFT JOIN `order_packages` for the closed-at proxy. `limit` clamped to 1..200 (default 50); `since` filters by closed-at; newest-first. |
| `GET /api/bot/liquidity?symbol=X&limit=N&sweeps_limit=N` | `src/web/api/routers/liquidity.py` | **Added S-064 (2026-05-09).** Per-symbol liquidity zones (equal highs / lows / recent sweeps) for the dashboard Liquidity Maps tab. Reads `runtime_logs/liquidity_state.json` (written per tick by the pipeline; see S-064 prereq). `limit` / `sweeps_limit` clamped to 1..100. Empty / missing file → 200 with empty arrays. |
| `GET /api/bot/config` | `src/web/api/routers/bot_config.py` | **Added S-064 (2026-05-09).** Read-only effective config view (accounts, strategies, risk caps, halt flag, live/dry per account) for the dashboard Settings tab. Allowlist for accounts; recursive secret-key denylist for strategy params. Never echoes `api_key_env` / `api_secret_env` field values. |
| `GET /api/bot/backtests?limit=N&strategy=X` | `src/web/api/routers/backtests.py` | **Added M5 P4 (2026-05-10).** Recent rows from `trade_journal.db::backtest_results` (populated by the M5 `/test <strategy>` consumer) for the dashboard Backtests tab. Returns headline metrics only (id, strategy, dates, totals, winRate, profitFactor, expectancy, sharpeRatio, maxDrawdownPct, totalPnl, createdAt). `limit` clamped 1..200 (default 50); `strategy` is an optional exact-match filter against `strategy_version`; newest-first by id. Missing DB / missing table both collapse to `[]`. |
| `GET /api/bot/health/latest` | `src/web/api/routers/health_snapshots.py` | **Added 2026-05-11 (#820).** Most recent `artifacts/health/latest.json`. Envelope `{present, path, snapshot}`. Drives the dashboard System Health tab's summary strip + per-check grid. |
| `GET /api/bot/health/history?hours=N&include_payload=BOOL` | `src/web/api/routers/health_snapshots.py` | **Added 2026-05-11 (#820).** Newest-first list of `artifacts/health/health_check_<TS>.json` snapshots within the lookback window. `hours` clamped 1..336 (default 24). `include_payload=true` embeds the full JSON for each row (used by the modal "view raw" path). |
| `GET /api/bot/health/snapshot?lines=N` | `src/web/api/routers/health_snapshots.py` | **Added 2026-05-11 (#820).** Tail of `artifacts/health/health_snapshot.txt`. `lines` clamped 1..5000 (default 200). |
| `GET /api/bot/health/services` | `src/web/api/routers/health_snapshots.py` | **Added 2026-05-11 (#820).** `systemctl show` state for the allowlisted bot units (`ict-trader-live.service`, `ict-web-api.service`). Returns `{systemctl_available, services: [{unit, state, sub_state, active_enter_iso}, ...]}`. Unit allowlist is hardcoded — no arbitrary `systemctl` query surface (those remain in Tier 2.5 `/api/diag/services`). |
| `GET /api/bot/trades/scores?limit=N&include_open=BOOL` | `src/web/api/routers/trade_scores.py` | **Added 2026-05-11 (#820).** Joins each trade's `openedAt → closedAt` window against `runtime_logs/shadow_predictions.jsonl` and returns per-`model_id` aggregates (count, first/last/min/max/mean). `limit` clamped 1..200 (default 50). Drives the dashboard Journals tab's "Model scores" column. Empty `scores: []` per trade when the shadow log is absent. |
| `GET /` | UI surface | Login redirect target. |
| `GET /login` | UI surface | Login page. |
| `GET /static/*` | `app.mount("/static", ...)` | Static assets. |

**Adding a route here is a code change reviewed in a PR.** The route
must be:

1. Read-only — never mutates state, never triggers an order.
2. Cheap — no expensive joins, no full-table scans without a window.
3. Safe to expose to anyone who can hit the dashboard's CORS origin
   (which today is just `DASHBOARD_ORIGIN`, but treat the threat
   model as "the dashboard URL leaked to a hostile party").

---

## Tier 2 — session-gated reads + mutations

JWT-gated via `require_session` (HS256, 1h TTL, allowlisted email).
The dashboard does not consume any of these today; once login lands
in S-065 the relevant ones will move there. The `/api/auth/login`
endpoint mints the token (it's in `PUBLIC_ROUTES` because you need
it to get a token in the first place).

| Endpoint | Source | Notes |
|---|---|---|
| `POST /api/auth/login` | `src/web/api/routers/auth.py` | Mints a JWT for the allowlisted email. Public so an unauthed caller can authenticate. |
| `GET /api/status` | `src/web/api/routers/status.py` | Detailed runtime status. |
| `GET /api/pnl` | `src/web/api/routers/pnl.py` | Per-account P&L (realized + unrealized). |

The `PUBLIC_ROUTES` set in `src/web/api/auth.py` enumerates the routes
that opt out of `require_session`. Adding a route there is a code change
reviewed in a PR.

---

## Tier 2.5 — token-gated diagnostics

The `/api/diag/*` surface is read-only but uses a **separate** bearer
token (`DIAG_READ_TOKEN`) instead of the dashboard's JWT. This is a
PM-side / operator-script surface, not a dashboard surface — see
`docs/claude/vm-operator-mode.md` § 9 and `docs/claude/diag-relay.md`.

If `DIAG_READ_TOKEN` is unset on the VM, every `/api/diag/*` route
returns 503 (closed by default). Bad/missing bearer → 401.

| Endpoint family | Source | Notes |
|---|---|---|
| `GET /api/diag/snapshot`, `audit`, `journal`, `status`, `services`, `journalctl`, `log_file` | `src/web/api/routers/diag.py` | Token-gated SELECT-only or shell-safe diagnostic reads. |

---

## Tier 3 — operator controls / risk surface (NOT YET BUILT)

Reserved for the eventual halt / live-dry / restart / order-cancel
controls. These will require:

- A real session (Tier 2 JWT) AND
- An explicit per-action confirmation gate (`?confirm=YES` or a
  short-lived signed action token), AND
- An audit log entry per call with the caller's email + IP.

S-065 will land the first Tier-3 endpoint (halt). Until then, all
mutating operator actions go through the `system-actions.yml`
GitHub workflow (Tier-2 ack via "Run workflow" click; allowlist of
four actions).

---

## Cross-references

- `src/web/api/auth.py` — `PUBLIC_ROUTES` constant + `require_session`
  dependency.
- `CLAUDE.md` — top-level architecture diagram (lists the Tier-1
  endpoints under "Dashboard REST API").
- `docs/claude/vm-operator-mode.md` § 9 — diag-token contract.
- `docs/claude/system-actions.md` — Tier-2 GitHub-workflow allowlist
  for the four mutating ops actions.
- `docs/sprints/sprint-063-prompt.md` — context for the S-063 auth
  decision (option (a)).
