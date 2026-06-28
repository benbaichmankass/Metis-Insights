"""Tests for Alpaca options order request-shape builders (pure, no network).

Asserts the verified mleg + single-leg request shapes and the validation guards.
"""
from __future__ import annotations

import pytest

from src.units.accounts.alpaca_options_exec import (
    OptionLeg,
    OptionsOrderError,
    build_mleg_body,
    build_single_option_body,
)

# A canonical $1-wide XLF call debit spread: buy lower strike, sell higher.
LONG = OptionLeg("XLF260116C00054000", "buy", "buy_to_open", 1)
SHORT = OptionLeg("XLF260116C00055000", "sell", "sell_to_open", 1)


def test_mleg_body_shape():
    body = build_mleg_body([LONG, SHORT], qty=2, limit_price=0.45)
    assert body["order_class"] == "mleg"
    assert body["qty"] == "2"
    assert body["type"] == "limit"
    assert body["limit_price"] == "0.45"
    assert body["time_in_force"] == "day"
    assert len(body["legs"]) == 2
    assert body["legs"][0] == {
        "symbol": "XLF260116C00054000", "ratio_qty": "1",
        "side": "buy", "position_intent": "buy_to_open",
    }
    assert body["legs"][1]["side"] == "sell"


def test_mleg_market_order_omits_limit_price():
    body = build_mleg_body([LONG, SHORT], qty=1, order_type="market")
    assert body["type"] == "market"
    assert "limit_price" not in body


def test_mleg_requires_two_to_four_legs():
    with pytest.raises(OptionsOrderError):
        build_mleg_body([LONG], qty=1, limit_price=0.4)
    with pytest.raises(OptionsOrderError):
        build_mleg_body([LONG, SHORT, LONG, SHORT, LONG], qty=1, limit_price=0.4)


def test_mleg_rejects_duplicate_leg_symbol():
    dup = OptionLeg("XLF260116C00054000", "sell", "sell_to_open", 1)
    with pytest.raises(OptionsOrderError):
        build_mleg_body([LONG, dup], qty=1, limit_price=0.4)


def test_mleg_limit_requires_positive_price():
    with pytest.raises(OptionsOrderError):
        build_mleg_body([LONG, SHORT], qty=1, order_type="limit", limit_price=None)
    with pytest.raises(OptionsOrderError):
        build_mleg_body([LONG, SHORT], qty=1, order_type="limit", limit_price=0)


def test_mleg_rejects_sub_one_qty():
    with pytest.raises(OptionsOrderError):
        build_mleg_body([LONG, SHORT], qty=0, limit_price=0.4)


def test_mleg_rejects_bad_intent():
    bad = OptionLeg("XLF260116C00056000", "buy", "open", 1)
    with pytest.raises(OptionsOrderError):
        build_mleg_body([LONG, bad], qty=1, limit_price=0.4)


def test_single_option_body_shape():
    body = build_single_option_body(
        "XLF260116C00054000", side="buy", qty=1,
        position_intent="buy_to_open", limit_price=0.55,
    )
    assert body["symbol"] == "XLF260116C00054000"
    assert body["qty"] == "1"
    assert body["side"] == "buy"
    assert body["position_intent"] == "buy_to_open"
    assert body["limit_price"] == "0.55"
    assert "order_class" not in body  # single-leg is NOT mleg


def test_single_option_rejects_bad_side():
    with pytest.raises(OptionsOrderError):
        build_single_option_body(
            "XLF260116C00054000", side="long", qty=1,
            position_intent="buy_to_open", limit_price=0.5,
        )


# ---------------------------------------------------------- Slice-4: live-path
# methods exercised against a captured-request fake (no network).

from src.units.accounts.alpaca_options_exec import AlpacaOptionsExecutor  # noqa: E402


class _CaptureExec(AlpacaOptionsExecutor):
    """Executor whose _request records calls and returns canned envelopes."""

    def __init__(self, responses):
        super().__init__(api_key="k", api_secret="s", env="paper")
        self._responses = list(responses)
        self.calls = []

    def _request(self, method, path, json_body=None):
        self.calls.append((method, path, json_body))
        return self._responses.pop(0) if self._responses else {"retCode": 0, "result": {}}


def test_account_activities_filters_to_lifecycle_types():
    ex = _CaptureExec([{"retCode": 0, "result": [{"id": "x"}]}])
    env = ex.account_activities(after="2026-06-20")
    assert env["retCode"] == 0
    method, path, _ = ex.calls[0]
    assert method == "GET"
    assert path.startswith("/v2/account/activities?")
    assert "activity_types=EXP,OPASN,OPEXC" in path
    assert "after=2026-06-20" in path


def test_close_structure_liquidates_every_leg_idempotently():
    # First leg closes ok; second is a 404 (no position) → mapped to success.
    ex = _CaptureExec([
        {"retCode": 0, "result": {}},
        {"retCode": 404, "retMsg": "position does not exist"},
    ])
    out = ex.close_structure(["SLV260116C00025000", "SLV260116C00027000"])
    assert out["retCode"] == 0
    assert out["result"]["closed"] == ["SLV260116C00025000", "SLV260116C00027000"]
    assert out["result"]["failed"] == []
    assert all(m == "DELETE" for m, _, _ in ex.calls)


def test_close_structure_reports_failed_leg():
    ex = _CaptureExec([
        {"retCode": 0, "result": {}},
        {"retCode": 500, "retMsg": "boom"},
    ])
    out = ex.close_structure(["SLV260116C00025000", "SLV260116C00027000"])
    assert out["retCode"] == 1
    assert out["result"]["closed"] == ["SLV260116C00025000"]
    assert out["result"]["failed"][0]["symbol"] == "SLV260116C00027000"
