# Sprint Log: S-GATE0-G1-EXIT-LABEL-RECLASSIFICATION-2026-08-26

## Date Range
- Start: 2026-08-26
- End: 2026-08-26

## Objective
- Primary goal: close **GATE 0 / G1** — finish the exit-label re-classification
  (`docs/claude/WORKPLAN-2026-08-26.md`), rows
  `BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE` and
  `BL-20260822-EXIT-ATTRIBUTION-UNDER-REPORTS-BRACKET-HITS`.
- Secondary goals: re-derive `perExitPath`, whose coverage is computed over the
  mislabelled buckets; leave the Tier-2 half stated and measured rather than
  silently skipped.

## Tier
- **Tier 1** for everything shipped. The one Tier-2 item — running the backfill
  with `--apply` against the money DB — was deliberately **NOT** done and is
  handed to the operator with exact projected numbers.
- Justification: a protected-key tuple, a script's `sys.path`, additive
  read-surface fields, tests, and docs. No order path, no config, no VM mutation.

## Starting Context
- Active roadmap items: GATE 0 (G1 open, G3 open, G5 blocked on G1).
- Prior sprint reference: `S-GATE0-MEASUREMENT-TRUST-2026-08-26` (PRs #10339,
  #10340). Its closing section states the gate is **not** cleared.
- Known risks at start: the handoff and the workplan both describe G1 as
  unstarted work. Both were checked rather than believed — correctly, as it
  turned out.

## Repo State Checked
- Branch or commit reviewed: `origin/main` = `da5a7d6d`; branch
  `claude/exit-label-reclassification-g1-b9kyip` cut from it.
- Deployment state reviewed: `/api/diag/version` → `git_sha da5a7d6d`,
  `git_sha_on_disk da5a7d6d`, `restart_pending false`. The deployed code is main.
- Canonical docs reviewed: `CLAUDE.md` (incl. the generated SESSION-BRIEF block —
  nothing due), `docs/claude/OPEN-ITEMS.json` (4 rows), the workplan, the prior
  sprint log, coordination board #6927 (tail proven by a short page).

## Files and Systems Inspected
- Code files inspected: `src/runtime/order_monitor.py` (all six late-price write
  sites), `src/runtime/provenance.py`, `src/utils/json_notes.py`,
  `scripts/ops/backfill_exit_labels.py`, `scripts/ops/backfill_exit_labels_action.sh`,
  `src/web/api/routers/performance.py`.
- Docs inspected: workplan, `CLAUDE.md` API contract, sprint log, backlogs.
- Live systems inspected: `trade_journal.db` via the Caddy diag host
  (`/api/bot/db/table/*`), replicated in full for an offline dry run.

## Work Completed

**1. Corrected the workplan's G1 premise before writing any code.** The row says
*"the Bybit-sweep path is still open"*. It was not: `_sweep_pending_pnl_from_bybit`
has re-run `_classify_broker_exit` since **#10262, merged 2026-08-25**, a day
BEFORE the workplan was written. Established with `git log -S` on the marker, not
by reading the comment. #10262 also shipped `scripts/ops/backfill_exit_labels.py`
and the Tier-2 `backfill-exit-labels` action. The plan's own number-one blocker
was described from a stale reading — the class GATE 0 exists to stop, committed
by the gate's own charter.

**2. Found the backfill tool INERT and fixed it.** `sys.path` walked two levels
from `scripts/ops/`, landing on `scripts/`, so `import src` raised
`ModuleNotFoundError` and the script could not run from anywhere — including the
Tier-2 wrapper, which invokes it by absolute path with no `cd` and no PYTHONPATH.
Every one of the eight sibling backfills uses `parents[2]`; this was the only one
that did not. Fixed to match.

**3. Found a third, unreadable provenance state and fixed it.**
`dump_capped(notes, 500)` trims the longest *unprotected* string value, and
`exit_reason_source` was absent from `_DEFAULT_PROTECTED` — which protects its two
siblings `pnl_source` and `exit_price_source`. Two live rows store
`"price_vs_p…"`, matching neither the resolved sentinel nor `unresolved`.

**4. Published per-exit-path LABEL attestation on `/api/bot/performance`** — four
counts (`labelAttested` / `labelRefused` / `labelUnresolved` / `labelUnattested`)
that partition `trades` exactly. **No ratio**, deliberately: an AUTHORED path
never reaches the classifier, so a `labelCoverage` of 0.0 would read as a gap on a
path that has none.

**5. G3 first slice — `/api/bot/stats` now states its own provenance.** The
headline surface both apps render first published a sum and a rate over journal
`pnl` with no coverage at all, while `/performance` has carried `pnlCoverage`
since 2026-07-31. Added `pnlCoverage` / `pnlMeasuredCount` / `pnlEstimatedCount`
/ `totalPnLMeasured` to the real-money block and, separately, to the `paper`
sub-block (P4: never blended — a paper book with perfect coverage must not
flatter the real-money caveat). `/performance`'s definitions are **imported, not
re-derived**, so the two surfaces cannot disagree.

**6. Filed two backlog rows** through `backlog_append.append_row` (both accepted
by the G6 similarity gate as novel).

## Validation Performed

- **Tests run:** `tests/test_json_notes.py` (15), `tests/ops/test_backfill_exit_labels.py`
  (5), `tests/test_performance_exit_label_attestation.py` (5),
  `tests/test_stats_pnl_provenance.py` (6, new), plus
  `tests/test_performance_per_exit_path.py`, `test_performance_pnl_coverage.py`,
  `test_ltmgmt_p4_metric_separation.py` and `test_dashboard_data_contract.py`
  (pre-existing). **All green.**
- **A pre-existing test that my change broke was STRENGTHENED, not loosened.**
  `test_stats_missing_db_zeroes_both_blocks` asserted whole-dict equality on the
  `paper` block, which additive keys break. It now asserts the four money keys
  exactly (so a real real/paper blend still fails it) plus the new keys' values.
  Writing it surfaced a genuine inconsistency in my own helper: a MISSING DB
  returned the all-`None` "we could not look" shape, when `_pnl_stats_for`
  already reads that same state as "no trades yet on a fresh install". Split
  into `_looked_and_found_nothing()` (real zero counts, `None` ratio) vs the
  all-`None` could-not-look shape.
- **Every new test verified to FAIL without its fix**, not merely to pass with it:
  - the truncation tests fail on the pre-fix `_DEFAULT_PROTECTED`;
  - the backfill subprocess tests fail on the original `sys.path` line, while the
    three importlib-based branch tests pass **either way** — which is precisely
    why no existing test caught this, and is documented in the test file.
- **`scripts/ci/run_guards.py`: PASS 42 · FAIL 0** on committed work.
- **Dry run against a full replica of the live journal.** All 5,056 `trades` and
  4,064 `order_packages` rows were pulled through the Data Explorer (every query
  asserting `filter_state: applied`) and rebuilt into SQLite **with the real
  schema types** read from `/api/bot/db/tables` — the first attempt declared every
  column `TEXT` and silently scanned 0 rows, because `COALESCE(is_backtest,0)=0`
  fails on a TEXT column. The typed replica reproduces the live counts exactly.

```
eligible 481   (scanned 1,122 closed non-backtest non-reduce)
  relabel -> sl   144   (119 measured basis, 25 estimated)
  relabel -> tp    47   ( 32 measured basis, 15 estimated)
  REFUSED         105   fabricated price
  unresolved      185   (105 measured, 80 estimated)
ROWS THAT WOULD CHANGE exit_reason: 191
```

- **Cross-check:** an independent provenance tally taken BEFORE running the script
  (256 classifiable on a MEASURED price, 120 on an ESTIMATED one) matches the
  script's own split exactly — so the projection is not the script grading its own
  homework.
- **Manual code verification:** all six `exit_price` write sites in
  `order_monitor.py` were read; every late-price path re-classifies going forward.
- **Cost measured, not assumed:** classifying the full closed population through
  `provenance.classify_pnl` costs **19.8 ms median / 434 KiB** — cheap for a
  30s-polled route. That measurement is why the design classifies in Python
  through the canonical module instead of re-deriving the vocabulary in SQL.
- **Gaps not yet verified:** the new `/performance` and `/stats` fields are
  proven against synthetic journals only — they have not been read off the
  deployed routes, because the code is not deployed until this PR merges.

## Documentation Updated
- Rules doc updates: none needed.
- Roadmap updates: none — GATE 0 is tracked in the workplan.
- Subsystem doc updates: `CLAUDE.md` `/api/bot/performance` row (the four new
  fields + why no ratio is published); `docs/claude/WORKPLAN-2026-08-26.md` (a
  **G1 CORRECTION** block, the status column, the session log).
- Backlog: 2 rows filed in `docs/claude/health-review-backlog.json`.

## Contradictions or Drift Found
- **The workplan's G1 row contradicted the code**, by one day. Corrected in place
  rather than routed around.
- **The backfill's own docstring reported measured numbers** (2026-08-23, 497
  eligible) for a script that has never been able to run. The numbers came from an
  analysis done alongside it; the tool was never executed. Not a false claim, but
  it reads as evidence of a working tool.
