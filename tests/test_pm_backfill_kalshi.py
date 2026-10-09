"""Offline tests for scripts/macro/pm_backfill_kalshi.py (fixture JSON, no network)."""
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "macro"))
import pm_backfill_kalshi as bf  # noqa: E402

CFG = {"kalshi": {"base_url": "https://k.test", "series": {"KXCPI": "cpi"}},
       "backfill": {"series": ["KXCPI"], "window_hours_before_close": 24, "period_minutes": 60}}
LIVE = {"markets": [{"ticker": "KXCPI-26AUG-T0.3", "event_ticker": "KXCPI-26AUG", "close_time": "2026-09-11T12:25:00Z",
                     "result": "yes", "floor_strike": 0.3, "strike_type": "greater", "expiration_value": "0.4"}], "cursor": ""}
HIST = {"markets": [{"ticker": "CPI-21JUN-T0.6", "event_ticker": "CPI-21JUN", "close_time": "2021-07-12T23:00:00Z", "result": "yes"},
                    {"ticker": "KXCPI-26AUG-T0.3", "close_time": "2026-09-11T12:25:00Z"}], "cursor": ""}
CANDLE = {"candlesticks": [
    {"end_period_ts": 1789124400, "open_interest_fp": "10", "volume_fp": "2", "price": {"previous_dollars": "0.40"},
     "yes_bid": {"open_dollars": "0.38", "high_dollars": "0.41", "low_dollars": "0.37", "close_dollars": "0.40"},
     "yes_ask": {"open_dollars": "0.42", "high_dollars": "0.44", "low_dollars": "0.41", "close_dollars": "0.43"}},
    {"end_period_ts": 1789120800, "yes_bid": {}, "yes_ask": {}}]}


def fake(url):
    if "/historical/markets?" in url:
        return HIST
    if "/markets?" in url:
        assert "status=settled" in url
        return LIVE
    if "/candlesticks" in url:
        return CANDLE
    raise AssertionError(url)


def mk(tmp_path, fetch=fake, **kw):
    lim = bf.Limiter(2.5, clock=lambda: 0.0, sleep=lambda s: None)
    return bf.backfill_series(CFG, "KXCPI", fetch, tmp_path, lim, now="2026-10-09T06:00:00Z", **kw), lim


def test_list_dedupes_and_routes_endpoints():
    ms = bf.list_markets("https://k.test", "KXCPI", fake)
    assert [m["ticker"] for m in ms] == ["CPI-21JUN-T0.6", "KXCPI-26AUG-T0.3"]
    assert {m["ticker"]: m["_endpoint"] for m in ms} == {"CPI-21JUN-T0.6": "historical", "KXCPI-26AUG-T0.3": "live"}
    assert "/historical/markets/CPI-21JUN-T0.6/candlesticks" in bf.candle_url("https://k.test", "KXCPI", ms[0], 24, 60)
    assert "/series/KXCPI/markets/KXCPI-26AUG-T0.3/candlesticks" in bf.candle_url("https://k.test", "KXCPI", ms[1], 24, 60)


def test_rows_are_point_in_time_and_sorted(tmp_path):
    st, _ = mk(tmp_path)
    rows = [json.loads(x) for x in (tmp_path / "KXCPI.jsonl").read_text().splitlines()]
    assert st["rows_written"] == 4 and st["fetched_markets"] == 2
    r = [x for x in rows if x["market_id"] == "KXCPI-26AUG-T0.3"]
    assert [x["candle_end_utc"] for x in r] == sorted(x["candle_end_utc"] for x in r)  # own timestamps
    full = r[-1]
    assert (full["yes_bid_close"], full["yes_ask_close"], full["volume"]) == (0.40, 0.43, 2.0)
    assert r[0]["yes_bid_close"] is None and full["result"] == "yes" and full["content_hash"]


def test_resume_skips_done_markets_and_never_duplicates(tmp_path):
    mk(tmp_path)
    before = (tmp_path / "KXCPI.jsonl").read_text()
    st, lim = mk(tmp_path)
    assert st["already_done"] == 2 and st["fetched_markets"] == 0
    assert (tmp_path / "KXCPI.jsonl").read_text() == before
    # manifest lost -> refetch, but (market, candle) keys already present are not re-appended
    (tmp_path / "KXCPI.markets_done.jsonl").unlink()
    st, _ = mk(tmp_path)
    assert st["fetched_markets"] == 2 and st["rows_written"] == 0
    assert (tmp_path / "KXCPI.jsonl").read_text() == before


def test_dry_run_writes_nothing(tmp_path, capsys):
    st, _ = mk(tmp_path, dry_run=True)
    assert not list(tmp_path.iterdir()) and st["fetched_markets"] == 0
    assert capsys.readouterr().out.count("candlesticks") == 2


def test_failed_market_not_marked_done(tmp_path):
    def flaky(url):
        if "CPI-21JUN" in url and "/candlesticks" in url:
            raise urllib.error.URLError("boom")
        return fake(url)
    st, _ = mk(tmp_path, fetch=flaky)
    assert st["failed_markets"] == ["CPI-21JUN-T0.6:URLError"] and st["fetched_markets"] == 1
    done = [json.loads(x)["market_id"] for x in (tmp_path / "KXCPI.markets_done.jsonl").read_text().splitlines()]
    assert done == ["KXCPI-26AUG-T0.3"]


def test_limiter_spacing_and_429_backoff():
    t = [0.0]
    sleeps = []
    lim = bf.Limiter(2.5, clock=lambda: t[0], sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)))
    lim(lambda u: 1, "a")
    lim(lambda u: 1, "b")
    assert sleeps == [2.5]
    n = [0]

    def f(u):
        n[0] += 1
        if n[0] == 1:
            raise urllib.error.HTTPError(u, 429, "x", None, None)
        return "ok"
    assert lim(f, "c", backoff=30) == "ok" and 30.0 in sleeps


def test_max_markets_cap(tmp_path):
    st, _ = mk(tmp_path, max_markets=1)
    assert st["fetched_markets"] == 1
