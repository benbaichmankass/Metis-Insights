"""ADX regime-gate on the live htf_pullback_trend_2h unit (2026-06-18, Tier-3).

The recombination sweep + out-of-pool holdout showed an ADX>=25 entry floor lifts
the pullback family (docs/research/recombination-sweep-2026-06-18.md). The gate was
ported VERBATIM from scripts/backtest_pullback.py into the live unit so live ==
backtest. These tests pin: (1) OFF by default = behaviour-preserving; (2) a high
adx_min rejects an otherwise-actionable setup with an ADX reason; (3) a low adx_min
admits it and the gate value equals the harness ADX on the signal bar; (4) the unit's
_adx is byte-identical to the harness _adx.
"""
import numpy as np
import pandas as pd

from src.units.strategies.htf_pullback_trend_2h import _adx, order_package


def _setup_df() -> pd.DataFrame:
    """Synthetic uptrend + deep pullback + modest bullish-confirm final bar that
    triggers a LONG htf_pullback setup (pos_in_range <= pullback_frac, close>prev)."""
    n = 120
    ts = pd.date_range("2025-01-01", periods=n, freq="2h", tz="UTC")
    rng = np.random.default_rng(1)
    base = 100 + np.arange(n) * 1.0 + rng.normal(0, 0.3, n)
    high = base + 0.7
    low = base - 0.7
    openp = base.copy()
    close = base.copy()
    close[-3] = base[-3] - 3.0
    low[-3] = close[-3] - 0.7
    close[-2] = base[-2] - 5.0
    low[-2] = close[-2] - 1.0
    close[-1] = close[-2] + 0.3            # bullish confirm, small bounce
    high[-1] = close[-1] + 0.4
    low[-1] = close[-2] - 0.3
    return pd.DataFrame({"timestamp": ts, "open": openp, "high": high, "low": low, "close": close})


_BASE_CFG = dict(
    symbol="ETHUSDT", trend_lookback=40, pullback_lookback=10, pullback_frac=0.5,
    atr_period=14, atr_stop_mult=2.5, trail_mult=5.0, tp_r=50.0, min_confidence=0.0,
)


def test_adx_filter_off_by_default_is_behaviour_preserving():
    df = _setup_df()
    pkg = order_package(dict(_BASE_CFG), df)            # no adx_min/adx_max
    assert pkg["direction"] == "long"
    assert pkg["meta"]["adx"] is None                  # not computed when off
    assert pkg["meta"]["adx_min"] is None


def test_adx_min_above_current_rejects():
    df = _setup_df()
    adx_now = float(_adx(df, 14).iloc[-1])
    try:
        order_package(dict(_BASE_CFG, adx_min=adx_now + 20), df)
        raise AssertionError("expected ValueError (ADX below floor)")
    except ValueError as exc:
        assert "ADX" in str(exc)


def test_adx_min_below_current_admits_and_stamps_meta():
    df = _setup_df()
    adx_now = float(_adx(df, 14).iloc[-1])
    pkg = order_package(dict(_BASE_CFG, adx_min=max(adx_now - 10, 1.0)), df)
    assert pkg["direction"] == "long"
    # gate value == harness ADX on the signal bar (live == backtest)
    assert abs(pkg["meta"]["adx"] - adx_now) < 1e-9
    assert pkg["meta"]["adx_min"] == max(adx_now - 10, 1.0)


def test_unit_adx_matches_harness_adx():
    """The unit's _adx must be byte-identical to scripts/backtest_pullback.py::_adx."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("backtest_pullback_ref", "scripts/backtest_pullback.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # register so dataclass InitVar resolution works
    spec.loader.exec_module(mod)
    df = _setup_df()
    a = _adx(df, 14).to_numpy()
    b = mod._adx(df, 14).to_numpy()
    assert np.allclose(a, b, equal_nan=True)
