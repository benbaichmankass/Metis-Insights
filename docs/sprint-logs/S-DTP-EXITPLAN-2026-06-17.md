# Sprint Log: S-DTP-EXITPLAN-2026-06-17

## Date Range
- **Start:** 2026-06-17
- **End:** 2026-06-17

## Objective
- **Primary:** Ship the **observe-only** foundation of the *dynamic-take-profit
  consistency* feature — a strategy-declared **ExitPlan** (ladder of partial-TP
  rungs + final target + stop/trail) that a materializer translates into
  concrete, broker-persistable exit instructions, **without changing any live
  exit behavior**. Land it in safe increments, each proving it equals today's
  behavior.
- **Secondary:** Wire the soak on the **live API accounts** (not just prop) so
  evidence accrues immediately on the venue we actually trade, and make it
  watchable (read endpoint + dashboard tab).

## Tier
- **Tier 1–2.** P1 modules/tests are Tier-1; the `order_packages` schema +
  `_log_new_order_package` / `execute.py` journaling writes are Tier-2
  (order-path observability, observe-only, best-effort, nothing read back). The
  inert `turtle_soup.exit_plan()` touches a strategy file (Tier-3) but changes
  no trading behavior. Each behavior-touching PR was opened draft and merged on
  explicit operator approval ("merge whatever is ready" + per-PR auto-merge OKs).

## Starting Context
- Roadmap: continuation of the 2026-06-16 live-trade-management contract
  (`docs/audits/live-trade-management-contract-2026-06-16.md`) — the
  verdict→broker mechanism. This feature builds **above** it (static ExitPlan +
  materializer), not a rebuild.
- The approved P1–P5 phasing (prop-first anchor) lived in the planning session,
  summarized on PR #3920's description (not committed to `docs/`).
- Known risk: the order path is on the live money VM; per-tick cost and
  best-effort isolation are load-bearing (cf. the 2026-06-09/10 CPU-wedge
  incidents). All new code is observe-only + exception-swallowed.

## Repo State Checked
- Branch/commit: started from `main` @ `02218a2`; ended with `main` @ `2ebd035`
  after the four PRs below merged.
- Deployment: `main` auto-deploys to the live trader (`ict-bot-arm`) via
  `ict-git-sync` — so the soak writer + endpoint deploy without manual steps.
- Canonical docs reviewed: `CLAUDE.md` (API table, env vars, Prime Directive /
  no-`*_ENABLED`-gate rule), the live-trade-management contract.

## Files and Systems Inspected
- `src/runtime/exit_plan.py`, `src/runtime/exit_plan_realism.py` (P1 modules).
- `src/runtime/strategy_verdict.py`, `src/runtime/order_monitor.py` (verdict→broker
  senders `_send_{modify,close,partial_close}_to_exchange`), `src/units/accounts/execute.py`
  (`execute_pkg`, `_submit_order`), `src/units/accounts/clients.py`
  (`EXCHANGE_MANAGEMENT_CAPS`) — the existing exit plumbing the materializer sits above.
- `src/prop/breakout_executor.py`, `multi_account_ticket.py`, `breakout_notify.py` (prop ticket path).
- `src/core/coordinator.py::_log_new_order_package`, `src/units/db/database.py` (order_packages schema).
- `src/web/api/routers/{news,diag}.py`, `src/web/api/main.py` (router patterns).
- `tests/test_real_schema_db_fixture.py` (codified order_packages column set).
- Dashboard `ict-trader-dashboard/streamlit_app.py` (PAGES/dispatch, `page_news` template).

## Work Completed
Four merged PRs in `ict-trading-bot` (all observe-only, no live exit changed):

- **P1 — #3920 (`676233d`):** `exit_plan.py` (schema + `validate_exit_plan` +
  `build_exit_plan_from_legacy`, never-raise), `exit_plan_realism.py` (advisory
  R-multiple reach clamp), `order_packages.exit_plan`/`exit_plan_state` TEXT
  columns + idempotent migration, `_log_new_order_package` derives + journals the
  ExitPlan, reference `turtle_soup.exit_plan()` (inert), P5-contract CI guard
  extended (every strategy yields a schema-valid plan). +35 tests.
- **P2 — #3921 (`0af67e7`):** `exit_plan_materializer.py` — pure
  `materialize_exit_plan` (direction-resolved, realism-bounded, lot-rounded,
  near→far ordered; account-agnostic fractional qty by default since the package
  is logged pre-sizing). `_log_new_order_package` materializes + persists into
  the `exit_plan_state` column P1 left null. +22 tests.
