"""Flat-USDT invariant verification after a close.

Pins the contract for
``src.units.accounts.execute._post_close_flat_check`` and its wiring
inside ``close_open_position``.

Operator invariant (confirmed 2026-05-08): idle state for spot
accounts is 100 % USDT, 0 base coin, 0 borrows. ``_post_close_repay``
handles the borrow leg (S-055). The flat-check helper covers the
**base-coin walletBalance** leg — partial fills, qty-rounding
leftovers, and "long forgot to fully sell back" scenarios that
leave a stale BTC balance the borrow-orphan reconciler can't see.

Detection-only by design: emits a sticky ``post_close_not_flat``
audit row via ``signal_audit_logger.log_signal`` so the operator
greps ``runtime_logs/signal_audit.jsonl`` rather than discovering
the leak on the next signal. No auto-flatten — a follow-up market
sell could race manual operator action.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from src.units.accounts.execute import (
    _FLAT_INVARIANT_EPSILON,
    _post_close_flat_check,
    close_open_position,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _wallet_with(coins):
    return {"result": {"list": [{"coin": list(coins)}]}}


class _StubClient:
    """Minimal Bybit-V5 stub: scripts ``get_wallet_balance`` and
    captures ``place_order`` kwargs. Mirrors the shape used by
    ``tests/test_borrow_orphan_reconciler.py`` so the two suites
    read side-by-side.
    """

    def __init__(self, *, wallet_response: Dict[str, Any],
                 place_response: Optional[Dict[str, Any]] = None):
        self._wallet_response = wallet_response
        self._place_response = place_response or {
            "retCode": 0, "retMsg": "OK",
            "result": {"orderId": "STUB-CLOSE-1"},
        }
        self.place_order_calls: List[Dict[str, Any]] = []
        self.repay_calls: List[Dict[str, Any]] = []
        self.wallet_calls = 0

    def get_wallet_balance(self, **_):
        self.wallet_calls += 1
        return self._wallet_response

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        return self._place_response

    def repay(self, **kwargs):
        self.repay_calls.append(kwargs)
        return {"retCode": 0, "retMsg": "OK"}


@pytest.fixture
def captured_audit(monkeypatch):
    """Redirect the JSONL writer so each test asserts on captured rows."""
    rows: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        "src.utils.signal_audit_logger.log_signal",
        lambda payload: rows.append(payload),
    )
    return rows


# ---------------------------------------------------------------------------
# _post_close_flat_check — direct unit tests
# ---------------------------------------------------------------------------


class TestPostCloseFlatCheckHelper:
    """The helper itself: spot/spot-margin only, residual-detection,
    audit-row emission, never raises.
    """

    def test_flat_wallet_returns_flat_true_no_audit(self, captured_audit):
        """Healthy idle state: walletBalance(BTC)=0 → flat=True, no
        audit row. The common path on every successful close.
        """
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0",
             "free": "0", "locked": "0"},
            {"coin": "USDT", "walletBalance": "200",
             "free": "200", "locked": "0"},
        ]))
        outcome = _post_close_flat_check(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long",
        )
        assert outcome is not None
        assert outcome["flat"] is True
        assert outcome["coin"] == "BTC"
        assert outcome["residual_qty"] == pytest.approx(0.0)
        assert outcome["epsilon"] == _FLAT_INVARIANT_EPSILON
        assert captured_audit == []

    def test_residual_btc_emits_post_close_not_flat_audit(
        self, captured_audit,
    ):
        """Headline contract: residual base coin > epsilon →
        flat=False, sticky audit row written with the structured
        fields the operator can grep.
        """
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0.0005",
             "free": "0.0005", "locked": "0"},
            {"coin": "USDT", "walletBalance": "150",
             "free": "150", "locked": "0"},
        ]))
        outcome = _post_close_flat_check(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long",
        )
        assert outcome is not None
        assert outcome["flat"] is False
        assert outcome["coin"] == "BTC"
        assert outcome["residual_qty"] == pytest.approx(0.0005)

        assert len(captured_audit) == 1
        evt = captured_audit[0]
        assert evt["action"] == "post_close_not_flat"
        assert evt["status"] == "warn"
        assert evt["account_id"] == "bybit_2"
        assert evt["symbol"] == "BTCUSDT"
        assert evt["side"] == "long"
        assert evt["coin"] == "BTC"
        assert evt["residual_qty"] == pytest.approx(0.0005)
        assert evt["epsilon"] == _FLAT_INVARIANT_EPSILON

    def test_dust_below_epsilon_treated_as_flat(self, captured_audit):
        """A 1e-7 BTC residue is below the epsilon cutoff and would
        fail repay-precision rules at Bybit. Treat as flat — no
        audit row, no false positive on the operator's feed.
        """
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0.0000001",
             "free": "0.0000001", "locked": "0"},
        ]))
        outcome = _post_close_flat_check(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="short",
        )
        assert outcome is not None
        assert outcome["flat"] is True
        assert captured_audit == []

    def test_locked_btc_flagged_as_residual(self, captured_audit):
        """Locked BTC (sitting in another open order) shows up as
        residual: ``free`` is 0 but ``walletBalance > epsilon``. The
        helper uses the locked-aware ``base_qty`` from
        ``_fetch_spot_coin_balances`` — which is walletBalance −
        locked — so locked-only BTC reads as flat (free=0). This
        avoids false positives when an unrelated reduce-only order
        is mid-flight.
        """
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0.001",
             "free": "0", "locked": "0.001"},
        ]))
        outcome = _post_close_flat_check(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long",
        )
        # base_qty = walletBalance − locked = 0 → flat=True.
        assert outcome is not None
        assert outcome["flat"] is True
        assert captured_audit == []

    def test_derivatives_account_skips_flat_check(self, captured_audit):
        """Derivatives accounts close via reduceOnly — no
        walletBalance flow to verify. Helper returns None and
        emits no audit row.
        """
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0.5",
             "free": "0.5", "locked": "0"},
        ]))
        outcome = _post_close_flat_check(
            client, {"account_id": "bybit_1", "exchange": "bybit",
                     "market_type": "linear"},
            symbol="BTCUSDT", side="long",
        )
        assert outcome is None
        assert client.wallet_calls == 0  # never even fetched
        assert captured_audit == []

    def test_unsupported_exchange_returns_none(self, captured_audit):
        """Binance + others aren't wired for the spot wallet read.
        Helper returns None, no audit row.
        """
        client = _StubClient(wallet_response=_wallet_with([]))
        outcome = _post_close_flat_check(
            client, {"account_id": "binance_x", "exchange": "binance"},
            symbol="BTCUSDT", side="long",
        )
        assert outcome is None
        assert captured_audit == []

    def test_no_client_returns_none(self, captured_audit):
        """Missing creds path: helper returns None instead of raising
        so close_open_position's wrapper sees a clean result.
        """
        outcome = _post_close_flat_check(
            None, {"account_id": "bybit_2", "exchange": "bybit",
                   "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long",
        )
        assert outcome is None
        assert captured_audit == []

    def test_wallet_refetch_failure_no_false_positive(self, captured_audit):
        """A transient wallet API failure must not crash the close
        AND must not generate a false-positive audit row. The
        underlying ``_fetch_spot_coin_balances`` swallows exceptions
        and returns default zeros, so the helper sees a flat wallet
        and emits nothing — preferable to flagging every transient
        rate-limit as an invariant violation.
        """
        class _Boom:
            def get_wallet_balance(self, **_):
                raise RuntimeError("rate limited")

        outcome = _post_close_flat_check(
            _Boom(), {"account_id": "bybit_2", "exchange": "bybit",
                      "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long",
        )
        # base_qty defaults to 0.0 on read failure → reads as flat.
        # No false-positive audit row.
        assert outcome is not None
        assert outcome["flat"] is True
        assert outcome["residual_qty"] == pytest.approx(0.0)
        assert captured_audit == []

    def test_audit_log_failure_does_not_propagate(self, monkeypatch):
        """If ``log_signal`` itself raises, the helper still returns
        a structured outcome — best-effort end-to-end. Pinning this
        guards against a regression where an audit-write failure
        unwinds the close.
        """
        def _raise(_payload):
            raise RuntimeError("disk full")

        monkeypatch.setattr(
            "src.utils.signal_audit_logger.log_signal",
            _raise,
        )
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0.001",
             "free": "0.001", "locked": "0"},
        ]))
        outcome = _post_close_flat_check(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="short",
        )
        assert outcome is not None
        assert outcome["flat"] is False
        assert outcome["residual_qty"] == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# close_open_position wiring — the helper must run alongside
# _post_close_repay and the result must surface on the returned dict.
# ---------------------------------------------------------------------------


class TestCloseOpenPositionWiresFlatCheck:
    def test_successful_close_includes_flat_check_result(
        self, captured_audit,
    ):
        """Headline wiring contract: a successful close returns a dict
        with both ``repay`` and ``flat_check`` fields populated.
        """
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0",
             "free": "0", "locked": "0", "borrowAmount": "0"},
            {"coin": "USDT", "walletBalance": "200",
             "free": "200", "locked": "0", "borrowAmount": "0"},
        ]))
        result = close_open_position(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is True
        assert "flat_check" in result
        assert result["flat_check"]["flat"] is True
        # No leak → no audit row.
        assert not any(
            r.get("action") == "post_close_not_flat"
            for r in captured_audit
        )

    def test_close_with_residual_emits_audit_and_marks_not_flat(
        self, captured_audit,
    ):
        """A close that fills but leaves residual base-coin balance
        (partial fill / rounding leftover) → ``flat_check.flat=False``
        + a ``post_close_not_flat`` audit row.
        """
        client = _StubClient(wallet_response=_wallet_with([
            {"coin": "BTC", "walletBalance": "0.00005",
             "free": "0.00005", "locked": "0", "borrowAmount": "0"},
        ]))
        result = close_open_position(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is True
        assert result["flat_check"]["flat"] is False
        assert result["flat_check"]["residual_qty"] == pytest.approx(0.00005)

        not_flat = [
            r for r in captured_audit
            if r.get("action") == "post_close_not_flat"
        ]
        assert len(not_flat) == 1
        assert not_flat[0]["account_id"] == "bybit_2"

    def test_failed_close_does_not_run_flat_check(self, captured_audit):
        """A close that the exchange rejects must NOT trigger the
        flat-check — the position is still live and we have no
        right to assume anything about the wallet.
        """
        client = _StubClient(
            wallet_response=_wallet_with([]),
            place_response={"retCode": 10001, "retMsg": "rejected",
                            "result": {}},
        )
        result = close_open_position(
            client, {"account_id": "bybit_2", "exchange": "bybit",
                     "market_type": "spot-margin"},
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is False
        assert "flat_check" not in result
        assert client.wallet_calls == 0
        assert captured_audit == []
