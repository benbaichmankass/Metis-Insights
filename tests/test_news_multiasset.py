"""Tests for the multi-asset news config + shadow-soak audit (M9 expansion).

Covers the move from a Bitcoin-only news layer to a per-symbol one:
  - config/news_symbols.yaml loads and resolves per-symbol queries + keywords,
  - index/commodity futures (MES, MGC, MHG) now score non-zero relevance where
    they previously fell back to the literal ticker and were dropped,
  - the shadow-soak writer is gated on layer-active and never raises.
"""
from __future__ import annotations

import json

import pytest

from src.news import news_symbols
from src.news.news_normalizer import normalize_article, _resolve_keywords
from src.news.news_score import NewsScoreResult
from src.news import news_audit
from src.news import news_client


@pytest.fixture(autouse=True)
def _fresh_config():
    """Drop the cached symbol config around each test."""
    news_symbols.reload_symbol_config()
    yield
    news_symbols.reload_symbol_config()


# --------------------------------------------------------------------------
# Config loader
# --------------------------------------------------------------------------

def test_config_loads_futures_symbols():
    cfg = news_symbols.load_symbol_config()
    symbols = cfg["symbols"]
    assert "MES" in symbols
    assert "MGC" in symbols
    assert "BTC" in symbols


def test_query_for_tags_resolves_per_symbol():
    # Equity-index future -> an S&P / macro query, not the Bitcoin default.
    q = news_symbols.query_for_tags(["MES"])
    assert q is not None
    assert "s&p" in q.lower() or "stock market" in q.lower()
    # Gold future -> a gold query.
    assert "gold" in (news_symbols.query_for_tags(["MGC"]) or "").lower()


def test_query_for_tags_unknown_returns_none():
    # No config entry -> None so the caller falls back to NEWS_QUERY / default.
    assert news_symbols.query_for_tags(["ZZZ"]) is None
    assert news_symbols.query_for_tags([]) is None


def test_query_for_tags_strips_suffix():
    # BTCUSDT must resolve to the BTC entry.
    assert news_symbols.query_for_tags(["BTCUSDT"]) == news_symbols.query_for_tags(["BTC"])


def test_keywords_for_base():
    kws = news_symbols.keywords_for_base("MGC")
    assert kws is not None
    assert "gold" in kws
    assert news_symbols.keywords_for_base("ZZZ") is None


# --------------------------------------------------------------------------
# Normalizer relevance (the no-op-for-futures bug this fixes)
# --------------------------------------------------------------------------

def test_resolve_keywords_prefers_config_then_builtin():
    assert "gold" in _resolve_keywords("MGC")          # from config
    assert "bitcoin" in _resolve_keywords("BTC")        # config or built-in
    assert _resolve_keywords("ZZZ") == ["zzz"]          # literal fallback


def test_mes_article_scores_relevance():
    raw = {
        "title": "S&P 500 jumps as Federal Reserve signals rate cut",
        "description": "Wall Street rallied after the Fed and cooling inflation.",
        "url": "http://x",
        "publishedAt": "2999-01-01T00:00:00Z",  # always fresh
        "source": {"name": "Reuters"},
    }
    item = normalize_article(raw, symbol_tags=["MES"])
    assert item["relevance_score"] > 0.0, "MES news should be relevant after multi-asset wiring"


def test_mgc_article_scores_relevance():
    raw = {
        "title": "Gold price hits record high on safe haven demand",
        "description": "Bullion rallied as the dollar weakened.",
        "url": "http://x",
        "publishedAt": "2999-01-01T00:00:00Z",
        "source": {"name": "Reuters"},
    }
    item = normalize_article(raw, symbol_tags=["MGC"])
    assert item["relevance_score"] > 0.0


def test_bitcoin_article_still_relevant():
    raw = {
        "title": "Bitcoin surges past resistance",
        "description": "BTC rallied on ETF inflows.",
        "url": "http://x",
        "publishedAt": "2999-01-01T00:00:00Z",
        "source": {"name": "CoinDesk"},
    }
    item = normalize_article(raw, symbol_tags=["BTC"])
    assert item["relevance_score"] > 0.0


# --------------------------------------------------------------------------
# Shadow-soak audit writer
# --------------------------------------------------------------------------

def test_is_active_gating(monkeypatch):
    # Activation is source-driven (no NEWS_ENABLED gate — removed 2026-06-10):
    # the default newsapi source is active iff a key is present; rss is keyless;
    # a leftover NEWS_ENABLED value is ignored.
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    assert news_client.is_active({}) is False  # newsapi default, no key
    assert news_client.is_active({"NEWS_API_KEY": "k"}) is True
    assert news_client.is_active({"NEWS_ENABLED": "false", "NEWS_API_KEY": "k"}) is True
    assert news_client.is_active({"NEWS_SOURCE": "rss"}) is True  # keyless


def test_log_news_decision_writes_line(tmp_path, monkeypatch):
    log = tmp_path / "news_decisions.jsonl"
    monkeypatch.setattr(news_audit, "news_decisions_path", lambda: log)
    result = NewsScoreResult(adjustment=-0.3, veto=False, decision="reduce", item_count=2)
    assert news_audit.log_news_decision(
        result=result, symbol="MES", side="buy", strategy="vwap", query="S&P 500"
    ) is True
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "MES"
    assert rows[0]["decision"] == "reduce"
    assert rows[0]["adjustment"] == -0.3


def test_log_news_decision_never_raises(monkeypatch):
    # A broken path resolver must not propagate.
    def _boom():
        raise RuntimeError("disk gone")
    monkeypatch.setattr(news_audit, "news_decisions_path", _boom)
    assert news_audit.log_news_decision(result=NewsScoreResult()) is False
