# Sprint Log: S-M20-LADDER-AND-COLLAPSED-STATES-2026-08-09

## Date Range
- Start: 2026-08-09
- End: 2026-08-09 (sweep still running detached at session end — see Deferred)

## Objective
Continue the M20 exit-refinement workstream: give the eight live `ict_scalp`
legs a real `exit_ladder` verdict instead of a harness gap, scope `exit_head_ml`
with the operator, and settle `BL-20260809-COLLAPSED-STATES-NO-CANONICAL-HOME`.

## Tier
Tier 1 throughout. Research harness + sweep tooling, docs/JSON, one new CI
guard. No `config/`, no `src/`, no order path, no DB write, no VM state change.
The backtest harness places no orders.

## Starting Context
The prompt described the coverage matrix as "now ONE ROW PER LEG: 50 rows /
400 cells". **That is on PR #8693, not on `main`** — `main` carried 23 rows /
184 cells. It also said "Branch: … (restart from main)", but #8693 is **open,
not merged**, so restarting would have discarded three unmerged commits. Per
the git-ops rule (restart applies only to an *already-merged* PR; unmerged
commits are kept), I continued on the existing branch. Verified before acting.

## Repo State Checked
- `origin/main` @ `55c3455e`, later `5f525c74` (merged in mid-session).
- PR #8693 open, `mergeable_state: behind`, head matched the branch.
- Coverage matrix on-branch: 50 rows / 400 cells / 47 live legs — confirmed by
  arithmetic, not by the prompt's claim.

## Files and Systems Inspected
- `scripts/backtest_ict_scalp.py`, `scripts/backtest_trend.py`,
  `scripts/backtest_pullback.py`, `scripts/backtest_squeeze.py`,
  `scripts/backtest_fvg_range.py` (CLI surfaces read by **AST**, not `--help` —
  the sandbox had no pandas, so `--help` tracebacks; see Validation).
- `scripts/research/m27/ict_scalp_exit_sweep.py`,
  `scripts/research/m20_fleet_exit_sweep.py`, `m20_exit_sweep.py`,
  `m20_exit_head_round.py`.
- `config/strategies.yaml` — all eight `ict_scalp` leg blocks, diffed.
- `docs/research/exit-refinement-coverage.json`, the M20 memo,
  `docs/CLAUDE-RULES-CANONICAL.md`, `docs/claude/coordination-board.md`.
- Trainer VM via `trainer-vm-diag` (#8696 data inventory, #8697/#8700/#8701 sweep).

## Work Completed
1. **`exit_ladder` lever ported into `backtest_ict_scalp.py`** — `bank_frac` /
   `bank_at_r`, identical semantics **and** rung-before-stop ordering to
   `backtest_trend.py`/`backtest_pullback.py`, so a ladder verdict compares
   across harnesses. `banked` is stamped on every `_simulate_exit` return path
   and read strictly; the summary echoes `banked_trades`/`banked_pct` (the
   rung-fill denominator).
2. **Sweep made leg-aware and config-exact** — `--leg` resolves symbol /
   timeframe / `tp_at_r` / declared exit levers from `config/strategies.yaml`;
   `--cells` filters by matrix lever; ladder rungs are **fractions of the leg's
   own `tp_at_r`**.
3. **Collapsed states promoted to canonical + enforced** (operator-approved):
   new `docs/CLAUDE-RULES-CANONICAL.md` section, `CLAUDE.md` mirror, and a new
   `collapsed-state-guard` registered in `scripts/ci/run_guards.py`.
4. **Matrix corrected** — 8 `exit_ladder` cells off the now-false
   `blocked:no_harness_levers`; two **absent** `vol_trail` cells filled.
5. Memo § 10; two backlog rows filed; `BL-20260809-COLLAPSED-STATES` closed.

## Validation Performed
- **27 tests pass** (19 in the lever suite, **6 new**), incl. an AST test that
  *counts* `_simulate_exit` return sites and asserts every one stamps `banked`.
- **Byte-identical default** with the lever off, end-to-end, over a **non-empty**
  trade population. My first attempt produced **0 trades**, where "identical"
  would have been **vacuously true** — the unasserted-denominator trap, caught
  by asserting the denominator before believing the result.
- **`collapsed-state-guard` negative-control tested**: injecting an undeclared
  state makes it fail; it returns clean on restore. A guard never seen to fail
  is not evidence.
- `ruff` clean on the **CI-pinned 0.15.x** (the repo pins `<0.16`; a 0.16.x
  install reports ~10k findings against this codebase — compare error *sets*,
  never totals).
- `canonical-doc-coherence` passes. CI `guards` **passed on the pushed head**.
- Trainer probes carried **positive controls** — the data-inventory probe listed
  67 files and matched every other leg's CSV, so the MGC absence is a real
  negative and not a broken probe; the sweep launch asserted `--bank-frac`
  present on the trainer's own checkout before starting.

## Documentation Updated
- `docs/CLAUDE-RULES-CANONICAL.md` — new § "Collapsed states".
- `CLAUDE.md` — mirror beside number/diagnostic provenance.
- `docs/research/M20-exit-refinement-2026-07-12.md` — § 10.
- `docs/research/exit-refinement-coverage.json`.
- `docs/claude/health-review-backlog.json` — 1 closed, 2 filed.

## Contradictions or Drift Found
- **The prompt's premise about the matrix location was wrong** (50 rows are on
  the open PR, not `main`) and its "restart from main" would have discarded
  unmerged work. Verified rather than followed.
