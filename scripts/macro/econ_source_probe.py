#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — free economic-calendar SOURCE PROBE (validate before we build).

FMP's free tier 403s the economic-calendar endpoint (it's premium), so before
building another adapter we EMPIRICALLY test every credible free source on a
GitHub runner (open egress; the sandbox can't reach these hosts) and report which
actually deliver **US events with a consensus AND an actual** — the two fields the
surprise spine needs. Runs in CI; prints a structured verdict to stdout +
``$GITHUB_STEP_SUMMARY``. No production wiring — pure diagnostics.

Each candidate reports: reachable?, HTTP status, total rows, US rows, whether a
consensus/forecast field and an actual field are present, and 2 sample US rows —
so the pick is made on real data, not documentation claims.

Candidates (keyless or trivially-keyed, likely-real US coverage):
  1. ForexFactory via faireconomy — the classic keyless MT4/MT5 weekly JSON feed.
  2. FXStreet calendar-api — the actual upstream Bigdata/FXStreet widget endpoint.
  3. Trading Economics guest:guest — free but historically a thin country sample.
  4. EODHD demo token — economic-events endpoint on the demo key.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_TIMEOUT = 30.0


def _get(url: str, *, headers: Optional[dict] = None) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Return (http_status, body_text, error). Best-effort — never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:  # noqa: PERF203
        return e.code, None, f"HTTPError {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, None, f"{type(e).__name__}: {e}"


def _verdict(
    name: str, url: str, *, rows: list, is_us: Callable[[dict], bool],
    consensus_keys: list[str], actual_keys: list[str], event_key: str,
    status: Optional[int], error: Optional[str],
) -> dict:
    us = [r for r in rows if isinstance(r, dict) and is_us(r)]

    def _present(row: dict, keys: list[str]) -> bool:
        return any(row.get(k) not in (None, "", "-") for k in keys)

    us_with_consensus = sum(1 for r in us if _present(r, consensus_keys))
    us_with_actual = sum(1 for r in us if _present(r, actual_keys))
    samples = []
    for r in us[:3]:
        samples.append({
            "event": r.get(event_key),
            "consensus": next((r.get(k) for k in consensus_keys if r.get(k) not in (None, "", "-")), None),
            "actual": next((r.get(k) for k in actual_keys if r.get(k) not in (None, "", "-")), None),
        })
    # Full raw field set of the first 2 US rows — so an adapter is built against
    # the REAL schema, not guessed field names.
    raw_samples = [r for r in us[:2]]
    ok = status == 200 and len(us) > 0 and us_with_consensus > 0
    return {
        "source": name, "url": url, "http_status": status, "error": error,
        "total_rows": len(rows), "us_rows": len(us),
        "us_with_consensus": us_with_consensus, "us_with_actual": us_with_actual,
        "usable": ok, "samples": samples, "raw_samples": raw_samples,
    }


def probe_faireconomy() -> list[dict]:
    """ForexFactory weekly JSON (keyless). thisweek + lastweek span ~2 weeks."""
    out = []
    for wk in ("thisweek", "lastweek", "nextweek"):
        url = f"https://nfs.faireconomy.media/ff_calendar_{wk}.json"
        status, body, err = _get(url)
        rows = []
        if body:
            try:
                rows = json.loads(body)
            except ValueError as e:
                err = f"json: {e}"
        out.append(_verdict(
            f"faireconomy:{wk}", url, rows=rows if isinstance(rows, list) else [],
            is_us=lambda r: str(r.get("country", "")).upper() in {"USD", "US", "UNITED STATES"},
            consensus_keys=["forecast"], actual_keys=["actual"], event_key="title",
            status=status, error=err,
        ))
    return out


def probe_fxstreet() -> list[dict]:
    """FXStreet calendar-api (the actual upstream widget endpoint)."""
    today = _dt.date.today()
    frm = (today - _dt.timedelta(days=10)).strftime("%Y-%m-%dT00:00:00Z")
    to = (today + _dt.timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    url = f"https://calendar-api.fxstreet.com/en/api/v1/eventDates/{frm}/{to}"
    status, body, err = _get(url, headers={"Origin": "https://www.fxstreet.com", "Referer": "https://www.fxstreet.com/"})
    rows = []
    if body:
        try:
            rows = json.loads(body)
        except ValueError as e:
            err = f"json: {e}"
    return [_verdict(
        "fxstreet:calendar-api", url, rows=rows if isinstance(rows, list) else [],
        is_us=lambda r: str(r.get("countryCode", "")).upper() == "US",
        consensus_keys=["consensus"], actual_keys=["actual"], event_key="name",
        status=status, error=err,
    )]


def probe_tradingeconomics() -> list[dict]:
    """Trading Economics guest:guest (free sample)."""
    url = "https://api.tradingeconomics.com/calendar?c=guest:guest&f=json"
    status, body, err = _get(url)
    rows = []
    if body:
        try:
            rows = json.loads(body)
        except ValueError as e:
            err = f"json: {e}"
    return [_verdict(
        "tradingeconomics:guest", url, rows=rows if isinstance(rows, list) else [],
        is_us=lambda r: str(r.get("Country", "")).strip().lower() == "united states",
        consensus_keys=["Forecast", "TEForecast"], actual_keys=["Actual"], event_key="Event",
        status=status, error=err,
    )]


def probe_eodhd() -> list[dict]:
    """EODHD economic-events on the public demo token."""
    url = "https://eodhd.com/api/economic-events?api_token=demo&fmt=json&limit=1000"
    status, body, err = _get(url)
    rows = []
    if body:
        try:
            data = json.loads(body)
            rows = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        except ValueError as e:
            err = f"json: {e}"
    return [_verdict(
        "eodhd:demo", url, rows=rows if isinstance(rows, list) else [],
        is_us=lambda r: str(r.get("country", "")).upper() in {"US", "USA", "UNITED STATES"},
        consensus_keys=["estimate"], actual_keys=["actual"], event_key="event",
        status=status, error=err,
    )]


def probe_fmp_endpoints() -> list[dict]:
    """Probe which FMP endpoints THIS key (FMP_API_KEY) can actually reach on the
    free tier — so we know what to repurpose vs what's premium-gated. FMP gates a
    premium endpoint with either an HTTP 401/403 OR an HTTP-200 JSON carrying an
    "Error Message" about an "exclusive"/"upgrade" plan; both are treated as gated.
    Curated to the endpoints THIS repo could actually use (commodity/NG EOD for the
    M1 price join, Treasury curve for M28, stock EOD failover, fundamentals for the
    value sleeve, economic indicators)."""
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return [{"source": "fmp:<no key>", "error": "FMP_API_KEY not set in probe env", "usable": False}]
    base = "https://financialmodelingprep.com/api/v3"
    today = _dt.date.today()
    frm = (today - _dt.timedelta(days=30)).isoformat()
    endpoints = [
        ("fmp:treasury-curve", f"{base}/treasury?from={frm}&to={today.isoformat()}"),
        ("fmp:commodity-natgas-eod", f"{base}/historical-price-full/NGUSD?from={frm}&to={today.isoformat()}"),
        ("fmp:commodity-gold-eod", f"{base}/historical-price-full/GCUSD?from={frm}&to={today.isoformat()}"),
        ("fmp:stock-eod-SPY", f"{base}/historical-price-full/SPY?from={frm}&to={today.isoformat()}"),
        ("fmp:etf-eod-UNG", f"{base}/historical-price-full/UNG?from={frm}&to={today.isoformat()}"),
        ("fmp:key-metrics-AAPL", f"{base}/key-metrics/AAPL?limit=4"),
        ("fmp:ratios-AAPL", f"{base}/ratios/AAPL?limit=4"),
        ("fmp:income-statement-AAPL", f"{base}/income-statement/AAPL?limit=4"),
        ("fmp:economic-indicator-GDP", f"{base}/economic?name=GDP"),
        ("fmp:economic-calendar", f"{base}/economic_calendar?from={frm}&to={today.isoformat()}"),
        ("fmp:earnings-calendar", f"{base}/earning_calendar?from={frm}&to={today.isoformat()}"),
        ("fmp:sector-pe", f"{base}/sector_price_earning_ratio?date={today.isoformat()}&exchange=NYSE"),
        ("fmp:forex-eod-EURUSD", f"{base}/historical-price-full/EURUSD?from={frm}&to={today.isoformat()}"),
        ("fmp:crypto-eod-BTCUSD", f"{base}/historical-price-full/BTCUSD?from={frm}&to={today.isoformat()}"),
    ]
    out = []
    for name, url in endpoints:
        status, body, err = _get(f"{url}{'&' if '?' in url else '?'}apikey={key}")
        gated = False
        rows = None
        note = None
        if body:
            low = body.lower()
            if '"error message"' in low or "exclusive endpoint" in low or "upgrade your plan" in low or "special endpoint" in low:
                gated = True
                note = body[:180]
            else:
                try:
                    data = json.loads(body)
                    if isinstance(data, list):
                        rows = len(data)
                    elif isinstance(data, dict):
                        rows = len(data.get("historical", data.get("data", []))) or (1 if data else 0)
                        note = "keys: " + ",".join(list(data.keys())[:6])
                except ValueError:
                    note = body[:120]
        usable = status == 200 and not gated and (rows is None or rows > 0)
        out.append({"source": name, "http_status": status, "gated_premium": gated,
                    "rows": rows, "note": note, "error": err, "usable": usable})
    return out


def probe_fmp_stable() -> list[dict]:
    """Re-probe FMP on the NEW ``/stable/`` path — every ``/api/v3/`` endpoint
    returned a uniform HTTP 403 (even normally-free SPY EOD), which signals FMP
    retired free access to the legacy path, not per-endpoint gating. If ``/stable/``
    works on the free key, FMP free is actually usable (calendar + backtest history)."""
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return [{"source": "fmp-stable:<no key>", "usable": False}]
    base = "https://financialmodelingprep.com/stable"
    today = _dt.date.today()
    frm = (today - _dt.timedelta(days=30)).isoformat()
    endpoints = [
        ("fmp-stable:economics-calendar", f"{base}/economics-calendar?from={frm}&to={today.isoformat()}"),
        ("fmp-stable:treasury-rates", f"{base}/treasury-rates?from={frm}&to={today.isoformat()}"),
        ("fmp-stable:eod-SPY", f"{base}/historical-price-eod/full?symbol=SPY&from={frm}&to={today.isoformat()}"),
        ("fmp-stable:eod-NGUSD", f"{base}/historical-price-eod/full?symbol=NGUSD&from={frm}&to={today.isoformat()}"),
        ("fmp-stable:eod-UNG", f"{base}/historical-price-eod/full?symbol=UNG&from={frm}&to={today.isoformat()}"),
        ("fmp-stable:key-metrics-AAPL", f"{base}/key-metrics?symbol=AAPL&limit=4"),
        ("fmp-stable:ratios-AAPL", f"{base}/ratios?symbol=AAPL&limit=4"),
        ("fmp-stable:economic-indicators-GDP", f"{base}/economic-indicators?name=GDP&from={frm}&to={today.isoformat()}"),
    ]
    out = []
    for name, url in endpoints:
        status, body, err = _get(f"{url}&apikey={key}")
        gated = False
        rows = None
        sample = None
        if body:
            low = body.lower()
            if '"error message"' in low or "exclusive" in low or "upgrade" in low or "premium" in low or "legacy" in low:
                gated = True
                sample = body[:200]
            else:
                try:
                    data = json.loads(body)
                    if isinstance(data, list):
                        rows = len(data)
                        sample = json.dumps(data[0])[:300] if data else None
                    elif isinstance(data, dict):
                        rows = 1
                        sample = json.dumps(data)[:300]
                except ValueError:
                    sample = body[:160]
        usable = status == 200 and not gated and (rows is None or rows > 0)
        out.append({"source": name, "http_status": status, "gated_premium": gated,
                    "rows": rows, "sample": sample, "error": err, "usable": usable})
    return out


def main() -> int:
    results: list[dict] = []
    for probe in (probe_faireconomy, probe_fxstreet, probe_tradingeconomics, probe_eodhd,
                  probe_fmp_endpoints, probe_fmp_stable):
        try:
            results.extend(probe())
        except Exception as e:  # noqa: BLE001
            results.append({"source": probe.__name__, "error": f"probe crashed: {e}", "usable": False})

    print("=" * 72)
    print("ROADMAP_MACRO M1 — free economic-calendar source probe")
    print("=" * 72)
    lines = ["| source | http | total | US | US+consensus | US+actual | usable |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r.get('source')} | {r.get('http_status')} | {r.get('total_rows','-')} | "
            f"{r.get('us_rows','-')} | {r.get('us_with_consensus','-')} | "
            f"{r.get('us_with_actual','-')} | {'✅' if r.get('usable') else '❌'} |"
        )
    table = "\n".join(lines)
    print(table)
    print("\nfull JSON:\n" + json.dumps(results, indent=2, default=str))

    # GitHub step summary (when running in Actions).
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as fh:
            fh.write("## Free economic-calendar source probe\n\n" + table + "\n\n")
            fh.write("<details><summary>samples + errors</summary>\n\n```json\n")
            fh.write(json.dumps(results, indent=2, default=str))
            fh.write("\n```\n</details>\n")

    usable = [r["source"] for r in results if r.get("usable")]
    print(f"\nUSABLE (US events + consensus): {usable or 'NONE — all free sources failed the US+consensus bar'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
