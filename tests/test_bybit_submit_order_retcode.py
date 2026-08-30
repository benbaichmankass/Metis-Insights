"""BL-20260830 — the Bybit branch of ``_submit_order`` must check ``retCode``
and must never fabricate a trade id.

WHY THIS EXISTS. Every other broker branch in ``_submit_order``
(alpaca / oanda / ib) checks the response's ``retCode`` and turns a non-zero
one into a RuntimeError. The bybit branch did neither: it read
``result.orderId`` and fell back to ``uuid.uuid4().hex``. So a refusal that
RETURNS rather than raises produced a FABRICATED order id and a journal row
for a position the venue never opened — a phantom trade wearing the shape of
a fill.

⚠️ STATE WHAT IS AND IS NOT REACHABLE. pybit's behaviour is mixed. ErrCode
10001 RAISES — the 20 ``exchange_rejected`` AVAX venue-max rows on the live
journal carry the ``RuntimeError: Order submission failed for AVAXUSDT: ...
(ErrCode: 10001)`` text produced by the branch's own exception handler, which
is the positive control proving refusals of that family were already visible.
What was NOT covered is the family that returns instead: ``execute.py``'s own
sub-min-lot comment records that Bybit "returns retCode != 0 (no exception)"
for a below-min-lot qty. Checking is correct under BOTH behaviours.
"""
from __future__ import annotations

import pytest

from src.units.accounts import precision
from src.units.accounts.execute import _submit_order

_CFG = {"account_id": "bybit_1", "exchange": "bybit", "market_type": "linear"}


class _Client:
    """Bybit V5 stub whose place_order response is what the test is about."""

    def __init__(self, response):
        self._response = response
        self.placed_kwargs = None

    def get_instruments_info(self, *, category, symbol):
        return {"result": {"list": [{
            "priceFilter": {"tickSize": "0.01"},
            "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"},
        }]}}

    def get_tickers(self, *, category, symbol):
        return {"result": {"list": [{"lastPrice": "99999"}]}}

    def place_order(self, **kwargs):
        self.placed_kwargs = kwargs
        return self._response


@pytest.fixture(autouse=True)
def _clean_caches():
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()
    yield
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()


def _order(qty=1.0, symbol="ETHUSDT", side="Sell"):
    return {"symbol": symbol, "side": side, "qty": qty, "sl": 1698.37, "tp": 1482.24}


def test_a_clean_accept_still_returns_the_venue_order_id():
    """The control: the happy path must be unchanged."""
    c = _Client({"retCode": 0, "result": {"orderId": "ord-1"}})
    assert _submit_order(c, _order(), _CFG) == "ord-1"


def test_a_returned_non_zero_retcode_raises_instead_of_reporting_success():
    """The family pybit does NOT raise for — the whole gap."""
    c = _Client({"retCode": 110007, "retMsg": "ab not enough for new order",
                 "result": {}})
    with pytest.raises(RuntimeError) as exc:
        _submit_order(c, _order(), _CFG)
    msg = str(exc.value)
    assert "110007" in msg, msg
    assert "ab not enough" in msg, msg


def test_a_missing_order_id_raises_rather_than_fabricating_one():
    """The defect proper. The old code returned `uuid.uuid4().hex` here, so the
    caller journalled an open trade for a position that does not exist."""
    c = _Client({"retCode": 0, "result": {}})
    with pytest.raises(RuntimeError) as exc:
        _submit_order(c, _order(), _CFG)
    assert "no orderId" in str(exc.value)


def test_the_returned_id_is_never_a_generated_uuid():
    """Non-vacuity guard on the two tests above.

    If a future change reinstates any fallback, this catches it: a returned id
    must be the venue's, so it can never be a bare 32-char hex string that the
    stub did not supply.
    """
    c = _Client({"retCode": 0, "result": {"orderId": "ord-2"}})
    got = _submit_order(c, _order(), _CFG)
    assert got == "ord-2"
    assert not (len(got) == 32 and all(ch in "0123456789abcdef" for ch in got)), (
        "a uuid4().hex slipped through — the fabrication is back")


def test_an_absent_retcode_is_not_treated_as_a_refusal():
    """Mirrors `_submit_test_order` exactly: `None` is acceptable. The two
    sibling paths must not disagree about what counts as a venue refusal."""
    c = _Client({"result": {"orderId": "ord-3"}})
    assert _submit_order(c, _order(), _CFG) == "ord-3"
