#!/usr/bin/env python3
"""PM-COLLECTOR — free prediction-market snapshot collector (macro questions).

Reads Kalshi's public market-data REST and Polymarket's public gamma API (both
unauthenticated, read-only) for the curated list in config/pm_markets.yaml and
APPENDS one JSON line per market per run to comms/macro/pm_snapshots/YYYY-MM.jsonl.

Point-in-time: every line carries ``collected_at_utc`` (the run instant), source,
market id, bid/ask/last/implied probability, and ``content_hash`` (sha256 of the
canonical line without the hash). Append-only: existing lines are never rewritten;
a re-run adds new lines. Off-VM only (ICT_OFFVM_BUILD_HOST) — no order path, no DB.
Evaluation (PM-SURPRISE-EVAL) is NOT here.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "pm_markets.yaml"
OUT_DIR = REPO / "comms" / "macro" / "pm_snapshots"
_UA = "metis-insights-pm-collector/1 (research; low-frequency read-only)"
Fetch = Callable[[str], Any]


def http_get_json(url: str, retries: int = 4) -> Any:
    if os.environ.get("ICT_OFFVM_BUILD_HOST", "").lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("refusing network: ICT_OFFVM_BUILD_HOST not set")
    delay = 2.0
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _f(x: Any) -> Optional[float]:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def content_hash(rec: Dict[str, Any]) -> str:
    body = {k: v for k, v in rec.items() if k != "content_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _finish(rec: Dict[str, Any]) -> Dict[str, Any]:
    rec["content_hash"] = content_hash(rec)
    return rec


def kalshi_records(cfg: Dict[str, Any], fetch: Fetch, now: str) -> List[Dict[str, Any]]:
    base, cap = cfg["kalshi"]["base_url"], int(cfg.get("max_markets_per_series", 12))
    out: List[Dict[str, Any]] = []
    for series, topic in cfg["kalshi"]["series"].items():
        url = f"{base}/markets?" + urllib.parse.urlencode({"series_ticker": series, "status": "open", "limit": 200})
        ms = sorted((fetch(url).get("markets") or []), key=lambda m: (m.get("close_time") or "", m.get("ticker") or ""))[:cap]
        for m in ms:
            bid, ask, last = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("last_price_dollars"))
            mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else last
            out.append(_finish({
                "collected_at_utc": now, "source": "kalshi", "topic": topic, "series": series,
                "market_id": m.get("ticker"), "title": m.get("title"), "close_time": m.get("close_time"),
                "yes_bid": bid, "yes_ask": ask, "last_price": last, "implied_prob": mid,
                "volume_24h": _f(m.get("volume_24h_fp")), "open_interest": _f(m.get("open_interest_fp")),
            }))
    return out


def polymarket_records(cfg: Dict[str, Any], fetch: Fetch, now: str) -> List[Dict[str, Any]]:
    base, cap = cfg["polymarket"]["gamma_url"], int(cfg.get("max_markets_per_series", 12))
    out: List[Dict[str, Any]] = []
    for q in cfg["polymarket"]["queries"]:
        url = f"{base}/public-search?" + urllib.parse.urlencode({"q": q["q"], "limit_per_type": 10})
        recs: List[Dict[str, Any]] = []
        for ev in fetch(url).get("events") or []:
            if not str(ev.get("slug", "")).startswith(q["slug_prefix"]) or ev.get("closed"):
                continue
            for m in ev.get("markets") or []:
                if m.get("closed"):
                    continue
                try:
                    outcomes, prices = json.loads(m.get("outcomes") or "[]"), json.loads(m.get("outcomePrices") or "[]")
                except ValueError:
                    outcomes, prices = [], []
                yes = _f(prices[outcomes.index("Yes")]) if "Yes" in outcomes and len(prices) == len(outcomes) else None
                recs.append(_finish({
                    "collected_at_utc": now, "source": "polymarket", "topic": q["topic"], "series": ev.get("slug"),
                    "market_id": m.get("conditionId") or m.get("id"), "title": m.get("question"),
                    "close_time": m.get("endDate"), "yes_bid": _f(m.get("bestBid")), "yes_ask": _f(m.get("bestAsk")),
                    "last_price": _f(m.get("lastTradePrice")), "implied_prob": yes,
                    "volume_24h": _f(m.get("volume24hr")), "open_interest": _f(m.get("liquidity")),
                }))
        out += sorted(recs, key=lambda r: (r["close_time"] or "", r["market_id"] or ""))[:cap]
    return out


def collect(cfg: Dict[str, Any], fetch: Fetch, now: str) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    for name, fn in (("kalshi", kalshi_records), ("polymarket", polymarket_records)):
        try:
            recs += fn(cfg, fetch, now)
        except Exception as e:  # one source failing must not drop the other
            print(f"::warning::{name} degraded: {type(e).__name__}: {e}", file=sys.stderr)
    return recs


def append_snapshots(recs: List[Dict[str, Any]], out_dir: Path, now: str) -> Path:
    """Append-only: open with 'a'; never read-modify-write a past line."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now[:7]}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--dry-run", action="store_true", help="fetch and print; write nothing")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(a.config.read_text())
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    recs = collect(cfg, http_get_json, now)
    by = {}
    for r in recs:
        by[r["source"]] = by.get(r["source"], 0) + 1
    print(json.dumps({"collected_at_utc": now, "rows": len(recs), "by_source": by}))
    if a.dry_run:
        for r in recs[:6]:
            print(json.dumps(r, sort_keys=True))
        return 0
    if not recs:
        print("no rows collected; nothing written", file=sys.stderr)
        return 0
    print(f"appended to {append_snapshots(recs, a.out_dir, now)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
