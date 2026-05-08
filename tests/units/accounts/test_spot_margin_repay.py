"""S-055 — spot-margin borrow repay surface.

Pins three contracts in the execute-path layer:

  1. ``_spot_margin_repay`` wraps Bybit's V5 repay endpoint
     defensively — successful repay returns ``ok=True``; transient /
     non-zero retCode / shape failures return ``ok=False`` with the
     reason recorded but never raise (the close path is fail-safe).
  2. ``_fetch_spot_coin_balances`` exposes ``base_borrowed_qty`` and
     ``quote_borrowed_qty`` from the wallet's per-coin
     ``borrowAmount`` — the consumed-borrow primitive distinct from
     ``availableToBorrow``. Default 0.0 so callers that don't read
     the field stay byte-for-byte identical to pre-S-055.
  3. ``close_open_position`` on a spot-margin account refetches the
     wallet after a successful close and force-repays any residual
     ``borrowAmount > epsilon``. The repay outcome rides on the close
     result dict under ``"repay"`` but never overrides the close's
     ``ok`` status — a successful close with a stuck borrow is still
     a successful close (the standalone reconciler picks it up).

Field shape: bybit_2 (USDT-only wallet) on 2026-05-08 left
``borrowAmount(BTC) > 0`` even when ``trade_journal.db`` showed no
open trade — Bybit's auto-repay didn't fully clear the line.
The S-055 fail-safe is the verify + force-repay this file pins.

Compliance with `docs/claude/operating-protocol.md` § 4.4: the
post-close repay is a side effect on the borrow line, NOT a gate on
the order path. The dispatcher's ``live | dry_run`` switch remains
the only canonical execution gate. No new refuse-to-trade decisions
land outside the risk manager.
"""
from __future__ import annotations

import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# pipeline.py needs matplotlib at import time via signal_notifications.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()


# ---------------------------------------------------------------------------
# Fixtures — shared client stub
# ---------------------------------------------------------------------------


class _RepayClient:
    """Captures repay calls + scripts the response. Mirrors the shape
    of ``pybit.unified_trading.HTTP`` — only the endpoints the helper
    touches are stubbed.
    """

    def __init__(
        self,
        *,
        repay_response=None,
        repay_raises=None,
        wallet_response=None,
    ):
        self.repay_calls = []
        self.wallet_calls = []
        self.place_calls = []
        self._repay_response = repay_response or {"retCode": 0, "retMsg": "OK"}
        self._repay_raises = repay_raises
        self._wallet_response = wallet_response or {
            "result": {"list": [{"coin": []}]},
        }

    def repay(self, **kwargs):
        self.repay_calls.append(kwargs)
        if self._repay_raises is not None:
            raise self._repay_raises
        return self._repay_response

    def get_wallet_balance(self, **kwargs):
        self.wallet_calls.append(kwargs)
        return self._wallet_response

    def place_order(self, **kwargs):
        self.place_calls.append(kwargs)
        return {"retCode": 0, "result": {"orderId": "close-stub-001"}}


def _spot_margin_account():
    return {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "market_type": "spot-margin",
        "api_key_env": "BYBIT_API_KEY_2",
        "api_secret_env": "BYBIT_API_SECRET_2",
        "mode": "live",
    }


def _cash_spot_account():
    return {
        "account_id": "bybit_1",
        "exchange": "bybit",
        "market_type": "spot",
        "api_key_env": "BYBIT_API_KEY_1",
        "api_secret_env": "BYBIT_API_SECRET_1",
        "mode": "live",
    }


def _wallet_with(coins):
    return {"result": {"list": [{"coin": list(coins)}]}}


# ---------------------------------------------------------------------------
# 1. Repay wrapper contract
# ---------------------------------------------------------------------------


