"""`/api/diag/version` must report the sha the PROCESS is running.

MEASURED DEFECT, 2026-08-23 —
BL-20260823-DIAG-VERSION-REPORTS-DISK-SHA-NOT-RUNNING-CODE.

The endpoint's whole purpose, per its own docstring, is to "assert that a
post-deploy restart actually rolled the running code forward (the 2026-05-09
24h-stale-code incident shipped because nothing in the deploy chain confirmed
the running web-api had rebooted)."

It could not. `_resolve_git_sha()` shells `git rev-parse --short HEAD` against
the working tree at CALL time, and the endpoint called it per request -- so it
reported what was on DISK. A `git pull` advances that without restarting
anything, meaning the endpoint reported the NEW sha while the OLD code served:
precisely the state it exists to detect.

And the deploy-side assertion was structurally vacuous. scripts/
deploy_pull_restart.sh set `EXPECTED_SHA=$(git rev-parse --short HEAD)` and
compared it to the endpoint's `git_sha` -- the same command over the same tree.
X == X. It would have passed during the 2026-05-09 incident.

LIVE PROOF, two independent endpoints on one process: `/api/diag/version`
returned `fced7279` while `/api/diag/log_file?name=target_naked_alert_state`
returned HTTP 400 -- a name present in `_LOG_FILES` as of `fced7279`. Control:
`account_reachability_alert_state`, allowlisted earlier, returned 200 with data.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / "src/web/api/routers/diag.py"


def test_the_running_sha_is_captured_once_at_import_not_per_request():
    src = DIAG.read_text(encoding="utf-8")
    assert re.search(r"^_RUNNING_GIT_SHA\s*:\s*str\s*=\s*_resolve_git_sha\(\)",
                     src, re.M), (
        "the running sha must be bound at MODULE scope; resolving it inside the "
        "request handler reads the working tree and reports disk, not the "
        "loaded code"
    )


def test_git_sha_field_serves_the_running_value_not_a_live_resolve():
    """The field named `git_sha` must be the process's, not the tree's."""
    src = DIAG.read_text(encoding="utf-8")
    body = src[src.index("def get_version("):]
    body = body[:body.index("\n@router") if "\n@router" in body else len(body)]
    assert '"git_sha": _RUNNING_GIT_SHA' in body, (
        "get_version must serve the import-time constant for `git_sha`"
    )
    assert '"git_sha": _resolve_git_sha()' not in body, (
        "serving a live resolve under `git_sha` is the defect itself"
    )


def test_disk_and_running_are_both_published_and_not_collapsed():
    src = DIAG.read_text(encoding="utf-8")
    body = src[src.index("def get_version("):]
    for field in ('"git_sha"', '"git_sha_on_disk"', '"restart_pending"'):
        assert field in body, f"{field} must be published so the two are distinguishable"


def test_restart_pending_is_none_when_either_sha_is_unknown():
    """'We could not look' must never be reported as 'they agree'."""
    src = DIAG.read_text(encoding="utf-8")
    body = src[src.index("def get_version("):]
    assert 'restart_pending = None' in body, (
        "an unresolvable sha on either side must yield None, not False -- False "
        "asserts the process matches the tree, which is exactly the claim we "
        "cannot make when we could not read one of them"
    )
    assert '"unknown"' in body, "the unknown check must be present in the handler"


def test_the_deploy_script_records_why_its_assertion_used_to_be_vacuous():
    """A fixed guard whose history is unwritten gets 'simplified' back."""
    sh = (ROOT / "scripts/deploy_pull_restart.sh").read_text(encoding="utf-8")
    assert "VACUOUS" in sh and "BL-20260823-DIAG-VERSION-REPORTS-DISK-SHA-NOT-RUNNING-CODE" in sh, (
        "deploy_pull_restart.sh must record that its comparison was disk-vs-disk, "
        "or a later reader sees two `git rev-parse` calls and 'tidies' one away"
    )
