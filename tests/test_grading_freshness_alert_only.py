"""grading-freshness is ALERT-ONLY (operator 2026-09-29): PRs warn, a daily job pings."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "ci" / "check_grading_freshness.py"
NOW = "2026-09-29T12:00:00Z"

_spec = importlib.util.spec_from_file_location("check_grading_freshness", GUARD)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_grading_freshness"] = mod
_spec.loader.exec_module(mod)


def _run(path, *extra):
    return subprocess.run([sys.executable, str(GUARD), "--path", str(path), "--now", NOW, *extra],
                          capture_output=True, text=True)


def _scores(tmp_path, ts):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"reviewed_at": ts}) + "\n")
    return p


def test_stale_exits_zero_with_warning(tmp_path):
    r = _run(_scores(tmp_path, "2026-09-08T10:50:51Z"))
    assert r.returncode == 0
    assert "::warning" in r.stdout and "STALE" in r.stdout


def test_unreadable_warns_and_is_not_fresh(tmp_path):
    r = _run(tmp_path / "absent.jsonl")
    assert r.returncode == 0
    assert "::warning" in r.stdout and "UNREADABLE" in r.stdout
    assert "grading-freshness: fresh" not in r.stdout


def test_fresh_is_silent(tmp_path):
    r = _run(_scores(tmp_path, "2026-09-28T00:00:00Z"))
    assert r.returncode == 0
    assert "::warning" not in r.stdout


def test_strict_keeps_raw_exit_codes(tmp_path):
    assert _run(_scores(tmp_path, "2026-09-08T00:00:00Z"), "--strict").returncode == 1
    assert _run(tmp_path / "absent.jsonl", "--strict").returncode == 2
    assert _run(_scores(tmp_path, "2026-09-28T00:00:00Z"), "--strict").returncode == 0


def _alert(path):
    calls = []

    def runner(cmd, check=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    return mod.alert(path, now, runner=runner), calls


def test_alert_pings_once_on_stale(tmp_path):
    rc, calls = _alert(_scores(tmp_path, "2026-09-08T00:00:00Z"))
    assert rc == 0 and len(calls) == 1
    assert "action=send-ping" in calls[0] and "system-actions.yml" in calls[0]


def test_alert_pings_once_on_unreadable(tmp_path):
    rc, calls = _alert(tmp_path / "absent.jsonl")
    assert rc == 0 and len(calls) == 1


def test_alert_quiet_on_fresh(tmp_path):
    rc, calls = _alert(_scores(tmp_path, "2026-09-28T00:00:00Z"))
    assert rc == 0 and calls == []


def test_alert_fails_if_ping_cannot_dispatch(tmp_path):
    def runner(cmd, check=False):
        return subprocess.CompletedProcess(cmd, 1)
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    assert mod.alert(_scores(tmp_path, "2026-09-08T00:00:00Z"), now, runner=runner) == 1


def test_alert_workflow_is_daily_and_runs_alert_mode():
    wf = (ROOT / ".github" / "workflows" / "grading-freshness-alert.yml").read_text()
    assert 'cron: "10 4 * * *"' in wf and "--alert" in wf
    assert "pull_request" not in wf
