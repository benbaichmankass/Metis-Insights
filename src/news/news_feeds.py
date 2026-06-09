"""RSS feed configuration loader for the M9 news layer (NEWS_SOURCE=rss).

Resolves the set of RSS feed URLs to fetch for a given set of symbol tags from
``config/news_feeds.yaml`` (per-symbol-class groups + a shared ``global`` group).
Never raises — a missing/malformed file yields no feeds (the layer then simply
returns "no news", same as a disabled state).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# src/news/news_feeds.py -> parents[2] == repo root.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "news_feeds.yaml"

_DEFAULT_MAX_ITEMS = 25
_DEFAULT_TIMEOUT = 8.0


def _base_of(tag: str) -> str:
    base = str(tag or "").upper().split("/")[0]
    for suffix in ("USDT", "PERP", "USD"):
        if base.endswith(suffix) and base != suffix:
            return base[: -len(suffix)]
    return base


@lru_cache(maxsize=1)
def load_feeds_config() -> Dict[str, Any]:
    empty: Dict[str, Any] = {"defaults": {}, "groups": {}, "symbol_groups": {}}
    try:
        import yaml
    except Exception:  # noqa: BLE001
        return empty
    try:
        if not _CONFIG_PATH.exists():
            return empty
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("news_feeds: failed to load %s — %s", _CONFIG_PATH, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    groups = data.get("groups") or {}
    sym = data.get("symbol_groups") or {}
    return {
        "defaults": data.get("defaults") or {},
        "groups": {str(k): list(v or []) for k, v in groups.items()} if isinstance(groups, dict) else {},
        "symbol_groups": {str(k).upper(): list(v or []) for k, v in sym.items()} if isinstance(sym, dict) else {},
    }


def reload_feeds_config() -> None:
    load_feeds_config.cache_clear()


def max_items_per_feed() -> int:
    try:
        return max(1, int(load_feeds_config()["defaults"].get("max_items_per_feed", _DEFAULT_MAX_ITEMS)))
    except (TypeError, ValueError, KeyError):
        return _DEFAULT_MAX_ITEMS


def feed_timeout_seconds() -> float:
    try:
        return max(1.0, float(load_feeds_config()["defaults"].get("timeout_seconds", _DEFAULT_TIMEOUT)))
    except (TypeError, ValueError, KeyError):
        return _DEFAULT_TIMEOUT


def feeds_for_tags(tags: Optional[Sequence[str]]) -> List[str]:
    """Resolve the de-duplicated feed-URL list for *tags*.

    Each tag's base maps (via ``symbol_groups``) to a list of groups; the
    ``global`` group is always included. When no tag matches, returns just the
    ``global`` feeds so a brand-new symbol still pulls macro news.
    """
    cfg = load_feeds_config()
    groups: Dict[str, List[str]] = cfg.get("groups", {})
    symbol_groups: Dict[str, List[str]] = cfg.get("symbol_groups", {})

    selected: List[str] = ["global"]
    for tag in tags or []:
        for g in symbol_groups.get(_base_of(tag), []):
            if g not in selected:
                selected.append(g)

    urls: List[str] = []
    for g in selected:
        for url in groups.get(g, []):
            u = str(url).strip()
            if u and u not in urls:
                urls.append(u)
    return urls