class TestSpotMarginRepayWrapper:
    """``_spot_margin_repay(client, coin=, qty=)`` is a defensive
    wrapper over Bybit's V5 ``/v5/account/repay`` (pybit's
    ``HTTP.repay``). Best-effort by design — every failure mode
    returns ``ok=False`` with the reason captured.
    """

    def test_success_returns_ok_true_and_passes_qty_string(self):
        """Bybit's repay endpoint accepts qty as a string (matches
        place_order). Wrapper should forward it that way and surface
        ``ok=True``.
        """
        from src.units.accounts.execute import _spot_margin_repay

        client = _RepayClient(
            repay_response={"retCode": 0, "retMsg": "OK", "result": {}},
        )
        result = _spot_margin_repay(client, coin="BTC", qty=0.001)

        assert result["ok"] is True
        assert result["error"] is None
        assert client.repay_calls == [{"coin": "BTC", "qty": "0.001"}]

    def test_repay_all_omits_qty(self):
        """Passing ``qty=None`` should forward a coin-only call —
        Bybit's "repay all of this coin's liability" form.
        """
        from src.units.accounts.execute import _spot_margin_repay

        client = _RepayClient()
        result = _spot_margin_repay(client, coin="USDT", qty=None)

        assert result["ok"] is True
        assert client.repay_calls == [{"coin": "USDT"}]

    def test_transient_failure_swallowed(self):
        """A network exception from the SDK must NOT propagate — the
        wrapper is called inside a fail-safe (close path / periodic
        reconciler); raising would crash the trader. Surface it as
        ``ok=False`` with the exception type captured.
        """
        from src.units.accounts.execute import _spot_margin_repay

        client = _RepayClient(repay_raises=ConnectionError("connection reset"))
        result = _spot_margin_repay(client, coin="BTC", qty=0.001)

        assert result["ok"] is False
        assert "ConnectionError" in (result["error"] or "")
        # qty was attempted — the test stub captured the kwargs.
        assert client.repay_calls == [{"coin": "BTC", "qty": "0.001"}]

    def test_bad_shape_response_not_ok(self):
        """Bybit returns retCode != 0 (e.g. 170213 'repay qty below
        precision') with a retMsg. Wrapper surfaces the error string
        and ok=False without raising.
        """
        from src.units.accounts.execute import _spot_margin_repay

        client = _RepayClient(repay_response={
            "retCode": 170213,
            "retMsg": "repay qty below precision",
        })
        result = _spot_margin_repay(client, coin="BTC", qty=1e-9)

        assert result["ok"] is False
        assert "precision" in (result["error"] or "")

    def test_no_client_returns_ok_false(self):
        """Missing client (creds unresolvable) → ok=False. Same
        defensive shape as ``close_open_position``.
        """
        from src.units.accounts.execute import _spot_margin_repay

        result = _spot_margin_repay(None, coin="BTC", qty=0.001)
        assert result["ok"] is False
        assert "missing" in (result["error"] or "").lower()

    def test_zero_or_negative_qty_rejected(self):
        """A zero / negative qty would itself trip retCode 170213
        and is meaningless on a fail-safe path. Refuse upfront —
        cheaper than a round trip.
        """
        from src.units.accounts.execute import _spot_margin_repay

        client = _RepayClient()
        for bad in (0, -1.0, 0.0):
            result = _spot_margin_repay(client, coin="BTC", qty=bad)
            assert result["ok"] is False
        # No wire calls for the rejected shapes.
        assert client.repay_calls == []


# ---------------------------------------------------------------------------
# 2. Wallet read exposes borrowAmount
# ---------------------------------------------------------------------------


class _WalletStub:
    def __init__(self, coin_rows):
        self._wallet = {"result": {"list": [{"coin": list(coin_rows)}]}}

    def get_wallet_balance(self, **_):
        return self._wallet


