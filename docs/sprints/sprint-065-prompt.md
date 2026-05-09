# Sprint S-065 — Controls phase 1 + minimal session/login flow

> **Sprint type:** auto-claude (M6 dashboard UI + first Tier-3 bot
> mutating endpoint + Tier-2 session).
> **Risk tier:** Tier 2 (login flow) + Tier 3 (halt control). This is
> the first sprint that adds risk surface to the dashboard. Scope is
> deliberately narrow.
> **Branches:**
> - dashboard: `claude/dashboard-S-065-login-and-halt` against
>   `benbaichmankass/ict-trader-dashboard`
> - bot: `claude/bot-S-065-halt-endpoint` against
>   `benbaichmankass/ict-trading-bot`
>
> **Sprint plan parent:** 5-sprint dashboard build-out (S-061..S-065)
> approved by operator on 2026-05-09. S-061..S-064 closed earlier.
> **This is the closing sprint of the arc.**

---

## Read this BEFORE code starts (auth + risk gate decisions)

Two architecture decisions the operator should sign off on before
any code lands:

### 1. Login flow — scope (pick one)

| Option | Scope | Pro | Con |
|---|---|---|---|
| **(a) Minimal email + shared secret** (Recommended) | Reuse existing `POST /api/auth/login` (`ALLOWED_EMAIL` + `WEBAPP_PASSWORD_SHA256`). Dashboard adds a `<LoginPage>` that posts the form and stores the JWT in `localStorage`. Bearer header attached to every fetch via the existing `services/api.ts`. Tier-2 endpoints (`/api/status`, `/api/pnl`, `/ui/fragments/*`) flow into the dashboard once the dashboard sends the bearer. | Smallest scope. No new endpoints. Re-uses the entire S-013 auth contract. | Single shared secret on the operator's machine — lose it = re-mint via the env var. |
| (b) Email magic-link | Bot mints a one-shot link, emails it via SES / SendGrid. Dashboard `/auth/callback?token=...` stores JWT. | Better UX for multi-device. | Requires a mail provider, secret rotation, magic-link expiry, SES sandbox handling. **Out of scope for an auto-claude sprint.** |
| (c) OAuth via Google | Auth0 / Google one-tap. | Best UX. | Requires DNS, OAuth client registration, callback verification, JWT exchange logic. **Way out of scope.** |

Default: **(a)**. The other two would each be their own sprint.

### 2. Halt control — gate design

The bot needs `POST /api/bot/halt` (touches `/tmp/trader_halt.flag`,
the same file `pipeline.py` already checks at order-placement time).
This is the first mutating dashboard endpoint. Three layers of gate:

1. **Session.** Standard JWT from `require_session`.
2. **Per-action confirm token.** A `?confirm=<token>` query parameter
   where `<token>` is `sha256(action + iat + signing_key)` truncated
   to 16 hex. The dashboard derives it from the action string and the
   bearer's iat claim; missing / mismatched → 400. This stops a stale
   tab or a clipboard paste from accidentally firing.
3. **Audit log entry.** Every call (success or refused) appends a
   row to `runtime_logs/halt_audit.jsonl` with `{ts, email, ip, action,
   outcome}`. The diag relay can read this to verify operator
   intent.

The audit log is the security-critical part. Tests must lock it in
(every call writes one row, even on 401/400/500).

The reverse — **un-halt** — also requires the same gate. Don't ship
"halt is one-way".

---

## Bot side

### `POST /api/bot/halt` (new) and `POST /api/bot/unhalt` (new)

- Files:
  - `src/web/api/routers/halt.py` — both endpoints + the
    `confirm-token` derivation helper, sharing `_audit_halt_call()`
  - `runtime_logs/halt_audit.jsonl` — append-only, atomic per write
  - `tests/test_web_api_halt.py` — happy path + every refusal path
    (no session / bad confirm / on the audit-log write)
- Contract:
  - `POST /api/bot/halt?confirm=<token>` — flag file created
    (mkdir-tmp + atomic move into `/tmp/trader_halt.flag`); 200
    `{halted: true, halt_audit_id: "..."}`
  - `POST /api/bot/unhalt?confirm=<token>` — flag file removed; 200
    `{halted: false, halt_audit_id: "..."}`
  - Either endpoint without a valid session: 401 + audit row
  - With session but bad confirm: 400 + audit row
  - Confirm token derivation: `sha256(action || claims.iat ||
    JWT_SIGNING_KEY)[:16]`
- Tier-policy doc:
  - Add `POST /api/bot/halt` and `POST /api/bot/unhalt` to a new
    **Tier 3** section in `docs/api-tier-policy.md` (currently
    placeholder-only). Document the confirm-token derivation.

### `GET /api/bot/halt-status` (new, Tier 1)

Tiny read endpoint the dashboard polls so the Settings tab's mode
strip is accurate without needing a logged-in session. Returns
`{halted: bool, halt_audit_tail: [last N from halt_audit.jsonl]}`
where the tail is empty if not logged in (the JSONL itself is read
via the same flag-presence check; the dashboard surfaces the bool
as the Settings mode pill).

## Dashboard side

### `LoginPage.tsx` (new)

- Renders an email + password form posting to
  `POST /api/auth/login`. JWT stored in `localStorage` as
  `ict-jwt-v1`. Failed login surfaces the bot's `error` string
  inline (no console log).
- `Dashboard.tsx` short-circuits to `<LoginPage>` if the JWT is
  missing or expired (`exp` claim < now); otherwise renders the
  existing chrome.

