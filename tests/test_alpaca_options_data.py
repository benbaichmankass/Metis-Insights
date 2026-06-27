"""Tests for the read-only Alpaca options data client's pure helpers.

Network paths are not exercised here (no creds in CI) — these cover the
static parsing helpers used by the Phase-0 probe and future strike selection.
"""
from __future__ import annotations

from src.units.accounts.alpaca_options_data import AlpacaOptionsData


def test_greeks_present_counts():
    payload = {
        "snapshots": {
            "XLF260116C00054000": {"greeks": {"delta": 0.5}, "impliedVolatility": 0.18},
            "XLF260116C00055000": {"greeks": None, "impliedVolatility": None},
            "XLF260116C00056000": {"impliedVolatility": 0.2},  # IV, no greeks
        }
    }
    summary = AlpacaOptionsData.greeks_present(payload)
    assert summary == {"total": 3, "with_greeks": 1, "with_iv": 2}


def test_greeks_present_empty():
    assert AlpacaOptionsData.greeks_present({}) == {"total": 0, "with_greeks": 0, "with_iv": 0}
    assert AlpacaOptionsData.greeks_present({"snapshots": {}}) == {
        "total": 0, "with_greeks": 0, "with_iv": 0
    }


def test_quote_mid_normal():
    assert AlpacaOptionsData.quote_mid({"latestQuote": {"bp": 0.44, "ap": 0.46}}) == 0.45


def test_quote_mid_one_sided():
    # Only an ask present -> use it rather than returning None.
    assert AlpacaOptionsData.quote_mid({"latestQuote": {"bp": 0, "ap": 0.46}}) == 0.46


def test_quote_mid_unquotable():
    assert AlpacaOptionsData.quote_mid({"latestQuote": {"bp": 0, "ap": 0}}) is None
    assert AlpacaOptionsData.quote_mid({}) is None
    assert AlpacaOptionsData.quote_mid({"latestQuote": {"bp": "x", "ap": "y"}}) is None


def test_default_feed_is_indicative_free_tier():
    # No creds needed to construct; default feed matches the free subscription.
    c = AlpacaOptionsData(api_key="k", api_secret="s")
    assert c.feed == "indicative"
