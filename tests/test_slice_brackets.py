"""NETTED-SLICES: every slice carries, trails and exits on its OWN bracket.

Mechanism (chosen over separate conditional orders — see positions.py admit_slice):
Bybit Partial position TP/SL, ``tpslMode=Partial`` with ``tpSize = slSize =
slice qty``. A slice's trail amends ITS tracked leg in place; a slice's exit is
attributed to the slice whose leg is gone.
"""
from __future__ import annotations

import pytest

from src.runtime.order_monitor import _netting_rows_to_attribute
from src.units.accounts.execute import _submit_order, modify_open_order

_CFG = {"account_id": "bybit_1", "exchange": "bybit", "market_type": "linear"}


class _Client:
    def __init__(self):
        self.placed = []
        self.amended = []
        self.stops = []

    def get_instruments_info(self, *, category, symbol):
        return {"result": {"list": [{
            "priceFilter": {"tickSize": "0.01"},
            "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
        }]}}

    def get_tickers(self, *, category, symbol):
        return {"result": {"list": [{"lastPrice": "2500"}]}}

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        return {"retCode": 0, "result": {"orderId": f"oid-{len(self.placed)}"}}

    def amend_order(self, **kwargs):
        self.amended.append(kwargs)
        return {"retCode": 0, "result": {}}

    def set_trading_stop(self, **kwargs):
        self.stops.append(kwargs)
        return {"retCode": 0}


def _order(qty, sl, tp, side="Buy"):
    return {"account_id": "bybit_1", "symbol": "ETHUSDT", "side": side,
            "qty": qty, "sl": sl, "tp": tp}


def test_each_slice_bracket_is_sized_to_its_own_qty(monkeypatch):
    monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
    c = _Client()
    _submit_order(c, _order(0.3, 2400.0, 2700.0), _CFG)   # slice A
    _submit_order(c, _order(0.1, 2450.0, 2600.0), _CFG)   # slice B, its own levels
    a, b = c.placed
    assert a["tpslMode"] == b["tpslMode"] == "Partial"
    assert float(a["tpSize"]) == float(a["slSize"]) == float(a["qty"]) == pytest.approx(0.3)
    assert float(b["tpSize"]) == float(b["slSize"]) == float(b["qty"]) == pytest.approx(0.1)
    assert a["stopLoss"] != b["stopLoss"] and a["takeProfit"] != b["takeProfit"]
    # Σ leg sizes == the netted venue position (0.4): no slice is over- or under-covered.
    assert float(a["slSize"]) + float(b["slSize"]) == pytest.approx(0.4)


def test_trailing_one_slice_amends_only_its_own_leg(monkeypatch):
    monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
    c = _Client()
    out = modify_open_order(c, _CFG, symbol="ETHUSDT", sl=2460.0, side="long",
                            qty=0.1, sl_order_id="leg-B-sl", tp_order_id="leg-B-tp")
    assert out["ok"] is True
    assert [k["orderId"] for k in c.amended] == ["leg-B-sl"]
    assert c.stops == []  # no add-a-leg call: slice A's legs are untouched


def _row(rid, strategy, qty, sl_id, created):
    return {"id": rid, "strategy_name": strategy, "position_size": qty,
            "sl_order_id": sl_id, "created_at": created}


def test_partial_exit_reduces_only_the_slice_whose_leg_fired():
    rows = [_row(1, "trend_donchian_eth_4h", 0.3, "leg-A", "2026-10-15T01:00:00"),
            _row(2, "trend_donchian_eth_prop", 0.1, "leg-B", "2026-10-15T02:00:00")]
    # The venue now holds 0.3: slice B's SL fired (its leg is gone, A's rests).
    picked = _netting_rows_to_attribute(rows, excess=0.1, live_leg_ids={"leg-A"})
    assert [(r["id"], q, basis) for r, q, basis in picked] == [(2, pytest.approx(0.1), "leg_gone")]


def test_partial_exit_never_takes_a_slice_whose_leg_still_rests_first():
    # Oldest row is A (protected); an untracked row C is the FIFO candidate.
    rows = [_row(1, "a", 0.3, "leg-A", "2026-10-15T01:00:00"),
            _row(3, "c", 0.2, None, "2026-10-15T03:00:00")]
    picked = _netting_rows_to_attribute(rows, excess=0.2, live_leg_ids={"leg-A"})
    assert [r["id"] for r, _, _ in picked] == [3]


def test_opposite_slice_on_a_hedge_book_opens_in_its_own_book(monkeypatch):
    """Opposite slices are admitted only on a hedge book (positions.admit_slice);
    there the short slice names positionIdx=2 and carries its own Partial legs."""
    monkeypatch.setenv("BYBIT_TPSL_MODE", "partial")
    monkeypatch.setenv("BYBIT_HEDGE_MODE_SYMBOLS", "bybit_1:ETHUSDT")
    c = _Client()
    _submit_order(c, _order(0.1, 2600.0, 2300.0, side="Sell"), _CFG)
    k = c.placed[0]
    assert k["positionIdx"] == 2
    assert k["tpslMode"] == "Partial"
    assert float(k["tpSize"]) == float(k["slSize"]) == pytest.approx(0.1)
    assert k.get("reduceOnly") is not True
