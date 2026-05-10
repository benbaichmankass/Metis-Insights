# Next-session prompt — S-067 follow-ups (read-path / observability hardening)

Use this as the prompt when starting the next Claude Code session on
`benbaichmankass/ict-trading-bot`. Copy-paste the block below
verbatim into a fresh session.

---

You are picking up an autonomous session on `benbaichmankass/ict-trading-bot`.
Sprint **S-067 — silent-empty error path audit & hardening** closed
on 2026-05-10 (PRs #642, #643, #644, #645, #646, #647 all merged).
This session works through the **S-067 follow-up queue** filed in
`docs/sprint-summaries/sprint-067-summary.md` § Hand-off.

## Read first (in this order)

1. `docs/claude/checkpoints/CP-2026-05-10-01-s067-complete.md` — the
   sprint-close checkpoint (standalone file; the canonical
   `CHECKPOINT_LOG.md` was too large to round-trip via the GitHub
   MCP API in the closing session, so this lives separately for now).
2. `docs/sprint-summaries/sprint-067-summary.md` — full sprint close,
   especially § Hand-off and § Lessons learned.
3. `docs/audits/silent-empty-2026-05-10.md` — the audit doc that
   triggered the sprint. It enumerates every site touched, the
   classification rationale, and **§ 5 Notable discoveries** which
   surfaced (a) `/api/bot/trades/closed` already partially shipped at
   `src/web/api/routers/trades_closed.py`, (b) the two `_vm_health`
   helpers are forked, (c) `_db_info_payload` is a textbook pattern.
4. `docs/claude/milestone-state.md` § Queued milestones — confirm
   the workplan order; S-047 T6 (live smoke + runbook) is still
   queued first per the workplan but is operator-gated on a Bybit
   Spot Margin toggle, so this session works the S-067 follow-up
   chain in parallel.
5. `docs/sprints/sprint-067-prompt.md` § 8 Hand-off — same priority
   list, primary source.

## Hard constraints

- **Tier 1 only by default.** Tier-2 items (live-order path, env-gate
  purge) need operator ack pre-merge — file as DRAFT and ping. See
  `docs/claude/workplan.md` § Decision and merge authority.
- **One PR per follow-up item.** Don't bundle. Each item below is
  sized for a single shippable PR with regression tests.
- **Self-merge Tier 1 after CI green.** Don't wait for human merge
  unless DRAFT status is required by Tier 2 / live-mode invariant.
- **No new sprints.** This is the queue; pick from it. If something
  ambiguous comes up, file a `BLOCKED-PM` draft PR with the
  question and skip to the next item.
- **Live-mode invariant.** No edits to `src/runtime/orders.py`,
  `src/runtime/pipeline.py` (dispatch logic), `src/runtime/risk_counters.py`,
  `src/runtime/order_monitor.py`, `src/main.py`, `src/units/accounts/execute.py`,
  `config/accounts.yaml`, `config/strategies.yaml`, or `deploy/*.service`
  in any Tier-1 PR. Items #3 and #4 below explicitly touch these and
  are flagged Tier 2.

## Pickup queue (priority order)

Each item is ~30–90 min including tests + commit + PR + merge.

### 1. Test fixture extraction (Tier 1)

**Goal:** generalise PR #627's real-schema test fixture pattern
(`_make_canonical_trades_db` in `tests/test_dashboard_data_contract.py`)
into a reusable shared fixture `tests/fixtures/real_schema_db.py`.
Apply to every read endpoint so future schema drift fails loudly in CI
instead of silently returning `[]`.

Steps:

1. Create `tests/fixtures/__init__.py` (empty) + `tests/fixtures/real_schema_db.py`.
2. Move `_make_canonical_trades_db` and `_insert_trade` helpers from
   `tests/test_dashboard_data_contract.py` into the new file. Keep
   the test file's existing tests passing by importing the moved
   helpers (`from tests.fixtures.real_schema_db import …`).
3. Add a `pytest.fixture` factory variant in the new file that
   yields a populated tmp-path DB as a `Path`, configurable per test.
4. Migrate `tests/test_s067_silent_empty_fixes.py` and
   `tests/test_s067_cp3_silent_empty_fixes.py` to use the shared
   fixture (where applicable — some tests intentionally materialise a
   broken schema and should keep their custom shape).
5. Add at least one **new** regression test per remaining read
   endpoint that doesn't already have one — `/api/bot/logs`,
   `/api/bot/trades/closed`, `/api/bot/liquidity`, `/api/bot/config`,
   `/api/diag/snapshot`, `/api/pnl`, `/api/pnl/history`. Each test:
   materialise the canonical schema with one realistic row, hit the
   endpoint, assert the wire shape.

Tier 1 / infra. Self-merge. PR title:
`refactor(tests): shared real-schema DB fixture for read-endpoint regression tests`.

### 2. Verify `/api/bot/trades/closed` end-to-end (Tier 1)

**Goal:** `/api/bot/trades/closed` was discovered already on disk
during the S-067 audit (`src/web/api/routers/trades_closed.py`) but
nobody confirmed end-to-end behaviour or retired the dashboard's
regex fallback (`deriveClosedTradesFromLogs` in
`ict-trader-dashboard/src/services/api.ts`).

Steps (bot side, this repo):

1. Confirm the endpoint is wired in `src/web/api/main.py` (it is —
   `app.include_router(trades_closed_router.router)`).
2. Add an end-to-end regression test in
   `tests/test_trades_closed_endpoint.py` using the shared fixture
   from item #1: insert a closed trade via `_insert_trade`, hit
   `/api/bot/trades/closed?limit=10`, assert the wire shape matches
   the dashboard's `ClosedTrade` interface
   (`ict-trader-dashboard/src/types.ts`).
3. Add tests for the `since` query param + the `MAX_LIMIT` clamp.
4. Close ict-trading-bot#557 with a comment linking to the new
   tests + the existing implementation.

Dashboard side (separate PR in `benbaichmankass/ict-trader-dashboard`):

5. Verify the endpoint is reachable from the dashboard. If yes,
   either delete `deriveClosedTradesFromLogs` and the fallback path
   in `getClosedTrades`, or annotate it as legacy + log a deprecation
   warning. Operator preference: prefer deletion if no production
   reads have hit the fallback in the last week (check Vercel logs).

Tier 1 / infra. Self-merge bot side; dashboard side is also Tier 1.
PR titles:
- bot: `feat(api): regression tests for /api/bot/trades/closed; close #557`.
- dashboard: `chore(api): retire deriveClosedTradesFromLogs fallback`.

### 3. Closed → exchange-flat invariant reconciler (**Tier 2**)

**Goal:** the trade #1049 incident from the 2026-05-10 review showed
a row with `status='closed'` in the DB while the position was still
open on the exchange (consumed margin until the orphan reconciler
swept it). The current orphan-position reconciler is the safety net,
not the invariant. Add a tighter loop that catches this in seconds.

Risk: touches `src/runtime/order_monitor.py` and dispatch
orchestration → **Tier 2**. File the PR as DRAFT, ping-PR the
operator, await ack before merging.

Steps:

1. Design memo as `docs/claude/closed-flat-invariant.md` —
   alert-only vs auto-flatten for v1, soak window, rollout plan.
   Recommendation per S-067 hand-off: alert-only for one week, then
   auto-flatten gated on a per-account flag.
2. New module `src/runtime/closed_flat_invariant.py`. Each tick:
   for every DB row that flipped to `status='closed'` in the last N
   seconds (configurable, default 60), query exchange residual size.
   On mismatch → Telegram alert via the trader bot, structured row
   to `runtime_logs/invariant_violations.jsonl`.
3. Test fixtures: mock-exchange test that injects "closed in DB /
   open on exchange" pair and asserts an alert fires within one tick.
4. Wire into the tick loop with the same never-raise contract as
   `runtime_status.write_status`.
5. Soak in dry-run on the live VM for 48h with `alert_only=True`
   before requesting auto-flatten promotion.

Tier 2 / live-order path. DRAFT PR + ping-PR pre-merge.
PR title: `feat(monitor): closed → exchange-flat invariant reconciler`.

### 4. Process-wide env-gate purge (**Tier 2**)

**Goal:** the 2026-05-03 directive said per-account
`RiskManager.dry_run` is the only live/dry switch. PR #630 deleted
`MONITOR_APPLY_TO_EXCHANGE`. There may be more survivors.

Risk: touches the live order path and dispatch routing → **Tier 2**.

Steps:

1. Grep audit: `MULTI_ACCOUNT_*`, `*_ENABLED` (for live/dry-related
   names — exclude unrelated feature flags like `M5_CONSUMER_ENABLED`
   which is just a bot init gate), `*_APPLY_TO_*`, `*_DRY_*`,
   `MONITOR_*`, `DISPATCH_*`. Document each: purpose, current
   default, last touched, whether it still has a job.
2. Decisions: for each survivor → either delete (most), or document
   with a `# allow-silent: <reason>` comment + a regression test
   asserting it can't suppress live exchange writes.
3. CI rule: lint check that any new env var matching the suspect
   patterns requires an inline justification comment, otherwise CI
   fails. Mirror the `silent-empty-guard` shape.
4. Update `docs/claude/trading-mode-flags.md` with the canonical
   "RiskManager.dry_run is the only switch" statement and a list of
   intentionally-surviving gates with reasons.

Tier 2 throughout. DRAFT PR + ping-PR pre-merge.
PR title: `refactor(runtime): purge process-wide env-gates; RiskManager.dry_run is the only switch`.

### 5. Deploy restart contract universalisation (Tier 1)

**Goal:** PR #635 fixed `ict-web-api.service` after 28h of
stale-code drift, but the fix is fragile — a new service added next
week falls into the same trap.

Steps:

1. Replace the fixed unit list in `scripts/deploy_pull_restart.sh`
   with `systemctl list-units --plain --no-legend 'ict-*' | awk '{print $1}'`
   enumeration. Keep a small skip-list (env var) for explicit
   opt-outs.
2. Post-deploy assertion: after restart, curl a sentinel diag
   route that returns the git SHA. Assert response 200 and SHA
   matches `git rev-parse HEAD`. Fail the workflow on mismatch.
3. Add `/api/diag/version` if it doesn't exist (Tier 1 diag
   endpoint, gated on `DIAG_READ_TOKEN` like the others). The
   endpoint returns the git SHA + the python-package version.
