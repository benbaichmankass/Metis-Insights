"""Tests for the reductive news-influence operator (graduated act layer).

Invariants:
  - default-off / annotate / off-mode -> factor 1.0 (inert),
  - opposed news downsizes toward the floor; aligned/neutral news does not,
  - an imminent event downsizes MORE when the trade is not aligned, and is
    discounted when it is,
  - the factor is always reductive (in [size_floor, 1.0], never > 1.0).
"""
from __future__ import annotations

import pytest

from src.news.news_influence import (
    NewsInfluencePolicy,
    news_size_factor,
    parse_policy,
)

_DOWNSIZE = NewsInfluencePolicy(mode="downsize", size_floor=0.5)


def test_disabled_is_identity():
    f, rec = news_size_factor(-1.0, "buy", _DOWNSIZE, flag_enabled=False)
    assert f == 1.0 and rec["action"] == "none"


def test_off_mode_is_identity():
    f, _ = news_size_factor(-1.0, "buy", NewsInfluencePolicy(mode="off"), flag_enabled=True)
    assert f == 1.0


def test_annotate_does_not_resize():
    f, rec = news_size_factor(-1.0, "buy", NewsInfluencePolicy(mode="annotate"), flag_enabled=True)
    assert f == 1.0 and rec["action"] == "annotate"


def test_aligned_news_no_downsize():
    # Bullish news on a BUY -> aligned -> no reduction.
    f, _ = news_size_factor(0.8, "buy", _DOWNSIZE, flag_enabled=True)
    assert f == 1.0
    # Bearish news on a SELL -> aligned -> no reduction.
    f2, _ = news_size_factor(-0.8, "sell", _DOWNSIZE, flag_enabled=True)
    assert f2 == 1.0


def test_opposed_news_downsizes_to_floor():
    # Fully bearish news on a BUY -> fully opposed -> floor.
    f, rec = news_size_factor(-1.0, "buy", _DOWNSIZE, flag_enabled=True)
    assert f == pytest.approx(0.5)
    assert rec["action"] == "downsize"
    # Partial opposition -> partial downsize, still > floor.
    f2, _ = news_size_factor(-0.4, "buy", _DOWNSIZE, flag_enabled=True)
    assert 0.5 < f2 < 1.0


def test_dead_band_neutral():
    f, _ = news_size_factor(-0.02, "buy", _DOWNSIZE, flag_enabled=True)
    assert f == 1.0  # below oppose_threshold


def test_event_risk_downsizes_when_not_aligned():
    # Neutral news but a big imminent event on a buy -> downsize from event alone.
    f, rec = news_size_factor(0.0, "buy", _DOWNSIZE, flag_enabled=True, event_risk=1.0)
    assert f < 1.0
    assert rec["event_component"] > 0.0


def test_event_risk_discounted_when_aligned():
    # Strongly aligned trade: the event likely pushes our way -> less reduction
    # than the same event on a neutral trade.
    aligned, _ = news_size_factor(0.9, "buy", _DOWNSIZE, flag_enabled=True, event_risk=1.0)
    neutral, _ = news_size_factor(0.0, "buy", _DOWNSIZE, flag_enabled=True, event_risk=1.0)
    assert aligned > neutral  # aligned position is shrunk less


def test_factor_always_reductive():
    for adj in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for side in ("buy", "sell"):
            for ev in (0.0, 0.5, 1.0):
                f, _ = news_size_factor(adj, side, _DOWNSIZE, flag_enabled=True, event_risk=ev)
                assert 0.5 <= f <= 1.0


def test_parse_policy_defaults_off():
    assert parse_policy(None).mode == "off"
    assert parse_policy({}).mode == "off"
    assert parse_policy({"news_influence": {"mode": "downsize", "size_floor": 0.3}}).size_floor == 0.3


def test_policy_validation():
    with pytest.raises(ValueError):
        NewsInfluencePolicy(mode="bogus")
    with pytest.raises(ValueError):
        NewsInfluencePolicy(size_floor=1.5)
