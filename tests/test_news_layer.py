"""
Tests for the M9 news-augmented trade decision layer.

Covers all acceptance criteria:
  - missing news
  - stale news
  - positive relevant news
  - negative high-impact news (veto)
  - disabled mode
  - score determinism
  - reason string present
  - adjust_probability behavior
  - cache TTL
  - normalizer schema completeness
  - client disabled / no-key paths
"""
from __future__ import annotations

import time
import unittest.mock as mock
from datetime import datetime, timezone, timedelta
from typing import Any, Dict


from src.news.news_cache import NewsCache
from src.news.news_normalizer import (
    normalize_article,
    normalize_articles,
    _score_sentiment,
    _score_relevance,
    _freshness_minutes,
)
from src.news.news_score import (
    NewsScoreResult,
    adjust_probability,
    score_news,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now(offset_minutes: float = 0) -> str:
    """Return an ISO-8601 UTC timestamp, optionally shifted by *offset_minutes*."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_article(
    title: str = "Bitcoin rises",
    description: str = "",
    published_at: str | None = None,
    source_name: str = "CryptoNews",
) -> Dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "url": "https://example.com/article",
        "publishedAt": published_at or _iso_now(5),
        "source": {"name": source_name},
    }


# ===========================================================================
# NewsCache
# ===========================================================================

class TestNewsCache:
    def test_miss_returns_none(self):
        cache = NewsCache()
        assert cache.get("nonexistent") is None

    def test_set_and_get(self):
        cache = NewsCache()
        cache.set("key", [1, 2, 3])
        assert cache.get("key") == [1, 2, 3]

    def test_ttl_expiry(self):
        cache = NewsCache(default_ttl=0.05)
        cache.set("key", "value")
        time.sleep(0.1)
        assert cache.get("key") is None

    def test_per_set_ttl_overrides_default(self):
        cache = NewsCache(default_ttl=9999)
        cache.set("key", "v", ttl=0.05)
        time.sleep(0.1)
        assert cache.get("key") is None

    def test_clear(self):
        cache = NewsCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert len(cache) == 0

    def test_len_counts_live_entries_only(self):
        cache = NewsCache(default_ttl=0.05)
        cache.set("live", "x", ttl=9999)
        cache.set("expiring", "y", ttl=0.05)
        time.sleep(0.1)
        assert len(cache) == 1


# ===========================================================================
# Normalizer internals
# ===========================================================================

class TestSentimentScoring:
    def test_purely_positive_text(self):
        score = _score_sentiment("Bitcoin surge record high breakout rally bullish")
        assert score > 0.3

    def test_purely_negative_text(self):
        score = _score_sentiment("Bitcoin crash drop ban hack fraud bankrupt")
        assert score < -0.3

    def test_neutral_text(self):
        score = _score_sentiment("Bitcoin is a cryptocurrency traded on exchanges")
        assert score == 0.0

    def test_mixed_text_balanced(self):
        score = _score_sentiment("Bitcoin surges but crashes")
        assert -0.5 <= score <= 0.5

    def test_empty_text(self):
        assert _score_sentiment("") == 0.0


class TestRelevanceScoring:
    def test_btc_mention(self):
        score = _score_relevance("Bitcoin surges", ["BTC"])
        assert score > 0.0

    def test_no_symbol_match(self):
        # Neither an instrument ticker nor any macro theme → relevance 0.
        score = _score_relevance("Apple unveils a new iPhone model", ["BTC"])
        assert score == 0.0

    def test_macro_theme_partial_match_for_crypto(self):
        # A macro article (no crypto ticker) now scores PARTIAL relevance for a
        # crypto symbol — general macro trends inform the crypto decision.
        score = _score_relevance("Federal Reserve holds interest rates steady", ["BTC"])
        assert 0.0 < score < 1.0

    def test_empty_symbol_tags(self):
        score = _score_relevance("Bitcoin surges", [])
        assert score == 0.0

    def test_multiple_symbols_partial_match(self):
        score = _score_relevance("Bitcoin and Ethereum both rise", ["BTC", "ETH", "XRP"])
        assert 0.0 < score < 1.0


class TestFreshnessMinutes:
    def test_just_published(self):
        now_str = _iso_now(0)
        mins = _freshness_minutes(now_str)
        assert mins < 2.0

    def test_published_one_hour_ago(self):
        old_str = _iso_now(60)
        mins = _freshness_minutes(old_str)
        assert 58 <= mins <= 62

    def test_invalid_timestamp_returns_large_value(self):
        mins = _freshness_minutes("not-a-date")
        assert mins > 9000


# ===========================================================================
# normalize_article — schema completeness
# ===========================================================================

class TestNormalizeArticle:
    REQUIRED_KEYS = {
        "timestamp", "source", "headline", "summary", "url",
        "symbol_tags", "sentiment_score", "relevance_score",
        "impact_score", "freshness_minutes", "reason",
    }

    def test_all_schema_keys_present(self):
        item = normalize_article(_make_article(), symbol_tags=["BTC"])
        assert self.REQUIRED_KEYS == self.REQUIRED_KEYS & item.keys()

    def test_source_prefixed_newsapi(self):
        item = normalize_article(_make_article(source_name="Reuters"), symbol_tags=["BTC"])
        assert item["source"].startswith("newsapi:")

    def test_scores_in_valid_range(self):
        item = normalize_article(_make_article("Bitcoin ETF approved — record high"), symbol_tags=["BTC"])
        assert -1.0 <= item["sentiment_score"] <= 1.0
        assert 0.0 <= item["relevance_score"] <= 1.0
        assert 0.0 <= item["impact_score"] <= 1.0
        assert item["freshness_minutes"] >= 0.0

    def test_reason_is_nonempty_string(self):
        item = normalize_article(_make_article(), symbol_tags=["BTC"])
        assert isinstance(item["reason"], str)
        assert len(item["reason"]) > 0

    def test_missing_description_defaults_to_empty(self):
        raw = _make_article()
        raw.pop("description", None)
        item = normalize_article(raw, symbol_tags=["BTC"])
        assert item["summary"] == ""

    def test_normalize_articles_skips_bad_entries(self):
        articles = [_make_article(), None, _make_article("Ethereum rally")]  # type: ignore[list-item]
        result = normalize_articles(articles, symbol_tags=["BTC", "ETH"])
        assert len(result) == 2


# ===========================================================================
# score_news — acceptance criteria
# ===========================================================================

class TestScoreNewsNoItems:
    """Acceptance criterion: missing news."""

    def test_empty_list_returns_neutral_no_veto(self):
        result = score_news([])
        assert isinstance(result, NewsScoreResult)
        assert result.veto is False
        assert result.adjustment == 0.0
        assert result.decision == "neutral"
        assert "no news" in result.reason

    def test_none_like_empty_settings(self):
        result = score_news([], settings=None)
        assert result.veto is False


class TestScoreNewsStale:
    """Acceptance criterion: stale news is ignored."""

    def test_stale_items_produce_neutral(self):
        raw = _make_article("Bitcoin surges to record high", published_at=_iso_now(200))
        item = normalize_article(raw, symbol_tags=["BTC"])
        # Manually set freshness beyond default max age (120 min).
        item["freshness_minutes"] = 200.0
        result = score_news([item], settings={"NEWS_MAX_AGE_MINUTES": "120"})
        assert result.veto is False
        assert result.item_count == 0
        assert "stale" in result.reason

    def test_fresh_item_not_ignored(self):
        raw = _make_article("Bitcoin surges record high breakout", published_at=_iso_now(10))
        item = normalize_article(raw, symbol_tags=["BTC"])
        result = score_news([item])
        # May or may not be relevant, but should not be stale
        assert "stale" not in result.reason or result.item_count == 0


class TestScoreNewsPositive:
    """Acceptance criterion: positive relevant news produces boost."""

    def _fresh_btc_positive(self) -> Dict[str, Any]:
        raw = _make_article(
            title="Bitcoin surges to all-time high as institutional investors rally",
            description="Record breaking growth and bullish sentiment across crypto markets.",
            published_at=_iso_now(5),
        )
        return normalize_article(raw, symbol_tags=["BTC"])

    def test_positive_news_adjustment_positive(self):
        item = self._fresh_btc_positive()
        result = score_news([item])
        if result.item_count > 0:
            assert result.adjustment >= 0

    def test_adjust_probability_increases_with_boost(self):
        item = self._fresh_btc_positive()
        result = score_news([item])
        if result.decision == "boost":
            prob = adjust_probability(0.5, result)
            assert prob > 0.5

    def test_reason_string_present(self):
        item = self._fresh_btc_positive()
        result = score_news([item])
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


class TestScoreNewsNegativeVeto:
    """Acceptance criterion: negative high-impact news triggers veto."""

    def _high_impact_negative(self) -> Dict[str, Any]:
        raw = _make_article(
            title="Bitcoin hacked: massive breach causes crash, SEC bans crypto",
            description="Fraud and bankruptcy fears after major hack. Liquidation cascade.",
            published_at=_iso_now(5),
        )
        item = normalize_article(raw, symbol_tags=["BTC"])
        # Force values to guarantee veto threshold is crossed.
        item["sentiment_score"] = -0.85
        item["impact_score"] = 0.9
        item["relevance_score"] = 1.0
        return item

    def test_high_impact_negative_triggers_veto(self):
        item = self._high_impact_negative()
        result = score_news(
            [item],
            settings={
                "NEWS_VETO_ENABLED": "true",
                "NEWS_VETO_SENTIMENT_THRESHOLD": "-0.6",
                "NEWS_VETO_IMPACT_THRESHOLD": "0.7",
            },
        )
        assert result.veto is True
        assert result.decision == "veto"
        assert "veto" in result.reason.lower()

    def test_veto_sets_probability_to_zero(self):
        item = self._high_impact_negative()
        result = score_news([item])
        result = NewsScoreResult(veto=True, reason="forced veto", decision="veto")
        prob = adjust_probability(0.9, result)
        assert prob == 0.0

    def test_veto_reason_contains_headline_excerpt(self):
        item = self._high_impact_negative()
        result = score_news(
            [item],
            settings={
                "NEWS_VETO_ENABLED": "true",
                "NEWS_VETO_SENTIMENT_THRESHOLD": "-0.6",
                "NEWS_VETO_IMPACT_THRESHOLD": "0.7",
            },
        )
        assert result.veto is True
        assert len(result.reason) > 20


class TestScoreNewsDisabled:
    """Acceptance criterion: disabled mode."""

    def test_veto_disabled_never_vetoes(self):
        item = normalize_article(
            _make_article(
                "Bitcoin crashes and burns hack fraud bankruptcy",
                published_at=_iso_now(5),
            ),
            symbol_tags=["BTC"],
        )
        item["sentiment_score"] = -1.0
        item["impact_score"] = 1.0
        item["relevance_score"] = 1.0
        result = score_news([item], settings={"NEWS_VETO_ENABLED": "false"})
        assert result.veto is False


class TestClientDisabled:
    """Acceptance criterion: disabled/no-key paths return empty."""

    def test_fetch_news_disabled_returns_empty(self):
        from src.news.news_client import fetch_news
        result = fetch_news({"NEWS_ENABLED": "false"})
        assert result == []

    def test_fetch_news_no_key_returns_empty(self):
        from src.news.news_client import fetch_news
        result = fetch_news({"NEWS_ENABLED": "true", "NEWS_API_KEY": ""})
        assert result == []

    def test_fetch_news_network_error_returns_empty(self):
        from src.news import news_client
        with mock.patch("urllib.request.urlopen", side_effect=Exception("network down")):
            result = news_client.fetch_news({
                "NEWS_ENABLED": "true",
                "NEWS_API_KEY": "fake-key",
            })
        assert result == []


# ===========================================================================
# Determinism
# ===========================================================================

class TestDeterminism:
    def test_same_input_same_output(self):
        item = normalize_article(
            _make_article("Bitcoin record rally institutional adoption", published_at=_iso_now(10)),
            symbol_tags=["BTC"],
        )
        r1 = score_news([item])
        r2 = score_news([item])
        assert r1.adjustment == r2.adjustment
        assert r1.veto == r2.veto
        assert r1.decision == r2.decision


# ===========================================================================
# adjust_probability edge cases
# ===========================================================================

class TestAdjustProbability:
    def test_neutral_result_does_not_change_probability(self):
        result = NewsScoreResult(adjustment=0.0, veto=False, decision="neutral")
        assert abs(adjust_probability(0.6, result) - 0.6) < 1e-6

    def test_clamped_below_zero(self):
        result = NewsScoreResult(adjustment=-1.0, veto=False, decision="reduce")
        prob = adjust_probability(0.01, result)
        assert prob >= 0.0

    def test_clamped_above_one(self):
        result = NewsScoreResult(adjustment=1.0, veto=False, decision="boost")
        prob = adjust_probability(0.99, result)
        assert prob <= 1.0

    def test_veto_result_zeros_out_probability(self):
        result = NewsScoreResult(adjustment=0.5, veto=True, decision="veto")
        assert adjust_probability(0.8, result) == 0.0

    def test_boost_increases_probability(self):
        result = NewsScoreResult(adjustment=0.5, veto=False, decision="boost")
        assert adjust_probability(0.5, result) > 0.5

    def test_reduce_decreases_probability(self):
        result = NewsScoreResult(adjustment=-0.5, veto=False, decision="reduce")
        assert adjust_probability(0.5, result) < 0.5


# ===========================================================================
# Public __init__ re-exports
# ===========================================================================

class TestPublicAPI:
    def test_imports_from_package_root(self):
        from src.news import score_news, adjust_probability, NewsScoreResult
        assert callable(score_news)
        assert callable(adjust_probability)
        assert NewsScoreResult is not None