4. Update `docs/claude/deployment-ops.md` with the new contract.

Tier 1 / deploy quality. Self-merge after the post-deploy assertion
succeeds on a real `main`-advancing pull on the VM.
PR title: `feat(deploy): universalise restart contract + post-deploy version round-trip assertion`.

### 6. Exchange-fills P&L attribution job (Tier 1)

**Goal:** every other follow-up hardens the local DB. This one
hardens *what we trust* — when local DB and exchange disagree,
exchange wins. Insulates performance reads from any future
schema/state bug.

Steps:

1. Bybit fills puller: daily cron (or per-tick incremental) that
   pulls fills via `GET /v5/execution/list` and writes to
   `runtime_state/exchange_fills.sqlite` with idempotent upserts.
   Live-only — read-path doesn't touch order placement.
2. Reconciliation report: for each closed trade in the local DB on
   day D, compare entry price, exit price, qty, fees against
   exchange truth. Surface mismatches as Telegram alerts via the
   trader bot + `runtime_logs/recon_mismatches.jsonl`.
3. New endpoint `/api/bot/pnl/exchange?days=N` that computes P&L
   per strategy from the exchange-fills DB rather than the local
   trades table. Dashboard PnL panel can switch to this once stable.
4. Doc: new `docs/claude/exchange-truth-attribution.md`.

