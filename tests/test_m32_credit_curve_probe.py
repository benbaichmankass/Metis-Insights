"""M32 — tests for the credit/rates risk-premium probe (no network; injected urlopen)."""

from __future__ import annotations

import io
import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import credit_curve_probe as ccp  # noqa: E402


# ---- feature builder -------------------------------------------------------

def test_level_pct_is_pit_and_bounded():
    vals = [float(i % 30) for i in range(60)]
    feat = ccp.build_credit_feature("level_pct", vals, pct_window=30)
    assert feat[0] is None                                # too few points to rank
    assert feat[-1] is not None and 0.0 <= feat[-1] <= 1.0


def test_mom_is_the_windowed_change():
    vals = [float(i) for i in range(50)]                  # +1 per step
    feat = ccp.build_credit_feature("mom", vals, mom_window=21)
    assert feat[20] is None                               # not warm until index >= mom_window
    assert math.isclose(feat[21], 21.0)                   # 21 − 0 over the 21-step window
    assert math.isclose(feat[40], 21.0)


def test_level_is_passthrough():
    vals = [0.5, -0.1, -0.8, 0.3]
    assert ccp.build_credit_feature("level", vals) == vals


def test_unknown_feature_raises():
    try:
        ccp.build_credit_feature("bogus", [1.0, 2.0])
    except ValueError:
        return
    raise AssertionError("expected ValueError on unknown feature")


# ---- run_probes with an injected FRED urlopen ------------------------------

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
    oas = _fred_csv([(d, 4.0 + math.sin(i * 0.05)) for i, d in enumerate(dates)])
    spx = _fred_csv([(d, 3000.0 + i) for i, d in enumerate(dates)])
    return {"BAMLH0A0HYM2": oas, "SP500": spx}


def test_run_probes_s2_grades_injected_series():
    probes = (("hy_oas_pct", "BAMLH0A0HYM2", "SP500", "level_pct"),)
    out = ccp.run_probes(probes=probes, horizons=(10, 21), mode="s2",
                         urlopen=_fake_urlopen_factory(_synthetic_bodies()))
    r = out["probes"][0]
    assert r["name"] == "hy_oas_pct" and r["n_aligned"] > 0
    assert r["verdict"] in {"directional_edge", "no_edge", "no_data"}
    assert {row["horizon"] for row in r["rows"]} == {10, 21}


def test_run_probes_s3_and_wf_modes_shape():
    probes = (("hy_oas_mom", "BAMLH0A0HYM2", "SP500", "mom"),)
    bodies = _fake_urlopen_factory(_synthetic_bodies())
    s3 = ccp.run_probes(probes=probes, horizons=(10, 21), mode="s3", urlopen=bodies)
    assert s3["probes"][0]["verdict"] in {"pays_oos_net", "s2_only_no_s3", "no_data"}
    wf = ccp.run_probes(probes=probes, horizons=(10, 21), mode="wf", urlopen=bodies)
    assert wf["probes"][0]["verdict"] in {"robust", "regime_dependent", "not_robust",
                                          "insufficient_sample", "no_data"}


def test_run_probes_empty_series_is_no_data_not_crash():
    probes = (("nfci", "NFCI", "SP500", "level"),)
    out = ccp.run_probes(probes=probes, horizons=(21,), mode="s2",
                         urlopen=_fake_urlopen_factory({}))
    assert out["probes"][0]["verdict"] == "no_data"
    assert "empty series" in out["probes"][0]["reason"]


def test_default_probes_cover_credit_curve_and_conditions():
    names = {p[0] for p in ccp.DEFAULT_PROBES}
    assert {"hy_oas_pct", "ig_oas_pct", "curve_10y2y", "nfci"} <= names
    # every probe targets SP500 (the credit-leads-equity cross-asset test)
    assert all(p[2] == "SP500" for p in ccp.DEFAULT_PROBES)
