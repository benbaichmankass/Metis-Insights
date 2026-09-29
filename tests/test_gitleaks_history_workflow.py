"""FIX-SA-13 — weekly gitleaks never echoes a value; four workflows are least-privilege."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "ops" / "gitleaks_summarize.py"

CANARY = "CANARY-SECRET-VALUE-9f8e7d6c5b4a"


def _row(**kw):
    base = {
        "RuleID": "github-pat", "File": "docs/x.md", "Commit": "a" * 40, "StartLine": 7,
        # every field gitleaks may carry the matched text in:
        "Secret": CANARY, "Match": f"token={CANARY}", "Line": f"token={CANARY}",
        "Fingerprint": f"{'a'*40}:docs/x.md:github-pat:7", "Author": "n", "Email": "e@x",
        "Message": f"commit msg {CANARY}", "Entropy": 4.1, "Tags": [], "SomeFutureField": CANARY,
    }
    base.update(kw)
    return base


def _run(tmp_path, rows, summary=True):
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps(rows))
    env = {**os.environ}
    if summary:
        env["GITHUB_STEP_SUMMARY"] = str(tmp_path / "sum.md")
    r = subprocess.run([sys.executable, str(SCRIPT), str(rep)], capture_output=True, text=True, env=env)
    return r, rep, tmp_path / "sum.md"


def test_never_emits_matched_value_anywhere(tmp_path):
    r, rep, summ = _run(tmp_path, [_row(), _row(RuleID="aws-access-token", File="a/b.py", StartLine=3)])
    assert r.returncode == 1
    assert CANARY not in r.stdout + r.stderr + summ.read_text()
    assert "github-pat" in r.stdout and "docs/x.md" in r.stdout and ("a" * 12) in r.stdout
    assert "a" * 13 not in r.stdout.replace("a/b.py", "")  # commit is cut to 12 chars
    assert not rep.exists(), "report must be deleted so nothing can upload it"


def test_clean_report_exits_zero(tmp_path):
    r, rep, _ = _run(tmp_path, [])
    assert r.returncode == 0 and "0 finding" in r.stdout and not rep.exists()


def test_missing_or_corrupt_report_is_not_a_clean_scan(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope.json")], capture_output=True, text=True)
    assert r.returncode == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    r = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    assert r.returncode == 2
    obj = tmp_path / "obj.json"
    obj.write_text('{"a": 1}')
    assert subprocess.run([sys.executable, str(SCRIPT), str(obj)], capture_output=True).returncode == 2


def _wf():
    return yaml.safe_load((WF / "gitleaks-history-weekly.yml").read_text())


def test_workflow_is_pinned_read_only_and_uploads_nothing():
    text = (WF / "gitleaks-history-weekly.yml").read_text()
    doc = _wf()
    assert doc["permissions"] == {"contents": "read"}
    steps = doc["jobs"]["scan"]["steps"]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert uses and all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", u) for u in uses), uses
    # comments may MENTION these; only real steps count
    assert not any(k in u for u in uses for k in ("upload-artifact", "github-script", "gitleaks-action"))
    assert "createComment" not in "\n".join(s.get("run", "") for s in steps)
    scan = next(s for s in doc["jobs"]["scan"]["steps"] if s["name"].startswith("Scan"))["run"]
    assert "--redact=100" in scan and "--exit-code 0" in scan
    assert not re.search(r"(^|\s)(-v|--verbose)(\s|$)", scan), "verbose gitleaks prints matches"
    assert re.search(r'GITLEAKS_SHA256:\s*"[0-9a-f]{64}"', text)
    assert doc["jobs"]["scan"]["steps"][0]["with"]["fetch-depth"] == 0


# ── the four least-privilege workflows ───────────────────────────────────────
def _perm_view(name):
    doc = yaml.safe_load((WF / f"{name}.yml").read_text())
    job_perms = [j.get("permissions") for j in doc["jobs"].values()]
    return doc, doc.get("permissions"), job_perms


def test_issue_commenting_workflows_have_exactly_contents_read_issues_write():
    for name in ("reset-daily-risk-state", "test-alpaca-creds", "test-alpaca-from-vm"):
        doc, top, jobs = _perm_view(name)
        text = (WF / f"{name}.yml").read_text()
        assert top == {"contents": "read", "issues": "write"}, name
        assert jobs == [None], name
        # what justifies issues: write — and nothing needing more is present
        assert "issues.createComment" in text and "issues.update" in text, name
        assert "actions/checkout" not in text and "git push" not in text, name
        assert "pull_request" not in text.replace("pull_request_target", "X").split("jobs:")[0], name


def test_training_rerun_keeps_write_only_where_it_pushes():
    doc, top, jobs = _perm_view("training-rerun-5m")
    text = (WF / "training-rerun-5m.yml").read_text()
    assert top == {"contents": "read"}
    assert jobs == [{"contents": "write"}]
    assert "git push origin" in text  # the reason the job-level write must stay
