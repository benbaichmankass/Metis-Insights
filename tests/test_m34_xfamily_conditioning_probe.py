"""M34 — tests for the cross-family conditioning probe (no network; injected urlopen)."""

from __future__ import annotations

import io
import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import xfamily_conditioning_probe as xf  # noqa: E402


# ---- helpers ---------------------------------------------------------------

def test_align_many_intersects_dates():
    dated = {
        "A": [("2020-01-01", 1.0), ("2020-01-02", 2.0), ("2020-01-03", 3.0)],
        "B": [("2020-01-02", 20.0), ("2020-01-03", 30.0), ("2020-01-04", 40.0)],
    }
    dates, vals = xf._align_many(dated)
    assert dates == ["2020-01-02", "2020-01-03"]
    assert vals["A"] == [2.0, 3.0] and vals["B"] == [20.0, 30.0]


def test_series_range():
    r = xf._series_range([("2020-01-01", 1.0), ("2020-01-03", "."), ("2020-01-05", 2.0)])
    assert r == {"first": "2020-01-01", "last": "2020-01-05", "n": 2}
    assert xf._series_range([])["n"] == 0


def test_align_many_drops_nonnumeric():
    dated = {"A": [("2020-01-01", 1.0), ("2020-01-02", "x")],
             "B": [("2020-01-01", 5.0), ("2020-01-02", 6.0)]}
    dates, vals = xf._align_many(dated)
    assert dates == ["2020-01-01"] and vals["A"] == [1.0]


def test_mean_t_and_favorable():
    m, t, n = xf._mean_t([0.01] * 10)
    assert n == 10 and math.isclose(m, 0.01) and t is None      # zero variance → t None
    m, t, n = xf._mean_t([0.01, 0.02, 0.03, 0.01, 0.02, 0.03, 0.01, 0.02, 0.03, 0.05])
    assert t is not None and t > 0
    # above-median fwd higher → sign +1
    anchors = [(1.0, 0.0, 0.02), (0.9, 0.0, 0.01), (0.1, 0.0, -0.01), (0.2, 0.0, 0.0)]
    thr, sign = xf._favorable(anchors, 0)
    assert sign == 1
    assert xf._is_favorable(1.0, thr, sign) and not xf._is_favorable(0.1, thr, sign)


def test_gate_anchors_nonoverlapping():
    term = [float(i % 2) for i in range(10)]
    credit = [float((i + 1) % 2) for i in range(10)]
    target = [100.0 * (1.01 ** i) for i in range(10)]
    a = xf.gate_anchors(term, credit, target, horizon=2)
    assert len(a) == 4               # i=0,2,4,6 (i+2<10); 8 excluded (needs idx 10)
    assert all(len(row) == 3 for row in a)


# ---- grade_cell: planted conjunction edge + null ---------------------------

