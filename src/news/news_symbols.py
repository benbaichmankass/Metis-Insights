"""Per-symbol news configuration loader (multi-asset support for the M9 layer).

Loads ``config/news_symbols.yaml`` and exposes two lookups the news layer needs
to stop being Bitcoin-only:

  - :func:`query_for_tags`   — the NewsAPI search query to fetch for a given set
                               of symbol tags (e.g. ``["MES"]`` -> the S&P query).
  - :func:`keywords_for_base`— the relevance keywords for a symbol base
                               (e.g. ``"MGC"`` -> gold/precious-metals terms).

Design rules (mirrors the rest of the news package):
  - **Never raises.** A missing or malformed file degrades to the built-in
    crypto behaviour: an unknown base resolves to ``[base.lower()]`` and the
    default macro query. The normalizer keeps its own ``_SYMBOL_KEYWORDS`` map
    as a second fallback, so crypto relevance works even if this file is absent.
  - **Cached.** Parsed once per process (``functools.lru_cache``); call
    :func:`reload_symbol_config` in tests to drop the cache.
  - **Config-only.** Adding an instrument is a YAML edit, not a code change.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# src/news/news_symbols.py -> parents[2] == repo root (matches bot_config.py).
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "news_symbols.yaml"

_DEFAULT_QUERY = "Bitcoin OR BTC"

_DEFAULT_MACRO_RELEVANCE_WEIGHT = 0.5

# Built-in macro / cross-asset relevance keywords. These are considered
# relevant to EVERY traded symbol (crypto included) so general market-moving
# news — the Fed, inflation, rates, risk sentiment, the dollar — informs every
# decision, not just the instrument-specific headlines. Used as the fallback
# when ``config/news_symbols.yaml`` has no ``defaults.macro_keywords`` block,
# so the macro layer works even if the config is stale/absent.
_BUILTIN_MACRO_KEYWORDS: List[str] = [
    "federal reserve", "fed", "fomc", "jerome powell", "powell",
    "interest rate", "interest rates", "rate cut", "rate hike", "rate decision",
    "monetary policy", "quantitative", "liquidity",
    "inflation", "cpi", "pce", "deflation", "stagflation",
    "recession", "gdp", "economy", "economic growth", "soft landing",
    "jobs report", "payrolls", "nonfarm", "unemployment", "labor market",
    "treasury", "treasury yield", "bond yield", "yields", "10-year", "10 year",
    "dollar", "u.s. dollar", "us dollar", "dxy", "greenback",
    "risk-off", "risk off", "risk-on", "risk on", "risk appetite", "risk sentiment",
    "stock market", "wall street", "equities", "s&p 500", "nasdaq",
    "tariff", "tariffs", "trade war", "sanctions", "geopolitical", "geopolitics",
    "central bank", "ecb", "boj",
]


def _base_of(tag: str) -> str:
    """Normalize a symbol tag to its base token (matches the pipeline's rule)."""
    base = str(tag or "").upper().split("/")[0]
    for suffix in ("USDT", "PERP", "USD"):
        if base.endswith(suffix) and base != suffix:
            base = base[: -len(suffix)]
            break
    return base


@lru_cache(maxsize=1)
def load_symbol_config() -> Dict[str, Any]:
    """Return the parsed ``news_symbols.yaml`` as ``{"defaults":..., "symbols":...}``.

    Returns an empty-but-valid structure on any error so callers never branch
    on ``None``.
    """
    empty: Dict[str, Any] = {"defaults": {}, "symbols": {}}
    try:
        import yaml  # local import: keeps the module importable without PyYAML
    except Exception:  # noqa: BLE001
        logger.debug("news_symbols: PyYAML unavailable; using built-in defaults")
        return empty
    try:
        if not _CONFIG_PATH.exists():
            logger.debug("news_symbols: %s absent; using built-in defaults", _CONFIG_PATH)
            return empty
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("news_symbols: failed to load %s — %s", _CONFIG_PATH, exc)
        return empty

    defaults = data.get("defaults") or {}
    raw_symbols = data.get("symbols") or {}
    # Upper-case the symbol keys so lookups are case-insensitive by base.
    symbols: Dict[str, Any] = {}
    if isinstance(raw_symbols, dict):
        for k, v in raw_symbols.items():
            if isinstance(v, dict):
                symbols[str(k).upper()] = v
    return {"defaults": defaults if isinstance(defaults, dict) else {}, "symbols": symbols}


def reload_symbol_config() -> None:
    """Drop the cached config (for tests / hot-reload)."""
    load_symbol_config.cache_clear()


def default_query() -> str:
    """The fallback fetch query for symbols with no explicit entry."""
    q = load_symbol_config().get("defaults", {}).get("query")
    return str(q).strip() if q else _DEFAULT_QUERY


def query_for_tags(tags: Optional[Sequence[str]]) -> Optional[str]:
    """Resolve the NewsAPI query for *tags* (first matching base wins).

    Returns ``None`` when no tag matches a configured symbol, so the caller can
    fall back to ``NEWS_QUERY`` / the module default rather than the broad macro
    query (keeps explicit operator-set ``NEWS_QUERY`` authoritative for crypto).
    """
    symbols = load_symbol_config().get("symbols", {})
    for tag in tags or []:
        entry = symbols.get(_base_of(tag))
        if entry and entry.get("query"):
            return str(entry["query"]).strip()
    return None


def keywords_for_base(base: str) -> Optional[List[str]]:
    """Relevance keywords for a symbol *base*, or ``None`` if not configured."""
    entry = load_symbol_config().get("symbols", {}).get(_base_of(base))
    if not entry:
        return None
    kws = entry.get("keywords")
    if not isinstance(kws, list):
        return None
    return [str(k).strip().lower() for k in kws if str(k).strip()]


def macro_keywords() -> List[str]:
    """Shared macro / cross-asset relevance keywords applied to EVERY symbol.

    Resolution: ``config/news_symbols.yaml::defaults.macro_keywords`` (operator
    edited) -> the built-in :data:`_BUILTIN_MACRO_KEYWORDS`. These let a
    market-moving macro article (Fed, inflation, the dollar, risk sentiment)
    register non-zero relevance for a crypto symbol whose own keywords are
    ticker-only — i.e. general macro trends inform crypto decisions, not just
    Bitcoin-specific headlines.
    """
    raw = load_symbol_config().get("defaults", {}).get("macro_keywords")
    if not isinstance(raw, list):
        return list(_BUILTIN_MACRO_KEYWORDS)
    kws = [str(k).strip().lower() for k in raw if str(k).strip()]
    return kws or list(_BUILTIN_MACRO_KEYWORDS)


def macro_relevance_weight() -> float:
    """Partial relevance (0..1) a macro-only article gets for a symbol.

    Reads ``defaults.macro_relevance_weight`` (default ``0.5``). Kept below 1.0
    so instrument-specific news still dominates the weighted aggregate while
    macro news contributes a real, secondary voice.
    """
    raw = load_symbol_config().get("defaults", {}).get(
        "macro_relevance_weight", _DEFAULT_MACRO_RELEVANCE_WEIGHT
    )
    try:
        w = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MACRO_RELEVANCE_WEIGHT
    return min(1.0, max(0.0, w))
