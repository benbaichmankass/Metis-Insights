"""PROP-SILENCE-ALERTS: idle-fill and phone-heartbeat edges, and the null case."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.ops import attention_watch as a
from scripts.ops import prop_silence as ps

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _fills(days_ago, field="closed_at"):
    t = (NOW - timedelta(days=days_ago)).isoformat()
    return ("read", {"present": True, "count": 1, "fills": [{field: t}]})


def _idle(days_ago):
    return ps.probe_prop_idle("tradeify_1", NOW, lambda _p: _fills(days_ago))


def test_idle_thresholds():
    assert _idle(13.9)["status"] == "ok"
    w = _idle(14.0)
    assert (w["status"], w["level"], w["priority"]) == ("breached", "warn", "high")
    u = _idle(21.0)
    assert (u["status"], u["level"], u["priority"]) == ("breached", "urgent", "urgent")


def test_zero_days_is_ok_and_distinct_from_null():
    z = _idle(0)
    assert z["status"] == "ok" and z["days"] == 0.0
    for fetch in (lambda _p: ("unreadable", "down"),
                  lambda _p: ("read", {"present": False, "fills": []}),
                  lambda _p: ("read", {"present": True, "fills": []})):
        n = ps.probe_prop_idle("tradeify_1", NOW, fetch)
        assert n["status"] == "unknown" and n["days"] is None


def test_last_fill_uses_trade_time_not_reported_at():
    old = (NOW - timedelta(days=30)).isoformat()
    rows = [{"closed_at": old, "reported_at": NOW.isoformat()}]
    assert ps.last_fill_time(rows) == datetime.fromisoformat(old)
    assert ps.last_fill_time([{"created_at": old}]) == datetime.fromisoformat(old)
    assert ps.last_fill_time([{}]) is None


def _hb(at, mode="live", key=True):
    body = {"account_id": "breakout_2"}
    if key:
        body["phone_heartbeat"] = None if at is None else {"at": at.isoformat()}
    return ps.probe_phone_heartbeat(NOW, lambda _p: ("read", body), lambda _a: mode)


def test_heartbeat_edges():
    assert _hb(NOW - timedelta(minutes=9))["status"] == "ok"
    b = _hb(NOW - timedelta(minutes=11))
    assert (b["status"], b["priority"]) == ("breached", "urgent")
    assert _hb(None)["status"] == "breached"            # live, none ever posted
    assert _hb(None, mode="dry_run")["status"] == "ok"  # not live: not required
    assert _hb(None, key=False)["status"] == "unknown"  # error envelope: did not look
    assert ps.probe_phone_heartbeat(NOW, lambda _p: ("unreadable", "x"),
                                    lambda _a: "live")["status"] == "unknown"
    assert ps.probe_phone_heartbeat(NOW, lambda _p: ("read", {}), lambda _a: None)["status"] == "unknown"


def _view(probes):
    base = {k: {"status": "ok", "detail": "fine"} for k in a.PROBE_LABEL}
    base.update(probes)
    return {"now": NOW, "today": NOW.date(), "pipeline_readable": True,
            "pipeline_unreadable": 0, "stats": {"due": 0, "unrouted": 0,
            "unrouted_alarm": {"breached": []}}, "ranked": [], "ask_operator": [],
            "soak_read": "absent", "soaks": [], "probes": base}


def _msgs(probe, state):
    m, st = a.plan_messages(_view({"prop_idle_tradeify_1": probe}), state)
    return [x for x in m if "tradeify_1" in x[1] or x[1].startswith("✅")], st


def test_edge_warn_escalate_clear_and_unknown():
    k = "prop_idle_tradeify_1"
    seed = a.plan_messages(_view({}), {})[1]
    m, st = _msgs(_idle(15), seed)
    assert [p for p, _ in m] == ["high"]
    m, st = _msgs(_idle(16), st)
    assert m == []                                    # no re-alert inside 24 h
    m, st = _msgs(_idle(22), st)
    assert [p for p, _ in m] == ["urgent"]            # warn -> urgent sends now
    m, st = _msgs(ps._probe("unknown", "?"), st)
    assert m == [] and st["alarms"][k]["breached"]    # could-not-look never clears
    m, st = _msgs(_idle(1), st)
    assert len(m) == 1 and m[0][1].startswith("✅")   # one clear line
    assert not st["alarms"][k].get("breached")


def test_block_priority_urgent():
    blk, pri, _ = a.compose_block(_view({}), [("urgent", "x"), ("high", "y")])
    assert pri == "urgent"
