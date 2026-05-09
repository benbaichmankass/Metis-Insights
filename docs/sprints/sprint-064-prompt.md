# Sprint S-064 — Liquidity Maps + Settings (read-only)

> **Sprint type:** auto-claude (M6 dashboard UI + two new Tier-1 bot
> read endpoints).
> **Risk tier:** Tier 1 (read-only bot endpoints, read-only dashboard
> tabs). Mutating controls (halt / start / restart / parameter edits)
> stay out of scope — those are S-065.
> **Branches:**
> - dashboard: `claude/dashboard-S-064-liquidity-settings` against
>   `benbaichmankass/ict-trader-dashboard`
> - bot: `claude/bot-S-064-liquidity-config-endpoints` against
>   `benbaichmankass/ict-trading-bot`
>
> **Sprint plan parent:** 5-sprint dashboard build-out (S-061..S-065)
> approved by operator on 2026-05-09. S-061 closed the data-contract
> gap. S-062 shipped Models + Time & Price tabs. S-063 shipped the
> Performance tab + dropped the JWT gate on `/api/pnl/history`. This
> is sprint D — fills the last two `Placeholder` stubs that don't
> need mutating endpoints.

---

## Starter prompt (copy this into a fresh Claude session)

> Continue the dashboard build-out. Sprint D (S-064): build out the
> **Liquidity Maps** tab and the **Settings** (read-only) tab in
> `benbaichmankass/ict-trader-dashboard`. Both tabs are currently
> `Placeholder` stubs. Both need new bot endpoints — Liquidity Maps
> consumes a new `GET /api/bot/liquidity` (equal-highs/lows + recent
> sweeps), Settings consumes a new `GET /api/bot/config` (read-only
> view of strategy + risk config). Both endpoints are Tier-1 (no
> session, per `docs/api-tier-policy.md`). Read
> `docs/sprints/sprint-064-prompt.md` in `benbaichmankass/ict-trading-bot`
> for full scope, the contract sketches, and the close-out file.
> Branches: `claude/dashboard-S-064-liquidity-settings` and
> `claude/bot-S-064-liquidity-config-endpoints`. Close the sprint with
> `sprint-065-prompt.md` filed — that's controls phase 1 (halt + live/dry
> toggle) and the minimal session/login flow that gates them.

---

## Why

S-061..S-063 closed three of the five `Placeholder` stubs (Models,
Time & Price, Performance). The remaining two — Liquidity Maps and
Settings — are the last read-only tabs. Settings unblocks operator
visibility into "what config is the bot running with right now"
without needing the VM. Liquidity Maps surfaces the structural
context behind every signal (where the equal highs/lows sit, what
got swept, where price might gravitate next).

Both need new bot endpoints. Both are pure reads. They're the right
last sprint before the controls phase (S-065) because they don't add
risk surface.

## Scope

### Bot side

#### `GET /api/bot/liquidity` (new)

Tier 1, no session. Returns liquidity zones the strategy layer
already detects internally; this endpoint just exposes them.

**Contract sketch** (lock down on read of the strategy code — the
fields below are the ICT primitives, but the bot's internal naming
may differ; map to these names in the response):

```jsonc
GET /api/bot/liquidity?symbol=BTCUSDT&limit=50
→ {
  "symbol": "BTCUSDT",
  "as_of": "2026-05-09T17:30:00Z",
  "equal_highs": [
    { "price": 80250.5, "first_touch": "2026-05-08T14:00:00Z",
      "last_touch": "2026-05-09T16:00:00Z", "touch_count": 3,
      "swept": false }
  ],
  "equal_lows": [ ... same shape ... ],
  "recent_sweeps": [
    { "side": "high", "price": 80300.0, "swept_at": "2026-05-09T15:42:00Z",
      "wick_size": 12.5 }
  ]
}
```

- `symbol` query param defaults to the bot's primary symbol (or the
  union of tracked symbols — pick whichever is cheaper to read from
  the runtime state).
- `limit` clamps both `equal_highs` and `equal_lows` independently;
  default 25, max 100.
- Empty state (strategy has no detections) → 200 with empty arrays.
- Source: read from whatever in-memory or `runtime_logs/*` artifact
  the strategy already emits. **Do not** introduce a new write path
  for this; if the data isn't already on disk, file a tiny prereq PR
  to surface it before this sprint.

#### `GET /api/bot/config` (new)

Tier 1, no session. Read-only view of the bot's effective config —
strategy params, risk caps, account allowlist, current trading mode
flags. The dashboard renders this as a key/value tree.

**Contract sketch:**

```jsonc
GET /api/bot/config
→ {
  "as_of": "2026-05-09T17:30:00Z",
  "trading_mode": { "live": true, "dry_run": false, "halted": false },
  "accounts": [
    { "id": "bybit_2", "exchange": "bybit", "spot_margin": true }
  ],
  "risk": {
    "max_position_usd": 500.0,
    "max_daily_loss_usd": 50.0,
    "max_open_positions": 3
  },
  "strategies": [
    { "name": "vwap", "enabled": true, "params": { "htf_gate": true, ... } },
    { "name": "fvg",  "enabled": true, "params": { ... } }
  ]
}
```

- **Redact secrets.** Never echo API keys / token hashes / DB paths.
  The endpoint returns config *intent*, not the contents of the env
  file.
- Source: read from whatever the bot already loads at startup
  (`config.yaml`, env, defaults). Don't reload — return the
  in-memory effective config.
- Empty / missing field → omit, not `null` — keeps the dashboard
  view tight.

#### Tier policy + tests

- Add both endpoints to `docs/api-tier-policy.md` Tier-1 table in
  the same PR.
