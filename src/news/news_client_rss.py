"""RSS/Atom news client for the M9 news layer — free, keyless, real-time.

Drop-in alternative to the NewsAPI client (`news_client.py`). Selected via
``NEWS_SOURCE=rss``. Fetches the RSS/Atom feeds resolved for the traded symbol
(`news_feeds.feeds_for_tags`) and returns raw article dicts in the **same shape**
the normalizer consumes (``{title, description, url, publishedAt, source}``), so
the scorer / veto / multi-asset relevance logic is unchanged.

Why RSS: publishers emit items as they're published with a real `pubDate`
(usually minutes old), which sidesteps the NewsAPI free-tier ~24h delay that
made the layer inert. No API key, no per-call quota.

Stdlib only (urllib + xml.etree + email.utils) — no `feedparser` dependency,
matching the rest of the package. Best-effort and total: any feed that errors
is skipped; the function never raises and returns ``[]`` on total failure or
when ``NEWS_ENABLED`` is false.
"""
from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Sequence
from xml.etree import ElementTree as ET

from src.news.news_cache import get_cache
from src.news.news_feeds import feed_timeout_seconds, feeds_for_tags, max_items_per_feed

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_TTL = 300  # seconds
_UA = "ict-trading-bot/1.0 (+rss)"


def _is_enabled(settings: dict) -> bool:
    raw = str(settings.get("NEWS_ENABLED", os.environ.get("NEWS_ENABLED", "true"))).strip().lower()
    return raw not in {"false", "0", "no"}


def _get_cache_ttl(settings: dict) -> float:
    raw = settings.get("NEWS_CACHE_TTL", os.environ.get("NEWS_CACHE_TTL", _DEFAULT_CACHE_TTL))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_CACHE_TTL)


def _local(tag: str) -> str:
    """Strip an XML namespace: ``{http://...}title`` -> ``title``."""
    return tag.rsplit("}", 1)[-1].lower() if tag else ""


def _find_text(elem: ET.Element, *names: str) -> str:
    """First direct child whose local-name is in *names*, its text (stripped)."""
    wanted = {n.lower() for n in names}
    for child in elem:
        if _local(child.tag) in wanted and (child.text or "").strip():
            return child.text.strip()
    return ""


def _find_link(elem: ET.Element) -> str:
    """RSS <link>text</link> or Atom <link href=.. rel="alternate"/>."""
    fallback = ""
    for child in elem:
        if _local(child.tag) != "link":
            continue
        if (child.text or "").strip():  # RSS
            return child.text.strip()
        href = child.get("href")  # Atom
        if href:
            rel = (child.get("rel") or "alternate").lower()
            if rel == "alternate":
                return href
            fallback = fallback or href
    return fallback


def _to_iso(raw: str) -> str:
    """Normalize an RSS RFC-822 or Atom ISO date string to ISO-8601 UTC."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    # RSS pubDate is RFC-822 ("Mon, 09 Jun 2026 14:30:00 GMT").
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    # Atom dates are already ISO-8601.
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _parse_feed(xml_bytes: bytes, source_name: str, cap: int) -> List[Dict[str, Any]]:
    """Parse one RSS/Atom document into raw article dicts (newest-first order
    as the feed provides). Returns [] on a malformed document."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug("news_rss: parse error for %s — %s", source_name, exc)
        return []

    # RSS 2.0 -> .//item ; Atom -> .//entry (namespace-agnostic walk).
    items = [e for e in root.iter() if _local(e.tag) in ("item", "entry")]
    out: List[Dict[str, Any]] = []
    for it in items[: max(0, cap)]:
        title = _find_text(it, "title")
        desc = _find_text(it, "description", "summary", "content")
        pub = _find_text(it, "pubdate", "published", "updated", "date")
        url = _find_link(it)
        if not title and not desc:
            continue
        out.append({
            "title": title,
            "description": desc,
            "url": url,
            "publishedAt": _to_iso(pub),
            "source": {"name": source_name},
        })
    return out


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc or url
    except Exception:  # noqa: BLE001
        return url


def _fetch_one(url: str, timeout: float, cap: int) -> List[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        logger.debug("news_rss: fetch failed %s — %s", url, getattr(exc, "reason", exc))
        return []
    except Exception as exc:  # noqa: BLE001
        logger.debug("news_rss: unexpected fetch error %s — %s", url, exc)
        return []
    return _parse_feed(body, _domain(url), cap)


def fetch_news_rss(
    settings: dict, symbol_tags: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Fetch + parse the RSS/Atom feeds for *symbol_tags*. Never raises.

    Returns raw article dicts (NewsAPI-compatible shape). ``[]`` when the layer
    is disabled, no feeds resolve, or every feed errors. Cached for
    ``NEWS_CACHE_TTL`` seconds keyed by the resolved feed set.
    """
    if not _is_enabled(settings):
        return []
    feeds = feeds_for_tags(symbol_tags)
    if not feeds:
        logger.debug("news_rss: no feeds resolved for tags=%s", symbol_tags)
        return []

    cache_key = "rss:" + "|".join(sorted(feeds))
    cache = get_cache()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    cap = max_items_per_feed()
    timeout = feed_timeout_seconds()
    articles: List[Dict[str, Any]] = []
    for url in feeds:
        articles.extend(_fetch_one(url, timeout, cap))

    logger.info("news_rss: fetched %d items from %d feed(s)", len(articles), len(feeds))
    cache.set(cache_key, articles, ttl=_get_cache_ttl(settings))
    return articles
