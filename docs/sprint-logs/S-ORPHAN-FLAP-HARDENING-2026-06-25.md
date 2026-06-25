# Sprint Log: S-ORPHAN-FLAP-HARDENING-2026-06-25

## Date Range
- Start: 2026-06-24
- End: 2026-06-25

## Objective
- Primary goal: Make ORPHAN a problem the system actively resolves and loudly flags — never a silent resting status — and clean up the historical orphan-flap footprint so every physical position is ONE reconciled row. Triggered by a Telegram alert: an `ib_paper` MHG position DB-closed at `sl_cross` but "re-adopt suppressed" — i.e. the journal looked clean while the broker position was never actually flattened.
- Secondary goals: (a) stop NEW flaps at the source (close path); (b) make every new orphan auto-log + ping for review; (c) add an explicit queryable reconcile state; (d) collapse the historical phantom-duplicate rows + keep them out of analytics.

## Tier
- Tier 1 / Tier 2 / **Tier 2** (+ one operator-gated live data mutation)
- Justification: order-path correctness fixes (IB close-confirm), observability writes (orphan red-flag + alerts), a schema column, an ops tool, and read-path analytics — all Tier-2. The live `reconcile-orphan-history --apply` on the real-money `bybit_2` cluster was the one operator-gated step (explicit "run it" approval).

## Starting Context
- Active roadmap items: orphan-flap hardening (reactive, operator-driven).
- Prior sprint reference: BL-20260624-MHG-CLOSE-CONFIRM (close-confirm fix), BL-20260618-RECONCILE-DUP (re-adopt flap guard), the MGC −$20,127 / 18-row flap incident.
- Known risks at start: a "clean DB, dead broker position" divergence is the dangerous failure mode; historical cleanup touches real-money rows.

