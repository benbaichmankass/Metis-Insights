#!/usr/bin/env python3
"""Fetch BTCUSDT 5m candles from Bybit public REST API.

Writes to BACKTEST_DATA_PATH (default: data/backtest_candles.csv under repo
root). No authentication required — Bybit V5 public klines endpoint.

Usage
-----
    # Last 365 days (default — wide enough for random-window sampling):
    python scripts/ops/fetch_backtest_candles.py

    # Explicit date range:
    python scripts/ops/fetch_backtest_candles.py \\
        --start-date 2026-02-01 --end-date 2026-05-13

    # Override output path:
    BACKTEST_DATA_PATH=/tmp/fresh.csv python scripts/ops/fetch_backtest_candles.py

Environment
-----------
BACKTEST_DATA_PATH   Override output CSV path.
REPO_ROOT            Override repo root (default: two levels above this file).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"
MAX_BARS_PER_REQUEST = 1000
_RETRY_LIMIT = 4
_RETRY_BACKOFF = [2, 4, 8, 16]

# Binance's public data archive (S3/Cloudflare, keyless, globally reachable —
# NOT geoblocked). The fallback when Bybit's REST 403s the caller's IP: Bybit
# geoblocks US IP ranges, and GitHub-hosted runners are US-based Azure, so the
# `research-panel-build` GH-runner path can never reach api.bybit.com. Binance
# USDⓈ-M futures BTCUSDT is the closest analog to Bybit linear BTCUSDT (both
# USDT-margined perps; prices track within a few bps — fine as a discovery
# substrate). See BL-20260727-BYBIT-USGEOBLOCK-GHRUNNER.
_BINANCE_VISION_BASE = "https://data.binance.vision/data/futures/um"
# Bybit interval code (what this script + the workflow speak) -> Binance label.
_BYBIT_TO_BINANCE_INTERVAL = {
    "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
    "60": "1h", "120": "2h", "240": "4h", "360": "6h", "480": "8h",
    "720": "12h", "D": "1d", "W": "1w",
    # Both spellings resolve, because `_interval_ms` accepts both and a caller
    # that writes the explicit count should not silently lose the source.
    "1D": "1d", "1W": "1w",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _interval_ms(interval: str) -> int:
    """Convert a Bybit interval string to milliseconds.

    ⚠️ A BARE ``D``/``W`` carries an implicit count of 1, and dropping that
    crashed this function for the two codes the CLI help advertises: the old
    body did ``int(interval[:-1])`` unconditionally, so ``"D"`` became
    ``int("")`` -> ValueError.

    ⚠️ **State the scope precisely — this function is on the BYBIT path only**
    (``fetch_klines``); the archive path resolves a label from
    ``_BYBIT_TO_BINANCE_INTERVAL`` and never calls here. So daily was not
    globally broken. What was true is that **no single spelling worked on both
    sources**: bare ``D``/``W`` crashed Bybit but resolved on the archive, while
    ``1D``/``1W`` computed correctly here and had no archive label. Under
    ``--source auto`` a bare ``D`` therefore burned the Bybit arm on an
    exception, was caught, and fell through to Binance — a venue selected by a
    stack trace rather than by a decision. Both spellings now work on both.
    """
    code = interval.strip().upper()
    for suffix, unit_ms in (("D", 86_400_000), ("W", 7 * 86_400_000)):
        if code.endswith(suffix):
            count = code[:-1] or "1"   # bare "D" means one day, not zero
            return int(count) * unit_ms
    return int(code) * 60_000


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Page through Bybit klines and return rows sorted oldest-first."""
    rows: list[dict] = []
    cursor_ms = start_ms
    interval_ms = _interval_ms(interval)

    while cursor_ms < end_ms:
        # Pass only `start` (no `end`) so Bybit returns the next `limit` bars
        # going forward from cursor_ms.  Passing both start+end causes Bybit to
        # return the NEWEST limit bars in the range, breaking forward pagination.
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "start": cursor_ms,
            "limit": MAX_BARS_PER_REQUEST,
        }
        resp = None
        for attempt in range(_RETRY_LIMIT):
            try:
                resp = requests.get(BYBIT_KLINES_URL, params=params, timeout=20)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                if attempt < _RETRY_LIMIT - 1:
                    wait = _RETRY_BACKOFF[attempt]
                    print(
                        f"  fetch retry {attempt + 1}/{_RETRY_LIMIT - 1} after {wait}s: {exc}",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                else:
                    raise

        data = resp.json()
        if data.get("retCode") == 10006:
            # API rate limit ("Too many visits"). Transport-level retries
            # above don't cover this (HTTP 200 with an error retCode), and
            # raising here is what killed 8 of 14 datasets in the M15 WS-C
            # alt fetch (2026-06-11). Back off hard and retry the same page.
            print("  rate-limited (retCode 10006) — sleeping 30s", file=sys.stderr)
            time.sleep(30)
            continue
        if data.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit API error: {data.get('retMsg')} (retCode {data.get('retCode')})"
            )

        candles = data["result"]["list"]  # Bybit returns newest-first
        if not candles:
            break

        # candles[0] = newest bar in batch, candles[-1] = oldest
        newest_ms = int(candles[0][0])

        added = 0
        for c in reversed(candles):  # reverse to oldest-first
            ts_ms = int(c[0])
            if ts_ms < start_ms or ts_ms >= end_ms:
                continue
            rows.append({
                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
            added += 1

        if added == 0:
            break

        # Advance cursor past the newest bar returned in this batch.
        next_cursor = newest_ms + interval_ms
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor

        print(
            f"  fetched up to {datetime.fromtimestamp(newest_ms / 1000, tz=timezone.utc).date()}"
            f" ({len(rows)} bars so far)",
            file=sys.stderr,
        )
        # Pace sustained pagination to stay under Bybit's public rate limit
        # (long multi-symbol pulls tripped retCode 10006 without this).
        time.sleep(0.25)

    return rows


def _parse_vision_kline_row(fields: list[str]) -> dict | None:
    """Parse one Binance-vision klines CSV row into our OHLCV dict.

    Binance vision columns: open_time, open, high, low, close, volume,
    close_time, quote_volume, count, taker_buy_base, taker_buy_quote, ignore.
    `open_time` is epoch-ms historically, epoch-µs in files Binance re-cut in
    2025 — disambiguated by magnitude (a 2020-2026 ms stamp is ~1.6e12, the µs
    stamp ~1.6e15). Header rows (non-numeric first field) return None.
    """
    try:
        ts_raw = int(fields[0])
    except (ValueError, IndexError):
        return None  # header line or malformed
    ts_ms = ts_raw // 1000 if ts_raw >= 10**14 else ts_raw
    row = {
        "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
        "open": float(fields[1]),
        "high": float(fields[2]),
        "low": float(fields[3]),
        "close": float(fields[4]),
        "volume": float(fields[5]),
        "_ts_ms": ts_ms,
    }
    # taker_buy_base (field 9): the aggressive-buy share of the bar's volume — the
    # free order-flow-imbalance (OFI) proxy the M30 exit/regime studies use
    # (2*taker_buy_base/volume - 1). Present on Binance-vision, absent on Bybit
    # klines; kept when parseable so downstream feature builders can compute it.
    try:
        row["taker_buy_base"] = float(fields[9])
    except (ValueError, IndexError):
        pass
    return row


def _download_vision_zip(url: str) -> list[dict] | None:
    """Download + parse one Binance-vision klines .zip. None on 404 (absent)."""
    import csv
    import io
    import zipfile

    for attempt in range(_RETRY_LIMIT):
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code == 404:
                return None  # month/day not published (e.g. current month)
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt < _RETRY_LIMIT - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
            else:
                raise
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8")
            out = []
            for fields in csv.reader(text):
                row = _parse_vision_kline_row(fields)
                if row is not None:
                    out.append(row)
            return out


def _months_in_range(start_dt: datetime, end_dt: datetime) -> list[tuple[int, int]]:
    months = []
    y, m = start_dt.year, start_dt.month
    while (y, m) <= (end_dt.year, end_dt.month):
        months.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def fetch_klines_binance_vision(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Fetch klines from Binance's public data archive (globally reachable).

    Prefers monthly aggregate zips; falls back to per-day zips for any month
    the monthly file doesn't cover yet (the current, still-accumulating month).
    Returns rows oldest-first in the same shape as `fetch_klines`.
    """
    binterval = _BYBIT_TO_BINANCE_INTERVAL.get(interval)
    if binterval is None:
        raise RuntimeError(f"no Binance-vision interval mapping for {interval!r}")

    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    rows: list[dict] = []
    seen_ms: set[int] = set()

    def _absorb(batch: list[dict]) -> None:
        for r in batch:
            ms = r["_ts_ms"]
            if start_ms <= ms < end_ms and ms not in seen_ms:
                seen_ms.add(ms)
                rows.append(r)

    for year, month in _months_in_range(start_dt, end_dt):
        ym = f"{year:04d}-{month:02d}"
        monthly = (
            f"{_BINANCE_VISION_BASE}/monthly/klines/{symbol}/{binterval}/"
            f"{symbol}-{binterval}-{ym}.zip"
        )
        batch = _download_vision_zip(monthly)
        if batch is not None:
            _absorb(batch)
            print(f"  vision monthly {ym}: {len(batch)} bars ({len(rows)} kept)",
                  file=sys.stderr)
            continue
        # Monthly absent — pull that month's days individually.
        day = datetime(year, month, 1, tzinfo=timezone.utc)
        got_any = False
        while day.month == month and day <= end_dt:
            ds = day.strftime("%Y-%m-%d")
            daily = (
                f"{_BINANCE_VISION_BASE}/daily/klines/{symbol}/{binterval}/"
                f"{symbol}-{binterval}-{ds}.zip"
            )
            dbatch = _download_vision_zip(daily)
            if dbatch is not None:
                _absorb(dbatch)
                got_any = True
            day += timedelta(days=1)
        if got_any:
            print(f"  vision daily {ym}: {len(rows)} bars kept so far",
                  file=sys.stderr)

    rows.sort(key=lambda r: r["_ts_ms"])
    for r in rows:
        r.pop("_ts_ms", None)
    return rows


# Days shaved off a clamped intraday request. yfinance's own message says the
# range "must be within the last 730 days", and a request landing exactly on
# that boundary races the venue's own clock between our clamp and its check —
# so give it a little room rather than rediscovering the refusal at the edge.
_YF_CAP_MARGIN_DAYS = 2


class YfLaneError(RuntimeError):
    """Base for the yfinance lane's failures, carrying the STAGE that failed.

    The lane's first proof run reported ``yfinance fetch failed: No module
    named 'yaml'`` — a message naming a network fetch against Yahoo for an
    error raised at import time, before anything was requested. That is
    ``diagnostic-provenance-guard`` sub-class **A**: the label names a
    quantity the code never computed, and a reader who trusts it goes looking
    at Yahoo, at the ticker, at the span — anywhere but the dependency.

    The remedy the guard prescribes is to branch on the failure STAGE rather
    than to reword the label, so the three stages are three types:
    """


class YfDependencyMissing(YfLaneError):
    """A module needed to run the lane is absent. NOTHING was requested."""


class YfRefused(YfLaneError):
    """We read the request and declined it. NOTHING was requested.

    An interval yfinance does not serve, or a symbol with no ticker mapped —
    both are decisions about the ask, never observations about the venue.
    """


class YfFetchFailed(YfLaneError):
    """The request reached yfinance and the venue/network failed it.

    This is the ONLY stage for which "yfinance fetch failed" is a true
    sentence.
    """


_YF_STAGE_LABEL = {
    YfDependencyMissing: "dependency missing (nothing was fetched)",
    YfRefused: "request refused before any fetch",
    YfFetchFailed: "yfinance fetch failed",
}


def _load_yf_symbols():
    """Load the canonical ticker map WITHOUT executing any package ``__init__``.

    ⚠️ **Deliberately by file path, not ``import``.** A plain
    ``from ml.datasets.adapters.yf_symbols import ...`` executes
    ``ml/datasets/__init__.py`` -> ``.registry`` -> fourteen dataset-family
    builders, one of which imports ``yaml`` — so reading a dict of ticker
    strings would require the whole ML dependency set on a host that only
    needs pandas + yfinance. ``yf_symbols`` is import-free precisely so this
    load works; the by-path form is what makes that property pay.

    The map is NOT copied here. One home, reached cheaply.
    """
    import importlib.util

    path = _REPO_ROOT / "ml" / "datasets" / "adapters" / "yf_symbols.py"
    if not path.is_file():
        raise YfDependencyMissing(
            f"the canonical yfinance symbol map is missing at {path}; "
            f"it is the single home for the ticker map — a copy here would "
            f"be the fourth in this repo")
    spec = importlib.util.spec_from_file_location("_yf_symbols", path)
    if spec is None or spec.loader is None:
        raise YfDependencyMissing(f"cannot load a module spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:
        # `yf_symbols` promises to import nothing local. If that ever stops
        # being true this is where it surfaces, and it must not be reported
        # as a missing file or a fetch failure.
        raise YfDependencyMissing(
            f"{path.name} is no longer import-free ({exc}); it must import "
            f"nothing but `typing` or the by-path load cannot work") from exc
    return mod


# Bybit interval code -> the canonical timeframe token the yfinance adapter
# speaks. Only the bars yfinance can actually serve appear here; an interval
# absent from this map is REFUSED rather than silently coerced to a neighbour,
# because a 4h request quietly served as 1h is a wrong backtest that looks fine.
_BYBIT_TO_YF_TIMEFRAME = {
    "1": "1m", "5": "5m", "15": "15m", "60": "1h",
    "D": "1d", "1D": "1d",
}


# Bybit interval code -> the token `fetch_dukascopy_ohlcv` speaks. Only bars
# Dukascopy actually serves appear here. ⚠️ `120` (2h) is ABSENT and that is
# deliberate: the venue's own INTERVALS tuple has no 2h, and a 2h request
# quietly served as 1h or 4h is a wrong backtest that looks fine — the same
# reasoning the yfinance map states one screen up.
_BYBIT_TO_DK_TIMEFRAME = {
    "1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h",
    "D": "1d", "1D": "1d",
}


class DkLaneError(RuntimeError):
    """Base for the Dukascopy lane, carrying the STAGE that failed."""


class DkSymbolRefused(DkLaneError):
    """The adjudication DECIDED this symbol must not be proxied.

    Emphatically not "no data": somebody ruled on it, and the reason travels
    with the refusal so the caller prints the judgement rather than a shrug.
    """


class DkSymbolUnadjudicated(DkLaneError):
    """Nobody has ruled on this symbol yet — the remedy is to adjudicate it."""


class DkIntervalUnsupported(DkLaneError):
    """An interval the venue does not serve. REFUSED, never coerced."""


class DkDependencyMissing(DkLaneError):
    """`dukascopy-python` is not installed — the venue was never contacted."""


class DkFetchFailed(DkLaneError):
    """The request reached Dukascopy and the venue/network failed it.

    The ONLY stage for which "dukascopy fetch failed" is a true sentence.
    """


_DK_STAGE_LABEL = {
    DkSymbolRefused: "dukascopy lane refused the symbol",
    DkSymbolUnadjudicated: "dukascopy lane has no adjudicated mapping",
    DkIntervalUnsupported: "dukascopy lane refused the interval",
    DkDependencyMissing: "dukascopy dependency missing",
    DkFetchFailed: "dukascopy fetch failed",
}


def fetch_klines_dukascopy(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """The DEEP non-crypto lane: equities/ETFs/metals/index CFDs.

    WHY THIS EXISTS. `yfinance` was the only non-crypto lane and it is MEASURED
    to serve at most ~730 d of 1h history, REFUSING a longer request outright
    (proof run 32734360738). `e35-bracket-sweep.yml` asks for `days: 1830`, so
    yfinance cannot serve it at any request size. Dukascopy depth was then
    MEASURED (run 32788423940, 66 probes, zero errors): every mapped instrument
    carries bars at the 1830 d anchor — the 9 ETF CFDs to 2555 d, XAU and the
    S&P index CFD to 3650 d.

    ⚠️ THE SYMBOL MAP IS AN ADJUDICATION, IMPORTED NOT GUESSED. A raw substring
    match once resolved `MHG` (CME Micro Copper) to a Norwegian salmon farmer.
    `scripts/ops/dukascopy_instruments.py` is the one owner; a refusal there is
    a decision and is surfaced as one.

    ⚠️ SEVERAL SYMBOLS RESOLVE TO A PROXY — a DIFFERENT instrument. `MES` is a
    CME future served here by an index CFD; `SCHA` is served by `IWM`, which
    tracks a different index entirely. The relation is printed on every fetch so
    it cannot be mistaken for the real instrument.
    """
    tf = _BYBIT_TO_DK_TIMEFRAME.get(str(interval))
    if tf is None:
        raise DkIntervalUnsupported(
            f"dukascopy does not serve interval {interval!r}; "
            f"mapped: {sorted(_BYBIT_TO_DK_TIMEFRAME)}")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dukascopy_instruments as _dk  # noqa: E402  (path shimmed above)

    res = _dk.resolve(symbol)
    if res.state == _dk.STATE_REFUSED:
        raise DkSymbolRefused(res.reason or symbol)
    if res.state == _dk.STATE_UNKNOWN:
        raise DkSymbolUnadjudicated(res.reason or symbol)

    sys.stderr.write(
        f"  dukascopy: {symbol} -> {res.instrument} "
        f"(relation={res.relation}: {res.reason})\n")

    try:
        from fetch_dukascopy_ohlcv import fetch as _dk_fetch  # noqa: E402
    except Exception as exc:  # noqa: BLE001  # allow-silent: staged, re-raised
        raise DkDependencyMissing(
            f"could not load the dukascopy fetcher ({exc}); "
            f"`pip install dukascopy-python`") from exc

    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    try:
        df = _dk_fetch(res.instrument, start, end, tf)
    except SystemExit as exc:
        # fetch() raises SystemExit for a name absent from the instruments
        # module — that is a MAPPING bug in dukascopy_instruments.py, not a
        # venue failure, so it must not be reported as one.
        raise DkSymbolUnadjudicated(
            f"{res.instrument!r} is not in dukascopy_python.instruments ({exc})") from exc
    except Exception as exc:  # noqa: BLE001  # allow-silent: staged, re-raised
        raise DkFetchFailed(str(exc)) from exc

    rows: list[dict] = []
    if df is None or len(df) == 0:
        return rows
    for r in df.to_dict("records"):
        try:
            row = {
                "timestamp": pd.Timestamp(r["timestamp"]).tz_convert(None),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r.get("volume", 0.0) or 0.0),
            }
        except (KeyError, TypeError, ValueError):
            # A malformed bar is COUNTED by its absence, never zero-filled —
            # the same rule the yfinance lane states.
            continue
        if any(row[k] != row[k] for k in ("open", "high", "low", "close")):
            continue
        rows.append(row)
    return rows


def fetch_klines_yfinance(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Fetch candles from Yahoo, for the NON-CRYPTO legs no free lane serves.

    ``data.binance.vision`` is a crypto archive, so the 25 bracket-geometry
    cells blocked on `no_free_lane_candle_feed` — 5 IBKR futures, 18 US
    equities/ETFs, XAUUSD — can never be served by it. That is a source-coverage
    fact, not a backlog oversight.

    ⚠️ **The symbol map is NOT duplicated here.** It lives in
    ``ml.datasets.adapters.yfinance_offvm``, which already owned one, and this
    repo already carries two further copies (``scripts/research/regime_debt_matrix``
    and the dashboard's ``_yf_ticker``). A fourth is how they drift.

    ⚠️ **yfinance CAPS INTRADAY HISTORY** (~730 d at 1h, ~60 d at 15m; 1d is
    effectively uncapped). A caller asking for five years of 1h gets about two
    and would otherwise never know, so the truncation is reported on stderr
    against the span actually requested — silence there would make a partial
    span read as a complete one.
    """
    syms = _load_yf_symbols()

    tf = _BYBIT_TO_YF_TIMEFRAME.get(interval.strip().upper())
    if tf is None:
        raise YfRefused(
            f"yfinance cannot serve interval {interval!r}; "
            f"supported: {sorted(set(_BYBIT_TO_YF_TIMEFRAME))}")

    ticker = syms._DEFAULT_TICKER_MAP.get(symbol.upper())
    if ticker is None:
        raise YfRefused(
            f"no yfinance ticker mapped for {symbol!r}. Add it to "
            f"ml/datasets/adapters/yf_symbols._DEFAULT_TICKER_MAP — an "
            f"unmapped symbol is UNKNOWN, not 'probably fine as-is'.")

    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    cap = syms.max_history_days(tf)
    requested_days = (end_dt - start_dt).days
    if cap is not None and requested_days > cap:
        # ⚠️ THE VENUE REFUSES AN OVER-LONG INTRADAY REQUEST; IT DOES NOT CLIP IT.
        # This block used to warn "the span WILL be truncated" and pass the
        # request through unchanged. MEASURED 2026-08-24 (proof run
        # 32734360738, SPY 1h, 1001 d): Yahoo answered
        #   "1h data not available ... The requested range must be within the
        #    last 730 days"
        # and returned ZERO rows. So the old warning predicted an outcome the
        # code never verified, and the real outcome was strictly worse than the
        # one predicted — a caller who trusted it would read "partial window"
        # and get no window at all. Same family as the stage-label defect this
        # lane was already fixed for: do not assert what you did not check.
        #
        # So CLAMP the start to the cap and SAY SO, rather than warn and fail.
        # Clamping is the useful behaviour (the 1h legs want whatever history
        # exists, not a refusal) and stays honest because the obtained span is
        # reported against the requested one downstream — the caller is never
        # left believing it got the window it asked for.
        clamped_start = end_dt - timedelta(days=cap - _YF_CAP_MARGIN_DAYS)
        sys.stderr.write(
            f"  ⚠️ yfinance serves at most ~{cap} d of {tf} history and REFUSES "
            f"a longer request outright (it does not truncate). {requested_days} d "
            f"were requested, so the START was clamped to "
            f"{clamped_start.date()} — {(end_dt - clamped_start).days} d. "
            f"Treat the result as a PARTIAL window, not the requested one.\n")
        start_dt = clamped_start

    try:
        import yfinance as yf  # noqa: E402  (optional dep)
    except ImportError as exc:      # pragma: no cover - exercised in the proof lane
        raise YfDependencyMissing(
            f"the yfinance package is not installed ({exc}); "
            f"`pip install yfinance`") from exc

    try:
        frame = yf.download(
            tickers=ticker, interval=tf, start=start_dt.date(),
            end=end_dt.date(),
            auto_adjust=False, progress=False, threads=False,
        )
    except Exception as exc:        # pragma: no cover - network
        # ONLY from here on is "the fetch failed" a true sentence: the request
        # left the process. Everything above is a dependency or a refusal.
        raise YfFetchFailed(f"{ticker} {tf}: {exc}") from exc
    if frame is None or frame.empty:
        return []
    if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
        frame.columns = frame.columns.get_level_values(0)

    rows: list[dict] = []
    for ts, r in frame.iterrows():
        ts = ts.to_pydatetime()
        ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        try:
            row = {
                "timestamp": ts,
                "open": float(r["Open"]), "high": float(r["High"]),
                "low": float(r["Low"]), "close": float(r["Close"]),
                "volume": float(r.get("Volume", 0.0) or 0.0),
            }
        except (KeyError, TypeError, ValueError):
            # A malformed bar is COUNTED by its absence from `rows`, never
            # substituted with a zero — a fabricated OHLC bar is worse than a
            # short series, and the caller can see the count it got.
            continue
        if any(row[k] != row[k] for k in ("open", "high", "low", "close")):
            continue  # NaN row (yfinance pads holidays) — drop, do not zero-fill
        rows.append(row)
    return rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch 5m candles for backtest (Bybit primary, Binance-vision fallback)"
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--interval",
        default="5",
        help="Bybit kline interval: 1/3/5/15/30/60/120/240/D/W (default: 5)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Trailing calendar days to fetch (default 365). Overridden by --start-date.",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="Start date YYYY-MM-DD UTC (overrides --days)",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="End date YYYY-MM-DD UTC inclusive (default: today)",
    )
    parser.add_argument(
        "--output",
        default=os.environ.get(
            "BACKTEST_DATA_PATH",
            str(_REPO_ROOT / "data" / "backtest_candles.csv"),
        ),
        help="Output CSV path (default: data/backtest_candles.csv or BACKTEST_DATA_PATH)",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "bybit", "binance_vision", "yfinance", "dukascopy"],
        default=os.environ.get("BACKTEST_FEED_SOURCE", "auto"),
        help=(
            "Feed source. 'auto' (default): try Bybit, fall back to Binance's "
            "public data archive if Bybit fails/returns nothing (Bybit "
            "geoblocks US IPs, so a US GH-runner always 403s — the fallback is "
            "how the off-trainer research-panel-build works). 'bybit' or "
            "'binance_vision' force one source. 'yfinance' is the NON-CRYPTO "
            "lane (equities/ETFs/futures) that neither Bybit nor Binance can "
            "serve; it is never reached by 'auto', because a crypto symbol "
            "silently answered from Yahoo would be a different instrument. "
            "'dukascopy' is the DEEP non-crypto lane — yfinance serves at "
            "most ~730 d of 1h and REFUSES more, while Dukascopy was "
            "MEASURED (run 32788423940) to carry every mapped instrument "
            "past 1830 d. Like yfinance it is never reached by 'auto'."
        ),
    )
    args = parser.parse_args(argv[1:])

    now_utc = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    if args.end_date:
        end_dt = datetime.fromisoformat(args.end_date).replace(
            tzinfo=timezone.utc
        ) + timedelta(days=1)
    else:
        end_dt = now_utc + timedelta(days=1)

    if args.start_date:
        start_dt = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)
    else:
        start_dt = now_utc - timedelta(days=args.days)

    print(
        f"Fetching {args.symbol} {args.interval} candles "
        f"{start_dt.date()} -> {(end_dt - timedelta(days=1)).date()} "
        f"(source={args.source}) …",
        file=sys.stderr,
    )

    start_ms, end_ms = _ms(start_dt), _ms(end_dt)
    rows: list[dict] = []

    if args.source == "yfinance":
        # Deliberately NOT part of the `auto` chain: `auto` exists to survive a
        # Bybit geoblock on a US runner, and quietly answering a crypto symbol
        # from Yahoo would substitute a different instrument for the one asked
        # for. Choosing this lane is explicit.
        try:
            rows = fetch_klines_yfinance(
                args.symbol, args.interval, start_ms, end_ms)
        except YfLaneError as exc:
            # Report the STAGE that failed, not a single label over all three.
            # The lane's first proof run printed "yfinance fetch failed: No
            # module named 'yaml'" for an error raised at import time — a
            # message blaming a venue that was never contacted.
            stage = _YF_STAGE_LABEL.get(type(exc), "yfinance lane failed")
            sys.stderr.write(f"{stage}: {exc}\n")
            return 1
        except Exception as exc:
            # An UNCLASSIFIED failure. Deliberately not folded into any stage
            # above: naming a stage we did not establish is the same defect
            # one level down.
            sys.stderr.write(
                f"yfinance lane failed at an unclassified stage "
                f"({type(exc).__name__}): {exc}\n")
            return 1

    if args.source == "dukascopy":
        # Explicit, never in the `auto` chain — same reasoning as yfinance: a
        # crypto symbol quietly answered from a CFD venue would be a different
        # instrument than the one the strategy trades.
        try:
            rows = fetch_klines_dukascopy(
                args.symbol, args.interval, start_ms, end_ms)
        except DkLaneError as exc:
            # Report the STAGE. "dukascopy fetch failed" for a symbol the
            # adjudication REFUSED would blame a venue that was never asked.
            stage = _DK_STAGE_LABEL.get(type(exc), "dukascopy lane failed")
            sys.stderr.write(f"{stage}: {exc}\n")
            return 1
        except Exception as exc:  # noqa: BLE001  # allow-silent: reported, exits non-zero
            sys.stderr.write(
                f"dukascopy lane failed at an unclassified stage "
                f"({type(exc).__name__}): {exc}\n")
            return 1

    if args.source in ("auto", "bybit"):
        try:
            rows = fetch_klines(args.symbol, args.interval, start_ms, end_ms)
        except Exception as exc:
            if args.source == "bybit":
                sys.stderr.write(f"fetch failed: {exc}\n")
                return 1
            sys.stderr.write(
                f"Bybit fetch failed ({exc}); falling back to Binance-vision …\n"
            )
        else:
            if not rows and args.source == "bybit":
                sys.stderr.write("ERROR: no candles returned from Bybit.\n")
                return 1
            if not rows:
                sys.stderr.write(
                    "Bybit returned no candles; falling back to Binance-vision …\n"
                )

    if not rows and args.source in ("auto", "binance_vision"):
        try:
            rows = fetch_klines_binance_vision(
                args.symbol, args.interval, start_ms, end_ms
            )
        except Exception as exc:
            sys.stderr.write(f"Binance-vision fetch failed: {exc}\n")
            return 1

    if not rows:
        # Name the source(s) actually ATTEMPTED. "from any source" claimed a
        # breadth of search the run did not perform: an explicit --source
        # yfinance run tries exactly one, and reporting it as if every source
        # had been exhausted sends the reader looking in the wrong places.
        tried = ("bybit, then binance_vision" if args.source == "auto"
                 else args.source)
        sys.stderr.write(
            f"ERROR: no candles returned (source attempted: {tried}).\n")
        return 1

    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(
        f"Wrote {len(df)} rows "
        f"({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}) "
        f"to {output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
