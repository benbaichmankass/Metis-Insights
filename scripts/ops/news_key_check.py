#!/usr/bin/env python3
"""Validate the news layer's feed end-to-end through the bot's own code.

Runs in GitHub Actions (open internet) so we can confirm the configured source
returns **fresh, relevant** articles — and that the multi-asset wiring resolves
the right feeds/query per symbol — **without enabling the layer on the live
trader**. No VM contact, read-only.

Leads with the RSS source (free, keyless, real-time — the path we use to dodge
the NewsAPI free-tier ~24h delay). If a ``NEWS_API_KEY`` is present it also runs
a NewsAPI auth + freshness check for comparison.

Prints a human-readable summary; never echoes a key or full article text.
Exit 0 if the RSS source yields fresh+relevant news for at least one symbol;
exit 1 otherwise (so CI/the issue comment flags a dead feed set).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.news.news_client import fetch_news  # noqa: E402
from src.news.news_client_rss import fetch_news_rss  # noqa: E402
from src.news.news_normalizer import normalize_articles  # noqa: E402
from src.news.news_score import score_news  # noqa: E402
from src.news.news_symbols import query_for_tags  # noqa: E402

_NEWSAPI_BASE = "https://newsapi.org/v2/everything"
_SYMBOLS = [(["BTC", "BTCUSDT"], "BTC"), (["MES"], "MES"), (["MGC"], "MGC")]


def _summarize(arts: list, tags: list, label: str, settings: dict) -> tuple[str, bool]:
    """Normalize+score articles for *tags*; return (report_line, has_fresh_relevant)."""
    norm = normalize_articles(arts, symbol_tags=tags, settings=settings)
    result = score_news(norm, settings=settings)
    fresh = sorted(round(float(a.get("freshness_minutes", 9999)), 1) for a in norm)
    rel = [round(float(a.get("relevance_score", 0.0)), 2) for a in norm]
    newest = fresh[0] if fresh else None
    n_fresh = sum(1 for f in fresh if f <= 120)
    n_rel = sum(1 for r in rel if r > 0.0)
    n_fresh_rel = sum(
        1 for a in norm
        if float(a.get("freshness_minutes", 9999)) <= 120 and float(a.get("relevance_score", 0)) > 0
    )
    line = (
        f"  [{label}] fetched={len(arts)}  decision={result.decision}  "
        f"adj={result.adjustment:+.4f}  veto={result.veto}  scored={result.item_count}\n"
        f"    newest_age_min={newest}  fresh(<=120m)={n_fresh}/{len(norm)}  "
        f"relevant(>0)={n_rel}/{len(norm)}  fresh&relevant={n_fresh_rel}"
    )
    return line, n_fresh_rel > 0


def _rss_check() -> bool:
    print("RSS source (free, keyless, real-time):")
    settings = {"NEWS_ENABLED": "true", "NEWS_SOURCE": "rss", "NEWS_CACHE_TTL": "0"}
    any_fresh_rel = False
    for tags, label in _SYMBOLS:
        try:
            arts = fetch_news_rss(settings, symbol_tags=tags)
            line, ok = _summarize(arts, tags, label, settings)
            print(line)
            any_fresh_rel = any_fresh_rel or ok
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] RSS error: {exc}")
    return any_fresh_rel


def _newsapi_direct_status(api_key: str) -> str:
    params = urllib.parse.urlencode({"q": "Bitcoin", "pageSize": 1, "language": "en", "apiKey": api_key})
    try:
        req = urllib.request.Request(f"{_NEWSAPI_BASE}?{params}", headers={"User-Agent": "ict/keycheck"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        try:
            b = json.loads(exc.read().decode("utf-8"))
            return f"FAIL — HTTP {exc.code}: {b.get('code')} — {b.get('message')}"
        except Exception:  # noqa: BLE001
            return f"FAIL — HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"FAIL — {exc}"
    if body.get("status") != "ok":
        return f"FAIL — status={body.get('status')} code={body.get('code')}"
    return f"OK — totalResults={body.get('totalResults')}"


def _newsapi_check(api_key: str) -> None:
    print("NewsAPI source (comparison; informational):")
    print(f"  auth: {_newsapi_direct_status(api_key)}")
    settings = {"NEWS_ENABLED": "true", "NEWS_SOURCE": "newsapi", "NEWS_API_KEY": api_key,
                "NEWS_CACHE_TTL": "0"}
    for tags, label in _SYMBOLS[:2]:
        try:
            arts = fetch_news(settings, query=query_for_tags(tags))
            line, _ = _summarize(arts, tags, label, settings)
            print(line)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] NewsAPI error: {exc}")


def main() -> int:
    print("=== news feed check (off-VM, read-only) ===")
    rss_ok = _rss_check()

    api_key = (os.environ.get("NEWS_API_KEY") or "").strip()
    if api_key:
        _newsapi_check(api_key)
    else:
        print("NewsAPI source: (no NEWS_API_KEY secret — skipped)")

    if rss_ok:
        print("RESULT: PASS — RSS yields fresh (<=120m) + relevant articles; "
              "the layer would actually score/veto on live news.")
        return 0
    print("RESULT: FAIL — RSS returned no fresh+relevant articles. Check the feed "
          "URLs in config/news_feeds.yaml (a feed may have moved/404'd).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
