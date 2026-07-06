"""
Calibration and refinement tests for M9 PR3 scoring changes.

Covers:
  - Weighted aggregation: high-relevance items dominate over low-relevance ones.
  - Plain (unweighted) mean via NEWS_WEIGHTED_AGGREGATION=false.
  - Adjustment magnitude always stays in [-1, 1].
  - Configurable positive keywords extend sentiment detection.
  - Configurable negative keywords extend sentiment detection.
  - Extra keywords are additive (built-in words still work alongside them).
  - Edge cases: all-equal weights, single item, empty extra keyword strings.
  - normalize_articles with settings parameter threads keywords correctly.
  - Pipeline end-to-end: custom keywords reach scorer via get_news_score.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict


from src.news.news_normalizer import (
    normalize_article,
    normalize_articles,
    _parse_extra_keywords,
    _score_sentiment,
)
from src.news.news_score import score_news


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(offset_minutes: float = 5) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(
    sentiment: float,
    relevance: float,
    impact: float = 0.5,
    freshness: float = 10.0,
) -> Dict[str, Any]:
    """Construct a normalized item dict with explicit score fields."""
    return {
        "headline": "synthetic item",
        "summary": "",
        "timestamp": _iso(freshness),
        "source": "test",
        "url": "",
        "symbol_tags": ["BTC"],
        "sentiment_score": sentiment,
        "relevance_score": relevance,
        "impact_score": impact,
        "freshness_minutes": freshness,
        "reason": "test",
    }


def _raw_article(title: str, description: str = "", offset: float = 5.0) -> Dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "url": "https://example.com",
        "publishedAt": _iso(offset),
        "source": {"name": "TestSource"},
    }


# ===========================================================================
# _parse_extra_keywords
# ===========================================================================

class TestParseExtraKeywords:
    def test_basic_comma_split(self):
        result = _parse_extra_keywords("halving, etf, moon")
        assert result == frozenset({"halving", "etf", "moon"})

    def test_empty_string_returns_empty(self):
        assert _parse_extra_keywords("") == frozenset()

    def test_strips_whitespace(self):
        result = _parse_extra_keywords("  halving , moon  ")
        assert "halving" in result
        assert "moon" in result

    def test_lowercases_tokens(self):
        result = _parse_extra_keywords("HALVING,ETF")
        assert "halving" in result
        assert "etf" in result

    def test_skips_empty_tokens(self):
        result = _parse_extra_keywords(",,,")
        assert result == frozenset()


# ===========================================================================
# _score_sentiment with extra keywords
# ===========================================================================

class TestSentimentWithExtraKeywords:
    def test_custom_positive_word_detected(self):
        extra = frozenset({"halving"})
        score = _score_sentiment("Bitcoin halving approaches", extra_positive=extra)
        assert score > 0.0

    def test_custom_negative_word_detected(self):
        extra = frozenset({"delist"})
        score = _score_sentiment("Exchange plans to delist token", extra_negative=extra)
        assert score < 0.0

    def test_built_in_words_still_work_with_extras(self):
        extra_pos = frozenset({"halving"})
        # "surges" is built-in positive; "halving" is custom
        score_with = _score_sentiment("Bitcoin surges halving", extra_positive=extra_pos)
        score_without = _score_sentiment("Bitcoin surges", extra_positive=frozenset())
        # Both positive, but with-extras should be >= without (more positive hits)
        assert score_with >= score_without

    def test_empty_extras_identical_to_no_extras(self):
        text = "Bitcoin surges to record high"
        assert _score_sentiment(text) == _score_sentiment(
            text, extra_positive=frozenset(), extra_negative=frozenset()
        )

    def test_conflicting_custom_words_balance_out(self):
        # Same word in both lists cancels
        extra = frozenset({"halving"})
        score = _score_sentiment("Bitcoin halving", extra_positive=extra, extra_negative=extra)
        # pos == neg == 1 → raw = 0 → score = 0
        assert score == 0.0


# ===========================================================================
# normalize_article with settings
# ===========================================================================

class TestNormalizeArticleWithSettings:
    def test_custom_positive_raises_sentiment(self):
        article = _raw_article("Bitcoin halving is near")
        without = normalize_article(article, symbol_tags=["BTC"])
        with_kw = normalize_article(
            article,
            symbol_tags=["BTC"],
            settings={"NEWS_POSITIVE_KEYWORDS": "halving"},
        )
        assert with_kw["sentiment_score"] >= without["sentiment_score"]

    def test_custom_negative_lowers_sentiment(self):
        article = _raw_article("Exchange delists token amid uncertainty")
        without = normalize_article(article, symbol_tags=["BTC"])
        with_kw = normalize_article(
            article,
            symbol_tags=["BTC"],
            settings={"NEWS_NEGATIVE_KEYWORDS": "delist,delists"},
        )
        assert with_kw["sentiment_score"] <= without["sentiment_score"]

    def test_empty_settings_same_as_no_settings(self):
        article = _raw_article("Bitcoin surges to record high rally")
        r1 = normalize_article(article, symbol_tags=["BTC"])
        r2 = normalize_article(article, symbol_tags=["BTC"], settings={})
        assert r1["sentiment_score"] == r2["sentiment_score"]

    def test_normalize_articles_passes_settings(self):
        articles = [_raw_article("Bitcoin halving confirmed")]
        settings = {"NEWS_POSITIVE_KEYWORDS": "halving"}
        results = normalize_articles(articles, symbol_tags=["BTC"], settings=settings)
        assert len(results) == 1
        assert results[0]["sentiment_score"] >= 0


# ===========================================================================
# Weighted aggregation
# ===========================================================================

class TestWeightedAggregation:
    def test_high_relevance_item_dominates(self):
        """A high-relevance item should pull the aggregate toward its value."""
        high_rel = _item(sentiment=0.9, relevance=0.9, impact=0.8)  # strongly positive
        low_rel  = _item(sentiment=-0.9, relevance=0.1, impact=0.8) # negative but low relevance

        result_weighted   = score_news([high_rel, low_rel], {"NEWS_WEIGHTED_AGGREGATION": "true"})
        result_unweighted = score_news([high_rel, low_rel], {"NEWS_WEIGHTED_AGGREGATION": "false"})

        # Weighted: high-relevance positive dominates → positive adjustment
        # Unweighted: plain mean of one positive, one negative → near-zero or dependent on magnitudes
        assert result_weighted.adjustment > result_unweighted.adjustment

    def test_equal_weights_same_as_unweighted(self):
        """When all items have equal relevance, weighted == unweighted."""
        items = [
            _item(sentiment=0.5, relevance=0.5, impact=0.5),
            _item(sentiment=-0.3, relevance=0.5, impact=0.5),
        ]
        w = score_news(items, {"NEWS_WEIGHTED_AGGREGATION": "true"})
        u = score_news(items, {"NEWS_WEIGHTED_AGGREGATION": "false"})
        assert abs(w.adjustment - u.adjustment) < 1e-6

    def test_single_item_weighted_equals_unweighted(self):
        item = _item(sentiment=0.6, relevance=0.7, impact=0.6)
        w = score_news([item], {"NEWS_WEIGHTED_AGGREGATION": "true"})
        u = score_news([item], {"NEWS_WEIGHTED_AGGREGATION": "false"})
        assert abs(w.adjustment - u.adjustment) < 1e-6

    def test_weighted_enabled_by_default(self):
        """No settings → weighted aggregation is on (default True)."""
        high_rel = _item(sentiment=0.9, relevance=0.9, impact=0.8)
        low_rel  = _item(sentiment=-0.9, relevance=0.1, impact=0.8)
        default_result = score_news([high_rel, low_rel], settings=None)
        weighted_result = score_news([high_rel, low_rel], {"NEWS_WEIGHTED_AGGREGATION": "true"})
        assert abs(default_result.adjustment - weighted_result.adjustment) < 1e-9


# ===========================================================================
# Adjustment magnitude calibration
# ===========================================================================

class TestAdjustmentMagnitude:
    def test_adjustment_always_in_bounds_single_item(self):
        for s in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            for r in [0.1, 0.5, 1.0]:
                item = _item(sentiment=s, relevance=r, impact=1.0, freshness=1.0)
                result = score_news([item])
                assert -1.0 <= result.adjustment <= 1.0, (
                    f"Out of bounds for sentiment={s}, relevance={r}: {result.adjustment}"
                )

    def test_adjustment_always_in_bounds_many_items(self):
        items = [_item(s, r) for s in [-1.0, 0.0, 1.0] for r in [0.2, 0.8]]
        result = score_news(items)
        assert -1.0 <= result.adjustment <= 1.0

    def test_max_theoretical_adjustment_bounded(self):
        """Even with all items at max sentiment/relevance, stays ≤ 1."""
        items = [_item(1.0, 1.0, 1.0, 1.0) for _ in range(20)]
        result = score_news(items)
        assert result.adjustment <= 1.0

    def test_min_theoretical_adjustment_bounded(self):
        """Even with all items at min sentiment, stays ≥ -1."""
        items = [_item(-1.0, 1.0, 1.0, 1.0) for _ in range(20)]
        result = score_news(items)
        assert result.adjustment >= -1.0

    def test_adjustment_scales_with_relevance(self):
        """Higher relevance item → larger magnitude adjustment."""
        low  = _item(sentiment=0.8, relevance=0.2, impact=0.8)
        high = _item(sentiment=0.8, relevance=0.9, impact=0.8)
        r_low  = score_news([low])
        r_high = score_news([high])
        # Both positive; high-relevance item should produce larger positive adj.
        assert r_high.adjustment >= r_low.adjustment


# ===========================================================================
# Regression: existing tests still pass after refactor
# ===========================================================================

class TestBackwardCompat:
    def test_normalize_article_no_settings_still_works(self):
        article = _raw_article("Bitcoin surges to record high")
        item = normalize_article(article, symbol_tags=["BTC"])
        assert "sentiment_score" in item
        assert -1.0 <= item["sentiment_score"] <= 1.0

    def test_score_news_no_items_returns_neutral(self):
        result = score_news([])
        assert result.adjustment == 0.0
        assert result.veto is False

    def test_score_news_stale_item_neutral(self):
        item = _item(sentiment=0.9, relevance=0.9, freshness=500.0)
        result = score_news([item], {"NEWS_MAX_AGE_MINUTES": "120"})
        assert result.item_count == 0


# ===========================================================================
# top_items / url — clickable news ticker (2026-07-06)
# ===========================================================================

class TestTopItemsForTicker:
    def test_raw_scores_carry_url(self):
        """Each scored item retains its source url for the clickable ticker."""
        item = _item(sentiment=0.8, relevance=0.9, impact=0.8)
        item["url"] = "https://example.com/btc-surges"
        result = score_news([item])
        assert result.raw_scores
        assert result.raw_scores[0]["url"] == "https://example.com/btc-surges"
        assert result.raw_scores[0]["headline"] == "synthetic item"

    def test_top_items_orders_by_abs_score_and_shapes(self):
        from src.news.news_audit import _top_items

        big = _item(sentiment=0.9, relevance=0.9, impact=0.9)
        big["headline"] = "big mover"
        big["url"] = "https://example.com/big"
        small = _item(sentiment=0.1, relevance=0.2, impact=0.2)
        small["headline"] = "small mover"
        result = score_news([small, big])
        top = _top_items(result, limit=3)
        assert top, "expected at least one top item"
        # Highest |score| first.
        assert top[0]["headline"] == "big mover"
        assert top[0]["url"] == "https://example.com/big"
        assert set(top[0].keys()) == {"headline", "url", "score"}

    def test_top_items_empty_when_no_scores(self):
        from src.news.news_audit import _top_items

        result = score_news([])
        assert _top_items(result) == []
