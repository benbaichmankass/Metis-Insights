"""Format B, the three classes, and the two ways this can fail SILENTLY.

The failures worth testing here are not "does it render" — they are the two the
repo has actually paid for: a decision suppressed by a rate limiter (silence
exactly when action is needed) and a limiter whose state resets on restart
(202 of 376 CRITICALs from one un-latched alarm).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.runtime import claude_ping as cp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "_state_path", lambda: tmp_path / "claude_ping_state.json")
    monkeypatch.delenv("CLAUDE_PING_LIFECYCLE", raising=False)


# ── the format itself ────────────────────────────────────────────────────

def test_format_is_two_lines_what_then_what_changed():
    out = cp.format_ping(
        "MERGED #10666 · alpaca extended-hours close · Tier-2 · deployed ab6985b3",
        "Close-fail pages drop ~160 → 7 per wedged session.",
        icon="✅")
    first, second = out.split("\n")
    assert first.startswith("✅ MERGED #10666")
    assert second.startswith("   "), "line 2 is indented so line 1 scans alone"
    assert "160" in second


def test_unproven_rides_the_why_line_not_a_third_line():
    out = cp.format_ping("DEPLOYED x", "It does y.", unproven="Not yet observed working.")
    assert len(out.split("\n")) == 2, (
        "a third line drifts toward the structured format the operator rejected")
    assert "Not yet observed working." in out.split("\n")[1]


def test_a_ping_with_no_why_is_refused():
    """The refusal IS the design: an event with nothing to say about what
    changed is activity, and activity must not ping."""
    with pytest.raises(ValueError, match="why must be non-empty"):
        cp.format_ping("SOMETHING HAPPENED", "")


def test_a_ping_with_no_headline_is_refused():
    with pytest.raises(ValueError, match="headline must be non-empty"):
        cp.format_ping("", "because")


# ── the class gating ─────────────────────────────────────────────────────

def test_a_decision_is_never_rate_limited():
    """Suppressing 'this needs your approval' is the desensitized-alarm failure
    INVERTED — silence exactly when action is required."""
    for _ in range(50):
        admit, reason = cp.admits(cp.DECISION)
        assert admit, reason
        cp.record_sent(cp.DECISION)


def test_lifecycle_collapses_a_burst_to_one():
    now = 1_000_000.0
    admit, _ = cp.admits(cp.LIFECYCLE, now=now)
    assert admit
    cp.record_sent(cp.LIFECYCLE, now=now)
    # a fan-out of six sub-sessions seconds apart is ONE line, not six
    for offset in (1, 2, 5, 30, 120):
        admit, reason = cp.admits(cp.LIFECYCLE, now=now + offset)
        assert not admit, f"burst at +{offset}s should be held: {reason}"
        assert "rate-limited" in reason
    admit, _ = cp.admits(cp.LIFECYCLE, now=now + 301)
    assert admit, "the window must reopen, not latch shut"


def test_lifecycle_has_its_own_switch(monkeypatch):
    """One flag to turn off, not a refactor — it is the class most likely to
    train the reader to skim."""
    assert cp.admits(cp.LIFECYCLE)[0] is True
    monkeypatch.setenv("CLAUDE_PING_LIFECYCLE", "0")
    admit, reason = cp.admits(cp.LIFECYCLE)
    assert admit is False and "CLAUDE_PING_LIFECYCLE" in reason
    # ⚠️ and it must NOT take the other classes down with it
    assert cp.admits(cp.DECISION)[0] is True
    assert cp.admits(cp.STATE_CHANGE)[0] is True


def test_state_changes_are_not_throttled_against_each_other():
    """The CLASS is the filter: every state change is a real change in the
    world, so throttling them would drop real events rather than noise."""
    now = 1_000_000.0
    for offset in range(0, 10):
        admit, _ = cp.admits(cp.STATE_CHANGE, now=now + offset)
        assert admit
        cp.record_sent(cp.STATE_CHANGE, now=now + offset)


# ── the two silent-failure modes ─────────────────────────────────────────

def test_the_limiter_is_durable_not_per_process(tmp_path, monkeypatch):
    """A module-global resets on restart — that is how one alarm produced 202
    of 376 CRITICALs in a window. Simulate a restart by re-importing."""
    now = 1_000_000.0
    cp.record_sent(cp.LIFECYCLE, now=now)
    import importlib
    fresh = importlib.reload(cp)
    monkeypatch.setattr(fresh, "_state_path",
                        lambda: tmp_path / "claude_ping_state.json")
    admit, reason = fresh.admits(fresh.LIFECYCLE, now=now + 10)
    assert not admit, f"a restart must not re-arm the limiter: {reason}"


def test_an_unreadable_limiter_SENDS(tmp_path, monkeypatch):
    """Failing loud is the only safe direction on a notification path: a broken
    limiter that suppressed is indistinguishable from a quiet day."""
    bad = tmp_path / "claude_ping_state.json"
    bad.write_text("{ this is not json")
    monkeypatch.setattr(cp, "_state_path", lambda: bad)
    admit, _ = cp.admits(cp.LIFECYCLE)
    assert admit is True


def test_an_unknown_class_is_refused_not_defaulted():
    with pytest.raises(ValueError, match="unknown ping class"):
        cp.admits("chatter")
