"""Sub-bar alignment for the intrabar exit-evaluation experiment.

The mapping is easy; the ways it can be quietly wrong are not. Each test below
pins one way an unnoticed defect would make the intrabar A/B report a number
that is not the number it claims (docs/live-exit-monitor-cadence-DESIGN.md § 4).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "subbar_align", REPO / "scripts" / "subbar_align.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["subbar_align"] = mod
    spec.loader.exec_module(mod)
    return mod


sa = _load()


def _hours(n, start="2024-01-01", tz="UTC"):
    return pd.date_range(start, periods=n, freq="1h", tz=tz)


def _mins(n, start="2024-01-01", step=5, tz="UTC"):
    return pd.date_range(start, periods=n, freq=f"{step}min", tz=tz)


def test_each_leg_bar_owns_exactly_its_own_sub_bars():
    leg = _hours(3)                  # 00:00, 01:00, 02:00
    fine = _mins(36)                 # 5m x 36 = 3h of coverage
    r = sa.align(leg, fine)
    assert r["reason"] is None
    assert r["slices"] == [(0, 12), (12, 24), (24, 36)]
    assert r["coverage"] == 1.0
    assert r["empty_bars"] == []


def test_a_sub_bar_on_the_boundary_belongs_to_the_new_leg_bar_only():
    """Half-open [t_i, t_i+1). A closed upper bound would double-count the
    boundary bar, and a double-counted bar is a second chance for an exit rule
    to fire on the same price — a silent bias toward the intrabar arm."""
    leg = _hours(2)
    fine = _mins(24)
    r = sa.align(leg, fine)
    (a0, a1), (b0, b1) = r["slices"]
    assert a1 == b0                      # no overlap
    assert a1 - a0 == 12 and b1 - b0 == 12
    # every fine row is claimed exactly once
    assert sorted(list(range(a0, a1)) + list(range(b0, b1))) == list(range(24))


def test_the_last_leg_bar_uses_the_measured_spacing_not_infinity():
    """Without a bounded final window the last leg bar would swallow every
    trailing fine row, including ones after the leg frame ends."""
    leg = _hours(2)
    fine = _mins(36)                     # an hour MORE fine data than leg
    r = sa.align(leg, fine)
    assert r["slices"][-1] == (12, 24)   # 12 rows, not 24
    assert r["leg_seconds"] == 3600.0


def test_a_gap_in_the_finer_frame_is_reported_not_absorbed():
    """A leg bar the finer frame does not describe must be COUNTED.

    An exit arm that silently evaluates those bars at the leg-bar close would
    dilute the A/B by an unstated amount and report the diluted number as the
    intrabar result.
    """
    leg = _hours(4)
    # 5m bars for hours 0 and 1, then nothing until hour 3.
    fine = list(_mins(24)) + list(_mins(12, start="2024-01-01 03:00"))
    r = sa.align(leg, fine)
    assert r["empty_bars"] == [2]
    assert r["covered"] == 3 and r["total"] == 4
    assert r["coverage"] == 0.75


def test_a_frame_that_is_not_finer_is_refused_rather_than_mapped():
    """Mapping a same-or-coarser frame yields ~1 sub-bar per leg bar, i.e. the
    baseline wearing the intrabar label — the worst possible outcome, because
    the arms would agree and the experiment would read as 'no effect'."""
    leg = _hours(5)
    r = sa.align(leg, _hours(5))
    assert r["reason"] is not None and "not finer" in r["reason"]
    assert r["coverage"] is None
    # Coarser is refused too.
    r2 = sa.align(leg, pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC"))
    assert r2["reason"] is not None


def test_naive_and_aware_frames_land_on_the_same_axis():
    """backtest_ict_scalp parses WITHOUT utc=True and backtest_trend with it.

    Comparing a naive column to an aware one raises; a mapping that crashed on
    a legitimate pairing would look like 'this leg has no finer data'.
    """
    leg_naive = pd.date_range("2024-01-01", periods=2, freq="1h")      # tz-naive
    fine_aware = _mins(24)                                             # tz-aware
    r = sa.align(leg_naive, fine_aware)
    assert r["reason"] is None
    assert r["coverage"] == 1.0
    assert r["slices"] == [(0, 12), (12, 24)]


def test_unparseable_rows_do_not_shift_the_mapping():
    leg = list(_hours(3))
    fine = list(_mins(12)) + ["not-a-time"] + list(_mins(24, start="2024-01-01 01:00"))
    r = sa.align(leg, fine)
    # The bad row is skipped, not counted into a bar and not treated as a gap.
    assert r["coverage"] == 1.0
    total_claimed = sum(b - a for a, b in r["slices"])
    assert total_claimed == 36


def test_empty_inputs_report_a_reason_instead_of_a_clean_zero():
    assert sa.align([], _mins(12))["reason"] == "leg frame is empty"
    r = sa.align(_hours(3), [])
    assert r["reason"] == "finer frame has no parseable timestamps"
    assert r["coverage"] is None      # not 0.0 — "we could not look"


def test_a_single_leg_bar_cannot_infer_a_width_and_says_so():
    r = sa.align(_hours(1), _mins(12))
    assert r["reason"] is not None and "fewer than two" in r["reason"]


def test_coverage_is_none_never_zero_when_the_mapping_is_refused():
    """0.0 coverage means 'we looked and the finer frame covers nothing'.
    None means 'we could not build a mapping'. Collapsing them would let a
    refused run be graded as a fully-uncovered one."""
    refused = sa.align(_hours(4), _hours(4))
    assert refused["coverage"] is None
    # A genuine total miss IS 0.0, and is a different statement.
    miss = sa.align(_hours(2), _mins(12, start="2030-01-01"))
    assert miss["coverage"] == 0.0
    assert miss["reason"] is None
    assert miss["empty_bars"] == [0, 1]


def test_mapping_is_linear_not_quadratic_on_a_realistic_frame():
    """370k 5m rows is the real scalp frame size; a per-bar rescan is not
    academic there."""
    leg = _hours(2000)
    fine = _mins(2000 * 12)
    r = sa.align(leg, fine)
    assert r["coverage"] == 1.0
    assert r["slices"][0] == (0, 12)
    assert r["slices"][-1] == (23988, 24000)
