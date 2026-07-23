"""M29 P1b — tests for the gas-calibration data adapter (no network)."""

from __future__ import annotations

import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import sysdyn_gas_data as gd  # noqa: E402


class _Resp:
    def __init__(self, t):
        self._t = t

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_iso_week():
    assert gd.iso_week("2020-01-01") in (1, 53)   # depends on ISO year boundary
    assert gd.iso_week("2020-07-15") == 29
    assert gd.iso_week("not-a-date") == 1          # fail-safe, never raises


def test_calendar_exog_winter_peak_summer_trough():
    winter = gd.calendar_exog_for_week(2)    # deep winter (peak_week)
    summer = gd.calendar_exog_for_week(28)   # deep summer
    # Heating demand is highest in winter, near-zero in summer.
    assert winter["heating_demand"] > summer["heating_demand"]
    # Injection window is the complement — fills in summer, ~0 in winter.
    assert summer["injection_season"] > winter["injection_season"]
    assert winter["injection_season"] < 0.1
    for e in (winter, summer):
        assert set(e) == {"heating_demand", "injection_season"}
        assert e["heating_demand"] >= 0.0 and e["injection_season"] >= 0.0


def test_build_calibration_series_windows_and_aligns():
    # 3 years of weekly points; window to the last ~1y keeps only the recent tail.
    import datetime as dt
    start = dt.date(2019, 1, 4)
    dated = [((start + dt.timedelta(days=7 * i)).isoformat(), 2.0 + (i % 10) * 0.1) for i in range(156)]
    dates, observed, exog = gd.build_calibration_series(dated, window_years=1.0)
    assert len(dates) == len(observed) == len(exog)
    assert 45 <= len(dates) <= 60                       # ~52 weeks kept
    assert dates == sorted(dates)                        # ascending
    assert dates[-1] == dated[-1][0]                     # tail preserved
    assert all(set(e) == {"heating_demand", "injection_season"} for e in exog)


def test_build_calibration_series_empty_is_safe():
    assert gd.build_calibration_series([]) == ([], [], [])


def test_fetch_weekly_ng_price_injectable():
    csv = "observation_date,WHHNGSP\n2020-01-03,2.10\n2020-01-10,2.05\n2020-01-17,.\n2020-01-24,2.20\n"
    got = gd.fetch_weekly_ng_price_dated(urlopen=lambda url, timeout=None: _Resp(csv))
    # missing "." row skipped; the rest parsed dated + ascending
    assert got == [("2020-01-03", 2.10), ("2020-01-10", 2.05), ("2020-01-24", 2.20)]
