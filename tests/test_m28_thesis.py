"""M28 — tests for the TradeThesis core object + lifecycle state machine."""

from __future__ import annotations

import pytest

from src.units.strategies.macro_thesis.thesis import (
    CLOSE_REASONS,
    DIRECTIONS,
    EXPRESS_AS,
    FREE_SOURCES,
    STATUSES,
    TradeThesis,
    can_transition,
    new_thesis_id,
    transition,
    would_transition,
)


def _draft(**kw):
    base = dict(thesis_id=new_thesis_id("01ABC"), created_at="2026-07-23T00:00:00Z",
                updated_at="2026-07-23T00:00:00Z")
    base.update(kw)
    return TradeThesis(**base)


# --------------------------------------------------------------------------
# vocab + id
# --------------------------------------------------------------------------

def test_vocabularies():
    assert STATUSES == {"draft", "active", "invalidated", "closed", "expired"}
    assert DIRECTIONS == {"long", "short"}
    assert "debit_vertical" in EXPRESS_AS
    assert CLOSE_REASONS == {"target", "invalidation", "event_outcome",
                             "time_barrier", "manual"}
    assert {"fred", "sec_edgar", "llm_extractor"} <= FREE_SOURCES


def test_new_thesis_id():
    assert new_thesis_id("01ABC") == "mth-01ABC"


# --------------------------------------------------------------------------
# lifecycle edges (§1a)
# --------------------------------------------------------------------------

def test_legal_edges():
    assert can_transition("draft", "active")
    assert can_transition("draft", "expired")
    assert can_transition("active", "invalidated")
    assert can_transition("active", "closed")
    assert can_transition("invalidated", "closed")


def test_illegal_edges():
    assert not can_transition("draft", "closed")        # must activate first
    assert not can_transition("draft", "invalidated")
    assert not can_transition("closed", "active")        # terminal
    assert not can_transition("expired", "active")       # terminal
    assert not can_transition("active", "expired")       # expired is a draft-only end


# --------------------------------------------------------------------------
# transition (immutable-by-copy)
# --------------------------------------------------------------------------

def test_transition_returns_new_object():
    d = _draft()
    a = transition(d, "active", updated_at="2026-07-23T06:00:00Z")
    assert d.status == "draft"            # input untouched
    assert a.status == "active"
    assert a.updated_at == "2026-07-23T06:00:00Z"
    assert a.thesis_id == d.thesis_id


def test_transition_close_requires_reason():
    a = transition(_draft(), "active", updated_at="t1")
    with pytest.raises(ValueError):
        transition(a, "closed", updated_at="t2")                     # no close_reason
    with pytest.raises(ValueError):
        transition(a, "closed", updated_at="t2", close_reason="bogus")
    closed = transition(a, "closed", updated_at="t2",
                        close_reason="target", realized_pnl=12.5)
    assert closed.status == "closed"
    assert closed.close_reason == "target"
    assert closed.realized_pnl == 12.5


def test_transition_illegal_raises():
    with pytest.raises(ValueError):
        transition(_draft(), "closed", updated_at="t", close_reason="target")
    with pytest.raises(ValueError):
        transition(_draft(), "nonsense", updated_at="t")


def test_full_lifecycle_paths():
    # target path
    a = transition(_draft(), "active", updated_at="t")
    assert transition(a, "closed", updated_at="t2", close_reason="target").status == "closed"
    # invalidation path: active -> invalidated -> closed
    inv = transition(a, "invalidated", updated_at="t2")
    assert inv.status == "invalidated"
    assert transition(inv, "closed", updated_at="t3",
                      close_reason="invalidation").status == "closed"
    # expiry path: draft -> expired
    assert transition(_draft(), "expired", updated_at="t").status == "expired"


def test_is_terminal():
    assert not _draft().is_terminal()
    assert transition(_draft(), "expired", updated_at="t").is_terminal()
    a = transition(_draft(), "active", updated_at="t")
    assert not a.is_terminal()
    assert transition(a, "closed", updated_at="t2", close_reason="manual").is_terminal()


# --------------------------------------------------------------------------
# would_transition (observe-only soak)
# --------------------------------------------------------------------------

def test_would_transition_logs_without_applying():
    d = _draft()
    rec = would_transition(d, "active", at="t9")
    assert rec == {"thesis_id": d.thesis_id, "from": "draft", "to": "active",
                   "close_reason": None, "at": "t9"}
    assert d.status == "draft"           # not applied

    a = transition(d, "active", updated_at="t")
    rec2 = would_transition(a, "closed", at="t9", close_reason="event_outcome")
    assert rec2["close_reason"] == "event_outcome"


def test_would_transition_none_for_illegal():
    assert would_transition(_draft(), "closed", at="t") is None


# --------------------------------------------------------------------------
# serialization round-trip
# --------------------------------------------------------------------------

def test_to_from_dict_round_trip():
    t = _draft(rationale="easing cycle -> duration bid", direction="long",
               world_view={"regime": "easing", "theme": "duration"},
               watched_events=[{"event_id": "evt-fomc-1",
                                "on_outcome": [{"if": {"field": "direction", "op": "eq",
                                                       "value": "dovish"}, "action": "add"}]}],
               instrument={"symbol": "TLT", "venue": "alpaca", "express_as": "debit_vertical"},
               thesis_conviction=0.62)
    row = t.to_dict()
    assert row["direction"] == "long"
    assert row["thesis_conviction"] == 0.62
    back = TradeThesis.from_dict(row)
    assert back == t


def test_from_dict_ignores_unknown_keys():
    row = {"thesis_id": "mth-x", "created_at": "t", "updated_at": "t",
           "status": "draft", "some_future_field": 123}
    back = TradeThesis.from_dict(row)
    assert back.thesis_id == "mth-x"
    assert not hasattr(back, "some_future_field")
