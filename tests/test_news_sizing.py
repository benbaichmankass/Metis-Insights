"""Tests for the execution-time news downsize wiring (M9 act layer, step 2)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.runtime import news_sizing


def _pkg(direction="long", news=None):
    meta = {}
    if news is not None:
        meta["news"] = news
    return SimpleNamespace(strategy="vwap", symbol="MES", direction=direction, meta=meta)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("NEWS_INFLUENCE_MODE", "NEWS_INFLUENCE_SIZE_FLOOR",
              "NEWS_INFLUENCE_OPPOSE_THRESHOLD", "NEWS_INFLUENCE_EVENT_RISK_WEIGHT"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_default_off_is_identity():
    pkg = _pkg(news={"adjustment": -1.0, "event_risk": 0.0})
    assert news_sizing.apply_news_downsize(pkg, 10.0, account_name="a") == 10.0


def test_annotate_does_not_resize(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "annotate")
    pkg = _pkg(news={"adjustment": -1.0})
    assert news_sizing.apply_news_downsize(pkg, 10.0) == 10.0


def test_unknown_mode_is_off(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "bogus")
    pkg = _pkg(news={"adjustment": -1.0})
    assert news_sizing.apply_news_downsize(pkg, 10.0) == 10.0


def test_downsize_opposed_news(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "downsize")
    monkeypatch.setenv("NEWS_INFLUENCE_SIZE_FLOOR", "0.5")
    # Bearish news (adj -1) on a long -> fully opposed -> floor 0.5.
    pkg = _pkg(direction="long", news={"adjustment": -1.0, "event_risk": 0.0})
    assert news_sizing.apply_news_downsize(pkg, 10.0) == pytest.approx(5.0)


def test_aligned_news_no_downsize(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "downsize")
    # Bullish news on a long -> aligned -> unchanged.
    pkg = _pkg(direction="long", news={"adjustment": 0.8})
    assert news_sizing.apply_news_downsize(pkg, 10.0) == 10.0


def test_no_news_stamp_is_identity(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "downsize")
    assert news_sizing.apply_news_downsize(_pkg(), 10.0) == 10.0


def test_factor_cached_on_meta(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "downsize")
    pkg = _pkg(direction="long", news={"adjustment": -1.0})
    news_sizing.apply_news_downsize(pkg, 10.0)
    assert pkg.meta["_news_factor"] == pytest.approx(0.5)
    assert pkg.meta["news_influence_decision"]["action"] == "downsize"


def test_never_raises_on_bad_qty(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "downsize")
    pkg = _pkg(news={"adjustment": -1.0})
    assert news_sizing.apply_news_downsize(pkg, 0.0) == 0.0
    assert news_sizing.apply_news_downsize(pkg, None) is None


def test_short_direction_maps_to_sell(monkeypatch):
    monkeypatch.setenv("NEWS_INFLUENCE_MODE", "downsize")
    # Bullish news on a short -> opposed -> downsize.
    pkg = _pkg(direction="short", news={"adjustment": 1.0})
    assert news_sizing.apply_news_downsize(pkg, 10.0) == pytest.approx(5.0)
