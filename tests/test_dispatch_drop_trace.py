"""PI-20260926-KNSTSR8N-0003 instrumentation: every silent dispatch drop
must now leave a trace.

``Coordinator.multi_account_execute`` has always dropped an account from a
dispatch round in two places WITHOUT writing a ``trades`` row — the pre-loop
eligibility filter (``_dispatch_exclusion_reason``) and the ``account_type``
scope filter inside the loop. That is correct BY DESIGN for the routine case
(see ``tests/test_s029_pr1_account_strategy_filter.py`` — the 2026-05-08
operator directive that removed the per-drop journal row to kill
O(strategies x accounts x ticks) noise). The defect closed here is not that
row's absence; it is that "no journal row" had come to mean "no trace of any
kind" — a genuinely mis-wired leg (``configured`` flipped False, an emptied
``strategies:`` list, an ``account_type`` scope mismatch) read identically to
the routine case from outside the function.

This suite pins:
  1. Each silent-drop reason now emits a structured ``dispatch_drop:`` log
     line naming account/strategy/symbol/reason/stage.
  2. The two high-volume, working-as-designed reasons
     (``strategy_not_assigned``, ``outside_account_scope``) log at DEBUG —
     they still leave a trace, but do not reintroduce the retired noise at
     the INFO level ops actually watches.
  3. The rarer, config/routing-shaped reasons (``not_configured``,
     ``strategies_declared_empty``, ``account_type_mismatch``) log at
     WARNING — they name a leg that DOES belong on the account and is still
     not reaching it.
  4. Negative control: a normal pass (account eligible, dispatch reaches
     execute_pkg) emits NO ``dispatch_drop:`` line for that account at all.

Purely observational — no dispatch decision, no journal row, and no order-
path behaviour changes as part of this instrumentation.
"""
from __future__ import annotations

import logging
import sqlite3
import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage

_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      configured_ok:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_OK
        strategies: [turtle_soup]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
      not_configured:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_MISSING
        strategies: [turtle_soup]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
      strategies_empty:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_OK
        strategies: []
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
      other_strategy_only:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_OK
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


def _pkg(strategy: str = "turtle_soup") -> OrderPackage:
    return OrderPackage(
        strategy=strategy,
        symbol="BTCUSDT",
        direction="long",
        entry=50_000.0,
        sl=49_500.0,
        tp=51_000.0,
        confidence=1.0,
        meta={"strategy_name": strategy, "is_test": True, "test_qty": 0.001},
    )


@pytest.fixture(autouse=True)
def _stub_creds(monkeypatch):
    # BYBIT_KEY_MISSING is deliberately left UNSET — that is what makes
    # ``not_configured`` account ``configured=False``.
    monkeypatch.setenv("BYBIT_KEY_OK", "test-value")
    monkeypatch.delenv("BYBIT_KEY_MISSING", raising=False)


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
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=lambda pkg, account_cfg, **kw: f"dry-{account_cfg['account_id']}",
    ) as m:
        yield m


def _trade_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT account_id, strategy_name, status FROM trades"
            ).fetchall()
        ]
    finally:
        conn.close()


def _drop_lines(caplog, account: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("dispatch_drop:")
        and f"account={account} " in r.getMessage()
    ]


