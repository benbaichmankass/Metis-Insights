# Sprint Log: S-DBWIRE-TRADE-PKG-LINK-2026-05-26

## Date Range
- Start: 2026-05-26 11:00 UTC
- End: 2026-05-26 13:00 UTC (PR open, merge held by GitHub Actions auth incident)

## Objective
- Primary goal: close the "(unlinked)" gap in the monitor-reconciler orphan-sweep notification by giving every fan-out leg of an `OrderPackage` a many-to-one back-reference to its parent decision.
- Secondary goals: do not regress any existing reconciler / strategy_monocle behaviour; keep the change additive (no backfill, no schema break).

## Tier
- **Tier 2** — DB schema migration + runtime writer path (`src/units/accounts/execute.py::_log_trade_to_journal`) + reconciler resolver (`src/runtime/order_monitor.py::_resolve_linked_package_id`).
- Justification: touches journal-side writebacks the live trader makes on every executed order; merge requires one operator OK in chat per `CLAUDE-RULES-CANONICAL.md` § Permission Tiers.

## Starting Context
- Active roadmap items: ROADMAP/sprint-logs unchanged by this PR; this is a journal-side data-correctness fix triggered by an operator-observed live event.
- Trigger: at 2026-05-26 11:00 UTC the operator received three monitor-reconciler orphan-sweep Telegram pings for `bybit_1` (BTCUSDT short, DB id 1724, demo) and `bybit_2` (DB ids 1725 + 1726, real) where 1724 + 1725 were tagged `Package: (unlinked)` despite all three rows coming from the same `ict_scalp_5m` short opened at 07:13:04 UTC. The live diag at issue #2038 (08:09 UTC) confirmed: trade rows 1724/1725/1726 share the same entry timestamp + side + strategy, but only 1726 carries the package id `pkg-6ad338849aa345a2` on its row.
- Known risks at start: the writer modification is on every executed-trade path; a regression would either miss the new column on a leg (making the reconciler fall back to legacy, still safe) OR break the strategy_monocle's primary-entry semantics.

