"""
Normalize raw NewsAPI articles into the internal news schema.

Internal schema (all fields always present):
  timestamp        : ISO-8601 UTC string of publication time
  source           : "newsapi"
  headline         : article title
  summary          : article description or "" if absent
  url              : article URL
  symbol_tags      : list[str] of matched trading symbols found in text
  sentiment_score  : float in [-1.0, 1.0]; positive = bullish, negative = bearish
  relevance_score  : float in [0.0, 1.0]; how closely the article relates to the symbol
  impact_score     : float in [0.0, 1.0]; expected market-impact magnitude
  freshness_minutes: float; minutes elapsed since publication (0 = just published)
  reason           : human-readable string explaining the scores

Sentiment is computed from a curated keyword list.  No external NLP
dependency is required — this keeps the module importable in all environments.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Sentiment keyword dictionaries
# ---------------------------------------------------------------------------

_POSITIVE_WORDS: frozenset[str] = frozenset({
    "surge", "surges", "surged", "rally", "rallies", "rallied",
    "gain", "gains", "gained", "bullish", "bull", "breakout",
    "record", "high", "all-time", "ath", "buy", "bought",
    "adoption", "approve", "approved", "approval", "etf",
    "launch", "launched", "partnership", "integrate", "upgrade",
    "growth", "grow", "grew", "recover", "recovery", "recovered",
    "rise", "rises", "rose", "risen", "pump", "pumps",
    "inflow", "inflows", "accumulate", "accumulation",
    "institutional", "invest", "investment", "confidence",
    "optimism", "optimistic", "positive", "profit", "profits",
    "revenue", "support", "strong", "strength",
})

_NEGATIVE_WORDS: frozenset[str] = frozenset({
    "crash", "crashes", "crashed", "drop", "drops", "dropped",
    "fall", "falls", "fell", "fallen", "bearish", "bear",
    "breakdown", "low", "plunge", "plunges", "plunged",
    "sell", "sold", "selloff", "sell-off", "dump", "dumps",
    "ban", "banned", "banning", "restrict", "restriction",
    "hack", "hacked", "exploit", "breach", "stolen", "theft",
    "fraud", "scam", "rug", "rugpull", "liquidate", "liquidation",
    "outflow", "outflows", "loss", "losses", "debt",
    "fear", "panic", "concern", "risk", "regulation",
    "fine", "lawsuit", "sec", "enforcement", "illegal",
    "insolvency", "insolvent", "bankrupt", "bankruptcy",
    "warning", "caution", "negative", "decline", "declined",
    "slump", "slumps", "slumped", "weak", "weakness",
})

# Known symbol → keyword mapping for relevance scoring.
_SYMBOL_KEYWORDS: Dict[str, List[str]] = {
    "BTC": ["bitcoin", "btc", "satoshi"],
    "ETH": ["ethereum", "eth", "ether"],
    "BNB": ["bnb", "binance coin"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
    "MATIC": ["polygon", "matic"],
    "LINK": ["chainlink", "link"],
}

# High-impact headline patterns (regex, case-insensitive).
_HIGH_IMPACT_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\betf\b", re.I),
    re.compile(r"\bhack(?:ed)?\b", re.I),
    re.compile(r"\bsec\b", re.I),
    re.compile(r"\bban(?:ned)?\b", re.I),
    re.compile(r"\bregulat", re.I),
    re.compile(r"\bbankrupt", re.I),
    re.compile(r"\ball.?time high\b", re.I),
    re.compile(r"\brecord high\b", re.I),
    re.compile(r"\bliquidat", re.I),
    re.compile(r"\binstitutional\b", re.I),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """Lower-case word tokens, stripping punctuation."""
    return re.findall(r"[a-zA-Z]+", text.lower())


def _score_sentiment(text: str) -> float:
    """Return sentiment in [-1, 1] from keyword counts."""
    tokens = _tokenize(text)
    pos = sum(1 for t in tokens if t in _POSITIVE_WORDS)
    neg = sum(1 for t in tokens if t in _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    raw = (pos - neg) / total
    # Damp toward zero: a one-word signal shouldn't hit ±1.
    weight = min(1.0, total / 5.0)
    return round(max(-1.0, min(1.0, raw * weight)), 4)


def _score_relevance(text: str, symbol_tags: List[str]) -> float:
    """Return relevance in [0, 1] based on symbol keyword matches."""
    text_lower = text.lower()
    hits = 0
    for sym in symbol_tags:
        base = sym.upper().replace("USDT", "").replace("PERP", "")
        keywords = _SYMBOL_KEYWORDS.get(base, [base.lower()])
        if any(kw in text_lower for kw in keywords):
            hits += 1
    if not symbol_tags:
        return 0.0
    return round(min(1.0, hits / len(symbol_tags)), 4)


def _score_impact(text: str, sentiment: float) -> float:
    """Return impact in [0, 1]. High-impact patterns + strong sentiment → higher."""
    pattern_hits = sum(1 for p in _HIGH_IMPACT_PATTERNS if p.search(text))
    pattern_score = min(1.0, pattern_hits / 3.0)
    sentiment_weight = abs(sentiment)
    return round(min(1.0, 0.5 * pattern_score + 0.5 * sentiment_weight), 4)


def _freshness_minutes(published_at: str) -> float:
    """Minutes elapsed since *published_at* (ISO-8601 string), clamped to ≥ 0."""
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = (now - pub).total_seconds() / 60.0
        return max(0.0, round(delta, 1))
    except Exception:  # noqa: BLE001
        return 9999.0  # treat unparseable timestamps as maximally stale


def _extract_symbol_tags(text: str, candidate_symbols: Optional[List[str]] = None) -> List[str]:
    """Return which of *candidate_symbols* appear in *text*."""
    candidates = candidate_symbols or list(_SYMBOL_KEYWORDS.keys())
    text_lower = text.lower()
    found = []
    for sym in candidates:
        base = sym.upper().replace("USDT", "").replace("PERP", "")
        keywords = _SYMBOL_KEYWORDS.get(base, [base.lower()])
        if any(kw in text_lower for kw in keywords):
            found.append(base)
    return found


def _build_reason(sentiment: float, relevance: float, impact: float, freshness: float) -> str:
    parts = []
    if sentiment > 0.2:
        parts.append("positive sentiment")
    elif sentiment < -0.2:
        parts.append("negative sentiment")
    else:
        parts.append("neutral sentiment")

    if relevance >= 0.8:
        parts.append("high relevance")
    elif relevance >= 0.4:
        parts.append("moderate relevance")
    else:
        parts.append("low relevance")

    if impact >= 0.7:
        parts.append("high impact")
    elif impact >= 0.4:
        parts.append("moderate impact")

    if freshness > 120:
        parts.append("stale (>2h)")
    elif freshness > 30:
        parts.append("recent (>30min)")

    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_article(
    raw: Dict[str, Any],
    symbol_tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convert a single raw NewsAPI article dict into the internal schema.

    Parameters
    ----------
    raw:
        A single article dict as returned by NewsAPI ``/v2/everything``.
    symbol_tags:
        Trading symbols to score for relevance, e.g. ``["BTC", "BTCUSDT"]``.
        When ``None``, all known symbols are checked.

    Returns
    -------
    dict
        Fully populated internal news schema.
    """
    headline = str(raw.get("title") or "")
    summary = str(raw.get("description") or "")
    url = str(raw.get("url") or "")
    published_at = str(raw.get("publishedAt") or "")
    source_name = str((raw.get("source") or {}).get("name") or "unknown")

    combined_text = f"{headline} {summary}"

    freshness = _freshness_minutes(published_at)
    tags = _extract_symbol_tags(combined_text, symbol_tags)
    sentiment = _score_sentiment(combined_text)
    relevance = _score_relevance(combined_text, tags if tags else (symbol_tags or []))
    impact = _score_impact(combined_text, sentiment)
    reason = _build_reason(sentiment, relevance, impact, freshness)

    return {
        "timestamp": published_at,
        "source": f"newsapi:{source_name}",
        "headline": headline,
        "summary": summary,
        "url": url,
        "symbol_tags": tags,
        "sentiment_score": sentiment,
        "relevance_score": relevance,
        "impact_score": impact,
        "freshness_minutes": freshness,
        "reason": reason,
    }


def normalize_articles(
    raw_list: List[Dict[str, Any]],
    symbol_tags: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Normalize a list of raw articles.  Articles that raise are skipped."""
    result = []
    for raw in raw_list:
        try:
            result.append(normalize_article(raw, symbol_tags=symbol_tags))
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("news: failed to normalize article — %s", exc)
    return result
