"""Regression: ``Coordinator.multi_account_execute`` must land a refusal
row in ``trade_journal.db::trades`` for every package that *enters*
dispatch but is refused by a downstream gate.

Pre-fix the dispatcher had early-out branches that produced a
result-row with an ``error`` field but skipped the
``log_rejection_to_journal`` call. Without a matching ``trades`` row
the operator's ``/packages`` view rendered the package as "open with
no linked trade" and no rejection counterpart explained why.

Two refusal kinds are exercised here:

* ``sizing_failed`` — risk_manager.position_size raised
* ``below_min_balance`` — sized_qty <= 0

Note: ``skipped_not_assigned`` *used* to be a third refusal path
covered here. As of 2026-05-08 the per-account strategy filter runs
*before* the dispatch loop and removes unmatched accounts from
``results`` entirely (no rejection row written). See
``test_s029_pr1_account_strategy_filter.py`` for that contract.
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


@pytest.fixture(autouse=True)
def _stub_account_creds(monkeypatch):
    """Plant env vars for the accounts.yaml ``api_key_env`` values so
    PR #507's ``configured=False`` filter doesn't drop these accounts
    before they reach the early-out branches under test."""
    for name in ("BYBIT_KEY_1", "BYBIT_KEY_2"):
        monkeypatch.setenv(name, "test-value")


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


class TestSizingFailedLogsRefusal:
    """``sizing_failed`` — RiskManager.position_size raised.

    Pre-fix the package sat in ``order_packages`` with no
    ``trades`` counterpart and no diagnostic. Post-fix the
    exception's type+message is recorded on the journal row.
    """

    def test_position_size_raises_logs_rejection(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg,
    ):
        # Force position_size to raise — only bybit_2 reaches the sizer
        # for a vwap package (per-account strategy pre-filter; see
        # test_s029_pr1_account_strategy_filter.py).
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
        # below_min_balance early-out fires for bybit_2 (the only
        # account left after the per-account strategy pre-filter).
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