class TestFetchSpotCoinBalancesBorrowedQty:
    """``_fetch_spot_coin_balances`` must expose per-coin
    ``borrowAmount`` as ``base_borrowed_qty`` / ``quote_borrowed_qty``
    so the reconciler + close path can detect outstanding borrow
    without a second wallet round trip.
    """

    def test_btc_borrow_amount_exposed_as_base_borrowed_qty(self):
        from src.units.accounts.execute import _fetch_spot_coin_balances

        client = _WalletStub([
            {
                "coin": "USDT", "walletBalance": "89", "locked": "0",
                "usdValue": "89", "borrowAmount": "0",
            },
            {
                "coin": "BTC", "walletBalance": "0", "locked": "0",
                "usdValue": "0", "borrowAmount": "0.0007",
            },
        ])
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")

        assert bal["base_borrowed_qty"] == pytest.approx(0.0007)
        assert bal["quote_borrowed_qty"] == 0.0
        # Every existing key still present — no contract change for
        # callers that don't read the new field.
        assert bal["base_qty"] == 0.0
        assert bal["base_usd_value"] == 0.0
        assert bal["quote_usdt"] == pytest.approx(89.0)

    def test_usdt_borrow_amount_exposed_as_quote_borrowed_qty(self):
        from src.units.accounts.execute import _fetch_spot_coin_balances

        client = _WalletStub([
            {
                "coin": "USDT", "walletBalance": "120", "locked": "0",
                "usdValue": "120", "borrowAmount": "55.5",
            },
            {
                "coin": "BTC", "walletBalance": "0.001", "locked": "0",
                "usdValue": "50", "borrowAmount": "0",
            },
        ])
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")

        assert bal["quote_borrowed_qty"] == pytest.approx(55.5)
        assert bal["base_borrowed_qty"] == 0.0

    def test_missing_borrow_amount_field_defaults_to_zero(self):
        """Cash-spot wallets don't carry ``borrowAmount`` at all.
        Default to 0.0 so existing callers stay byte-for-byte
        compatible.
        """
        from src.units.accounts.execute import _fetch_spot_coin_balances

        client = _WalletStub([
            {"coin": "USDT", "walletBalance": "100", "locked": "0",
             "usdValue": "100"},
            {"coin": "BTC", "walletBalance": "0.001", "locked": "0",
             "usdValue": "50"},
        ])
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        assert bal["base_borrowed_qty"] == 0.0
        assert bal["quote_borrowed_qty"] == 0.0

    def test_unparseable_borrow_amount_floors_to_zero(self):
        """Defensive: any string Bybit can't render parses to 0 —
        same shape as ``_coin_borrow_usd``."""
        from src.units.accounts.execute import _coin_borrowed_qty

        for raw in (None, "", "null", "n/a", "garbage"):
            assert _coin_borrowed_qty({"borrowAmount": raw}) == 0.0
        # Negative input floors to zero — guards against pathological
        # exchange responses.
        assert _coin_borrowed_qty({"borrowAmount": "-0.01"}) == 0.0


# ---------------------------------------------------------------------------
# 3. close_open_position post-close verify + repay
# ---------------------------------------------------------------------------


