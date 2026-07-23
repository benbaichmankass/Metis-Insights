"""M29 — tests for the crypto funding/OI/basis sleeve (no network).

Pure Bybit-shape parsers + daily resample + basis + percentile orientation +
snapshot schema, plus the load-bearing integration check: a crypto snapshot flows
through the SAME P4 machinery that grades value/COT snapshots (it emits the
valuation-snapshot schema, so it's graded UNCHANGED).
"""

from __future__ import annotations

import datetime as dt
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import crypto_signals_backfill as cbf  # noqa: E402
import crypto_signals_data as cd  # noqa: E402
from src.units.strategies.macro_thesis.thesis_engine import value_conviction  # noqa: E402
from src.units.strategies.macro_thesis.thesis_tick import _valueread_from_snapshot  # noqa: E402
from src.units.strategies.macro_thesis.valuation import value_to_direction  # noqa: E402


def _day_ms(day: str) -> int:
    d = dt.datetime.fromisoformat(day + "T00:00:00+00:00")
    return int(d.timestamp() * 1000)


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
# parsers
# ---------------------------------------------------------------------------


def test_parse_funding_history():
    payload = {"result": {"list": [
        {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1700003600000"},
        {"symbol": "BTCUSDT", "fundingRate": "-0.0002", "fundingRateTimestamp": "1700000000000"},
    ]}}
    got = cd.parse_funding_history(payload)
    assert [ms for ms, _ in got] == [1700000000000, 1700003600000]   # ascending
    assert got[0][1] == -0.0002


def test_parse_open_interest():
    payload = {"result": {"list": [{"openInterest": "12345.6", "timestamp": "1700000000000"}]}}
    assert cd.parse_open_interest(payload) == [(1700000000000, 12345.6)]


def test_parse_kline_close():
    payload = {"result": {"list": [
        ["1700086400000", "42000", "42500", "41000", "42300", "100", "x"],
        ["1700000000000", "41000", "41500", "40000", "41200", "90", "x"],
    ]}}
    got = cd.parse_kline_close(payload)
    assert got == [(1700000000000, 41200.0), (1700086400000, 42300.0)]   # ascending, close col


def test_resample_daily_last():
    pairs = [(_day_ms("2024-01-01"), 1.0), (_day_ms("2024-01-01") + 3600_000, 2.0),
             (_day_ms("2024-01-02"), 3.0)]
    got = cd.resample_daily_last(pairs)
    assert got == [("2024-01-01", 2.0), ("2024-01-02", 3.0)]   # last-of-day


def test_compute_basis():
    perp = [("2024-01-01", 102.0), ("2024-01-02", 99.0)]
    spot = [("2024-01-01", 100.0), ("2024-01-02", 100.0)]
    got = cd.compute_basis(perp, spot)
    assert got[0] == ("2024-01-01", 0.02)     # 2% premium
    assert got[1] == ("2024-01-02", -0.01)    # 1% discount
    assert cd.compute_basis([("2024-01-01", 5.0)], [("2024-01-01", 0.0)]) == []   # spot<=0 guard


# ---------------------------------------------------------------------------
# percentile orientation + schema
# ---------------------------------------------------------------------------


def _rising_then_spike(n=60):
    days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(n)]
    vals = [0.0001 + 0.000001 * i for i in range(n)]   # gentle rise
    series = list(zip(days, vals))
    series.append(("2024-06-01", 0.02))    # extreme high funding → crowded long → RICH
    series.append(("2024-06-02", -0.02))   # extreme negative → crowded short → CHEAP
    return series


def test_build_percentile_snapshots_contrarian_orientation():
    snaps = cd.build_percentile_snapshots(
        "BTCUSDT", "funding_rate", _rising_then_spike(), lookback=90, min_history=30,
        higher_is_cheaper=False, note="x",
    )
    high = next(s for s in snaps if s["as_of"] == "2024-06-01")
    low = next(s for s in snaps if s["as_of"] == "2024-06-02")
    assert high["label"] == "rich" and high["cheap_score"] < 0.30    # crowded long → short bias
    assert low["label"] == "cheap" and low["cheap_score"] > 0.70     # crowded short → long bias
    assert high["higher_is_cheaper"] is False
    assert high["asset_class"] == "crypto"
    assert high["observed_at"] == "2024-06-01"                       # PIT bare date
    for k in ("symbol", "metric", "value", "cheap_score", "label", "percentile", "n_history"):
        assert k in high


def test_build_crypto_snapshots_all_three_metrics():
    days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(50)]
    f = [(d, 0.0001 * (i % 5 + 1)) for i, d in enumerate(days)]
    b = [(d, 0.001 * (i % 3)) for i, d in enumerate(days)]
    oi = [(d, 1000.0 + i) for i, d in enumerate(days)]
    snaps = cd.build_crypto_snapshots("ETHUSDT", funding_daily=f, basis_daily=b, oi_daily=oi,
                                      lookback=90, min_history=30)
    metrics = {s["metric"] for s in snaps}
    assert metrics == {"funding_rate", "perp_basis", "open_interest"}
    assert all(s["symbol"] == "ETHUSDT" for s in snaps)


