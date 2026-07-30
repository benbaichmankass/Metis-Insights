"""The vacuity / under-coverage dead-man switch (`scripts/ops/check_artifact_validity.py`).

This guard exists because the SAME bug class recurred four times in one day
(2026-07-30) and twice across months: an artifact reporting success that is true
relative to its own scope, while the scope is wrong or the measurement inside is
empty. A guard against that class must itself be tested, or it becomes another
green-but-doing-nothing check — which would be the bug guarding the bug.

The load-bearing property is `TestFailsLoudly`: a vacuous artifact must produce a
NON-ZERO exit. A checker that prints a warning and exits 0 is what let instance 1
(`price_bars: 0`) survive for the producer's entire life.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "ops"))

cav = pytest.importorskip("check_artifact_validity")


def _write(root, rel, obj):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


class TestVacuityDetection:
    def test_zero_input_is_flagged(self, tmp_path):
        p = _write(tmp_path, "a.json", {"meta": {"price_bars": 0, "releases": 6}})
        problems = cav.check_json_artifact(p, {"inputs": [("meta.price_bars", 1)]})
        assert problems and "VACUOUS" in problems[0]

    def test_nonzero_input_is_clean(self, tmp_path):
        p = _write(tmp_path, "a.json", {"meta": {"price_bars": 1916}})
        assert cav.check_json_artifact(p, {"inputs": [("meta.price_bars", 1)]}) == []

    def test_missing_declared_input_is_flagged_not_skipped(self, tmp_path):
        """A key we cannot find must FAIL — 'we couldn't check' is not 'it's fine'.
        This is what caught the author's own wrong registry key for horizon_ic."""
        p = _write(tmp_path, "a.json", {"meta": {}})
        problems = cav.check_json_artifact(p, {"inputs": [("meta.price_bars", 1)]})
        assert problems and "MISSING" in problems[0]

    def test_unreadable_artifact_is_flagged(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        problems = cav.check_json_artifact(p, {"inputs": [("meta.n", 1)]})
        assert problems and "unreadable" in problems[0]

    def test_a_floor_above_one_is_respected(self, tmp_path):
        p = _write(tmp_path, "a.json", {"meta": {"n": 5}})
        assert cav.check_json_artifact(p, {"inputs": [("meta.n", 12)]})
        assert cav.check_json_artifact(p, {"inputs": [("meta.n", 5)]}) == []

    def test_list_valued_input_counts_by_length(self, tmp_path):
        p = _write(tmp_path, "a.json", {"records": []})
        assert cav.check_json_artifact(p, {"inputs": [("records", 1)]})
        p2 = _write(tmp_path, "b.json", {"records": [1, 2, 3]})
        assert cav.check_json_artifact(p2, {"inputs": [("records", 1)]}) == []

    def test_bool_is_not_treated_as_a_count(self, tmp_path):
        """`present: true` must not satisfy a count floor — it is not a measurement."""
        p = _write(tmp_path, "a.json", {"meta": {"n": True}})
        problems = cav.check_json_artifact(p, {"inputs": [("meta.n", 1)]})
        assert problems and "not numeric" in problems[0]


class TestJsonlArtifacts:
    def test_empty_ledger_is_vacuous(self, tmp_path):
        p = tmp_path / "l.jsonl"
        p.write_text("\n\n", encoding="utf-8")
        assert cav.check_jsonl_artifact(p, {"jsonl_min_rows": 1})

    def test_populated_ledger_is_clean(self, tmp_path):
        p = tmp_path / "l.jsonl"
        p.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
        assert cav.check_jsonl_artifact(p, {"jsonl_min_rows": 1}) == []


class TestHeuristicScan:
    """Unregistered artifacts must not be silently exempt."""

    def test_all_zero_inputs_are_flagged_when_unregistered(self, tmp_path):
        _write(tmp_path, "comms/x/thing.json", {"meta": {"count": 0, "rows": 0}})
        found = cav.heuristic_scan(tmp_path, registered=set())
        assert found and "VACUOUS (heuristic)" in found[0][1]

    def test_a_single_zero_among_real_counts_is_NOT_flagged(self, tmp_path):
        """Conservative by design: one zero among real counts is normal data."""
        _write(tmp_path, "comms/x/thing.json", {"meta": {"count": 40, "rows": 0}})
        assert cav.heuristic_scan(tmp_path, registered=set()) == []

    def test_registered_artifacts_are_not_double_reported(self, tmp_path):
        p = _write(tmp_path, "comms/x/thing.json", {"meta": {"count": 0}})
        assert cav.heuristic_scan(tmp_path, registered={p}) == []

    def test_artifact_with_no_known_input_keys_is_not_flagged(self, tmp_path):
        _write(tmp_path, "comms/x/cfg.json", {"colour": "blue"})
        assert cav.heuristic_scan(tmp_path, registered=set()) == []


class TestFailsLoudly:
    """THE load-bearing property: vacuity must exit NON-ZERO."""

    def test_vacuous_registered_artifact_exits_nonzero(self, tmp_path, monkeypatch):
        _write(tmp_path, "art.json", {"meta": {"price_bars": 0}})
        monkeypatch.setattr(cav, "CHECKS",
                            {"art.json": {"inputs": [("meta.price_bars", 1)]}})
        assert cav.main(["--repo-root", str(tmp_path), "--no-heuristic"]) == 1

    def test_clean_artifact_exits_zero(self, tmp_path, monkeypatch):
        _write(tmp_path, "art.json", {"meta": {"price_bars": 1916}})
        monkeypatch.setattr(cav, "CHECKS",
                            {"art.json": {"inputs": [("meta.price_bars", 1)]}})
        assert cav.main(["--repo-root", str(tmp_path), "--no-heuristic"]) == 0

    def test_missing_artifact_fails_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cav, "CHECKS",
                            {"nope.json": {"inputs": [("meta.n", 1)]}})
        assert cav.main(["--repo-root", str(tmp_path), "--no-heuristic"]) == 1

    def test_missing_artifact_can_be_allowed_explicitly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cav, "CHECKS",
                            {"nope.json": {"inputs": [("meta.n", 1)]}})
        assert cav.main(["--repo-root", str(tmp_path), "--no-heuristic",
                         "--allow-missing"]) == 0