- Per-endpoint unit tests:
  - Happy path: 200 with the expected shape.
  - No-session: explicit test that the endpoint returns 200 without
    a JWT (matches the S-063 pattern in
    `tests/test_web_api_pnl_history.py`).
  - Empty state: 200 with empty arrays / minimal config.
  - Secret redaction (config endpoint): assert no API key / token
    string ever appears in the response.

### Dashboard side

#### `LiquidityMapsTab.tsx` (new)

- **Symbol switcher** — dropdown of currently-tracked symbols
  (read from `getStats()` + `getPositions()`; fall back to a
  hard-coded list of tracked symbols if neither surfaces them).
- **Equal-highs / equal-lows panel** — two stacked tables (or a
  combined sortable table with a "side" chip), price | first touch
  | last touch | touch count | status (active / swept).
- **Recent sweeps strip** — chronological list, side + price +
  wick size + when.
- **Price-axis sketch** — a simple vertical price-axis component
  with the current price marker and tick marks per equal-high/low
  level. Can be a CSS-only component; no chart lib needed.
- **Empty state** — explicit "No liquidity zones detected for
  {symbol}" card.
- **Loading / error** — match the pattern from `PerformanceTab`
  (skeleton card; amber notice on error).

#### `SettingsTab.tsx` (new)

- **Mode strip** — `live | dry-run | halted` chips at the top,
  read-only. The dashboard PR notes this is read-only; the
  *toggle* lands in S-065.
- **Accounts section** — per-account row: id, exchange, spot
  margin flag.
- **Risk section** — key/value list of every risk cap.
- **Strategies section** — collapsible per-strategy block: name +
  enabled chip + params tree (key/value pairs, indented, mono).
- **Effective-as-of stamp** — small grey timestamp at the bottom
  so the operator knows when the config was last loaded.
- **Read-only banner** — clear "Read-only — controls land in
  S-065" notice at the top of the page so the operator doesn't
  hunt for a halt button.
- **Loading / error** — same pattern as `PerformanceTab`.

#### Wiring

- `Dashboard.tsx` — replace the two remaining `Placeholder` cases
  for `activeNav === 'liquidity'` and `activeNav === 'settings'`
  with the new components.
- `services/api.ts` — add `getLiquidity(symbol?: string, limit = 25)`
  and `getConfig()`. Both treat 404 as "endpoint not deployed yet"
  and return a structured "not available" sentinel so the tab can
  render a graceful empty state during the rolling deploy.
- `types.ts` — add `LiquidityZone`, `LiquidityResponse`,
  `BotConfig` types matching the contract sketches above.

## Non-goals

- **Operator controls** — halt / live-dry / restart / order-cancel.
  S-065.
- **Strategy parameter editing** — never in this sprint; Tier 3,
  always.
- **Login / session flow** — S-065.
- **Liquidity zone *detection*** — the bot already detects these;
  this sprint only exposes them. If the data isn't on disk in some
  shape, file a 1-day prereq PR before starting the dashboard work.
- **`/api/bot/trades/closed` (#557)** — Performance tab already
  degrades gracefully without it; not part of S-064 scope. If it's
  a quick finish, do it as a fast-followup outside this sprint.

## Checkpoints

| # | Title | Output |
|---|---|---|
| 1 | Read S-063 close-out + audit existing strategy code for liquidity-zone state | confirm where equal-highs/lows live in the runtime; pick the source surface for `/api/bot/liquidity` |
| 2 | Bot: `GET /api/bot/liquidity` + tests + tier-policy entry | bot commit |
| 3 | Bot: `GET /api/bot/config` + tests + tier-policy entry | bot commit |
| 4 | Dashboard: `LiquidityMapsTab.tsx` + `SettingsTab.tsx` + wiring | dashboard commit; `vite build` green |
| 5 | Sprint D close-out + `sprint-065-prompt.md` filed (controls phase 1 + minimal session/login) | both PRs open with green CI |

## Validation

- Bot: `pytest tests/test_web_api_liquidity.py tests/test_web_api_config.py -v` green; secret-redaction test passes.
- Bot: `curl http://127.0.0.1:8001/api/bot/liquidity?symbol=BTCUSDT` returns sane JSON locally.
- Dashboard: `npm run build` clean.
- Dashboard: Vercel preview shows both tabs rendering with live data.
- Empty-bot path: both tabs render explicit empty states cleanly.
- Mobile: usable at sm.
- Manual: clear that no actionable buttons are present on Settings —
  the operator must not be able to misclick a toggle.

## Documentation updates

- `docs/api-tier-policy.md` — add `/api/bot/liquidity` and
  `/api/bot/config` to the Tier-1 table in the same PR as the
  endpoints.
- `CLAUDE.md` — File Structure: add the new dashboard tabs and the
  two new bot routers under "Dashboard REST API".
- `ROADMAP.md` — mark S-064 closed with a one-liner; advance to
  S-065 active.
- `docs/claude/milestone-state.md` — refresh Active milestone to
  S-065.

## Closing handoff

The closing checkpoint files `docs/sprints/sprint-065-prompt.md`
covering **controls phase 1 + minimal session/login flow**:

- Halt button (Tier 3, requires JWT + per-action confirm token)
- Live/dry toggle (same gate)
- Minimal email + shared-secret login on the dashboard, wired to
  the existing `POST /api/auth/login` endpoint
- All Tier-2 endpoints currently still gated (`/api/status`,
  `/api/pnl`) start to flow into the dashboard once login lands

S-065 is the first sprint that adds risk surface; flag the auth
contract + per-action confirmation token design at the top of
the prompt so the operator can review before code work starts.
