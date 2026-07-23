"""M29 P1c — tests for the dual-target gas calibration (EIA storage + weather HDD).

Two halves, both network-free (injected responses / synthetic data):

  1. The new off-VM readers' PURE parsers/aggregators — EIA v2 storage JSON, the
     Open-Meteo daily-temp JSON, daily→HDD, weekly-HDD aggregation, the national
     weight-average, and the dual-target series builder's point-in-time alignment.
  2. The load-bearing check: a synthetic DUAL round-trip — generate observed
     storage + price FROM the seed model (known params, a synthetic weather-HDD
     driver) and confirm ``run_dual_calibration`` reproduces BOTH targets
     out-of-sample and emits a well-formed dual scorecard with the go/no-go verdict.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import sysdyn_gas_calibrate as gc  # noqa: E402
import sysdyn_gas_data as gd  # noqa: E402
from src.sysdyn.engine import simulate  # noqa: E402
from src.sysdyn.seed_gas import build_gas_storage_model, price_series, storage_series  # noqa: E402


class _Resp:
    def __init__(self, t):
        self._t = t

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# EIA v2 storage parser
# ---------------------------------------------------------------------------


def test_parse_eia_v2_series_sorts_and_coerces():
    payload = {
        "response": {
            "data": [
                {"period": "2024-01-19", "value": "2500"},   # v2 values are strings
                {"period": "2024-01-05", "value": 2650},     # number tolerated too
                {"period": "2024-01-12", "value": "2570"},
                {"period": "2024-01-26", "value": None},     # missing → skipped
                {"period": None, "value": "9"},              # no period → skipped
            ]
        }
    }
    got = gd.parse_eia_v2_series(payload)
    assert got == [("2024-01-05", 2650.0), ("2024-01-12", 2570.0), ("2024-01-19", 2500.0)]


def test_parse_eia_v2_series_bad_payload_is_empty():
    assert gd.parse_eia_v2_series(None) == []
    assert gd.parse_eia_v2_series({"nope": 1}) == []
    assert gd.parse_eia_v2_series("not-a-dict") == []


def test_fetch_eia_storage_no_key_is_empty(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    # No key AND no injected urlopen → honest empty (never raises, never fetches).
    assert gd.fetch_eia_storage_dated() == []


def test_fetch_eia_storage_injectable():
    body = '{"response": {"data": [{"period": "2024-02-02", "value": "2400"}]}}'
    got = gd.fetch_eia_storage_dated(api_key="x", urlopen=lambda url, timeout=None: _Resp(body))
    assert got == [("2024-02-02", 2400.0)]


# ---------------------------------------------------------------------------
# Weather HDD — Open-Meteo parse, daily HDD, weekly aggregation, national basket
# ---------------------------------------------------------------------------


def test_parse_open_meteo_daily():
    payload = {"daily": {"time": ["2024-01-02", "2024-01-01"],
                         "temperature_2m_mean": [20.0, 15.0]}}
    got = gd.parse_open_meteo_daily(payload)
    assert got == [("2024-01-01", 15.0), ("2024-01-02", 20.0)]   # ascending
    assert gd.parse_open_meteo_daily({}) == []
    assert gd.parse_open_meteo_daily(None) == []


def test_daily_hdd_from_temp():
    assert gd.daily_hdd_from_temp(65.0) == 0.0
    assert gd.daily_hdd_from_temp(45.0) == 20.0     # 65 - 45
    assert gd.daily_hdd_from_temp(80.0) == 0.0      # warm day → no heating demand
    assert gd.daily_hdd_from_temp(None) == 0.0      # honest: missing reading → 0
    assert gd.daily_hdd_from_temp(float("nan")) == 0.0


def test_weekly_hdd_ending_means_the_window():
    # 14 days of daily HDD; two week-end dates → mean over the 7 days ending each.
    days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(14)]
    daily = [(d, 10.0) for d in days[:7]] + [(d, 20.0) for d in days[7:]]
    got = gd.weekly_hdd_ending(daily, [days[6], days[13]], days=7)
    assert got[days[6]] == 10.0     # week 1 all 10s
    assert got[days[13]] == 20.0    # week 2 all 20s
    # a week-end with no covered days → None (dropped downstream)
    assert gd.weekly_hdd_ending(daily, ["1999-01-01"], days=7)["1999-01-01"] is None


def test_national_daily_hdd_weight_average_and_missing_city():
    def urlopen(url, timeout=None):
        # Chicago colder than Atlanta; Atlanta returns empty (renormalise over present).
        if "latitude=41.85" in url:  # chicago
            return _Resp('{"daily":{"time":["2024-01-01"],"temperature_2m_mean":[25.0]}}')
        if "latitude=33.75" in url:  # atlanta
            return _Resp('{"daily":{"time":[],"temperature_2m_mean":[]}}')
        return _Resp('{"daily":{"time":["2024-01-01"],"temperature_2m_mean":[45.0]}}')

    got = gd.national_daily_hdd("2024-01-01", "2024-01-01",
                                cities=(("chicago", 41.85, -87.65, 0.5),
                                        ("atlanta", 33.75, -84.39, 0.3),
                                        ("nyc", 40.71, -74.01, 0.2)),
                                urlopen=urlopen)
    # chicago HDD=40 (w .5), nyc HDD=20 (w .2), atlanta absent → renormalise over .7
    assert len(got) == 1
    d, v = got[0]
    assert d == "2024-01-01"
    assert v == (0.5 * 40.0 + 0.2 * 20.0) / 0.7


# ---------------------------------------------------------------------------
# dual-target series builder — point-in-time alignment on the storage clock
# ---------------------------------------------------------------------------


def test_build_dual_calibration_series_aligns_and_anchors():
    dates = [(dt.date(2020, 1, 2) + dt.timedelta(days=7 * i)).isoformat() for i in range(20)]
    storage = [(d, 2000.0 + 10.0 * i) for i, d in enumerate(dates)]
    # price observed a few days before each storage date → as-of-prior must still resolve
    price = [((dt.date.fromisoformat(d) - dt.timedelta(days=2)).isoformat(), 3.0 + 0.01 * i)
             for i, d in enumerate(dates)]
    daily = []
    for d in dates:
        for k in range(7):
            daily.append(((dt.date.fromisoformat(d) - dt.timedelta(days=k)).isoformat(), 12.0))

    ds, obs_s, obs_p, exog, meta = gd.build_dual_calibration_series(storage, price, daily)
    assert len(ds) == len(obs_s) == len(obs_p) == len(exog) == 20
    assert meta["initial_storage"] == 2000.0                      # anchor = first observed
    assert meta["storage_normal"] == sum(obs_s) / len(obs_s)      # normal = mean observed
    assert all(set(e) == {"heating_demand", "injection_season"} for e in exog)
    assert all(e["heating_demand"] == 12.0 for e in exog)         # real weekly-mean HDD


def test_build_dual_calibration_series_drops_weeks_missing_inputs():
    dates = [(dt.date(2020, 1, 2) + dt.timedelta(days=7 * i)).isoformat() for i in range(5)]
    storage = [(d, 2000.0) for d in dates]
    price = [(dates[0], 3.0)]                        # only the FIRST week has an at/prior price
    daily = [(d, 10.0) for d in dates]              # HDD only ON each date (window mean still resolves)
    ds, obs_s, obs_p, exog, meta = gd.build_dual_calibration_series(storage, price, daily)
    # weeks after the first still resolve price via as-of-PRIOR (last known 3.0) → all kept
    assert len(ds) == 5
    # but with NO price at all, every week is dropped
    ds2, *_ = gd.build_dual_calibration_series(storage, [], daily)
    assert ds2 == []


# ---------------------------------------------------------------------------
# synthetic DUAL round-trip — the load-bearing correctness check
# ---------------------------------------------------------------------------


def _synthetic_dual(truth, *, weeks=140, start=dt.date(2019, 1, 3), initial_storage=2400.0):
    """Generate observed storage + price + a per-week-constant daily HDD FROM the seed.

    ``price_feedback`` is 0 in ``truth`` so storage decouples from price (and from
    ``storage_normal``) — the round-trip is then exact regardless of the mean-derived
    normal the runner picks. Heating demand is a deterministic winter-peaking season."""
    dates = [(start + dt.timedelta(days=7 * i)).isoformat() for i in range(weeks)]
    exog = []
    daily = []
    for d in dates:
        wk = gd.iso_week(d)
        hd = max(0.0, 8.0 + 10.0 * math.cos(2.0 * math.pi * ((wk - 2) / 52.0)))  # winter peak
        inj = gd.calendar_exog_for_week(wk)["injection_season"]
        exog.append({"heating_demand": hd, "injection_season": inj})
        for k in range(7):
            daily.append(((dt.date.fromisoformat(d) - dt.timedelta(days=k)).isoformat(), hd))
    model = build_gas_storage_model(initial_storage=initial_storage)
    traj = simulate(model, truth, exog, weeks, dt=1.0)
    obs_storage = list(storage_series(traj))
    obs_price = list(price_series(traj))
    dated_storage = list(zip(dates, obs_storage))
    dated_price = list(zip(dates, obs_price))
    return dated_storage, dated_price, daily


def test_synthetic_dual_round_trip_fits_both_targets_out_of_sample():
    # storage_normal used to GENERATE price must match what the runner derives (mean
    # of observed storage), so generate storage first (feedback=0 → normal-independent),
    # then set truth base/price on that mean.
    inj_rate, wd_rate = 90.0, 4.0
    # First pass: get the storage mean with a placeholder normal (price irrelevant to storage).
    truth0 = {"base_price": 3.0, "storage_normal": 2400.0, "inj_rate": inj_rate,
              "wd_rate": wd_rate, "price_k": 1.5, "price_feedback": 0.0}
    dated_storage, _p0, _d0 = _synthetic_dual(truth0)
    normal = sum(v for _d, v in dated_storage) / len(dated_storage)
    # Regenerate with the model's storage_normal == the mean the runner will use.
    truth = {**truth0, "storage_normal": normal, "price_k": 1.8, "base_price": 3.2}
    dated_storage, dated_price, daily = _synthetic_dual(truth)

    card = gc.run_dual_calibration(
        dated_storage=dated_storage, dated_price=dated_price, daily_hdd=daily,
        window_years=None, holdout_frac=0.25, n_folds=3, generated_at="2026-07-23T00:00:00Z",
    )

    assert "error" not in card
    assert card["mode"] == "dual_target"
    th = card["train_holdout"]
    # Storage (the anchor) + price (the readout) both reproduce OOS on model-generated
    # data — the machinery works end-to-end. We do NOT over-assert exact param recovery:
    # the joint fit is genuinely equifinal here (a wd_rate/price_feedback trade-off gives
    # a near-equal fit) — that equifinality is exactly what the stability block MEASURES,
    # not a property the round-trip must exhibit (same stance as the P1b round-trip test).
    assert th["storage_fit"]["oos_r2"] is not None and th["storage_fit"]["oos_r2"] > 0.8
    assert th["price_fit"]["oos_r2"] is not None and th["price_fit"]["oos_r2"] > 0.5
    assert th["price_fit"]["in_sample_r2"] > 0.9
    # anchor/normal derived from the real series
    assert card["initial_storage"] == round(dated_storage[0][1], 6)
    # verdict shape + go/no-go present
    vd = card["verdict"]
    assert vd["label"] in {"mechanistic_edge", "storage_fits_no_price_edge",
                           "price_edge_but_equifinal", "no_mechanistic_edge"}
    assert vd["go_no_go"] in {"invest_deeper", "park_deeper_investment"}
    assert set(card["stability"]["param_rel_spread"]) >= set(gc.STRUCTURAL_FREE_PARAMS)
    assert card["generated_at"] == "2026-07-23T00:00:00Z"


def test_dual_thin_data_returns_error_envelope():
    dated_storage = [("2020-01-03", 2100.0), ("2020-01-10", 2080.0)]
    dated_price = [("2020-01-03", 3.0), ("2020-01-10", 3.1)]
    daily = [("2020-01-03", 10.0), ("2020-01-10", 10.0)]
    card = gc.run_dual_calibration(dated_storage=dated_storage, dated_price=dated_price,
                                   daily_hdd=daily, window_years=None, n_folds=4)
    assert "error" in card
    assert "train_holdout" not in card
