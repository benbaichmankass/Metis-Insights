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


# ---- S3: OOS split + cost-aware conviction spread --------------------------

def test_anchors_are_nonoverlapping():
    feat = [float(i) for i in range(120)]
    tgt = [100.0 + i for i in range(120)]
    a = ivp._anchors(feat, tgt, 8)
    assert 12 <= len(a) <= 15                              # ~120/8 disjoint, not ~112


def test_conviction_s3_row_planted_signal_pays_oos():
    # feature LOW → forward HIGH (negative IC), stable across the whole series so
    # the IS-fit orientation holds OOS and the long/short spread pays net.
    n = 400
    tgt = [100.0]
    feat = [None] * n
    for i in range(1, n):
        tgt.append(tgt[-1] * 1.001)                        # gentle uptrend
    for i in range(0, n - 5):
        # feature = -(realized fwd 5d return) → perfectly negative IC, big spread
        fwd = ivp.log_return(tgt[i], tgt[i + 5])
        feat[i] = -fwd if fwd is not None else None
    row = ivp.conviction_s3_row(feat, tgt, 5, split_frac=0.6, fee_frac=0.0)
    assert row["orient"] == -1                             # IS IC negative → long low bin
    assert row["oos_ic"] is not None and row["oos_ic"] < 0
    assert row["gross_spread"] > 0 and row["pays_oos"] is True


def test_conviction_s3_row_thin_is_no_pay():
    row = ivp.conviction_s3_row([None] * 5, [1.0] * 5, 5)
    assert row["pays_oos"] is False and row["oos_ic"] is None


def test_conviction_s3_fee_can_flip_net_negative():
    n = 300
    tgt = [100.0]
    feat = [None] * n
    for i in range(1, n):
        tgt.append(tgt[-1] * 1.0005)
    for i in range(0, n - 5):
        fwd = ivp.log_return(tgt[i], tgt[i + 5])
        feat[i] = -fwd if fwd is not None else None
    cheap = ivp.conviction_s3_row(feat, tgt, 5, fee_frac=0.0)
    pricey = ivp.conviction_s3_row(feat, tgt, 5, fee_frac=0.5)   # absurd fee
    assert cheap["net_spread"] > pricey["net_spread"]
    assert pricey["pays_oos"] is False                    # cost kills it


def test_scan_s3_verdict_and_run_probes_s3_smoke():
    n = 400
    tgt = [100.0]
    feat = [None] * n
    for i in range(1, n):
        tgt.append(tgt[-1] * 1.001)
    for i in range(0, n - 5):
        fwd = ivp.log_return(tgt[i], tgt[i + 5])
        feat[i] = -fwd if fwd is not None else None
    scan = ivp.scan_s3(feat, tgt, (5,), fee_frac=0.0)
    assert scan["verdict"] == "pays_oos_net" and scan["pays_oos"] is True

    # run_probes_s3 wiring on injected FRED series (verdict is data-driven, just no crash)
    dates = [f"2020-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(400)]
    vix = _fred_csv([(d, 15.0 + 5.0 * math.sin(i * 0.1)) for i, d in enumerate(dates)])
    spx = _fred_csv([(d, 3000.0 + i) for i, d in enumerate(dates)])
    probes = (("vix_level", "VIXCLS", "SP500", "level_pct", None),)
    out = ivp.run_probes_s3(probes=probes, horizons=(5, 10),
                            urlopen=_fake_urlopen_factory({"VIXCLS": vix, "SP500": spx}))
    assert out["probes"][0]["verdict"] in {"pays_oos_net", "s2_only_no_s3", "no_data"}
    assert {r["horizon"] for r in out["probes"][0]["rows"]} == {5, 10}


# ---- S4-prep: multi-fold walk-forward robustness --------------------------

def _stable_neg_ic_series(n, horizon):
    """feature = -(realized fwd H-return) → a stable NEGATIVE IC across all time."""
    tgt = [100.0]
    for i in range(1, n):
        tgt.append(tgt[-1] * 1.001)
    feat = [None] * n
    for i in range(0, n - horizon):
        fwd = ivp.log_return(tgt[i], tgt[i + horizon])
        feat[i] = -fwd if fwd is not None else None
    return feat, tgt


def test_walkforward_stable_signal_is_robust():
    # a signal whose sign is stable across the whole series → holds every fold.
    feat, tgt = _stable_neg_ic_series(600, 5)
    row = ivp.walkforward_row(feat, tgt, 5, k_folds=4)
    assert row["k_used"] >= 2
    assert row["expected_sign"] == -1
    assert row["sign_consistency"] == 1.0                 # every fold holds the sign
    assert row["verdict"] == "robust"


def test_walkforward_flipping_signal_is_not_robust():
    # feature sign flips halfway → the OOS IC sign flips across folds.
    n, horizon = 600, 5
    tgt = [100.0]
    for i in range(1, n):
        tgt.append(tgt[-1] * 1.001)
    feat = [None] * n
    for i in range(0, n - horizon):
        fwd = ivp.log_return(tgt[i], tgt[i + horizon])
        if fwd is None:
            continue
        # first half: feature = -fwd (neg IC); second half: feature = +fwd (pos IC)
        feat[i] = (-fwd if i < n // 2 else fwd)
    row = ivp.walkforward_row(feat, tgt, horizon, k_folds=4)
    assert row["sign_consistency"] is not None
    assert row["sign_consistency"] < 1.0                  # not every fold holds
    assert row["verdict"] in {"regime_dependent", "not_robust"}


def test_walkforward_insufficient_sample_is_not_a_pass():
    # too few anchors for 2 folds → insufficient_sample, never robust.
    feat, tgt = _stable_neg_ic_series(60, 42)             # ~1 anchor
    row = ivp.walkforward_row(feat, tgt, 42, k_folds=4)
    assert row["verdict"] == "insufficient_sample"
    assert row["k_used"] == 0


def test_walkforward_no_lookahead_expanding_train():
    feat, tgt = _stable_neg_ic_series(400, 5)
    row = ivp.walkforward_row(feat, tgt, 5, k_folds=3)
    trains = [f["n_train"] for f in row["folds"]]
    # expanding: each fold trains on strictly more anchors than the last
    assert trains == sorted(trains) and trains[0] < trains[-1]


def test_scan_walkforward_ranks_best_horizon_and_run_probes_smoke():
    feat, tgt = _stable_neg_ic_series(600, 5)
    scan = ivp.scan_walkforward(feat, tgt, (5, 42), k_folds=4)
    # H=5 is robust, H=42 insufficient → probe verdict takes the strongest (robust)
    assert scan["verdict"] == "robust" and scan["best_horizon"] == 5
    assert scan["is_robust"] is True

    dates = [f"2020-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(600)]
    vix = _fred_csv([(d, 15.0 + 5.0 * math.sin(i * 0.1)) for i, d in enumerate(dates)])
    spx = _fred_csv([(d, 3000.0 + i) for i, d in enumerate(dates)])
    probes = (("vix_level", "VIXCLS", "SP500", "level_pct", None),)
    out = ivp.run_probes_walkforward(probes=probes, horizons=(5, 10),
                                     urlopen=_fake_urlopen_factory({"VIXCLS": vix, "SP500": spx}))
    assert out["probes"][0]["verdict"] in {"robust", "regime_dependent", "not_robust", "insufficient_sample"}
    assert {r["horizon"] for r in out["probes"][0]["rows"]} == {5, 10}
