"""SOAK-WATCH (2026-10-04): the weekly soak grade must LAND, and a run that
lands nothing must not read green.

MEASURED incident: the 2026-10-04 slot's keeper run crashed in
r3_cost_fidelity (`ValueError: month must be in 1..12`) and wrote no record;
the late scheduled run was deduped and finished `success`. And no run of the
workflow had ever committed the per-leg record — only its pointer.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import soak_grade_landed as sgl  # noqa: E402

WF = REPO / ".github" / "workflows" / "soak-book-grade-weekly.yml"


def _wf() -> dict:
    return yaml.safe_load(WF.read_text())


# ── the guard itself ────────────────────────────────────────────────────────
def test_landed_check_fails_on_the_2026_10_04_incident_shape():
    good = {"mechanics_read_state": "measured", "cost_fidelity_read_state": "measured",
            "population": {"n": 45}}
    rc, msg = sgl.judge((date(2026, 9, 26), good), date(2026, 10, 4), 1)
    assert rc == 1 and "did NOT land" in msg


def test_landed_check_fails_when_nothing_ever_landed():
    assert sgl.judge(None, date(2026, 10, 4), 1)[0] == 1


def test_landed_check_fails_on_a_landed_record_that_measured_nothing():
    rep = {"mechanics_read_state": "producer_failed", "cost_fidelity_read_state": "producer_failed"}
    assert sgl.judge((date(2026, 10, 4), rep), date(2026, 10, 4), 1)[0] == 1


def test_landed_check_passes_a_fresh_record_with_one_dimension_measured():
    rep = {"mechanics_read_state": "measured", "cost_fidelity_read_state": "producer_failed",
           "population": {"n": 45}}
    assert sgl.judge((date(2026, 10, 4), rep), date(2026, 10, 4), 1)[0] == 0


# ── the workflow wiring the guard depends on ────────────────────────────────
def test_workflow_has_a_verify_landed_job_that_also_runs_for_the_deduped_run():
    jobs = _wf()["jobs"]
    assert "verify-landed" in jobs
    v = jobs["verify-landed"]
    assert v["needs"] == ["grade"]
    # it must run when grade was SKIPPED by dedupe — that is the green-over-nothing case
    assert "github.event_name == 'schedule'" in v["if"] and "!cancelled()" in v["if"]
    assert any("soak_grade_landed.py" in str(s.get("run", "")) for s in v["steps"])


def test_workflow_commits_the_per_leg_record_not_only_the_pointer():
    steps = _wf()["jobs"]["grade"]["steps"]
    land = [s for s in steps if "research-result" in str(s.get("uses", ""))]
    assert land, "the landing step is gone"
    extra = str(land[0]["with"].get("extra-paths", ""))
    assert "steps.run.outputs.out_path" in extra
    assert "docs/claude/work/SOAK-REPORT.md" in extra and "docs/claude/work/soak-state.json" in extra


def test_research_result_action_passes_extra_paths_to_the_commit():
    act = (REPO / ".github" / "actions" / "research-result" / "action.yml").read_text()
    assert "extra-paths:" in act
    assert "paths: ${{ steps.emit.outputs.path }} ${{ inputs.extra-paths }}" in act


# ── the crash that caused it ────────────────────────────────────────────────
def test_r3_tolerates_a_malformed_closed_at_and_reports_it():
    import r3_cost_fidelity as r3

    bad = {"id": 7, "account_id": "alpaca_paper", "closed_at": "2026-13-40T00:00:00Z"}
    assert r3._close_ts(bad) is None
    assert r3._close_ts({"closed_at": None}) is None
    assert r3._close_ts({"closed_at": "2026-10-01T00:00:00Z"}) is not None
    assert r3._malformed_closed_at([bad, {"id": 8, "closed_at": "2026-10-01T00:00:00Z"}]) == [
        {"trade_id": 7, "account_id": "alpaca_paper", "closed_at": "2026-13-40T00:00:00Z"}]


def test_soak_book_grade_writes_a_record_even_when_the_grade_crashes(tmp_path, monkeypatch):
    import soak_book_grade as sbg

    def boom(**_kw):
        raise ValueError("month must be in 1..12")

    monkeypatch.setattr(sbg, "build_report", boom)
    out = tmp_path / "2026-10-04.json"
    rc = sbg.main(["--out", str(out)])
    assert rc == 1, "both dimensions unmeasured must exit 1 (the workflow still lands rc=1)"
    rep = json.loads(out.read_text())
    assert rep["mechanics_read_state"] == "producer_failed"
    assert rep["population"]["n"] is None, "could not look is null, never a measured 0"
    assert "month must be in 1..12" in rep["error"]


# ── the soak alarm on the current pipeline ──────────────────────────────────
def test_pipeline_soak_items_are_due_only_when_not_accruing():
    import pipeline

    item = {"state": "queued", "due_when": {"kind": "observation", "clears_when": "x",
                                            "check_every_days": 7, "last_checked": "2026-01-01",
                                            "soak": {"subject": "bybit_1/x"}}}
    t = date(2026, 10, 4)
    assert not pipeline.is_due(item, t, soak_states={"bybit_1/x": "accruing"})
    assert pipeline.is_due(item, t, soak_states={"bybit_1/x": "dead"})
    assert pipeline.is_due(item, t, soak_states={}), "ungraded = could-not-look = due"


def test_soak_report_grades_the_four_states():
    import soak_report as sr

    assert sr._self_test() == 0
