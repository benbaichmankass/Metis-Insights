"""BLOCKER-WATCH: stale blockers, stalled queue, overdue observations."""
from __future__ import annotations

from datetime import datetime, timezone

from scripts.ops import attention_watch as aw
from scripts.ops import blocker_watch as bw
from scripts.ops import render_daily_brief as rdb

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def test_self_test_passes():
    assert bw._self_test() == 0


def _scan(rows, units=None, pipe=None):
    return bw.scan(rows, pipe or {}, units or {}, NOW)


def test_stale_block_and_denominators():
    rep = _scan([{"id": "A", "state": "done"},
                 {"id": "B", "state": "blocked", "updated_at": "2026-10-09T00:00:00Z",
                  "blocked_on": [{"kind": "work_item", "ref": "A"}]}])
    assert [f["key"] for f in rep["findings"]] == ["STALE_BLOCK:B:A"]
    assert rep["denominators"]["rows_scanned"] == 2 and rep["denominators"]["blocked_rows"] == 1


def test_live_blocker_is_not_stale():
    rep = _scan([{"id": "A", "state": "in_flight"},
                 {"id": "B", "state": "blocked", "blocked_on": ["A"]}])
    assert rep["findings"] == []


def test_unit_never_dispatched_is_not_running_and_taints_its_blocked_row():
    units = {"RQ-1": {"id": "RQ-1", "status": "queued", "last_dispatched_at": None,
                      "run": {"workflow": "session-local"}}}
    rep = _scan([{"id": "R", "state": "blocked", "blocked_on": [{"kind": "research_unit", "ref": "RQ-1"}]}], units)
    keys = {f["key"] for f in rep["findings"]}
    assert keys == {"NOT_RUNNING:RQ-1", "NOT_RUNNING:R:RQ-1"}
    assert next(f for f in rep["findings"] if f["key"] == "NOT_RUNNING:RQ-1")["needs_lane"] is True


def test_overdue_observation_and_missing_observation_counted_apart():
    rep = _scan([{"id": "X", "state": "landed_unproven", "observation": {"what": "w", "due_by": "2026-10-01T00:00Z"}},
                 {"id": "Y", "state": "landed_unproven"}])
    assert [f["key"] for f in rep["findings"]] == ["UNPROVEN_OVERDUE:X"]
    assert rep["denominators"]["landed_unproven_no_observation"] == 1


def _view(findings, read="read"):
    return {"now": NOW, "today": NOW.date(), "pipeline_readable": True, "pipeline_unreadable": 0,
            "stats": {"due": 0, "unrouted": 0, "unrouted_alarm": {"breached": []}},
            "ranked": [], "ask_operator": [], "soak_read": "absent", "soaks": [],
            "probes": {k: {"status": aw.OK, "detail": "fine"} for k in aw.PROBE_LABEL},
            "blocker_read": read, "blocker_findings": findings}


def _f(key, kind="NOT_RUNNING"):
    return {"key": key, "kind": kind, "id": key.split(":")[1], "evidence": "e"}


def _blocker_msgs(msgs):
    return [b for _, b in msgs if b.startswith("🧱")]


def test_alert_is_once_per_new_finding_and_seed_is_a_count_line():
    state = {"last_digest_date": NOW.date().isoformat()}
    m1, s1 = aw.plan_messages(_view([_f("NOT_RUNNING:RQ-1")]), state)
    assert len(_blocker_msgs(m1)) == 1 and "at deploy" in _blocker_msgs(m1)[0]
    m2, s2 = aw.plan_messages(_view([_f("NOT_RUNNING:RQ-1")]), s1)
    assert _blocker_msgs(m2) == []                                   # never re-paged
    m3, s3 = aw.plan_messages(_view([_f("NOT_RUNNING:RQ-1"), _f("STALE_BLOCK:B:A", "STALE_BLOCK")]), s2)
    assert len(_blocker_msgs(m3)) == 1 and "STALE_BLOCK B" in _blocker_msgs(m3)[0]
    m4, s4 = aw.plan_messages(_view([_f("NOT_RUNNING:RQ-1")]), s3)   # fixed -> forgotten
    assert _blocker_msgs(m4) == []
    m5, _ = aw.plan_messages(_view([_f("NOT_RUNNING:RQ-1"), _f("STALE_BLOCK:B:A", "STALE_BLOCK")]), s4)
    assert len(_blocker_msgs(m5)) == 1                               # recurrence alerts again


def test_unreadable_source_neither_alerts_nor_clears():
    state = {"last_digest_date": NOW.date().isoformat(), "seen_blocker_findings": ["NOT_RUNNING:RQ-1"],
             "seed_blocker_watch_sent_at": "x"}
    m, s = aw.plan_messages(_view([], read="unreadable"), state)
    assert _blocker_msgs(m) == [] and s["seen_blocker_findings"] == ["NOT_RUNNING:RQ-1"]


def test_brief_section0_carries_blocker_watch():
    rep = bw.scan([{"id": "A", "state": "done"}], {}, {}, NOW)
    rep.update(readable=True, sources={})
    out = rdb._section0({"pipeline": {"section0Lines": ["## §0"]}, "blockerWatch": rep})
    assert any("BLOCKER-WATCH" in x for x in out) and any("Scanned: 1 rows" in x for x in out)
    out2 = rdb._section0({"pipeline": {"section0Lines": ["## §0"]}, "blockerWatch": None})
    assert any("COULD NOT BE RUN" in x for x in out2)