class TestAnomalousReasonsLogAtWarning:
    """``not_configured``, ``strategies_declared_empty`` and
    ``account_type_mismatch`` name a leg that DOES belong on the account
    (or would, absent the scope narrowing) and is still not reaching it —
    these must be visible without raising the log level."""

    def test_not_configured_account_emits_warning_drop_trace(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg, caplog,
    ):
        with caplog.at_level(logging.INFO, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        lines = _drop_lines(caplog, "not_configured")
        assert len(lines) == 1
        assert "reason=not_configured" in lines[0]
        assert "strategy=turtle_soup" in lines[0]
        assert "symbol=BTCUSDT" in lines[0]
        assert "stage=pre_loop_eligibility" in lines[0]

        # And it still writes no trades row — the journal contract this
        # instrumentation must not change.
        rows = _trade_rows(tmp_journal)
        assert not any(r["account_id"] == "not_configured" for r in rows)

    def test_strategies_declared_empty_emits_warning_drop_trace(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg, caplog,
    ):
        with caplog.at_level(logging.INFO, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        lines = _drop_lines(caplog, "strategies_empty")
        assert len(lines) == 1
        assert "reason=strategies_declared_empty" in lines[0]
        assert "stage=pre_loop_eligibility" in lines[0]

    def test_account_type_mismatch_emits_warning_drop_trace(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg, caplog,
    ):
        with caplog.at_level(logging.INFO, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
                account_type="prop",
            )

        # configured_ok declares turtle_soup and passes the pre-loop filter,
        # then is dropped by the in-loop account_type scope (it is "regular",
        # the round wants "prop").
        lines = _drop_lines(caplog, "configured_ok")
        assert len(lines) == 1
        assert "reason=account_type_mismatch" in lines[0]
        assert "stage=account_type_scope" in lines[0]

    def test_anomalous_reasons_log_at_warning_level(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg, caplog,
    ):
        # At WARNING (no DEBUG/INFO), the anomalous reasons still show up —
        # they must not depend on a verbose log level to be findable.
        with caplog.at_level(logging.WARNING, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert len(_drop_lines(caplog, "not_configured")) == 1
        assert len(_drop_lines(caplog, "strategies_empty")) == 1


class TestRoutineReasonsLogAtDebugOnly:
    """``strategy_not_assigned`` fires for most accounts on most strategies,
    every tick — it must still leave a trace (at DEBUG), but must NOT
    reappear at WARNING/INFO, which is exactly the noise the 2026-05-08
    directive retired."""

    def test_strategy_not_assigned_is_silent_above_debug(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg, caplog,
    ):
        with caplog.at_level(logging.INFO, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        # other_strategy_only declares only "vwap" — routine exclusion, must
        # not surface at INFO or above.
        assert _drop_lines(caplog, "other_strategy_only") == []

    def test_strategy_not_assigned_traces_at_debug(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg, caplog,
    ):
        with caplog.at_level(logging.DEBUG, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        lines = _drop_lines(caplog, "other_strategy_only")
        assert len(lines) == 1
        assert "reason=strategy_not_assigned" in lines[0]
        assert "stage=pre_loop_eligibility" in lines[0]
        rec = next(
            r for r in caplog.records
            if r.getMessage() == lines[0]
        )
        assert rec.levelno == logging.DEBUG


class TestNegativeControlNoDropTraceOnNormalPass:
    """The account that IS eligible and DOES reach ``execute_pkg`` must emit
    no ``dispatch_drop:`` line at all — the instrumentation only fires on an
    actual drop, never on the happy path."""

    def test_eligible_account_emits_no_drop_trace(
        self, coord, accounts_yaml, tmp_journal, stub_execute_pkg, caplog,
    ):
        with caplog.at_level(logging.DEBUG, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert _drop_lines(caplog, "configured_ok") == []
        # And the happy-path account really did reach execute_pkg —
        # otherwise this "no trace" result would be vacuous.
        called_accounts = [
            call.args[1]["account_id"] for call in stub_execute_pkg.call_args_list
        ]
        assert "configured_ok" in called_accounts

    def test_no_drop_trace_at_all_when_every_account_is_eligible(
        self, coord, tmp_path, tmp_journal, stub_execute_pkg, caplog, monkeypatch,
    ):
        single = tmp_path / "accounts_single.yaml"
        single.write_text(textwrap.dedent("""\
            accounts:
              only_account:
                type: regular
                exchange: bybit
                api_key_env: BYBIT_KEY_OK
                strategies: [turtle_soup]
                risk:
                  max_dd_pct: 0.05
                  daily_usd: 100
                  pos_size: 500
                  risk_pct: 0.01
                  min_balance_usd: 50
        """))
        with caplog.at_level(logging.DEBUG, logger="src.core.coordinator"):
            coord.multi_account_execute(
                _pkg("turtle_soup"),
                accounts_path=str(single),
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("dispatch_drop:")
        ] == []
