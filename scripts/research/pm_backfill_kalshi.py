#!/usr/bin/env python3
# wiring: manual-only - one-shot backfill, run once by hand via trainer-vm-diag; deliberately no timer or service (operator/manager brief PM-BACKFILL-BUILD).
"""PM-BACKFILL — one-shot, point-in-time Kalshi history for settled release markets.

Feeds RQ-20261009-903 (PM-SURPRISE-EVAL). For each settled market in the series listed in
config/pm_markets.yaml ``kalshi.series`` (release series only, see DEFAULT_SERIES) it fetches HOURLY bid/ask candlesticks over the window
[close - window_hours_before_close, close + 1h] and APPENDS one JSON line per candle to
<data_dir>/pm_backfill/kalshi/<series>.jsonl (off-repo; third-party terms unread, operator 2026-10-08).

Point-in-time: every row carries the candle's own period end (``candle_end_utc``) and the market's
``close_time``; a consumer MUST use only rows with ``candle_end_utc`` <= the release instant, so no
lookahead is possible from the data itself. ``fetched_at_utc`` is provenance only, never a signal time.
Prices are yes bid/ask OHLC at the candle (``None`` where Kalshi reports none).

Append-only and resumable: a per-series manifest ``<series>.markets_done.jsonl`` records each market once
fully fetched; a re-run skips those and also skips any (market_id, candle_end_utc) already present.
Rate limited (>= 2.5 s between calls, 429 backs off). Trainer/off-VM only (ICT_OFFVM_BUILD_HOST).
No timer, no service, no DB, no order path. Evaluation is NOT here.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macro"))
import pm_snapshot_collect as pmc  # noqa: E402  reuse: http_get_json, content_hash, _f, CONFIG

REPO = pmc.REPO
data_dir = pmc.data_dir
Fetch = Callable[[str], Any]
DEFAULT_SERIES = ["KXCPI", "KXPAYROLLS", "KXFEDDECISION"]
DEFAULTS = {"window_hours_before_close": 168, "period_minutes": 60, "min_seconds_between_calls": 2.5}


class Limiter:
    """Enforce >= ``gap`` seconds between calls (clock/sleep injectable for tests)."""

    def __init__(self, gap: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.gap, self._clock, self._sleep, self._last = gap, clock, sleep, None
        self.calls = 0

    def __call__(self, fetch: Fetch, url: str, backoff: float = 30.0, tries: int = 6) -> Any:
        for i in range(tries):
            if self._last is not None:
                wait = self.gap - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            self.calls += 1
            try:
                return fetch(url)
            except urllib.error.HTTPError as e:
                if e.code == 429 and i < tries - 1:
                    self._sleep(backoff * (2 ** i))
                    continue
                raise
        raise RuntimeError("unreachable")


def _iso(ts: int) -> str:
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(iso: str) -> int:
    return int(_dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def list_markets(base: str, series: str, call: Callable[[str], Any]) -> List[Dict[str, Any]]:
    """Settled markets for a series from the live and historical (pre-cutoff) endpoints, deduped by ticker.

    Each market gets ``_endpoint`` ('live'|'historical') naming which candlestick route serves it.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for endpoint, path, extra in (("live", "/markets", {"status": "settled"}), ("historical", "/historical/markets", {})):
        cursor = ""
        while True:
            q = {"series_ticker": series, "limit": 1000, **extra}
            if cursor:
                q["cursor"] = cursor
            page = call(f"{base}{path}?" + urllib.parse.urlencode(q))
            for m in page.get("markets") or []:
                t = m.get("ticker")
                if t and m.get("close_time") and t not in out:
                    out[t] = {**m, "_endpoint": endpoint}
            cursor = page.get("cursor") or ""
            if not cursor or not page.get("markets"):
                break
    return sorted(out.values(), key=lambda m: (m["close_time"], m["ticker"]))


def candle_url(base: str, series: str, m: Dict[str, Any], hours_before: int, period_min: int) -> str:
    close = _epoch(m["close_time"])
    q = urllib.parse.urlencode({"start_ts": close - hours_before * 3600, "end_ts": close + 3600,
                                "period_interval": period_min})
    if m["_endpoint"] == "historical":
        return f"{base}/historical/markets/{m['ticker']}/candlesticks?{q}"
    return f"{base}/series/{series}/markets/{m['ticker']}/candlesticks?{q}"


def _ohlc(d: Optional[Dict[str, Any]], pfx: str) -> Dict[str, Optional[float]]:
    d = d or {}
    return {f"{pfx}_{k}": pmc._f(d.get(f"{k}_dollars")) for k in ("open", "high", "low", "close")}


