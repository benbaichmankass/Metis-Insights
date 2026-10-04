"""RESEARCH-PIPELINE-REPAIR (2026-10-04), the grader/replenisher half."""
from pathlib import Path

from scripts.research import queue_grade as qg
from scripts.research import queue_replenish as qr


def test_e35_template_never_generates_an_out_of_scope_leg():
    facts = qr.repo_facts(Path("."))
    legs = {p["leg"] for p in qr._leg_source("live_roster_legs_e35_sweepable", {}, facts)}
    assert legs and not any(leg.startswith("ict_scalp") for leg in legs)
    assert legs <= set(facts["legs"])


def test_failed_producer_row_does_not_veto_a_later_measured_row():
    row = lambda v, rs: ("r/x.jsonl", {"verdict": v, "read_state": rs})  # noqa: E731
    g = qg.grade_e5([row("not_applicable", "producer_failed"), row("indeterminate", "measured")], 1)
    assert g["verdict"] == "indeterminate"
    unit = {"cadence": "once", "decision_rule": {"id": "R"}, "generated": {"key": "k"}}
    d = qg.decide(unit, g, "2030-01-01")
    assert d["status"] == "done" and d["grading"]["awaiting"] == "more_data"
