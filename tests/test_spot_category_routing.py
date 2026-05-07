"""Regression: Bybit order placement must respect the per-account
``market_type`` field (operator directive 2026-05-06).

Pre-fix, ``src/units/accounts/execute.py`` and
``src/units/accounts/clients.py`` hardcoded ``category="linear"`` on
every Bybit place_order, set_trading_stop, get_positions, and reduce-
only close call. That meant BTCUSDT signals always landed on the USDT-
margined perp book even when the operator's wallet held the spot pair.

The fix:

* ``_bybit_category(account_cfg)`` resolves ``market_type`` (default
  ``"spot"``) into a Bybit V5 category string.
* ``_submit_order`` / ``_submit_test_order`` pass that category and add
  ``marketUnit="baseCoin"`` for spot so qty is interpreted as BTC, not
  USDT.
* ``modify_open_order`` refuses cleanly for spot (set_trading_stop is
  derivatives-only).
* ``close_open_position`` drops ``reduceOnly`` for spot.
* ``account_open_positions`` returns ``[]`` for spot accounts (no
  derivative-style positions to query).

Tests use stub clients to capture kwargs; no live network calls.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.units.accounts.execute import (
    _bybit_category,
    close_open_position,
    execute_pkg,
    modify_open_order,
)

# OrderPackage is in src.core.coordinator which transitively imports
# yaml. The unit tests in this file only need a tiny shim with the
# attributes execute_pkg reads — keep them runnable in sandboxes that
# do not have PyYAML installed.
try:
    from src.core.coordinator import OrderPackage  # type: ignore
    _HAS_COORDINATOR = True
except Exception:  # noqa: BLE001
    _HAS_COORDINATOR = False

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

    def __init__(self, place_response=None, get_positions_response=None,
                 set_trading_stop_response=None,
                 wallet_balance_response=None):
        self.place_kwargs = None
        self.set_trading_stop_kwargs = None
        self.get_positions_kwargs = None
        self.get_wallet_balance_kwargs = None
        self._place_response = place_response or {
            "retCode": 0, "result": {"orderId": "stub-order-id"},
        }
        self._get_positions_response = get_positions_response or {
            "retCode": 0, "result": {"list": []},
        }
        self._set_trading_stop_response = set_trading_stop_response or {
            "retCode": 0, "result": {},
        }
        self._wallet_balance_response = wallet_balance_response or {
            "retCode": 0,
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]},
        }

    def place_order(self, **kwargs):
        self.place_kwargs = kwargs
        return self._place_response

    def set_trading_stop(self, **kwargs):
        self.set_trading_stop_kwargs = kwargs
        return self._set_trading_stop_response

    def get_positions(self, **kwargs):
        self.get_positions_kwargs = kwargs
        return self._get_positions_response

    def get_wallet_balance(self, **kwargs):
        self.get_wallet_balance_kwargs = kwargs
        return self._wallet_balance_response


def _pkg(strategy: str = "turtle_soup", direction: str = "long") -> OrderPackage:
    return OrderPackage(
        strategy=strategy,
        symbol="BTCUSDT",
        direction=direction,
        entry=50_000.0,
        sl=49_000.0,
        tp=52_000.0,
        meta={"strategy_name": strategy},
    )


# ---------------------------------------------------------------------------
# _bybit_category resolver
# ---------------------------------------------------------------------------


class TestBybitCategoryResolver:
    def test_default_is_spot_when_field_missing(self):
        assert _bybit_category({}) == "spot"

    def test_explicit_spot(self):
        assert _bybit_category({"market_type": "spot"}) == "spot"

    def test_explicit_linear(self):
        assert _bybit_category({"market_type": "linear"}) == "linear"

    def test_perp_alias_normalises_to_linear(self):
        assert _bybit_category({"market_type": "perp"}) == "linear"
        assert _bybit_category({"market_type": "perpetual"}) == "linear"
        assert _bybit_category({"market_type": "futures"}) == "linear"

    def test_unknown_value_falls_back_to_spot(self):
        # Operator typo / bad config must not silently route to perp.
        assert _bybit_category({"market_type": "margin"}) == "spot"
        assert _bybit_category({"market_type": ""}) == "spot"


# ---------------------------------------------------------------------------
# _submit_order: live order routes spot/linear correctly
# ---------------------------------------------------------------------------


class TestSubmitOrderRouting:
    def test_spot_account_routes_category_spot_with_marketUnit(self):
        client = _CapturingClient()
        account_cfg = {
            "account_id": "bybit_1",
            "exchange": "bybit",
            "market_type": "spot",
            "risk_pct": 0.01,
            "min_balance_usd": 50,
        }
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            trade_id = execute_pkg(
                _pkg(),
                account_cfg,
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.001,
            )
        assert trade_id == "stub-order-id"
        assert client.place_kwargs is not None
        assert client.place_kwargs["category"] == "spot"
        assert client.place_kwargs["marketUnit"] == "baseCoin"
        assert client.place_kwargs["symbol"] == "BTCUSDT"
        assert client.place_kwargs["side"] == "Buy"
        assert client.place_kwargs["orderType"] == "Market"
        # BUG-061: Bybit V5 rejects spot Market orders that include
        # stopLoss/takeProfit with retCode 170130. SL/TP are enforced by
        # the S-030 monitor loop via close_open_position instead.
        assert "stopLoss" not in client.place_kwargs
        assert "takeProfit" not in client.place_kwargs

    def test_linear_account_routes_category_linear_without_marketUnit(self):
        client = _CapturingClient()
        account_cfg = {
            "account_id": "bybit_legacy_perp",
            "exchange": "bybit",
            "market_type": "linear",
            "risk_pct": 0.01,
            "min_balance_usd": 50,
        }
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _pkg(direction="short"),
                account_cfg,
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.001,
            )
        assert client.place_kwargs["category"] == "linear"
        assert "marketUnit" not in client.place_kwargs
        assert client.place_kwargs["side"] == "Sell"
        # Derivatives still carry SL/TP on the entry — the spot-only
        # restriction in BUG-061 must not regress the linear path.
        assert "stopLoss" in client.place_kwargs
        assert "takeProfit" in client.place_kwargs

    def test_default_account_without_market_type_is_spot(self):
        """An account_cfg with no market_type field must default to spot
        — the regression mode for the perp-instead-of-spot bug."""
        client = _CapturingClient()
        account_cfg = {
            "account_id": "bybit_silent",
            "exchange": "bybit",
            "risk_pct": 0.01,
            "min_balance_usd": 50,
        }
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            return_value=True,
        ):
            execute_pkg(
                _pkg(),
                account_cfg,
                exchange_client=client,
                balance_usdt=10_000.0,
                dry_run=False,
                qty_override=0.001,
            )
        assert client.place_kwargs["category"] == "spot"


# ---------------------------------------------------------------------------
# modify_open_order: spot must refuse cleanly, linear must succeed
# ---------------------------------------------------------------------------


class TestModifyOpenOrderRouting:
    def test_spot_returns_ok_false_with_clear_error(self):
        client = _CapturingClient()
        result = modify_open_order(
            client,
            {"account_id": "bybit_1", "exchange": "bybit", "market_type": "spot"},
            symbol="BTCUSDT",
            sl=49_000.0,
            tp=52_000.0,
        )
        assert result["ok"] is False
        assert "spot" in result["error"]
        # Crucially, the SDK was never called — Bybit returns retCode=10001
        # for set_trading_stop on spot, which the previous code path
        # would have silently surfaced as an exchange error.
        assert client.set_trading_stop_kwargs is None

    def test_linear_calls_set_trading_stop_with_category_linear(self):
        client = _CapturingClient()
        result = modify_open_order(
            client,
            {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"},
            symbol="BTCUSDT",
            sl=49_000.0,
            tp=52_000.0,
        )
        assert result["ok"] is True
        assert client.set_trading_stop_kwargs["category"] == "linear"
        assert client.set_trading_stop_kwargs["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# close_open_position: spot omits reduceOnly, linear keeps it
# ---------------------------------------------------------------------------


class TestCloseOpenPositionRouting:
    def test_spot_close_omits_reduceOnly_and_sets_marketUnit(self):
        client = _CapturingClient()
        result = close_open_position(
            client,
            {"account_id": "bybit_1", "exchange": "bybit", "market_type": "spot"},
            symbol="BTCUSDT",
            side="long",
            qty=0.001,
        )
        assert result["ok"] is True
        assert client.place_kwargs["category"] == "spot"
        assert "reduceOnly" not in client.place_kwargs
        assert client.place_kwargs["marketUnit"] == "baseCoin"
        assert client.place_kwargs["side"] == "Sell"  # closing a long spot

    def test_linear_close_keeps_reduceOnly_true(self):
        client = _CapturingClient()
        result = close_open_position(
            client,
            {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"},
            symbol="BTCUSDT",
            side="long",
            qty=0.001,
        )
        assert result["ok"] is True
        assert client.place_kwargs["category"] == "linear"
        assert client.place_kwargs["reduceOnly"] is True


# ---------------------------------------------------------------------------
# account_open_positions: spot returns [] (no derivative-style positions)
# ---------------------------------------------------------------------------


class TestAccountOpenPositionsRouting:
    def test_spot_account_returns_empty_list_without_calling_get_positions(self):
        from src.units.accounts import clients as clients_mod

        client = _CapturingClient()
        with patch.object(clients_mod, "bybit_client_for", return_value=client):
            result = clients_mod.account_open_positions({
                "account_id": "bybit_1",
                "exchange": "bybit",
                "market_type": "spot",
            })
        assert result == []
        assert client.get_positions_kwargs is None

    def test_linear_account_calls_get_positions_with_category_linear(self):
        from src.units.accounts import clients as clients_mod

        client = _CapturingClient(get_positions_response={
            "retCode": 0,
            "result": {"list": [{
                "symbol": "BTCUSDT", "side": "Buy", "size": "0.001",
                "avgPrice": "50000", "unrealisedPnl": "1.0",
            }]},
        })
        with patch.object(clients_mod, "bybit_client_for", return_value=client):
            result = clients_mod.account_open_positions({
                "account_id": "bybit_legacy",
                "exchange": "bybit",
                "market_type": "linear",
            })
        assert isinstance(result, list)
        assert len(result) == 1
        assert client.get_positions_kwargs["category"] == "linear"


# ---------------------------------------------------------------------------
# End-to-end: accounts.yaml market_type plumbs through to execute_pkg
# ---------------------------------------------------------------------------


_SPOT_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_1:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_1
        market_type: spot
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


@pytest.mark.skipif(
    not _HAS_COORDINATOR,
    reason="Coordinator (and yaml) unavailable in this sandbox",
)
class TestEndToEndYamlPlumbThrough:
    def test_yaml_market_type_reaches_execute_pkg_via_coordinator(self, tmp_path):
        from src.core.coordinator import Coordinator

        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text(_SPOT_ACCOUNTS_YAML)
        units_yaml = tmp_path / "units.yaml"
        units_yaml.write_text("units: {}\n")
        coord = Coordinator(units_path=str(units_yaml))

        captured_cfgs = []

        def _stub_execute_pkg(pkg, account_cfg, **kwargs):
            captured_cfgs.append(dict(account_cfg))
            return f"dry-stub-{account_cfg.get('account_id')}"

        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=_stub_execute_pkg,
        ):
            coord.multi_account_execute(
                _pkg(),
                accounts_path=str(accounts_path),
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert len(captured_cfgs) == 1
        assert captured_cfgs[0]["market_type"] == "spot", (
            "market_type from accounts.yaml must reach execute_pkg's "
            "account_cfg dict — without it, _bybit_category falls back "
            "to its default and the per-account override is silently "
            "ignored."
        )
