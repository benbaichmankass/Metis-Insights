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


# ----------------------------------------- per-hook split (2026-08-10)
def _tick_hook_from_main():
    """Load the REAL src/main.py::_tick_hook without importing src.main's deps
    (ccxt/dotenv are not present in every test env). Testing a copy would test
    nothing — the point is that the LIVE wrapper is inert on failure."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "_tick_hook")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<extract>", "exec"), ns)
    return ns["_tick_hook"]


def test_per_hook_split_reports_its_own_attribution_coverage():
    """A split that reports only the hooks it instrumented invites the reader to
    conclude those hooks ARE the cost; they are a lower bound on it.

    Motivating measurement (2026-08-10): /api/diag/tick_cost read 253s mean with
    NO attribution, which is why the tick's 5-minute cadence could not be acted
    on. `attributed_pct` is the rCoverage/pnlCoverage discipline applied here —
    never a bare figure over an unstated denominator.
    """
    import time
    from src.runtime import tick_cost as tc
    tc._reset_for_tests()
    for _ in range(3):
        tc.begin_tick()
        with tc.hook("instrumented"):
            time.sleep(0.02)
        time.sleep(0.02)          # deliberately UNinstrumented
        tc.end_tick()
    s = tc.snapshot()
    assert s["ticks_measured"] == 3
    assert s["hooks"]["instrumented"]["n"] == 3
    # roughly half the tick is unattributed, and the envelope SAYS so
    assert 20.0 < s["attributed_pct"] < 80.0, s["attributed_pct"]
    assert s["hooks_attributed_mean_ms"] < s["mean_ms"]


def test_a_hook_that_raises_is_still_timed():
    """A hook that burns 40s and then throws is precisely the one worth seeing;
    it must not vanish from the split."""
    from src.runtime import tick_cost as tc
    tc._reset_for_tests()
    tc.begin_tick()
    try:
        with tc.hook("boom"):
            raise RuntimeError("x")
    except RuntimeError:
        pass
    tc.end_tick()
    assert tc.snapshot()["hooks"]["boom"]["n"] == 1


def test_hook_names_are_bounded_and_overflow_is_declared():
    """The module's contract is a FIXED-SIZE payload on a 2-core box. A caller
    generating names dynamically (per-symbol, per-account) must be refused —
    and the refusal COUNTED, so a truncated split cannot read as a whole one."""
    from src.runtime import tick_cost as tc
    tc._reset_for_tests()
    for i in range(tc._MAX_HOOK_NAMES + 4):
        tc.record_hook(f"dyn_{i}", 1.0)
    s = tc.snapshot()
    assert len(s["hooks"]) == tc._MAX_HOOK_NAMES
    assert s["hook_names_refused"] == 4


def test_attribution_is_none_not_zero_before_any_tick():
    """'We have not measured a tick yet' is not '0% of the tick is attributed'."""
    from src.runtime import tick_cost as tc
    tc._reset_for_tests()
    s = tc.snapshot()
    assert s["attributed_pct"] is None
    assert s["hooks_attributed_mean_ms"] is None


def test_main_tick_hook_is_inert_when_the_measurement_module_is_unimportable():
    """This wraps the LIVE trading loop. An instrumentation import error must
    never stop a tick from running — and must never swallow the body's own
    exception, since each hook keeps its existing handler."""
    import sys
    _tick_hook = _tick_hook_from_main()

    ran = []
    with _tick_hook("ok"):
        ran.append("body")
    assert ran == ["body"]

    # the body's exception propagates (the caller handles it, not us)
    import pytest as _pytest
    with _pytest.raises(ValueError):
        with _tick_hook("raises"):
            raise ValueError("must propagate")

    real = sys.modules.pop("src.runtime.tick_cost", None)

    class _Boom:
        def __getattr__(self, k):
            raise ImportError("simulated")

    sys.modules["src.runtime.tick_cost"] = _Boom()
    try:
        with _tick_hook("fallback"):
            ran.append("still ran")
    finally:
        if real is not None:
            sys.modules["src.runtime.tick_cost"] = real
        else:
            sys.modules.pop("src.runtime.tick_cost", None)
    assert ran == ["body", "still ran"]


# --- Attribution coverage must be a coverage -------------------------------
#
# BL-20260811-TICKCOST-ATTRIBUTED-PCT-DOUBLE-COUNTS-NESTED-HOOKS. The wraps
# NEST — `order_monitor` wraps 14 `monitor.*` hooks — and summing every slot
# counted each parent together with its own children. Live that read
# `attributed_pct: 136.8`, and the documented interpretation
# ("100 - attributed_pct is every other hook COMBINED") then yields -36.8%,
# which is not a quantity. No test asserted the bound, which is how it shipped.


def _nested_tick(monkeypatch, *, parent_ms: float, child_ms: float, n_children: int):
    """One tick: a top-level hook wrapping `n_children` nested hooks."""
    clock = {"t": 0.0}
    monkeypatch.setattr(tc.time, "monotonic", lambda: clock["t"])
    tc.begin_tick()
    with tc.hook("order_monitor"):
        for i in range(n_children):
            with tc.hook(f"monitor.child_{i}"):
                clock["t"] += child_ms / 1000.0
        clock["t"] += parent_ms / 1000.0
    tc.end_tick()


def test_attributed_pct_never_exceeds_100_with_nested_hooks(monkeypatch):
    """The regression. Pre-fix this reported ~190% for these numbers."""
    _nested_tick(monkeypatch, parent_ms=10.0, child_ms=30.0, n_children=3)
    snap = tc.snapshot()
    assert snap["attributed_pct"] is not None
    assert snap["attributed_pct"] <= 100.0, (
        f"attribution counted parents with their children: {snap['attributed_pct']}%")


def test_the_documented_reading_yields_a_real_remainder(monkeypatch):
    """`100 - attributed_pct` is the UNATTRIBUTED share, so it must be >= 0.
    That subtraction is what CLAUDE.md tells a reader to perform."""
    _nested_tick(monkeypatch, parent_ms=10.0, child_ms=30.0, n_children=3)
    assert 100.0 - tc.snapshot()["attributed_pct"] >= 0.0


def test_a_nested_hook_is_marked_nested_and_a_top_level_one_is_not(monkeypatch):
    """The per-hook `pct_of_total` column still sums past 100 by design (each
    row is measured against the TICK). `nested` is how a reader knows why."""
    _nested_tick(monkeypatch, parent_ms=10.0, child_ms=30.0, n_children=2)
    hooks = tc.snapshot()["hooks"]
    assert hooks["order_monitor"]["nested"] is False
    assert hooks["monitor.child_0"]["nested"] is True


def test_nested_time_is_still_reported_per_hook(monkeypatch):
    """Excluding children from the SUM must not hide them from the SPLIT —
    'where does the tick go' is the question the per-hook block answers."""
    _nested_tick(monkeypatch, parent_ms=10.0, child_ms=30.0, n_children=2)
    hooks = tc.snapshot()["hooks"]
    assert hooks["monitor.child_0"]["mean_ms"] == pytest.approx(30.0, abs=1.0)
    assert hooks["monitor.child_0"]["pct_of_total"] > 0


def test_a_direct_record_hook_call_still_counts(monkeypatch):
    """Back-compat: `record_hook` used outside the context manager defaults to
    top-level, so an existing direct caller is not silently dropped."""
    _tick(100.0, monkeypatch)
    tc.record_hook("direct", 40.0)
    snap = tc.snapshot()
    assert snap["hooks"]["direct"]["nested"] is False
    assert snap["hooks_attributed_mean_ms"] == pytest.approx(40.0, abs=0.1)


def test_sequential_top_level_hooks_both_attribute(monkeypatch):
    """Two hooks that do NOT nest must both count — the fix must not
    over-correct by attributing only the first."""
    clock = {"t": 0.0}
    monkeypatch.setattr(tc.time, "monotonic", lambda: clock["t"])
    tc.begin_tick()
    for name in ("run_one_tick", "order_monitor"):
        with tc.hook(name):
            clock["t"] += 0.050
    tc.end_tick()
    snap = tc.snapshot()
    assert snap["hooks_attributed_mean_ms"] == pytest.approx(100.0, abs=2.0)
    assert snap["attributed_pct"] == pytest.approx(100.0, abs=2.0)
