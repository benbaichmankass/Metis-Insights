"""ECE must say why it is (not) a number (ML-HYGIENE item 4)."""
from ml.calibration import ece_with_read_state, expected_calibration_error


def test_empty_is_not_a_perfect_score():
    assert expected_calibration_error([], []) == 0.0  # legacy float: the collapse
    r = ece_with_read_state([], [])
    assert r["ece"] is None and r["read_state"] == "empty"


def test_small_n_has_no_number():
    r = ece_with_read_state([1, 0] * 10, [0.5] * 20)
    assert r["ece"] is None and r["read_state"] == "insufficient_n"
    assert r["min_n"] == 100


def test_out_of_range_scale_is_flagged_not_understated():
    ys = [1, 0] * 60
    xs = [70.0, 40.0] * 60  # percent scale: every row falls outside the bins
    assert expected_calibration_error(ys, xs) == 0.0  # silent under-statement
    assert ece_with_read_state(ys, xs)["read_state"] == "out_of_range"


def test_ok_matches_legacy_value():
    ys = [1, 0] * 60
    xs = [0.9, 0.1] * 60
    r = ece_with_read_state(ys, xs)
    assert r["read_state"] == "ok"
    assert r["ece"] == expected_calibration_error(ys, xs)
