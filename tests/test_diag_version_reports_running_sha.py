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
    """'We could not look' must never be reported as 'they agree'.

    The three-way now lives in `_restart_pending` (FIX-SA-11); the handler must
    delegate to it, and the helper must return None for an unknown sha.
    """
    src = DIAG.read_text(encoding="utf-8")
    handler = src[src.index("def get_version("):]
    assert "_restart_pending(_RUNNING_GIT_SHA, on_disk)" in handler
    helper = src[src.index("def _restart_pending("):src.index('@router.get("/version")')]
    assert 'running == "unknown" or on_disk == "unknown"' in helper
    assert "return None" in helper, (
        "an unresolvable sha or failed diff must yield None, not False -- False "
        "asserts the process matches the tree, which we cannot claim unread"
    )


def test_the_deploy_script_records_why_its_assertion_used_to_be_vacuous():
    """A fixed guard whose history is unwritten gets 'simplified' back."""
    sh = (ROOT / "scripts/deploy_pull_restart.sh").read_text(encoding="utf-8")
    assert "VACUOUS" in sh and "BL-20260823-DIAG-VERSION-REPORTS-DISK-SHA-NOT-RUNNING-CODE" in sh, (
        "deploy_pull_restart.sh must record that its comparison was disk-vs-disk, "
        "or a later reader sees two `git rev-parse` calls and 'tidies' one away"
    )


# --- FIX-SA-11: restart_pending is computed from the runtime-path diff --------
import subprocess
import pytest


@pytest.fixture()
def _repo(tmp_path, monkeypatch):
    def git(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("1")
    git("add", "."); git("commit", "-qm", "base")
    base = git("rev-parse", "--short", "HEAD")
    from src.web.api.routers import diag
    monkeypatch.setattr(diag, "repo_root", lambda: tmp_path)
    return tmp_path, git, base, diag


def test_docs_only_diff_is_not_restart_pending(_repo):
    tmp, git, base, diag = _repo
    (tmp / "docs").mkdir(); (tmp / "docs/x.md").write_text("x")
    (tmp / "README.md").write_text("x")
    git("add", "."); git("commit", "-qm", "docs")
    assert diag._restart_pending(base, git("rev-parse", "--short", "HEAD")) is False


def test_runtime_diff_is_restart_pending(_repo):
    tmp, git, base, diag = _repo
    (tmp / "src/a.py").write_text("2")
    git("add", "."); git("commit", "-qm", "code")
    assert diag._restart_pending(base, git("rev-parse", "--short", "HEAD")) is True


def test_unresolvable_side_is_none_not_false(_repo):
    tmp, git, base, diag = _repo
    head = git("rev-parse", "--short", "HEAD")
    assert diag._restart_pending("unknown", head) is None
    assert diag._restart_pending("deadbee", head) is None  # not in the repo: could not look
    assert diag._restart_pending(base, base) is False


def test_status_carries_running_and_on_disk_shas_distinctly():
    from src.web import runtime_status as rs
    st = rs.build_status(git_sha="disk123")
    assert st["git_sha_running"] == rs._RUNNING_GIT_SHA
    assert st["git_sha_on_disk"] == "disk123" and st["git_sha"] == "disk123"
