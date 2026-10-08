"""RESEARCH-PIPELINE-REPAIR (2026-10-04), the dispatcher half: dispatch order, attribution,
e35 scope, id allocation. Each test plants the failing shape."""
from datetime import datetime, timezone
from pathlib import Path

from scripts.research import dispatch_queue as dq
from scripts.research.research_queue import QueueJob

NOW = datetime(2030, 1, 10, 12, tzinfo=timezone.utc)
TH = {"share_window_hours": 48, "aging_hours": 72, "themes": {"t": {"weight": 1}}}


def _job(uid, **kw):
    raw = {"id": uid, "status": "queued", "cadence": "once", "theme": "t", "priority": 2,
           "run": {"workflow": "x.yml"}, **kw}
    return QueueJob(path=Path(f"{uid}.yaml"), raw=raw)


def test_hand_written_unit_outranks_generated_at_equal_priority():
    gen = _job("RQ-20300110-049", generated={"family": "f"})
    hand = _job("RQ-20300110-101")
    assert [j.id for j in dq.fair_order([gen, hand], NOW, TH)] == ["RQ-20300110-101", "RQ-20300110-049"]
    # ... but priority still decides first
    gen1 = _job("RQ-20300110-049", priority=1, generated={"family": "f"})
    assert dq.fair_order([hand, gen1], NOW, TH)[0].id == "RQ-20300110-049"


def test_dispatcher_supplies_research_unit_when_the_workflow_declares_it():
    entry = {"id": "RQ-20300110-006", "run": {"workflow": "m20-exit-lever-sweep.yml", "inputs": {"legs": "x"}}}
    inputs, _ = dq.dispatch_inputs(entry, power_state="runnable")
    assert inputs["research_unit"] == "RQ-20300110-006" and inputs["power_state"] == "runnable"
    # a unit that names its own identity keeps it
    entry["run"]["inputs"]["research_unit"] = "RQ-OTHER-001"
    assert dq.dispatch_inputs(entry)[0]["research_unit"] == "RQ-OTHER-001"


def test_e35_unit_naming_an_out_of_scope_leg_is_not_due():
    bad = {"id": "RQ-20300110-001", "run": {"workflow": "e35-bracket-sweep.yml", "inputs": {"only": "ict_scalp_sol_15m"}}}
    due, why = dq._is_due({**bad, "status": "queued", "cadence": "once"}, NOW)
    assert not due and "out_of_scope" in why
    ok = {"id": "RQ-20300110-002", "status": "queued", "cadence": "once",
          "run": {"workflow": "e35-bracket-sweep.yml", "inputs": {"only": "trend_donchian"}}}
    assert dq._is_due(ok, NOW)[0]


def test_next_rq_id_skips_every_claimed_number():
    from scripts.research import next_rq_id as n
    # Hand-authored ids live in the 900-999 band since PI-20261005-RQ-ID-RACE-0001 (the generator
    # mints 001-899), so "skips every claimed number" is asserted inside that band.
    claimed = {"RQ-20301001-001": "worktree", "RQ-20301001-914": "origin/some-branch"}
    assert n.next_ids(claimed, "2030-10-01", 2) == ["RQ-20301001-915", "RQ-20301001-916"]
    # a generated id high in its own band must not push a hand id out of 900-999
    assert n.next_ids({"RQ-20301001-653": "origin/automation"}, "2030-10-01") == ["RQ-20301001-900"]
    assert n.next_ids(claimed, "2030-10-02") == ["RQ-20301002-900"]   # an empty day starts at the hand band
