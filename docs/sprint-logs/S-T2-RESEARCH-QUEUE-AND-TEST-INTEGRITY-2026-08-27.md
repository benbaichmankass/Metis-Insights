# Sprint Log: S-T2-RESEARCH-QUEUE-AND-TEST-INTEGRITY-2026-08-27

## Date Range
2026-08-27 (single session, continued through one context compaction).

## Objective
Land Lane P (the compat-matrix standard arm), then build Lane T2 — the standing
research/testing job queue with a blocking power gate and a dispatcher — and
settle R3's remaining blockers so the transport can be built next.

## Tier
Mixed. **Tier-3**: Lane P (#10364) — prop sizing/gating, merged on explicit
operator instruction. **Tier-1**: Lane T2 (#10365), the three test-integrity
fixes, all backlog/register work.

## Starting Context
Resumed mid-flight. `pytest-run` was RED on the Lane P branch and the cause was
unknown — the session had no numpy locally, so `montecarlo.py` changes had only
ever been compile-checked. Lane T2's shape was operator-decided (hybrid: queue
declared in repo, dispatcher routes) but unbuilt. R3 was blocked on two measured
blockers plus one explicitly unmeasured quantity.

## Repo State Checked
- `origin/main` at `97ffab0a` → `f2ea9e44` (Lane P) during the session.
- Branches: `claude/system-review-roadmap-pwdqgo` (Lane P, merged),
  `claude/research-queue-dispatcher-t2` (Lane T2, open — operator granted the
  separate branch so the Tier-3 diff stayed single-lane).
- Trainer VM read via `trainer-vm-diag` #10366.

## Files and Systems Inspected
- `tests/test_account_compat_matrix_override.py`, `src/prop/account_rulesets.py`
- `scripts/ml/_heavy_queue.py`, `docs/claude/strategy-refinement-queue.json`,
  the three review backlogs — the *does this already exist* check for T2
- `.github/workflows/gpu-burst-train.yml`, `comms/gpu_spend_ledger.json`,
  `scripts/ml/gpu_burst/run_burst.sh`
- `tests/conftest.py`, `tests/test_notify_session.py`,
  `tests/test_outcomes_integration.py`, `ml/calibration/fit.py`,
  `src/runtime/alert_cooldown.py`, `src/runtime/pipeline.py`
- Trainer: `ml/registry-store/`, `ml/experiments-runs/**/model_state.json`

## Work Completed
1. **Lane P `pytest-run` red — root-caused and fixed.** Three failures, all
   `AttributeError: '_FakePropUnit' object has no attribute 'gradeable'`. The
   dataclass gained the property in-branch; the hand-rolled test stub did not.
   Fixed by building the stub from the REAL dataclass, not by a defensive
   `getattr` in the production path (a `True` default would silently grade an
   account whose size could not be established — the defect the branch removes).
   **#10364 merged as `f2ea9e44`.**
2. **Lane T2 built** (#10365): `research/queue/*.yaml` one-file-per-job, the R4
   blocking power gate (5 states), routing (4 states), `dispatch_queue.py`, a
   daily cron workflow (dry-run by default), 51 tests, README, and registration
   in the workflow catalogue + research capability index.
3. **R3 unblocked.** `model_state.json` measured (trainer-diag #10366); both
   stated blockers confirmed directly; operator chose the artifact policy.
4. **Three local-only test defects root-caused and FIXED**, taking the local
   full suite from 11 failures to 0.

## Validation Performed
- Lane P: full local suite **13,241 passed**, the three failures gone; CI green
  on all four checks.
- Lane T2: 51 new tests; guards **PASS 42 / FAIL 0**; `ruff` clean;
  `lint-imports` 6/6 contracts kept.
- Test fixes, each against its own reproducer:
  - sklearn stub — `pytest tests/ml/calibration/ tests/test_outcomes_integration.py`
    **5 failed → 22 passed**.
  - durable latch — the module now passes **3× consecutively** and creates no
    real latch file.
  - `sys.modules` pop — `pytest tests/test_[d-n]*.py tests/test_send_ping.py`
    **10 failed → 0**.
- Full local suite across the three fixes: **11 → 5 → 0**.

## Documentation Updated
- `docs/github-actions-workflows.md` (new workflow row) and
  `docs/research/RESEARCH-CAPABILITY-INDEX.md` (§ 11, two new tools) — both
  guards **refused the change until these landed**, correctly.
- `.github/workflows/gpu-burst-train.yml` header: its safety claim that the
  adapter "currently aborts (adapter pending) — so it can't spend even if armed
  early" was **false and stale in the reassuring direction**. Corrected against
  the ledger.
- `docs/claude/OPEN-ITEMS.json`: two rows (8/12).
- Health backlog: 5 rows filed, 2 of them resolved same day.

## Contradictions or Drift Found
- **`gpu-burst-train.yml` claimed it could not spend; the ledger records 7
  RunPod runs.** Field beats comment. Load-bearing because the operator chose to
  let the dispatcher fire GPU unattended on the strength of that gate.
- **`BL-20260827-CALIBRATION-TESTS-ORDER-DEPENDENT-ON-NEWER-SKLEARN` named a
  plausible wrong suspect** (dependency versions) while claiming the cause was
  unknown. Resolved and the mis-diagnosis recorded on the row.
- **My own retraction was wrong and is itself withdrawn.** I briefly retracted
  the correct latch finding because a full-suite run "still failed" — without
  reading the assertion. The run was failing for a *second*, unrelated reason. A
  multi-cause failure list is not one phenomenon.

## Risks and Follow-Ups
- **The T2 dispatcher has never fired for real.** Every run so far was a dry run
  or a stubbed `_fire`. None of the three GPU spend gates has been observed
  holding against it (`OI-20260827-RESEARCH-QUEUE-DISPATCHER-NEVER-FIRED`).
- **Lane P merged without a measured before/after** of which compat verdicts
  move. `RQ-20260827-001` exists to produce it
  (`OI-20260827-LANE-P-COMPAT-VERDICTS-UNMEASURED`).
- **The cadence stamp can lose a race**: it lands via an auto-merge PR, so a
  second dispatcher run inside that window would re-fire. Bounded by the
  concurrency group, the daily cron and the per-run GPU cap; the stronger fix
  (result-based idempotence off `lands.store`) is named, not built.
- **All three test defects were invisible in CI** because CI's fuller dependency
  set masks them. A conftest-level audit — no test may leave `sys.modules` or
  `runtime_logs/` changed — would catch the class rather than the instances.

## Deferred Items
- **R3 itself** — decisions all settled, nothing built. Next session's item.
- The psutil gap in `requirements-test.txt`
  (`BL-20260827-REQUIREMENTS-TEST-OMITS-PSUTIL-EIGHT-TESTS-FAIL`).
- The broad conftest isolation fixture described above.

## Next Recommended Sprint
**R3 — close the offload loop.** Build the transport only; leave the drain
unarmed. Everything needed is on
`BL-20260827-R3-OFFLOAD-LANDING-HAS-TWO-MEASURED-BLOCKERS`.

## Wrap-Up Check
- [x] Both PRs merged or set to auto-merge on green
- [x] Guards PASS 42 / FAIL 0
- [x] Local full suite 0 failures
- [x] OPEN-ITEMS updated (8/12, under cap)
- [x] Every finding filed with a reproducer
- [x] Two of my own errors recorded on the rows they affected
