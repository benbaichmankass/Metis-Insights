#!/usr/bin/env python3
"""Validate the NewsAPI key end-to-end through the bot's own news code.

Runs in GitHub Actions (where the ``NEWS_API_KEY`` secret is available) so we
can confirm the key works — and that the multi-asset wiring resolves the right
query per symbol — **without enabling the layer on the live trader**. No VM
contact, read-only.

Prints a human-readable summary and never echoes the key or full article text.
Exit 0 if the key authenticates; exit 1 on an invalid/exhausted key or no key.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

# Repo root on path so `src.news.*` imports resolve when run from CI.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.news.news_client import fetch_news  # noqa: E402
from src.news.news_pipeline import get_news_score  # noqa: E402
from src.news.news_symbols import query_for_tags  # noqa: E402

_NEWSAPI_BASE = "https://newsapi.org/v2/everything"


def _direct_status(api_key: str) -> tuple[bool, str]:
    """Minimal direct NewsAPI call to validate the key. Returns (ok, detail).

    Never prints/returns the key. Distinguishes a bad key (auth error) from a
    valid key that simply returned no rows.
    """
    params = urllib.parse.urlencode(
        {"q": "Bitcoin", "pageSize": 1, "language": "en", "apiKey": api_key}
    )
    try:
        req = urllib.request.Request(
            f"{_NEWSAPI_BASE}?{params}", headers={"User-Agent": "ict-trading-bot/keycheck"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # NewsAPI returns 401 with a JSON body for a bad key.
        try:
            b = json.loads(exc.read().decode("utf-8"))
            return False, f"HTTP {exc.code}: {b.get('code')} — {b.get('message')}"
        except Exception:  # noqa: BLE001
            return False, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, f"network error: {exc}"
    if body.get("status") != "ok":
        return False, f"status={body.get('status')} code={body.get('code')} msg={body.get('message')}"
    return True, f"ok — totalResults={body.get('totalResults')}"


def _probe_symbol(api_key: str, tags: list[str], label: str) -> str:
    """Run a symbol's resolved query through fetch_news + get_news_score."""
    settings = {"NEWS_ENABLED": "true", "NEWS_API_KEY": api_key}
    query = query_for_tags(tags)
    arts = fetch_news(settings, query=query)
    result = get_news_score(settings, symbol_tags=tags)
    return (
        f"  [{label}] query={query!r}\n"
        f"    articles_fetched={len(arts)}  decision={result.decision}  "
        f"adjustment={result.adjustment:+.4f}  veto={result.veto}  "
        f"items_scored={result.item_count}"
    )


def main() -> int:
    api_key = (os.environ.get("NEWS_API_KEY") or "").strip()
    print("=== NewsAPI key check (off-VM, read-only) ===")
    if not api_key:
        print("RESULT: FAIL — NEWS_API_KEY secret is empty/unset.")
        return 1

    ok, detail = _direct_status(api_key)
    print(f"Direct NewsAPI auth: {'OK' if ok else 'FAIL'} — {detail}")
    if not ok:
        print("RESULT: FAIL — the key did not authenticate. Check the NEWS_API_KEY secret value.")
        return 1

    print("Through-the-bot probe (multi-asset):")
    try:
        print(_probe_symbol(api_key, ["BTC", "BTCUSDT"], "BTC"))
        print(_probe_symbol(api_key, ["MES"], "MES"))
    except Exception as exc:  # noqa: BLE001
        print(f"  pipeline error: {exc}")
        print("RESULT: PARTIAL — key authenticates but the bot pipeline raised (see above).")
        return 1

    print("RESULT: PASS — key authenticates and the bot fetched + scored news for BTC and MES.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
