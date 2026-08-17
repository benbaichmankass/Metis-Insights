# S-M31-HARNESS-MFE-DIST — the harness half of P5 precondition 2

## Date Range
- Start: 2026-08-17
- End: 2026-08-17

## Objective

M31 P5's binding blocker is precondition 2: P4 Check B abstains because the live
final-MFE population is **n=1 fleet-wide**. That is soak depth nobody can hurry.

But Check B needs **two** inputs — a live final-MFE population **and** a harness
`mfe_r` distribution — and the second was missing for an entirely different
reason: it was simply never committed. A session waiting only on live depth
would have reached the floor and *then* discovered the other half absent.

Ship the mechanism for the harness half. Do **not** ship the numbers — the
distinction is the finding.

## Tier
- Tier: **1** (research tooling + CI wiring + docs)
- Justification: no `src/`, no `config/`, no order path, no live lever, no
  Tier-3 decision. **P5 itself remains Tier-3 and withheld.**

## Starting Context
- Active roadmap items: M31 (position telemetry) — P4 Check B / P5 precondition 2.
- Prior sprint reference: `S-M31-P4-MFE-PARITY-2026-08-17.md` (which filed this
  gap), `S-M31-P5-RR-FLOOR-HARNESS-2026-08-17.md` (this session's earlier unit).
- Known risks at start: the obvious implementation walks into M31's own defect
  class — generating the distribution from the only committed candle fixture,
  which is the wrong volatility regime by an order of magnitude.

## Repo State Checked
- Branch or commit reviewed: branched off `main` `6b0fb8e`; merged as `55ba0f3`.
- Deployment state reviewed: **n/a — nothing here deploys.** No `src/` file is
  touched, so the live trader is unaffected and no restart or post-state check
  applies.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md`, `ROADMAP.md` § M31,
  `docs/design/m31-p5-telemetry-reading-lever-PROPOSAL.md`, coordination board
  #6927 (tail read and proven, plus a `list_pull_requests` cross-check).

## Files and Systems Inspected
- Code files inspected: `scripts/research/m31_mfe_parity.py`,
  `scripts/backtest_trend.py`, `scripts/research/m20_fleet_exit_sweep.py`
  (`resolve_data` only — not modified), `scripts/ops/check_research_index.py`.
- Config files inspected: `.gitignore` (`data/*.csv`), `ruff.toml`,
  `requirements-dev.txt` (ruff pin).
- Deployment files inspected: none.
- Docs inspected: `RESEARCH-CAPABILITY-INDEX.md`,
  `docs/claude/performance-review-backlog.json`,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Services or timers inspected: none.
- GitHub Actions workflows inspected: `guards.yml` (ruff pin + the diff-scoping
  step), `scripts/ci/run_guards.py`.

## Work Completed

- **Item 1 — verified the premise rather than inheriting it.** A key census over
  **all 1,376** rows of `docs/research/m20-sweep-corpus.jsonl` finds **zero**
  keys containing `mfe`. Reproduces the backlog claim exactly. Two incidentals
  worth the next session's time: the corpus keys on **`leg`** while
  `--harness-emit` rows key on **`strategy`**, and every corpus row carries
  `tp_cap_pct`.
- **Item 2 — `scripts/research/m31_harness_mfe_dist.py`** (new): aggregates a
  `backtest_trend.py --emit-trades` JSONL into a small committed per-leg record
  — percentiles + `n`, never per-trade rows, per the backlog row's own criteria
  (*"small, versions with the corpus, does not need a sweep to be
  reproducible"*). Refuses an uncapped sweep; requires `--symbol`/`--timeframe`.
- **Item 3 — `m31_mfe_parity.py --harness-dist`**: consumes that artifact,
  mutually exclusive with `--harness-emit`. Three consequences of a *committed*
  artifact: the **`tp_cap_pct` gate becomes PER-LEG** (an artifact can hold legs
  swept under different settings, and one uncapped leg must neither condemn nor
  be excused by its neighbours); `harness_source` travels into every record; and
  `harness_symbol`/`harness_timeframe` reach the report, which is what makes a
  wrong-instrument comparison *visible* rather than merely wrong.
- **Item 4 — one definition.** Percentiles are **imported** from
  `m31_mfe_parity._pct`, never re-derived — the same rule that made
  `backtest_trend.py` import `r_distances` from the live telemetry module.
- **Item 5 — CI wiring + capability index.** `mfe-parity-instrument-guard` runs
  **both** self-tests and globs both files; the new script is routed in
  `RESEARCH-CAPABILITY-INDEX.md` (77/77).

## Validation Performed

- Self-tests: parity **14/14** (10 pre-existing + 4 new), aggregator **8/8**.
- **End-to-end on a real capped sweep**, not a fixture of a fixture: 144 emit
  rows, **144/144** carrying `mfe_r` → aggregator → artifact → parity returned
  `parity_state: compared`, `harness_side: committed_dist`.
- **Agreement test**: the committed distribution and the raw emit rows it was
  built from produce identical parity verdicts. If they disagreed, the artifact
  would be a quiet re-measurement rather than a record.
- 14 pytest cases; every refusal paired with a positive control.
- **43/43 guards on the COMMITTED diff.** The runner explicitly warned that
  uncommitted paths are not a clean bill of health, so the suite was re-run
  after committing. `diagnostic-provenance` + `api-tier-policy` run explicitly
  against a freshly generated `origin/main...HEAD` diff (per
  `BL-20260814-RUN-GUARDS-CONSUMES-A-DIFF-IT-NEVER-GENERATES-SO-LOCAL-RUNS-SCAN-A-STALE-FILE`). `ruff check .`
  clean on the **pinned** `<0.16` ruff — the unpinned install reported 103
  findings from 0.16's expanded default ruleset, which the repo documents.
- Post-merge: verified on `main` by **reading the files** and re-running, not by
  trusting the merge SHA.

### Mutation-tested — and one mutation found a defect in my own test

| mutation | result |
|---|---|
| collapse the per-leg cap to the global flag | parity test 13 FAILS ✅ |
| treat a zero-`n` leg as comparable | parity test 14 FAILS ✅ |
| re-derive `_pct` locally (byte-identical) | one-definition test FAILS ✅ |
| drop the `--symbol`/`--timeframe` requirement | aggregator test 8 FAILS ✅ |
| **delete the uncapped-sweep refusal** | **PASSED — the test was wrong** ❌ |

The last is the useful one. The test pointed `--emit` at a nonexistent
`x.jsonl` and asserted `rc == 2` — and a missing file returns `2` on its own, so
it held with the guard **deleted**. It was passing for a reason it was not
testing: **an exit code that a different failure also produces.** Rewritten
against a real emit file with a positive control (*a capped sweep with identity
IS written*) ahead of the refusals, so they cannot pass by refusing everything.
Re-run under the same mutation, it now fails.

A second: `test_percentiles_are_the_parity_modules_own` failed on first run
because the test loaded `m31_mfe_parity` twice under different module
identities — it was measuring the test's own loading strategy, not the
production wiring. Fixed to hold the module the aggregator's import resolves to.

## Documentation Updated
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` — new row; the sibling
  `m31_mfe_parity` row's now-stale "both halves are missing" corrected.
- `docs/claude/performance-review-backlog.json` — `PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE`
  updated with the measurement and a sharpened `next_action`; **left OPEN**.
- `ROADMAP.md` § M31 — the split recorded.
- This log.

## Contradictions or Drift Found
- The capability-index row for `m31_mfe_parity` claimed *"both halves are
  missing"* — true when written, stale once the mechanism landed. Corrected in
  the same PR rather than left to a later sweep.
- ⚠️ **The first version of this very log carried 7 of the 14 mandatory
  sections** (`docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`). Caught post-merge and
  rewritten. My *earlier* log this session had all 14, so this was a regression
  within one session, not an unfamiliarity with the format.

## Risks and Follow-Ups
- **`PB-20260817-SPRINT-LOG-TEMPLATE-HAS-NO-GUARD`** (filed): the sprint-log
  format is documented as **mandatory** and **nothing enforces it** — searched
  `scripts/`, `tests/`, `.github/` for `SPRINT-LOG-TEMPLATE-CANONICAL` and found
  zero references, with the template file itself as the positive control that
  the probe works. Measured drift on recent logs: 15, 10, 10, and mine at 7.
- `PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE` stays open pending the
  artifact.
- The aggregator will accept a fixture-derived distribution if given the right
  flags. `--symbol`/`--timeframe` make that visible downstream, but they do not
  make it impossible; the guard is honesty-of-record, not prevention.

## Deferred Items
- Generating the artifact: a **capped** (`--tp-cap-pct 0.099`) trainer-side
  sweep per telemetry-writing leg — `trend_donchian` + `htf_pullback_trend_2h`
  **only**, since no other leg writes `position_telemetry` and no other leg is
  comparable — with `--emit-trades`.
- Deferred because `data/*.csv` is gitignored: measured, not assumed —
  `m20_fleet_exit_sweep.resolve_data` returns `(None, False, None)` for all of
  `SOLUSDT/4h`, `XRPUSDT/2h`, `BTCUSDT/1h`, `ADAUSDT/2h`, `QQQ/1d`. The only
  committed candles are **BTCUSDT 1-MINUTE** (median `(high−low)/close`
  **0.101%**), where the 9.9% cap lands at **~37R** against live legs at cap_R
  **2.13–5.83**. Committing that would put a wrong-regime artifact under the
  exact name Check B reads — M31's own defect class, authored by us and
  **versioned**, which is worse than the honest absence it replaces.

## Next Recommended Sprint
1. The trainer-side capped sweep above → commit `m31-harness-mfe-dist.jsonl`.
   Precondition 2 then waits only on live soak depth.
2. `PB-20260817-RR-FLOOR-UNMEASURED-ON-LIVE-REGIME-DATA` (precondition 3b) —
   the same trainer data unblocks both, so they should share one sweep.
3. The sprint-log template guard, if a review session wants a cheap one.

## Wrap-Up Check
- [x] All work committed and merged (`55ba0f3`), verified on `main` by reading
      files and re-running — never by a merge SHA.
- [x] Backlog updated; the row that is not resolved is **not marked resolved**.
- [x] Coordination board: START, holding note, claim, release.
- [x] Telegram ping sent.
- [x] No live path touched; **P5 remains Tier-3 and withheld**, and precondition
      2's binding half (live soak depth) is untouched by anything here.
