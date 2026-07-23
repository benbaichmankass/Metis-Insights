"""M28 P2 — tests for the point-in-time event + thesis-link store."""

from __future__ import annotations

from src.units.strategies.macro_thesis.event_store import (
    read_event_links,
    read_events,
    read_events_by_status,
    read_latest_events,
    resolve_all,
    write_event_links,
    write_events,
)


def _evt(event_id, status, observed_at, **extra):
    row = {"event_id": event_id, "status": status, "observed_at": observed_at}
    row.update(extra)
    return row


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

def test_write_then_read_events_newest_first(tmp_path):
    p = tmp_path / "events.jsonl"
    n = write_events([
        _evt("evt-1", "scheduled", "2026-07-23T00:00:00Z"),
        _evt("evt-2", "scheduled", "2026-07-23T01:00:00Z"),
    ], path=p)
    assert n == 2
    rows = read_events(path=p)
    assert len(rows) == 2
    assert rows[0]["event_id"] == "evt-2"   # newest-first = append order reversed


def test_latest_events_supersedes_scheduled_with_resolved(tmp_path):
    p = tmp_path / "events.jsonl"
    # scheduled, then the SAME event resolves later (a new line, not an overwrite)
    write_events([_evt("evt-1", "scheduled", "2026-07-23T00:00:00Z")], path=p)
    write_events([
        _evt("evt-1", "resolved", "2026-07-23T18:00:00Z",
             realized_outcome={"direction": "hawkish"})
    ], path=p)
    assert len(read_events(path=p)) == 2        # both retained (history preserved)
    latest = read_latest_events(path=p)
    assert latest["evt-1"]["status"] == "resolved"
    assert latest["evt-1"]["realized_outcome"] == {"direction": "hawkish"}


def test_read_events_by_status(tmp_path):
    p = tmp_path / "events.jsonl"
    write_events([
        _evt("evt-1", "scheduled", "t1"),
        _evt("evt-2", "scheduled", "t1"),
        _evt("evt-1", "resolved", "t2", realized_outcome={"x": 1}),
    ], path=p)
    scheduled = read_events_by_status("scheduled", path=p)
    resolved = read_events_by_status("resolved", path=p)
    assert {e["event_id"] for e in scheduled} == {"evt-2"}   # evt-1 now resolved
    assert {e["event_id"] for e in resolved} == {"evt-1"}


def test_latest_events_ignores_rows_missing_event_id(tmp_path):
    p = tmp_path / "events.jsonl"
    write_events([{"status": "scheduled", "observed_at": "t"},
                  _evt("evt-1", "scheduled", "t")], path=p)
    assert list(read_latest_events(path=p).keys()) == ["evt-1"]


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------

def test_write_then_read_links_filter_by_event(tmp_path):
    p = tmp_path / "links.jsonl"
    n = write_event_links([
        {"thesis_id": "mth-A", "event_id": "evt-1", "on_outcome": []},
        {"thesis_id": "mth-B", "event_id": "evt-2", "on_outcome": []},
    ], path=p)
    assert n == 2
    assert len(read_event_links(path=p)) == 2
    only1 = read_event_links(path=p, event_id="evt-1")
    assert [x["thesis_id"] for x in only1] == ["mth-A"]


# --------------------------------------------------------------------------
# resolve_all — ties resolved events to linked theses (observe-only)
# --------------------------------------------------------------------------

def test_resolve_all_ties_resolved_events_to_theses(tmp_path):
    ep = tmp_path / "events.jsonl"
    lp = tmp_path / "links.jsonl"
    write_events([
        _evt("evt-1", "scheduled", "t1"),           # scheduled -> not resolved yet
        _evt("evt-1", "resolved", "t2",
             realized_outcome={"direction": "hawkish", "surprise": 0.4}),
        _evt("evt-2", "scheduled", "t1"),           # still scheduled -> no actions
    ], path=ep)
    write_event_links([
        {"thesis_id": "mth-A", "event_id": "evt-1",
         "on_outcome": [{"if": {"field": "direction", "op": "eq", "value": "hawkish"},
                         "action": "exit"}]},
        {"thesis_id": "mth-B", "event_id": "evt-2",
         "on_outcome": [{"if": {"field": "x", "op": "eq", "value": 1}, "action": "add"}]},
    ], path=lp)
    actions = resolve_all(events_path=ep, links_path=lp)
    by = {a["thesis_id"]: a["action"] for a in actions}
    assert by == {"mth-A": "exit"}   # only the resolved event's linked thesis fires


def test_resolve_all_empty_when_nothing_resolved(tmp_path):
    ep = tmp_path / "events.jsonl"
    lp = tmp_path / "links.jsonl"
    write_events([_evt("evt-1", "scheduled", "t1")], path=ep)
    write_event_links([{"thesis_id": "A", "event_id": "evt-1",
                        "on_outcome": [{"if": {"field": "x", "op": "eq", "value": 1},
                                        "action": "exit"}]}], path=lp)
    assert resolve_all(events_path=ep, links_path=lp) == []


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

def test_read_missing_files_are_empty(tmp_path):
    assert read_events(path=tmp_path / "nope.jsonl") == []
    assert read_latest_events(path=tmp_path / "nope.jsonl") == {}
    assert read_event_links(path=tmp_path / "nope.jsonl") == []
    assert resolve_all(events_path=tmp_path / "a.jsonl", links_path=tmp_path / "b.jsonl") == []


def test_read_skips_bad_lines(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text('{"event_id":"evt-1","status":"scheduled","observed_at":"t"}\nNOT JSON\n\n')
    assert len(read_events(path=p)) == 1
