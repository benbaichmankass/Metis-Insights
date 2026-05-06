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


# ---------------------------------------------------------------------------
# BUG-057 reopen — live_instrument_diagnostic
# ---------------------------------------------------------------------------


def test_live_instrument_diagnostic_returns_full_record():
    """Captures the full priceFilter + lotSizeFilter so the operator
    can see Bybit's ground-truth precision the next time 170134 fires.
    Bypasses the cache (always hits the wire)."""
    class FakeClient:
        def get_instruments_info(self, *, category, symbol):
            return {"result": {"list": [{
                "symbol": symbol,
                "status": "Trading",
                "priceFilter": {"tickSize": "0.10", "minPrice": "0.01"},
                "lotSizeFilter": {"basePrecision": "0.000001",
                                  "minOrderAmt": "1"},
            }]}}

    out = precision.live_instrument_diagnostic(FakeClient(), "BTCUSDT", "spot")
    assert out is not None
    assert out["symbol"] == "BTCUSDT"
    assert out["category"] == "spot"
    assert out["status"] == "Trading"
    assert out["priceFilter"]["tickSize"] == "0.10"
    assert out["lotSizeFilter"]["basePrecision"] == "0.000001"


def test_live_instrument_diagnostic_returns_none_on_client_error():
    """Diagnostics on the failure path must never amplify the failure —
    a broken client returns None, not a raise."""
    class BrokenClient:
        def get_instruments_info(self, *_a, **_kw):
            raise RuntimeError("network down")

    assert precision.live_instrument_diagnostic(
        BrokenClient(), "BTCUSDT", "spot",
    ) is None


def test_live_instrument_diagnostic_returns_none_for_empty_list():
    class EmptyClient:
        def get_instruments_info(self, *_a, **_kw):
            return {"result": {"list": []}}

    assert precision.live_instrument_diagnostic(
        EmptyClient(), "UNKNOWN", "spot",
    ) is None


# ---------------------------------------------------------------------------
# BUG-057 reopen — _log_170134_diagnostic in execute.py
# ---------------------------------------------------------------------------


def test_log_170134_diagnostic_emits_structured_record(caplog):
    """The diagnostic must log a single ERROR with the
    ``BUG-057-DIAG`` prefix, the exact SL/TP we sent, and the live
    priceFilter/lotSizeFilter."""
    import logging

    from src.units.accounts import execute

    class FakeClient:
        def get_instruments_info(self, *, category, symbol):
            return {"result": {"list": [{
                "symbol": symbol, "status": "Trading",
                "priceFilter": {"tickSize": "0.10"},
                "lotSizeFilter": {"basePrecision": "0.000001",
                                  "minOrderAmt": "1"},
            }]}}

    order = {
        "symbol": "BTCUSDT", "side": "Buy", "qty": 0.002,
        "sl": 81072.4823841, "tp": 81854.0098765,
    }
    account_cfg = {"account_id": "bybit_2"}

    with caplog.at_level(logging.ERROR, logger="src.units.accounts.execute"):
        execute._log_170134_diagnostic(FakeClient(), order, account_cfg, "spot")

    matches = [r for r in caplog.records if "BUG-057-DIAG" in r.getMessage()]
    assert len(matches) == 1, f"expected 1 BUG-057-DIAG record, got {len(matches)}"
    msg = matches[0].getMessage()
    assert "account=bybit_2" in msg
    assert "symbol=BTCUSDT" in msg
    assert "category=spot" in msg
    assert "qty=0.002" in msg
    # Raw value preserved so the operator sees what arithmetic produced
    # (binary-float noise included).
    assert "sl_raw=81072.4823841" in msg
    # Quantized value matches what reaches Bybit.
    assert "sl_sent='81072.48'" in msg
    assert "tp_sent='81854.01'" in msg
    # Live filters captured.
    assert "live_priceFilter={'tickSize': '0.10'}" in msg
    assert "live_status=Trading" in msg


def test_log_170134_diagnostic_safe_when_instruments_info_raises(caplog):
    """If get_instruments_info raises, the diagnostic still emits the
    record with ``live_priceFilter=None`` rather than blowing up the
    failure path."""
    import logging

    from src.units.accounts import execute

    class BrokenClient:
        def get_instruments_info(self, *_a, **_kw):
            raise RuntimeError("network down")

    order = {"symbol": "BTCUSDT", "qty": 0.002,
             "sl": 81072.48, "tp": 81854.00}

    with caplog.at_level(logging.ERROR, logger="src.units.accounts.execute"):
        execute._log_170134_diagnostic(
            BrokenClient(), order, {"account_id": "bybit_2"}, "spot",
        )

    matches = [r for r in caplog.records if "BUG-057-DIAG" in r.getMessage()]
    assert len(matches) == 1
    msg = matches[0].getMessage()
    assert "live_priceFilter=None" in msg
    assert "live_status=None" in msg
    # The static-map tick still shows so we record what we WOULD have sent.
    assert "static_tick=0.01" in msg


def test_log_170134_diagnostic_never_raises_even_if_client_is_none(caplog):
    """``client is None`` is a degenerate case the failure path must
    survive (e.g. a torn-down session). The function logs and returns."""
    import logging

    from src.units.accounts import execute

    order = {"symbol": "BTCUSDT", "qty": 0.002, "sl": 81072.48, "tp": 81854.00}

    with caplog.at_level(logging.ERROR, logger="src.units.accounts.execute"):
        execute._log_170134_diagnostic(
            None, order, {"account_id": "bybit_2"}, "spot",
        )

    # Even with a None client, the static-map tick still resolves and
    # the diagnostic emits.
    matches = [r for r in caplog.records if "BUG-057-DIAG" in r.getMessage()]
    assert len(matches) == 1
