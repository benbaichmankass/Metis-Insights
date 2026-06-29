"""Prop manual-bridge sizing bypass (operator directive 2026-06-29; PB-20260625-001).

A prop (breakout) account has NO broker API, so there is no live ``balance()`` to
size against — and it must not need one. Before the fix, ``multi_account_execute``
ran the balance-based RiskManager for the prop account too, so when ``balance()``
returned None it raised ``sizing_failed`` and the prop ticket NEVER emitted
(trend_donchian_sol/breakout_1). The fix bypasses the balance fetch + sizing for a
breakout account: a sentinel qty carries the decision to the breakout branch where
``emit_prop_ticket`` sizes the leg from the account RULESET and the assistant places
it.

These tests assert the prop account:
* does NOT call the balance fetcher (so a failing/None balance can't block it), and
* reaches ``emit_prop_ticket`` (the ticket emits) with no ``sizing_failed`` refusal.
"""
from __future__ import annotations

import sqlite3
import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage


_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      breakout_1:
        type: prop
        exchange: breakout
        account_class: prop
        api_key_env: BREAKOUT_KEY_1
        strategies: [trend_donchian_sol]
        risk:
          max_dd_pct: 0.05
          daily_usd: 150
          pos_size: 500
          risk_pct: 0.015
          min_balance_usd: 0
""")


def _pkg(strategy: str = "trend_donchian_sol") -> OrderPackage:
    return OrderPackage(
        strategy=strategy,
        symbol="SOLUSDT",
        direction="long",
        entry=150.0,
        sl=145.5,
        tp=175.5,
        confidence=0.68,
        meta={"strategy_name": strategy, "entry_reason": f"{strategy} signal"},
    )


@pytest.fixture(autouse=True)
def _stub_creds(monkeypatch):
    monkeypatch.setenv("BREAKOUT_KEY_1", "test-value")
    monkeypatch.setenv("BREAKOUT_SECRET_1", "test-value")


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
            "SELECT account_id, status, entry_reason FROM trades "
            "WHERE status='rejected'"
        ).fetchall()]
    finally:
        conn.close()


def _raising_fetcher(_acc):
    raise RuntimeError(
        "balance() returned None for breakout_1 (exchange=breakout): "
        "API error or credentials missing — account unreachable"
    )


def test_prop_account_emits_despite_failing_balance(
    coord, accounts_yaml, tmp_journal,
):
    """A raising balance fetcher must NOT block the prop ticket — the prop
    account bypasses balance-based sizing entirely."""
    with patch(
        "src.prop.breakout_executor.emit_prop_ticket",
        return_value="prop-manual-deadbeef",
    ) as emit:
        coord.multi_account_execute(
            _pkg("trend_donchian_sol"),
            accounts_path=accounts_yaml,
            dry_run=False,
            balance_fetcher=_raising_fetcher,
        )

    # The ticket emitted (reached the breakout branch) ...
    assert emit.call_count == 1, "prop ticket should have emitted"
    # ... and no sizing_failed refusal was journaled for the prop account.
    refusals = _refusal_rows(tmp_journal)
    sizing_failed = [
        r for r in refusals
        if "sizing_failed" in (r["entry_reason"] or "")
        or "account unreachable" in (r["entry_reason"] or "")
    ]
    assert sizing_failed == [], f"unexpected sizing_failed refusal: {sizing_failed}"


def test_prop_account_does_not_call_balance_fetcher(
    coord, accounts_yaml, tmp_journal,
):
    """The fetcher must never be invoked for a breakout account."""
    calls = {"n": 0}

    def _counting_fetcher(_acc):
        calls["n"] += 1
        return 10_000.0

    with patch(
        "src.prop.breakout_executor.emit_prop_ticket",
        return_value="prop-manual-cafef00d",
    ):
        coord.multi_account_execute(
            _pkg("trend_donchian_sol"),
            accounts_path=accounts_yaml,
            dry_run=False,
            balance_fetcher=_counting_fetcher,
        )

    assert calls["n"] == 0, "prop account must bypass the balance fetcher"
