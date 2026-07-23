#!/usr/bin/env python3
"""M29 — off-VM CFTC Commitments-of-Traders (COT) positioning adapter.

A **positioning** sleeve for the macro-research family: weekly large-speculator
net positioning from the CFTC's free, keyless **Legacy Futures-Only** COT report
(Socrata open-data JSON), turned into a snapshot→percentile conviction and emitted
in the **valuation-snapshot schema** — so the existing M28 P4 value gate
(`thesis_backtest_run.py`) and the horizon-IC scan (`horizon_ic_scan.py`) grade it
**unchanged** (they grade any row whose `cheap_score` drives `value_conviction` /
`value_to_direction`).

**The signal (contrarian large-spec COT index).** For each market, the weekly
large-speculator ("Noncommercial") net position = long − short. Its position within
its own trailing `lookback`-week range is the classic **COT index** percentile. The
orientation emitted here is **contrarian on the large specs** — the crowd:

  - specs crowded net-long (high percentile) → over-owned → **rich** → short bias,
  - specs washed out / net-short extreme (low percentile) → **cheap** → long bias.

So ``cheap_score = 1 − percentile(spec_net)`` and ``higher_is_cheaper = False`` (a
higher raw net-long is richer), exactly the orientation-normalised convention
`valuation.value_read` uses. The conviction the sleeve emits fires only at the
extremes (``|cheap_score − 0.5|×2``). **This orientation is a documented research
hypothesis, not a claim** — the P4 + horizon-IC scans grade whether it predicts;
a strongly *negative* edge simply means the momentum/commercial orientation is the
right one (flip ``cheap_score``). That is exactly what these scans are for.

**Point-in-time discipline.** A COT report snapshots Tuesday positions and is
**released the following Friday** (~3-day lag). Every row is stamped
``observed_at`` = report date + ``release_lag_days`` (default 3) so a backtest can
never see positioning before it was public (the report's Tuesday date rides in
``inputs.report_date``). Each row's percentile uses ONLY the trailing window ending
at its own report date — leakage-safe by construction.

Off-VM-guarded (refuses on the live VM unless ``ICT_OFFVM_BUILD_HOST`` is set) and
fully ``urlopen``-injectable for tests. No order path, no DB write.
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

# CFTC Legacy Futures-Only COT — Socrata open-data resource (keyless JSON). One
# uniform schema across commodities AND financials (Noncommercial = large specs,
# Commercial = hedgers). Overridable via --socrata-base so a resource-id change is
# a one-flag fix, not a code edit.
COT_SOCRATA_BASE = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Socrata legacy futures-only field names.
_F_DATE = "report_date_as_yyyy_mm_dd"
_F_NAME = "market_and_exchange_names"
_F_CODE = "cftc_contract_market_code"
_F_OI = "open_interest_all"
_F_SPEC_LONG = "noncomm_positions_long_all"
_F_SPEC_SHORT = "noncomm_positions_short_all"
_F_COMM_LONG = "comm_positions_long_all"
_F_COMM_SHORT = "comm_positions_short_all"

# The positioning universe: (key, uppercase name substring, tradeable proxy symbol,
# asset_class). The symbol is a liquid ETF/proxy the candle fetcher can price so the
# P4/horizon scans have a forward-return series; the COT positioning is on the
# underlying future (documented proxy). CFTC market names are uppercase.
COT_MARKETS = (
    {"key": "crude", "name": "CRUDE OIL, LIGHT SWEET-WTI", "symbol": "USO", "asset_class": "commodity"},
    {"key": "natgas", "name": "NATURAL GAS - NEW YORK MERCANTILE", "symbol": "UNG", "asset_class": "commodity"},
    {"key": "gold", "name": "GOLD - COMMODITY EXCHANGE", "symbol": "GLD", "asset_class": "commodity"},
    {"key": "copper", "name": "COPPER-", "symbol": "CPER", "asset_class": "commodity"},
    {"key": "es", "name": "E-MINI S&P 500", "symbol": "SPY", "asset_class": "index"},
    {"key": "eur", "name": "EURO FX", "symbol": "FXE", "asset_class": "fx"},
    {"key": "ust10", "name": "10-YEAR U.S. TREASURY NOTES", "symbol": "IEF", "asset_class": "bond"},
)

DEFAULT_LOOKBACK_WEEKS = 156     # ~3y — the classic COT-index window
DEFAULT_RELEASE_LAG_DAYS = 3     # Tuesday report → Friday public release
_CHEAP_PCT = 0.70                # mirror valuation.value_read cheap/rich cut points
_RICH_PCT = 0.30


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def _norm_day(s) -> str:
    return str(s).strip()[:10]


def _to_float(x) -> Optional[float]:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# pure parsing
# ---------------------------------------------------------------------------


def parse_cot_rows(payload) -> list[dict]:
    """Parse a Socrata legacy-COT JSON array → per-week rows sorted ascending by
    date: ``[{date, code, name, spec_long, spec_short, comm_long, comm_short,
    open_interest}, ...]``. Values are coerced from Socrata's string numbers; a row
    missing the date or both spec legs is skipped (honest, never raises)."""
    out: list[dict] = []
    for r in payload or []:
        if not isinstance(r, dict):
            continue
        date = _norm_day(r.get(_F_DATE, "")) if r.get(_F_DATE) is not None else ""
        if len(date) != 10:
            continue
        sl = _to_float(r.get(_F_SPEC_LONG))
        ss = _to_float(r.get(_F_SPEC_SHORT))
        if sl is None and ss is None:
            continue
        out.append({
            "date": date,
            "code": str(r.get(_F_CODE, "") or ""),
            "name": str(r.get(_F_NAME, "") or ""),
            "spec_long": sl or 0.0,
            "spec_short": ss or 0.0,
            "comm_long": _to_float(r.get(_F_COMM_LONG)) or 0.0,
            "comm_short": _to_float(r.get(_F_COMM_SHORT)) or 0.0,
            "open_interest": _to_float(r.get(_F_OI)),
        })
    out.sort(key=lambda x: x["date"])
    return out


def dominant_market_rows(rows: list[dict]) -> list[dict]:
    """Keep only rows of the modal ``cftc_contract_market_code`` — so a loose name
    substring that matched more than one related contract collapses to the single
    dominant market (the one with the most weekly observations). Empty in → empty out."""
    if not rows:
        return []
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["code"]] = counts.get(r["code"], 0) + 1
    top = max(counts, key=lambda c: counts[c])
    return [r for r in rows if r["code"] == top]


def spec_net_series(rows: list[dict]) -> list[tuple]:
    """``[(date, spec_net), ...]`` where spec_net = large-spec long − short.
    De-duplicates to one row per date (the last seen), ascending."""
    by_date: dict[str, float] = {}
    for r in rows:
        by_date[r["date"]] = float(r["spec_long"]) - float(r["spec_short"])
    return sorted(by_date.items(), key=lambda x: x[0])


def comm_net_series(rows: list[dict]) -> list[tuple]:
    """``[(date, comm_net), ...]`` where comm_net = COMMERCIAL (hedger) long − short —
    the other side of the COT report. The classic COT edge is the spec-vs-commercial
    *divergence*, so this feeds the D1 divergence construction. Same de-dup as
    `spec_net_series`. Rows missing the commercial legs contribute 0 (parse default)."""
    by_date: dict[str, float] = {}
    for r in rows:
        by_date[r["date"]] = float(r.get("comm_long", 0.0)) - float(r.get("comm_short", 0.0))
    return sorted(by_date.items(), key=lambda x: x[0])


# ---------------------------------------------------------------------------
# COT index (rolling percentile) → snapshot rows in the valuation schema
# ---------------------------------------------------------------------------


def _percentile_rank(value: float, window) -> float:
    """Fraction of ``window`` <= ``value`` (midrank for ties), in [0,1]."""
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


def build_cot_snapshots(
    market: dict,
    rows: list[dict],
    *,
    lookback: int = DEFAULT_LOOKBACK_WEEKS,
    release_lag_days: int = DEFAULT_RELEASE_LAG_DAYS,
    min_history: int = 52,
    source: str = "cftc_cot_backfill",
) -> list[dict]:
    """Emit point-in-time valuation-schema snapshot rows for one market.

    For each week with at least ``min_history`` trailing observations, the row's
    ``percentile`` is the raw-axis rank of that week's spec_net within the trailing
    ``lookback`` window, and ``cheap_score = 1 − percentile`` (contrarian large-spec:
    crowded long = rich). ``observed_at`` = report date + ``release_lag_days`` so the
    row is only visible once the report was public. Leakage-safe: each row uses only
    the window ending at its own report date."""
    rows = dominant_market_rows(rows)
    net = spec_net_series(rows)
    # spec_net_series de-dupes dates; index the full row detail by date for `inputs`.
    detail = {r["date"]: r for r in rows}
    out: list[dict] = []
    for i in range(len(net)):
        if i + 1 < min_history:
            continue
        window_vals = [v for _d, v in net[max(0, i + 1 - lookback):i + 1]]
        cur = net[i][1]
        date = net[i][0]
        pct = _percentile_rank(cur, window_vals)
        cheap_score = 1.0 - pct
        z = None
        if len(window_vals) >= 2:
            try:
                sd = statistics.pstdev(window_vals)
                if sd > 0:
                    z = (cur - statistics.fmean(window_vals)) / sd
            except statistics.StatisticsError:
                z = None
        d = detail.get(date, {})
        observed_at = _release_stamp(date, release_lag_days)
        out.append({
            "symbol": market["symbol"],
            "asset_class": market["asset_class"],
            "metric": "cot_spec_positioning",
            "value": round(cur, 2),
            "cheap_score": cheap_score,
            "label": _label(cheap_score),
            "z_score": z,
            "percentile": pct,
            "n_history": len(window_vals),
            "higher_is_cheaper": False,
            "as_of": observed_at,
            "observed_at": observed_at,
            "source": source,
            "inputs": {
                "market": d.get("name") or market["name"],
                "cftc_code": d.get("code"),
                "spec_long": d.get("spec_long"),
                "spec_short": d.get("spec_short"),
                "spec_net": round(cur, 2),
                "comm_net": (float(d.get("comm_long", 0.0)) - float(d.get("comm_short", 0.0)))
                if d else None,
                "open_interest": d.get("open_interest"),
                "lookback_weeks": lookback,
                "report_date": date,
            },
            "note": "contrarian large-spec COT index (cheap_score=1-percentile(spec_net); "
                    "crowded net-long=rich); orientation is a graded hypothesis",
        })
    return out


def _release_stamp(report_date: str, lag_days: int) -> str:
    """``report_date`` (YYYY-MM-DD Tuesday) + ``lag_days`` → an ISO-8601 ``…Z`` stamp
    at the public-release instant (Friday). PIT: the data isn't knowable before this."""
    try:
        rel = _dt.date.fromisoformat(_norm_day(report_date)) + _dt.timedelta(days=max(0, int(lag_days)))
        return rel.strftime("%Y-%m-%dT00:00:00Z")
    except (ValueError, TypeError):
        return f"{_norm_day(report_date)}T00:00:00Z"


