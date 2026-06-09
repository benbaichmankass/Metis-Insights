"""
News pipeline: single entry point for the news-augmented trade decision layer.

Wires together fetch → normalize → score into one call so the strategy
layer only needs to import a single function.

Usage
-----
    from src.news.news_pipeline import get_news_score

    result = get_news_score(settings, symbol_tags=["BTC", "BTCUSDT"])

    print(result.adjustment)   # float in [-1, 1]
    print(result.veto)         # bool
    print(result.decision)     # "boost" | "reduce" | "veto" | "neutral"
    print(result.reason)       # str

The function is safe to call at every strategy tick:
  - Returns a neutral result instantly when NEWS_ENABLED=false or no key.
  - Network fetches are cached (default 5 min TTL via NEWS_CACHE_TTL).
  - Never raises; all errors are caught and logged at WARNING level.

Logging
-------
The caller can log the full decision payload with:

    import logging, json
    logging.getLogger(__name__).info(
        "news_score %s",
        json.dumps({
            "base_score": base,
            "news_adjustment": result.adjustment,
            "final_score": adjust_probability(base, result),
            "decision": result.decision,
            "reason": result.reason,
            "item_count": result.item_count,
        }),
    )
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.news.news_client import fetch_news
from src.news.news_normalizer import normalize_articles
from src.news.news_score import NewsScoreResult, score_news
from src.news.news_symbols import query_for_tags

logger = logging.getLogger(__name__)


def get_news_score(
    settings: Optional[Dict[str, Any]] = None,
    symbol_tags: Optional[List[str]] = None,
) -> NewsScoreResult:
    """Fetch, normalize, and score live news for *symbol_tags*.

    Parameters
    ----------
    settings:
        Config/env dict (same pattern as the rest of the pipeline).
        When ``None``, all values fall back to environment variables.
    symbol_tags:
        Trading symbols to score for relevance, e.g. ``["BTC", "BTCUSDT"]``.
        When ``None``, the normalizer checks all known symbols.

    Returns
    -------
    NewsScoreResult
        Always returns a valid result; never raises.
        Returns a neutral/no-news result when the module is disabled,
        the API key is absent, or any fetch/parse error occurs.
    """
    settings = settings or {}

    # Multi-asset: fetch the query that matches the traded symbol (S&P news for
    # MES, gold news for MGC, ...). None -> fetch_news falls back to NEWS_QUERY /
    # the Bitcoin default. A per-symbol config match takes precedence so a global
    # NEWS_QUERY can't re-break a non-crypto instrument.
    try:
        per_symbol_query = query_for_tags(symbol_tags)
    except Exception:  # noqa: BLE001
        per_symbol_query = None

    try:
        raw_articles = fetch_news(settings, query=per_symbol_query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("news_pipeline: fetch_news raised unexpectedly — %s", exc)
        return NewsScoreResult(reason=f"fetch error: {exc}")

    if not raw_articles:
        logger.debug("news_pipeline: no articles returned")
        return NewsScoreResult(reason="no news items")

    try:
        normalized = normalize_articles(raw_articles, symbol_tags=symbol_tags, settings=settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("news_pipeline: normalize_articles raised unexpectedly — %s", exc)
        return NewsScoreResult(reason=f"normalize error: {exc}")

    try:
        result = score_news(normalized, settings=settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("news_pipeline: score_news raised unexpectedly — %s", exc)
        return NewsScoreResult(reason=f"score error: {exc}")

    logger.info(
        "news_pipeline: decision=%s adj=%.4f items=%d reason=%s",
        result.decision,
        result.adjustment,
        result.item_count,
        result.reason[:120],
    )
    return result
