"""News feed selection is DERIVED from instrument classification.

Regression cover for BL-20260807-NEWS-FEED-SYMBOL-COVERAGE-5-OF-24: the news
layer used to select feeds from a hand-maintained per-symbol map running
parallel to instruments.yaml. It drifted to 5 of 24 traded bases, so 19 symbols
— every non-BTC/ETH crypto among them — read macro-only headlines while the
news veto was armed and able to block real-money trades.

The bug was invisible because falling through to ``global`` is indistinguishable
from being correctly assigned to ``global``. These tests pin the distinction.
"""
from __future__ import annotations

import pytest

from src.core.instrument_class import (
    asset_class_for_symbol,
    base_of,
    news_group_for_symbol,
)
from src.news.news_feeds import feeds_for_tags, groups_for_tags


# ── the classifier ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("symbol,expected", [
    ("XRPUSDT", "XRP"), ("SOLUSDT", "SOL"), ("BTCUSDT", "BTC"),
    ("SOL/ETH", "SOL"), ("SPY", "SPY"), ("", ""),
])
def test_base_of(symbol, expected):
    assert base_of(symbol) == expected


def test_news_group_defaults_to_asset_class():
    assert asset_class_for_symbol("XRPUSDT") == "crypto"
    assert news_group_for_symbol("XRPUSDT") == "crypto"


def test_news_group_override_wins_over_asset_class():
    """USO is `commodity` (metals desk) but its news is ENERGY.

    The narrow escape hatch that keeps instruments.yaml the single registry.
    """
    assert asset_class_for_symbol("USO") == "commodity"
    assert news_group_for_symbol("USO") == "energy"


def test_unknown_symbol_yields_no_group_not_a_wrong_one():
    assert news_group_for_symbol("ZZZZ_NOT_AN_INSTRUMENT") is None


# ── the regression itself ────────────────────────────────────────────────────

@pytest.mark.parametrize("symbol", ["XRPUSDT", "ADAUSDT", "SOLUSDT", "AVAXUSDT"])
def test_every_crypto_reaches_the_crypto_feeds(symbol):
    """THE regression. These four were the ones reading macro-only headlines.

    None of them appeared in the old per-symbol map, so XRP's top-scored
    article was a Social Security story while the veto was armed.
    """
    assert "crypto" in groups_for_tags([symbol]), (
        f"{symbol} must reach the crypto feeds — this is the exact 5-of-24 gap"
    )


def test_global_is_always_included():
    """Macro applies to everything — it is a floor, never a substitute."""
    for sym in ("XRPUSDT", "SPY", "USO", "TLT"):
        assert groups_for_tags([sym])[0] == "global"


def test_bonds_are_macro_only_on_purpose():
    """TLT/IEF resolve to global-only, and that is CORRECT, not a gap.

    Rates/Fed/inflation news IS the macro feed for a bond ETF. Pinned so a
    future reader does not "fix" it by inventing a bond feed group.
    """
    for sym in ("TLT", "IEF"):
        assert asset_class_for_symbol(sym) == "bond"
        assert groups_for_tags([sym]) == ["global"]


def test_energy_override_routes_to_the_oil_desk():
    assert "energy" in groups_for_tags(["USO"])
    assert "metals" not in groups_for_tags(["USO"])


def test_metals_and_equities_route_by_class():
    assert "metals" in groups_for_tags(["GLD"])
    assert "equities" in groups_for_tags(["SPY"])


def test_multiple_tags_union_their_groups():
    groups = groups_for_tags(["XRPUSDT", "GLD", "USO"])
    assert {"global", "crypto", "metals", "energy"} <= set(groups)


def test_groups_and_urls_are_deduplicated():
    groups = groups_for_tags(["BTCUSDT", "ETHUSDT", "XRPUSDT"])
    assert len(groups) == len(set(groups))
    urls = feeds_for_tags(["BTCUSDT", "ETHUSDT", "XRPUSDT"])
    assert len(urls) == len(set(urls))


def test_crypto_symbols_get_more_feeds_than_macro_only_ones():
    """A behavioural check on URLs, not just group names.

    If this ever fails with equality, the crypto group has been emptied and
    every crypto symbol is silently back on macro-only.
    """
    assert len(feeds_for_tags(["XRPUSDT"])) > len(feeds_for_tags(["TLT"]))


# ── degradation ──────────────────────────────────────────────────────────────

def test_no_tags_yields_global_only():
    assert groups_for_tags([]) == ["global"]
    assert groups_for_tags(None) == ["global"]


def test_classifier_failure_degrades_to_global_rather_than_raising(monkeypatch):
    """This runs inside the per-signal news fetch — it must never raise."""
    import src.core.instrument_class as ic

    def _boom(_symbol):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(ic, "news_group_for_symbol", _boom)
    assert groups_for_tags(["XRPUSDT"]) == ["global"]
