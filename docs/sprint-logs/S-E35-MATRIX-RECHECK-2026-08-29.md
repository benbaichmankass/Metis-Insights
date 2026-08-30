# Sprint Log: S-E35-MATRIX-RECHECK-2026-08-29

## Date Range
- Start: 2026-08-30T04:52Z (board START)
- End: 2026-08-30T05:10Z

## Objective
- **Primary goal:** the work the operator's ruling unblocked — re-check the stale-ref
  `bracket_geometry` matrix cells against the 2026-08-29 corpus, and read the
  reverse-direction legs.
- **Secondary goal:** leave the check executable rather than a one-off reading.

## Tier
- **Tier 1.** Records + tooling. No `src/`, no `config/`, no order path, no VM action.
- The operator's "yes" accepted an **evidence substitute** for a failed control. It did
  not approve a config change, and none was made.

## Starting Context
- #10441 merged (`bd08cecf`); corpus 8,289 keys.
- Its § 4 left a stop-and-ask: the CLEAN-leg control failed as specified, and the
  fold-level diagnosis rests on 11 of 3,515 differing cells.
- Deferred behind it: the stale-ref cells and the reverse-direction legs.

## Repo State Checked
- `origin/main` at `bd08cecf`; branch `claude/e35-stale-ref-recheck`.
- Board #6927 tail proven by a SHORT page (41 of 100) before the START; merge slot free.

## Files and Systems Inspected
- `docs/research/e35-bracket-corpus.jsonl` (8,289 rows, 41 legs, 4 run dates)
- `docs/research/exit-refinement-coverage.json` (52 rows)
- `scripts/ci/check_matrix_bracket_values.py`, `scripts/research/m20_fleet_exit_sweep.py`
  (`PROXY_DATA`, `resolve_data`), `config/strategies.yaml` (read only)

## Work Completed
- **`scripts/research/e35_matrix_recheck.py`** (new, self-test 8 cases) — staleness as a
  three-state question, shippability by timeout axis, the reverse-direction split, and
  `--b4-outcome`.
- **Matrix corrected:** stale refs **30 → 0**; unshippable `passed_unshipped` claims
  **4 → 0**; 10 legs flipped `honest_negative`/`blocked` → `passed_unshipped`; 16
  confirmed-but-stale refs refreshed; 2 `known_caveats` added. The 8 `shipped` cells were
  not touched and `matrix-bracket-values` stayed green throughout.
- **Write-up:** `docs/research/e35-matrix-recheck-2026-08-29.md`.
- **Filed:** `BL-20260829-MATRIX-BRACKET-VALUES-READS-ONLY-THE-BACKTICKED-CELL-SPELLING`.

## Validation Performed
- `run_guards.py --all` → **PASS 59 · FAIL 0**, plus the three diff-scoped guards against
  the PR diff.
- **`diagnostic-provenance-guard` caught a real defect in my own new tool** — it printed
  the count of ungraded legs without the denominator it ranges over. Fixed (now prints
  "40 of 52 … every count above ranges over the 40 graded legs only"), not overridden.
- A consistency assertion inside the update: every leg in the "confirmed" set was checked
  to have **no** shippable winner, so the flip set and the confirm set partition cleanly.
- **Gaps:** nothing on the live fleet was observed. All of this is corpus reading.

## Contradictions or Drift Found
- **The alarming reading was the wrong one.** All 8 B4-shipped legs show zero passing
  cells, which reads as regression. It is the expected signature of an absorbed lever —
  the base is config-exact. Confirmed by arithmetic: the base rose by the claimed
  improvement, 3 of 8 exact to 4dp, all 8 within 0.89–1.03×, median 1.00.
- **`mgc_trend_1h` and `xauusd_trend_1h` are one measurement**, identical on all 199
  cells (both → `GC_F`). Deliberate and correct, but it makes the control **n=1
  independent leg**, not 2. Recorded as a caveat.
- **The earlier "19 stale-ref cells" is not reproducible** from what was recorded; the
  measured figure under a stated definition is 30. Superseded, not reconciled.
- **The earlier "9 reverse legs, sol holds 7" must not be re-quoted** — measured on the
  contaminated pre-re-sweep corpus. It is 10 legs; sol holds 2 shippable.

## Risks and Follow-Ups
- **Tier-3, open:** 15 shippable passing cells across 10 legs. Not applied.
  `OI-20260829-E35-REVERSED-LEGS-ARE-A-TIER-3-PROPOSAL-SET-NOT-APPLIED` (`loud`).
- **The § 1 control still needs re-specifying** — pin the window so a re-run is a pure
  function of code + data, or every future plan asserting identity repeats the false alarm.

## Deferred Items
- The Tier-3 proposal set (above).
- The guard-regex widening (filed, deliberately not done in a research PR).
- **12 matrix legs carry no corpus rows at all** — ungraded, not clean. Unchanged by this
  work and worth its own pass.

## Next Recommended Sprint
- Put the Tier-3 set to the operator per leg, or move to Lane P (P1/P2) per N-D4.
- **Required verification before starting:** read § 1 of the write-up. Every verdict here
  inherits an accepted-substitute caveat, not a passing control.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched, so `docs/TRADE-PIPELINE.md` is unchanged.
- [x] Roadmap status checked; M20 row updated.
- [x] Contradictions recorded.
- [x] Remaining unknowns stated with their denominators.
