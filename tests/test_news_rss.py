"""Tests for the RSS/Atom news source (M9 free real-time feed)."""
from __future__ import annotations

from datetime import datetime, timezone

from src.news import news_client_rss as rss
from src.news import news_feeds
from src.news import news_pipeline
from src.news.news_client import is_active


def _rss_doc(pubdate: str) -> bytes:
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>Test Feed</title>"
        "<item><title>Bitcoin surges to record high</title>"
        "<description>BTC rallied on ETF inflows.</description>"
        "<link>https://example.com/a</link>"
        f"<pubDate>{pubdate}</pubDate></item>"
        "<item><title>Fed signals rate cut</title>"
        "<description>S&amp;P 500 jumps.</description>"
        "<link>https://example.com/b</link>"
        f"<pubDate>{pubdate}</pubDate></item>"
        "</channel></rss>"
    ).encode("utf-8")


_ATOM_DOC = (
    '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
    "<title>Atom Feed</title>"
    "<entry><title>Gold hits record</title>"
    "<summary>Bullion rallies on safe-haven demand.</summary>"
    '<link rel="alternate" href="https://example.com/g"/>'
    "<updated>2026-06-09T12:00:00Z</updated></entry>"
    "</feed>"
).encode("utf-8")


# ── parser ──────────────────────────────────────────────────────────────────

def test_parse_rss_items():
    items = rss._parse_feed(_rss_doc("Mon, 09 Jun 2026 12:00:00 GMT"), "example.com", 25)
    assert len(items) == 2
    a = items[0]
    assert a["title"] == "Bitcoin surges to record high"
    assert a["url"] == "https://example.com/a"
    assert a["source"]["name"] == "example.com"
    assert a["publishedAt"].startswith("2026-06-09T12:00:00")


def test_parse_atom_entries():
    items = rss._parse_feed(_ATOM_DOC, "example.com", 25)
    assert len(items) == 1
    assert items[0]["title"] == "Gold hits record"
    assert items[0]["url"] == "https://example.com/g"
    assert items[0]["publishedAt"].startswith("2026-06-09T12:00:00")


def test_rfc822_date_to_iso():
    assert rss._to_iso("Mon, 09 Jun 2026 14:30:00 GMT").startswith("2026-06-09T14:30:00")


def test_parse_caps_items():
    items = rss._parse_feed(_rss_doc("Mon, 09 Jun 2026 12:00:00 GMT"), "x", 1)
    assert len(items) == 1


def test_malformed_doc_returns_empty():
    assert rss._parse_feed(b"<not xml", "x", 25) == []


# ── feed resolution ─────────────────────────────────────────────────────────

def test_feeds_for_tags_includes_global_and_class(monkeypatch):
    cfg = {
        "defaults": {},
        "groups": {"global": ["g1"], "crypto": ["c1", "c2"], "equities": ["e1"]},
        "symbol_groups": {"BTC": ["crypto"], "MES": ["equities"]},
    }
    monkeypatch.setattr(news_feeds, "load_feeds_config", lambda: cfg)
    btc = news_feeds.feeds_for_tags(["BTC", "BTCUSDT"])
    assert "g1" in btc and "c1" in btc and "c2" in btc and "e1" not in btc
    mes = news_feeds.feeds_for_tags(["MES"])
    assert "g1" in mes and "e1" in mes and "c1" not in mes
    # unknown symbol still gets global
    assert news_feeds.feeds_for_tags(["ZZZ"]) == ["g1"]


# ── fetch + pipeline wiring (no network) ────────────────────────────────────

def test_fetch_news_rss_disabled_returns_empty():
    assert rss.fetch_news_rss({"NEWS_ENABLED": "false"}, ["BTC"]) == []


def test_fetch_news_rss_aggregates(monkeypatch):
    monkeypatch.setattr(rss, "feeds_for_tags", lambda tags: ["http://f1", "http://f2"])
    monkeypatch.setattr(rss, "_fetch_one", lambda url, t, cap: [{"title": url}])
    rss.get_cache().clear()
    arts = rss.fetch_news_rss({"NEWS_ENABLED": "true", "NEWS_CACHE_TTL": "0"}, ["BTC"])
    assert {a["title"] for a in arts} == {"http://f1", "http://f2"}


def test_pipeline_uses_rss_when_selected(monkeypatch):
    captured = {}

    def fake_rss(settings, symbol_tags=None):
        captured["called"] = True
        # one fresh, relevant BTC article
        ts = datetime.now(timezone.utc).isoformat()
        return [{"title": "Bitcoin crashes hard", "description": "BTC plunges, selloff",
                 "url": "u", "publishedAt": ts, "source": {"name": "x"}}]

    monkeypatch.setattr(news_pipeline, "fetch_news_rss", fake_rss)
    result = news_pipeline.get_news_score(
        {"NEWS_ENABLED": "true", "NEWS_SOURCE": "rss"}, symbol_tags=["BTC"]
    )
    assert captured.get("called") is True
    assert result.item_count >= 1  # fresh + relevant -> actually scored


def test_is_active_rss_needs_no_key(monkeypatch):
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    assert is_active({"NEWS_ENABLED": "true", "NEWS_SOURCE": "rss"}) is True
    assert is_active({"NEWS_ENABLED": "true", "NEWS_SOURCE": "newsapi"}) is False
    assert is_active({"NEWS_ENABLED": "false", "NEWS_SOURCE": "rss"}) is False


def test_news_source_selector():
    assert news_pipeline._news_source({"NEWS_SOURCE": "rss"}) == "rss"
    assert news_pipeline._news_source({"NEWS_SOURCE": "bogus"}) == "newsapi"
    assert news_pipeline._news_source({}) == "newsapi"
