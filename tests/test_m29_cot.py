"""M29 — tests for the CFTC-COT positioning sleeve (no network).

Two halves:
  1. Pure parse / COT-index / snapshot-schema construction (injected Socrata JSON).
  2. The load-bearing integration check: a COT snapshot flows through the SAME P4
     machinery (`build_replay_entries` → `run_thesis_backtest`) that grades value
     snapshots — proving the positioning sleeve is graded UNCHANGED because it emits
     the valuation-snapshot schema.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import cot_data as cot  # noqa: E402
import cot_snapshot_backfill as cbf  # noqa: E402
from src.units.strategies.macro_thesis.thesis_engine import value_conviction  # noqa: E402
from src.units.strategies.macro_thesis.thesis_tick import _valueread_from_snapshot  # noqa: E402
from src.units.strategies.macro_thesis.valuation import value_to_direction  # noqa: E402


class _Resp:
    def __init__(self, t):
        self._t = t

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _weekly(n, *, start=dt.date(2021, 1, 5), long0=1000, step=1, short=500):
    """n genuinely-unique weekly (date, spec_long, spec_short) tuples, ascending."""
    return [((start + dt.timedelta(days=7 * i)).isoformat(), long0 + step * i, short)
            for i in range(n)]


def _rows(code, name, series):
    """Socrata-shaped rows: series = [(date, spec_long, spec_short)]."""
    return [
        {"report_date_as_yyyy_mm_dd": f"{d}T00:00:00.000",
         "market_and_exchange_names": name, "cftc_contract_market_code": code,
         "open_interest_all": "100000", "noncomm_positions_long_all": str(sl),
         "noncomm_positions_short_all": str(ss), "comm_positions_long_all": "10",
         "comm_positions_short_all": "20"}
        for d, sl, ss in series
    ]


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_parse_cot_rows_coerces_and_sorts():
    payload = _rows("067651", "CRUDE OIL, LIGHT SWEET-WTI - NYMEX",
                    [("2024-01-16", "5,000", "2000"), ("2024-01-09", 4000, 2500)])
    got = cot.parse_cot_rows(payload)
    assert [r["date"] for r in got] == ["2024-01-09", "2024-01-16"]     # ascending
    assert got[0]["spec_long"] == 4000.0 and got[0]["spec_short"] == 2500.0
    assert got[1]["spec_long"] == 5000.0                                 # comma stripped
    # a row missing the date is dropped
    assert cot.parse_cot_rows([{"noncomm_positions_long_all": "1"}]) == []


def test_dominant_market_rows_collapses_to_modal_code():
    rows = [{"date": "2024-01-02", "code": "A", "spec_long": 1, "spec_short": 0},
            {"date": "2024-01-09", "code": "A", "spec_long": 2, "spec_short": 0},
            {"date": "2024-01-02", "code": "B", "spec_long": 9, "spec_short": 0}]
    kept = cot.dominant_market_rows(rows)
    assert {r["code"] for r in kept} == {"A"}     # A has more rows → dominant


def test_spec_net_series():
    rows = [{"date": "2024-01-02", "code": "A", "spec_long": 5000, "spec_short": 2000},
            {"date": "2024-01-09", "code": "A", "spec_long": 3000, "spec_short": 4000}]
    assert cot.spec_net_series(rows) == [("2024-01-02", 3000.0), ("2024-01-09", -1000.0)]


def test_build_cot_query_url_soql():
    url = cot.build_cot_query_url("EURO FX", limit=10)
    assert url.startswith(cot.COT_SOCRATA_BASE + "?")
    assert "EURO+FX" in url or "EURO%20FX" in url    # urlencoded space
    assert "noncomm_positions_long_all" in url
    assert "limit=10" in url


def test_fetch_cot_market_history_injectable():
    payload = _rows("067651", "CRUDE OIL, LIGHT SWEET-WTI - NYMEX",
                    [("2024-01-09", 4000, 2500)])
    import json
    got = cot.fetch_cot_market_history("CRUDE", urlopen=lambda u, timeout=None: _Resp(json.dumps(payload)))
    assert len(got) == 1 and got[0]["spec_long"] == 4000.0


def test_fetch_cot_market_history_offvm_guard(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    import pytest
    with pytest.raises(RuntimeError):
        cot.fetch_cot_market_history("CRUDE")   # no urlopen + not off-VM → refuse


# ---------------------------------------------------------------------------
# COT-index orientation + snapshot schema
# ---------------------------------------------------------------------------


def _market():
    return {"key": "crude", "name": "CRUDE OIL, LIGHT SWEET-WTI", "symbol": "USO", "asset_class": "commodity"}


def test_build_cot_snapshots_orientation_contrarian():
    # 60 unique weeks of a modest rising spec_net, then a crowded-long spike, then a
    # washed-out plunge — on the two dates the orientation must flip cheap/rich.
    series = _weekly(60)                            # ends 2021-01-05 + 59*7 days
    series.append(("2023-06-06", 50000, 500))      # crowded net-long → should read RICH
    series.append(("2023-06-13", 500, 50000))      # net-short extreme → should read CHEAP
    rows = _rows("067651", "CRUDE OIL, LIGHT SWEET-WTI - NYMEX", series)
    parsed = cot.parse_cot_rows(rows)
    snaps = cot.build_cot_snapshots(_market(), parsed, lookback=156, min_history=52)
    assert len(snaps) >= 2
    crowded = next(s for s in snaps if s["inputs"]["report_date"] == "2023-06-06")
    washed = next(s for s in snaps if s["inputs"]["report_date"] == "2023-06-13")
    # contrarian: crowded net-long → rich (low cheap_score → short); washed out → cheap (long)
    assert crowded["label"] == "rich" and crowded["cheap_score"] < 0.30
    assert washed["label"] == "cheap" and washed["cheap_score"] > 0.70
    assert crowded["higher_is_cheaper"] is False
    # PIT: observed_at is the report date + release lag (Friday)
    assert crowded["observed_at"] == "2023-06-09T00:00:00Z"     # Tue 06-06 + 3d
    # schema completeness
    for k in ("symbol", "asset_class", "metric", "value", "cheap_score", "label",
              "z_score", "percentile", "n_history", "higher_is_cheaper", "observed_at", "source"):
        assert k in crowded


def test_cot_snapshot_drives_conviction_and_direction():
    # A COT snapshot must round-trip through the SAME ValueRead → conviction/direction
    # path the value sleeve uses (this is what makes it gradable unchanged).
    series = _weekly(60)
    series.append(("2023-06-06", 50000, 500))   # crowded → bearish/short, high conviction
    parsed = cot.parse_cot_rows(_rows("067651", "CRUDE OIL, LIGHT SWEET-WTI - NYMEX", series))
    snap = [s for s in cot.build_cot_snapshots(_market(), parsed, lookback=156, min_history=52)
            if s["inputs"]["report_date"] == "2023-06-06"][0]
    read = _valueread_from_snapshot(snap)
    assert value_conviction(read) is not None and value_conviction(read) > 0.4   # extreme → conviction
    assert value_to_direction(read) == "bearish"    # crowded long → fade → short


# ---------------------------------------------------------------------------
# integration — graded UNCHANGED by the P4 machinery
# ---------------------------------------------------------------------------


def test_cot_snapshots_grade_through_p4_machinery():
    from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest
    from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries

    # Two rebalance dates, each with a strong (extreme) COT read on USO.
    records = [
        {"symbol": "USO", "metric": "cot_spec_positioning", "cheap_score": 0.95,
         "label": "cheap", "observed_at": "2024-01-05T00:00:00Z", "n_history": 156,
         "higher_is_cheaper": False},
        {"symbol": "USO", "metric": "cot_spec_positioning", "cheap_score": 0.05,
         "label": "rich", "observed_at": "2024-02-05T00:00:00Z", "n_history": 156,
         "higher_is_cheaper": False},
    ]
    prices = {("USO", "2024-01-06"): 70.0, ("USO", "2024-02-05"): 75.0,
              ("USO", "2024-03-06"): 80.0}

    def price_at(sym, date):
        return prices.get((sym, str(date)[:10]))

    cfg = {"universe": [], "min_conviction": 0.4, "express_as": "debit_vertical",
           "account": "alpaca_options_paper"}
    entries = build_replay_entries(
        records, price_at, rebalance_dates=["2024-01-06", "2024-02-05"], cfg=cfg, horizon_days=30.0,
    )
    # Both extreme reads form a directional thesis with a resolvable forward price.
    assert len(entries) == 2
    dirs = {e["symbol"]: e["direction"] for e in entries}
    assert dirs["USO"] in ("long", "short")     # a real side, not neutral
    card = run_thesis_backtest(entries, n_bins=4)
    assert card["n"] == 2                        # the P4 scorer graded them unchanged
    assert "calibration_rank" in card and "edge_vs_baseline" in card


# ---------------------------------------------------------------------------
# backfill orchestration
# ---------------------------------------------------------------------------


def test_backfill_injected_rows_and_fresh_write(tmp_path):
    series = _weekly(80)
    parsed = cot.parse_cot_rows(_rows("067651", "CRUDE - NYMEX", series))
    result = cbf.backfill(
        markets=[_market()], market_rows={"crude": parsed}, min_history=52, lookback=156,
    )
    assert result["markets_ok"] == 1
    assert result["by_market"]["crude"] > 0
    assert all(r["symbol"] == "USO" for r in result["rows"])

    out = tmp_path / "cot.jsonl"
    n = cbf.write_snapshots_fresh(result["rows"], out)
    assert n == len(result["rows"])
    lines = out.read_text().strip().splitlines()
    assert len(lines) == n
    import json
    assert json.loads(lines[0])["metric"] == "cot_spec_positioning"
