"""Tests for the economic-calendar event_risk source (M9 news influence step 3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.news import news_events


@pytest.fixture(autouse=True)
def _fresh():
    news_events.reload_calendar()
    yield
    news_events.reload_calendar()


def _cal(monkeypatch, events, classes=None):
    data = {
        "defaults": {"pre_window_minutes": 60, "post_window_minutes": 15},
        "symbol_event_classes": classes or {"MES": ["cpi", "fomc"]},
        "events": events,
    }
    monkeypatch.setattr(news_events, "load_calendar", lambda: data)


NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_no_classes_for_symbol(monkeypatch):
    _cal(monkeypatch, [], classes={"MES": ["cpi"]})
    risk, meta = news_events.event_risk_for_symbol("MGC", now=NOW)
    assert risk == 0.0
    assert meta["reason"] == "no_event_classes_for_symbol"


def test_no_imminent_event(monkeypatch):
    # Event 5 days out -> out of window -> 0.
    far = (NOW + timedelta(days=5)).isoformat()
    _cal(monkeypatch, [{"class": "cpi", "time": far, "impact": 0.9}])
    risk, _ = news_events.event_risk_for_symbol("MES", now=NOW)
    assert risk == 0.0


def test_event_at_now_is_full_impact(monkeypatch):
    _cal(monkeypatch, [{"class": "cpi", "time": NOW.isoformat(), "impact": 0.9}])
    risk, meta = news_events.event_risk_for_symbol("MES", now=NOW)
    assert abs(risk - 0.9) < 1e-6
    assert meta["class"] == "cpi"


def test_proximity_ramps(monkeypatch):
    # 30 min before a 60-min-window CPI with impact 1.0 -> proximity 0.5.
    t = (NOW + timedelta(minutes=30)).isoformat()
    _cal(monkeypatch, [{"class": "cpi", "time": t, "impact": 1.0}])
    risk, _ = news_events.event_risk_for_symbol("MES", now=NOW)
    assert abs(risk - 0.5) < 1e-6


def test_only_relevant_classes_count(monkeypatch):
    # EIA isn't in MES's classes -> ignored.
    _cal(monkeypatch, [{"class": "eia", "time": NOW.isoformat(), "impact": 1.0}],
         classes={"MES": ["cpi", "fomc"]})
    risk, _ = news_events.event_risk_for_symbol("MES", now=NOW)
    assert risk == 0.0


def test_max_over_events(monkeypatch):
    _cal(monkeypatch, [
        {"class": "cpi", "time": (NOW + timedelta(minutes=30)).isoformat(), "impact": 1.0},  # 0.5
        {"class": "fomc", "time": NOW.isoformat(), "impact": 0.8},  # 0.8
    ])
    risk, meta = news_events.event_risk_for_symbol("MES", now=NOW)
    assert abs(risk - 0.8) < 1e-6
    assert meta["class"] == "fomc"


def test_symbol_suffix_stripped(monkeypatch):
    _cal(monkeypatch, [{"class": "cpi", "time": NOW.isoformat(), "impact": 0.5}],
         classes={"BTC": ["cpi"]})
    risk, _ = news_events.event_risk_for_symbol("BTCUSDT", now=NOW)
    assert abs(risk - 0.5) < 1e-6


def test_real_config_loads_and_is_inert():
    # The shipped config has an empty events list -> risk 0 everywhere, never raises.
    news_events.reload_calendar()
    risk, _ = news_events.event_risk_for_symbol("MES")
    assert risk == 0.0