Tier 1 / observability (read-only Bybit calls + new SQLite store).
Self-merge. PR title:
`feat(observability): exchange-fills P&L attribution + per-strategy reconciliation`.

### 7. Daily one-trade audit (auto-task category, Tier 1)

**Goal:** aggregate metrics hide single-trade pathologies. Trade
#1049 surfaced only because two PRs named it. Adopt a Velotrade-style
daily one-trade walkthrough.

Steps:

1. Auto-task instruction doc: new
   `docs/claude/auto-task-daily-trade-audit.md` describing the
   workflow per the Auto-task / Audit-debug category in
   `workplan.md` § "Auto-task routine".
2. Selection logic: pseudo-random pick from yesterday's closed
   trades, weighted to oversample (a) exits with
   `closeReason='reconciler'`, (b) abnormally large/small P&L,
   (c) stuck-long-duration trades.
3. Audit template: markdown template that walks signal → entry →
   each monitor tick → exit verdict → exchange fill → DB row, with
   pass/fail at each stage.
4. Schedule: wire into the existing daily auto-task routine. Output
   committed under `docs/claude/audits/trade-NNNN-YYYY-MM-DD.md`.
   Mismatches escalate via ClaudeBot (one-way per workplan).

Tier 1 / process / observability. Self-merge.
PR title: `feat(auto-task): daily one-trade lifecycle audit`.

### 8. `hourly_report.py` + `boot_audit.py` audit (Tier 1)

**Goal:** the S-067 audit explicitly deferred these files — the
silent-empty patterns may exist there too but the scope was
read-path-only.

Steps:

1. Same audit shape as `docs/audits/silent-empty-2026-05-10.md`
   but scoped to `src/runtime/hourly_report.py` +
   `src/runtime/boot_audit.py`. New file:
   `docs/audits/silent-empty-reporting-YYYY-MM-DD.md`.
2. Classify each `except` block as legitimate / borderline /
   trust-corroding (same definitions as the original audit).
3. Convert trust-corroding sites with regression tests; add log
   calls to borderline sites; document legitimate ones.
4. Extend the `silent-empty-guard` lint script's `_PROTECTED_FILES`
   tuple to include these two paths once converted.

Tier 1 / infra (these are reporting / boot-time files, not
live-order path). Self-merge.
PR title: `refactor(runtime): silent-empty audit + fixes for hourly_report + boot_audit`.

### 9. `_vm_health` helper consolidation (Tier 1)

