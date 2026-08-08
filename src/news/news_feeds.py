"""RSS feed configuration loader for the M9 news layer (NEWS_SOURCE=rss).

Resolves the set of RSS feed URLs to fetch for a given set of symbol tags from
``config/news_feeds.yaml`` (feed groups + a shared ``global`` group).
Never raises — a missing/malformed file yields no feeds (the layer then simply
returns "no news", same as a disabled state).

**Feed selection is DERIVED, not hand-maintained** (2026-08-07,
``BL-20260807-NEWS-FEED-SYMBOL-COVERAGE-5-OF-24``). A symbol's feed group comes
from its instrument classification
(:func:`src.core.instrument_class.news_group_for_symbol`), which reads
``config/instruments.yaml`` — the registry that must already be correct for the
symbol to trade at all. The class→group mapping lives in
``news_feeds.yaml::asset_class_groups``.

This replaced a per-symbol ``symbol_groups`` map, which was a SECOND registry
running parallel to ``instruments.yaml`` and had drifted to cover 5 of 24 traded
bases — so 19 symbols (every non-BTC/ETH crypto among them) read macro-only
headlines while the news veto was armed. A hand-extended list would have drifted
again; deriving removes the failure mode by construction. ``symbol_groups`` is
still read if present, purely so an old config keeps working, but it is no
longer the mechanism and nothing ships with it.
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
    acg = data.get("asset_class_groups") or {}
    return {
        "defaults": data.get("defaults") or {},
        "groups": {str(k): list(v or []) for k, v in groups.items()} if isinstance(groups, dict) else {},
        # Legacy per-symbol map — read only so a pre-2026-08-07 config keeps
        # working. Nothing ships with it; see the module docstring.
        "symbol_groups": {str(k).upper(): list(v or []) for k, v in sym.items()} if isinstance(sym, dict) else {},
        # asset class (or a per-instrument ``news_group`` override) -> feed groups.
        "asset_class_groups": (
            {str(k).lower(): list(v or []) for k, v in acg.items()} if isinstance(acg, dict) else {}
        ),
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


def groups_for_tags(tags: Optional[Sequence[str]]) -> List[str]:
    """Resolve the de-duplicated FEED-GROUP names for *tags*.

    ``global`` is always first — the shared macro feeds apply to everything.
    Each tag then contributes its class-derived group(s):

        symbol -> news_group_for_symbol()  (instruments.yaml: ``news_group``
                  override, else ``asset_class``)
               -> asset_class_groups[...]  (news_feeds.yaml)
               -> feed group name(s)

    A symbol resolving to no class-specific group keeps just ``global``. That
    is the CORRECT answer for bonds and FX — rates/Fed/inflation news *is* the
    macro feed — not a gap to be patched.

    Exposed separately from :func:`feeds_for_tags` so the coverage guard can
    assert on group NAMES without depending on which URLs a group happens to
    hold today.
    """
    cfg = load_feeds_config()
    class_groups: Dict[str, List[str]] = cfg.get("asset_class_groups", {})
    legacy: Dict[str, List[str]] = cfg.get("symbol_groups", {})

    selected: List[str] = ["global"]

    def _add(name: str) -> None:
        n = str(name or "").strip()
        if n and n not in selected:
            selected.append(n)

    for tag in tags or []:
        # Legacy per-symbol map first, purely for back-compat with an old
        # config. Nothing ships with symbol_groups populated.
        for g in legacy.get(_base_of(tag), []):
            _add(g)
        try:
            from src.core.instrument_class import news_group_for_symbol

            token = news_group_for_symbol(tag)
        except Exception:  # noqa: BLE001
            # Classifier unavailable/failed — degrade to the macro feeds rather
            # than dropping the symbol's news entirely. Never raises upward:
            # this runs inside the per-signal news fetch.
            logger.warning(
                "news_feeds: instrument classification failed for %r — "
                "falling back to global feeds only", tag, exc_info=True,
            )
            token = None
        if token:
            mapped = class_groups.get(str(token).lower())
            if mapped:
                for g in mapped:
                    _add(g)
            else:
                # An asset class with no mapping is a CONFIG gap, not a symbol
                # problem — say so once, loudly enough to fix, and continue on
                # the macro feeds.
                logger.info(
                    "news_feeds: no asset_class_groups entry for %r (symbol %r) "
                    "— global feeds only", token, tag,
                )
    return selected


def feeds_for_tags(tags: Optional[Sequence[str]]) -> List[str]:
    """Resolve the de-duplicated feed-URL list for *tags*."""
    cfg = load_feeds_config()
    groups: Dict[str, List[str]] = cfg.get("groups", {})
    urls: List[str] = []
    for g in groups_for_tags(tags):
        for url in groups.get(g, []):
            u = str(url).strip()
            if u and u not in urls:
                urls.append(u)
    return urls
