"""The per-tick total must be measured, and measured honestly.

`src/main.py`'s tick is a chain of a dozen best-effort hooks. Each is
individually bounded; nothing measured the SUM. Both June 2026 wedges were "a
per-tick cost that was fine in isolation" — the defence each time bounded the
NEW component and never the total.

Two properties are asserted here, and they are the same two the exposure soak
asserts, for the same reasons:

1. **The max survives the write cadence.** Persisting on a cadence must not lose
   the peak between writes, because the peak is the whole point.
2. **"Not timed" is not "took no time."** A tick whose start marker is missing
   reports None, never 0.0.

And one that is specific to living on the live trader's main loop: the
measurement must never be able to break the tick it measures.
"""

from __future__ import annotations

import json

import pytest

from src.runtime import tick_cost as tc


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "state_file_path", lambda: tmp_path / "tick_cost.json")
    tc._reset_for_tests()
    yield
    tc._reset_for_tests()


def _tick(ms: float, monkeypatch):
    """Drive one measured tick of a controlled duration."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(tc.time, "monotonic", lambda: clock["t"])
    tc.begin_tick()
    clock["t"] += ms / 1000.0
    return tc.end_tick()


# ---------------------------------------------------------------------------
# The peak is the point
# ---------------------------------------------------------------------------

def test_the_max_is_retained_across_ticks(monkeypatch):
    _tick(10.0, monkeypatch)
    _tick(250.0, monkeypatch)
    _tick(12.0, monkeypatch)
    snap = tc.snapshot()
    assert snap["max_ms"] == pytest.approx(250.0, abs=1.0)
    assert snap["last_ms"] == pytest.approx(12.0, abs=1.0)
    assert snap["ticks_measured"] == 3


def test_the_max_survives_the_write_cadence(monkeypatch):
    """A spike between two persists must still reach the persisted payload.

    This is the exposure-soak lesson applied here: sampling on a cadence is fine
    only because the ACCUMULATOR runs every tick. If the max were computed from
    what happened to be written, the peak would be invisible.
    """
    monkeypatch.setenv(tc._WRITE_CADENCE_ENV, "999999")  # effectively never
    _tick(10.0, monkeypatch)
    _tick(4000.0, monkeypatch)  # the spike, between writes
    _tick(10.0, monkeypatch)
    tc.write_state_file()  # forced persist
    payload = json.loads(tc.state_file_path().read_text())
    assert payload["max_ms"] == pytest.approx(4000.0, abs=1.0)


def test_the_max_ships_with_its_denominator(monkeypatch):
    _tick(50.0, monkeypatch)
    snap = tc.snapshot()
    assert snap["max_ms"] is not None
    assert snap["ticks_measured"] == 1, (
        "a max over 1 tick and a max over 1000 are different claims, so the "
        "denominator must never be omitted"
    )
    assert snap["max_at_utc"], "the peak must be dated"


# ---------------------------------------------------------------------------
# Not timed is not zero
# ---------------------------------------------------------------------------

def test_end_without_begin_reports_none_not_zero():
    assert tc.end_tick() is None
    assert tc.snapshot()["last_ms"] is None
    assert tc.snapshot()["ticks_measured"] == 0, (
        "an untimed tick must not inflate the denominator"
    )


def test_mean_is_none_rather_than_zero_before_any_tick():
    assert tc.snapshot()["mean_ms"] is None


# ---------------------------------------------------------------------------
# It must never break the tick it measures
# ---------------------------------------------------------------------------

def test_begin_never_raises_on_a_broken_clock(monkeypatch):
    monkeypatch.setattr(
        tc.time, "monotonic",
        lambda: (_ for _ in ()).throw(RuntimeError("clock gone")),
    )
    tc.begin_tick()  # must not raise
    assert tc.end_tick() is None


def test_write_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(tc, "state_file_path",
                        lambda: (_ for _ in ()).throw(OSError("no fs")))
    assert tc.write_state_file() is False  # no raise


def test_a_write_failure_does_not_lose_the_in_memory_max(monkeypatch):
    _tick(120.0, monkeypatch)
    monkeypatch.setattr(tc, "state_file_path",
                        lambda: (_ for _ in ()).throw(OSError("no fs")))
    assert tc.write_state_file() is False
    assert tc.snapshot()["max_ms"] == pytest.approx(120.0, abs=1.0)


# ---------------------------------------------------------------------------
# Cadence knob, fail-ON
# ---------------------------------------------------------------------------

def test_cadence_defaults_on(monkeypatch):
    monkeypatch.delenv(tc._WRITE_CADENCE_ENV, raising=False)
    assert tc.write_cadence_seconds() == tc._DEFAULT_WRITE_CADENCE_S > 0


def test_a_garbage_cadence_falls_back_rather_than_disabling(monkeypatch):
    monkeypatch.setenv(tc._WRITE_CADENCE_ENV, "banana")
    assert tc.write_cadence_seconds() == tc._DEFAULT_WRITE_CADENCE_S


# ---------------------------------------------------------------------------
# Reader envelope
# ---------------------------------------------------------------------------

def test_read_state_absent_is_present_false_not_an_empty_success():
    env = tc.read_state()
    assert env["present"] is False
    assert env.get("max_ms") is None


def test_read_state_reports_staleness(monkeypatch):
    _tick(30.0, monkeypatch)
    tc.write_state_file()
    env = tc.read_state()
    assert env["present"] is True
    assert env["max_ms"] == pytest.approx(30.0, abs=1.0)
    assert env["age_seconds"] is not None and env["age_seconds"] >= 0


def test_corrupt_state_file_surfaces_an_error_not_a_silent_default():
    p = tc.state_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    env = tc.read_state()
    assert env["present"] is False
    assert "error" in env, "a corrupt file must not read as a clean absence"


# ---------------------------------------------------------------------------
# The deliberate non-feature
# ---------------------------------------------------------------------------

def test_this_module_enforces_no_budget(monkeypatch):
    """A 4-second tick is recorded, not refused.

    Setting a cap without a distribution behind it is the exposure-ceiling
    mistake. If a budget is ever added it is a separate, evidenced change — this
    test exists so nobody adds one here by reflex.
    """
    assert _tick(4000.0, monkeypatch) == pytest.approx(4000.0, abs=1.0)
    assert tc.snapshot()["max_ms"] == pytest.approx(4000.0, abs=1.0)
    assert not any(
        n in dir(tc) for n in ("enforce_budget", "budget_exceeded", "refuse_tick")
    )
