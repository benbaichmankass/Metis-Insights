"""M35 — tests for the crypto OI/basis momentum probe (no network; injected fetchers)."""

from __future__ import annotations

import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import crypto_momentum_probe as cm  # noqa: E402


# ---- feature helpers -------------------------------------------------------

def test_roc_point_in_time():
    vals = [10.0, 11.0, 12.0, 13.0]
    out = cm._roc(vals, 2)
    assert out[0] is None and out[1] is None
    assert math.isclose(out[2], (12.0 - 10.0) / 10.0)   # 0.20
    assert math.isclose(out[3], (13.0 - 11.0) / 11.0)


def test_zscore_warms_and_none_on_flat():
    flat = [5.0] * 10
    z = cm._zscore(flat, 5)
    assert all(v is None for v in z)                    # zero variance → None
    rising = [float(i) for i in range(10)]
    zr = cm._zscore(rising, 5)
    assert zr[-1] is not None and zr[-1] > 0


def test_build_features_names():
    f = cm.build_features([1.0, 2.0, 3.0, 4.0, 5.0], [0.1, 0.2, 0.3, 0.4, 0.5], window=2)
    assert set(f) == {"oi_roc2", "oi_z2", "basis_roc2", "basis_z2"}


def test_align_days_intersects():
    a = [("2020-01-01", 1.0), ("2020-01-02", 2.0), ("2020-01-03", 3.0)]
    b = [("2020-01-02", 20.0), ("2020-01-03", 30.0)]
    days, (va, vb) = cm.align_days(a, b)
    assert days == ["2020-01-02", "2020-01-03"]
    assert va == [2.0, 3.0] and vb == [20.0, 30.0]


# ---- grade_feature: planted directional edge + null ------------------------

def test_grade_feature_detects_planted_momentum_edge():
    # feature high → next-bar return high (trend continuation), H=1, one symbol.
    n = 400
    feat, ret = [], [100.0]
    for i in range(n):
        f = float((i % 5) - 2)          # -2..2 cycling, non-constant
        feat.append(f)
        # forward log-return proportional to the CURRENT feature (+ tiny noise)
        step = 0.01 * f + (0.0003 if i % 2 else -0.0003)
        ret.append(ret[-1] * math.exp(step))
    ret = ret[:n]
    g = cm.grade_feature([feat], [ret], (1,), split_frac=0.6, fee_frac=0.0, t_flag=2.0)
    row = g["rows"][0]
    assert row["ic"] is not None and row["ic"] > 0 and abs(row["ic_t"]) > 2.0
    assert g["verdict"] == "directional_edge"


def test_grade_feature_null_is_no_edge():
    n = 400
    feat, ret = [], [100.0]
    for i in range(n):
        feat.append(float((i % 7) - 3))
        ret.append(ret[-1] * math.exp(0.0004 if i % 2 else -0.0004))  # feature-independent
    ret = ret[:n]
    g = cm.grade_feature([feat], [ret], (1, 3), split_frac=0.6, fee_frac=0.001, t_flag=2.0)
    assert g["verdict"] == "no_directional_edge"


def test_grade_feature_thin_is_no_edge():
    g = cm.grade_feature([[1.0, 2.0, 3.0]], [[100.0, 101.0, 102.0]], (1,),
                         split_frac=0.6, fee_frac=0.001, t_flag=2.0)
    assert g["verdict"] == "no_directional_edge"
    assert g["rows"][0]["n"] < 16 and g["rows"][0]["pays_oos"] is False


# ---- run_probe wiring with injected Bybit fetchers -------------------------

def _ms(day_idx):
    return (1577836800 + day_idx * 86400) * 1000   # 2020-01-01 + day_idx days, in ms


def _oi_fetch_factory(n=200):
    def _f(symbol, interval_time="1d"):
        return [(_ms(i), 1000.0 + (i % 11) * 10.0) for i in range(n)]
    return _f


def _kline_fetch_factory(n=200):
    def _f(symbol, spot=False):
        base = 100.0 if not spot else 99.5
        return [(_ms(i), base + i * 0.5 + (0.0 if not spot else 0.1)) for i in range(n)]
    return _f


def test_run_probe_shape_and_verdict_domain():
    out = cm.run_probe(symbols=("BTCUSDT",), oi_fetch=_oi_fetch_factory(200),
                       kline_fetch=_kline_fetch_factory(200), horizons=(1, 3), windows=(7,))
    assert out["verdict"] in {"directional_edge", "no_directional_edge"}
    assert out["coverage"]["BTCUSDT"] > 100
    feats = {r["feature"] for r in out["features"]}
    assert feats == {"oi_roc7", "oi_z7", "basis_roc7", "basis_z7"}


def test_run_probe_thin_coverage_yields_no_edge():
    # too few days → every feature skipped → no_directional_edge, coverage recorded
    out = cm.run_probe(symbols=("BTCUSDT",), oi_fetch=_oi_fetch_factory(15),
                       kline_fetch=_kline_fetch_factory(15), horizons=(1, 3), windows=(7,))
    assert out["verdict"] == "no_directional_edge"
    assert out["coverage"]["BTCUSDT"] <= 15


def test_default_kline_wrapper_uses_category(monkeypatch):
    # The default (live) kline wrapper inside run_probe must call
    # fetch_kline_close with category= (not spot=), else it TypeErrors,
    # is swallowed per-symbol, and yields coverage=0 (issue #7598 false null).
    import crypto_signals_data as cs
    calls = []
    monkeypatch.setattr(cs, "fetch_open_interest",
                        lambda s, **k: [(_ms(i), 1000.0 + (i % 11) * 10.0) for i in range(80)])

    def _fkc(sym, **k):
        calls.append(k)
        base = 100.0 if k.get("category") == "linear" else 99.5
        return [(_ms(i), base + i * 0.5) for i in range(80)]

    monkeypatch.setattr(cs, "fetch_kline_close", _fkc)
    out = cm.run_probe(symbols=("BTCUSDT",), horizons=(1, 3), windows=(7,))
    assert out["coverage"]["BTCUSDT"] > 40
    cats = {c.get("category") for c in calls}
    assert "spot" in cats and "linear" in cats


def test_build_symbol_series_aligns():
    s = cm.build_symbol_series("BTCUSDT", oi_fetch=_oi_fetch_factory(50),
                               kline_fetch=_kline_fetch_factory(50))
    assert s["n_days"] > 40
    assert len(s["oi"]) == len(s["basis"]) == len(s["ret"]) == s["n_days"]
