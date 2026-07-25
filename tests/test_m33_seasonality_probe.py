"""M33 — tests for the calendar-seasonality probe (no network; injected urlopen)."""

from __future__ import annotations

import io
import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import seasonality_probe as sp  # noqa: E402


# ---- calendar helpers -------------------------------------------------------

def test_dow_and_dom_and_bucket():
    assert sp._dow("2026-07-27") == 0          # a Monday
    assert sp._dow("2026-07-25") == 5          # a Saturday (weekend)
    assert sp._dom("2026-07-27") == 27
    assert sp._bucket("2026-07-27", "dow") == "Mon"
    assert sp._bucket("2026-07-25", "dow") is None   # weekend dropped
    assert sp._bucket("2026-07-05", "dom") == "early"
    assert sp._bucket("2026-07-27", "dom") == "late"
    assert sp._bucket("2026-07-28", "tom") == "turn"  # dom>=26
    assert sp._bucket("2026-07-15", "tom") == "rest"


def test_daily_returns_drops_first_and_bad():
    dated = [("2020-01-01", 100.0), ("2020-01-02", 110.0), ("2020-01-03", "x"),
             ("2020-01-04", 121.0)]
    rets = sp._daily_returns(dated)
    # first dropped; 110/100-1=0.10; the "x" breaks the chain so 121 has no valid prev
    assert len(rets) == 1
    assert rets[0][0] == "2020-01-02" and math.isclose(rets[0][1], 0.10, rel_tol=1e-9)


# ---- grade_dimension: planted edge + null -----------------------------------

def _synthetic_dom_dates(n=400):
    return [f"2020-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}" for i in range(n)]


def test_grade_dimension_detects_planted_late_month_edge():
    # "late" (dom>20) days ~+1% (with tiny variance so the t-stat is defined), others 0.
    dates = _synthetic_dom_dates(400)
    returns = [(d, (0.01 + (0.0002 if i % 2 else -0.0002)) if sp._dom(d) > 20 else 0.0)
               for i, d in enumerate(dates)]
    row = sp.grade_dimension(returns, "dom", split_frac=0.6, cost_frac=0.0001, t_flag=2.0)
    assert row["best_bucket"] == "late"
    assert row["verdict"] == "seasonal_edge"
    assert row["oos_gross_mean"] > 0 and row["oos_t"] > 2.0


def test_grade_dimension_null_is_no_seasonal_edge():
    dates = _synthetic_dom_dates(400)
    returns = [(d, (0.001 if i % 2 else -0.001)) for i, d in enumerate(dates)]  # bucket-independent
    row = sp.grade_dimension(returns, "dom", split_frac=0.6, cost_frac=0.0001, t_flag=2.0)
    assert row["verdict"] == "no_seasonal_edge"


def test_grade_dimension_thin_is_no_data():
    returns = [("2020-01-02", 0.01)] * 10
    assert sp.grade_dimension(returns, "dow", split_frac=0.6, cost_frac=0.0001,
                              t_flag=2.0)["verdict"] == "no_data"


# ---- run_probe wiring with injected FRED urlopen ----------------------------

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


def test_run_probe_shape_and_no_data():
    dates = _synthetic_dom_dates(300)
    spx = _fred_csv([(d, 3000.0 + i) for i, d in enumerate(dates)])
    out = sp.run_probe(targets=("SP500",), urlopen=_fake_urlopen_factory({"SP500": spx}))
    r = out["targets"][0]
    assert r["target"] == "SP500" and r["verdict"] in {"seasonal_edge", "no_seasonal_edge"}
    assert {row["dimension"] for row in r["rows"]} == {"dow", "dom", "tom"}

    empty = sp.run_probe(targets=("DJIA",), urlopen=_fake_urlopen_factory({}))
    assert empty["targets"][0]["verdict"] == "no_data"


# ---- walk-forward fixed-bucket confirmation ---------------------------------

def test_walkforward_robust_when_edge_persists_all_eras():
    # "late" bucket carries a stable +1% edge across the whole span → robust.
    dates = _synthetic_dom_dates(500)
    returns = [(d, (0.01 + (0.0002 if i % 2 else -0.0002)) if sp._dom(d) > 20 else 0.0)
               for i, d in enumerate(dates)]
    wf = sp.walkforward_fixed_bucket(returns, "dom", "late", k_eras=4,
                                     cost_frac=0.0001, t_flag=2.0)
    assert wf["verdict"] == "robust"
    assert wf["sign_consistency"] == 1.0 and wf["modern_significant"] is True


def test_walkforward_era_front_loaded_when_edge_decays():
    # +1% in the early eras, ~0 in the late eras → sign holds early, modern insignificant.
    dates = _synthetic_dom_dates(600)
    cutoff = len(dates) // 2
    returns = []
    for i, d in enumerate(dates):
        if sp._dom(d) > 20:
            r = (0.01 + (0.0002 if i % 2 else -0.0002)) if i < cutoff else (0.0002 if i % 2 else -0.0002)
        else:
            r = 0.0
        returns.append((d, r))
    wf = sp.walkforward_fixed_bucket(returns, "dom", "late", k_eras=4,
                                     cost_frac=0.0001, t_flag=2.0)
    assert wf["modern_significant"] is False
    assert wf["verdict"] in {"era_front_loaded", "not_robust"}


def test_walkforward_insufficient_sample():
    returns = [("2020-01-27", 0.01)] * 10  # only ~10 "late" rows, k_eras=5 → insufficient
    wf = sp.walkforward_fixed_bucket(returns, "dom", "late", k_eras=5,
                                     cost_frac=0.0001, t_flag=2.0)
    assert wf["verdict"] == "insufficient_sample"


def test_run_walkforward_wiring():
    dates = _synthetic_dom_dates(500)
    ndx = _fred_csv([(d, 3000.0 * (1.0 + 0.0005 * i)) for i, d in enumerate(dates)])
    out = sp.run_walkforward(cells=(("NASDAQ100", "dom", "late"),),
                             urlopen=_fake_urlopen_factory({"NASDAQ100": ndx}), k_eras=4)
    r = out["cells"][0]
    assert r["target"] == "NASDAQ100" and r["dimension"] == "dom"
    assert r["verdict"] in {"robust", "era_front_loaded", "not_robust",
                            "insufficient_sample", "no_data"}
