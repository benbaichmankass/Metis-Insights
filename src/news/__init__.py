"""
News-augmented trade decision layer.

This package provides a news scoring layer that adjusts trade probability
using live news sentiment. **It is NOT observe-only**: when the source is
active (``NEWS_SOURCE=rss``, or ``newsapi`` + ``NEWS_API_KEY``) the veto
(``news_score._get_veto_enabled``, default ``NEWS_VETO_ENABLED=true``) can
**skip a live trade for every account including real money** in ``pipeline.py``
before ``multi_account_execute`` (sentiment < -0.6 AND impact > 0.7). The
graduated influence-sizing hook (``runtime.news_sizing``) is the separate,
default-off (``NEWS_INFLUENCE_MODE``) reductive path. When the source is
inactive the layer is a cheap neutral no-op.

Public API
----------
get_news_score(settings, symbol_tags) -> NewsScoreResult   # full pipeline
score_news(items, settings)           -> NewsScoreResult   # score only
adjust_probability(base, result)      -> float
"""
from __future__ import annotations

from src.news.news_pipeline import get_news_score
from src.news.news_score import NewsScoreResult, adjust_probability, score_news

__all__ = [
    "get_news_score",
    "score_news",
    "adjust_probability",
    "NewsScoreResult",
]