def candle_rows(series: str, topic: str, m: Dict[str, Any], candles: List[Dict[str, Any]], fetched_at: str) -> List[Dict[str, Any]]:
    rows = []
    for c in sorted(candles, key=lambda c: c.get("end_period_ts") or 0):
        if not c.get("end_period_ts"):
            continue
        rows.append(pmc._finish({
            "source": "kalshi_backfill", "endpoint": m["_endpoint"], "topic": topic, "series": series,
            "market_id": m["ticker"], "event_ticker": m.get("event_ticker"), "title": m.get("title"),
            "strike_type": m.get("strike_type"), "floor_strike": pmc._f(m.get("floor_strike")),
            "cap_strike": pmc._f(m.get("cap_strike")), "close_time": m.get("close_time"),
            "result": m.get("result"), "expiration_value": m.get("expiration_value"),
            "candle_end_utc": _iso(c["end_period_ts"]),
            **_ohlc(c.get("yes_bid"), "yes_bid"), **_ohlc(c.get("yes_ask"), "yes_ask"),
            "last_price_prev": pmc._f((c.get("price") or {}).get("previous_dollars")),
            "volume": pmc._f(c.get("volume_fp")), "open_interest": pmc._f(c.get("open_interest_fp")),
            "fetched_at_utc": fetched_at,
        }))
    return rows


def _read_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def _append(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def backfill_series(cfg: Dict[str, Any], series: str, fetch: Fetch, out_dir: Path, limiter: Limiter,
                    dry_run: bool = False, max_markets: Optional[int] = None, now: Optional[str] = None) -> Dict[str, Any]:
    base, bf = cfg["kalshi"]["base_url"], {**DEFAULTS, **cfg.get("backfill", {})}
    hours, period = int(bf.get("window_hours_before_close", 168)), int(bf.get("period_minutes", 60))
    topic = cfg["kalshi"]["series"].get(series, series)
    now = now or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data, manifest = out_dir / f"{series}.jsonl", out_dir / f"{series}.markets_done.jsonl"
    call = lambda u: limiter(fetch, u)  # noqa: E731
    markets = list_markets(base, series, call)
    done: Set[str] = {r["market_id"] for r in _read_jsonl(manifest)}
    seen: Set[Tuple[str, str]] = {(r["market_id"], r["candle_end_utc"]) for r in _read_jsonl(data)}
    todo = [m for m in markets if m["ticker"] not in done]
    if max_markets is not None:
        todo = todo[:max_markets]
    stat = {"series": series, "settled_markets": len(markets), "already_done": len(markets) - len([m for m in markets if m["ticker"] not in done]),
            "fetched_markets": 0, "rows_written": 0, "empty_markets": 0, "failed_markets": []}
    for m in todo:
        url = candle_url(base, series, m, hours, period)
        if dry_run:
            print(url)
            continue
        try:
            resp = call(url)
        except (OSError, ValueError) as e:  # not marked done -> retried next run; never silent
            stat["failed_markets"].append(f"{m['ticker']}:{type(e).__name__}")
            print(f"::warning::{m['ticker']} failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        rows = [r for r in candle_rows(series, topic, m, resp.get("candlesticks") or [], now)
                if (r["market_id"], r["candle_end_utc"]) not in seen]
        _append(data, rows)
        _append(manifest, [{"market_id": m["ticker"], "event_ticker": m.get("event_ticker"), "close_time": m["close_time"],
                            "candles": len(rows), "fetched_at_utc": now}])
        seen.update((r["market_id"], r["candle_end_utc"]) for r in rows)
        stat["fetched_markets"] += 1
        stat["rows_written"] += len(rows)
        stat["empty_markets"] += 0 if rows else 1
    return stat


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=pmc.CONFIG)
    ap.add_argument("--out-dir", type=Path, default=None, help="default: <data_dir>/pm_backfill/kalshi")
    ap.add_argument("--series", nargs="*", default=None, help="default: DEFAULT_SERIES")
    ap.add_argument("--max-markets", type=int, default=None, help="cap markets per series (smoke test)")
    ap.add_argument("--dry-run", action="store_true", help="list markets, print candle URLs; write nothing")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(a.config.read_text())
    out_dir = a.out_dir or (data_dir() / "pm_backfill" / "kalshi")
    gap = float({**DEFAULTS, **cfg.get("backfill", {})}["min_seconds_between_calls"])
    limiter = Limiter(max(gap, 2.5))
    rc = 0
    for s in a.series or cfg.get("backfill", {}).get("series") or DEFAULT_SERIES:
        st = backfill_series(cfg, s, pmc.http_get_json, out_dir, limiter, a.dry_run, a.max_markets)
        print(json.dumps(st, sort_keys=True))
        rc = rc or (1 if st["failed_markets"] else 0)
    print(json.dumps({"api_calls": limiter.calls, "dry_run": a.dry_run}))
    return rc


if __name__ == "__main__":
    sys.exit(main())
