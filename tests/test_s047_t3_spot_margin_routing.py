"""S-047 T3 — D4 + D5 routing tests.

D4 (`feat(exec): route spot-margin orders via isLeverage=1`):
  - ``_is_spot_margin`` recognises the ``market_type: spot-margin`` label.
  - ``_bybit_category`` maps ``"spot-margin"`` to API category ``"spot"``.
  - ``_submit_order`` adds ``isLeverage=1`` for spot-margin Buy + Sell.
  - ``_submit_test_order`` adds ``isLeverage=1`` for the smoke path too.
  - ``close_open_position`` adds ``isLeverage=1`` so the close repays the
    borrow line instead of routing as a fresh cash-spot order.
  - The existing spot-sell pre-flight base-coin balance guard is
    SKIPPED for spot-margin (the system can borrow base coin).
  - Cash-spot accounts (``market_type: spot``) are unchanged — no
    ``isLeverage`` in kwargs.
  - Linear (perp) accounts are unchanged — no ``isLeverage`` in kwargs.

D5 (`feat(coordinator): direction-aware balance for spot-margin accounts`):
  - The coordinator forwards ``market_type`` as a primitive kwarg to
    ``RiskManager.position_size`` (so T2's spot-margin kernel fires).
  - ``size_order_from_cfg`` does the same for the direct
    ``account_execute`` path (no qty_override).

Compliance with § 4.4 / S-047 § 5b: tests assert that none of D4 / D5
introduces a new pre-flight refusal on spot-margin accounts. The
existing spot-sell guard is _removed_ for spot-margin (a refusal lifted,
not added); RiskManager's zero-qty returns remain the canonical
sizing-rule refusal mechanism.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.units.accounts.execute import (
    _bybit_category,
    _is_spot_margin,
    close_open_position,
    execute_pkg,
)


# OrderPackage is in src.core.coordinator (which transitively imports
# yaml). Match the existing test_spot_category_routing.py shim so the
# file remains importable in environments without PyYAML.
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


class _CapturingClient:
    """Minimal pybit.HTTP stand-in that records the last call kwargs."""

    def __init__(self, place_response=None, wallet_response=None,
                 instruments_response=None):
        self.place_kwargs = None
        self._place_response = place_response or {
            "retCode": 0, "result": {"orderId": "stub-spot-margin-id"},
        }
        # Default wallet has $1000 free USDT and 0 BTC — i.e., a USDT-
        # collateral-only account that can ONLY short BTC by borrowing.
        # Pre-T3, the spot-sell pre-flight refused this scenario; T3
        # makes it the happy path for spot-margin accounts.
        self._wallet_response = wallet_response or {
            "retCode": 0,
            "result": {"list": [{"coin": [
                {"coin": "USDT", "walletBalance": "1000",
                 "locked": "0", "usdValue": "1000"},
                {"coin": "BTC", "walletBalance": "0",
                 "locked": "0", "usdValue": "0"},
            ]}]},
        }
        # Stub instruments_info covers tick_size lookups.
        self._instruments_response = instruments_response or {
            "retCode": 0,
            "result": {"list": [{
                "symbol": "BTCUSDT",
                "priceFilter": {"tickSize": "0.01"},
                "lotSizeFilter": {"qtyStep": "0.001"},
                "status": "Trading",
            }]},
        }

    def place_order(self, **kwargs):
        self.place_kwargs = kwargs
        return self._place_response

    def get_wallet_balance(self, **kwargs):
        return self._wallet_response

    def get_instruments_info(self, **kwargs):
        return self._instruments_response


def _spot_margin_cfg() -> dict:
    return {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "market_type": "spot-margin",
        "risk_pct": 0.01,
        "min_balance_usd": 50,
    }


def _cash_spot_cfg() -> dict:
    return {
        "account_id": "bybit_1",
        "exchange": "bybit",
        "market_type": "spot",
        "risk_pct": 0.01,
        "min_balance_usd": 50,
    }


def _linear_cfg() -> dict:
    return {
        "account_id": "linear_test",
        "exchange": "bybit",
        "market_type": "linear",
        "risk_pct": 0.01,
        "min_balance_usd": 50,
    }


def _short_pkg() -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="short",
        entry=50_000.0,
        sl=51_000.0,
        tp=48_000.0,
        meta={"strategy_name": "vwap"},
    )


def _long_pkg() -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=50_000.0,
        sl=49_000.0,
        tp=52_000.0,
        meta={"strategy_name": "vwap"},
    )


# ---------------------------------------------------------------------------
# D4 — _is_spot_margin + _bybit_category routing
# ---------------------------------------------------------------------------


class TestSpotMarginLabel:
    def test_is_spot_margin_true_for_label(self):
        assert _is_spot_margin({"market_type": "spot-margin"}) is True

    def test_is_spot_margin_case_insensitive(self):
        assert _is_spot_margin({"market_type": "Spot-Margin"}) is True
        assert _is_spot_margin({"market_type": "SPOT-MARGIN"}) is True

    def test_is_spot_margin_false_for_cash_spot(self):
        assert _is_spot_margin({"market_type": "spot"}) is False

    def test_is_spot_margin_false_for_linear(self):
        assert _is_spot_margin({"market_type": "linear"}) is False

    def test_is_spot_margin_false_when_missing(self):
        assert _is_spot_margin({}) is False

    def test_bybit_category_resolves_spot_margin_to_spot(self):
        # Bybit V5 has no "spot-margin" category; the API category is
        # "spot" and isLeverage=1 communicates the margin trait.
        assert _bybit_category({"market_type": "spot-margin"}) == "spot"

    def test_bybit_category_unchanged_for_other_values(self):
        assert _bybit_category({"market_type": "spot"}) == "spot"
        assert _bybit_category({"market_type": "linear"}) == "linear"
        assert _bybit_category({}) == "spot"


# ---------------------------------------------------------------------------
# D4 — execute_pkg routes spot-margin orders with isLeverage=1
# ---------------------------------------------------------------------------


class TestSpotMarginRoutingShortLeg:
    def test_short_routes_with_isLeverage_1(self):
        client = _CapturingClient()
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _short_pkg(),
                _spot_margin_cfg(),
                exchange_client=client,
                balance_usdt=1_000.0,
                dry_run=False,
                qty_override=0.002,
            )
        assert client.place_kwargs is not None
        assert client.place_kwargs["category"] == "spot"
        assert client.place_kwargs["side"] == "Sell"
        assert client.place_kwargs["isLeverage"] == 1
        assert client.place_kwargs["marketUnit"] == "baseCoin"

    def test_short_skips_spot_sell_preflight(self):
        # Wallet holds 0 BTC (USDT-collateral-only). Cash-spot would
        # refuse this Sell pre-flight; spot-margin must let it through
        # because the account can borrow BTC. Regression on the most
        # important behaviour change in D4.
        client = _CapturingClient()
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            trade_id = execute_pkg(
                _short_pkg(),
                _spot_margin_cfg(),
                exchange_client=client,
                balance_usdt=1_000.0,
                dry_run=False,
                qty_override=0.002,
            )
        assert trade_id == "stub-spot-margin-id"
        assert client.place_kwargs is not None  # order actually placed


class TestSpotMarginRoutingLongLeg:
    def test_long_routes_with_isLeverage_1(self):
        # The user's live error: a vwap Buy on bybit_2 with $177 USDT
        # but qty=0.002 BTC notional ≈ $100 — pre-T3 the cash-spot Buy
        # returned 170131. With isLeverage=1 the wallet borrows USDT
        # and the Buy completes (after the operator enables Spot
        # Margin on the Bybit web UI).
        client = _CapturingClient()
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _long_pkg(),
                _spot_margin_cfg(),
                exchange_client=client,
                balance_usdt=177.0,
                dry_run=False,
                qty_override=0.002,
            )
        assert client.place_kwargs is not None
        assert client.place_kwargs["category"] == "spot"
        assert client.place_kwargs["side"] == "Buy"
        assert client.place_kwargs["isLeverage"] == 1
        assert client.place_kwargs["marketUnit"] == "baseCoin"


class TestSpotMarginCloseRouting:
    def test_close_short_routes_with_isLeverage_1(self):
        client = _CapturingClient()
        result = close_open_position(
            client, _spot_margin_cfg(),
            symbol="BTCUSDT",
            side="short",
            qty=0.002,
        )
        assert result["ok"] is True
        # Closing a short = Buy back BTC to repay the borrow.
        assert client.place_kwargs["side"] == "Buy"
        assert client.place_kwargs["category"] == "spot"
        assert client.place_kwargs["isLeverage"] == 1
        assert client.place_kwargs["marketUnit"] == "baseCoin"
        # No reduceOnly on spot — would be rejected by Bybit V5.
        assert "reduceOnly" not in client.place_kwargs

    def test_close_long_routes_with_isLeverage_1(self):
        client = _CapturingClient()
        result = close_open_position(
            client, _spot_margin_cfg(),
            symbol="BTCUSDT",
            side="long",
            qty=0.002,
        )
        assert result["ok"] is True
        assert client.place_kwargs["side"] == "Sell"
        assert client.place_kwargs["isLeverage"] == 1


# ---------------------------------------------------------------------------
# D4 — Cash-spot regression (must NOT add isLeverage)
# ---------------------------------------------------------------------------


class TestCashSpotUnchanged:
    def test_cash_spot_long_has_no_isLeverage(self):
        client = _CapturingClient(wallet_response={
            "retCode": 0,
            "result": {"list": [{"coin": [
                {"coin": "USDT", "walletBalance": "10000",
                 "locked": "0", "usdValue": "10000"},
            ]}]},
        })
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _long_pkg(),
                _cash_spot_cfg(),
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.001,
            )
        assert client.place_kwargs is not None
        assert client.place_kwargs["category"] == "spot"
        assert "isLeverage" not in client.place_kwargs

    def test_cash_spot_short_preserves_preflight(self):
        # bybit_1-style: holds BTC + USDT, market_type=spot. The
        # pre-flight base-coin balance guard MUST still fire here
        # (D4 only skips it for spot-margin). Wallet below caps qty.
        client = _CapturingClient(wallet_response={
            "retCode": 0,
            "result": {"list": [{"coin": [
                {"coin": "USDT", "walletBalance": "10000",
                 "locked": "0", "usdValue": "10000"},
                {"coin": "BTC", "walletBalance": "0",
                 "locked": "0", "usdValue": "0"},
            ]}]},
        })
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            with pytest.raises(RuntimeError, match="insufficient free"):
                execute_pkg(
                    _short_pkg(),
                    _cash_spot_cfg(),
                    exchange_client=client,
                    balance_usdt=10_000.0,
                    dry_run=False,
                    qty_override=0.002,
                )

    def test_close_cash_spot_has_no_isLeverage(self):
        client = _CapturingClient()
        close_open_position(
            client, _cash_spot_cfg(),
            symbol="BTCUSDT",
            side="long",
            qty=0.002,
        )
        assert "isLeverage" not in client.place_kwargs


class TestLinearUnchanged:
    def test_linear_has_no_isLeverage(self):
        client = _CapturingClient()
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _long_pkg(),
                _linear_cfg(),
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.001,
            )
        assert client.place_kwargs["category"] == "linear"
        assert "isLeverage" not in client.place_kwargs


# ---------------------------------------------------------------------------
# D4 — Smoke / test order path (meta.is_test=True)
# ---------------------------------------------------------------------------


class TestSmokeTestOrderPath:
    def test_smoke_spot_margin_order_passes_isLeverage(self):
        client = _CapturingClient(place_response={
            "retCode": 170140, "retMsg": "below min lot",
        })
        smoke_pkg = OrderPackage(
            strategy="smoke_test",
            symbol="BTCUSDT",
            direction="short",
            entry=50_000.0,
            sl=51_000.0,
            tp=48_000.0,
            meta={"is_test": True, "test_qty": 0.0001},
        )
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            trade_id = execute_pkg(
                smoke_pkg,
                _spot_margin_cfg(),
                exchange_client=client,
                balance_usdt=1_000.0,
                dry_run=False,
            )
        assert trade_id.startswith("rejected_too_small:")
        assert client.place_kwargs["isLeverage"] == 1
        assert client.place_kwargs["category"] == "spot"

    def test_smoke_cash_spot_order_has_no_isLeverage(self):
        client = _CapturingClient(place_response={
            "retCode": 170140, "retMsg": "below min lot",
        })
        smoke_pkg = OrderPackage(
            strategy="smoke_test",
            symbol="BTCUSDT",
            direction="short",
            entry=50_000.0,
            sl=51_000.0,
            tp=48_000.0,
            meta={"is_test": True, "test_qty": 0.0001},
        )
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                smoke_pkg,
                _cash_spot_cfg(),
                exchange_client=client,
                balance_usdt=1_000.0,
                dry_run=False,
            )
        assert "isLeverage" not in client.place_kwargs


# ---------------------------------------------------------------------------
# D5 — size_order_from_cfg forwards market_type to position_size
# ---------------------------------------------------------------------------


class TestSizeOrderFromCfgForwardsMarketType:
    def test_spot_margin_cfg_triggers_kernel(self):
        """When market_type='spot-margin' the spot-margin kernel runs."""
        from src.units.accounts.risk import RiskManager, size_order_from_cfg

        cfg = {
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "max_borrow_btc": 0.001,    # tiny cap to make the rule observable
            "borrow_fee_apr_pct": 10.0,
            "liquidation_buffer_pct": 30.0,
            "market_type": "spot-margin",
        }
        # Long with notional > collateral (forces borrow) so the
        # liquidation-buffer rule runs. balance=$200, qty=0.05 BTC at
        # $50k entry = $2500 notional → liq_distance≈$4000 → buffer
        # check: risk_distance=$1000 vs (1-0.3)*4000=$2800 → passes.
        # The max_borrow_btc=0.001 cap then clips qty.
        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="long",
            entry=50_000.0, sl=49_000.0, tp=52_000.0, meta={},
        )
        sized = size_order_from_cfg(pkg, cfg, balance_usdt=10_000.0)
        # Expect the max_borrow_btc cap to be the binding constraint;
        # the unconstrained qty would be much larger.
        assert sized <= 0.001

        # Compare against the same call without the spot-margin label —
        # should NOT be capped at 0.001.
        cfg_cash = dict(cfg)
        cfg_cash["market_type"] = "spot"
        rm = RiskManager(cfg_cash)
        unconstrained = rm.position_size(pkg, 10_000.0, market_type="spot")
        assert unconstrained > 0.001  # cap ignored on cash-spot

    def test_default_market_type_is_spot(self):
        """A cfg dict without market_type defaults to spot — no kernel."""
        from src.units.accounts.risk import size_order_from_cfg

        cfg = {
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "max_borrow_btc": 0.001,    # would clip but kernel never runs
        }
        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="long",
            entry=50_000.0, sl=49_000.0, tp=52_000.0, meta={},
        )
        sized = size_order_from_cfg(pkg, cfg, balance_usdt=10_000.0)
        # qty for $10k * 1% = $100 risk / $1000 sl distance = 0.1 BTC.
        # Far above the spot-margin cap, but the kernel never runs
        # since market_type defaults to "spot".
        assert sized > 0.001


# ---------------------------------------------------------------------------
# D5 — coordinator.multi_account_execute forwards market_type
# ---------------------------------------------------------------------------


class TestCoordinatorForwardsMarketType:
    """Surgical test: the coordinator's call to position_size includes
    ``market_type=<account.market_type>`` for both spot-margin and
    non-spot-margin accounts. We don't spin up the full multi_account
    pipeline — too much I/O — we patch ``RiskManager.position_size`` to
    capture the kwargs and assert.
    """

    def _account_stub(self, name: str, market_type: str):
        from src.units.accounts.risk import RiskManager

        acc = MagicMock()
        acc.name = name
        acc.exchange = "bybit"
        acc.api_key_env = "BYBIT_API_KEY_FAKE"
        acc.account_type = "regular"
        acc.market_type = market_type
        acc.dry_run = False
        acc.cached_balance_usd = 1_000.0
        acc.strategies = ["vwap"]
        acc.risk_manager = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
        })
        return acc

    def test_spot_margin_account_passes_market_type_keyword(self):
        from src.core.coordinator import Coordinator

        acc = self._account_stub("bybit_2", "spot-margin")
        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            return_value=0.0,    # short-circuit downstream submission
        ) as mock_size, patch(
            "src.units.accounts.load_accounts",
            return_value=[acc],
        ), patch(
            "src.core.coordinator._log_new_order_package",
            return_value="pkg-fake",
        ):
            coord = Coordinator()
            coord.multi_account_execute(
                _long_pkg(),
                accounts_path="/dev/null",
                balance_fetcher=lambda a: 1_000.0,
            )
        assert mock_size.called
        _args, kwargs = mock_size.call_args
        assert kwargs.get("market_type") == "spot-margin"

    def test_cash_spot_account_passes_market_type_spot(self):
        from src.core.coordinator import Coordinator

        acc = self._account_stub("bybit_1", "spot")
        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            return_value=0.0,
        ) as mock_size, patch(
            "src.units.accounts.load_accounts",
            return_value=[acc],
        ), patch(
            "src.core.coordinator._log_new_order_package",
            return_value="pkg-fake",
        ):
            coord = Coordinator()
            coord.multi_account_execute(
                _long_pkg(),
                accounts_path="/dev/null",
                balance_fetcher=lambda a: 1_000.0,
            )
        _args, kwargs = mock_size.call_args
        assert kwargs.get("market_type") == "spot"


# ---------------------------------------------------------------------------
# D5 — coordinator's direction-aware balance fetch returns USDT for both
#       directions on spot-margin accounts.
# ---------------------------------------------------------------------------


class TestSpotMarginBalanceForBothDirections:
    """For ``market_type: spot-margin`` the coordinator's spot-balance
    override returns USDT collateral for SHORTS too. Cash-spot SHORTS
    still return base-coin USD value (the pre-T3 contract).
    """

    def _account_stub(self, market_type: str):
        from src.units.accounts.risk import RiskManager

        acc = MagicMock()
        acc.name = "bybit_2"
        acc.exchange = "bybit"
        acc.api_key_env = "BYBIT_API_KEY_FAKE"
        acc.account_type = "regular"
        acc.market_type = market_type
        acc.dry_run = False
        acc.cached_balance_usd = 99_999.0   # sentinel to detect override
        acc.strategies = ["vwap"]
        acc.risk_manager = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
        })
        return acc

    def _spot_balances_stub(self):
        # USDT collateral=$177 (matches the user's live wallet); 0 BTC.
        return {
            "base_coin": "BTC",
            "base_qty": 0.0,
            "base_usd_value": 0.0,
            "quote_usdt": 177.0,
        }

    def test_spot_margin_short_uses_usdt_collateral(self):
        from src.core.coordinator import Coordinator

        acc = self._account_stub("spot-margin")
        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            return_value=0.0,
        ) as mock_size, patch(
            "src.units.accounts.load_accounts",
            return_value=[acc],
        ), patch(
            "src.core.coordinator._log_new_order_package",
            return_value="pkg-fake",
        ), patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=MagicMock(),    # just needs to be non-None
        ), patch(
            "src.units.accounts.execute._fetch_spot_coin_balances",
            return_value=self._spot_balances_stub(),
        ):
            coord = Coordinator()
            coord.multi_account_execute(
                _short_pkg(),
                accounts_path="/dev/null",
                balance_fetcher=lambda a: 1_000.0,
            )
        # SHORT on spot-margin must size from USDT (177), not BTC (0).
        positional, _kwargs = mock_size.call_args
        # position_size(pkg, balance, market_type=...)
        balance_arg = positional[1]
        assert abs(balance_arg - 177.0) < 1e-6

    def test_spot_margin_long_uses_usdt_collateral(self):
        from src.core.coordinator import Coordinator

        acc = self._account_stub("spot-margin")
        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            return_value=0.0,
        ) as mock_size, patch(
            "src.units.accounts.load_accounts",
            return_value=[acc],
        ), patch(
            "src.core.coordinator._log_new_order_package",
            return_value="pkg-fake",
        ), patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=MagicMock(),
        ), patch(
            "src.units.accounts.execute._fetch_spot_coin_balances",
            return_value=self._spot_balances_stub(),
        ):
            coord = Coordinator()
            coord.multi_account_execute(
                _long_pkg(),
                accounts_path="/dev/null",
                balance_fetcher=lambda a: 1_000.0,
            )
        positional, _kwargs = mock_size.call_args
        balance_arg = positional[1]
        assert abs(balance_arg - 177.0) < 1e-6

    def test_cash_spot_short_still_uses_base_usd_value(self):
        # Pre-T3 contract: cash-spot SHORTS size from BTC USD value. T3
        # must NOT change this — only spot-margin gets the new
        # both-directions-USDT semantics.
        from src.core.coordinator import Coordinator

        acc = self._account_stub("spot")
        spot_balances = {
            "base_coin": "BTC",
            "base_qty": 0.05,
            "base_usd_value": 2_500.0,    # 0.05 BTC × $50k
            "quote_usdt": 177.0,
        }
        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            return_value=0.0,
        ) as mock_size, patch(
            "src.units.accounts.load_accounts",
            return_value=[acc],
        ), patch(
            "src.core.coordinator._log_new_order_package",
            return_value="pkg-fake",
        ), patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=MagicMock(),
        ), patch(
            "src.units.accounts.execute._fetch_spot_coin_balances",
            return_value=spot_balances,
        ):
            coord = Coordinator()
            coord.multi_account_execute(
                _short_pkg(),
                accounts_path="/dev/null",
                balance_fetcher=lambda a: 1_000.0,
            )
        positional, _kwargs = mock_size.call_args
        balance_arg = positional[1]
        # SHORT on cash-spot uses base_usd_value, not quote_usdt.
        assert abs(balance_arg - 2_500.0) < 1e-6
