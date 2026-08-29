# Sprint Log: S-NIGHT-N3-N7-E35-RESWEEP-2026-08-29

## Date Range
- Start: 2026-08-29T21:30Z
- End: 2026-08-29T23:20Z

## Objective
- **Primary goal:** execute items **N3–N7** of
  [`../claude/WORKPLAN-NIGHT-2026-08-29.md`](../claude/WORKPLAN-NIGHT-2026-08-29.md) —
  the autonomous overnight continuation of Lane B (M20 Active Trade Management), which
  the operator scoped as *"focus on active management"*.
- **Secondary goals:** close the guard gap B4 exposed (a matrix column asserting
  geometry nothing checked), decide the D1b live-time-stop question in writing, prepare
  the B6 split, and get the 41-leg e35 re-sweep to actually land its rows.

## Tier
- **Tier 1** throughout.
- **Justification:** one new CI guard + its tests, one design proposal, one prep packet,
  one workflow-branch fix, one research script + write-up, and the corpus data itself.
  No `src/`, no `config/`, no order path, no VM action. **No verdict change was applied
  to `config/strategies.yaml`** — that is Tier-3 and was explicitly out of scope for the
  night, stated as such in the plan and again in the N7 write-up.

## Starting Context
- **Active roadmap items:** Lane B / M20. Lane A (Alpaca go-live, M15) calendar-blocked
  until Monday's US open — untouched this session.
