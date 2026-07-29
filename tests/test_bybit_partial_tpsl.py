"""BYBIT_TPSL_MODE=partial — qty-scoped brackets (Fix 2 of
BL-20260720-ICTSCALP-PASTSTOP-EXITS).

Default ``full`` keeps the pre-fix wire format byte-identical; ``partial``
adds tpslMode/tpSize/slSize/orderTypes on placement and the qty-scoped
amend params on ``modify_open_order``.
"""
from __future__ import annotations

import pytest

from src.units.accounts.execute import (
    _bybit_open_missing_sl_leg,
    _bybit_tpsl_mode,
    _submit_order,
    modify_open_order,
)


class _ProtClient:
    """Stub for the entry-side bracket-verification helper."""

    def __init__(self, pos, legs=None, raise_pos=False, raise_oo=False):
        self._pos = pos
        self._legs = legs or []
        self._rp = raise_pos
        self._ro = raise_oo

    def get_positions(self, category=None, symbol=None):
        if self._rp:
            raise RuntimeError("pos boom")
        return {"result": {"list": [self._pos] if self._pos else []}}

    def get_open_orders(self, category=None, symbol=None, orderFilter=None):
        if self._ro:
            raise RuntimeError("oo boom")
        return {"result": {"list": self._legs}}


def test_entry_bracket_verification_detects_naked_open():
    """BL-20260729: a live position with no Full stop and no resting SL leg is
    confirmed NAKED (only a TP leg present → still naked)."""
    c = _ProtClient({"size": "100", "stopLoss": ""},
                    [{"stopOrderType": "PartialTakeProfit"}])
    assert _bybit_open_missing_sl_leg(c, "linear", "XRPUSDT") is True


def test_entry_bracket_verification_protected_paths():
    # Full-mode position stop present → not naked.
    assert _bybit_open_missing_sl_leg(
        _ProtClient({"size": "100", "stopLoss": "1.08"}), "linear", "XRPUSDT") is False
    # Resting Partial SL leg present → not naked.
    assert _bybit_open_missing_sl_leg(
        _ProtClient({"size": "100", "stopLoss": ""},
                    [{"stopOrderType": "PartialStopLoss"}]), "linear", "XRPUSDT") is False
    # Flat / no position → not naked.
    assert _bybit_open_missing_sl_leg(
        _ProtClient({"size": "0", "stopLoss": ""}), "linear", "XRPUSDT") is False
    assert _bybit_open_missing_sl_leg(_ProtClient(None), "linear", "XRPUSDT") is False


def test_entry_bracket_verification_read_failure_is_none():
    """A read failure must return None (never a false naked alarm)."""
    assert _bybit_open_missing_sl_leg(
        _ProtClient({"size": "1", "stopLoss": ""}, raise_pos=True),
        "linear", "XRPUSDT") is None
    assert _bybit_open_missing_sl_leg(
        _ProtClient({"size": "1", "stopLoss": ""}, raise_oo=True),
        "linear", "XRPUSDT") is None


class _Client:
    def __init__(self):
        self.placed_kwargs = None
        self.stop_kwargs = None

    def get_instruments_info(self, *, category, symbol):
        return {"result": {"list": [{
            "priceFilter": {"tickSize": "0.1"},
            "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
        }]}}

    def get_tickers(self, *, category, symbol):
        return {"result": {"list": [{"lastPrice": "64000"}]}}

    def place_order(self, **kwargs):
        self.placed_kwargs = kwargs
        return {"retCode": 0, "result": {"orderId": "oid-1"}}

    def set_trading_stop(self, **kwargs):
        self.stop_kwargs = kwargs
        return {"retCode": 0}


_CFG = {"account_id": "bybit_1", "exchange": "bybit", "market_type": "linear"}


def _order():
    return {
        "account_id": "bybit_1", "symbol": "BTCUSDT", "side": "Buy",
        "qty": 0.002, "sl": 63000.0, "tp": 65000.0,
    }


class TestTpslModeResolver:
    def test_default_full(self, monkeypatch):
        monkeypatch.delenv("BYBIT_TPSL_MODE", raising=False)
        assert _bybit_tpsl_mode() == "full"

    def test_partial(self, monkeypatch):
        monkeypatch.setenv("BYBIT_TPSL_MODE", "Partial")
        assert _bybit_tpsl_mode() == "partial"

    def test_unknown_resolves_full(self, monkeypatch):
        monkeypatch.setenv("BYBIT_TPSL_MODE", "bogus")
        assert _bybit_tpsl_mode() == "full"


class TestPlacement:
    def test_full_mode_wire_format_unchanged(self, monkeypatch):
        monkeypatch.delenv("BYBIT_TPSL_MODE", raising=False)
        client = _Client()
        _submit_order(client, _order(), _CFG)
        k = client.placed_kwargs
        assert "tpslMode" not in k
        assert "tpSize" not in k and "slSize" not in k
        assert k["stopLoss"] and k["takeProfit"]

    def test_partial_mode_qty_scoped(self, monkeypatch):
        monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
        client = _Client()
        _submit_order(client, _order(), _CFG)
        k = client.placed_kwargs
        assert k["tpslMode"] == "Partial"
        assert k["tpSize"] == k["qty"]
        assert k["slSize"] == k["qty"]
        assert k["tpOrderType"] == "Market"
        assert k["slOrderType"] == "Market"

    def test_reduce_only_never_carries_tpsl(self, monkeypatch):
        monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
        client = _Client()
        order = _order()
        order["reduce_only"] = True
        _submit_order(client, order, _CFG)
        k = client.placed_kwargs
        assert k.get("reduceOnly") is True
        assert "tpslMode" not in k and "stopLoss" not in k


class TestModify:
    def test_full_mode_amend_unchanged(self, monkeypatch):
        monkeypatch.delenv("BYBIT_TPSL_MODE", raising=False)
        client = _Client()
        out = modify_open_order(client, _CFG, symbol="BTCUSDT", sl=63500.0,
                                qty=0.002)
        assert out["ok"] is True
        assert "tpslMode" not in client.stop_kwargs
        assert "slSize" not in client.stop_kwargs

    def test_partial_mode_amend_qty_scoped(self, monkeypatch):
        monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
        client = _Client()
        out = modify_open_order(client, _CFG, symbol="BTCUSDT", sl=63500.0,
                                qty=0.002)
        assert out["ok"] is True
        k = client.stop_kwargs
        assert k["tpslMode"] == "Partial"
        assert k["slSize"] == "0.002"
        assert "tpSize" not in k  # only the amended leg is scoped

    def test_partial_mode_without_qty_falls_back_full(self, monkeypatch):
        monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
        client = _Client()
        out = modify_open_order(client, _CFG, symbol="BTCUSDT", sl=63500.0)
        assert out["ok"] is True
        assert "tpslMode" not in client.stop_kwargs


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
