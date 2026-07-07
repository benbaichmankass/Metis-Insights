"""Tests for the IBKR per-symbol FUT/STK instrument-type resolver
(src/units/accounts/ib_instruments.py, 2026-07-07).

Covers:
  * ib_instrument_spec() reading real config/instruments.yaml `ib:` blocks
    (futures: MES/MGC/MHG; equities: the 10-symbol ETF basket).
  * The legacy-futures-map fallback when a symbol has no `ib:` block.
  * Unmapped symbols raising ValueError (never silently misrouted).
  * is_ib_equity_symbol() / ib_order_market_type() fail-safe behavior.
"""
from __future__ import annotations

import pytest

from src.units.accounts.ib_instruments import (
    IBInstrumentSpec,
    ib_instrument_spec,
    ib_order_market_type,
    is_ib_equity_symbol,
)

_ETF_SYMBOLS = (
    "SPY", "QQQ", "GLD", "IWM", "TLT", "IEF", "SLV", "USO", "GDX", "TQQQ", "QLD",
)


class TestRealConfigResolution:
    """Resolution against the actual config/instruments.yaml on disk."""

    @pytest.mark.parametrize("sym,exchange", [("MES", "CME"), ("MGC", "COMEX"), ("MHG", "COMEX")])
    def test_futures_resolve_fut(self, sym, exchange):
        spec = ib_instrument_spec(sym)
        assert spec.sec_type == "FUT"
        assert spec.exchange == exchange
        assert spec.currency == "USD"

    @pytest.mark.parametrize("sym", _ETF_SYMBOLS)
    def test_etfs_resolve_stk(self, sym):
        spec = ib_instrument_spec(sym)
        assert spec.sec_type == "STK"
        assert spec.exchange == "SMART"
        assert spec.primary_exchange  # every ETF carries a disambiguation hint
        assert spec.currency == "USD"

    def test_lowercase_symbol_normalizes(self):
        assert ib_instrument_spec("spy").sec_type == "STK"
        assert ib_instrument_spec("mes").sec_type == "FUT"

    def test_unmapped_symbol_raises(self):
        with pytest.raises(ValueError):
            ib_instrument_spec("BTCUSDT")

    def test_none_symbol_defaults_to_mes(self):
        assert ib_instrument_spec(None).symbol == "MES"


class TestLegacyFallback:
    """Symbols absent from a (fake) instruments.yaml still resolve via the
    hardcoded legacy futures map — purely additive over the pre-2026-07-07
    behavior."""

    def test_legacy_fallback_when_no_ib_block(self, monkeypatch):
        import src.units.accounts.ib_instruments as mod

        monkeypatch.setattr(mod, "_SPEC_CACHE", {})
        spec = mod.ib_instrument_spec("MGC")
        assert spec.sec_type == "FUT"
        assert spec.exchange == "COMEX"

    def test_legacy_fallback_still_refuses_unknown(self, monkeypatch):
        import src.units.accounts.ib_instruments as mod

        monkeypatch.setattr(mod, "_SPEC_CACHE", {})
        with pytest.raises(ValueError):
            mod.ib_instrument_spec("SPY")  # no `ib:` block AND not in the legacy map


class TestIsIbEquitySymbol:
    def test_etf_is_equity(self):
        assert is_ib_equity_symbol("SPY") is True

    def test_future_is_not_equity(self):
        assert is_ib_equity_symbol("MES") is False

    def test_unknown_symbol_fails_safe_to_false(self):
        assert is_ib_equity_symbol("BTCUSDT") is False
        assert is_ib_equity_symbol(None) is False


class TestIbOrderMarketType:
    def test_equity_symbol_overrides_default(self):
        assert ib_order_market_type("SPY", default="futures") == "equity"

    def test_futures_symbol_keeps_default(self):
        assert ib_order_market_type("MES", default="futures") == "futures"

    def test_unknown_symbol_keeps_default(self):
        assert ib_order_market_type("BTCUSDT", default="spot") == "spot"


def test_ib_instrument_spec_is_frozen_dataclass():
    spec = IBInstrumentSpec(symbol="SPY", sec_type="STK", exchange="SMART")
    with pytest.raises(Exception):
        spec.symbol = "QQQ"  # type: ignore[misc]
