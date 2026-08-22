"""Bars-held reporting + the over-hold escalation guard.

BL-20260821-SCALPS-HELD-10-TO-100X-THEIR-DESIGN-HORIZON, Tier-1 half: the review's
execution-capture block reported hold only as a MEAN IN HOURS, which is (a) not
comparable across legs and (b) exactly the statistic that hid 15m scalps riding 11
days for weeks. p90 in BARS, against the leg's own backtested horizon, is the
statistic the row's resolution criteria are written against.

These tests pin three things the feature is worthless without:
  1. the p90 actually reaches the rendered report;
  2. a null is "we did not measure", never a passing 0;
  3. a leg past 3.0x is ESCALATED, not merely tabulated.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "rsr", Path(__file__).resolve().parents[1] / "scripts" / "reports" / "render_system_report.py"
)
rsr = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(rsr)


def _rc(rows, flags=None):
    """Both helpers take the FULL report, not the review_coverage block."""
    return {"consolidated": {"review_coverage": {
        "execution_capture": {"per_strategy": rows, "anomalies": []},
        "flags_raised": flags or [],
    }}}


# --------------------------------------------------------------------------- format


def test_ratio_none_is_a_dash_not_a_zero():
    """`None` must not render as a number. 'Not measured' and 'held no bars' are
    opposite claims, and a ratio is where that collapse would be least visible."""
    out = rsr._bars_ratio(None)
    assert out == rsr.DASH
    assert "0" not in out


def test_ratio_under_threshold_is_plain_and_over_threshold_is_marked():
    """The control that makes the mark meaningful: it must NOT fire on a healthy leg."""
    assert rsr._bars_ratio(2.9) == "2.9x"
    assert "🔴" not in rsr._bars_ratio(2.9)
    assert "🔴" in rsr._bars_ratio(3.1)


def test_ratio_boundary_is_exclusive_at_three():
    """3.0x is 'within 3x' per the row's own wording — it resolves, so it must not flag."""
    assert "🔴" not in rsr._bars_ratio(3.0)


def test_ratio_non_numeric_degrades_instead_of_raising():
    assert rsr._bars_ratio("n/a") == "n/a"


# --------------------------------------------------------------------------- render


def test_bars_render_as_integers_not_two_decimals():
    """Bars are discrete. `_num` would print "1,082.00", implying a precision a bar
    count does not have — and the generic formatter is the easy wrong choice here."""
    assert rsr._bars(1082) == "1,082"
    assert rsr._bars(96) == "96"
    assert "." not in rsr._bars(1082)


def test_bars_none_is_a_dash_not_zero():
    """Unmeasured, versus closed on the entry bar — different facts."""
    assert rsr._bars(None) == rsr.DASH


def test_p90_reaches_the_rendered_html():
    """A field the renderer drops is a field nobody reads."""
    html = rsr._section_review_coverage(_rc([{
        "strategy": "ict_scalp_mgc_15m", "book": "real", "n_closed": 12,
        "bars_held_median": 210, "bars_held_p90": 1082,
        "bars_held_expected": 96, "bars_held_p90_ratio": 11.3, "state": "anomaly",
    }]))
    assert "1,082" in html, "p90 must reach the report"
    assert "210" in html, "median must reach the report"
    assert "11.3x" in html, "the ratio must be rendered, not left to the reader"


def test_unmeasured_leg_renders_dashes_not_zeros():
    html = rsr._section_review_coverage(_rc([{
        "strategy": "some_leg", "book": "paper", "n_closed": 3, "state": "ok",
    }]))
    assert "Bars held" in html
    assert ">0<" not in html.replace(">0</td><td>", ">ZEROCELL<")


# --------------------------------------------------------------------------- guard


def test_over_held_leg_must_be_escalated_into_flags():
    v = rsr._validate_review_coverage(_rc([{"strategy": "ict_scalp_sol_5m", "bars_held_p90_ratio": 9.4}]))
    assert any("ict_scalp_sol_5m" in x and "3.0x" in x for x in v), v


def test_over_held_leg_named_in_flags_does_not_violate():
    """Discriminating control — a guard that always fires is not a guard."""
    v = rsr._validate_review_coverage(_rc([{"strategy": "ict_scalp_sol_5m", "bars_held_p90_ratio": 9.4}],
                          flags=["ict_scalp_sol_5m over-holds p90 9.4x; max-hold proposed"]))
    assert not any("ict_scalp_sol_5m" in x and "3.0x" in x for x in v), v


def test_healthy_leg_never_violates():
    v = rsr._validate_review_coverage(_rc([{"strategy": "trend_donchian_sol_4h", "bars_held_p90_ratio": 1.8}]))
    assert not any("trend_donchian_sol_4h" in x for x in v), v


def test_unmeasured_leg_is_not_treated_as_passing():
    """A null ratio must produce neither a violation nor a silent pass claim —
    it is simply not gradeable, and the guard must not invent a verdict for it."""
    v = rsr._validate_review_coverage(_rc([{"strategy": "unmeasured_leg", "bars_held_p90_ratio": None}]))
    assert not any("unmeasured_leg" in x for x in v), v