- **The matrix roll-up divided by a denominator counting cells that did not
  exist** — `squeeze_breakout_4h` and `fvg_range_15m` were missing `vol_trail`
  entirely, so "376 live cells" over-counted by 2. Found by arithmetic while
  editing. This is the collapsed-states class *in the artifact that measures
  M20's own done-condition*, found the same hour the class became canonical.
- **`ict_scalp_mgc_15m`'s existing verdicts are not reproducible** — the XAU
  proxy dataset they were measured on is gone from the trainer.
- The M27 sweep's "every ict_scalp leg is a config-exact copy" comment is true
  for *detection* geometry (verified by diff) but **not** for policy: one leg
  ships its own exit lever, another ships regime `off_cells` the harness cannot
  reproduce.
- **I overwrote the coordination board's issue body** (`issue_write
  method=update` instead of `add_issue_comment`). Restored from
  `docs/claude/coordination-board.md`, which that doc designates as the board's
  body of record; all 635 comments were unaffected, and the restored body says
  what happened.

## Risks and Follow-Ups
- `BL-20260809-XAU-PROXY-DATASET-GONE-VERDICTS-UNREPRODUCIBLE` (Tier-1).
- `BL-20260809-SCALP-HARNESS-LOADS-BTC-YAML-FOR-EVERY-LEG` (Tier-1) — safe
  today by coincidence of config, not by code.
- `BL-20260809-THIRD-CASE-AND-UNTESTED-BRANCH-RULES` remains **open**: its
  second rule ("a green suite over an untested branch is not evidence") was
  deliberately **not** folded into the new canonical section — it is a testing
  rule and the operator's call. The section says so explicitly.

## Deferred Items
- **`exit_ladder` verdicts for the 7 data-reachable legs.** The sweep is
  running **detached** on the trainer and did not finish in-session — each leg
  is ~10 harness runs for IS/OOS plus up to 30 more for the walk-forward,
  4-concurrent on **one** core, over 164k–647k-bar frames. Re-sequenced
  cheapest-first (15m before 5m) so partial completion yields usable verdicts;
  `run_cell` caches per (cell, window), so the reorder cost nothing.
  **No verdict was pre-written from the prior.**
- **`exit_head_ml`** (operator scoped: equities E0→E1 + `ict_scalp` datasets).
  **Queued, not started** — the trainer is single-core and the VM lane is held
  by the ladder sweep; FIFO says running is never preempted. Driver exists
  (`m20_exit_head_round.py`); exact command posted on board #6927. Consider
  routing the equities round to a **free GH runner** (the `*_1d` frames are
  ~100–200 KB; the crypto ladder sweep legitimately needed the VM because
  re-fetching would change the data vintage every prior verdict used).

## Next Recommended Sprint
Collect the ladder verdicts and record them in the matrix + memo § 10.5 in the
same PR, then run the equities exit-head round.
- Required verification before starting: poll
  `runtime_logs/m20_ladder/2026-08-09/*/verdicts.json` on the trainer; read
  each cell's `banked_pct` **beside** its ΔR — a cell whose rung never filled
  is INERT, not a negative, and the two must not be recorded alike.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched (`docs/TRADE-PIPELINE.md` N/A).
- [x] Roadmap status was checked (M20 row updated by the preceding commit on
      this branch; this sprint's deltas are in the memo + matrix).
- [x] Contradictions were recorded — including my own two errors.
- [x] Remaining unknowns stated: **the ladder verdicts themselves are not yet
      known.** The expected result is negative, and that expectation is
      recorded as a prior with its mechanism examined — not as a result.
