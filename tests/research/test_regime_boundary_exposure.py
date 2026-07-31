"""Guards for the feed-sensitivity honesty metrics on ``regime_tag_emitted``.

BL-20260731-REGIME-ATTRIBUTION-FEED-SENSITIVE. Per-regime net-R is bucketed by
HARD cutoffs (chop <20, transitional 20-25, trending >=25) over a noisy rolling
indicator, against a heavy-tailed per-trade R distribution. Measured live: the
SAME 357 trades re-tagged against a second, equally-valid BTCUSDT 1h feed moved
one bucket by 24.92R (~31% of lifetime net-R) while the feeds agreed on trade
outcomes to 1.05R and on base rates to 0.5pp.

These tests pin the two metrics that make that visible, and — more importantly —
pin the properties that stop them becoming decoration: absolute-value share (so
a +8R and a -8R near a boundary don't cancel), NaN never counted as safe, and a
sign flip surfaced regardless of how small the delta is.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "scripts", "research"))
import regime_tag_emitted as rte  # type: ignore  # noqa: E402


def _df(n: int = 60) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts})


def _adx_const(value: float, n: int = 60) -> pd.Series:
    return pd.Series([value] * n)


def _trade(when: str, net_r: float) -> dict:
    return {"entry_time": when, "direction": "long", "net_r": net_r}


def test_trade_at_a_cutoff_is_flagged_near():
    """ADX 24.9 is one relabel from trending — the whole defect in one case."""
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 8.0)],
                                _adx_const(24.9), _df(), band=2.0)
    assert out["trades_near_boundary"] == 1
    assert out["exposure_pct"] == 100.0
    assert out["largest_single_near_boundary_abs_r"] == 8.0


def test_trade_far_from_any_cutoff_is_not_flagged():
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 8.0)],
                                _adx_const(40.0), _df(), band=2.0)
    assert out["trades_near_boundary"] == 0
    assert out["exposure_pct"] == 0.0
    assert out["fragile"] is False


def test_exposure_uses_absolute_value_so_winners_and_losers_do_not_cancel():
    """The property that makes the metric honest.

    A +8R and a -8R trade both sitting at a cutoff are BOTH one relabel away
    from moving. Netting them first would report 0% exposure for a grade that is
    maximally fragile — the metric would say "safe" precisely when it is not.
    """
    trades = [_trade("2024-01-01T05:00:00Z", 8.0), _trade("2024-01-01T06:00:00Z", -8.0)]
    out = rte.boundary_exposure(trades, _adx_const(19.5), _df(), band=2.0)
    assert out["abs_net_r_near_boundary"] == 16.0
    assert out["exposure_pct"] == 100.0


def test_nan_adx_is_never_counted_as_safe():
    """A trade with no bar to judge is 'unknown', not 'far from a boundary'."""
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 5.0)],
                                _adx_const(float("nan")), _df(), band=2.0)
    assert out["trades_near_boundary"] == 0
    assert "unknown" in out["by_regime"]
    assert out["by_regime"]["unknown"]["trades"] == 1


@pytest.mark.parametrize("adx_value", [20.0, 25.0])
def test_both_cutoffs_are_watched(adx_value):
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 3.0)],
                                _adx_const(adx_value), _df(), band=0.5)
    assert out["trades_near_boundary"] == 1


def test_fragile_flag_reports_its_own_threshold_and_basis():
    """A flag that hides its threshold invites being quoted as a verdict."""
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 1.0)],
                                _adx_const(24.9), _df(), band=2.0)
    assert out["fragile_threshold_pct"] == rte._FRAGILE_EXPOSURE_PCT
    assert "not a validated gate" in out["fragile_threshold_basis"]


def test_feed_sensitivity_surfaces_a_sign_flip():
    """The finding that matters most: positive on one feed, negative on another."""
    trades = [_trade("2024-01-01T05:00:00Z", 5.0)]
    baseline = {"chop": {"trades": 1, "net_r": 5.0}}
    # Second feed reads the same bar as TRENDING, so chop empties to 0.0 and
    # trending gains +5.0 -> chop 5.0 -> 0.0 is not a flip, trending 0.0 -> 5.0
    # is not either. Force a real flip by making the baseline negative there.
    baseline_neg = {"trending": {"trades": 1, "net_r": -5.0}}
    out = rte.feed_sensitivity(trades, baseline_neg, _adx_const(40.0), _df())
    assert out["any_sign_flip"] is True
    assert "trending" in out["sign_flips"]
    assert out["by_regime"]["trending"]["net_r_feed_a"] == -5.0
    assert out["by_regime"]["trending"]["net_r_feed_b"] == 5.0


def test_feed_sensitivity_reports_delta_without_a_flip():
    trades = [_trade("2024-01-01T05:00:00Z", 5.0)]
    baseline = {"trending": {"trades": 1, "net_r": 2.0}}
    out = rte.feed_sensitivity(trades, baseline, _adx_const(40.0), _df())
    assert out["any_sign_flip"] is False
    assert out["by_regime"]["trending"]["delta_r"] == 3.0
    assert out["max_abs_delta_r"] == 3.0


def test_feed_sensitivity_holds_the_trade_set_fixed():
    """Every delta must be attributable to labelling, never to generation.

    Same trades in, so n across all buckets on feed B equals the input count —
    if this ever drifts, the metric silently starts measuring two things.
    """
    trades = [_trade("2024-01-01T05:00:00Z", 1.0), _trade("2024-01-01T06:00:00Z", 2.0)]
    out = rte.feed_sensitivity(trades, {}, _adx_const(10.0), _df())
    assert sum(d["n_feed_b"] for d in out["by_regime"].values()) == len(trades)
    assert "LABELLING" in out["note"]


def test_boundary_metrics_are_declared_on_every_run():
    """Same contract as ``vol_axis``: absence must be stated, not implied."""
    src = open(rte.__file__, encoding="utf-8").read()
    assert '"feed_sensitivity_checked"' in src
    assert "feed sensitivity: NOT CHECKED" in src
    assert '"boundary_exposure": boundary' in src


def test_transitional_exposure_reports_its_structural_floor():
    """`transitional` is 5 ADX wide, so it is ~always 'near' a cutoff.

    Without the floor this reads as a 100% alarm on every single run — a real
    number under a label implying something it does not, which is the
    unprovenanced-diagnostic class the repo now guards against.
    """
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 4.0)],
                                _adx_const(22.5), _df(), band=2.5)
    slot = out["by_regime"]["transitional"]
    assert slot["exposure_pct"] == 100.0
    assert slot["structural_floor_pct"] == 100.0, "band 2.5 covers the whole 5-wide band"
    assert slot["exposure_above_floor_pct"] == 0.0, "100% here is a tautology, not a finding"


def test_trending_has_no_width_based_floor():
    """Unbounded above — inventing a floor would be manufacturing a denominator."""
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 4.0)],
                                _adx_const(40.0), _df(), band=2.0)
    assert out["by_regime"]["trending"]["structural_floor_pct"] is None
    assert out["by_regime"]["trending"]["exposure_above_floor_pct"] is None


def test_chop_floor_uses_its_single_interior_edge():
    """chop is [0,20): only the upper edge is a cutoff, so floor = band/20."""
    out = rte.boundary_exposure([_trade("2024-01-01T05:00:00Z", 4.0)],
                                _adx_const(5.0), _df(), band=2.0)
    assert out["by_regime"]["chop"]["structural_floor_pct"] == 10.0
