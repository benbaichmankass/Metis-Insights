# Next-session prompt — post-VWAP-backtest (2026-05-14)

Use this as the prompt when starting the next Claude Code session on
`benbaichmankass/ict-trading-bot`. Copy-paste the block below verbatim
into a fresh session.

---

CONTEXT — picking up from the VWAP backtest debug session (2026-05-14).

All VWAP backtest work landed on `ict-trading-bot` main:
  `a78fa500` — fix YAML syntax in vwap-backtest.yml (broken issues trigger)
  `0d51697`  — fix fetch_backtest_candles pagination (Bybit start+end bug)
  `8462f32`  — fix SSH timeout on long backtest runs (ServerAliveInterval)
  `6f84ddd`  — disable htf_trend_filter (backtest #1090 — all HTF configs
               near-zero Sharpe; no-filter baseline wins on 8 random
               30-day windows across 365-day dataset)

Config is deployed to the live VM (ict-trader-live.service is active,
heartbeat healthy, bybit_2 is the active live account).

TWO OPEN ISSUES — both need fixing, Item 1 is CRITICAL:

──────────────────────────────────────────────────────────────
ITEM 1 — CRITICAL: bybit_2 went live unintentionally
──────────────────────────────────────────────────────────────
Between 05:34–05:53 UTC 2026-05-14, bybit_2 was in dry_run mode
(trades 1315–1318 all REJECTED: account_mode_dry_run). Then at
06:00 UTC trade 1319 was placed as a real live trade WITHOUT any
intentional operator action:

  Trade 1319: SHORT BTCUSDT 0.004 BTC @ 79,829.3
  SL: 79,971.72  |  TP: 79,433.43
  pkg: pkg-c0599d3a4b0d463c  |  account: bybit_2
  Status at last check (07:08 UTC): OPEN
  SL/TP are native Bybit bracket orders (submitted with entry).

Actions required:
1. Pull a fresh vm-diag snapshot to check if trade 1319 is still
   open: issue title `[diag-request] snapshot?limit=5`,
   label `vm-diag-request`.
2. If still open on Bybit: close it safely (operator action or
   manual Bybit UI close). Confirm closure via follow-up diag.
3. Investigate WHY bybit_2 flipped from dry_run to live between
   05:53 and 06:00 — check for config change, service restart,
   git-sync timer push, or env override around that window.
   Pull journalctl: `[diag-request] journalctl?unit=ict-trader-live.service&lines=200&since=2026-05-14T05:30:00Z&until=2026-05-14T06:15:00Z`
4. Return bybit_2 to dry_run mode. Use the sanctioned wire:
   operator-action issue with:
     action: set-account-mode
     account: bybit_2
     mode: dry_run
     reason: accidental live flip on 2026-05-14; investigating root cause
5. Lock the config so git-sync can't flip it again.

──────────────────────────────────────────────────────────────
ITEM 2 — DB schema gap: no such column: signal_type
──────────────────────────────────────────────────────────────
The live VM logs a repeating WARNING every tick:
  src.units.ui.data_loaders | _count_signals_today(vwap):
    no such column: signal_type
  src.units.ui.data_loaders | _count_signals_today(turtle_soup):
    no such column: signal_type

The UI data_loaders are querying a `signal_type` column that
doesn't exist in the deployed database schema.

Actions required:
1. Read `src/units/ui/data_loaders.py` — find the query using
   `signal_type`.
2. Check the DB migration infrastructure — how are existing
   migrations applied? Look for an `alembic/` dir, a
   `migrations/` dir, or inline `CREATE TABLE` / `ALTER TABLE`
   statements in the codebase.
3. Write and apply a migration to add the `signal_type` column
   (or fix the query if the column was removed intentionally).
4. Deploy via operator-action pull-and-deploy after merging.

──────────────────────────────────────────────────────────────
DIAG HOW-TO (updated — read before using vm-diag-snapshot)
──────────────────────────────────────────────────────────────
vm-diag-snapshot reads the issue TITLE as the diag path; body
is ignored. ALWAYS use:
  title:  [diag-request] snapshot?limit=5
  labels: ["vm-diag-request"]
  body:   (anything or empty)

DO NOT put `cmd:` in the body — that is for trainer-vm-diag,
a completely different workflow.

Use `snapshot?limit=5` for packages/trades/health.
Use `snapshot?limit=200` ONLY when you need audit history
(the 665kB response truncates — only audit_tail appears).

Operator-actions issue body format (Tier-2 requires reason):
  action: <action>
  reason: <non-empty text>

Full docs:
  docs/claude/diag-relay.md
  docs/claude/operator-actions.md
  docs/claude/debug-memory.md § "Session 2026-05-14"
