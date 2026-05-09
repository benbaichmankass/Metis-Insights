# S-062 — Dashboard build-out: Sprint B — Models tab + Time & Price tab

> **Sprint type:** auto-claude (M6 dashboard UI).
> **Risk tier:** Tier 1 (read-only consumer of existing
> `/api/bot/signals`; no bot endpoint changes required by default —
> only a tiny bot-side read flag if Models tab needs explicit
> active/paused state).
> **Branches to develop on:**
> - dashboard: `claude/dashboard-S-062-models-time-price` against
>   `benbaichmankass/ict-trader-dashboard`
> - bot: `claude/dashboard-S-062-strategy-status` against
>   `benbaichmankass/ict-trading-bot` (only if a tiny strategy-status
>   field is needed; see § "Bot-side decision point").
>
> **Sprint plan parent:** 5-sprint dashboard build-out (S-061..S-065)
> approved by operator on 2026-05-09. This is sprint B (S-062);
> sprint A (S-061) closed PR with the data-contract fix for
> `benbaichmankass/ict-trading-bot#556` and dashboard nullable types.

---

## Starter prompt (copy this into a fresh Claude session)

> Continue the dashboard build-out. Sprint B (S-062): build out the
> **Models** tab and the **Time & Price** tab in
> `benbaichmankass/ict-trader-dashboard`. Both tabs are currently
> `Placeholder` stubs. They consume the bot's existing
> `/api/bot/signals` stream — no new bot endpoints required by
> default. Read `docs/sprints/sprint-062-prompt.md` in
> `benbaichmankass/ict-trading-bot` for full scope, non-goals, and
> checkpoints. Branch:
> `claude/dashboard-S-062-models-time-price` against
> `benbaichmankass/ict-trader-dashboard`. Close the sprint with a
> filed `sprint-063-prompt.md` so I can paste it into the next
> session.

---

## Why

Sprint A landed the data-quality fix; the live signal stream now
carries real `pattern` + `confidence` and the Infrastructure card
shows real readings. That stable feed is the foundation for the
two next tabs:

- **Models tab** (renamed from "SMC Concepts" per operator
  decision 2026-05-09): explains how the strategies are working,
  what they're finding, and gives an at-a-glance roster of
  strategy status + recent signal activity. This is the
  operator's primary lens on "is the system actually doing
  anything useful right now".
- **Time & Price tab**: killzone overlays + signal density per
  session. Builds on the same `/api/bot/signals` stream.

## Goal

Two real tabs replacing two of the five `Placeholder` stubs.
Both tabs render real data when bot has signals; both have an
explicit empty state when the bot is quiet.

## Scope

### Models tab (`src/components/ModelsTab.tsx`, new)