- **P3 — #3922 (`d4486b8`):** `exit_ladder_soak.py` — venue-agnostic
  `build/record_exit_ladder_soak` → `runtime_logs/exit_ladder_soak.jsonl`
  (single-target vs laddered, `differs_from_single_target`). Wired observe-only
  into `execute.py` (venue=api, live opening orders, reduce-only skipped) and
  `breakout_executor.py` (venue=prop). Diag `log_file?name=exit_ladder_soak`. +12 tests.
- **Endpoint — #3923 (`2ebd035`):** `read_soak_records` (pure, summary block) +
  `GET /api/bot/exit-ladder/soak` (Tier-1). +5 tests. CLAUDE.md API table + diag
  list updated.

Dashboard (`ict-trader-dashboard`):
- **#107 (DRAFT, pending operator preview):** new **Exit Ladder** tab rendering
  `/api/bot/exit-ladder/soak` (summary metrics, venue filter, flattened
  per-order table). Pushed to the standing preview branch `claude/web-app-preview`;
  CLAUDE.md tabs updated.

## Validation Performed
- **Tests run locally (this sandbox):** exit_plan 35, materializer 22, ladder
  soak 17 (incl. read path), plus surrounding order-package/breakout/turtle/P5
  suites — all green. `ruff` clean on every changed bot file (installed ruff
  locally after P1's first CI miss, which was an unused import).
- **CI:** all 13 required checks green on each merged PR.
- **End-to-end (local):** derived plan → materialized `exit_plan_state` →
  `order_packages` JSON column round-trip verified; both `api` and `prop` venue
  soak records build correctly (turtle qty-split ladder; vwap single-target parity).
- **Gaps not yet verified (IMPORTANT):**
  - **NOT verified on the live VM** that `exit_ladder_soak.jsonl` is actually
    accruing — needs `#3923` deployed + the next live opening order. A future
    session should pull `diag log_file?name=exit_ladder_soak` to confirm.
  - The web endpoint + dashboard page were **not** runtime-rendered (no
    fastapi/streamlit in the sandbox) — only `py_compile` + the fastapi-free
    pure helpers were unit-tested. The dashboard preview app is the render check
    (operator preview pending).

## Documentation Updated
- `CLAUDE.md` (bot): `/api/bot/exit-ladder/soak` API row + `exit_ladder_soak`
  diag `log_file` name.
- `CLAUDE.md` (dashboard): Exit Ladder tab row + sidebar order.
- This sprint log; performance-review backlog entry `PB-20260617-002` (below).

## Contradictions or Drift Found
- None introduced. Note: P1 set a precedent of **not** adding an
  ARCHITECTURE-CANONICAL change-log row for this feature; left as-is for now
  (advisory `arch-doc-guard` only, always exits 0). If the feature graduates to
  behavior-changing (P4), it MUST get an ARCHITECTURE-CANONICAL row.

## Risks and Follow-Ups
- **P4 (graduate the ladder to the real exit) is the behavior-changing,
  Tier-3, backtest-gated step — NOT started by design.** It needs the soak to
  accumulate, before/after backtest evidence + `scripts/prop/account_compat_matrix.py`
  PASS for prop, and explicit operator approval. Tracked as `PB-20260617-002`.
- **Dashboard #107 is open (draft) pending operator preview** on the preview app.
  Merge to production only after the operator approves the preview.
- Live-VM accrual of the soak is unverified (see Gaps).

## Deferred Items
- P4 API graduation + P3-live prop graduation (behavior-changing).
- P5 empirical reach-distribution feed for `exit_plan_realism` (MFE/reach
  quantiles per strategy → `config/exit_reach_bounds.yaml`).
- Android consumer of `/api/bot/exit-ladder/soak` (not built).

## Next Recommended Sprint
- **After the soak has accrued a meaningful window** (e.g. ≥30 differing API
  records across strategies): a P4 analysis sprint — characterize laddered-vs-
  single outcomes, run the standalone backtest harness before/after on the same
  history (win rate, expectancy, PnL, hold time, TP-reach/partial-fill
  distribution), and propose the graduation as a Tier-3 PR with that evidence.
  **Required verification first:** confirm via diag that the soak log is
  populating on the live VM.

## Wrap-Up Check
- [x] Code inspected directly (real paths listed above; verdict→broker plumbing mapped before building).
- [x] Docs reviewed + updated (both repos' CLAUDE.md; this log).
- [ ] TRADE-PIPELINE doc — no pipeline *stage* changed (observe-only side-writes only); no update needed.
- [x] Roadmap — see ROADMAP.md row added for S-DTP-EXITPLAN.
- [x] Contradictions recorded (none introduced; arch-doc note logged).
- [x] Unknowns stated plainly (live-VM accrual unverified; UI not runtime-rendered).
