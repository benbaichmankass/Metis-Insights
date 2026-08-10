# Sprint Log: S-M20-LADDER-AND-COLLAPSED-STATES-2026-08-09

## Date Range
- Start: 2026-08-09
- End: 2026-08-10 (see the 2026-08-10 addendum: ladder verdicts landed, and
  a first-principles claim this log's § 10/§ 11 work had asserted was retracted
  on measurement)

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
5. Memo § 10; two backlog rows filed;
   `BL-20260809-COLLAPSED-STATES-NO-CANONICAL-HOME` closed.

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
- ~~**`exit_ladder` verdicts for the 7 data-reachable legs.**~~ **DONE
  2026-08-10** — see the addendum below. (The original entry said the sweep was
  "running detached on the trainer"; that run was **killed and re-routed**, so
  the text is corrected rather than left to read as the record.)
- **`exit_head_ml`** (operator scoped: equities E0→E1 + `ict_scalp` datasets).
  **Queued, not started.** The VM-lane contention noted here is **gone** — the
  ladder sweep was re-routed off the trainer entirely (addendum), so nothing
  holds the lane. Driver exists (`m20_exit_head_round.py`); exact command
  posted on board #6927. Route the equities round to a **free GH runner** on
  the pattern the ladder sweep just proved: the `*_1d` frames are ~100–200 KB,
  and scp-ing the pinned frame from the trainer keeps the data vintage
  identical to every prior verdict in the row (the failure mode that made the
  first ladder attempt unusable).
  **57 of the 376 live cells remain `pending`** — by lever: `exit_head_ml` 31,
  `vol_trail` 10, `giveback_stop` 7, `trail_decay` 6, `exit_ladder` 2
  (`squeeze_breakout_4h`, `fvg_range_15m` — neither is an `ict_scalp` leg),
  `trail_geometry` 1. `exit_head_ml` is now the single largest open block in
  M20's done-condition by a factor of three.

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

---

## Addendum — 2026-08-10: the ladder verdicts, and a retraction

### What ran
Re-routed off the trainer (1 OCPU, ~12–20 h serial) onto **7 parallel free GH
runners** — `ict-scalp-exit-sweep.yml`, run **31344328313**, 7/7 jobs
`success`, ~38 min wall clock, $0. Data is `m27_data/<SYM>_<TF>.csv` scp'd from
the trainer: the SAME population every other M27 verdict in these rows used. An
earlier trainer attempt had silently picked up `data/` (2021+) instead, which
would have made these verdicts incomparable with their own row.

### Result — population: 7 of 8 legs, 4 cells each, 28 cells
**26 `honest_negative`; 2 cells on `ict_scalp_sol_15m` cleared the gate** (IS
and OOS beat on net_R AND maxDD) and then survived the yearly walk-forward 3/4
usable folds. Coverage matrix live-cell processing **312 → 319 of 376** —
delta exactly 7, cross-checked by arithmetic against `git show HEAD:` rather
than by re-reading the file.

The two SOL cells are a **Tier-3 PROPOSAL**, not a ship. Nothing in this PR
touches `config/strategies.yaml`.

### The correction — I was wrong, and my own run proved it
Hours before the results returned, memo § 11.1 argued from first principles
that banking **necessarily** lowers net_R and lowers maxDD, so no banking cell
could ever pass: *"P(pass) = 0, a priori."* Measured over the 28 cells:

| claim | measured |
|---|--:|
| necessarily lowers net_R | net_R **rose** in 6/28 IS, 8/28 OOS |
| lowers maxDD | maxDD **rose** (worse) in 7/28 IS, **14/28** OOS |
| P(pass) = 0 | **2/28 passed**, incl. walk-forward |

The argument followed only the **winner** side of the distribution. On a fixed
1.5R bracket with a 1.0R rung, banking also turns a loser that first printed
+1R from −1R into −0.5R — so it can *earn* net_R. And maxDD is a property of
the equity **path**: capping the biggest winners removes the recoveries that
used to end drawdowns, so drawdown deepened on 12 of the 16 5m OOS cells.

This is the RULE ONE failure mode named in the rule itself — *"verify your own
output too, hardest when it confirms what you expected."* The claim felt like a
derivation, so it was written into a canonical memo as a structural fact and
never queued for measurement. § 10.1 had **already** noted the fat-tail premise
does not transfer to a capped-upside strategy; § 11.1 asserted the general form
anyway, one section later.

Corrected in place, not deleted (the claim was acted on, so it needs a findable
home): memo § 11.1 carries a strike-through + retraction banner, § 10.6 is the
measurement, `m20_banking_risk_adjusted.py`'s docstring is rewritten, and
`BL-20260810-BANKING-GATE-CANNOT-PASS` is rescoped under its original id. The
§ 6.2 **measurements** were never in doubt and stand — what fell is the claim
that they *had* to come out that way.

Net effect on the finding: banking on this fleet is a **stronger** negative than
"the gate couldn't adjudicate it." On three of the four 5m legs it lost on
**both** axes out-of-sample.

### Also fixed this pass
- `artifact-validity-guard` caught `m20_banking_risk_adjusted.py` missing from
  `RESEARCH-CAPABILITY-INDEX.md` — a tool a session cannot find. Indexed.
- The verdict-posting step had logged `core.warning` and exited 0 when it could
  not resolve the PR, so all 7 jobs went green having delivered nothing. It now
  resolves the PR from `context.sha` and `core.setFailed`s. Verdicts for this
  run were recovered from the job logs.

### Guards
33 PASS / 1 FAIL — the failure is `layer-guard`, exit **127**: `lint-imports`
is not installed in this sandbox (`which` confirms). Not a finding; CI has it.
