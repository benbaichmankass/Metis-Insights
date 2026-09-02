"""Tests for the COMMITTED decision record's read surface (Phase F / C3).

The cron and the committed path shipped in PR #10649. What was missing —
measured 2026-09-01 by grepping `*.py`/`*.ts`/`*.svelte`/`*.yml` for
`comms/strategy_reviews` and getting back the writer and the docs and **no
reader** — is a consumer. A record that is written and never read is the shape
`provenance-consumer-guard` exists to catch; here it is the C3 failure one level
up, because a durable packet that reaches no decision has not repaired anything.

⚠️ The properties under test are mostly about what the route REFUSES to say. The
easy version of this endpoint returns `graded: 0, rows: []` on a missing or
corrupt index, which reads exactly like "the fleet was graded and proposed
nothing" — a confident clean negative over a population nobody looked at. Those
cases are asserted individually below rather than folded into one "handles
errors" test, because they are DIFFERENT FACTS and collapsing them in the test
is how they end up collapsed in the code.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.web.api.routers import strategy_review as mod


@pytest.fixture()
def committed(tmp_path, monkeypatch):
    """Point the route at a throwaway repo root and return its reviews dir."""
    monkeypatch.setattr(mod, "repo_root", lambda: str(tmp_path))
    root = tmp_path / "comms" / "strategy_reviews"
    root.mkdir(parents=True)
    return root


_UNSET = object()


def _write_index(root, date, rows, generated_at=_UNSET):
    """Write an index the way the generator does.

    ``generated_at`` uses a sentinel rather than ``None`` as its default, so a
    test can write an index whose timestamp is genuinely absent or empty — the
    `undateable` cases. Defaulting on falsiness would silently substitute
    ``now()`` for the very value under test.
    """
    day = root / date
    day.mkdir(parents=True, exist_ok=True)
    mappings = [r for r in rows if isinstance(r, dict)]
    payload = {
        "generated_at": (
            datetime.now(timezone.utc).isoformat() if generated_at is _UNSET else generated_at
        ),
        "utc_date": date,
        "graded": len(mappings),
        "no_action_verdict": "hold",
        "actionable": sum(1 for r in mappings if r.get("actionable")),
        "by_action": {},
        "rows": rows,
    }
    (day / "INDEX.json").write_text(json.dumps(payload))
    return day


def _row(name, action="hold", actionable=False):
    return {"strategy": name, "proposed_action": action, "actionable": actionable}


# --- the three read states, never collapsed -------------------------------


def test_no_record_ever_committed_reads_absent_not_zero(tmp_path, monkeypatch):
    """`absent` is "nothing has ever been committed", and the counts are None.

    A `graded: 0` here would assert an observation nobody made — zero graded is
    a REAL reading (a run that graded nothing) and must stay distinguishable.
    """
    monkeypatch.setattr(mod, "repo_root", lambda: str(tmp_path))
    out = mod.get_committed_strategy_reviews()
    assert out["read_state"] == "absent"
    assert out["present"] is False
    assert out["graded"] is None and out["actionable"] is None


def test_unreadable_index_is_not_an_empty_fleet(committed):
    """FOUND-BUT-UNREADABLE must never render as "nothing graded".

    This is sub-class C of the diagnostic-provenance defect (an empty result
    read as a clean negative) and the consumer side of `silent-empty-guard`.
    """
    day = committed / "2026-09-01"
    day.mkdir()
    (day / "INDEX.json").write_text("{not json")
    out = mod.get_committed_strategy_reviews()
    assert out["read_state"] == "unreadable"
    assert out["graded"] is None, "an unreadable index must not report a count"
    assert out["rows"] == []
    assert out["error"] == "index_unreadable"


def test_successful_read_is_stamped_index_read(committed):
    _write_index(committed, "2026-09-01", [_row("a"), _row("b", "kill", True)])
    out = mod.get_committed_strategy_reviews()
    assert out["read_state"] == "index_read"
    assert out["present"] is True
    assert out["graded"] == 2 and out["actionable"] == 1


def test_a_date_with_no_index_is_absent_not_unreadable(committed):
    """"No record for that day" and "we could not read it" are different."""
    _write_index(committed, "2026-09-01", [_row("a")])
    out = mod.get_committed_strategy_reviews(date="2026-08-01")
    assert out["read_state"] == "absent"
    assert out["graded"] is None


# --- the denominator ------------------------------------------------------


def test_denominator_survives_the_actionable_filter(committed):
    """`graded` counts the whole fleet even when the rows are filtered.

    "1 actionable" over an unstated population is the unstated-denominator
    error; `returned` is what says how many rows came back.
    """
    rows = [_row("a"), _row("b"), _row("c", "kill", True)]
    _write_index(committed, "2026-09-01", rows)
    out = mod.get_committed_strategy_reviews(actionable_only=True)
    assert out["returned"] == 1
    assert out["graded"] == 3, "the filter must not shrink the denominator"
    assert out["actionable"] == 1
    assert [r["strategy"] for r in out["rows"]] == ["c"]


def test_every_hold_row_is_still_served_unfiltered(committed):
    """The index exists so "48 graded and held" is distinguishable from "only
    4 were graded at all". A route dropping HOLDs would undo that."""
    rows = [_row(f"s{i}") for i in range(48)] + [_row("k", "kill", True)]
    _write_index(committed, "2026-09-01", rows)
    out = mod.get_committed_strategy_reviews()
    assert out["returned"] == 49 and out["graded"] == 49


