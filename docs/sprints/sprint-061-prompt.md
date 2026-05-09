# S-061 — Dashboard build-out: Sprint A — land PR #5 + close #556 contract loop

> **Sprint type:** auto-claude (M6 dashboard UI + M2 backend contract follow-up).
> **Risk tier:** Tier 1 (read-path bot endpoints; read-only dashboard types).
> **Branches:**
> - bot: `claude/vercel-sprint-planning-vjcdP` against
>   `benbaichmankass/ict-trading-bot`
> - dashboard: `claude/vercel-sprint-planning-vjcdP` against
>   `benbaichmankass/ict-trader-dashboard`
>
> **Sprint plan parent:** 5-sprint dashboard build-out (S-061..S-065)
> approved by operator on 2026-05-09. This file is sprint A
> (S-061). Subsequent sprints: S-062 (Models + Time & Price tabs),
> S-063 (Performance + persistent equity), S-064 (Liquidity Maps +
> Settings read-only), S-065 (controls phase 1 — halt + live/dry
> toggle).

---

## Why

The Vercel dashboard's functionality lags the workplan and the
original Google AI Studio prototype. PR
`benbaichmankass/ict-trader-dashboard#5` introduced 7-tab routing
+ a real Journals tab + diagnostics, but five tabs are still
`Placeholder` stubs and the live data feed still has visible gaps
(vmHealth all zeros, "unknown — conf 0.00" rows). Sprint A closes
the data-quality loop so subsequent sprints (Models, Time & Price,
Performance, Liquidity, Settings, controls) build on a stable
read-path.

## Goal

Land the dashboard's M6+ tab routing + Journals work
(`#5`) and remove the on-wire gaps captured in
`benbaichmankass/ict-trading-bot#556` so subsequent sprints don't
inherit the cosmetic-but-misleading rendering.

## Scope

### Bot side (this repo)

- `src/runtime/pipeline.py` — extend the `log_signal()` payload at
  the pipeline-result write to carry `pattern`, `confidence`, and
  `price` from the originating signal. Without these, the audit
  log loses the structural pattern and the dashboard shows
  "unknown" rows.
- `src/web/api/routers/dashboard.py`:
  - `_vm_health()` — return `None` per field on psutil failure
    instead of fabricating `0.0`. Log the underlying exception so
    the operator can see why telemetry stopped.
  - `get_signals()` — pass missing `pattern` / `confidence` /
    `price` through as `null` instead of `"unknown"` / `0` / `0`.
    A real `0.0` reading still round-trips.
- `tests/test_dashboard_data_contract.py` — three new tests:
  - vmHealth null per field when `_vm_health()` returns the
    failure shape.
  - `/api/bot/signals` returns `null` for missing pattern /
    confidence / price.
  - `0.0` confidence round-trips as `0.0` (regression for the
    prior `0`-as-default).

### Dashboard side (companion PR)

- `src/types.ts` — widen `BotStats.vmHealth.*` and `Signal.{pattern,
  confidence, price}` to `T | null`.
- `src/components/StatsGrid.tsx` — extract `InfrastructureCard`;
  render `—` per null reading; keep the all-three-null empty
  state.
- `src/components/StrategySignals.tsx` — aggregator skips empty
  patterns; render `—` for null `lastConfidence`.
- `CLAUDE.md` — API contract section flags the nullable fields.

### Cross-repo coordination

- Operator self-merge of dashboard `#5` (M6+ tab routing +
  Journals + diagnostics) is unblocked once Sprint A bot PR lands
  on main and the dashboard companion is rebased.
- Sprint A bot fix is forward-compatible with `#5`'s defensive
  rendering; the dashboard PR here is rebased on top of `#5` once
  it merges (small, mechanical conflict on `StatsGrid.tsx`).

## Non-goals

- New dashboard tabs (Models, Time & Price, Performance, Liquidity,
  Settings) — those are S-062..S-064.
- Login / auth / `/api/pnl/history` wiring — S-063.
- Operator controls (halt, close-all, live/dry toggle) — S-065.
- `GET /api/bot/trades/closed` endpoint
  (benbaichmankass/ict-trading-bot#557) — handed to the dashboard
  Journals tab via fallback today, real implementation lands in
  S-063 or as a fast-followup if Journals usage requires it
  sooner.
- Bot-side schema cleanup (consolidating `signal_type` vs `pattern`
  into one canonical field) — out of scope; the writer fix in
  `pipeline.py` handles both.

## Checkpoints

| # | Title | Output |
|---|---|---|
| 1 | Audit `signal_audit.jsonl` writer + `/api/bot/*` consumer | confirmation that the gap is at the writer (no `pattern`/`confidence` in payload) and at the dashboard router (fall-through to `0` / `"unknown"`) |
| 2 | `pipeline.py` writer fix + `dashboard.py` null-on-missing | bot commit with tests |
| 3 | Dashboard `types.ts` widening + render fixes + CLAUDE.md | dashboard commit, `vite build` green |
| 4 | PRs opened against both repos | links in close-out summary |
| 5 | Sprint A close-out + Sprint B (S-062) handoff prompt | `docs/sprints/sprint-062-prompt.md` filed; this doc updated with closing checkpoint |

## Validation steps

- Unit tests: `pytest tests/test_dashboard_data_contract.py -v` — all green.
- Dashboard build: `npm run build` (or Vercel preview deploy) — clean.
- Manual: live preview against the deployed bot — Infrastructure
  card shows real CPU/RAM/Disk readings (`#556` fix landed) or
  `—` (psutil sample failed); StrategySignals shows real ICT
  patterns (FVG / OB / BOS / etc.) instead of "unknown — conf
  0.00".

## Documentation updates

- `ROADMAP.md` — add S-061..S-065 to the sprint ledger.
- `docs/claude/milestone-state.md` — refresh **Active milestone**
  to S-061; add the S-062..S-065 queue.
- `ict-trader-dashboard/CLAUDE.md` — API contract nullable note.
- `ict-trader-dashboard/ROADMAP.md` (if it exists) — add Sprint
  A→E entries.

## Closing handoff

The closing checkpoint of this sprint files the next-sprint
prompt at `docs/sprints/sprint-062-prompt.md` so the operator can
paste it into a fresh Claude session. Sprint B (S-062) scope:
**Models tab + Time & Price tab** (Models replaces "SMC Concepts"
per operator decision 2026-05-09).
