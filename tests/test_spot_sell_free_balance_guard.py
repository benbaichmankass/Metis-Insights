"""Regression: Bybit spot Sell pre-flight uses *free* balance, not total.

Recurrence guard for ErrCode 170131 ("Insufficient balance"). The prior
fix (PR #441) added a pre-flight balance check but read the V5 wallet
``walletBalance`` field directly. ``walletBalance`` is the *total* coin
holding — it includes amounts locked in open orders, recent-deposit
holds, and (UTA) cross-margin commitments. A balance-equal Sell on a
wallet with any locked portion still hits 170131 because Bybit only
considers ``walletBalance − locked``.

These tests pin three behaviours so the guard cannot regress:

* ``_fetch_spot_coin_balances`` returns ``walletBalance − locked`` as
  the free base/quote qty, and scales ``usdValue`` proportionally so
  the sizer never treats locked BTC as risk capital.
* The pre-flight guard caps a balance-equal Sell qty using a 0.5%
  safety buffer and refuses cleanly when even the free balance is
  below the configured ``min_qty``.
* When ``locked`` is missing/null (older response shapes / non-UTA
  accounts), the helper falls back to ``walletBalance`` so existing
  spot-only accounts keep working.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.units.accounts.execute import (
    _coin_free,
    _fetch_spot_coin_balances,
    _SPOT_SELL_SAFETY_BUFFER,
    execute_pkg,
)

try:
    from src.core.coordinator import OrderPackage  # type: ignore
except Exception:  # noqa: BLE001
    class OrderPackage:  # type: ignore[no-redef]
        def __init__(self, strategy, symbol, direction, entry, sl, tp,
                     meta=None, confidence=0.0):
            self.strategy = strategy
            self.symbol = symbol
            self.direction = direction
            self.entry = entry
            self.sl = sl
            self.tp = tp
            self.meta = meta or {}
            self.confidence = confidence


def _short_pkg(symbol: str = "BTCUSDT") -> OrderPackage:
    return OrderPackage(
        strategy="vwap", symbol=symbol, direction="short",
        entry=130_000.0, sl=131_300.0, tp=128_400.0,
    )


class _StubClient:
    """Captures ``place_order`` kwargs, returns a configurable wallet."""

    def __init__(self, wallet_coins):
        self._wallet = {
            "retCode": 0,
            "result": {"list": [{"coin": wallet_coins}]},
        }
        self.place_kwargs = None

    def get_wallet_balance(self, **_):
        return self._wallet

    def place_order(self, **kwargs):
        self.place_kwargs = kwargs
        return {"retCode": 0, "result": {"orderId": "stub-id"}}

    # Spot path doesn't call get_instruments_info, but provide a stub
    # so any defensive call doesn't blow up.
    def get_instruments_info(self, **_):
        return {"retCode": 0, "result": {"list": []}}


# ---------------------------------------------------------------------------
# _coin_free: locked-aware free balance
# ---------------------------------------------------------------------------


class TestCoinFree:
    def test_subtracts_locked_from_walletBalance(self):
        # 0.005 total, 0.001 locked → 0.004 free.
        assert _coin_free({"walletBalance": "0.005", "locked": "0.001"}) == \
            pytest.approx(0.004)

    def test_locked_zero_returns_walletBalance(self):
        assert _coin_free({"walletBalance": "0.005", "locked": "0"}) == \
            pytest.approx(0.005)

    def test_missing_locked_falls_back_to_walletBalance(self):
        # Older response shapes / non-UTA accounts may omit ``locked``.
        # Don't refuse trades just because we can't see a lock; behave
        # like the pre-fix code in that case.
        assert _coin_free({"walletBalance": "0.005"}) == pytest.approx(0.005)
        assert _coin_free({"walletBalance": "0.005", "locked": None}) == \
            pytest.approx(0.005)
        assert _coin_free({"walletBalance": "0.005", "locked": ""}) == \
            pytest.approx(0.005)

    def test_locked_greater_than_wallet_floors_at_zero(self):
        # Pathological state during cross-margin liquidation; never
        # propagate a negative cap to the sizer.
        assert _coin_free({"walletBalance": "0.001", "locked": "0.005"}) == 0.0

    def test_non_numeric_locked_falls_back_to_walletBalance(self):
        assert _coin_free({"walletBalance": "0.005", "locked": "n/a"}) == \
            pytest.approx(0.005)


# ---------------------------------------------------------------------------
# _fetch_spot_coin_balances: free qty + proportional usdValue
# ---------------------------------------------------------------------------


class TestFetchSpotCoinBalances:
    def test_returns_free_base_and_scales_usd_value(self):
        # 0.005 BTC total worth $650, with 0.001 locked → free 0.004,
        # free USD value 0.004/0.005 × 650 = 520.
        client = _StubClient([
            {"coin": "BTC", "walletBalance": "0.005",
             "locked": "0.001", "usdValue": "650"},
            {"coin": "USDT", "walletBalance": "100", "locked": "0"},
        ])
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        assert bal["base_coin"] == "BTC"
        assert bal["base_qty"] == pytest.approx(0.004)
        assert bal["base_usd_value"] == pytest.approx(520.0)
        assert bal["quote_usdt"] == pytest.approx(100.0)

    def test_returns_free_usdt_when_partially_locked(self):
        client = _StubClient([
            {"coin": "BTC", "walletBalance": "0.01", "locked": "0",
             "usdValue": "1300"},
            {"coin": "USDT", "walletBalance": "200", "locked": "50"},
        ])
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        assert bal["quote_usdt"] == pytest.approx(150.0)

    def test_zero_total_does_not_divide_by_zero(self):
        client = _StubClient([
            {"coin": "BTC", "walletBalance": "0", "locked": "0",
             "usdValue": "0"},
            {"coin": "USDT", "walletBalance": "0", "locked": "0"},
        ])
        bal = _fetch_spot_coin_balances(client, "BTCUSDT")
        assert bal["base_qty"] == 0.0
        assert bal["base_usd_value"] == 0.0
        assert bal["quote_usdt"] == 0.0


# ---------------------------------------------------------------------------
# Pre-flight guard inside execute_pkg
# ---------------------------------------------------------------------------


class TestSpotSellPreflightGuard:
    """Mirrors the live failure: balance-equal Sell at 0.005 BTC where
    a portion is locked."""

    @pytest.fixture
    def account_cfg(self):
        return {
            "account_id": "bybit_2",
            "exchange": "bybit",
            "market_type": "spot",
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "min_qty": 0.001,
            "qty_precision": 3,
        }

    def test_caps_qty_when_locked_reduces_free_below_requested(self, account_cfg):
        # Wallet shows 0.005 BTC total but 0.001 locked → free 0.004.
        # Requested qty 0.005 must be capped to ≤ 0.004 × 0.995 = 0.00398
        # which floors to 0.003 at qty_precision=3.
        client = _StubClient([
            {"coin": "BTC", "walletBalance": "0.005",
             "locked": "0.001", "usdValue": "650"},
            {"coin": "USDT", "walletBalance": "100", "locked": "0"},
        ])
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _short_pkg(), account_cfg,
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.005,
            )
        assert client.place_kwargs is not None, \
            "expected order to be submitted with capped qty, not refused"
        assert float(client.place_kwargs["qty"]) <= 0.004
        assert client.place_kwargs["side"] == "Sell"
        assert client.place_kwargs["category"] == "spot"
        assert client.place_kwargs["marketUnit"] == "baseCoin"

    def test_refuses_when_only_locked_balance_held(self, account_cfg):
        # Pathological: wallet has 0.005 BTC but it's all locked. Free=0
        # → must refuse cleanly without round-tripping to Bybit.
        client = _StubClient([
            {"coin": "BTC", "walletBalance": "0.005",
             "locked": "0.005", "usdValue": "650"},
            {"coin": "USDT", "walletBalance": "100", "locked": "0"},
        ])
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            with pytest.raises(RuntimeError, match="insufficient free"):
                execute_pkg(
                    _short_pkg(), account_cfg,
                    exchange_client=client,
                    balance_usdt=10_000.0,
                    dry_run=False,
                    qty_override=0.005,
                )
        assert client.place_kwargs is None, \
            "must not submit to Bybit when free balance is zero"

    def test_safety_buffer_protects_balance_equal_sell(self, account_cfg):
        # Wallet shows 0.005 BTC, nothing locked. Free=0.005, but the
        # 0.5% safety buffer means we cap to 0.004975 and floor to 0.004
        # so a microsecond-late lock from Bybit's side can't tip us into
        # 170131.
        client = _StubClient([
            {"coin": "BTC", "walletBalance": "0.005",
             "locked": "0", "usdValue": "650"},
            {"coin": "USDT", "walletBalance": "100", "locked": "0"},
        ])
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _short_pkg(), account_cfg,
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.005,
            )
        assert client.place_kwargs is not None
        sent_qty = float(client.place_kwargs["qty"])
        # Capped strictly below total free balance (race headroom).
        assert sent_qty < 0.005
        assert sent_qty <= 0.005 * _SPOT_SELL_SAFETY_BUFFER + 1e-9

    def test_passes_through_when_qty_well_below_free(self, account_cfg):
        # Free 0.01 BTC, requested 0.001 — no cap needed, sizer's qty
        # passes straight through.
        client = _StubClient([
            {"coin": "BTC", "walletBalance": "0.01",
             "locked": "0", "usdValue": "1300"},
            {"coin": "USDT", "walletBalance": "100", "locked": "0"},
        ])
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _short_pkg(), account_cfg,
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.001,
            )
        assert client.place_kwargs is not None
        assert float(client.place_kwargs["qty"]) == pytest.approx(0.001)