# --- freshness, four states, never collapsed ------------------------------


def test_fresh_within_the_window(committed):
    ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _write_index(committed, "2026-09-01", [_row("a")], generated_at=ts)
    out = mod.get_committed_strategy_reviews()
    assert out["freshness"] == "fresh"
    assert out["age_hours"] == pytest.approx(3.0, abs=0.2)


def test_stale_past_the_window(committed):
    """A stale packet rendered beside a confident action badge is
    indistinguishable from a current one — the defect `/api/bot/prop/status`
    grew `status_freshness` for."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    _write_index(committed, "2026-09-01", [_row("a")], generated_at=ts)
    out = mod.get_committed_strategy_reviews()
    assert out["freshness"] == "stale"


@pytest.mark.parametrize("bad", [None, "", "not-a-timestamp"])
def test_undateable_fails_safe_to_not_fresh(committed, bad):
    """A record that cannot be DATED cannot be shown to be CURRENT, so the
    fail-safe reading is not-fresh — matching `prop_balance`'s refusal on an
    undateable row rather than optimistically passing it."""
    _write_index(committed, "2026-09-01", [_row("a")], generated_at=bad)
    out = mod.get_committed_strategy_reviews()
    assert out["freshness"] == "undateable"
    assert out["age_hours"] is None, "an undateable record has no age, not age 0"


# --- packet presence ------------------------------------------------------


def test_packet_committed_flag_tracks_what_is_actually_there(committed):
    """Full packets ride along only where an action is proposed, so a HOLD
    without a packet is DESIGNED. The flag separates that from a real gap."""
    day = _write_index(committed, "2026-09-01", [_row("withpkt", "kill", True), _row("nopkt")])
    (day / "withpkt.json").write_text("{}")
    rows = {r["strategy"]: r["packet_committed"] for r in mod.get_committed_strategy_reviews()["rows"]}
    assert rows == {"withpkt": True, "nopkt": False}


# --- traversal + hygiene --------------------------------------------------


@pytest.mark.parametrize("bad", ["../etc", "2026-9-1", "2026-09-01/../..", "latest"])
def test_malformed_date_is_refused_not_resolved(committed, bad):
    _write_index(committed, "2026-09-01", [_row("a")])
    out = mod.get_committed_strategy_reviews(date=bad)
    assert out["error"] == "invalid_date"
    assert out["graded"] is None, "a refused request must not report a count"


def test_newest_date_is_served_by_default(committed):
    _write_index(committed, "2026-08-30", [_row("old")])
    _write_index(committed, "2026-09-01", [_row("new")])
    out = mod.get_committed_strategy_reviews()
    assert out["utc_date"] == "2026-09-01"
    assert out["available_dates"] == ["2026-09-01", "2026-08-30"]


def test_source_is_always_stamped(committed):
    """The two routes read DIFFERENT records and can legitimately disagree;
    serving one under the other's name is the semantic-substitution defect."""
    _write_index(committed, "2026-09-01", [_row("a")])
    for out in (
        mod.get_committed_strategy_reviews(),
        mod.get_committed_strategy_reviews(date="1999-01-01"),
        mod.get_committed_strategy_reviews(date="bad"),
    ):
        assert out["source"] == "comms/strategy_reviews"


def test_non_mapping_row_is_skipped_not_crashed(committed):
    """Grading 51 of 52 and saying so beats taking the route down."""
    _write_index(committed, "2026-09-01", [_row("a"), None, "junk"])
    out = mod.get_committed_strategy_reviews()
    assert [r["strategy"] for r in out["rows"]] == ["a"]


