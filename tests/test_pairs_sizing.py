"""Unit tests for src/units/strategies/pairs_sizing.py — pure money-math."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.units.strategies import pairs_sizing as ps  # noqa: E402


def test_notionals_risk_at_stop_equals_budget():
    # N_A = budget/risk_spread; a spread move of risk_spread on N_A must lose ~budget.
    r = ps.pair_notionals(risk_budget_usd=100.0, risk_spread=0.02, beta=1.5,
                          price_a=200.0, price_b=100.0)
    assert r["n_a_usd"] == pytest.approx(5000.0)          # 100 / 0.02
    assert r["qty_a"] == pytest.approx(25.0)              # 5000 / 200
    assert r["notional_b_usd"] == pytest.approx(7500.0)   # 1.5 * 5000
    assert r["qty_b"] == pytest.approx(75.0)              # 7500 / 100
    # loss at the stop = N_A * risk_spread == budget
    assert r["n_a_usd"] * 0.02 == pytest.approx(100.0)


def test_notionals_refuse_degenerate():
    for bad in [
        ps.pair_notionals(0.0, 0.02, 1.0, 100, 100),
        ps.pair_notionals(100, 0.0, 1.0, 100, 100),
        ps.pair_notionals(100, 0.02, 1.0, 0.0, 100),
        ps.pair_notionals(100, 0.02, 1.0, 100, -5),
    ]:
        assert bad["qty_a"] == 0.0 and bad["qty_b"] == 0.0


def test_beta_nonfinite_falls_back_to_one():
    r = ps.pair_notionals(100.0, 0.02, float("nan"), 100.0, 100.0)
    assert r["notional_b_usd"] == pytest.approx(r["n_a_usd"])  # beta->1.0


def test_protective_levels_long_leg():
    sl, tp = ps.leg_protective_levels("long", entry_price=100.0, risk_spread=0.02,
                                      backstop_mult=3.0)
    assert sl < 100.0 < tp
    assert sl == pytest.approx(100.0 * math.exp(-0.06), abs=1e-4)  # 3*0.02
    assert tp == pytest.approx(100.0 * math.exp(0.06), abs=1e-4)


def test_protective_levels_short_leg():
    sl, tp = ps.leg_protective_levels("short", entry_price=100.0, risk_spread=0.02,
                                      backstop_mult=3.0)
    assert tp < 100.0 < sl  # short: SL above, TP below
    assert sl == pytest.approx(100.0 * math.exp(0.06), abs=1e-4)


def test_protective_levels_degenerate():
    assert ps.leg_protective_levels("long", 0.0, 0.02) == (0.0, 0.0)
    assert ps.leg_protective_levels("long", 100.0, 0.0) == (0.0, 0.0)


def test_protective_levels_clamped_on_inflated_risk_spread():
    """Regression: a degenerate/inflated risk_spread must NOT explode the leg
    backstop into a venue-rejected level. Repro of the SOLUSDT/BTCUSDT paper
    pairs order (risk_spread≈5.4 → exp(3·5.4)=e^16 → $1.5e9 TP, $0 SL)."""
    entry = 150.0
    sl, tp = ps.leg_protective_levels("long", entry_price=entry, risk_spread=5.4,
                                      backstop_mult=3.0)
    # capped at +100% / −50% (log move ln 2), never the exploded/collapsed values
    assert tp == pytest.approx(entry * 2.0, rel=1e-6)
    assert sl == pytest.approx(entry * 0.5, rel=1e-6)
    assert 0.0 < sl < entry < tp < 1e6  # sane band, not $1.5e9 / $0
    # short leg mirrors: SL above (capped), TP below (floored at 0.5x)
    sl_s, tp_s = ps.leg_protective_levels("short", entry_price=entry, risk_spread=5.4)
    assert tp_s == pytest.approx(entry * 0.5, rel=1e-6)
    assert sl_s == pytest.approx(entry * 2.0, rel=1e-6)


def test_protective_levels_floored_on_tiny_risk_spread():
    """A near-zero risk_spread must not collapse SL/TP onto the entry price."""
    entry = 100.0
    sl, tp = ps.leg_protective_levels("long", entry_price=entry, risk_spread=1e-6,
                                      backstop_mult=3.0)
    assert sl < entry < tp
    assert tp == pytest.approx(entry * 1.02, rel=1e-6)  # floored at ln(1.02)
    assert sl == pytest.approx(entry / 1.02, rel=1e-6)


def test_correlation_haircut():
    assert ps.correlation_haircut(0) == 1.0
    assert ps.correlation_haircut(1, factor=0.5) == 0.5
    assert ps.correlation_haircut(2, factor=0.5) == 0.25
    assert ps.correlation_haircut(3, factor=0.5) == 0.125


# --- G1: min-qty-aware sizer -------------------------------------------------

def test_min_viable_scale_preserves_hedge():
    # leg A already clears (qty 25 >= min 1); leg B (qty 0.0005) needs 0.001 min.
    k = ps.min_viable_scale(25.0, 0.0005, min_a=1.0, min_b=0.001)
    assert k == pytest.approx(2.0)          # 0.001 / 0.0005
    # both already viable -> k == 1
    assert ps.min_viable_scale(5.0, 5.0, min_a=1.0, min_b=1.0) == 1.0
    # binding leg is A
    assert ps.min_viable_scale(0.4, 10.0, min_a=1.0, min_b=1.0) == pytest.approx(2.5)


def test_plan_pair_default_multiple_is_the_6591_skip():
    """max_risk_multiple=1.0 (default) must behave exactly like the both-legs-or-
    nothing skip: any sub-min pair refuses, no scaling."""
    # BTC-leg squeeze: budget $7.5, small beta -> leg B floors sub-min.
    d = ps.plan_pair_sizing(risk_budget_usd=7.5, risk_spread=0.05, beta=0.03,
                            price_a=3000.0, price_b=100000.0,
                            step_a=0.01, min_a=0.01, step_b=0.001, min_b=0.001)
    assert d["ok"] is False
    assert d["reason"] == "min_viable_exceeds_risk_tolerance"
    assert d["risk_multiple"] > 1.0


def test_plan_pair_viable_at_nominal():
    d = ps.plan_pair_sizing(risk_budget_usd=100.0, risk_spread=0.02, beta=1.0,
                            price_a=200.0, price_b=100.0,
                            step_a=0.001, min_a=0.001, step_b=0.001, min_b=0.001)
    assert d["ok"] is True and d["scaled"] is False
    assert d["risk_multiple"] == 1.0
    assert d["qty_a"] > 0 and d["qty_b"] > 0


def test_plan_pair_scales_within_tolerance():
    """With a tolerance >= the needed multiple, the sub-min leg is scaled up and
    the hedge ratio is preserved; risk_multiple records the inflation."""
    # ideal qty_b ~0.0005 (needs 2x to reach 0.001 min); allow up to 3x.
    d = ps.plan_pair_sizing(risk_budget_usd=7.5, risk_spread=0.05, beta=0.03,
                            price_a=3000.0, price_b=100000.0,
                            step_a=0.0001, min_a=0.0001, step_b=0.001, min_b=0.001,
                            max_risk_multiple=100.0)
    assert d["ok"] is True and d["scaled"] is True
    assert d["risk_multiple"] > 1.0
    assert d["qty_b"] >= 0.001 - 1e-12
    # hedge ratio (notional_b / notional_a) stays ~ beta despite scaling
    ratio = d["notional_b_usd"] / d["notional_a_usd"]
    assert ratio == pytest.approx(0.03, rel=0.15)


def test_plan_pair_refuses_when_over_tolerance():
    d = ps.plan_pair_sizing(risk_budget_usd=7.5, risk_spread=0.05, beta=0.03,
                            price_a=3000.0, price_b=100000.0,
                            step_a=0.0001, min_a=0.0001, step_b=0.001, min_b=0.001,
                            max_risk_multiple=1.5)  # needs ~7x -> refuse
    assert d["ok"] is False
    assert d["reason"] == "min_viable_exceeds_risk_tolerance"


def test_plan_pair_degenerate_refuses():
    d = ps.plan_pair_sizing(0.0, 0.02, 1.0, 100.0, 100.0,
                            step_a=0.001, min_a=0.001, step_b=0.001, min_b=0.001)
    assert d["ok"] is False and d["reason"] == "degenerate_size"


def test_floor_to_step():
    assert ps._floor_to_step(0.0037, 0.001) == 0.003
    assert ps._floor_to_step(5.0, 0.0) == 5.0
    assert ps._floor_to_step(0.30000000000000004, 0.1) == pytest.approx(0.3)
