"""Tests for the allow-degraded owner+expiry guard (BL-20260730-ALLOW-DEGRADED-NEEDS-EXPIRY)."""
from __future__ import annotations

import json

from scripts.ops.check_allow_degraded import marker_problems, scan

_FILED = {"BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0"}
_TODAY = "2026-08-01"


# ------------------------------------------------------------- marker_problems
def test_well_formed_annotation_has_no_problems():
    payload = "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-11-01 fetch may fail"
    assert marker_problems(payload, _FILED, _TODAY) == []


def test_missing_backlog_id_is_flagged():
    probs = marker_problems("until:2026-11-01 just because", _FILED, _TODAY)
    assert any("names no backlog id" in p for p in probs)


def test_unresolvable_backlog_id_is_flagged():
    probs = marker_problems("BL-20260101-NEVER-FILED until:2026-11-01", _FILED, _TODAY)
    assert any("resolve to NOTHING" in p for p in probs)


def test_missing_until_is_flagged():
    probs = marker_problems("BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 no expiry", _FILED, _TODAY)
    assert any("no `until:" in p for p in probs)


def test_expired_until_is_flagged():
    payload = "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-07-01"
    probs = marker_problems(payload, _FILED, "2026-08-01")
    assert any("EXPIRED on 2026-07-01" in p for p in probs)


def test_unexpired_until_on_the_boundary_is_ok():
    # today == until is NOT past it (strict `today > until`), so it still passes.
    payload = "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-08-01"
    assert marker_problems(payload, _FILED, "2026-08-01") == []


# ------------------------------------------------------------------------ scan
def _mk_repo(tmp_path, workflow_body):
    (tmp_path / "docs" / "claude").mkdir(parents=True)
    (tmp_path / "docs" / "claude" / "health-review-backlog.json").write_text(
        json.dumps({"items": [{"id": "BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0"}]}),
        encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "x.yml").write_text(workflow_body, encoding="utf-8")
    return tmp_path


def test_scan_accepts_a_well_formed_marker(tmp_path):
    repo = _mk_repo(tmp_path,
                    'run: fetch.py || echo degraded  '
                    '# allow-degraded: BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0 until:2026-11-01\n')
    findings, n_valid = scan(repo, _TODAY)
    assert findings == []
    assert n_valid == 1


def test_scan_flags_a_marker_missing_until(tmp_path):
    repo = _mk_repo(tmp_path,
                    'run: fetch.py || true  '
                    '# allow-degraded: BL-20260730-CANDLE-FETCH-DEGRADES-TO-N0\n')
    findings, _ = scan(repo, _TODAY)
    assert len(findings) == 1
    assert any("no `until:" in p for p in findings[0]["problems"])


def test_scan_skips_placeholder_payloads(tmp_path):
    # A syntax reference `# allow-degraded: <reason>` (docs) must NOT be flagged.
    repo = _mk_repo(tmp_path,
                    '# An exception carries `# allow-degraded: <reason>` here\n')
    findings, n_valid = scan(repo, _TODAY)
    assert findings == [] and n_valid == 0


def test_scan_ignores_non_comment_form(tmp_path):
    # A bare `allow-degraded:` inside a string literal is not the comment marker.
    repo = _mk_repo(tmp_path, "run: python -c \"'allow-degraded:' not in buf\"\n")
    findings, n_valid = scan(repo, _TODAY)
    assert findings == [] and n_valid == 0
