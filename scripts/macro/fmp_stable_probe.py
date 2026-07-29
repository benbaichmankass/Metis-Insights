#!/usr/bin/env python3
"""ROADMAP_MACRO M28 — FMP `/stable/` response-shape capture (verify-before-build).

The source probe (`docs/research/M1-econ-calendar-source-probe-2026-07-29.md`)
found FMP's **`/stable/`** path serves some data on the free key even though the
legacy `/api/v3/` path 403s: `treasury-rates`, `historical-price-eod`, and the
`key-metrics`/`ratios` fundamentals returned 200. A **200 is not a schema** — per
the `macro-research` skill's verify-the-source-before-you-build invariant, we do
NOT write an adapter against guessed field names (the FMP-403 lesson, one level
in). This probe captures the REAL response shapes on a runner (the sandbox can't
reach FMP; the proxy 403s) and prints them to the run log so the adapter is built
against observed fields, not assumed ones.

Observe-only, throwaway diagnostic: it fetches a few rows per endpoint and prints
each one's HTTP status + row count + first-row key set + a trimmed first row. No
repo write, no order path. Runs on a hosted GitHub runner with the free
`FMP_API_KEY`; off-VM-guarded so it can never fire on the live trading VM.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_TRUTHY = {"1", "true", "yes", "on"}
_BASE = "https://financialmodelingprep.com/stable"

# (label, path, query) — the endpoints the source probe flagged as free-tier 200,
# plus the two natural-gas price proxies (UNG is an equity ETF, so it may be free
# like SPY even though the NGUSD commodity symbol was 402 — worth confirming as a
# keyless-free M1 NG price source).
_TARGETS = [
    # Round 2 — resolve the one concrete M28 gap (index-level earnings yield for the
    # equity_risk_premium metric) + the history-depth questions round 1 left open.
    ("key_metrics_spy", "key-metrics", "symbol=SPY"),               # ETF fundamentals free?
    ("ratios_spy", "ratios", "symbol=SPY"),
    ("key_metrics_gspc", "key-metrics", "symbol=^GSPC"),            # index proxy
    ("key_metrics_aapl_q", "key-metrics", "symbol=AAPL&period=quarter&limit=40"),  # quarterly depth?
    ("treasury_rates_ranged", "treasury-rates", "from=2015-01-01&to=2020-01-01"),  # history depth?
    ("ratios_aapl_q", "ratios", "symbol=AAPL&period=quarter&limit=40"),
]


def _offvm_enabled() -> bool:
    return str(os.environ.get("ICT_OFFVM_BUILD_HOST", "")).lower() in _TRUTHY


def _trim(value: object, *, maxlen: int = 60) -> object:
    """Truncate long scalar values so the log stays readable."""
    if isinstance(value, str) and len(value) > maxlen:
        return value[:maxlen] + "…"
    return value


def _summarize(payload: object) -> dict:
    """Row count + first-row key set + a trimmed first row, for any shape."""
    if isinstance(payload, list):
        n = len(payload)
        first = payload[0] if payload else None
    elif isinstance(payload, dict):
        # some /stable endpoints wrap rows under a key; also handle the flat dict
        rows = next((v for v in payload.values() if isinstance(v, list)), None)
        if rows is not None:
            n, first = len(rows), (rows[0] if rows else None)
        else:
            n, first = 1, payload
    else:
        return {"shape": type(payload).__name__, "rows": 0, "first": None}
    keys = sorted(first.keys()) if isinstance(first, dict) else None
    trimmed = {k: _trim(v) for k, v in list(first.items())[:20]} if isinstance(first, dict) else _trim(first)
    return {"shape": type(payload).__name__, "rows": n, "keys": keys, "first_row": trimmed}


def probe_one(label: str, path: str, query: str, *, key: str, urlopen=urllib.request.urlopen) -> dict:
    sep = "&" if query else ""
    url = f"{_BASE}/{path}?{query}{sep}apikey={key}"
    safe_url = url.replace(key, "***")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "metis-fmp-probe/1"})
        with urlopen(req, timeout=25) as resp:
            body = resp.read().decode()
            status = getattr(resp, "status", 200)
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return {"label": label, "url": safe_url, "http": status, "error": "non-json", "body_head": body[:200]}
        out = {"label": label, "url": safe_url, "http": status}
        out.update(_summarize(payload))
        return out
    except urllib.error.HTTPError as exc:
        head = ""
        try:
            head = exc.read().decode()[:200]
        except Exception:  # noqa: BLE001
            pass
        return {"label": label, "url": safe_url, "http": exc.code, "error": f"HTTPError {exc.code}", "body_head": head}
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "url": safe_url, "http": None, "error": f"{type(exc).__name__}: {exc}"}


def main(argv=None) -> int:
    if not _offvm_enabled():
        print("fmp_stable_probe: off-VM only (set ICT_OFFVM_BUILD_HOST=1)")
        return 2
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        print("fmp_stable_probe: FMP_API_KEY not set — cannot probe")
        return 2

    results = [probe_one(lbl, path, q, key=key) for (lbl, path, q) in _TARGETS]
    print("FMP /stable response-shape capture")
    print("=" * 60)
    for r in results:
        print(f"\n### {r['label']}  →  HTTP {r.get('http')}")
        print(f"    url: {r['url']}")
        if r.get("error"):
            print(f"    ERROR: {r['error']}  {r.get('body_head', '')}")
            continue
        print(f"    shape={r.get('shape')}  rows={r.get('rows')}")
        print(f"    keys={r.get('keys')}")
        print(f"    first_row={json.dumps(r.get('first_row'), default=str)}")
    print("\n" + "=" * 60)
    print("MACHINE_SUMMARY " + json.dumps(results, default=str))
    usable = [r["label"] for r in results if r.get("http") == 200 and not r.get("error") and r.get("rows")]
    print(f"usable (HTTP 200 + rows): {usable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
