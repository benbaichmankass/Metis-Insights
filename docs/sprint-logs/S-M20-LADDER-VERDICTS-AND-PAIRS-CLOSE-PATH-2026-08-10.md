# Sprint Log: S-M20-LADDER-VERDICTS-AND-PAIRS-CLOSE-PATH-2026-08-10

## Date Range
- Start: 2026-08-10
- End: 2026-08-10

## Objective
Collect the `ict_scalp` `exit_ladder` verdicts and record them; then, on the
operator's directive, generalize the "12-day ETH trade going nowhere" complaint
into a measured finding, fix what it exposed, and make capital efficiency a
shipping criterion.

## Tier
Mixed, all operator-approved where required:
- **Tier 1** — verdicts, coverage matrix, memo, research tooling, CI guard, docs.
- **Tier 2** — `src/units/strategies/pairs_executor.py` (order path). Operator:
  *"fix the pairs bug first"*.
- **Tier 3** — capital efficiency promoted from gate *tiebreak* to a **shipping
  criterion**. Operator: *"including making capital efficiency a shipping
  criterion"*.

`config/strategies.yaml` is **untouched**.

## Starting Context
Continuation of `S-M20-LADDER-AND-COLLAPSED-STATES-2026-08-09`. PR #8693 merged
mid-run, so the unmerged remainder was rebased onto `main` and opened as #8712.

## Work Completed

### 1. Ladder verdicts — 7 legs × 4 cells, and a retraction
Population: **7 of the 8 live legs** (mgc is `blocked:data_missing`), 28 cells.
Run 31344328313, 7 parallel free runners, 7/7 success, ~38 min, $0.

**26 `honest_negative`; 2 passed** — `ict_scalp_sol_15m` `bank0.25@1R` and
`bank0.5@1R`, both clearing IS+OOS on net_R AND maxDD and then surviving the
yearly walk-forward 3/4. Coverage 312 → 319 of 376, delta asserted as exactly 7.

**Every one of the 28 cells was independently reproduced** on a second run
(31346893856) after the rebase — all 7 legs, all numbers exact.

**§ 11.1 was retracted.** Hours before the results, I had argued from first
principles that banking *necessarily* lowers net_R and lowers maxDD, so nothing
of that shape could ever pass — *"P(pass) = 0, a priori."* Measured:

| claim | measured over 28 cells |
|---|--:|
| necessarily lowers net_R | net_R **rose** in 6/28 IS, 8/28 OOS |
| lowers maxDD | maxDD **rose** in 7/28 IS, **14/28** OOS |
| P(pass) = 0 | **2/28 passed**, incl. walk-forward |

The argument followed only the winner side of the distribution: on a fixed 1.5R
bracket with a 1.0R rung, banking also turns a loser that first printed +1R
from −1R into −0.5R. And maxDD is a property of the equity **path** — capping
the biggest winners removes the recoveries that used to *end* drawdowns.

### 2. The live stale-hold audit (operator directive)
Population: **all 32 open positions**, every account, hold normalized to each
strategy's **own bars**.

- `eth_pullback_2h` / **bybit_2 (real money)**: 12.4 days = **149 bars** on a 2h
  strategy, −0.33R. The operator's example, confirmed.
- **5 of 25** positions past 100 of their own bars; **all 5** declare no stall
  lever. **24 of 25** declare none at all.
- The naive fix is wrong and the same data proves it: `xrp_pullback_2h` is 139
  bars old on the same timeframe at **+3.94R**. The lever must be conditional.

### 3. The pairs sleeve had never closed a pair
Found while normalizing hold times: `pairs_bnb_btc` **declares
`max_hold_bars: 20`** and its legs were **300–595 bars** old.

Population: **all 2,471 soak rows**, `by_event` verified to sum to exactly 2471
so the breakdown is exhaustive: **29 `open`, ZERO `close`**; 958 (38.8%)
`skip_state_unreadable`.