# --- the evidence floor: what `actionable: 0` actually means ---------------


def test_zero_actionable_over_an_ungradeable_fleet_is_not_a_clean_bill(committed):
    """The finding this block exists for, asserted end to end.

    Measured on the real 2026-09-01 run — population: all 52 enabled
    strategies, window 7 days — `n_closed` was 0 for 34 legs and never
    exceeded 8, so 52/52 sat under the n>=20 floor. `actionable: 0` there does
    NOT mean the fleet is healthy; it means nothing could be graded. A surface
    that cannot tell those apart reports a clean bill of health for a fleet
    nobody looked at.
    """
    rows = [dict(_row(f"s{i}"), below_evidence_floor=True) for i in range(52)]
    day = committed / "2026-09-01"
    day.mkdir()
    (day / "INDEX.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "utc_date": "2026-09-01", "graded": 52, "actionable": 0,
        "no_action_verdict": "hold", "min_closed_for_action": 20,
        "by_action": {"hold": 52}, "rows": rows,
    }))
    out = mod.get_committed_strategy_reviews()
    assert out["actionable"] == 0
    ev = out["evidence"]
    assert ev["floor_state"] == "none_gradeable"
    assert ev["below_floor"] == 52 and ev["gradeable"] == 0
    assert ev["min_closed_for_action"] == 20


def test_index_predating_the_field_grades_unknown_not_clean(committed):
    """An index written before the generator published the floor must grade
    `unknown` — WE COULD NOT LOOK. Reading it as "no row was below the floor"
    would assert every row had enough evidence, which is the opposite of what
    was measured, and re-deriving it from the English in `reasons` is the
    sub-class A defect that defers C4."""
    _write_index(committed, "2026-09-01", [_row("a"), _row("b")])
    ev = mod.get_committed_strategy_reviews()["evidence"]
    assert ev["floor_state"] == "unknown"
    assert ev["below_floor"] is None and ev["gradeable"] is None


def test_mixed_and_fully_gradeable_are_distinct(committed):
    mixed = [dict(_row("a"), below_evidence_floor=True),
             dict(_row("b", "kill", True), below_evidence_floor=False)]
    _write_index(committed, "2026-09-01", mixed)
    (committed / "2026-09-01" / "INDEX.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "utc_date": "2026-09-01", "graded": 2, "actionable": 1,
        "min_closed_for_action": 20, "rows": mixed,
    }))
    assert mod.get_committed_strategy_reviews()["evidence"]["floor_state"] == "partly_gradeable"

    allg = [dict(_row("a"), below_evidence_floor=False),
            dict(_row("b"), below_evidence_floor=False)]
    (committed / "2026-09-01" / "INDEX.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "utc_date": "2026-09-01", "graded": 2, "actionable": 0,
        "min_closed_for_action": 20, "rows": allg,
    }))
    assert mod.get_committed_strategy_reviews()["evidence"]["floor_state"] == "all_gradeable"


def test_generator_and_route_agree_without_re_deriving(committed):
    """Round-trip: the GENERATOR writes the index, the ROUTE reads it.

    This is the property that matters. The consumer re-deriving a rule the
    generator owns is what produced the 105-file PR, so the floor is published
    once and asserted here across the seam rather than spelled twice.
    """
    from scripts.ml.strategy_review_packet import MIN_CLOSED_FOR_ACTION, write_index

    rows = [
        {"strategy": "thin", "proposed_action": "hold", "actionable": False,
         "n_closed": 3, "below_evidence_floor": True},
        {"strategy": "thick", "proposed_action": "hold", "actionable": False,
         "n_closed": MIN_CLOSED_FOR_ACTION + 5, "below_evidence_floor": False},
    ]
    # write_index returns the INDEX.json path itself, not the day dir.
    written = write_index(rows, committed)
    payload = json.loads(written.read_text())
    assert payload["min_closed_for_action"] == MIN_CLOSED_FOR_ACTION
    assert payload["below_evidence_floor"] == 1

    out = mod.get_committed_strategy_reviews(date=payload["utc_date"])
    assert out["read_state"] == "index_read"
    ev = out["evidence"]
    assert ev["min_closed_for_action"] == MIN_CLOSED_FOR_ACTION
    assert ev["floor_state"] == "partly_gradeable"
    assert ev["below_floor"] == 1 and ev["gradeable"] == 1