## Repo State Checked
- Branch or commit reviewed: `main` @ d131c65 → b6169726 (this sprint's merges); dev branch `claude/mhg-orphan-position-zta9gr`.
- Deployment state reviewed: live VM `ict-bot-arm` auto-deploys from `main` (ict-git-sync); confirmed HEAD synced before the live apply (pull-and-deploy #4510).
- Canonical docs reviewed: CLAUDE.md (env-var table, trade_journal.db schema), docs/claude/system-actions.md, docs/CLAUDE-RULES-CANONICAL.md (tiers).

## Files and Systems Inspected
- Code files inspected: `src/runtime/order_monitor.py` (reverse/forward reconciler, adopt/reattach, watchdog), `src/units/accounts/ib_client.py` (place/close/protective), `src/runtime/execution_diagnostics.py`, `src/units/db/database.py`, `src/web/api/_clean_trades.py`, the analytics routers (`performance`, `dashboard`, `pnl`, `pnl_history`, `strategies`, `attribution`, `trades_closed`).
- Config files inspected: n/a (no config change).
- Deployment files inspected: `.github/workflows/system-actions.yml`, `scripts/ops/_lib.sh`, `scripts/ops/notify_run.sh`.
- Docs inspected: CLAUDE.md, docs/claude/system-actions.md.
- Services or timers inspected: `ict-trader-live.service` (post-deploy state via #4510).
- GitHub Actions workflows inspected: system-actions, vm-diag-snapshot (diag relay).

## Work Completed
- **Item #1 — confirm-flatten on close (merged earlier this session).** `IBClient.close` now re-reads the live IB position after the opposing market order and requires it to reach flat within `IB_CLOSE_CONFIRM_S` (default 6s); if not, returns retCode 1 so the monitor leaves the DB row OPEN, naked-autoprotect re-arms a bracket, and the close retries. `IB_CLOSE_RETRY_COOLDOWN_S` (default 300s) defers the active close while unconfirmed so it can't churn-cancel the protective bracket every tick. "DB closed" now always means "broker confirmed flat."
- **Item #2 — red-flag every new orphan (PR #4465).** `execution_diagnostics.enqueue_orphan_created_flag` writes a durable `runtime_logs/orphan_events.jsonl` row + a critical "🚩 ORPHAN TRADE CREATED … ▶️ Initiate a /system-review" Telegram ping, wired at the reverse-reconciler adopt chokepoint, `_mark_orphaned`, and the stuck-strategy watchdog. New `orphan_events` log added to the diag `_LOG_FILES` allowlist; health-review SKILL.md gained an "Orphan-events ingest" step.
- **Item #3 — alert the silent close-fail + stuck-package-sweep paths (PR #4468).** Close-fail streak alert (`enqueue_close_failure` after `MONITOR_CLOSE_FAIL_ALERT_AFTER`, default 3) + stuck-package-sweep alert.
- **Item #4 — explicit `reconcile_status` column (PR #4469).** Idempotent migration `_migrate_add_reconcile_status`; live reconciler writes `reconciled`/`unreconciled` at the adopt/reattach/`_mark_orphaned`/watchdog paths. Orphan is now an explicit queryable terminal state (NULL / unreconciled / reconciled / superseded), not inferred from setup_type/strategy_name.
- **Item #5 — historical reconciliation tool + analytics exclusion (PR #4481).** `scripts/ops/reconcile_orphan_history.py` (+ wrapper, allowlisted Tier-2 system-action `reconcile-orphan-history`): groups orphan-flagged rows by `(account,symbol,direction)`, time-contiguous clusters, keeps ONE canonical (live OPEN row if any, else earliest), reconciles to the originating order package when recoverable else flags `unreconciled`, and void-flags phantom duplicates `superseded`. Never deletes, never void-flags an OPEN row, never collapses a distinct-package trade. New NULL-safe `exclude_superseded_predicate` in `_clean_trades.py` wired into the KPI routers + the Trades list so superseded rows leave the aggregates.
- **Live cleanup applied (operator-approved).** `reconcile-orphan-history --apply` on `/data/bot-data/trade_journal.db` (system-action #4516): 169 orphan-flagged rows across 38 physical positions → 12 reconciled, 26 unreconciled, **131 phantom duplicates void-flagged superseded**, 0 open rows touched. The MGC 19-row flap (−$20k incident) collapsed to 1 reconciled row. Backup: `trade_journal.db.reconcile-orphan-bak-20260625T061404Z`.

## Validation Performed
- Tests run: reconcile-tool suite (9 tests: flap collapse keeps OPEN canonical, package recovery, distinct-package non-collapse, second-open-row never voided, time-gap split, backtest ignored, dry-run no-write, idempotent apply); `exclude_superseded_predicate` (drops only superseded, NULL-safe, prefix-equivalence); system-actions allowlist↔wrapper↔doc consistency (231); analytics router suites after fixture fix (`fastapi` installed locally → 1599 passed across web-api/analytics/reconcile).
- Dry-runs or staging checks: the live `reconcile-orphan-history` DRY-RUN (#4513) was reviewed for sanity (clean exit, 0 open rows touched, plausible counts) before the apply.
- Manual code verification: read the reverse/forward reconciler write paths to mirror the package-recovery match rule; verified each analytics predicate composition by SQL replication.
- Gaps not yet verified: the live dashboard render (Streamlit) was not visually checked this session — the bot-side analytics change is covered by CI + the SQL replication, and the dashboard is a read-only consumer.

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none (schema note lives in CLAUDE.md).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): none (no new pipeline stage; reconciler behaviour unchanged in shape).
- Roadmap updates: this sprint log is the execution record; the work was reactive hardening, not a roadmap milestone.
- GitHub Actions doc updates: `docs/claude/system-actions.md` — new `reconcile-orphan-history` Tier-2 action row + list entry.
- Subsystem doc updates: CLAUDE.md — `IB_CLOSE_CONFIRM_S` / `IB_CLOSE_RETRY_COOLDOWN_S` env rows, `trade_journal.db` `reconcile_status` schema note (superseded = excluded from analytics, now actually wired).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: none net-new.
- Contradiction 2: n/a.
- Code/doc mismatch: the CLAUDE.md note already claimed `superseded` is "excluded from analytics" (added with #4 before the wiring existed); #5 made that true (the `exclude_superseded_predicate` wiring), so the doc and code now agree.

## Risks and Follow-Ups
- Remaining technical risks: low — the live fixes are merged + deployed; the historical apply is reversible (backup taken, void-flag not delete).
- Remaining product decisions (Tier 3): none.
- Blockers: none.

## Deferred Items
- Deferred item 1: **PB-20260625-002** (performance-review backlog) — the 26 `unreconciled` bybit_2 canonicals kept their original possibly-phantom PnL, which still counts in analytics; their exits can't be recovered from Bybit (7-day window expired). A future performance-review should reconcile/accept/void each per evidence.
- Deferred item 2: BL-20260624-MHG-CLOSE-CONFIRM-VERIFY (health-review backlog) — keep verifying the close-confirm fix holds in the live close path.

## Next Recommended Sprint
- Suggested next sprint: a `/system-review` pass once a fresh window accrues — confirm no NEW orphan rows appeared (the #2 red-flag log should be the canary), and drain PB-20260625-002.
- Why next: validates the live-path fixes (#1–#3) actually stop new flaps under real trading, and resolves the residual unreconciled tail.
- Required verification before starting: check `runtime_logs/orphan_events.jsonl` is empty/quiet since this sprint, and re-count `reconcile_status='superseded'` (expected 131) + `unreconciled` (expected 26).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — N/A: no pipeline-stage shape change (reconciler/close behaviour hardened in place); dashboard is a read-only consumer, not visually re-verified this session.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