- **Prior sprint reference:** `S-M20-B9-TIMEOUT-BARS-BLAST-RADIUS-2026-08-29` (the same
  session's earlier unit) and `S-ALPACA-T1-AND-WORKPLAN-2026-08-29`.
- **Known risks at start:** B4 (#10419, `91de68b9`) had already shipped validated
  bracket geometry to 8 live legs on real money while the matrix still carried all 8 as
  `passed_unshipped`, and `matrix-config-agreement` stayed green throughout — so the
  column was asserting something no guard could contradict.

## Repo State Checked
- **Branch or commit reviewed:** `origin/main` at `a986ac3` at the start of N2; branch
  `claude/e35-resweep-n2` for the corpus + N7 work.
- **Board state:** #6927 tail proven by a **SHORT page** — `perPage=100` at page 17
  returned **41 items** — last entry my own `🔓 RELEASE` at 22:16:15Z, slot free.
- **Deployment state reviewed:** none mutated. No VM action taken this session.

## Files and Systems Inspected
- **Code:** `scripts/ci/run_guards.py`, `scripts/ci/check_matrix_config_agreement.py`,
  `scripts/research/timeout_binding_audit.py`.
- **Config:** `config/strategies.yaml` (read only — the 14 B4 annotations and their cell ids).
- **Docs:** `docs/strategy-coverage-matrix.md`, `docs/research/exit-refinement-coverage.json`,
  `docs/research/RESEARCH-CAPABILITY-INDEX.md`,
  `docs/design/exit-mechanism-construction-PROCESS.md`.
- **Data:** `docs/research/e35-bracket-corpus.jsonl` at both revisions (`a986ac3` and `c2641827`).
- **Workflows:** `.github/workflows/e35-bracket-sweep.yml`.

## Work Completed
- **N3 / B10 — `matrix-bracket-values` guard (#10437, `f4e750ab`).** Parses each matrix
  cell id (`tp3_sm2`, `to24`) and asserts the named axes against the leg's live
  `config/strategies.yaml` values. Three states, never collapsed: `value_mismatch` /
  `unreadable` (**a FAILURE, not a pass**) / `undeliverable_axis` (a `to*` component can
  never be `shipped`, because no live unit implements a bar-count exit). Registered in
  `run_guards.py` immediately after its sibling, with a header saying why the sibling
  structurally cannot cover this column. 6 tests. **Proven against real data**: perturbing
  `uso_trend_1h`'s `atr_stop_mult` 2.0 → 2.25 made it exit 1 naming the leg, the cell and
  both numbers; restored byte-identically.
- **N5 — D1b live time-stop proposal (#10438, `9eec434`).** **Recommendation: do not
  build a live bar-count exit.** Corrects the workplan's own population from 4 legs to 2.
  Documents the stale `eth_pullback_2h` matrix cell, and states what such an exit would
  have to look like if ever built (rests nowhere → inherits exit-loop liveness as a
  *correctness* dependency; bars counted per-leg from closed bars; reader and declaration
  shipped together; annotate before it acts; four non-collapsed states including
  `unknown_bar_count`). Five falsifiers stated.
- **N6 — B6 split packet (#10439, `f0b42342`).** Confirms the 12 `passed_unshipped` cells
  are 4 `bracket_geometry` (B4's) + exactly 8 B6 cells splitting 6+2. B9's precondition
  applied per leg: 5 of 6 named legs CLEAN; `ict_scalp_sol_15m` plus 2 shadow-fleet rows
  are **ABSENT from the corpus, i.e. ungraded, not clean**.
- **N2 / N7 — the e35 re-sweep.** Fixed the workflow to write to a per-run branch
  (#10440, `9dc72492`), re-dispatched from a working branch, and the run succeeded:
  corpus **8,211 → 8,289 keys, 0 keys lost** (`c2641827`). Then built
  `scripts/research/e35_resweep_verdict_diff.py` + the write-up + its index row (#10441).

## Validation Performed
- **Tests run:** `scripts/ci/run_guards.py --all` → **PASS 58 · FAIL 0 · SKIP 0**, plus
  the three diff-scoped guards (`api-tier-policy`, `test-schema-fidelity`,
  `diagnostic-provenance`) run explicitly against the PR diff — all clean. `ruff check .`
  clean. `e35_resweep_verdict_diff.py --self-test` OK (8 cases, including a positive
  control where equal `base`/`to400` must NOT register).
- **Manual code verification:** the new guard was checked against a deliberately
  perturbed real config value, not only against fixtures.
- **Gaps not yet verified:** the N7 control's diagnosis rests on fold-level evidence over
  **11 of 3,515** differing cells. Nothing on the live fleet was observed this session.

## Documentation Updated
- **Subsystem docs:** `docs/design/d1b-live-time-stop-PROPOSAL.md` (new),
  `docs/design/b6-split-packet-2026-08-29.md` (new),
  `docs/research/e35-resweep-verdict-diff-2026-08-29.md` (new),
  `docs/research/RESEARCH-CAPABILITY-INDEX.md` (one row).
- **Plan doc:** `docs/claude/WORKPLAN-NIGHT-2026-08-29.md` — N2/N7 **re-corrected** in
  #10436; two § 6 lessons added ("one observed case is not the contract"; "a mitigation
  living only in a resolved row's prose is a mitigation nothing enforces").
- **Open items:** `docs/claude/OPEN-ITEMS.json` +1 row (see below).

## Contradictions or Drift Found
- **The matrix column had no checker.** `matrix-config-agreement` verifies a cell's
  *presence*, never its *values*, so B4 could ship geometry to 8 live legs while all 8
  read `passed_unshipped` and CI stayed green. Closed by N3.
- **19 matrix cells carry corpus rows from later runs while their ref still cites the
  superseded 2026-08-20 run**, and 2 of those assert `passed_unshipped` on a winner the
  newer rows do not reproduce. Recorded, **deferred** on the § 4 decision.
- **My own workplan text was wrong twice about where a `main`-dispatched sweep writes**,
  and both corrections are in #10436 rather than left standing.

## Risks and Follow-Ups
- **Remaining product decisions (Tier 3):** whether to accept the fold-level evidence in
  place of the failed clean-leg control, and thereafter whether any verdict change lands
  in `config/strategies.yaml`. Neither was taken.
- **Blockers:** the § 4 decision blocks the 19 stale-ref cells and the 9
  reverse-direction legs.
- **Filed:** `OI-20260829-E35-RESWEEP-LANDED-BUT-ITS-CLEAN-LEG-CONTROL-FAILED`
  (`loud: true`).

## Deferred Items
- The **19 stale-ref matrix cells** — gated on the § 4 decision.
- The **9 reverse-direction legs** (matrix negative, corpus now passing;
  `trend_donchian_sol_4h` holds 7). **Deliberately not read**: 7 of 9 are
  timeout-contaminated and were being re-measured, so reading them now would be the exact
  error B9 exists to prevent.
- **B7, B8, and Lane P (P1/P2)** — untouched; P1/P2 is the next daytime priority per N-D4.
- **Lane A** — calendar-blocked until Monday.

## Next Recommended Sprint
- **Suggested next sprint:** put the § 4 question to the operator, then either work the
  19 stale-ref cells or move to Lane P (P1/P2).
- **Why next:** every remaining Lane-B verdict item is downstream of that one ruling, and
  proceeding without it would apply verdicts on a control that failed.
- **Required verification before starting:** re-read
  `docs/research/e35-resweep-verdict-diff-2026-08-29.md` § 4 — it says explicitly that the
  failed control does **not** license proceeding as though it passed.

## Mistake Recorded
I dispatched the first 41-leg sweep from `main` against a documented mitigation that says
not to (`BL-20260826-E35-CORPUS-BRANCH-STRANDED-1629-MEASURED-CELLS-AND-CANNOT-ACCEPT-NEW-PUSHES`
records *"dispatch the e35 sweep from a WORKING BRANCH, never `main`"*). The run swept all
41 legs, produced 42 green artifacts, then **discarded every row** at the corpus-commit
step. Cost: one 43-minute run; nothing lost (artifacts expire 2026-09-28). I had not read
that row before dispatching. The durable fix is #10440 — the workflow now writes to a
per-run branch, so the mitigation no longer lives only in a backlog row's prose.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] This sprint touched no pipeline stage, so `docs/TRADE-PIPELINE.md` is unchanged.
- [x] Roadmap status was checked (Lane B / M20; no milestone row moved — nothing shipped
      to the fleet).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly, with their denominators.