class TestRegistry:
    def test_the_known_incident_artifacts_are_registered(self):
        """The four 2026-07-30 instances must stay covered — a regression here is how
        the class comes back."""
        assert "comms/macro/econ_event_study_scorecard.json" in cav.CHECKS
        assert "comms/macro/econ_event_study_crude_scorecard.json" in cav.CHECKS

    def test_every_registered_entry_declares_a_check(self):
        for rel, spec in cav.CHECKS.items():
            assert spec.get("inputs") or spec.get("jsonl_min_rows"), \
                f"{rel} declares no check — it would silently pass"

    def test_every_registered_entry_carries_a_note(self):
        """The note is why the check exists; without it a future session deletes it."""
        for rel, spec in cav.CHECKS.items():
            assert spec.get("note"), f"{rel} has no note"


class TestGrandfatherListDiscipline:
    """The grandfather list must be attributed and time-boxed, never a silence list.

    "It was already like that" is how the original bug survived. A known-vacuous
    artifact is therefore allowed ONLY while it names an owning backlog row and has
    not passed its expiry — after that it is a hard failure again.
    """

    def test_every_entry_names_a_backlog_owner(self):
        for rel, spec in cav.KNOWN_VACUOUS.items():
            assert spec.get("backlog"), f"{rel}: unowned debt is hidden debt"

    def test_every_entry_is_time_boxed(self):
        for rel, spec in cav.KNOWN_VACUOUS.items():
            assert spec.get("until"), f"{rel}: must not be allowed to become permanent"

    def test_every_entry_explains_itself(self):
        for rel, spec in cav.KNOWN_VACUOUS.items():
            assert spec.get("why"), f"{rel}: no rationale"

    def test_unexpired_entries_are_clean_today(self):
        """The shipped list must be valid as shipped."""
        import datetime as _dt
        today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
        assert cav.known_vacuous_problems(today) == []

    def test_an_expired_entry_becomes_a_hard_failure(self, monkeypatch):
        monkeypatch.setattr(cav, "KNOWN_VACUOUS", {
            "x.json": {"backlog": "BL-X", "until": "2020-01-01", "why": "old"}})
        problems = cav.known_vacuous_problems("2026-07-30")
        assert problems and "EXPIRED" in problems[0]

    def test_an_unattributed_entry_is_a_hard_failure(self, monkeypatch):
        monkeypatch.setattr(cav, "KNOWN_VACUOUS", {
            "x.json": {"until": "2099-01-01", "why": "no owner"}})
        problems = cav.known_vacuous_problems("2026-07-30")
        assert problems and "no backlog row" in problems[0]

    def test_grandfathered_artifact_exits_zero_but_strict_fails(self, tmp_path, monkeypatch):
        _write(tmp_path, "art.json", {"meta": {"price_bars": 0}})
        monkeypatch.setattr(cav, "CHECKS",
                            {"art.json": {"inputs": [("meta.price_bars", 1)],
                                          "note": "n"}})
        monkeypatch.setattr(cav, "KNOWN_VACUOUS",
                            {"art.json": {"backlog": "BL-X", "until": "2099-01-01",
                                          "why": "tracked"}})
        assert cav.main(["--repo-root", str(tmp_path), "--no-heuristic"]) == 0
        assert cav.main(["--repo-root", str(tmp_path), "--no-heuristic", "--strict"]) == 1

    def test_grandfathering_expires_into_failure(self, tmp_path, monkeypatch):
        _write(tmp_path, "art.json", {"meta": {"price_bars": 0}})
        monkeypatch.setattr(cav, "CHECKS",
                            {"art.json": {"inputs": [("meta.price_bars", 1)],
                                          "note": "n"}})
        monkeypatch.setattr(cav, "KNOWN_VACUOUS",
                            {"art.json": {"backlog": "BL-X", "until": "2026-01-01",
                                          "why": "tracked"}})
        assert cav.main(["--repo-root", str(tmp_path), "--no-heuristic",
                         "--today", "2026-07-30"]) == 1
