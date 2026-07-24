"""M31 Track A — tests for the implied-vol IC probe (no network; injected urlopen)."""

from __future__ import annotations

import io
import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import implied_vol_probe as ivp  # noqa: E402


# ---- pure helpers ----------------------------------------------------------

def test_align_dated_inner_joins_on_date():
    a = [("2020-01-01", 10.0), ("2020-01-02", 11.0), ("2020-01-03", 12.0)]
    b = [("2020-01-02", 100.0), ("2020-01-03", 101.0), ("2020-01-04", 102.0)]
    dates, av, bv = ivp.align_dated(a, b)
    assert dates == ["2020-01-02", "2020-01-03"]
    assert av == [11.0, 12.0] and bv == [100.0, 101.0]


def test_align_dated_drops_nonfinite():
    a = [("d1", 1.0), ("d2", float("nan"))]
    b = [("d1", 2.0), ("d2", 3.0)]
    dates, av, bv = ivp.align_dated(a, b)
    assert dates == ["d1"] and av == [1.0] and bv == [2.0]


def test_pct_rank_last_bounds():
    assert ivp.pct_rank_last([1.0] * 10) is None          # < 20 points
    vals = [float(i) for i in range(100)]                 # last (99) is the max
    assert ivp.pct_rank_last(vals) > 0.98
    vals2 = [50.0] + [float(i) for i in range(1, 100)]    # last is small-ish
    # midpoint value → around its rank percentile
    assert 0.0 <= ivp.pct_rank_last(vals2) <= 1.0


def test_realized_vol_and_log_return():
    assert ivp.realized_vol([]) is None
    rv = ivp.realized_vol([0.01, -0.01, 0.02, -0.02])
    assert rv is not None and rv > 0
    assert math.isclose(ivp.log_return(100.0, 110.0), math.log(1.1), rel_tol=1e-9)
    assert ivp.log_return(0.0, 5.0) is None               # non-positive operand


def test_build_feature_level_pct_is_pit():
    vol = [float(i % 30) for i in range(60)]
    tgt = [100.0 + i for i in range(60)]
    feat = ivp.build_feature_series("level_pct", vol, tgt, pct_window=30)
    assert feat[0] is None                                # too few points to rank
    assert feat[-1] is not None and 0.0 <= feat[-1] <= 1.0


def test_build_feature_term_ratio():
    vol = [20.0, 25.0, 10.0]
    extra = [22.0, 25.0, 15.0]                            # VIX3M
    feat = ivp.build_feature_series("term_ratio", vol, [1, 2, 3], extra_vals=extra)
    assert math.isclose(feat[0], 22.0 / 20.0)             # contango > 1
    assert math.isclose(feat[1], 1.0)                     # flat
    assert feat[2] > 1.0                                  # 15/10 backwardation-ish ratio


def test_build_feature_vrp_shape():
    vol = [20.0] * 40
    tgt = [100.0 * (1.01 ** i) for i in range(40)]        # steady drift → low realized vol
    feat = ivp.build_feature_series("vrp", vol, tgt, rv_window=21)
    assert feat[-1] is not None                           # implied − realized computed


# ---- IC math ---------------------------------------------------------------

def test_rank_handles_ties():
    assert ivp.rank([10.0, 30.0, 20.0]) == [0.0, 2.0, 1.0]
    assert ivp.rank([5.0, 5.0, 9.0]) == [0.5, 0.5, 2.0]


def test_spearman_ic_recovers_monotone():
    pairs = [(x, x ** 3) for x in range(-6, 7)]           # monotone → IC ≈ 1
    ic = ivp.spearman_ic(pairs)
    assert ic is not None and ic["ic"] > 0.99 and ic["ic_t"] > 2.0


def test_spearman_ic_thin_is_none():
    assert ivp.spearman_ic([(1, 2)] * 4) is None          # < 8 pairs


def test_nonoverlap_ic_row_is_nonoverlapping():
    # 200 points, H=10 → ~19 disjoint anchors, NOT ~190
    feat = [float(i) for i in range(200)]
    tgt = [100.0 + i for i in range(200)]
    row = ivp.nonoverlap_ic_row(feat, tgt, 10)
    assert 15 <= row["n_nonoverlap"] <= 20


def test_scan_probe_flags_planted_edge():
    # feature = next-step target return (a planted perfect predictor) → directional_edge
    n = 300
    tgt = [100.0]
    for i in range(1, n):
        tgt.append(tgt[-1] * (1.0 + (0.01 if i % 2 else -0.008)))
    # feature at i = the sign of the forward 5d return (leaks the answer → strong IC)
    feat = [None] * n
    for i in range(0, n - 5):
        feat[i] = ivp.log_return(tgt[i], tgt[i + 5])
    out = ivp.scan_probe(feat, tgt, (5,), t_flag=2.0)
    assert out["verdict"] == "directional_edge" and out["has_edge"] is True


def test_scan_probe_no_data_when_thin():
    out = ivp.scan_probe([None] * 5, [1.0] * 5, (5, 10), t_flag=2.0)
    assert out["verdict"] == "no_data" and out["has_edge"] is False


# ---- run_probes with an injected FRED urlopen ------------------------------

def _fred_csv(pairs) -> str:
    return "DATE,VALUE\n" + "\n".join(f"{d},{v}" for d, v in pairs)


def _fake_urlopen_factory(series_bodies):
    """Return a urlopen(url, timeout=) that serves fredgraph CSV by ?id=<sid>."""
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    def _urlopen(url, timeout=None):
        sid = url.split("id=")[-1]
        body = series_bodies.get(sid, "DATE,VALUE\n")     # unknown → header only (empty)
        return _Resp(body.encode())

    return _urlopen


def test_run_probes_grades_injected_series():
    # a clean synthetic pair for VIXCLS→SP500: 400 business-ish days
    dates = [f"2020-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(400)]
    vix = _fred_csv([(d, 15.0 + 5.0 * math.sin(i * 0.1)) for i, d in enumerate(dates)])
    spx = _fred_csv([(d, 3000.0 + i) for i, d in enumerate(dates)])
    bodies = {"VIXCLS": vix, "SP500": spx}
    probes = (("vix_level", "VIXCLS", "SP500", "level_pct", None),)
    out = ivp.run_probes(probes=probes, horizons=(5, 10),
                         urlopen=_fake_urlopen_factory(bodies), t_flag=2.0)
    r = out["probes"][0]
    assert r["name"] == "vix_level" and r["verdict"] in {"directional_edge", "no_edge", "no_data"}
    assert r["n_aligned"] > 0
    # every requested horizon produced a row
    assert {row["horizon"] for row in r["rows"]} == {5, 10}


def test_run_probes_empty_series_is_no_data_not_crash():
    probes = (("ovx_level", "OVXCLS", "DCOILWTICO", "level_pct", None),)
    out = ivp.run_probes(probes=probes, horizons=(5,),
                         urlopen=_fake_urlopen_factory({}), t_flag=2.0)
    assert out["probes"][0]["verdict"] == "no_data"
    assert "empty series" in out["probes"][0]["reason"]
