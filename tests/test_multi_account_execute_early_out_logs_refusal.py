"""Regression: ``Coordinator.multi_account_execute`` must land a refusal
row in ``trade_journal.db::trades`` for every package that was logged but
not dispatched.

Pre-fix the dispatcher had three early-out branches that produced a
result-row with an ``error`` field but skipped the
``log_rejection_to_journal`` call:

* ``skipped_not_assigned`` — pkg.strategy not in account.strategies
* ``sizing_failed`` — risk_manager.position_size raised
* ``below_min_balance`` — sized_qty <= 0

When any of those fired, ``_log_new_order_package`` had already
inserted a row into ``order_packages``. With no matching ``trades`` row
the operator's ``/packages`` view rendered the package as "open with
no linked trade" and no rejection counterpart explained why — exactly
the symptom that surfaced for VWAP → bybit_2 packages whose dispatch
was suppressed by the per-account strategy filter / sizing path.

Post-fix every early-out branch writes ``status='rejected'`` with the
matching reason token so ``/packages`` can pair every open package
with a journal reason.
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


def _pkg(strategy: str = "vwap") -> OrderPackage:
    return OrderPackage(
        strategy=strategy,
        symbol="BTCUSDT",
        direction="long",
        entry=80_000.0,
        sl=79_500.0,
        tp=81_000.0,
        confidence=0.42,
        meta={"strategy_name": strategy, "entry_reason": f"{strategy} signal"},
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
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return db_path


def _refusal_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT account_id, strategy_name, status, entry_reason, notes "
            "FROM trades WHERE status = 'rejected'"
        ).fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def stub_execute_pkg():
    """Live dispatch path is irrelevant — the early-out branches under
    test never reach execute_pkg."""
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=lambda pkg, account_cfg, **kw: f"dry-{account_cfg['account_id']}",
    ) as m:
        yield m


class TestStrategyFilterLogsRefusal:
    """``skipped_not_assigned`` — pkg.strategy not in account.strategies.

    The unassigned account must produce both a result row (so the
    operator sees the skip) AND a ``trades`` row with
    ``status='rejected'`` so ``/packages`` can pair the open package
    with the refusal reason.
    """

    def test_vwap_to_turtle_soup_only_account_logs_rejection(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg,
    ):
        results = coord.multi_account_execute(
            _pkg("vwap"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        # Existing contract: skipped account produces a result row.
        skipped = [r for r in results
                   if r["error"] and "skipped_not_assigned" in r["error"]]
        assert len(skipped) == 1
        assert skipped[0]["name"] == "bybit_1"

        # New contract: every skipped account also lands a rejection row.
        rows = _refusal_rows(tmp_journal)
        assert len(rows) == 1
        row = rows[0]
        assert row["account_id"] == "bybit_1"
        assert row["strategy_name"] == "vwap"
        assert "skipped_not_assigned" in row["entry_reason"]


class TestSizingFailedLogsRefusal:
    """``sizing_failed`` — RiskManager.position_size raised.

    Pre-fix the package sat in ``order_packages`` with no
    ``trades`` counterpart and no diagnostic. Post-fix the
    exception's type+message is recorded on the journal row.
    """

    def test_position_size_raises_logs_rejection(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg,
    ):
        # Force position_size to raise on every account so both rows
        # land in the rejection journal.
        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            side_effect=ValueError("test_sizing_explosion"),
        ):
            coord.multi_account_execute(
                _pkg("vwap"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        rows = _refusal_rows(tmp_journal)
        # Both accounts hit sizing_failed (the strategy filter still
        # skips bybit_1 first → its row reads skipped_not_assigned;
        # bybit_2 reaches sizing_failed). So we expect at least one
        # sizing_failed row keyed to bybit_2.
        sizing = [r for r in rows
                  if "sizing_failed" in (r["entry_reason"] or "")]
        assert len(sizing) == 1
        assert sizing[0]["account_id"] == "bybit_2"
        assert "ValueError" in sizing[0]["entry_reason"]
        assert "test_sizing_explosion" in sizing[0]["entry_reason"]


class TestBelowMinBalanceLogsRefusal:
    """``below_min_balance`` — sized_qty <= 0 (under-balance account)."""

    def test_zero_balance_account_logs_rejection(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg,
    ):
        # balance below min_balance_usd → position_size returns 0.0 →
        # below_min_balance early-out fires for bybit_2 (the vwap-
        # assigned account; bybit_1 hits skipped_not_assigned first).
        coord.multi_account_execute(
            _pkg("vwap"),
            accounts_path=accounts_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 1.0,  # < min_balance_usd=50
        )

        rows = _refusal_rows(tmp_journal)
        below = [r for r in rows
                 if "below_min_balance" in (r["entry_reason"] or "")]
        assert len(below) == 1
        assert below[0]["account_id"] == "bybit_2"
        assert below[0]["strategy_name"] == "vwap"
