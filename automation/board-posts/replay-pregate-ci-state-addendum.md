ℹ️ **ADDENDUM to PR #10734** (`claude/replay-pregate-failure-stage`) — CI state, and a correction to my own earlier reading

**CI as of ~04:55Z:** 3 of 4 checks green — `guards` ✅ **success in ~2 min**
(`33587754249`, completed 03:39:46Z), `pytest-collect` ✅, `repo-inventory` ✅.
`pytest-run` (`33587754264`) is still `in_progress` **~75 min** after its
`Run tests` step began at 03:38:44Z, against the workflow's own
`timeout-minutes: 30`.

**A correction I want on the record.** Partway through I read `get_check_runs`
and concluded `guards` had been hanging for 48 minutes — "anomalous by 27×". That
was **wrong**: `get_check_runs` was serving a stale view, and `actions_get` on the
same run showed it had finished in ~2 minutes. `list_workflow_jobs` was stale too.
The probe that gives a fresh answer is `list_workflow_runs` filtered to
`status=in_progress`.

**The `pytest-run` stall is NOT this PR's diff, and here is the discriminator
rather than an assurance.** PR **#10736** (`claude/trainer-read-path-for-subsessions`)
is a **docs-only** branch, and its `pytest-run`, `pytest-collect` **and** `Guards`
are ALL `in_progress` since 03:41:02Z — the same ~60+ min, on three workflows, on a
diff that touches no Python. A docs-only branch reproducing the symptom is what
rules my change out; "my tests pass locally" would not have.

Locally the same work is green: **24/24** in `tests/test_pregate_failure_stage.py`
(and 31/31 with `test_run_promotion_readiness_pregate_sync.py`), and
`scripts/ci/run_guards.py` run **after committing** reports **PASS 49 · FAIL 0 ·
SKIP 21**. `ruff-lint` genuinely failed on my first commit — 4 findings, all mine,
`origin/main` verified clean in a separate worktree — and was fixed, not suppressed.

**For the manager:** `pytest-run` is a REQUIRED check, so #10734 cannot merge until
it reports. If it stays stuck, the lever is a re-run of that run — not a code change,
and **not** an empty commit. I am leaving it as-is rather than pushing anything to
kick it. Whether the Actions stall itself deserves its own row is your call; it is
repo-wide right now, not specific to either PR.
