"""M36 Track C · C3 — tests for crowding_read (reductive over-extension conditioner).

Pure, offline, injected scalars. Verifies renormalize-over-present blending, the
reductive (never-enlarging) size multiplier, and the observe-only exit-tighten fold
into a C2 progress action.
"""

from __future__ import annotations

from src.units.strategies.macro_thesis.crowding_read import (
    conditioned_exit,
    crowding_read,
)


def test_blend_over_present_inputs():
    r = crowding_read(move_extension=0.8, positioning_extremity=0.4, sentiment_intensity=0.6)
    assert abs(r.crowding - 0.6) < 1e-9  # mean of the three
    assert set(r.inputs) == {"move_extension", "positioning_extremity", "sentiment_intensity"}


def test_missing_inputs_do_not_drag_score():
    # only one input present → crowding == that input (not divided by 3)
    r = crowding_read(sentiment_intensity=0.9)
    assert r.crowding == 0.9
    assert r.inputs == {"sentiment_intensity": 0.9}


def test_no_inputs_is_neutral_noop():
    r = crowding_read()
    assert r.crowding is None
    assert r.size_multiplier == 1.0 and r.exit_tighten == 0.0
    assert "neutral" in (r.note or "")


def test_size_multiplier_is_reductive():
    # neutral
    assert crowding_read(move_extension=0.0).size_multiplier == 1.0
    # max crowding → floor (default 0.5)
    assert crowding_read(move_extension=1.0).size_multiplier == 0.5
    # never enlarges, always within [floor, 1]
    for x in (0.0, 0.25, 0.5, 0.75, 1.0):
        m = crowding_read(move_extension=x).size_multiplier
        assert 0.5 <= m <= 1.0
    # custom floor honored + clamped
    assert abs(crowding_read(move_extension=1.0, size_floor=0.3).size_multiplier - 0.3) < 1e-9


def test_inputs_clamped_to_unit():
    r = crowding_read(move_extension=5.0, positioning_extremity=-2.0)
    assert r.inputs["move_extension"] == 1.0
    assert r.inputs["positioning_extremity"] == 0.0
    # bool / bad values rejected as absent
    r2 = crowding_read(move_extension=True, sentiment_intensity="x")
    assert r2.crowding is None


def test_conditioned_exit_advances_a_crowded_near_target_hold():
    hold = {"thesis_id": "t", "action": "hold", "move_progress": 0.8, "time_progress": 0.4}
    crowded = crowding_read(move_extension=0.9, sentiment_intensity=0.8)  # exit_tighten high
    out = conditioned_exit(hold, crowded)
    assert out["action"] == "trim"
    assert "crowding conditioner" in out["reason"]
    # original record untouched (immutable)
    assert hold["action"] == "hold"


def test_conditioned_exit_leaves_uncrowded_or_far_hold_alone():
    hold = {"thesis_id": "t", "action": "hold", "move_progress": 0.8}
    calm = crowding_read(move_extension=0.1)  # not crowded
    assert conditioned_exit(hold, calm)["action"] == "hold"
    far = {"thesis_id": "t", "action": "hold", "move_progress": 0.3}  # not near target
    crowded = crowding_read(move_extension=0.9)
    assert conditioned_exit(far, crowded)["action"] == "hold"


def test_conditioned_exit_never_relaxes_an_exit():
    ex = {"thesis_id": "t", "action": "exit", "move_progress": 1.4}
    calm = crowding_read(move_extension=0.0)
    assert conditioned_exit(ex, calm)["action"] == "exit"  # reductive-only, never relaxes