- **A near-miss worth recording:** this session was one step from writing
  `scripts/ops/backfill_exit_labels.py` from scratch — the file already existed
  and was found only because a `grep` for the refusal constant listed it.
  `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`, avoided by the existence check
  `CLAUDE.md` mandates.

## Risks and Follow-Ups
- Remaining technical risks: the two rows carrying a truncated marker still carry
  it. The backfill's `--apply` would restamp them (a corrupted marker is not a
  value its idempotency check recognises), so no separate repair is needed.
- **Remaining product decisions (Tier 2, operator-gated):** run
  `backfill-exit-labels` with `apply:1`. It rewrites `exit_reason` on **191 rows**
  of the money DB, touches no monetary field, and records the prior value under
  `notes.pre_backfill_exit_reason` so it is reversible from the row itself.
- Blockers: **G5 stays blocked on G1** until that apply run lands — the 08-21
  plan's headline is computed off exactly these labels.

## Deferred Items
- **G3 is PARTIAL, not done.** `/api/bot/stats` shipped. Still uncovered, from
  a measured inventory: `attribution.py`, `pnl_history.py`, `strategies.py`,
  and the session-gated `pnl.py` (no consumer calls it). `backtests.py` and
  `pnl_broker_truth.py` are exempt — neither reads journal `pnl`.
- Repairing the two truncated markers as a standalone job — unnecessary; the
  backfill subsumes it.

## Next Recommended Sprint
- Suggested next sprint: the Tier-2 `backfill-exit-labels` apply run, then **G5**
  (re-state the 08-21 headline off corrected labels), then **G3**.
- Why next: G1's code half is done and G5 unblocks the moment the labels are
  corrected. G3 is the last open GATE 0 item.
- Required verification before starting: after the apply run, confirm
  `notes.pre_backfill_exit_reason` is non-zero on the live journal and re-read
  `perExitPath` — `labelAttestedCount` on `reconciler_filled` should move from
  its current 0 to ~191, and `labelRefusedCount` to ~105.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched; `docs/TRADE-PIPELINE.md` unchanged.
- [x] Roadmap status was checked (GATE 0 lives in the workplan; updated there).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
