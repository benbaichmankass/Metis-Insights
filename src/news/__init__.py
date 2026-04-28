"""
News-augmented trade decision layer.

This package provides an additive news scoring layer that adjusts trade
probability using live news sentiment. It does not alter the strategy stack
or runtime behavior in v1; it exposes data that the pipeline can optionally
consume.

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
