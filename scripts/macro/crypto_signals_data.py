#!/usr/bin/env python3
"""M29 — off-VM crypto positioning/derivatives signal adapter (Bybit public API).

Short-horizon BTC/ETH/SOL signals off Bybit's already-wired **keyless public** v5
API — **funding-rate extremes**, **open interest**, and **perp-spot basis** — turned
into snapshot→percentile convictions emitted in the **valuation-snapshot schema**,
so the existing M28 P4 gate + horizon-IC scan grade them **UNCHANGED** (same as the
value + CFTC-COT sleeves).

**The three signals (all contrarian crowding gauges).** Perp funding, perp-spot
basis, and open interest are leverage/positioning gauges: an extreme reading means
one side is crowded, which historically mean-reverts on a short (days) horizon:

  - **funding_rate** — high positive funding = longs paying shorts = crowded long →
    over-owned → **rich** (short bias); deeply negative = crowded short → **cheap**.
  - **perp_basis** — perp premium over spot = leveraged long demand → **rich**;
    discount → **cheap**.
  - **open_interest** — high OI = crowded / fragile leverage → **rich** (the weakest
    directional claim of the three — primarily a fragility gauge).

So each emits ``cheap_score = 1 − percentile(value)`` with ``higher_is_cheaper =
False`` (higher raw reading = richer), the orientation-normalised convention
`valuation.value_read` uses — the conviction fires only at the extremes. **These
orientations are graded hypotheses, not claims** — the P4 + horizon-IC scans grade
whether (and in which direction) each predicts; a negative edge just means flip it.

**Point-in-time.** Crypto is real-time; a day-``D`` reading + a day-``D`` close are
both known at end of ``D``, so a row is stamped ``observed_at = "D"`` (bare date),
PIT-consistent with an entry at ``D``'s close. Each percentile uses only the trailing
``lookback`` window ending at its own day.

Off-VM-guarded (needs ``ICT_OFFVM_BUILD_HOST`` or an injected ``urlopen``) and fully
injectable for tests. No order path, no DB write.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os
import statistics
import sys
import urllib.parse
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_TRUTHY = {"1", "true", "yes", "on"}

BYBIT_BASE = "https://api.bybit.com"
# Bybit geo-blocks US IPs on api.bybit.com; api.bytick.com is Bybit's documented
# alternate domain (different CDN) that US hosts (e.g. GitHub-hosted runners) can
# reach. Try bytick FIRST so a US off-VM runner works, then the canonical host (for
# non-US hosts like the live VM). A fetcher uses the first base that returns data.
BYBIT_BASES = ("https://api.bytick.com", "https://api.bybit.com")


def _bases(base):
    """The base URLs to try, in order — an explicit ``base`` wins; else the
    bytick-first fallback list (so a US GitHub runner can reach Bybit)."""
    return [base] if base else list(BYBIT_BASES)
CRYPTO_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_LOOKBACK_DAYS = 90       # short-horizon: funding/basis mean-revert fast
_CHEAP_PCT = 0.70
_RICH_PCT = 0.30

# Per-metric orientation config (all contrarian-crowding → higher_is_cheaper=False).
CRYPTO_METRICS = (
    {"metric": "funding_rate",
     "note": "contrarian: high perp funding = crowded longs = rich (short bias); graded hypothesis"},
    {"metric": "perp_basis",
     "note": "contrarian: perp premium over spot = leveraged longs = rich; graded hypothesis"},
    {"metric": "open_interest",
     "note": "crowding/fragility gauge: high OI = leveraged = rich (weakest directional claim); graded hypothesis"},
)


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def _to_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ms_to_day(ms) -> Optional[str]:
    """Epoch-ms → ``YYYY-MM-DD`` (UTC). ``None`` on a bad value."""
    try:
        return _dt.datetime.fromtimestamp(int(float(ms)) / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# ---------------------------------------------------------------------------
# pure parsers (Bybit v5 result.list shapes)
# ---------------------------------------------------------------------------


def _result_list(payload) -> list:
    try:
        return (payload or {}).get("result", {}).get("list", []) or []
    except AttributeError:
        return []


def parse_funding_history(payload) -> list:
    """``[(ms, rate), ...]`` ascending from a v5 funding/history body
    (``{fundingRateTimestamp, fundingRate}``)."""
    out = []
    for r in _result_list(payload):
        if not isinstance(r, dict):
            continue
        ms = r.get("fundingRateTimestamp")
        rate = _to_float(r.get("fundingRate"))
        if ms is None or rate is None:
            continue
        try:
            out.append((int(float(ms)), rate))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def parse_open_interest(payload) -> list:
    """``[(ms, oi), ...]`` ascending from a v5 open-interest body
    (``{timestamp, openInterest}``)."""
    out = []
    for r in _result_list(payload):
        if not isinstance(r, dict):
            continue
        ms = r.get("timestamp")
        oi = _to_float(r.get("openInterest"))
        if ms is None or oi is None:
            continue
        try:
            out.append((int(float(ms)), oi))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def parse_kline_close(payload) -> list:
    """``[(ms, close), ...]`` ascending from a v5 kline body — each list item is
    ``[start, open, high, low, close, volume, turnover]``."""
    out = []
    for r in _result_list(payload):
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            continue
        ms = r[0]
        close = _to_float(r[4])
        if ms is None or close is None:
            continue
        try:
            out.append((int(float(ms)), close))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def resample_daily_last(pairs_ms) -> list:
    """Collapse ``[(ms, val), ...]`` to one value per UTC day (the last of the day),
    returned as ``[(YYYY-MM-DD, val), ...]`` ascending."""
    by_day: dict[str, float] = {}
    for ms, val in sorted(pairs_ms or [], key=lambda x: x[0]):
        day = _ms_to_day(ms)
        if day is not None:
            by_day[day] = float(val)   # later ms same day overwrites → last of day
    return sorted(by_day.items(), key=lambda x: x[0])


def compute_basis(perp_daily, spot_daily) -> list:
    """Perp-spot basis ``[(day, (perp−spot)/spot), ...]`` on the common days.
    Skips a day with a non-positive spot (division guard)."""
    spot = dict(spot_daily or [])
    out = []
    for day, p in perp_daily or []:
        s = spot.get(day)
        if s is None or s <= 0:
            continue
        out.append((day, (float(p) - float(s)) / float(s)))
    return sorted(out, key=lambda x: x[0])


# ---------------------------------------------------------------------------
# percentile → valuation-schema snapshot rows
# ---------------------------------------------------------------------------


def _percentile_rank(value: float, window) -> float:
    n = len(window)
    below = sum(1 for h in window if h < value)
    equal = sum(1 for h in window if h == value)
    return (below + 0.5 * equal) / n


def _label(cheap_score: float) -> str:
    if cheap_score >= _CHEAP_PCT:
        return "cheap"
    if cheap_score <= _RICH_PCT:
        return "rich"
    return "fair"


def build_percentile_snapshots(
    symbol: str,
    metric: str,
    dated_values,
    *,
    asset_class: str = "crypto",
    lookback: int = DEFAULT_LOOKBACK_DAYS,
    min_history: int = 30,
    higher_is_cheaper: bool = False,
    note: str = "",
    source: str = "bybit_backfill",
) -> list:
    """Emit point-in-time valuation-schema rows for one ``(symbol, metric)`` from a
    dated daily series ``[(day, value), ...]``. ``cheap_score`` re-orients the raw
    percentile per ``higher_is_cheaper`` (False → ``1 − percentile``: higher reading
    = richer). Leakage-safe: each row uses only the trailing window ending at its day."""
    series = sorted(dated_values or [], key=lambda x: x[0])
    out = []
    for i in range(len(series)):
        if i + 1 < min_history:
            continue
        window = [v for _d, v in series[max(0, i + 1 - lookback):i + 1]]
        day, cur = series[i][0], float(series[i][1])
        pct = _percentile_rank(cur, window)
        cheap_score = pct if higher_is_cheaper else (1.0 - pct)
        z = None
        if len(window) >= 2:
            try:
                sd = statistics.pstdev(window)
                if sd > 0:
                    z = (cur - statistics.fmean(window)) / sd
            except statistics.StatisticsError:
                z = None
        out.append({
            "symbol": symbol,
            "asset_class": asset_class,
            "metric": metric,
            "value": cur,
            "cheap_score": cheap_score,
            "label": _label(cheap_score),
            "z_score": z,
            "percentile": pct,
            "n_history": len(window),
            "higher_is_cheaper": higher_is_cheaper,
            "as_of": day,
            "observed_at": day,
            "source": source,
            "inputs": {"value": cur, "lookback_days": lookback},
            "note": note,
        })
    return out


def build_crypto_snapshots(
    symbol: str,
    *,
    funding_daily=None,
    basis_daily=None,
    oi_daily=None,
    lookback: int = DEFAULT_LOOKBACK_DAYS,
    min_history: int = 30,
    source: str = "bybit_backfill",
) -> list:
    """All three metric snapshots for one symbol (funding + basis + OI), concatenated."""
    series_by_metric = {
        "funding_rate": funding_daily,
        "perp_basis": basis_daily,
        "open_interest": oi_daily,
    }
    out = []
    for m in CRYPTO_METRICS:
        dated = series_by_metric.get(m["metric"])
        if not dated:
            continue
        out.extend(build_percentile_snapshots(
            symbol, m["metric"], dated, lookback=lookback, min_history=min_history,
            higher_is_cheaper=False, note=m["note"], source=source,
        ))
    return out


# ---------------------------------------------------------------------------
# off-VM Bybit fetch (guarded + injectable)
# ---------------------------------------------------------------------------


def _resolve_urlopen(urlopen):
    if urlopen is not None:
        return urlopen
    if not _offvm_enabled():
        raise RuntimeError(
            "crypto_signals_data: network fetch is off-VM only "
            "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
        )
    import urllib.request
    return urllib.request.urlopen


def _get_json(url, urlopen, timeout):
    try:
        with urlopen(url, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return None


def fetch_kline_close(
    symbol: str, *, category: str = "linear", interval: str = "D", limit: int = 1000,
    base: Optional[str] = None, urlopen=None, timeout: float = 30.0,
) -> list:
    """Daily closes ``[(ms, close), ...]`` for one symbol/category (perp=linear,
    spot=spot). Best-effort → ``[]``. One call (Bybit returns up to 1000 bars).
    Tries each base in :func:`_bases` until one returns data (US-block fallback)."""
    urlopen = _resolve_urlopen(urlopen)
    q = urllib.parse.urlencode({"category": category, "symbol": symbol, "interval": interval, "limit": int(limit)})
    for b in _bases(base):
        out = parse_kline_close(_get_json(f"{b}/v5/market/kline?{q}", urlopen, timeout))
        if out:
            return out
    return []


def _fetch_funding_one(symbol, base, urlopen, timeout, limit, max_pages) -> list:
    acc: list = []
    end_time = None
    for _ in range(max(1, max_pages)):
        params = {"category": "linear", "symbol": symbol, "limit": int(limit)}
        if end_time is not None:
            params["endTime"] = int(end_time)
        page = parse_funding_history(_get_json(f"{base}/v5/market/funding/history?{urllib.parse.urlencode(params)}", urlopen, timeout))
        if not page:
            break
        acc = page + acc
        oldest = page[0][0]
        if end_time is not None and oldest >= end_time:
            break            # no progress → stop (defensive)
        end_time = oldest - 1
    seen: dict[int, float] = {}
    for ms, v in acc:
        seen[ms] = v
    return sorted(seen.items(), key=lambda x: x[0])


def fetch_funding_history(
    symbol: str, *, base: Optional[str] = None, urlopen=None, timeout: float = 30.0,
    limit: int = 200, max_pages: int = 10,
) -> list:
    """Funding-rate history ``[(ms, rate), ...]`` ascending, paginated backward via
    ``endTime`` (up to ``max_pages`` × ``limit`` rows ≈ funding is 8-hourly). Tries
    each base in :func:`_bases` until one returns data (US-block fallback). Best-effort."""
    urlopen = _resolve_urlopen(urlopen)
    for b in _bases(base):
        out = _fetch_funding_one(symbol, b, urlopen, timeout, limit, max_pages)
        if out:
            return out
    return []


def _fetch_oi_one(symbol, interval_time, base, urlopen, timeout, limit, max_pages) -> list:
    acc: list = []
    cursor = None
    for _ in range(max(1, max_pages)):
        params = {"category": "linear", "symbol": symbol, "intervalTime": interval_time, "limit": int(limit)}
        if cursor:
            params["cursor"] = cursor
        payload = _get_json(f"{base}/v5/market/open-interest?{urllib.parse.urlencode(params)}", urlopen, timeout)
        page = parse_open_interest(payload)
        if not page:
            break
        acc = page + acc
        try:
            cursor = (payload or {}).get("result", {}).get("nextPageCursor")
        except AttributeError:
            cursor = None
        if not cursor:
            break
    seen: dict[int, float] = {}
    for ms, v in acc:
        seen[ms] = v
    return sorted(seen.items(), key=lambda x: x[0])


def fetch_open_interest(
    symbol: str, *, interval_time: str = "1d", base: Optional[str] = None, urlopen=None,
    timeout: float = 30.0, limit: int = 200, max_pages: int = 10,
) -> list:
    """Open-interest history ``[(ms, oi), ...]`` ascending, paginated via
    ``nextPageCursor``. Tries each base in :func:`_bases` until one returns data
    (US-block fallback). Best-effort."""
    urlopen = _resolve_urlopen(urlopen)
    for b in _bases(base):
        out = _fetch_oi_one(symbol, interval_time, b, urlopen, timeout, limit, max_pages)
        if out:
            return out
    return []