## Repo State Checked
- Branch: `claude/elegant-mccarthy-8oTAn` cut from `main @ adc6158` (the 2026-05-26 10:00 UTC health-review backlog drain).
- Deployment state reviewed via the live-VM diag relay (issue #2038 → trades 1718–1726 journal tail; #2039 attempted but timed out on `journalctl ict-trader-live.service`).
- Canonical docs reviewed: `CLAUDE.md` § Architecture / Important Notes + `docs/ARCHITECTURE-CANONICAL.md` Change log + Known gaps (especially the "Reduce-only fill correlation in S-030 monitor" entry).

## Files and Systems Inspected
- Code files: `src/units/db/database.py` (schema + migrations + insert/update_trade + insert/update_order_package), `src/units/accounts/execute.py` (`_log_trade_to_journal` writer path), `src/runtime/order_monitor.py` (`_resolve_linked_package_id`, `_mark_orphaned`, `_cascade_close_linked_package`, `_sweep_unlinked_packages`, `_sweep_stuck_linked_packages`, `_close_trade_from_order_status`), `src/runtime/strategy_monocle.py` (`_has_open_package_for_strategy` to confirm it no longer depends on `linked_trade_id`), `src/runtime/execution_diagnostics.py` (`enqueue_orphan_reconciliation` ping shape — where `linked_package_id` is rendered).
- Tests: `tests/test_monitor_reconciler.py` (fixture pattern + reconciler contract), `tests/test_s030_pr1_order_packages_log.py`, `tests/test_s030_pr3_monitor_loop.py`, `tests/test_execute_journal_rejections.py`, `tests/test_strategy_monocle_open_gate.py`.
- Live state inspected via diag relay (read-only, no operator paste): `journal?table=trades&limit=10` confirmed the three-leg fanout shape; `journalctl?unit=ict-trader-live&lines=300` timed out (the trader unit returned `available: false, reason: timeout` at 08:10 UTC — unrelated to this sprint).
- CI guards inspected so my diff would pass: `scripts/check_silent_empty_in_diff.py`, `scripts/check_dry_run_in_diff.py`, `scripts/check_env_gate_in_diff.py`, `scripts/check_canonical_db_resolver.py`, `scripts/check_canonical_config_loaders.py`.

## Work Completed
- **Schema (`src/units/db/database.py`)**: added `_migrate_add_order_package_id(cursor)` mirroring `_migrate_add_is_demo` / `_migrate_add_account_id`; added `order_package_id TEXT` to the `trades` CREATE TABLE; wired the migration into `create_tables`; added `CREATE INDEX IF NOT EXISTS idx_trades_order_package_id ON trades (order_package_id)` for the reverse lookup.
- **Writer (`src/units/accounts/execute.py::_log_trade_to_journal`)**: hoisted `pkg_id = (pkg.meta or {}).get("order_package_id")` once; passed it into every `insert_trade` payload (real entry, demo mirror, intent_reduce flip leg — every leg of the fanout); gated the legacy `update_order_package(linked_trade_id=int(trade_row_id))` call so it only fires for the **primary real-money entry** (`status=='open' AND not intent_reduce AND not is_demo`). The strategy_monocle "primary entry trade" semantics stay deterministic instead of last-writer-wins; rejection rows continue to skip the linked_trade_id update (gating on a never-live trade would suppress legitimate retries forever).
- **Reconciler (`src/runtime/order_monitor.py::_resolve_linked_package_id`)**: reads `trades.order_package_id` first; falls back to the legacy `SELECT order_package_id FROM order_packages WHERE linked_trade_id = ?` for pre-column rows. Best-effort, never raises — preserves the existing contract.
- **Tests (`tests/test_trades_order_package_id_link.py`, new file)**: 6 cases pinning (a) migration adds the column to a pre-existing schema, (b) migration is idempotent on a fresh DB, (c) `insert_trade` persists the column, (d) `_resolve_linked_package_id` resolves all three legs of the live 07:13 UTC fanout via the new column, (e) legacy fallback still resolves rows with `order_package_id IS NULL`, (f) returns `None` when no link exists.
- **Docs**: added a 2026-05-26 row to `docs/ARCHITECTURE-CANONICAL.md` § Change log. Left the Known-gaps "Reduce-only fill correlation" entry alone — it's about `intent_reduce → parent` join for `position_size` updates, which this PR doesn't touch.

## Validation Performed
- Tests run locally:
  - `tests/test_trades_order_package_id_link.py` — 6 / 6 passing.
  - `tests/test_monitor_reconciler.py tests/test_s030_pr1_order_packages_log.py tests/test_s030_pr3_monitor_loop.py tests/test_execute_journal_rejections.py tests/test_strategy_monocle_open_gate.py` — 186 / 186 passing.
  - `python -m ruff check .` — clean (had one F401 unused `pytest` import in the new test file, fixed at commit `62da2c7`).
- CI guard scripts run locally on the PR's diff (`git diff origin/main...HEAD > /tmp/pr.diff`): all five (`silent_empty_in_diff`, `dry_run_in_diff`, `env_gate_in_diff`, `canonical_db_resolver`, `canonical_config_loaders`) exit 0.
- Manual code verification: confirmed `strategy_monocle._has_open_package_for_strategy` dropped `linked_only=True` on 2026-05-09 — so the writer's new "primary entry only" gating of `linked_trade_id` does not affect the open-package gate. Confirmed `_sweep_unlinked_packages` uses `WHERE linked_trade_id IS NULL` which still correctly sweeps packages that never executed.
- **Not yet verified**: CI on PR #2046 — every job is failing with `actions/checkout@v4` HTTP 403 due to the GitHub Actions auth incident that started 2026-05-26 10:57 UTC (https://www.githubstatus.com — "Incident with Actions and Pages"). Push-event workflows on `main` work (`adc6158` ran clean at 10:00 UTC pre-incident); `pull_request` and `issues.opened` event workflows are silent or 403 at checkout. Disambiguated with a clean-room test (PR #2048 from a fresh branch off main with one no-op file — same 403). Will retrigger once GitHub's mitigation lands.

## Documentation Updated
- Architecture doc: added 2026-05-26 row to `docs/ARCHITECTURE-CANONICAL.md` § Change log.
- Sprint log: this file.
- No `CLAUDE.md`, `docs/TRADE-PIPELINE.md`, or `ROADMAP.md` updates — the change is a journal-side data-correctness fix; it does not move the pipeline's contracts or any roadmap item.
- No subsystem doc updates needed — `docs/claude/*` doesn't enumerate `trades` schema columns.

## Contradictions or Drift Found
- None introduced by this PR.
- Pre-existing: the comment on `_log_trade_to_journal`'s `update_order_package` block referenced `pipeline.py::_has_open_package_for_strategy, linked_only=True`. That gate dropped `linked_only=True` on 2026-05-09 (per the `strategy_monocle.py` source comment). The new comment block in this PR phrases the dependency more accurately ("the strategy_monocle gate") so the comment no longer carries the stale flag name — but I did not separately edit the `pipeline.py` reference because the actual code path through `strategy_monocle` is unchanged by this PR and the comment is now superseded.

## Risks and Follow-Ups
- Remaining technical risks:
  - The legacy `order_packages.linked_trade_id` writer for the primary leg is unchanged, so the existing `_sweep_unlinked_packages` 5-min-orphan watchdog continues to function the same way. The new column does NOT change which packages get swept — only which trade-row → package id lookups succeed for the reconciler.
  - The migration is additive (`ALTER TABLE … ADD COLUMN`, nullable, no backfill). Pre-existing rows resolve via the legacy fallback. A future migration that backfills `order_package_id` for old rows (joining `order_packages.linked_trade_id` → `trades.id`) would let us remove the fallback entirely; not in scope here.
- Remaining product decisions: none.
- Blockers:
  - GitHub Actions auth incident (2026-05-26 10:57 UTC, status "Identified" + mitigation in progress at 12:37 UTC per githubstatus). Holds PR #2046's CI green and therefore the operator merge gate.

## Deferred Items
- Backfill `trades.order_package_id` for historical rows from `order_packages.linked_trade_id` — easy follow-up, lets the resolver drop the legacy fallback. Not urgent because the fallback is correct and cheap.
- `intent_reduce → parent` join in the reconciler so `position_size` updates on the parent open trade row when a reduce leg fires (the Known-gaps entry at `docs/ARCHITECTURE-CANONICAL.md` line ~747). This PR makes those reduce-leg rows resolvable to their package but does NOT join their qty back to the parent trade. Still the right design: wait for the first live conflict (Turtle Soup vs VWAP or any ict_scalp event) so the matching heuristic is grounded.

## Next Recommended Sprint
- Suggested next sprint: once CI clears + PR #2046 merges + deploys, verify on the next multi-leg fanout that the orphan-sweep ping shows `Package: pkg-…` on every leg (not `(unlinked)`). Open BL-20260526-* in the health-review backlog if any leg still surfaces `(unlinked)`.
- Why next: the only remaining piece is operational verification — code paths are tested locally and via the new pytest module; the live confirmation comes from observing the next reconciler sweep.
- Required verification before starting: confirm CI is healthy again on `main` (push-event runs succeeding); confirm PR #2046 squash-merged on `main` and the live VM redeployed (next `Database()` instantiation runs the additive migration; nothing else to do on the VM side).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — N/A: the change is journal-side wiring, not a pipeline contract change.
- [x] Roadmap status was checked. — no roadmap item moves; this is a data-correctness fix.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly (live-CI green pending GitHub Actions incident mitigation).
