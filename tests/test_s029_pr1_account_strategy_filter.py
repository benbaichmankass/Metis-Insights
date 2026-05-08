"""Regression: ``Coordinator.multi_account_execute`` must dispatch only
to accounts whose ``strategies`` list contains the package's strategy.

History:
  * Pre-S-029 (architecture-audit-2026-05-02 § P0-1):
    ``account.strategies`` was loaded from ``accounts.yaml`` but never
    consulted in dispatch. Every signal fanned out to every account.
  * S-029 PR1: filter inside the per-account loop and write a
    ``skipped_not_assigned`` rejection row to ``trades`` for every
    skipped (account, tick) pair.
  * 2026-05-08 operator directive: filter the *list* upfront, do not
    write a rejection row. With multi-strategy + multi-account fan-out
    at 1-min ticks, the per-tick ``skipped_not_assigned`` rows became
    O(strategies × accounts × ticks) noise that buried real refusals.
    The accounts.yaml ``strategies:`` map is the audit trail.

Post-2026-05-08 contract:
  * Skipped accounts do **not** appear in ``results``.
  * Skipped accounts do **not** produce a ``trades`` row.
  * Accounts without a ``strategies`` list keep the unfiltered
    behaviour (legacy fixtures + tests that don't declare the map).
"""
from __future__ import annotations

import sqlite3
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


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return str(db)


@pytest.fixture()
def coord(tmp_journal):
    return Coordinator()


@pytest.fixture()
def stub_execute_pkg():
    """The dispatch path is irrelevant — we're testing the filter, not the
    exchange call. Returns a stable trade_id so the result rows look real."""
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=lambda pkg, account_cfg, **kw: f"dry-{account_cfg['account_id']}",
    ) as m:
        yield m


def _pkg(strategy: str) -> OrderPackage:
    return OrderPackage(
        symbol="BTCUSDT",
        direction="long",
        entry=50_000.0,
        sl=49_500.0,
        tp=51_000.0,
        confidence=1.0,
        strategy=strategy,
        meta={"strategy_name": strategy, "is_test": True, "test_qty": 0.001},
    )


def _trade_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT account_id, strategy_name, status, entry_reason "
            "FROM trades"
        ).fetchall()]
    finally:
        conn.close()


class TestStrategyFilterRouting:
    """A package only routes to accounts whose ``strategies`` list
    contains it. Skipped accounts are *invisible* to the dispatch:
    no result row, no trades row."""

    def test_vwap_signal_routes_only_to_vwap_account(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg,
    ):
        results = coord.multi_account_execute(
            _pkg("vwap"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        assert len(results) == 1, (
            f"vwap package must reach exactly the vwap account; got {results!r}"
        )
        assert results[0]["name"] == "bybit_2"
        assert results[0]["error"] is None

        # bybit_1 must not produce a trade row of any kind for this dispatch.
        rows = _trade_rows(tmp_journal)
        bybit_1_rows = [r for r in rows if r["account_id"] == "bybit_1"]
        assert bybit_1_rows == [], (
            f"filtered account must not write to trades; got {bybit_1_rows!r}"
        )

    def test_turtle_soup_signal_routes_only_to_turtle_soup_account(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg,
    ):
        results = coord.multi_account_execute(
            _pkg("turtle_soup"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        assert len(results) == 1
        assert results[0]["name"] == "bybit_1"
        assert results[0]["error"] is None

        rows = _trade_rows(tmp_journal)
        bybit_2_rows = [r for r in rows if r["account_id"] == "bybit_2"]
        assert bybit_2_rows == []

    def test_unknown_strategy_dispatches_to_no_account(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg,
    ):
        """A package whose strategy doesn't appear on any account's
        ``strategies`` list is filtered everywhere — empty results,
        no exchange calls, no trades rows."""
        results = coord.multi_account_execute(
            _pkg("phantom_strategy"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        assert results == []
        assert stub_execute_pkg.call_count == 0
        assert _trade_rows(tmp_journal) == []


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

        names = sorted(r["name"] for r in results)
        assert names == ["bybit_legacy_a", "bybit_legacy_b"]