- **Per-strategy / pattern roster** — table-style list:
  - Pattern name (e.g. FVG_REVERSAL, OB_BULLISH, BOS, MSS, CHOCH)
    or strategy name (vwap, fvg, etc.) — depends on which is the
    canonical "model" identity for the operator. Default to
    aggregating by `signal.pattern`.
  - Status: active / paused / errored. **See bot-side decision
    point below**.
  - Last fired (relative time).
  - Recent signal count (last N signals, default N=50).
  - Last-week win rate — **gracefully empty** until
    `/api/bot/trades/closed` (benbaichmankass/ict-trading-bot#557)
    is wired (S-063 or fast-followup).
- **Live signals snapshot** — last N signals from
  `/api/bot/signals`, table view:
  - timestamp, symbol, side, pattern, confidence, price.
  - Filter chips: by symbol, by pattern.
  - Click a row to expand for full context.
- **"What it's finding" narrative strip per strategy** — last 3-5
  signals per strategy summarized (1-line plain English: "FVG_BULLISH
  on BTCUSDT @ 80,250 (conf 0.82) — 12 min ago").

### Time & Price tab (`src/components/TimePriceTab.tsx`, new)

- **Killzone overlay** — London (07-10 UTC), New York (12-15 UTC),
  Asia (00-04 UTC) shaded bands on a 24-hour time axis.
- **Signal density per killzone** — bar chart of signal count per
  killzone over the last 24 / 168 hours.
- **Power-of-3 phase strip** — accumulation / manipulation /
  distribution bands if any signal metadata supports it; render
  empty/disabled with a clear "needs richer signal metadata"
  message otherwise. Don't fake it.

### Wiring

- `src/components/Dashboard.tsx` — replace the `Placeholder` for
  `activeNav === 'models'` with `<ModelsTab signals={signals}
  positions={positions} />` and for `activeNav === 'time-price'`
  with `<TimePriceTab signals={signals} />`.
- `NAV_SECTIONS` — rename `'SMC Concepts'` → `'Models'`, id
  `smc` → `models`. Icon: keep `TrendingUp` or pick a `Brain`-ish
  one from `lucide-react`. Update all references in
  `Dashboard.tsx`.

### Bot-side decision point (Models tab status pill)

The Models tab wants to show each strategy as active / paused /
errored. The bot today does **not** publish a per-strategy status
field on `/api/bot/stats`. Three options:

1. **Recency heuristic (no bot work)** — derive status from signal
   recency: fired-in-last-hour → active; fired in last 24h → idle;
   else → quiet. Cheapest, ships in a single dashboard PR.
2. **Tiny bot read-field** — add `stats.strategies: {name, status,
   last_fired_at}[]` to `/api/bot/stats`. Requires a small bot PR.
3. **Defer** — leave the status pill as `—` for now and let it
   land alongside Settings (S-064).

Default to option 1 unless operator preference says otherwise on
sprint open. Flag the decision in a comment on the dashboard PR.

## Non-goals

- Performance tab (P&L curve, drawdown, win-rate by strategy) —
  S-063.
- Liquidity Maps tab — S-064.
- Settings tab (read-only config view) — S-064.
- Operator controls (halt, close-all, live/dry toggle) — S-065.
- `/api/pnl/history` wiring + login flow — S-063.
- Strategy parameter editing — Tier 3, never in this sprint.

## Checkpoints

| # | Title | Output |
|---|---|---|
| 1 | Read S-061 close-out + audit current `/api/bot/signals` | sample real-data shape; pick option 1/2/3 for the status pill |
| 2 | `ModelsTab.tsx` — roster + signals snapshot + narrative strip | dashboard commit |
| 3 | `TimePriceTab.tsx` — killzones + density bars | dashboard commit |
| 4 | `Dashboard.tsx` rewire + nav rename | dashboard commit; `vite build` green |
| 5 | Sprint B close-out + S-063 (Performance + persistent equity) handoff prompt | `docs/sprints/sprint-063-prompt.md` filed |

## Validation steps

- `npm run build` — clean.
- Vercel preview — both tabs render with live signals.
- Manual: kill bot signals (or run against an empty audit log) —
  empty states render cleanly, no JS errors.
- Mobile: both tabs usable at sm breakpoint (table scrolls,
  killzone bands stack or scroll).

## Documentation updates

- `ict-trader-dashboard/CLAUDE.md` — File Structure section: add
  `ModelsTab.tsx`, `TimePriceTab.tsx`. NAV section change in
  Dashboard.tsx.
- `benbaichmankass/ict-trading-bot/ROADMAP.md` — mark S-062 closed
  with a one-liner.
- `benbaichmankass/ict-trading-bot/docs/claude/milestone-state.md` —
  refresh Active milestone to S-063; advance the queue.

## Closing handoff

The closing checkpoint files
`docs/sprints/sprint-063-prompt.md` covering **Performance tab +
persistent equity history**. That sprint will need a Tier-2
decision on auth (drop `require_session` for `/api/pnl/history`
behind the Vercel rewrite, or build a real login flow); flag the
trade-off at the top of the next prompt so the operator can
choose before code work starts.
