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
- **Tier 1** for everything shipped in the PR. The one **Tier-2** item — running
  the backfill with `--apply` against the money DB — was proposed with exact
  projected numbers, **approved by the operator in-conversation**, and then run
  and verified within this session.
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
- **Nothing deferred.** G3 was PARTIAL mid-session and was then completed: one
  owner (`src/web/api/_pnl_provenance.py`) wired into all five remaining
  aggregates. `backtests.py` and `pnl_broker_truth.py` are exempt — verified,
  neither reads journal `pnl`.
- Repairing the two truncated markers as a standalone job — unnecessary; the
  backfill subsumes it.

## Next Recommended Sprint
- Suggested next sprint: **Lane B**, whose top item (B1, the non-crypto candle
  feed on a runner) is the single highest-leverage remaining item in the plan —
  it unblocks 25 M20 cells and M31 P5 precondition 3b.
- Why next: **GATE 0 is cleared** — all of G1–G7 shipped this session, so Lanes
  B/C/D are unblocked for the first time.
- ⚠️ **Required framing before starting:** "cleared" means the instruments now
  state their own provenance, NOT that the numbers are good. The re-derived
  exit-path split still puts cleanup machinery at **671 of 1,363 (49.2%)** of
  closes, and real-money `pnlCoverage` is **0.768** against **0.425** across all
  accounts (n=1,187). Acting on what the instruments now say is the next job.

## Addendum — GATE 0 cleared, then Lane B / B1 measured (same session)

With GATE 0 cleared (PR #10343, squash `d20ff991`) the lanes unblocked, and the
plan's own ordering puts **B1** first. It was **stale in the same way G1 was**,
and the correction is a different one than the plan anticipates.

**B1 is not "build a feed".** The non-crypto lane was proven on a runner on
2026-08-24 and `e35-bracket-sweep.yml` already fetches per-leg through it.
Established by running the sweep's **own** planner, not by reading prose.

**Population: the 25 cells recorded `blocked:no_free_lane_candle_feed` in
`docs/research/exit-refinement-coverage.json` (`updated_at` 2026-08-24T15:55Z).
All 25 sit in ONE lever column — `bracket_geometry` — across 25 distinct legs.**

| | before | after |
|---|---|---|
| schedulable by the planner | 21 of 25 | **22** of 25 |
| …resolve after the workflow's own fetch | 17 | **22** |
| …fetch-then-fail | **4** | **0** |

### The defect: a green job that measured nothing

The workflow wrote `data/{SYMBOL}_{tf}.csv`; `m20_fleet_exit_sweep.resolve_data`
applies `PROXY_DATA` (`MES→ES_F`, `MGC`/`XAUUSD→GC_F`, `MHG→HG_F`)
**unconditionally, with no native fallback**, so it looked only for the proxy
stem. Two definitions of where a leg's candles live.

Reproduced by invoking the sweep exactly as the workflow does, with
`MES_1d.csv` on disk — this is the whole finding:

```
plan: 0 legs runnable, 1 skipped
  SKIP mes_trend_long_1d: data_missing:MES
EXIT CODE = 0     report.json -> legs= 0
```

The leg pays the fetch, the job **passes**, and the artifact holds a report with
an empty `legs` list.

⚠️ **A second, independent defect made it unobservable.** `aggregate` counted
`report.json` **files** — an empty report is still a returned one, so the count
could equal `planned`, the shortfall warning never fired, and the table (which
iterates `legs`) printed no row. **4 legs vanished from a summary that read as
complete coverage.** Notable because it is the unasserted-denominator class this
same workflow *already guards against elsewhere in its own file*: the guard
existed and simply did not cover "the report is real and the measurement is
absent".

### What shipped

- `e35_shard_plan.data_basename` — the ONE definition of the stem, **derived
  from `fleet.PROXY_DATA`** rather than restated, and carried in the matrix so
  the workflow cannot hold a second opinion.
- `PROXY_DATA` symbols route to **yfinance** (operator-approved). Those stems
  mean the yfinance full-size contract, and `yf_symbols` maps `MES→ES=F`,
  `MGC`/`XAUUSD→GC=F`, `MHG→HG=F` — exactly that series. Dukascopy carries more
  depth but would write an S&P **index CFD** and **spot** XAU under names
  asserting the futures contract.
- `aggregate` counts legs **MEASURED**, not reports returned, and **names** each
  skipped leg's reason.

**Cost stated rather than hidden:** yfinance caps 1h at ~730 d, so the two 1h
legs take a PARTIAL window against the sweep's 1830 d request. The fetcher
already clamps and says so on stderr, so a short span is never silently read as
a full one.

⚠️ **`MHG` joined the servable set as a CONSEQUENCE of applying the rule
uniformly, not as a separate decision.** It is in `PROXY_DATA` and `HG=F` is the
honest `HG_F` series; its prior refusal was about Dukascopy (whose only
catalogue hit was a Norwegian salmon farmer), never about the leg. `QLD`/`TQQQ`
are **not** in `PROXY_DATA`, so the rule does not reach them and they stay
refused — correctly: a daily leverage reset means the path is not N × the
underlying.

### Validation

- Each fix confirmed to **FAIL against the pre-fix code** and pass against the
  fix — the naive stem reverted (1 test fails), the rollup reverted (3 fail).
- `test_the_naive_stem_is_what_used_to_break` is a **positive control**: it
  asserts the old derivation genuinely fails, so the main assertion cannot go
  vacuous if `PROXY_DATA` is ever emptied. It also names the failing set rather
  than counting it.
- The rollup test **lifts its python out of the shipping YAML** rather than
  copying it (the `test_merge_slot_guard.py` discipline) — a pasted duplicate
  would pass while the thing CI runs drifted.
- Planner self-test 44/44; `run_guards.py` **PASS 41 · FAIL 0**; `ruff` clean on
  the pinned 0.15.8.

⚠️ **Local `python3 -m ruff` is NOT the pinned ruff** — it reported 12,924
repo-wide errors against the `ruff` binary's 1. `requirements-dev.txt` pins
`>=0.15.0,<0.16` precisely because 0.16 expanded the default ruleset. Use the
binary; a sandbox `-m` invocation can resolve to a different install.

### Not done, deliberately

**The 25 cells are still blocked.** Nothing here measured a bracket. The
coverage matrix must not be flipped until `e35-bracket-sweep.yml` is dispatched
and returns verdicts — marking cells resolved on a code change would assert a
measurement nobody took. The reachable ceiling on this lever is **22 of 25**,
not 25.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched; `docs/TRADE-PIPELINE.md` unchanged.
- [x] Roadmap status was checked (GATE 0 lives in the workplan; updated there).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
