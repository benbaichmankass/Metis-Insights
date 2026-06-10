"""
NewsAPI client for the news-augmented trade decision layer.

Source: NewsAPI (https://newsapi.org)
  - Single source for v1, chosen for simplicity and clean normalization.
  - Endpoint: GET /v2/everything?q=<query>&apiKey=<key>

Config keys (read from the settings dict or environment):
  NEWS_API_KEY          : NewsAPI key (required for the newsapi source)
  NEWS_QUERY            : search query, e.g. "BTC OR Bitcoin" (default "Bitcoin")
  NEWS_MAX_ARTICLES     : max articles to fetch per call (default 10, max 100)
  NEWS_CACHE_TTL        : seconds to cache results (default 300)

Activation is source-driven — there is no separate enable gate (the legacy
``NEWS_ENABLED`` flag was removed 2026-06-10: for an RSS feed no gate is
needed, and for the newsapi source the key presence IS the gate). The ``rss``
source is keyless and always active; the ``newsapi`` source is active only
when ``NEWS_API_KEY`` is set. When the source is unusable (newsapi with no
key) the client returns an empty list rather than raising, so the pipeline
sees "no news."
"""
from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import json
from typing import Any, Dict, List, Optional

from src.news.news_cache import get_cache

logger = logging.getLogger(__name__)

_NEWSAPI_BASE = "https://newsapi.org/v2/everything"
_DEFAULT_QUERY = "Bitcoin OR BTC"
_DEFAULT_MAX_ARTICLES = 10
_DEFAULT_CACHE_TTL = 300  # seconds


def _get_api_key(settings: dict) -> str:
    return str(settings.get("NEWS_API_KEY", os.environ.get("NEWS_API_KEY", ""))).strip()


def _news_source(settings: dict) -> str:
    raw = settings.get("NEWS_SOURCE") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("NEWS_SOURCE", "newsapi")
    return str(raw).strip().lower()


def is_active(settings: Optional[dict] = None) -> bool:
    """True when the news layer has a usable source.

    The single source of truth for "is the news hook live" — used to gate the
    shadow-decision soak log so it stays empty (no per-tick noise) until the
    layer is actually evaluating news. Activation is purely source-driven: the
    RSS source needs no key (always active); the NewsAPI source needs a
    non-blank key. (No separate NEWS_ENABLED gate — removed 2026-06-10.)
    """
    settings = settings or {}
    if _news_source(settings) == "rss":
        return True
    return bool(_get_api_key(settings))


def _get_query(settings: dict) -> str:
    return str(settings.get("NEWS_QUERY", os.environ.get("NEWS_QUERY", _DEFAULT_QUERY))).strip()


def _get_max_articles(settings: dict) -> int:
    raw = settings.get("NEWS_MAX_ARTICLES", os.environ.get("NEWS_MAX_ARTICLES", _DEFAULT_MAX_ARTICLES))
    try:
        return max(1, min(100, int(raw)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ARTICLES


def _get_cache_ttl(settings: dict) -> float:
    raw = settings.get("NEWS_CACHE_TTL", os.environ.get("NEWS_CACHE_TTL", _DEFAULT_CACHE_TTL))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_CACHE_TTL)


def fetch_news(settings: dict, query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch raw news articles from NewsAPI.

    Returns a list of raw article dicts as returned by the NewsAPI
    ``/v2/everything`` endpoint. Returns an empty list when:
      - no API key is configured (the newsapi source's activation gate), or
      - a network or API error occurs (logged at WARNING level).

    The result is cached for ``NEWS_CACHE_TTL`` seconds (keyed by query).

    Parameters
    ----------
    settings:
        Dict of config/env values (same pattern as the rest of the pipeline).
        Keys accepted: NEWS_API_KEY, NEWS_QUERY, NEWS_MAX_ARTICLES,
        NEWS_CACHE_TTL.
    query:
        Per-symbol search query override (resolved from ``config/news_symbols.yaml``
        by the pipeline). When ``None`` falls back to ``NEWS_QUERY`` / the module
        default — so a per-symbol query wins, an explicit ``NEWS_QUERY`` is next,
        and the Bitcoin default is last.
    """
    api_key = _get_api_key(settings)
    if not api_key:
        logger.debug("news layer has no API key (NEWS_API_KEY not set); returning empty")
        return []

    query = (query.strip() if isinstance(query, str) and query.strip() else None) or _get_query(settings)
    page_size = _get_max_articles(settings)
    cache_ttl = _get_cache_ttl(settings)
    cache_key = f"newsapi:{query}:{page_size}"

    cache = get_cache()
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug("news: cache hit for query=%r", query)
        return cached

    params = urllib.parse.urlencode({
        "q": query,
        "pageSize": page_size,
        "language": "en",
        "sortBy": "publishedAt",
        "apiKey": api_key,
    })
    url = f"{_NEWSAPI_BASE}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ict-trading-bot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("news: NewsAPI HTTP error %s — %s", exc.code, exc.reason)
        return []
    except urllib.error.URLError as exc:
        logger.warning("news: NewsAPI network error — %s", exc.reason)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("news: unexpected error fetching news — %s", exc)
        return []

    if body.get("status") != "ok":
        logger.warning("news: NewsAPI returned status=%r message=%r",
                       body.get("status"), body.get("message"))
        return []

    articles: List[Dict[str, Any]] = body.get("articles") or []
    logger.info("news: fetched %d articles for query=%r", len(articles), query)
    cache.set(cache_key, articles, ttl=cache_ttl)
    return articles
