"""Tests for src.units.accounts.precision.

Reproduces the BTCUSDT-spot rejection path that surfaced as
``retCode 170134 — Order price has too many decimals`` and pins the
quantize / lookup behaviour the executor relies on.
"""
from __future__ import annotations

from decimal import Decimal

from src.units.accounts import precision
from src.units.accounts.precision import get_tick_size, quantize_price


# ---------------------------------------------------------------------------
# quantize_price
# ---------------------------------------------------------------------------


def test_quantize_price_btc_spot_strips_float_noise():
    """The exact values from the production rejection log must
    quantize to two decimals on BTCUSDT spot."""
    tick = Decimal("0.01")
    assert quantize_price(81199.1764251841, tick) == "81199.18"
    assert quantize_price(81899.98467877129, tick) == "81899.98"


def test_quantize_price_btc_linear_uses_dime_tick():
    tick = Decimal("0.10")
    assert quantize_price(81199.1764251841, tick) == "81199.20"
    assert quantize_price(81899.98467877129, tick) == "81900.00"


def test_quantize_price_already_aligned_no_change():
    assert quantize_price(81199.18, Decimal("0.01")) == "81199.18"


def test_quantize_price_pads_trailing_zeros_to_tick_exponent():
    """0.10-tick must serialise as ``81199.10`` not ``81199.1``."""
    assert quantize_price(81199.10, Decimal("0.10")) == "81199.10"


def test_quantize_price_handles_sub_tick_value():
    assert quantize_price(0.0049, Decimal("0.01")) == "0.00"
    assert quantize_price(0.005, Decimal("0.01")) == "0.01"


# ---------------------------------------------------------------------------
# get_tick_size
# ---------------------------------------------------------------------------


def test_get_tick_size_static_btc_spot_no_network():
    """Known pair must be served from the static map without
    touching the client."""

    class ExplodingClient:
        def get_instruments_info(self, **_kw):
            raise AssertionError("static map should preempt the network call")

    assert get_tick_size(ExplodingClient(), "BTCUSDT", "spot") == Decimal("0.01")


def test_get_tick_size_static_btc_linear():
    assert get_tick_size(None, "BTCUSDT", "linear") == Decimal("0.10")


def test_get_tick_size_live_lookup_then_caches():
    """Unknown pair triggers exactly one live lookup; subsequent
    calls hit the process cache."""
    calls: list = []

    class FakeClient:
        def get_instruments_info(self, *, category, symbol):
            calls.append((category, symbol))
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.005"}}]}}

    client = FakeClient()
    first = get_tick_size(client, "FAKEUSDT", "spot")
    second = get_tick_size(client, "FAKEUSDT", "spot")
    assert first == Decimal("0.005")
    assert second == Decimal("0.005")
    assert calls == [("spot", "FAKEUSDT")]


def test_get_tick_size_falls_back_when_lookup_raises():
    """A network blip on instruments-info must not block the order
    path — we fall back to the conservative 0.01 default."""

    class BrokenClient:
        def get_instruments_info(self, **_kw):
            raise RuntimeError("network down")

    # Use a fresh symbol so the cache from previous tests can't help.
    assert get_tick_size(BrokenClient(), "ZZZZUSDT", "spot") == Decimal("0.01")


def test_get_tick_size_falls_back_on_empty_response():
    class EmptyClient:
        def get_instruments_info(self, **_kw):
            return {"result": {"list": []}}

    assert get_tick_size(EmptyClient(), "QQQQUSDT", "spot") == Decimal("0.01")


def test_get_tick_size_normalises_case():
    assert get_tick_size(None, "btcusdt", "SPOT") == Decimal("0.01")


# ---------------------------------------------------------------------------
# integration: simulate the executor's kwargs build
# ---------------------------------------------------------------------------


def test_executor_kwargs_pass_bybit_decimal_check():
    """End-to-end shape: feed the production SL/TP values through
    the helper exactly the way ``execute._submit_order`` does."""
    tick = get_tick_size(None, "BTCUSDT", "spot")
    sl = quantize_price(81199.1764251841, tick)
    tp = quantize_price(81899.98467877129, tick)
    # Bybit's 170134 rejects when the decimal places exceed the tick;
    # a 2-decimal string is the contract for BTCUSDT spot.
    assert sl.count(".") == 1 and len(sl.split(".")[1]) == 2
    assert tp.count(".") == 1 and len(tp.split(".")[1]) == 2
    assert sl == "81199.18"
    assert tp == "81899.98"


def test_static_map_covers_all_actively_routed_symbols():
    """Sanity guard: the symbols listed in the static map must form a
    coherent (symbol, category) set so a config typo (e.g. ``Spot``)
    can't bypass quantization."""
    for sym, cat in precision._STATIC_TICK_SIZE.keys():
        assert sym == sym.upper()
        assert cat == cat.lower()
        assert cat in {"spot", "linear", "inverse"}
