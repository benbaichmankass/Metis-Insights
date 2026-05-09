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
| `GET /ui/fragments/status` | `src/web/api/routers/status_fragment.py` | HTMX fragment (legacy UI; will be removed). |
| `GET /ui/fragments/pnl` | `src/web/api/routers/pnl_fragment.py` | HTMX fragment (legacy UI; will be removed). |

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
mutating operator actions go through the `operator-actions.yml`
GitHub workflow (Tier-2 ack via "Run workflow" click; allowlist of
four actions).

---

## Cross-references

- `src/web/api/auth.py` — `PUBLIC_ROUTES` constant + `require_session`
  dependency.
- `CLAUDE.md` — top-level architecture diagram (lists the Tier-1
  endpoints under "Dashboard REST API").
- `docs/claude/vm-operator-mode.md` § 9 — diag-token contract.
- `docs/claude/operator-actions.md` — Tier-2 GitHub-workflow allowlist
  for the four mutating ops actions.
- `docs/sprints/sprint-063-prompt.md` — context for the S-063 auth
  decision (option (a)).
