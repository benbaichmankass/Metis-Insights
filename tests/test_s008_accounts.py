"""S-008 PR #122: accounts unit tests.

Fully offline — mocked exchange clients, no DB, no live connections.
Covers risk sizing, execute_pkg(), and Coordinator.account_execute()
end-to-end wiring.
"""
from __future__ import annotations

import textwrap
from unittest.mock import MagicMock

import pytest

from src.core.coordinator import Coordinator, OrderPackage, _PAUSED_ACCOUNTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: vwap
          service: ict-trader-vwap
          model: null
          signal_prefixes: [vwap]
      accounts:
        - id: live
          exchange: bybit
          risk_pct: 0.01
          env_path: .env
          strategies: [vwap]
          balance_usdt: 10000.0
      dashboards:
        db:
          trade_journal: trade_journal.db
          signals: data/trades.db
      return_commands:
        supported: []
      telegram_bot:
        data_source: dashboards
      app:
        config_enabled: true
      trading_school:
        auto_backtest: true
      db:
        trade_journal: trade_journal.db
        signals: data/trades.db
      workflows:
        docs: docs/claude/
""")


@pytest.fixture()
def units_yaml(tmp_path):
    p = tmp_path / "units.yaml"
    p.write_text(UNITS_YAML)
    return str(p)


@pytest.fixture()
def coord(units_yaml, tmp_path):
    _PAUSED_ACCOUNTS.clear()
    # S-012 PR B3: Coordinator now prefers config/accounts.yaml. For these
    # synthetic-fixture tests, pass a non-existent accounts_path so the
    # Coordinator falls back to units.yaml::accounts (the fixture's path).
    c = Coordinator(
        units_path=units_yaml,
        accounts_path=str(tmp_path / "no-accounts.yaml"),
    )
    yield c
    _PAUSED_ACCOUNTS.clear()


def _pkg(direction="long", entry=50_000.0, sl=49_000.0, tp=52_000.0) -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        confidence=0.8,
    )


def _mock_bybit_client(order_id: str = "ord-123") -> MagicMock:
    client = MagicMock()
    client.get_wallet_balance.return_value = {
        "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
    }
    client.place_order.return_value = {"result": {"orderId": order_id}}
    return client


# ---------------------------------------------------------------------------
# Risk sizing
# ---------------------------------------------------------------------------


class TestRiskSizing:
    def test_basic_sizing(self):
        from src.units.accounts.risk import size_order
        pkg = _pkg(entry=50_000.0, sl=49_000.0)
        # risk_usdt = 10_000 * 0.01 = 100; risk_distance = 1_000
        # qty = 100 / 1_000 = 0.1
        qty = size_order(pkg, risk_pct=0.01, balance_usdt=10_000.0)
        assert qty == pytest.approx(0.1, rel=1e-3)

    def test_short_sizing(self):
        from src.units.accounts.risk import size_order
        pkg = _pkg(direction="short", entry=50_000.0, sl=51_000.0)
        qty = size_order(pkg, risk_pct=0.01, balance_usdt=10_000.0)
        assert qty == pytest.approx(0.1, rel=1e-3)

    def test_qty_clipped_to_min(self):
        from src.units.accounts.risk import size_order
        # Very large risk distance → tiny qty → clipped to min
        pkg = _pkg(entry=50_000.0, sl=1.0)  # 49_999 distance
        qty = size_order(pkg, risk_pct=0.01, balance_usdt=100.0, min_qty=0.001)
        assert qty >= 0.001

    def test_qty_clipped_to_max(self):
        from src.units.accounts.risk import size_order
        # Tiny risk distance → huge qty → clipped to max
        pkg = _pkg(entry=50_000.0, sl=49_999.9)  # 0.1 distance
        qty = size_order(pkg, risk_pct=0.01, balance_usdt=10_000.0, max_qty=5.0)
        assert qty <= 5.0

    def test_zero_balance_raises(self):
        from src.units.accounts.risk import size_order
        with pytest.raises(ValueError, match="balance_usdt"):
            size_order(_pkg(), risk_pct=0.01, balance_usdt=0.0)

    def test_zero_risk_pct_raises(self):
        from src.units.accounts.risk import size_order
        with pytest.raises(ValueError, match="risk_pct"):
            size_order(_pkg(), risk_pct=0.0, balance_usdt=10_000.0)

    def test_entry_equals_sl_raises(self):
        from src.units.accounts.risk import size_order
        pkg = _pkg(entry=50_000.0, sl=50_000.0)
        with pytest.raises(ValueError, match="division by zero"):
            size_order(pkg, risk_pct=0.01, balance_usdt=10_000.0)

    def test_qty_precision(self):
        from src.units.accounts.risk import size_order
        pkg = _pkg(entry=50_000.0, sl=49_000.0)
        qty = size_order(pkg, risk_pct=0.01, balance_usdt=10_000.0, qty_precision=3)
        # Check it's rounded to 3 decimal places
        assert qty == round(qty, 3)

    def test_size_order_from_cfg(self):
        from src.units.accounts.risk import size_order_from_cfg
        # S-026 G3: bump daily_usd so the new daily-loss budget gate
        # doesn't clip the qty for this base sizing assertion.
        cfg = {"risk_pct": 0.02, "min_qty": 0.001, "max_qty": 10.0,
               "daily_usd": 1_000_000_000}
        pkg = _pkg(entry=50_000.0, sl=49_000.0)
        # risk_usdt = 10_000 * 0.02 = 200; distance = 1_000; qty = 0.2
        qty = size_order_from_cfg(pkg, cfg, balance_usdt=10_000.0)
        assert qty == pytest.approx(0.2, rel=1e-3)


# ---------------------------------------------------------------------------
# execute_pkg
# ---------------------------------------------------------------------------


class TestExecutePkg:
    def test_dry_run_returns_trade_id_string(self):
        from src.units.accounts.execute import execute_pkg
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        trade_id = execute_pkg(_pkg(), cfg, exchange_client=None, balance_usdt=10_000.0)
        assert isinstance(trade_id, str)
        assert trade_id.startswith("dry-")

    def test_dry_run_explicit_true(self):
        from src.units.accounts.execute import execute_pkg
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        client = _mock_bybit_client()
        trade_id = execute_pkg(_pkg(), cfg, exchange_client=client,
                               balance_usdt=10_000.0, dry_run=True)
        assert trade_id.startswith("dry-")
        client.place_order.assert_not_called()

    def test_live_mode_calls_exchange(self):
        from src.units.accounts.execute import execute_pkg
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        client = _mock_bybit_client("ord-abc")
        trade_id = execute_pkg(_pkg(), cfg, exchange_client=client,
                               balance_usdt=10_000.0, dry_run=False)
        assert trade_id == "ord-abc"
        client.place_order.assert_called_once()

    def test_paused_account_raises_runtime_error(self):
        from src.units.accounts.execute import execute_pkg
        _PAUSED_ACCOUNTS.add("live")
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        try:
            with pytest.raises(RuntimeError, match="paused"):
                execute_pkg(_pkg(), cfg, balance_usdt=10_000.0)
        finally:
            _PAUSED_ACCOUNTS.discard("live")

    def test_order_calls_correct_symbol(self):
        from src.units.accounts.execute import execute_pkg
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        client = _mock_bybit_client()
        execute_pkg(_pkg(), cfg, exchange_client=client,
                    balance_usdt=10_000.0, dry_run=False)
        call_kwargs = client.place_order.call_args
        # symbol should be "BTCUSDT"
        args_or_kwargs = call_kwargs[1] if call_kwargs[1] else {}
        assert args_or_kwargs.get("symbol") == "BTCUSDT"

    def test_long_maps_to_buy_side(self):
        from src.units.accounts.execute import execute_pkg
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        client = _mock_bybit_client()
        execute_pkg(_pkg(direction="long"), cfg, exchange_client=client,
                    balance_usdt=10_000.0, dry_run=False)
        kwargs = client.place_order.call_args[1]
        assert kwargs.get("side") == "Buy"

    def test_short_maps_to_sell_side(self):
        from src.units.accounts.execute import execute_pkg
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        client = _mock_bybit_client()
        execute_pkg(_pkg(direction="short", sl=51_000.0, tp=48_000.0),
                    cfg, exchange_client=client, balance_usdt=10_000.0, dry_run=False)
        kwargs = client.place_order.call_args[1]
        assert kwargs.get("side") == "Sell"

    def test_balance_fetched_from_client_when_not_provided(self):
        from src.units.accounts.execute import execute_pkg
        cfg = {"account_id": "live", "exchange": "bybit", "risk_pct": 0.01}
        client = _mock_bybit_client()
        execute_pkg(_pkg(), cfg, exchange_client=client, dry_run=False)
        client.get_wallet_balance.assert_called_once()


# ---------------------------------------------------------------------------
# Coordinator.account_execute() end-to-end
# ---------------------------------------------------------------------------


class TestCoordinatorAccountExecute:
    def test_dry_run_returns_trade_id(self, coord):
        trade_id = coord.account_execute(
            "live", _pkg(), balance_usdt=10_000.0
        )
        assert isinstance(trade_id, str)
        assert trade_id.startswith("dry-")

    def test_unknown_account_raises_key_error(self, coord):
        with pytest.raises(KeyError, match="no_such_account"):
            coord.account_execute("no_such_account", _pkg())

    def test_halted_account_raises_runtime_error(self, coord):
        coord.return_command("halt")
        with pytest.raises(RuntimeError, match="paused"):
            coord.account_execute("live", _pkg(), balance_usdt=10_000.0)

    def test_after_resume_execute_succeeds(self, coord):
        coord.return_command("halt")
        coord.return_command("resume")
        trade_id = coord.account_execute("live", _pkg(), balance_usdt=10_000.0)
        assert trade_id.startswith("dry-")

    def test_explicit_dry_run_false_with_mock_client(self, coord):
        client = _mock_bybit_client("ord-xyz")
        trade_id = coord.account_execute(
            "live", _pkg(), exchange_client=client,
            balance_usdt=10_000.0, dry_run=False,
        )
        assert trade_id == "ord-xyz"

    def test_account_cfg_passed_correctly(self, coord):
        """risk_pct from units.yaml (0.01) drives sizing: qty ≈ 0.1."""
        client = _mock_bybit_client()
        coord.account_execute(
            "live", _pkg(entry=50_000.0, sl=49_000.0),
            exchange_client=client, balance_usdt=10_000.0, dry_run=False,
        )
        kwargs = client.place_order.call_args[1]
        qty = float(kwargs.get("qty") or 0)
        assert qty == pytest.approx(0.1, rel=0.01)