### `services/api.ts` — bearer attachment

- `fetchJson` reads `localStorage.ict-jwt-v1` and sets
  `Authorization: Bearer <token>` on every request when present.
  401 from any endpoint blows away the stored token + re-renders
  to the login page.

### `SettingsTab.tsx` — halt control

- Adds two buttons (`Halt` and `Resume`) in the mode strip,
  visible **only when logged in**. Each click:
  1. Fetches a fresh confirm token from the bot
     (`GET /api/bot/halt-confirm-token?action=halt|unhalt`).
  2. Pops a `<ConfirmDialog>` with the action wording + the bot's
     reason field.
  3. On confirm, posts `POST /api/bot/{halt,unhalt}?confirm=<t>`.
  4. Polls `/api/bot/halt-status` every 5 s until the flag flips.
- The confirm dialog text is explicit: "This will block all new
  order placement at the next pipeline tick. Open positions are
  unaffected. Type `HALT` to confirm." (Type-to-confirm is
  client-side only — the real gate is the confirm token + JWT.)

### `JournalsTab` / `Performance` etc. — gated reads

Once login lands, the previously-Tier-2-but-currently-unused
endpoints `/api/status`, `/api/pnl`, `/ui/fragments/*` start
flowing. Wire `JournalsTab` to consume `/api/pnl` for accurate
realised totals (drops the trade-journal-derived approximation).

## Non-goals

- Live/dry per-account toggle — defer to S-066 once halt has
  baked. Same gate pattern, more verbs.
- Strategy parameter editing — Tier 3, never via this surface.
- Order-cancel button — Tier 3, separate sprint.
- OAuth / magic-link login — see option (b) / (c) in the auth
  decision section.
- `/api/bot/trades/closed` (#557) — still tracked separately;
  Performance tab already degrades gracefully.

## Checkpoints

| # | Title | Output |
|---|---|---|
| 1 | Read S-064 close-out + audit `auth.py` `PUBLIC_ROUTES` + `confirm-token` design notes | confirmed contract; sketch of `tests/test_web_api_halt.py` |
| 2 | Bot: `halt.py` router + `halt_audit.jsonl` writer + tier-policy Tier-3 entry | bot commit |
| 3 | Bot: `halt-status` read endpoint + tests | bot commit |
| 4 | Dashboard: `LoginPage.tsx` + bearer plumbing in `services/api.ts` | dashboard commit |
| 5 | Dashboard: halt buttons in `SettingsTab` + `ConfirmDialog` | dashboard commit; `vite build` green |
| 6 | Sprint E close-out — close out the dashboard build-out arc | both PRs open + ROADMAP / milestone-state advanced + S-065 (final M6 sprint) summary |

## Validation

- Bot: `pytest tests/test_web_api_halt.py -v` green; secret-redaction tests still green; halt-audit row written on every call (success + 401 + 400)
- Bot: `curl` against the deployed VM:
  - `POST /api/bot/halt?confirm=...` (with bearer) → flag created
  - `POST /api/bot/unhalt?confirm=...` → flag removed
  - `GET /api/bot/halt-status` (no bearer) → `{halted: bool, halt_audit_tail: []}`
- Dashboard: `npm run build` clean
- Dashboard: Vercel preview shows login page → log in → halt button on Settings → click halts the bot at next tick → status pill flips
- Manual: log out, hard-refresh, confirm login is required again
- Manual: open two tabs, halt in one, observe the other tab's status pill flip within 5 s

## Documentation updates

- `docs/api-tier-policy.md` — flesh out the **Tier 3** section with
  `POST /api/bot/halt`, `POST /api/bot/unhalt`, and the confirm-token
  derivation. Add `GET /api/bot/halt-status` to Tier 1.
- `CLAUDE.md` — top-level diagram: add the two POST endpoints + the
  halt-status GET. File Structure: add `halt.py`. Notes: spell out
  the confirm-token derivation so a future session doesn't have to
  re-derive it.
- `ict-trader-dashboard/CLAUDE.md` — File Structure: add
  `LoginPage.tsx` + `ConfirmDialog.tsx`. Document the
  `localStorage` JWT key (`ict-jwt-v1`) and the 401-clears-on-stale
  contract.
- `ROADMAP.md` — mark S-065 closed; **dashboard build-out arc
  closed**. Active milestone moves on to whatever's next (M5
  / M9 / M10 per the queue).
- `docs/claude/milestone-state.md` — refresh **Active milestone**;
  M6 stays IN PROGRESS until the closing checkpoint, then closes.

## Closing handoff

This is the **closing sprint of the dashboard build-out arc**.
The closing checkpoint:

1. Closes M6 — Web app UI in `docs/claude/milestone-state.md`.
2. Files no `sprint-066-prompt.md` for *this* arc (the next session
   picks the next workplan milestone — M5 strategy testing
   workflow is the queued auto-claude per the workplan).
3. Notes in the close-out summary: with login + halt landed, every
   future dashboard sprint can stop pre-flighting the auth
   decision (it's settled).

Halt is the first risk surface in the dashboard. The closing summary
should explicitly call out:
- The `halt_audit.jsonl` location + how to tail it from the diag
  relay
- That the operator should rotate `WEBAPP_PASSWORD_SHA256` if the
  password ever leaves their machine
- That un-halt is gated identically to halt (no asymmetric "easy
  on, hard off")
