📌 MI-57 FINAL STATE — PR #10783, and one thing I could not do

Session: `session_01T8iWSepqAuBU7sgbs8GPHu`
Branch: `claude/openprs-settled-reconciler` · head `dae0bbf`
https://github.com/benbaichmankass/Metis-Insights/pull/10783

## The manager's decision is implemented

**Bot-author condition DROPPED.** The predicate is now the `automation/` prefix
alone, matched ANCHORED (so `claude/automation-notes` is not excused). Your
measurement matched mine and went further — the author field is inverted on both
sides, so a bot-author test would have excused NOTHING while dropping genuine
sub-session PRs. #10785 also upgrades the reconciler's own landing PR from a
predicted case to a MEASURED one; it is now a class with three observed members.

Tests, exactly as specified:
- a `claude/**` PR still requires a row, with **#10783 — bot-authored — as the
  fixture**, because that is what proves the predicate does not key on author;
- an `automation/` branch is excused with **both** a human and a bot author
  (parametrized), so the behaviour cannot read as an accident of which account
  opened it;
- the anchoring test, and the fail-closed bare-number test.

⚠️ **`automation/` is now a RESERVED NAMESPACE and the hole is asserted, not
hidden.** A session opening a PR from an `automation/`-prefixed branch is
SILENTLY excused and its operator decision goes unrecorded with nothing
complaining. `test_automation_is_a_reserved_namespace_and_that_is_the_residual`
pins it so a later reader meets the hole rather than inferring coverage. Session
work belongs on `claude/**`; nothing in the repo enforces that.

`commit-to-main` + `verify-merged: "true"` unchanged, and a missing
`BRANCH_PROTECTION_TOKEN` REFUSES the run up front rather than degrading to a
no-op that would read as "nothing had merged".

## Status, stated precisely

CI on `dae0bbf`: **guards ✅ · pytest-collect ✅ · repo-inventory ✅.**
**`pytest-run` has NOT been observed completing** — it has been `in_progress`
since 10:52:58Z, past the 12.9–15.3 min this repo records for it. I am reporting
that rather than calling the PR green.

Locally: 52 targeted tests pass, `open_pr_record.py --strict` passes,
`run_guards.py` 49 pass / 0 fail, ruff clean, `layer-guard` 6 kept / 0 broken.
⚠️ A full local `pytest tests/` is NOT available as evidence here: 107 of 110
collection errors are `fastapi` / `pydantic` / `_cffi_backend` missing in this
container, and **no error names any file I touched**. CI's green
`pytest-collect` is the positive control that collection is clean in the real
environment. I am calling that inconclusive-by-environment, not a pass.

## ⚠️ ONE THING I COULD NOT DO — un-drafting

`update_pull_request(draft: false)` returns **403 Resource not accessible by
integration**, the same write-scope boundary that 403s `add_issue_comment` and
`create_pull_request`. **There is no relay for it**: `pr-opener.yml` only
CREATES a PR and no-ops once a result file exists, and re-creating would mint a
new PR number — orphaning the row recorded for #10783 and both board references.

So **#10783 stays a DRAFT and needs you to flip it.** I am naming it rather than
leaving it looking like a skipped step.

⚠️ This post's own relay result commit moves the branch head and re-buries
#10783's checks (the documented trap). The runs above are on `dae0bbf`.

## Still not proven

The reconciler is DEPLOYED, NOT OBSERVED — no run has moved a real row. Its
done-condition is a `push: main` run that MOVES a row and stamps
`last_reconciled_sha`, **not** green CI. A dead reconciler is self-announcing
via `reconciler_not_run`, which is a distinct finding from `stale_row`.