**Goal:** the S-067 audit found two forks of `_vm_health` —
`src/web/api/routers/dashboard.py::_vm_health` and
`src/web/api/routers/diag.py::_vm_health` — with identical logic
post-fix. Unify.

Steps:

1. Create `src/web/api/_vm_health.py` (or extend an existing
   shared module) exporting a single `vm_health() -> dict[str,
   float | None]` function.
2. Both routers import from the new module.
3. Delete the duplicates. Keep both router-side function names as
   aliases if any tests reference them by attribute.
4. Add one shared test in `tests/test_vm_health_helper.py`.

Tier 1 / cleanup. Self-merge.
PR title: `refactor(api): consolidate forked _vm_health helpers`.

### 10. **Deferred from S-067 CP-5:** bug-log BUG-065 entry (Tier 1)

**Goal:** the S-067 close PR (#647) couldn't push a bug-log entry
because the file is ≈ 100 KB and the GitHub MCP
`create_or_update_file` API requires the full file content embedded
in a single tool-call payload. A session with local clone access
(or with smaller working-set) can fold it in.

Steps:

1. Clone the repo locally (or operate via `gh` CLI on a host with
   git auth).
2. Insert a single new row at line 41 of `docs/claude/bug-log.md`
   (immediately after the table header), following the BUG-064 /
   BUG-063 verbosity precedent.
3. Compose the row from `docs/audits/silent-empty-2026-05-10.md`:
   - **ID:** BUG-065
   - **Date:** 2026-05-10
   - **Sprint:** S-067 (silent-empty error path audit & hardening)
   - **Area:** api / read-path / error handling
   - **Symptom:** read-path endpoints surfacing structural failures
     as fabricated zeroes / `[]` / `{}` / `None` (PR #627 + #629
     are canonical examples; 5 more sites found in the 2026-05-10
     audit).
   - **Root cause:** broad `except Exception` / `except sqlite3.Error`
     blocks returning shape-correct sentinels without logging.
   - **Fix (PR):** #642, #643, #644, #645, #646, #647.
   - **Concern:** `data` / `config` / observability.
   - **Notes:** lessons-learned summary + cross-references to the
     audit doc and `docs/sprints/sprint-067-prompt.md` § 8 hand-off.

Tier 1 / docs-only. Self-merge.
PR title: `docs(bug-log): BUG-065 entry for the silent-empty error path class`.

## Stop conditions

- If you've spent more than 90 min on a single item without a
  shippable PR, stop, commit a `[BLOCKED-PM]` summary, file a draft
  PR with the diagnosis, and skip to the next item.
- If a Tier-1 fix surfaces a Tier-2 concern (e.g. item #1's fixture
  migration starts to require `src/runtime/orders.py` edits), stop
  the migration immediately and refile the affected scope as a
  Tier-2 follow-up.
- If you reach the end of this queue, append a checkpoint to
  `docs/claude/checkpoints/` (standalone file per the
  `CP-2026-05-10-01-s067-complete.md` precedent until the canonical
  log is repaired) summarising what shipped and stop.

## What's already deployed (don't redo)

- S-067 PRs #642, #643, #644, #645, #646, #647 — all merged 2026-05-10.
- The audit doc `docs/audits/silent-empty-2026-05-10.md` is on `main`.
- The `silent-empty-guard` CI workflow + lint script are live and
  enforced on every PR.
- `docs/claude/testing-policy.md` has the new endpoint error-path
  testing section.
- `docs/sprint-summaries/sprint-067-summary.md` is the canonical
  close artifact.
- `docs/claude/milestone-state.md` has S-067 in Recently closed.
- `docs/claude/checkpoints/CP-2026-05-10-01-s067-complete.md` is
  the standalone closing checkpoint.

## What you must NOT touch this run

- Live-order path: `src/runtime/orders.py`, `src/runtime/pipeline.py`
  (dispatch logic), `src/runtime/risk_counters.py`,
  `src/runtime/order_monitor.py`, `src/main.py`,
  `src/units/accounts/execute.py` — except as flagged Tier 2 in
  items #3 and #4 above (which require operator ack pre-merge).
- `config/accounts.yaml` / `config/strategies.yaml` — strategy /
  account changes are explicit Tier 3 per `workplan.md`.
- Any `deploy/*.service` file — outside the scope of any item in
  this queue. Item #5 touches `scripts/deploy_pull_restart.sh` only.
- Any change that would silence the `silent-empty-guard` workflow's
  output. The CI guard is the contract this sprint shipped; respect it.

## Workplan order context

`docs/claude/milestone-state.md` § Queued milestones still ranks
**S-047 T6** (live smoke + runbook) as workplan-priority #1. That's
operator-gated on a Bybit Spot Margin toggle and runs on its own
branch in parallel — it does NOT block this S-067 follow-up chain.
Pick from this queue while S-047 T6 waits on the operator action.