class TestCloseOpenPositionPostCloseRepay:
    """After a successful spot-margin close, the helper refetches the
    wallet and calls the repay wrapper for any residual borrow.
    """

    def _client_after_short_close(self, *, residual_borrow):
        """Wallet snapshot mimicking 'short was just closed but Bybit's
        auto-repay left ``borrowAmount(BTC)=residual_borrow``'.
        """
        return _RepayClient(
            wallet_response=_wallet_with([
                {"coin": "USDT", "walletBalance": "85", "locked": "0",
                 "usdValue": "85", "borrowAmount": "0"},
                {"coin": "BTC", "walletBalance": "0", "locked": "0",
                 "usdValue": "0",
                 "borrowAmount": str(residual_borrow)},
            ]),
            repay_response={"retCode": 0, "retMsg": "OK"},
        )

    def test_short_close_with_residual_borrow_triggers_repay(self):
        """Field-shape: post-close BTC borrowAmount > epsilon →
        ``_post_close_repay`` calls the wrapper with that exact qty.
        The audit row payload is captured on the close result.
        """
        from src.units.accounts.execute import close_open_position

        client = self._client_after_short_close(residual_borrow=0.0007)
        result = close_open_position(
            client, _spot_margin_account(),
            symbol="BTCUSDT", side="short", qty=0.0007,
        )

        assert result["ok"] is True
        assert result["error"] is None
        # The repay outcome rides on the close result.
        assert result["repay"] is not None
        assert result["repay"]["ok"] is True
        assert result["repay"]["coin"] == "BTC"
        assert result["repay"]["qty"] == pytest.approx(0.0007)
        # Wire call shape matches the wrapper.
        assert client.repay_calls == [{"coin": "BTC", "qty": "0.0007"}]

    def test_short_close_with_zero_residual_does_not_call_repay(self):
        """The common path: Bybit's auto-repay cleared the line.
        ``borrowAmount(BTC) ≈ 0`` → no repay attempted.
        """
        from src.units.accounts.execute import close_open_position

        client = self._client_after_short_close(residual_borrow=0.0)
        result = close_open_position(
            client, _spot_margin_account(),
            symbol="BTCUSDT", side="short", qty=0.001,
        )
        assert result["ok"] is True
        assert result["repay"] is None
        assert client.repay_calls == []

    def test_short_close_below_epsilon_does_not_call_repay(self):
        """Sub-epsilon residuals (Bybit dust e.g. 1e-7 BTC) round to
        zero — repaying 1.7e-7 trips retCode 170213 ("repay qty below
        precision"). The epsilon guard is what prevents the alert
        spam.
        """
        from src.units.accounts.execute import close_open_position

        client = self._client_after_short_close(residual_borrow=1e-8)
        result = close_open_position(
            client, _spot_margin_account(),
            symbol="BTCUSDT", side="short", qty=0.001,
        )
        assert result["ok"] is True
        assert result["repay"] is None
        assert client.repay_calls == []

    def test_long_close_with_residual_usdt_triggers_repay(self):
        """Symmetric for the long side: a leveraged long borrows USDT,
        residual ``borrowAmount(USDT)`` after close should be repaid.
        """
        from src.units.accounts.execute import close_open_position

        client = _RepayClient(
            wallet_response=_wallet_with([
                {"coin": "USDT", "walletBalance": "120", "locked": "0",
                 "usdValue": "120", "borrowAmount": "23.5"},
                {"coin": "BTC", "walletBalance": "0", "locked": "0",
                 "usdValue": "0", "borrowAmount": "0"},
            ]),
        )
        result = close_open_position(
            client, _spot_margin_account(),
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["repay"]["coin"] == "USDT"
        assert result["repay"]["qty"] == pytest.approx(23.5)
        assert client.repay_calls == [{"coin": "USDT", "qty": "23.5"}]

    def test_cash_spot_close_skips_repay(self):
        """A cash-spot account has no borrow line — the repay verify
        is a no-op there. Important guard: dispatching repay on a
        Classic-spot account would 401 every minute.
        """
        from src.units.accounts.execute import close_open_position

        client = _RepayClient(
            wallet_response=_wallet_with([
                {"coin": "USDT", "walletBalance": "120", "locked": "0",
                 "usdValue": "120"},
                {"coin": "BTC", "walletBalance": "0.001", "locked": "0",
                 "usdValue": "50"},
            ]),
        )
        result = close_open_position(
            client, _cash_spot_account(),
            symbol="BTCUSDT", side="long", qty=0.001,
        )
        assert result["ok"] is True
        # Crucial: repay key may exist with None, OR be omitted —
        # but ``client.repay`` must NOT have been called.
        assert result.get("repay") is None
        assert client.repay_calls == []

    def test_repay_failure_does_not_unwind_close(self):
        """A transient repay failure must NOT flip the close's
        ``ok=True`` to False — the close itself filled at the exchange.
        The standalone reconciler picks up the residual on the next
        sweep.
        """
        from src.units.accounts.execute import close_open_position

        client = _RepayClient(
            wallet_response=_wallet_with([
                {"coin": "USDT", "walletBalance": "85", "locked": "0",
                 "usdValue": "85", "borrowAmount": "0"},
                {"coin": "BTC", "walletBalance": "0", "locked": "0",
                 "usdValue": "0", "borrowAmount": "0.001"},
            ]),
            repay_raises=ConnectionError("transient"),
        )
        result = close_open_position(
            client, _spot_margin_account(),
            symbol="BTCUSDT", side="short", qty=0.001,
        )
        assert result["ok"] is True   # close still ok
        assert result["repay"]["ok"] is False
        assert "ConnectionError" in (result["repay"]["error"] or "")

    def test_close_failure_skips_repay(self):
        """When the close itself fails (Bybit rejects the order), the
        repay verify must NOT fire — there's no flatten, the borrow is
        intentional.
        """
        from src.units.accounts.execute import close_open_position

        client = _RepayClient(
            wallet_response=_wallet_with([
                {"coin": "BTC", "walletBalance": "0", "borrowAmount": "0.001"},
            ]),
        )
        # Override place_order to return a rejection.
        client.place_order = lambda **kwargs: {
            "retCode": 170131,
            "retMsg": "Insufficient balance",
        }
        result = close_open_position(
            client, _spot_margin_account(),
            symbol="BTCUSDT", side="short", qty=0.001,
        )
        assert result["ok"] is False
        assert client.repay_calls == []
