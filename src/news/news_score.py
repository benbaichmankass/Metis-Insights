"""
News scoring and veto logic for the trade-decision layer.

Aggregates normalized news items into a single score that can nudge the
existing trade probability without overpowering it.

Score formula (per item):
    freshness_score = max(0, 1 - freshness_minutes / max_age_minutes)
    item_score      = sentiment_score * relevance_score
                      * freshness_score * impact_score

Aggregate:
    news_adjustment = mean(item_scores) over all non-stale items
    Clamped to [-1, 1]; typically much smaller in practice.

Veto:
    Any item where sentiment_score < VETO_SENTIMENT_THRESHOLD
    AND impact_score > VETO_IMPACT_THRESHOLD triggers a veto.

    Veto overrides the adjustment and signals the caller to skip the trade.

Config keys (from settings dict or env):
    NEWS_MAX_AGE_MINUTES          : items older than this are ignored (default 120)
    NEWS_VETO_ENABLED             : "true"/"false" (default "true")
    NEWS_VETO_SENTIMENT_THRESHOLD : sentiment threshold for veto (default -0.6)
    NEWS_VETO_IMPACT_THRESHOLD    : impact threshold for veto (default 0.7)

Usage
-----
    from src.news.news_score import score_news, adjust_probability

    items   = normalize_articles(raw_list, symbol_tags=["BTC"])
    result  = score_news(items, settings)

    print(result.adjustment)   # float in [-1, 1]
    print(result.veto)         # bool
    print(result.reason)       # str

    final_prob = adjust_probability(base_prob, result)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_DEFAULT_MAX_AGE_MINUTES = 120.0
_DEFAULT_VETO_SENTIMENT = -0.6
_DEFAULT_VETO_IMPACT = 0.7
_MAX_ADJUSTMENT_FACTOR = 0.15  # news can move probability by at most ±15 pp
_DEFAULT_WEIGHTED_AGGREGATION = True  # weight items by relevance_score


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class NewsScoreResult:
    """Result returned by :func:`score_news`.

    Attributes
    ----------
    adjustment:
        Numeric adjustment in ``[-1, 1]``.  Multiply by
        ``_MAX_ADJUSTMENT_FACTOR`` to get the actual probability delta.
    veto:
        When ``True`` the pipeline should skip the trade entirely.
    reason:
        Human-readable explanation of how the adjustment was derived.
    item_count:
        Number of non-stale, relevant items that contributed.
    decision:
        One of ``"boost"`` / ``"reduce"`` / ``"veto"`` / ``"neutral"``.
    raw_scores:
        Per-item score breakdown for logging.
    """
    adjustment: float = 0.0
    veto: bool = False
    reason: str = "no news"
    item_count: int = 0
    decision: str = "neutral"
    raw_scores: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_max_age(settings: dict) -> float:
    raw = settings.get("NEWS_MAX_AGE_MINUTES",
                       os.environ.get("NEWS_MAX_AGE_MINUTES", _DEFAULT_MAX_AGE_MINUTES))
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_AGE_MINUTES


def _get_veto_enabled(settings: dict) -> bool:
    raw = str(settings.get("NEWS_VETO_ENABLED",
                           os.environ.get("NEWS_VETO_ENABLED", "true"))).strip().lower()
    return raw not in {"false", "0", "no"}


def _get_veto_sentiment(settings: dict) -> float:
    raw = settings.get("NEWS_VETO_SENTIMENT_THRESHOLD",
                       os.environ.get("NEWS_VETO_SENTIMENT_THRESHOLD", _DEFAULT_VETO_SENTIMENT))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_VETO_SENTIMENT


def _get_veto_impact(settings: dict) -> float:
    raw = settings.get("NEWS_VETO_IMPACT_THRESHOLD",
                       os.environ.get("NEWS_VETO_IMPACT_THRESHOLD", _DEFAULT_VETO_IMPACT))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_VETO_IMPACT


def _get_weighted_aggregation(settings: dict) -> bool:
    raw = str(settings.get("NEWS_WEIGHTED_AGGREGATION",
                           os.environ.get("NEWS_WEIGHTED_AGGREGATION", "true"))).strip().lower()
    return raw not in {"false", "0", "no"}


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

def _item_score(
    item: Dict[str, Any],
    max_age_minutes: float,
) -> Optional[Dict[str, Any]]:
    """Score a single normalized item.  Returns None if stale or relevance=0."""
    freshness = float(item.get("freshness_minutes", 9999))
    if freshness > max_age_minutes:
        return None

    sentiment = float(item.get("sentiment_score", 0.0))
    relevance = float(item.get("relevance_score", 0.0))
    impact = float(item.get("impact_score", 0.0))

    # Items with zero relevance contribute nothing.
    if relevance == 0.0:
        return None

    freshness_score = max(0.0, 1.0 - freshness / max_age_minutes)
    score = sentiment * relevance * freshness_score * impact

    return {
        "headline": item.get("headline", ""),
        "url": item.get("url", ""),
        "sentiment": sentiment,
        "relevance": relevance,
        "freshness_score": round(freshness_score, 4),
        "impact": impact,
        "score": round(score, 6),
        "reason": item.get("reason", ""),
    }


def score_news(
    items: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> NewsScoreResult:
    """Aggregate normalized news items into a single :class:`NewsScoreResult`.

    Parameters
    ----------
    items:
        List of dicts produced by :func:`src.news.news_normalizer.normalize_article`.
    settings:
        Config/env dict (same pattern as the rest of the pipeline).

    Returns
    -------
    NewsScoreResult
        Always returns a valid result; never raises.
    """
    settings = settings or {}

    if not items:
        return NewsScoreResult(reason="no news items")

    max_age = _get_max_age(settings)
    veto_enabled = _get_veto_enabled(settings)
    veto_sentiment_thresh = _get_veto_sentiment(settings)
    veto_impact_thresh = _get_veto_impact(settings)
    weighted = _get_weighted_aggregation(settings)

    raw_scores: List[Dict[str, Any]] = []
    valid_scores: List[float] = []
    valid_weights: List[float] = []
    veto = False
    veto_reason = ""

    for item in items:
        entry = _item_score(item, max_age)
        if entry is None:
            continue
        raw_scores.append(entry)
        valid_scores.append(entry["score"])
        # Weight = relevance_score so high-relevance items dominate the aggregate.
        valid_weights.append(entry["relevance"])

        # Veto check on the raw item fields (not the compound score).
        if veto_enabled:
            sent = float(item.get("sentiment_score", 0.0))
            imp = float(item.get("impact_score", 0.0))
            if sent < veto_sentiment_thresh and imp > veto_impact_thresh:
                veto = True
                veto_reason = (
                    f"adverse news veto: sentiment={sent:.2f} impact={imp:.2f} "
                    f"— {item.get('headline', '')[:80]}"
                )

    if not valid_scores:
        return NewsScoreResult(
            reason="all news items stale or irrelevant",
            raw_scores=raw_scores,
        )

    if weighted and sum(valid_weights) > 0:
        # Weighted mean: each item's score is weighted by its relevance.
        adjustment = sum(s * w for s, w in zip(valid_scores, valid_weights)) / sum(valid_weights)
    else:
        adjustment = sum(valid_scores) / len(valid_scores)
    adjustment = max(-1.0, min(1.0, round(adjustment, 6)))

    if veto:
        decision = "veto"
        reason = veto_reason
    elif adjustment > 0.05:
        decision = "boost"
        reason = (
            f"news boost: adj={adjustment:.4f} over {len(valid_scores)} item(s); "
            + raw_scores[0]["reason"]
        )
    elif adjustment < -0.05:
        decision = "reduce"
        reason = (
            f"news reduce: adj={adjustment:.4f} over {len(valid_scores)} item(s); "
            + raw_scores[0]["reason"]
        )
    else:
        decision = "neutral"
        reason = f"news neutral: adj={adjustment:.4f} over {len(valid_scores)} item(s)"

    return NewsScoreResult(
        adjustment=adjustment,
        veto=veto,
        reason=reason,
        item_count=len(valid_scores),
        decision=decision,
        raw_scores=raw_scores,
    )


# ---------------------------------------------------------------------------
# Probability adjustment helper
# ---------------------------------------------------------------------------

def adjust_probability(base: float, result: NewsScoreResult) -> float:
    """Apply the news adjustment to a base trade probability.

    The adjustment is bounded by ``_MAX_ADJUSTMENT_FACTOR`` so news can
    nudge but never dominate the decision.

    Parameters
    ----------
    base:
        Base probability in [0.0, 1.0].
    result:
        :class:`NewsScoreResult` from :func:`score_news`.

    Returns
    -------
    float
        Adjusted probability clamped to [0.0, 1.0].
        Returns 0.0 when ``result.veto`` is True.
    """
    if result.veto:
        return 0.0
    delta = result.adjustment * _MAX_ADJUSTMENT_FACTOR
    return max(0.0, min(1.0, round(base + delta, 6)))
