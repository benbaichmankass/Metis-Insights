# Sprint Log: S-M31-VERIFY-AND-COVERAGE-2026-08-16

Continuation of [`S-M31-POSITION-TELEMETRY-2026-08-16.md`](S-M31-POSITION-TELEMETRY-2026-08-16.md),
whose *Next Recommended Sprint* was **"verify P2 in production, then P3"**. This
log is that verification — and what the verification found.

## Date Range
- Start: 2026-08-16 (overnight, operator asleep)
- End: 2026-08-16

## Objective
- **Primary goal:** verify M31 P2 in production rather than assume it from a
  merge, and answer the operator's standing question about the exit monitor on
  both halves — *is it built* and *is it deployed*.
- **Secondary goals:** measure whether each live leg's own unit module
  implements the exit levers it declares; re-sweep the queued `trail_decay_arm_r`
  values at live parity; leave the morning a single readable decision memo.

## Tier
- **Tier 1**, with one Tier-2 fix inside it.
- Justification: the coverage probe, the memo, the sprint logs and the registry
  record are docs/tooling/observability. `position_telemetry.account_id`
  (#9660) is a **Tier-2** writer change to a live-loop module and shipped under
  the pre-granted Tier-2 authority. **No Tier-3 value was changed** — every arm
  is queued for the operator, and `check_lever_reachability.py` asserts each one
  unchanged.

## Starting Context
- Active roadmap items: M31 (position telemetry), M20 (exit refinement).
- Prior sprint reference: `S-M31-POSITION-TELEMETRY-2026-08-16.md` (P1+P2 built,
  merged, **unverified in production**).
- Known risks at start: the P2 writer runs on the live exit loop — the June 2026
  wedge class is *"individually cheap, sum unwatched"*, so the first post-deploy
  read of `offloop_hooks` mattered more than the test suite did.

## Repo State Checked
- Branch/commits: `main` at `ca81c512` → `41f9f046` → `c986a70c` → `84a2e40f`.
- Deployment state: read from the live VM via the `vm-diag-snapshot` relay
  (#9673, #9674, #9675). **`bot_uptime_s` / `process_started_utc` were used as
  the deploy evidence, never `git_sha`** — `git_sha` reads the working tree and
  can report a SHA the running process is not executing.
- Canonical docs reviewed: `CLAUDE-RULES-CANONICAL.md`, `CLAUDE.md`
  (§ Collapsed states, § Dashboard REST API, § `EXIT_LOOP_*`), `ROADMAP.md`.

## Files and Systems Inspected
- Code: `src/runtime/position_telemetry.py`, `src/runtime/trail_decay.py`,
  `src/units/strategies/{trend_donchian,htf_pullback_trend_2h}.py`,
  `src/runtime/strategy_signal_builders.py`, `src/runtime/exit_loop_health.py`.
- Config: `config/strategies.yaml` (read-only), `config/lever_reachability.json`.
- Docs: the M31 sprint log, `docs/claude/health-review-backlog.json`.
- Live surfaces: `/api/diag/tick_cost`, `/api/diag/status`,
  `/api/diag/log_file?name={exit_loop_health,exit_interval_soak}`,
  `/api/bot/db/table/position_telemetry`.

## Work Completed

### 1. A defect I shipped, found on the FIRST post-deploy read (#9660, Tier-2)

`position_telemetry.account_id` was **structurally unpopulatable**: the record
built it as `account_id or open_pkg.get("account_id")`, and `order_packages` has
no such column, while `monitor(cfg, candles_df, open_pkg)` has no account in
scope. All 12 live rows were `NULL`.

**The tests could not have caught it** — they asserted the field round-trips,
which it does. Only the live journal could show the column was never fed.

Fixed by resolving the account from `trades.account_id` via the `trade_id` the
row already carries, plus
`account_id=COALESCE(excluded.account_id, {TABLE}.account_id)` on the upsert so
**pre-existing rows backfill** rather than staying null forever.

**My first fix was wrong and the tests said so.** A correlated subquery in the
`INSERT` made the whole telemetry write depend on a join, and two existing
persistence tests failed because their fixture DB has no `trades` table — i.e.
the fix would have made the writer fail closed on any DB shape lacking that
table. Restructured to a guarded Python resolution that returns `None` on any
`sqlite3.Error`; both tests then passed **unmodified**. I deliberately did *not*
"fix" the tests by teaching their fixture about `trades` — they were right.

### 2. Exit-mechanism coverage: the prior question nothing asked (#9633)

`check_lever_reachability.py` (M31 P1) asks whether a *declared* R-threshold
lever can arm under its TP cap. `scripts/ops/exit_mechanism_coverage.py` asks
the question before it: **does the leg's own unit module implement that lever at
all?**

Five states, never collapsed — `not_implemented` (the module has no such lever)
vs `undeclared` (it does; the leg opts out) are opposite statements about whose
choice it was, and `unresolved` is *"we could not look"*, never *"no"*.

**Result: zero orphaned declares over 46 of 47 resolved legs.** The denominator
is stated because a clean count over an unstated denominator is not a clean
count — `ict_scalp_5m` does not resolve and is ungraded.

The probe **self-tests against the real modules**: a coverage probe that cannot
find a known positive proves nothing, so `--self-test` asserts
`trend_donchian` *does* implement `stale_stop` and `htf_pullback_trend_2h` does
*not*. If either flips, every "clean" result the tool has ever produced is void.

### 3. The finding that mattered more than the verification

| mechanism | module lacks it | implemented, leg opts out | declared |
|---|--:|--:|--:|
| `stale_stop` | 19 | 24 | 3 |
| `giveback_stop` | **26** | 19 | **1** |
| `exit_head` | 26 | 17 | 3 |
| `trail_decay` | 8 | 23 | 15 |

`htf_pullback_trend_2h` — **18 of 47 live legs** — implements exactly **one** of
the four M20 mechanisms. `squeeze_breakout_4h` implements **none**.

This explains the live XRP short structurally rather than anecdotally: it runs
`xrp_pullback_2h` → `htf_pullback_trend_2h`, whose only mechanism is
`trail_decay`, and on that leg `trail_decay_arm_r: 4.49` sat **above** its
`cap_R` for most entries. **The trade had no working M20 exit mechanism for 18
days.** That is not a mis-declaration — the leg declares only what its module
reads, so it grades `ok` on the orphan check. It is a coverage gap, and the two
are worth telling apart.

### 4. The M20 levers have fired 13 times, ever

`stale_stop` 10 · `exit_head` 2 · `giveback_stop` 1 — and the single
`giveback_stop` firing is on a **paper** account. Against 1,142 closed trades,
with `reconciler_filled` at 44.6% (the exchange bracket is the dominant exit
path, as designed).

**This reframes "are the mechanisms performing well at strategy level": there is
not enough live history to answer it.** n=2 and n=1 are not evaluable. The
backtests are the evidence base; the live journal is not, yet. It is also the
sharpest argument for M31 — a lever's effect has to be measured from the
**counterfactual on every trade**, not from 13 firings.

### 5. The arm_r re-sweep, and a trainer re-prioritisation that paid for itself

A broad fleet sweep was at leg 7 of 55 after ~4 h with every queued leg at index
21–52 — `xrp_pullback_2h` was **~25 hours away**. Replaced with a narrow
live-parity sweep (`--p80-only --tp-cap-pct 0.099 --split-target-oos 50`) over
just the six legs declaring `trail_decay_arm_r`: **all six answered in 4
minutes.**

**4 of 6 fail at live parity**, and the one PASS proposes an arm that is *itself*
above that leg's measured `cap_R`. Full table + the contradiction in
`docs/claude/m20-m31-operator-decisions-2026-08-16.md` § 3 and in
`config/lever_reachability.json::live_parity_p80_resweep_2026_08_16`, recorded
**beside** the reachability verdict it disagrees with rather than replacing it.

## Validation Performed
- **Tests:** `tests/test_exit_mechanism_coverage.py` 13 new;
  `tests/test_position_telemetry.py` +5 (`TestAccountIdIsResolvedNotAccepted`);
  `test_lever_reachability_audit.py` 24 passed;
  `check_lever_reachability.py --self-test` 10/10;
  `exit_mechanism_coverage.py --self-test` 5/5. CI green on every merged PR.
- **Live verification (the point of this sprint):**
  - **P2 cost** — `monitor.position_telemetry` in `offloop_hooks` at
    **n=306, mean 6.4 ms, max 55.1 ms** against a ~23.6 s exit pass. Bounded.
  - **#9660** — 12 of 12 rows carry `account_id` across five accounts
    (`bybit_1`, `bybit_2`, `alpaca_paper`, `alpaca_portfolio`, `ib_paper`),
    `order_state: "applied"` so the count is trustworthy. The decisive evidence
    is the **backfill**: `pkg-a687f228480e4f96` read `null` at 12:03 and
    `alpaca_paper` at 12:09 — the `COALESCE` update path repairing a
    pre-existing row, confirmed against the live journal rather than a fixture.
  - **The motivating trade, fully attributed:** `xrp_pullback_2h` / trade 4163 /
    **`bybit_2` (real money)** — `peak_r 3.4179`, `cap_r 3.9233`,
    `levers {"trail_decay_arm_r": 4.49}`, `bars_held 200`,
    `rr_from_here 0.6329`. The arm-above-cap defect readable off one row.
  - **Exit loop** — `exit_loop_health` `state: fresh`; `exit_interval_soak`
    accruing.
- **Manual code verification:** every `arm_r` in the registry asserted unchanged
  **programmatically**, not eyeballed.

### Gaps not yet verified
- **The exit-interval requirement is not settled.** Measured n=61 intervals on
  one process: max **50044.3 ms (83.4%** of the 60 s requirement), **zero
  breaches**. But the worst reading on record (58.9 s) came from an n=694
  overnight process, and no daytime process has lived long enough to draw that
  tail. `requirement_state` must be read beside `intervals_measured` — it read
  `within` over **three** intervals on the new process.
- **~34 rows of `exit_interval_soak` were truncated** by the relay's
  55000-byte budget and I did not read them, so the genuine **cross-process**
  max is unread. Inferring it spans processes from restart times is not a read.
- **The `gld_pullback_1d` contradiction is unresolved.** The p80 is over
  backtest winner MFEs (134 trades); `cap_R` is over 8 live order packages.
  Either the live entries are unrepresentative of the leg's vol regime, or the
  backtest population's `risk/entry` differs systematically. Both testable;
  **neither tested.**
- The two `skipped` legs (`qqq_trend_long_1d`, `scha_trend_long_1d`) are
  **absence of evidence, not evidence of failure** — as unmeasured as before.

## Documentation Updated
- `docs/claude/m20-m31-operator-decisions-2026-08-16.md` — the consolidated
  morning memo (new), then § 3 replaced with the full re-sweep table.
- `config/lever_reachability.json` — the `live_parity_p80_resweep_2026_08_16`
  block per leg + a top-level `_resweep_note`.
- `docs/claude/health-review-backlog.json` — two rows (612 → 614).
- This log.

## Contradictions or Drift Found
- **`trainer-vm-heavy-request` triggers no workflow.** The label exists and is
  guard-enforced; nothing consumes it. A heavy job dispatched exactly as the
  skill instructs is **silently discarded** — cost ~50 min of trainer time here,
  and I had already reported the work as done on the board before noticing.
  Filed `BL-20260816-TRAINER-HEAVY-LABEL-TRIGGERS-NO-WORKFLOW` (high).
- **The diag relay double-prefixes a slashless `api/diag/…` path** to
  `/api/diag/api/diag/<path>`, returning a bare 404 indistinguishable from a
  missing route. I nearly filed *"the entire `/api/diag/*` surface is down"*;
  what refuted it was a **positive control in the same relay run** — an
  `api/bot/…` path returned real data while all four diag paths 404'd. Filed
  `BL-20260816-DIAG-RELAY-DOUBLE-PREFIXES-A-SLASHLESS-API-DIAG-PATH` (medium).
- **My own board release mislabelled the merge slot.** I posted "slot is FREE"
  while another session's claim for #9663 was still open; corrected on the board
  before another session could merge over them.
- **A "~26.6 s shared timeout ceiling" hypothesis I raised was refuted** by a
  concurrent session's cluster-tightness test (a confirmed timeout clusters at
  0.001% spread; mine at 1.837%). Retracted publicly. Recorded because the
  retraction is the useful part.
- **I described five queued registry entries as "three arm_r corrections"** in
  overnight pings. Three are measured-bad and need a decision; two are
  **unmeasured** and need measurement, not a decision. Corrected in the memo § 0.

## Risks and Follow-Ups
- **Technical:** the coverage table is a snapshot. A lever moving between
  modules silently changes every verdict, which is why
  `test_htf_pullback_implements_only_trail_decay` asserts the measured asymmetry
  the whole finding rests on — if it fails, the docs quoting it are stale.
- **Tier-3 queued (all five, none acted on):** `gld_pullback_1d` (inert, 0 of 8
  over its complete history), `qqq_trend_long_1d` (inert), `xrp_pullback_2h`
  (vol-conditional) — decisions; `trend_donchian_sol_4h`, `scha_trend_long_1d` —
  measurements. Plus the `--tp-cap-pct` default flip.
- **The larger call the re-sweep surfaced:** for at least four of six legs the
  answer may be *"none — the lever should not be declared on this leg"*, which
  is bigger than a value change and is the operator's.

## Deferred Items
- **M31 P3 readers** — `/api/bot/positions` R-fields, a diag surface, the
  exit-ladder input. Now unblocked: the table is confirmed populating.
- **M31 P4 backtest↔live MFE parity** — the largest item. Every bug in the
  tp-cap family is *"the harness measured a book production does not run"*, and
  there has still never been a live measurement of the same quantity to check
  against. Needs the table to accrue first.
- Reading the untruncated `exit_interval_soak` for a genuine cross-process max
  (owned by the concurrent exit-eval-interval session).

## Next Recommended Sprint
**M31 P3 readers, and the `gld_pullback_1d` population question.** P3 is
unblocked and cheap. The population question is the one that decides whether the
re-sweep's numbers can be used at all: compare `risk/entry` across the backtest
population and the live order packages for one leg. Until that is answered, a
p80 arm and a `cap_R` are two numbers about two different books, and taking
either at face value is how a second inert arm ships wearing a PASS badge.

## Wrap-Up Check
- [x] Code inspected directly (file:line, not recalled)
- [x] Canonical docs reviewed and updated
- [ ] TRADE-PIPELINE updated — **N/A**, no pipeline stage changed
- [x] Roadmap checked (M31 already recorded by the prior sprint)
- [x] Contradictions recorded, **including my own three**
- [x] Unknowns stated rather than smoothed over — see *Gaps not yet verified*
