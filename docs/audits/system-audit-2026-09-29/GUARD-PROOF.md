# GUARD-PROOF — plant-a-defect proof of the unproven CI guards (E75, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane GUARD-PROOF, JC-SA-07 option (a). Evidence: [`GUARD-PROOF.findings.jsonl`](GUARD-PROOF.findings.jsonl) (121 rows, parses; 97 are final, 24 superseded history).

## Population
- Registry: **97** guards (`python3 scripts/ci/run_guards.py --list`, base `e2d8f84`). The first wave (#14175) saw 95; two guards were added since.
- Wave 1 (#14175) plant-proved 25, marked 2 unplantable and did not run 10. The 48 that CA-B05/B06 labelled `proven-fails` (meaning only "has a `--self-test`") and the lead's 10 were excluded by instruction there. **This lane (GUARD-PROOF-2) re-proved all of them by plant**, so the population is the whole registry.
- Unproven going in: 97 - 25 caught - 2 unplantable = **70** (the 10 not-run + 60 others). One (`account-class-guard`) was done first as a tool smoke test, so 70 = 1 + 69 plants.

## Result (over all 97 guards, final row per guard)
| caught | missed | unplantable | invalid | not run |
|---:|---:|---:|---:|---:|
| **92** | **0** | **5** | **0** | **0** |

92 + 0 + 5 + 0 + 0 = 97. Of the 92: 25 from wave 1 plus 67 in this lane (70 attempted - 3 unplantable = 67); 25 + 67 = 92. Unplantable: 2 from wave 1 (`research-queue-autonomy-selftests`, `mfe-parity-instrument-guard`) plus 3 new (`arch-doc-guard`, `diag-relay-render-guard`, `pytest-skip-attribution-guard`); 2 + 3 = 5.

"Caught" = control exit 0 before the plant, `run_guards.py --only <g>` exit 1 with FAIL >= 1 after it, control exit 0 after revert, all observed by `scripts/ops/guard_plant.py`, which writes each row from the observed diff and exit codes.

## Superseded rows (24, kept as history, not evidence)
`not_run` 10 (wave 1, replaced by real runs) · `invalid` 7 · `missed` 6 · `unplantable` 1. Every `missed` row below is a **plant or harness error, not a guard miss**, and each has a later `caught` row for the same guard:
- `automerge-trigger-guard`: sed hit a comment, not the `paths:` filter.
- `workflow-push-target-guard`: `workflow_dispatch`-only is deliberately `conditional_default` (reported, not failed); a `schedule:` plant was caught.
- `corpus-row-floor-guard`: `.gitignore` (`data/*.csv`) kept the planted file out of the commit; `git add -f` was caught.
- `test-schema-fidelity-guard`: its scan step has a per-step `when` regex that is empty under `--all` (BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH); re-run with the new `--no-all` was caught.
- `pr-landing-guard` (two rows): a `hold` declaration is not subject to the tier check, and a clean-tree control cannot pass for a `self` declaration (R6 needs the arming file in the diff). The re-plant corrupts an existing `hold` declaration (drops `hold_reason`) under `GITHUB_HEAD_REF`, and was caught. The earlier `unplantable` row for it (harness claim that the step was skipped) was wrong: the default event is `pull_request`; the real obstacle was a detached worktree with no branch.
The supersede flag was added to these rows by hand after the fact (the tool wrote the rest of each row); that is the only edit to tool-written rows.

## Method
`scripts/ops/guard_plant.py`. Scratch worktree off `origin/main`, never pushed, removed after. A control that is not exit 0, or a planted run that is COULD-NOT-RUN, yields `invalid`. Changes made to the tool in this lane: `--env K=V` (guards that need a branch, e.g. `GITHUB_HEAD_REF`, in a detached worktree), `--no-all` (relevance on, so per-step `when` clauses see the changed files), and `.plant-pr.diff` is no longer committed into the plant (it previously made an empty plant look non-empty). The plants in this lane were designed by six parallel sub-lanes, each running the tool; every row was still written by the tool.

## Not established
- Each "caught" shows the guard fails on *one* plant of its stated claim, not every variant. Weaker cases: `research-tooling-selftests` is a ~20-step bundle and only `check_workflow_failure_swallow.py` was planted; `roster-promotion-evidence-guard` failed on clause C4 (verdict 'fail' of an existing record), not on a missing record; `guard-glob-coverage-detector` was proved through its self-test's live-table check; `mandate-autoland-guard` needed `GITHUB_HEAD_REF`.
- `artifact-caveat-guard`: its backlog files are archived and absent on main, so its core rule has nothing to check on the real tree; the plant recreated one.
- The 5 unplantable guards cannot go red in CI on the tree or diff at all (advisory, or self-test only). Filed as pipeline `PI-20260929-5VMBTZPH-0001` and `PI-20260929-LEUUWHQC-0002`. `PI-20260929-LEUUWHQC-0001` is closed.
- Verdicts are on `origin/main` @ `e2d8f84`; merged != deployed != observed. Nothing here is deployed.
