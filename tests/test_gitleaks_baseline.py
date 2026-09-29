"""The gitleaks baseline accepts the reviewed findings and NOTHING that leaks a secret.

The weekly scan (gitleaks-history-weekly.yml) picks up `.gitleaks-baseline.json`
automatically. These checks pin the properties that make that safe: it is a redacted
report, the four known-revoked findings are baselined AND named in the notes doc, and
the workflow really reads the file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".gitleaks-baseline.json"
NOTES = ROOT / "docs" / "security" / "gitleaks-baseline.md"
WF = ROOT / ".github" / "workflows" / "gitleaks-history-weekly.yml"

KNOWN_REVOKED = {
    "2c7818524a785b85d2a8db767bd698cb4bdb71cb:.env:telegram-bot-api-token:1",
    "9ea895023d4781eeeeda55d729b52333e5ac5a6d:bybit_config.py:telegram-bot-api-token:1",
    "67f307e0a408dafe9096943ef5674df18671e9d0:test_bybit_keys.py:generic-api-key:5",
    "67f307e0a408dafe9096943ef5674df18671e9d0:test_bybit_keys.py:generic-api-key:6",
}


def _rows():
    return json.loads(BASELINE.read_text())


def test_baseline_is_a_redacted_report():
    rows = _rows()
    assert isinstance(rows, list) and len(rows) > 300
    assert all(r["Secret"] == "REDACTED" for r in rows)
    assert all("REDACTED" in r["Match"] for r in rows)
    assert all(r["Fingerprint"] and r["Commit"] and r["RuleID"] for r in rows)


def test_known_revoked_are_baselined_and_named_in_the_notes():
    fps = {r["Fingerprint"] for r in _rows()}
    assert KNOWN_REVOKED <= fps, KNOWN_REVOKED - fps
    notes = NOTES.read_text()
    for fp in KNOWN_REVOKED:
        commit, file, rule, line = fp.split(":")
        assert commit[:12] in notes, fp
        assert f"`{file.split(' ')[0]}" in notes, fp


def test_no_vendor_token_shape_survives_in_any_baseline_field():
    blob = BASELINE.read_text()
    for name, rx in {
        "aws": r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])",
        "github": r"\bgh[pousr]_[A-Za-z0-9]{30,}",
        "pem": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        "telegram": r"(?<!\d)\d{8,12}:AA[A-Za-z0-9_-]{30,}",
    }.items():
        assert not re.search(rx, blob), f"{name}-shaped string present in baseline"


def test_weekly_workflow_reads_the_baseline():
    doc = yaml.safe_load(WF.read_text())
    scan = next(s for s in doc["jobs"]["scan"]["steps"] if s["name"].startswith("Scan"))["run"]
    assert "--baseline-path .gitleaks-baseline.json" in scan
    assert "--redact=100" in scan


def test_fingerprints_are_unique():
    fps = [r["Fingerprint"] for r in _rows()]
    assert len(fps) == len(set(fps))
