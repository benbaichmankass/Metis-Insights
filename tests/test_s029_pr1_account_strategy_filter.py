"""Regression: ``Coordinator.multi_account_execute`` must filter packages
to accounts whose ``strategies`` list contains the package's strategy.

Pre-fix (architecture-audit-2026-05-02 § P0-1):
``account.strategies`` was loaded from ``accounts.yaml`` but never
consulted in dispatch. Every signal fanned out to every account, so a
vwap package landed in a turtle_soup-only wallet and vice versa.

Post-fix: skipped accounts produce a ``skipped_not_assigned`` result.
Accounts without an assigned-strategies list (legacy test fixtures)
remain unfiltered to preserve back-compat.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage


_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_1:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_1
        strategies: [turtle_soup]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_2
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


def _pkg(strategy: str) -> OrderPackage:
    return OrderPackage(
        strategy=strategy,
        symbol="BTCUSDT",
        direction="long" if strategy == "vwap" else "short",
        entry=50_000.0,
        sl=49_500.0,
        tp=51_000.0,
        meta={"strategy_name": strategy},
    )


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def coord(tmp_path):
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    return Coordinator(units_path=str(units_yaml))


@pytest.fixture()
def stub_execute_pkg():
    """Stub the canonical live entry point so tests don't need exchange creds."""
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=lambda pkg, account_cfg, **kw: f"dry-{account_cfg['account_id']}",
    ) as m:
        yield m


class TestStrategyFilterRouting:
    """Each strategy must only route to its assigned accounts."""

    def test_vwap_signal_routes_only_to_vwap_account(
        self, coord, accounts_yaml, stub_execute_pkg,
    ):
        results = coord.multi_account_execute(
            _pkg("vwap"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        # Both accounts produce a result row (so the operator can see
        # the skip), but only bybit_2 actually executes.
        ok = [r for r in results if r["error"] is None]
        skipped = [r for r in results if r["error"] and "skipped_not_assigned" in r["error"]]

        assert len(ok) == 1
        assert ok[0]["name"] == "bybit_2"
        assert len(skipped) == 1
        assert skipped[0]["name"] == "bybit_1"
        assert "vwap" in skipped[0]["error"]
        assert "turtle_soup" in skipped[0]["error"]

    def test_turtle_soup_signal_routes_only_to_turtle_soup_account(
        self, coord, accounts_yaml, stub_execute_pkg,
    ):
        results = coord.multi_account_execute(
            _pkg("turtle_soup"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        ok = [r for r in results if r["error"] is None]
        skipped = [r for r in results if r["error"] and "skipped_not_assigned" in r["error"]]

        assert len(ok) == 1
        assert ok[0]["name"] == "bybit_1"
        assert len(skipped) == 1
        assert skipped[0]["name"] == "bybit_2"

    def test_unknown_strategy_skipped_on_every_account(
        self, coord, accounts_yaml, stub_execute_pkg,
    ):
        """A package whose strategy doesn't appear on any account.strategies
        list is silently skipped on every account — no exchange calls."""
        results = coord.multi_account_execute(
            _pkg("phantom_strategy"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        skipped = [r for r in results if r["error"] and "skipped_not_assigned" in r["error"]]
        assert len(skipped) == 2
        assert stub_execute_pkg.call_count == 0


class TestBackCompatNoStrategiesList:
    """Legacy fixtures whose accounts.yaml has no ``strategies`` field
    must keep routing every package to every account — pre-existing
    test contracts depend on this and the per-account assignment is
    a production-config concern."""

    LEGACY_YAML = textwrap.dedent("""\
        accounts:
          bybit_legacy_a:
            type: regular
            exchange: bybit
            api_key_env: BYBIT_KEY_A
            risk:
              max_dd_pct: 0.05
              daily_usd: 100
              pos_size: 500
              risk_pct: 0.01
              min_balance_usd: 50
          bybit_legacy_b:
            type: regular
            exchange: bybit
            api_key_env: BYBIT_KEY_B
            risk:
              max_dd_pct: 0.05
              daily_usd: 100
              pos_size: 500
              risk_pct: 0.01
              min_balance_usd: 50
    """)

    def test_no_strategies_list_means_no_filter(
        self, coord, tmp_path, stub_execute_pkg,
    ):
        p = tmp_path / "accounts.yaml"
        p.write_text(self.LEGACY_YAML)

        results = coord.multi_account_execute(
            _pkg("vwap"),
            accounts_path=str(p),
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        assert all(r["error"] is None for r in results)
        assert {r["name"] for r in results} == {"bybit_legacy_a", "bybit_legacy_b"}
