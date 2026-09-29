# GUARD-PROOF — plant-a-defect proof of the unproven CI guards (E75, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane GUARD-PROOF, JC-SA-07 option (a). Evidence: [`GUARD-PROOF.findings.jsonl`](GUARD-PROOF.findings.jsonl) (40 rows, parses).

## Population
- Registry: **95** guards (`run_guards.py --list`, base `25f9eeb05622`).
- Excluded as already proven: the lead's 10 (§ 9 of the audit) and the **48** that CA-B05/B06 marked `proven-fails`. Caveat: that CA label means "has a `--self-test`", which the audit calls CA-B05/B06's flaw, so those 48 are *excluded by instruction, not re-proven here*.
- **Unproven set: 37.**

## Result (over the 37)
| planted-and-caught | missed | unplantable | not run |
|---:|---:|---:|---:|
| **25** | **0** | **2** | **10** |

25 + 0 + 2 + 10 = 37. "Caught" = control exit 0 before the plant, `run_guards.py --only <g> --all` exit 1 with FAIL ≥ 1 after it, control exit 0 after revert.

## Method
`scripts/ops/guard_plant.py` (added here). Each row is written **by the tool** from the observed `git diff origin/main...HEAD` and the three exit codes; the caller supplies only the plant and claim, and the verdict is computed. Scratch worktree off `origin/main`, never pushed, removed after. A control that is not exit 0, or a planted run that is COULD-NOT-RUN, yields `invalid`, not evidence.

## Problems found in the process (all disclosed)
- **My harness had a bug**: it passed `--pr-diff` to a file it never wrote, so `{pr_diff}` guards failed even as controls. `strategy-risk-guard` and `writer-conformance-guard` first came back `invalid` (exits 1/1/1); fixed in `guard_plant.py` and rerun → both caught. The invalid rows stay in the jsonl, `superseded: true`.
- `layer-guard`: first run `invalid` (`lint-imports` absent → COULD NOT RUN); rerun after install → caught.
- **10 guards not run**: the plant worker for `account-class-guard … canonical-doc-coherence` had its Bash calls denied by the permission classifier and ran nothing. Rows are `not_run`, no exit codes. Filed as pipeline `PI-20260929-LEUUWHQC-0001`. I did not run them myself to route around the denial.
- **Unplantable (2)**: `research-queue-autonomy-selftests`, `mfe-parity-instrument-guard` — every CI step is `--self-test`, so there is no whole-tree/diff step to plant against. Filed as a pipeline row.

## Not established
- Each "caught" shows the guard fails on *one* plant of its stated claim, not that it catches every variant. No guard **missed**, so no guard-fix rows; no guard was fixed here.
- Verdicts are on `origin/main` @ `25f9eeb`; merged ≠ deployed ≠ observed — nothing here is deployed.