def _planted_series(n=200, edge=0.01):
    """term hi & credit hi (i%4==0) → +edge; else ~0. H=1 anchors."""
    term, credit, target = [], [], []
    price = 100.0
    for i in range(n):
        term.append(1.0 if i % 4 in (0, 1) else 0.0)
        credit.append(1.0 if i % 4 in (0, 2) else 0.0)
        target.append(price)
        # noise varies WITHIN each regime (keyed on the regime-cycle, not i%2) so the
        # gated set has non-zero variance and its t-stat is defined.
        noise = 0.0003 * (((i // 4) % 3) - 1)
        r = (edge + noise) if (i % 4 == 0) else noise
        price *= math.exp(r)
    return term, credit, target


def test_grade_cell_detects_conjunction_edge():
    term, credit, target = _planted_series(240, edge=0.01)
    row = xf.grade_cell(term, credit, target, horizon=1, split_frac=0.6,
                        cost_frac=0.0001, t_flag=2.0)
    assert row["verdict"] == "conjunction_pays"
    jg, tg, cg = row["conj_gate"], row["term_gate"], row["credit_gate"]
    assert jg["net_mean"] > tg["net_mean"] and jg["net_mean"] > cg["net_mean"]
    assert jg["t"] > 2.0


def test_grade_cell_null_is_no_conjunction_edge():
    n = 240
    term = [float(i % 4 in (0, 1)) for i in range(n)]
    credit = [float(i % 4 in (0, 2)) for i in range(n)]
    price = 100.0
    target = []
    for i in range(n):
        target.append(price)
        price *= math.exp(0.0003 if i % 2 else -0.0003)      # regime-independent
    row = xf.grade_cell(term, credit, target, horizon=1, split_frac=0.6,
                        cost_frac=0.0001, t_flag=2.0)
    assert row["verdict"] == "no_conjunction_edge"


def test_grade_cell_thin_is_no_data():
    term, credit, target = _planted_series(20)
    row = xf.grade_cell(term, credit, target, horizon=2, split_frac=0.6,
                        cost_frac=0.0001, t_flag=2.0)
    assert row["verdict"] == "no_data"


# ---- walk-forward ----------------------------------------------------------

def test_walkforward_robust_when_edge_persists():
    term, credit, target = _planted_series(500, edge=0.01)
    wf = xf.walkforward_conjunction(term, credit, target, horizon=1, k_eras=4,
                                    cost_frac=0.0001, t_flag=2.0)
    assert wf["verdict"] == "robust"
    assert wf["sign_consistency"] == 1.0 and wf["modern_significant"] is True


def test_walkforward_front_loaded_when_edge_decays():
    n = 600
    term = [float(i % 4 in (0, 1)) for i in range(n)]
    credit = [float(i % 4 in (0, 2)) for i in range(n)]
    cutoff = n // 2
    price, target = 100.0, []
    for i in range(n):
        target.append(price)
        noise = 0.0002 if i % 2 else -0.0002
        big = (i % 4 == 0)
        r = ((0.01 + noise) if i < cutoff else noise) if big else noise
        price *= math.exp(r)
    wf = xf.walkforward_conjunction(term, credit, target, horizon=1, k_eras=4,
                                    cost_frac=0.0001, t_flag=2.0)
    assert wf["modern_significant"] is False
    assert wf["verdict"] in {"era_front_loaded", "not_robust"}


def test_walkforward_insufficient_sample():
    term, credit, target = _planted_series(30)
    wf = xf.walkforward_conjunction(term, credit, target, horizon=1, k_eras=5,
                                    cost_frac=0.0001, t_flag=2.0)
    assert wf["verdict"] == "insufficient_sample"


# ---- run_probe / run_walkforward wiring with injected FRED urlopen ---------

def _fred_csv(pairs) -> str:
    return "DATE,VALUE\n" + "\n".join(f"{d},{v}" for d, v in pairs)


def _dates(n):
    return [f"20{10 + i // 250:02d}-{(i // 21) % 12 + 1:02d}-{i % 21 + 1:02d}" for i in range(n)]


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


def _all_bodies(n=800):
    ds = _dates(n)
    return {
        "VIXCLS": _fred_csv([(d, 15.0 + (i % 7)) for i, d in enumerate(ds)]),
        "VXVCLS": _fred_csv([(d, 17.0 + (i % 5)) for i, d in enumerate(ds)]),
        "BAMLH0A0HYM2": _fred_csv([(d, 3.0 + (i % 11) * 0.1) for i, d in enumerate(ds)]),
        "BAA10Y": _fred_csv([(d, 2.0 + (i % 9) * 0.1) for i, d in enumerate(ds)]),
        "SP500": _fred_csv([(d, 3000.0 + i) for i, d in enumerate(ds)]),
        "NASDAQ100": _fred_csv([(d, 3000.0 * (1.0 + 0.0004 * i)) for i, d in enumerate(ds)]),
    }


def test_run_probe_grades_both_credit_proxies():
    out = xf.run_probe(targets=("SP500",), urlopen=_fake_urlopen_factory(_all_bodies()),
                       horizons=(21,))
    creds = {r["credit"] for r in out["targets"]}
    assert creds == {"hy_oas_pct", "baa_10y"}          # both proxies graded in one run
    for r in out["targets"]:
        assert r["target"] == "SP500"
        assert r["verdict"] in {"conjunction_pays", "no_conjunction_edge", "no_data"}
    assert set(out["series_ranges"]) >= {"VIXCLS", "VXVCLS", "BAMLH0A0HYM2", "BAA10Y"}
    assert out["horizons"] == [21]


def test_run_probe_single_proxy_and_empty():
    out = xf.run_probe(targets=("SP500",), urlopen=_fake_urlopen_factory(_all_bodies()),
                       horizons=(21,), credit_proxies=(("baa_10y", "BAA10Y"),))
    assert len(out["targets"]) == 1 and out["targets"][0]["credit"] == "baa_10y"

    empty = xf.run_probe(targets=("NASDAQ100",), urlopen=_fake_urlopen_factory({}))
    assert all(r["verdict"] == "no_data" for r in empty["targets"])


def test_run_walkforward_wiring():
    out = xf.run_walkforward(cells=(("SP500", 21),),
                             urlopen=_fake_urlopen_factory(_all_bodies()), k_eras=4)
    creds = {c["credit"] for c in out["cells"]}
    assert creds == {"hy_oas_pct", "baa_10y"}
    for c in out["cells"]:
        assert c["target"] == "SP500" and c["horizon"] == 21
        assert c["verdict"] in {"robust", "era_front_loaded", "not_robust",
                                "insufficient_sample", "no_data"}
