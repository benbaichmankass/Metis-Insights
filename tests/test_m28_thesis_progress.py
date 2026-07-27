"""M36 Track C · C2 — tests for thesis_progress (the "priced-in-early → exit" read).

Pure, offline, synthetic theses. Exercises the signed move/time progress, the
long/short symmetry (expected_move carries the sign), tolerant resolution, and the
observe-only would-be action.
"""

from __future__ import annotations

from src.units.strategies.macro_thesis.thesis import TradeThesis
from src.units.strategies.macro_thesis.thesis_progress import (
    compute_progress,
    progress_action,
)


def _thesis(**kw) -> TradeThesis:
    base = dict(
        thesis_id="mth-p1", created_at="2026-07-27T00:00:00Z",
        updated_at="2026-07-27T00:00:00Z", status="active", direction="long",
        entry_plan={"entry": 100.0}, target={"expected_value": 110.0}, horizon_days=14,
    )
    base.update(kw)
    return TradeThesis(**base)


def test_long_move_progress_and_time():
    th = _thesis()
    r = compute_progress(th, current_price=105.0, elapsed_days=7)
    assert r.expected_move == 10.0
    assert r.realized_move == 5.0
    assert r.move_progress == 0.5
    assert r.time_progress == 0.5
    assert not r.overshoot and not r.early


def test_long_target_reached_early():
    th = _thesis()
    r = compute_progress(th, current_price=110.0, elapsed_days=3)  # target, only 3/14 days
    assert r.move_progress == 1.0
    assert r.early is True
    act = progress_action(r)
    assert act["action"] == "trim"  # advance the exit — the operator's example
    assert "target reached before horizon" in act["reason"]


def test_long_overshoot_exits():
    th = _thesis()
    r = compute_progress(th, current_price=115.0, elapsed_days=5)  # 1.5x expected
    assert r.move_progress == 1.5
    assert r.overshoot is True
    assert progress_action(r)["action"] == "exit"


def test_short_symmetry():
    th = _thesis(direction="short", target={"expected_value": 90.0})  # expected_move -10
    r = compute_progress(th, current_price=95.0, elapsed_days=7)  # realized -5
    assert r.expected_move == -10.0
    assert r.move_progress == 0.5  # -5 / -10
    r2 = compute_progress(th, current_price=85.0, elapsed_days=5)  # realized -15
    assert r2.move_progress == 1.5 and r2.overshoot
    assert progress_action(r2)["action"] == "exit"


def test_target_from_expected_move_pct():
    th = _thesis(target={"expected_move_pct": 0.10})  # entry 100 → target 110
    r = compute_progress(th, current_price=105.0, elapsed_days=7)
    assert abs(r.target_value - 110.0) < 1e-9
    assert abs(r.move_progress - 0.5) < 1e-9


def test_against_thesis_is_negative_progress():
    th = _thesis()
    r = compute_progress(th, current_price=95.0, elapsed_days=7)  # moved the wrong way
    assert r.move_progress == -0.5
    assert progress_action(r)["action"] == "hold"


def test_stalled_after_horizon():
    th = _thesis()
    r = compute_progress(th, current_price=101.0, elapsed_days=20)  # 0.1 progress, 20/14 time
    assert r.time_progress > 1.0
    act = progress_action(r)
    assert act["action"] == "hold" and "stalled" in act["reason"]


def test_tolerant_missing_fields():
    th = _thesis(entry_plan={}, target={})  # no entry, no target
    r = compute_progress(th, current_price=105.0, elapsed_days=7)
    assert r.move_progress is None and "missing" in (r.note or "")
    assert progress_action(r)["action"] == "hold"


def test_degenerate_target_equals_entry():
    th = _thesis(target={"expected_value": 100.0})  # == entry
    r = compute_progress(th, current_price=105.0, elapsed_days=7)
    assert r.move_progress is None and "degenerate" in (r.note or "")