def test_snapshot_drives_conviction_and_direction():
    snaps = cd.build_percentile_snapshots(
        "BTCUSDT", "funding_rate", _rising_then_spike(), lookback=90, min_history=30,
        higher_is_cheaper=False,
    )
    high = next(s for s in snaps if s["as_of"] == "2024-06-01")
    read = _valueread_from_snapshot(high)
    assert value_conviction(read) is not None and value_conviction(read) > 0.4
    assert value_to_direction(read) == "bearish"    # crowded long funding → fade → short


# ---------------------------------------------------------------------------
# graded UNCHANGED by the P4 machinery
# ---------------------------------------------------------------------------


def test_crypto_snapshots_grade_through_p4_machinery():
    from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest
    from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries

    records = [
        {"symbol": "BTCUSDT", "metric": "funding_rate", "cheap_score": 0.95, "label": "cheap",
         "observed_at": "2024-01-05", "n_history": 90, "higher_is_cheaper": False},
        {"symbol": "BTCUSDT", "metric": "funding_rate", "cheap_score": 0.03, "label": "rich",
         "observed_at": "2024-01-12", "n_history": 90, "higher_is_cheaper": False},
    ]
    prices = {("BTCUSDT", "2024-01-06"): 45000.0, ("BTCUSDT", "2024-01-13"): 46000.0,
              ("BTCUSDT", "2024-01-20"): 47000.0}

    def price_at(sym, date):
        return prices.get((sym, str(date)[:10]))

    cfg = {"universe": [], "min_conviction": 0.4, "express_as": "debit_vertical",
           "account": "alpaca_options_paper"}
    entries = build_replay_entries(records, price_at, rebalance_dates=["2024-01-06", "2024-01-13"],
                                   cfg=cfg, horizon_days=7.0)
    assert len(entries) == 2
    assert {e["symbol"] for e in entries} == {"BTCUSDT"}
    assert all(e["direction"] in ("long", "short") for e in entries)
    card = run_thesis_backtest(entries, n_bins=4)
    assert card["n"] == 2
    assert "calibration_rank" in card and "edge_vs_baseline" in card


# ---------------------------------------------------------------------------
# fetch injectable + off-VM guard + backfill orchestration
# ---------------------------------------------------------------------------


def test_fetch_kline_injectable():
    body = '{"result":{"list":[["1700000000000","1","2","0.5","1.5","10","x"]]}}'
    got = cd.fetch_kline_close("BTCUSDT", urlopen=lambda u, timeout=None: _Resp(body))
    assert got == [(1700000000000, 1.5)]


def test_fetch_kline_base_fallback():
    # First base (bytick) returns empty; fetch must fall through to the second base.
    body = '{"result":{"list":[["1700000000000","1","2","0.5","1.5","10","x"]]}}'

    def urlopen(url, timeout=None):
        return _Resp(body if cd.BYBIT_BASES[1] in url else '{"result":{"list":[]}}')

    got = cd.fetch_kline_close("BTCUSDT", urlopen=urlopen)
    assert got == [(1700000000000, 1.5)]     # resolved via the fallback base
    assert cd._bases(None) == list(cd.BYBIT_BASES)
    assert cd._bases("http://x") == ["http://x"]


def test_fetch_offvm_guard(monkeypatch):
    monkeypatch.delenv("ICT_OFFVM_BUILD_HOST", raising=False)
    import pytest
    with pytest.raises(RuntimeError):
        cd.fetch_kline_close("BTCUSDT")


def test_backfill_injected_and_candles(tmp_path):
    days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)) for i in range(60)]
    funding = [(int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000),
                0.0001 * (i % 4)) for i, d in enumerate(days)]
    oi = [(int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000),
           1000.0 + i) for i, d in enumerate(days)]
    perp = [(int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000),
             45000.0 + 10 * i) for i, d in enumerate(days)]
    spot = [(int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000),
             44950.0 + 10 * i) for i, d in enumerate(days)]
    result = cbf.backfill(
        symbols=["BTCUSDT"],
        fetched_by_symbol={"BTCUSDT": {"funding": funding, "oi": oi, "perp_close": perp, "spot_close": spot}},
        lookback=90, min_history=30,
    )
    assert result["symbols_ok"] == 1
    assert result["by_symbol"]["BTCUSDT"] > 0
    assert len(result["candles"]["BTCUSDT"]) == 60

    snap_out = tmp_path / "crypto.jsonl"
    n = cbf.write_snapshots_fresh(result["rows"], snap_out)
    assert n == len(result["rows"]) > 0
    cdir = tmp_path / "candles"
    wc = cbf.write_candles(result["candles"], cdir)
    assert wc["BTCUSDT"] == 60
    assert (cdir / "BTCUSDT.csv").read_text().splitlines()[0] == "date,close"
