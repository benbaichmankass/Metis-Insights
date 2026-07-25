"""M31 Track A-S5 — tests for the vix_term single-asset timing backtest (no network)."""

from __future__ import annotations

import io
import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import vix_term_backtest as vtb  # noqa: E402


# ---- a-priori position rule -------------------------------------------------

def test_positions_are_a_priori_short_high_long_low():
    # A rising feature: the last value is the max, so its trailing percentile is high
    # → SHORT (−1). Build a long warm window so pct_rank_last is defined.
    rising = [float(i) for i in range(60)]
    pos = vtb._positions(rising, lo_q=0.33, hi_q=0.67)
    assert pos[:19] == [None] * 19          # not warm until >= 20 points
    assert pos[-1] == -1.0                   # highest-percentile value → short

    falling = [float(60 - i) for i in range(60)]
    posf = vtb._positions(falling, lo_q=0.33, hi_q=0.67)
    assert posf[-1] == 1.0                    # lowest-percentile value → long


def test_positions_flat_in_the_middle():
    # A value sitting mid-distribution → FLAT (0.0), not a taken side.
    vals = [float(i % 40) for i in range(80)]  # last value = 79%40 = 39 (near top)
    vals[-1] = 20.0                            # force the last value to the middle
    pos = vtb._positions(vals, lo_q=0.33, hi_q=0.67)
    assert pos[-1] == 0.0


# ---- period returns: net-of-cost, non-overlapping ---------------------------

def test_period_returns_net_of_cost_and_flat_pays_nothing():
    # target doubles every step; positions alternate long / flat.
    tgt = [100.0, 110.0, 121.0, 133.1, 146.41]
    pos = [1.0, None, 0.0, None, 1.0]
    # horizon 2, stride 2 → anchors at i=0 (pos=1) and i=2 (pos=0, flat).
    pr = vtb._period_returns(pos, tgt, 2, cost_frac=0.001)
    # i=0: 1·(121/100−1) − 1·0.001 = 0.21 − 0.001 = 0.209
    assert math.isclose(pr[0], 0.209, rel_tol=1e-9)
    # i=2: flat → 0·ret − 0·cost = 0.0 (no cost drag when flat)
    assert math.isclose(pr[1], 0.0, abs_tol=1e-12)


def test_period_returns_short_profits_when_price_falls():
    tgt = [100.0, 90.0, 81.0]
    pos = [-1.0, None, None]
    pr = vtb._period_returns(pos, tgt, 2, cost_frac=0.0)
    # short from 100 to 81: −1·(81/100−1) = −1·(−0.19) = +0.19
    assert math.isclose(pr[0], 0.19, rel_tol=1e-9)


# ---- equity metrics ---------------------------------------------------------

def test_equity_metrics_shape_and_honest_null():
    assert vtb._equity_metrics([0.01, 0.02], ann_periods=52)["sharpe"] is None  # <4 periods
    m = vtb._equity_metrics([0.02, -0.01, 0.03, 0.01, 0.00], ann_periods=52)
    assert m["n_periods"] == 5
    assert m["sharpe"] is not None and m["max_drawdown"] <= 0.0
    assert 0.0 <= m["hit_rate"] <= 1.0 and 0.0 <= m["exposure"] <= 1.0


# ---- run_backtest wiring with an injected FRED urlopen ----------------------

def _fred_csv(pairs) -> str:
    return "DATE,VALUE\n" + "\n".join(f"{d},{v}" for d, v in pairs)


def _fake_urlopen_factory(series_bodies):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    def _urlopen(url, timeout=None):
        sid = url.split("id=")[-1]
        return _Resp(series_bodies.get(sid, "DATE,VALUE\n").encode())

    return _urlopen


def _synthetic_bodies():
    dates = [f"2020-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(400)]
    vix = _fred_csv([(d, 18.0 + 4.0 * math.sin(i * 0.05)) for i, d in enumerate(dates)])
    vix3m = _fred_csv([(d, 20.0 + 3.0 * math.sin(i * 0.05)) for i, d in enumerate(dates)])
    spx = _fred_csv([(d, 3000.0 + i) for i, d in enumerate(dates)])
    return {"VIXCLS": vix, "VXVCLS": vix3m, "SP500": spx}


def test_run_backtest_grades_injected_series_without_crash():
    out = vtb.run_backtest(targets=(("SP500", "SP500"),), horizons=(10, 21),
                           urlopen=_fake_urlopen_factory(_synthetic_bodies()))
    r = out["targets"][0]
    assert r["name"] == "SP500"
    assert r["verdict"] in {"positive_oos_edge", "no_deployable_edge", "no_data"}
    assert {row["horizon"] for row in r.get("rows", [])} == {10, 21}


def test_run_backtest_empty_series_is_no_data():
    out = vtb.run_backtest(targets=(("NASDAQ100", "NASDAQ100"),), horizons=(21,),
                           urlopen=_fake_urlopen_factory({}))
    assert out["targets"][0]["verdict"] == "no_data"


def test_default_targets_are_the_three_equity_legs():
    names = {t[0] for t in vtb.DEFAULT_TARGETS}
    assert {"SP500", "NASDAQ100", "DJIA"} == names
