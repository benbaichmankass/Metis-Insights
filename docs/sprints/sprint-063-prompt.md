# Sprint S-063 — Performance tab + persistent equity history

> **Sprint type:** auto-claude (M6 dashboard UI + tiny M2 backend
> auth-surface change).
> **Risk tier:** Tier 2 in spirit (auth decision); the chosen path
> (option 1 — drop the gate on `/api/pnl/history` only) is Tier 1
> read-surface extension once approved.
> **Branches:**
> - dashboard: `claude/dashboard-S-063-performance-tab` against
>   `benbaichmankass/ict-trader-dashboard`
> - bot (only if option 1 or 2 picked): `claude/bot-S-063-pnl-history-auth`
>   against `benbaichmankass/ict-trading-bot`
>
> **Sprint plan parent:** 5-sprint dashboard build-out (S-061..S-065)
> approved by operator on 2026-05-09. Sprint A (S-061) closed the
> data-contract gap. Sprint B (S-062) shipped Models + Time & Price
> tabs. This is sprint C.

---

## Tier-2 auth decision (READ THIS FIRST — blocks code work)

S-063 needs `/api/pnl/history?days=N`, which is JWT-gated today
(`require_session`). Three options, pick before code work starts:

1. **Drop the gate on `/api/pnl/history` only.** Cleanest for the
   dashboard, smallest blast radius, but extends Tier-1 read surface
   by one endpoint.
2. **Stand up a minimal session/login flow on the dashboard**
   (email-link or shared secret), wire it to `require_session`.
   Larger scope but future-proofs every JWT-gated endpoint, including
   the eventual halt controls.
3. **Defer Performance tab.** S-063 falls back to "drawdown only,
   computed from the same client-side rolling totalPnL buffer
   `EquityChart` already uses". Performance lives until S-065 when
   login lands anyway.

Recommend **(1)** for S-063 specifically — Performance is the
highest-value tab still missing and the trade window is read-only.

## Performance tab scope

`src/components/PerformanceTab.tsx` (new):

- Daily P&L bars + cumulative line from `/api/pnl/history?days=30|90`
- Drawdown curve (computed client-side from the same series)
- Per-strategy P&L breakdown — *requires `ict-trading-bot#557`*
  closed-trades endpoint with `pattern` attribution. Empty-state
  until that lands.
- Win rate by pattern (same #557 dependency)
- Time-of-day P&L heatmap (UTC hour x weekday) — same #557 data
- Sharpe / win-loss ratio in the header strip

## Persistent equity history

Replace the in-memory `EQUITY_BUFFER_MAX = 60` rolling buffer in
`Dashboard.tsx`/`EquityChart` with localStorage persistence so the
chart survives a hard refresh. Migrate to `/api/pnl/history` once
the auth decision is made.

## Bot-side scope (only if option 1 picked)

- `src/web/api/routers/pnl_history.py` — drop the
  `Depends(require_session)` from `GET /api/pnl/history`. Keep auth
  on every mutating route.
- Response shape compatible with the dashboard's `PnlHistoryPoint`:
  `{ date, pnl, cumulativePnl?, trades?, wins?, losses? }`. Minimum
  viable response is `{ date, pnl }`; richer is better.
- New unit test that hits `/api/pnl/history` without a session and
  asserts 200.
- Update the route docstring to flag it as Tier-1 read surface.
- File / extend `docs/api-tier-policy.md` enumerating Tier-1
  (read-only, no-session) vs Tier-2 (gated) routes.

## Non-goals

- Login / session flow on the dashboard (S-065 if option 1 picked
  for S-063)
- Liquidity Maps tab (S-064; needs new bot endpoint)
- Settings tab (S-064)
- Operator controls / halt button (S-065)
- Strategy parameter editing (S-065)
- `/api/bot/trades/closed` implementation (#557; tracked
  separately — Performance tab degrades gracefully without it)

## Validation

- `npm run build` clean
- Vercel preview shows Performance tab with daily bars + drawdown
- Empty-bot fallback renders cleanly (in-session equity buffer
  surfaces with an amber notice)
- Mobile usable at sm
- Equity buffer survives a hard refresh
- Bot-side: pytest green, no-session GET → 200, response shape
  is array of `{date, pnl, trades}`

## Closing handoff

The closing checkpoint of this sprint files
`docs/sprints/sprint-064-prompt.md` covering **Liquidity Maps +
Settings (read-only)**. Liquidity Maps will need a new bot
endpoint surfacing liquidity zones (equal highs/lows, recent
sweeps); Settings will need a read-only `/api/bot/config`
endpoint. Mutating controls (halt/start/restart) stay in S-065.