# ---------------------------------------------------------------------------
# off-VM Socrata fetch (guarded + injectable)
# ---------------------------------------------------------------------------


def build_cot_query_url(name_substring: str, *, base: str = COT_SOCRATA_BASE, limit: int = 5000) -> str:
    """SoQL query URL for one market's full weekly history — selected fields,
    case-insensitive name-substring filter, ascending by date, capped at ``limit``."""
    params = {
        "$select": ",".join([_F_DATE, _F_NAME, _F_CODE, _F_OI, _F_SPEC_LONG,
                             _F_SPEC_SHORT, _F_COMM_LONG, _F_COMM_SHORT]),
        "$where": f"upper({_F_NAME}) like '%{str(name_substring).upper()}%'",
        "$order": f"{_F_DATE} ASC",
        "$limit": int(limit),
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


def fetch_cot_market_history(
    name_substring: str, *, base: str = COT_SOCRATA_BASE, limit: int = 5000,
    urlopen=None, timeout: float = 30.0,
) -> list[dict]:
    """Parsed weekly COT rows for one market. Off-VM-guarded + ``urlopen``-injectable.
    Best-effort: any fetch/parse failure → ``[]`` (never fatal)."""
    if urlopen is None:
        if not _offvm_enabled():
            raise RuntimeError(
                "fetch_cot_market_history: network fetch is off-VM only "
                "(set ICT_OFFVM_BUILD_HOST=1) or inject urlopen"
            )
        import urllib.request
        urlopen = urllib.request.urlopen
    url = build_cot_query_url(name_substring, base=base, limit=limit)
    try:
        with urlopen(url, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return []
    return parse_cot_rows(payload)