**Root cause, proven not inferred.** `_open_pkg_meta` queried `order_packages`
on `account_id` and `id` — **two columns that table does not have**. Building
the table from the real DDL and running the exact query raises
`OperationalError: no such column: account_id`; a broad `except` swallowed it at
DEBUG. So the limit was never violated — it was never **read**.

Fixed three ways: the query; the read made **three-state**
(`found`/`absent`/`error`, registered with `collapsed-state-guard`); and a
persistent unreadable state now **alerts**, rate-limited per (pair, reason)
because the condition fires every tick and a per-tick alarm is itself the
desensitized-alarm P1.

### 4. Capital efficiency: extracted, then promoted to a shipping criterion
The metric was single-homed into **`scripts/capital_efficiency.py`** and both
harnesses wired to it — copying it into a second harness is the
two-definitions-that-drift shape that has cost this repo three incidents.

The P2 gate now has a **second qualifying path** (operator-approved): Path A
(net_R AND maxDD) unchanged; **Path B** — `net_r_per_capital_day` improves in
both windows, maxDD does not worsen, net_R falls no more than a declared floor.
Either path still passes the **same** walk-forward. A second door, never a
lower bar.

**Path B's thresholds are deliberately UNSET.** No sweep has reported the
distribution yet, and a threshold with no distribution behind it is the
exposure-ceiling mistake. Until the operator sets them from data, Path B
surfaces candidates for review rather than shipping them.

## Validation Performed
- 28/28 cells independently reproduced on a second run.
- Coverage delta asserted as **exactly 7** against `git show HEAD:`, not a re-read.
- Pairs fix **negative-controlled**: reverting the query makes 4 tests fail with
  the exact production error. 41 pairs tests pass.
- 8 new capital-efficiency tests incl. an **anti-drift** assertion that both
  harnesses import the shared module and neither keeps a local copy.
- Backlog merge conflicts resolved twice by taking `main` **verbatim** and
  re-appending only this branch's rows, id-set asserted each time (436 + 5 = 441).
- Guards green in CI after two self-inflicted failures (below).

## Contradictions or Drift Found
- **My own § 11.1 claim was false** and my own run disproved it. Retracted in
  place with a strike-through rather than deleted, because it had been written
  into a canonical memo and acted on.
- **The pairs tests were green against a fictional schema** — they declared
  their own `order_packages` with `id INTEGER PRIMARY KEY, account_id TEXT`,
  columns production does not have. They now lift the DDL from the module that
  owns it. This is "green is not evidence" in its exact canonical form.
- **A green run that delivered nothing**: the verdict-posting step warned and
  exited 0 when it could not resolve the PR, so 7 jobs went green having posted
  zero verdicts. Now `core.setFailed`s.
- **Truncated backlog ids, third instance this session** — a long id
  hyphen-broken across a wrapped docstring line resolves to a row that does not
  exist. Caught by `artifact-validity-guard` in CI, not locally, because my
  local `run_guards.py` invocation ran a *different* sub-check than CI does.
- `impossibility-claim-guard` fired on the word "unmeasurable" in a new index
  row. Correct strictness; reworded.

## Risks and Follow-Ups
- **The pairs fix is verified in CODE ONLY.** The proof is a non-zero `close`
  count in the live soak after deploy.
- `BL-20260810-NO-STALL-EXIT-CAPITAL-SITS-IN-DEAD-TRADES` — the sweep that sets
  Path B's thresholds has not run.
- `BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED` — deploy + verify, then the 6
  stranded legs.
- Ship `ict_scalp_sol_15m` `bank0.5@1R`: **approved, not built.** It is not a
  config edit — the live ExitPlan path is journalled for soak and nothing reads
  it back to drive an order (M20 P4).

## Wrap-Up Check
- [x] Code inspected directly, not inferred from summaries.
- [x] Documentation updated as part of the sprint.
- [x] Contradictions recorded — including four of my own errors.
- [x] Remaining unknowns stated: the pairs fix is unverified live; Path B's
      thresholds are unset and must come from data, not from me.
