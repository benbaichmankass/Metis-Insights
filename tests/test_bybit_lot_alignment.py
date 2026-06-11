"""BL-20260611-005 — Bybit qty lot-size alignment in ``_submit_order``.

Reproduces the eth_pullback_2h rejection wall (trades #2532/#2533,
2026-06-11): the sizer's account-level 3dp precision produced 14.937 /
12.771 ETH on ETHUSDT linear whose ``lotSizeFilter.qtyStep`` is 0.01, and
Bybit rejected every order with ``retCode 10001 Qty invalid``. The fix
floors qty to the symbol's qtyStep pre-flight (mirroring the price side's
tick alignment), refuses below ``minOrderQty``, and writes the aligned qty
back onto the order dict so the journal records what was actually sent.
"""
from __future__ import annotations

import pytest

from src.units.accounts import precision
from src.units.accounts.execute import _submit_order


class _LotClient:
    """Minimal Bybit V5 stub: instruments-info with a lotSizeFilter +
    a place_order recorder."""

    def __init__(self, qty_step="0.01", min_qty="0.01", fail_info=False):
        self._qty_step = qty_step
        self._min_qty = min_qty
        self._fail_info = fail_info
        self.placed_kwargs = None

    def get_instruments_info(self, *, category, symbol):
        if self._fail_info:
            raise RuntimeError("simulated instruments-info outage")
        return {"result": {"list": [{
            "priceFilter": {"tickSize": "0.01"},
            "lotSizeFilter": {
                "qtyStep": self._qty_step,
                "minOrderQty": self._min_qty,
            },
        }]}}

    def get_tickers(self, *, category, symbol):
        # Keep lastPrice above SL so the Buy-SL pre-check never trips.
        return {"result": {"list": [{"lastPrice": "99999"}]}}

    def place_order(self, **kwargs):
        self.placed_kwargs = kwargs
        return {"retCode": 0, "result": {"orderId": "ord-eth-1"}}


@pytest.fixture(autouse=True)
def _clean_caches():
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()
    yield
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()


def _order(qty, symbol="ETHUSDT", side="Sell"):
    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "sl": 1698.37,
        "tp": 1482.24,
    }


_CFG = {"account_id": "bybit_1", "exchange": "bybit", "market_type": "linear"}


class TestQtyStepAlignment:
    def test_eth_rejection_qty_is_floored_to_step(self):
        """The literal trade #2533 qty: 14.937 → submitted as 14.93."""
        client = _LotClient()
        order = _order(14.937)
        _submit_order(client, order, _CFG)
        assert client.placed_kwargs["qty"] == "14.93"
        # Journal sees what was sent.
        assert order["qty"] == pytest.approx(14.93)

    def test_aligned_qty_unmodified(self):
        client = _LotClient()
        order = _order(12.77)
        _submit_order(client, order, _CFG)
        assert client.placed_kwargs["qty"] == "12.77"
        assert order["qty"] == pytest.approx(12.77)

    def test_below_min_order_qty_refused_preflight(self):
        client = _LotClient()
        with pytest.raises(RuntimeError, match="below the exchange lot minimum"):
            _submit_order(client, _order(0.004), _CFG)
        assert client.placed_kwargs is None  # nothing transmitted

    def test_unknown_rule_submits_unmodified(self):
        """Lookup outage + no static entry → pre-fix behaviour (qty sent
        as sized) rather than aligning to a guessed step."""
        client = _LotClient(fail_info=True)
        order = _order(1.2345, symbol="DOGEUSDT")
        _submit_order(client, order, _CFG)
        assert client.placed_kwargs["qty"] == "1.2345"
        assert order["qty"] == pytest.approx(1.2345)

    def test_static_fallback_used_on_outage_for_known_symbol(self):
        """ETHUSDT has a static (0.01, 0.01) entry — an instruments-info
        outage still aligns correctly."""
        client = _LotClient(fail_info=True)
        order = _order(14.937)
        _submit_order(client, order, _CFG)
        assert client.placed_kwargs["qty"] == "14.93"

    def test_reduce_only_orders_also_aligned(self):
        client = _LotClient()
        order = _order(0.519, side="Buy")
        order["reduce_only"] = True
        _submit_order(client, order, _CFG)
        assert client.placed_kwargs["qty"] == "0.51"
        assert client.placed_kwargs.get("reduceOnly") is True
