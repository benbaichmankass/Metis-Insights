"""P0 fix: ``Coordinator.multi_account_execute`` must honour each
account's ``mode: live | dry_run`` field.

Pre-fix (this PR's bug shape): the parameter ``dry_run`` defaulted to
``True``. ``src/runtime/pipeline.py`` calls ``multi_account_execute(pkg)``
without specifying ``dry_run``, so every dispatch silently used dry
mode regardless of ``cfg["mode"]: live`` in ``config/accounts.yaml``.

Symptom: liveness watchdog reported "5 actionable signals fired in the
last 1h, but 0 trades landed" while ``bybit_2`` was ``mode: live`` with
$177 balance and zero open positions.

Architectural rule (CLAUDE.md "Autonomous live-trading rule"):
> per-account ``mode: live | dry_run`` in ``config/accounts.yaml`` is
> the SINGLE dry/live toggle in the codebase

Post-fix the parameter defaults to ``None`` and each iteration
resolves ``effective_dry`` from ``account.dry_run`` (already loaded
from YAML by ``load_accounts``). The caller can still force a mode
with an explicit ``True`` / ``False`` (smoke tests + integration
tests use this).

Four contracts under test:

1. **No caller override + account is live → live path.** Client is
   constructed (``bybit_client_for`` is called); ``execute_pkg`` is
   passed ``dry_run=False``.
2. **No caller override + account is dry → dry path.** Client is NOT
   constructed; ``execute_pkg`` is passed ``dry_run=True``.
3. **Caller override (``dry_run=True``) wins over a live account.**
   Smoke-test contract — explicit ``True`` short-circuits the
   per-account decision.
4. **Caller override (``dry_run=False``) wins over a dry account.**
   Same shape, opposite direction.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage


# Happy-path snapshot mimicking what ``_fetch_spot_coin_balances``
# returns once the SDK call has been parsed. The tests in this file
# patch ``bybit_client_for`` to a stub object that doesn't implement
# ``get_wallet_balance``; the autouse fixture below wires this dict
# in so the spot-margin sizing branch (added in S-053 / coordinator
# line 754) doesn't crater the test on AttributeError → balance=0 →
# zero_balance refusal.
_HAPPY_SPOT_BALANCES = {
    "base_coin": "BTC",
    "base_qty": 0.0,
    "base_usd_value": 0.0,
    "quote_usdt": 10_000.0,
    "base_borrow_usd": 0.0,
    "quote_borrow_usd": 0.0,
    "total_account_usd": 10_000.0,
}


@pytest.fixture(autouse=True)
def _stub_account_creds_and_balances(monkeypatch):
    """Plumbing exposed by PR #507's ``configured=False`` filter:

    The accounts.yaml fixtures here use ``BYBIT_KEY_LIVE`` and
    ``BYBIT_KEY_PAPER`` as ``api_key_env``. Without env vars set,
    ``resolve_credentials`` returns falsy and the loader marks the
    account ``configured=False``, which the coordinator now drops
    before dispatch — empty results, every assertion fails.

    ``_derive_secret_env`` falls back to api_key_env when there's
    no ``_API_KEY`` substring to replace, so the same value
    satisfies both api_key + api_secret lookups.

    Note: ``_fetch_spot_coin_balances`` was removed from execute.py in
    PR #1655 (Bybit spot semantics fix); the spot-sizing path now runs
    through the coordinator's inline balance logic. Tests that supply
    ``balance_fetcher`` directly are unaffected.
    """
    for name in ("BYBIT_KEY_LIVE", "BYBIT_KEY_PAPER"):
        monkeypatch.setenv(name, "test-value")


@pytest.fixture(autouse=True)
def _force_execution_live(monkeypatch):
    """Isolate these tests to the per-ACCOUNT `mode` gate.

    The fixtures here route ``vwap``, which is ``execution: shadow`` in
    the live config/strategies.yaml (S9) — so without this the coordinator
    would (correctly) force every vwap dispatch dry via the per-STRATEGY
    gate, masking the per-account behaviour under test. Pin every
    strategy to ``execution: live`` so this file exercises only the
    account gate; the strategy gate has its own coverage in
    tests/test_strategy_execution_gate.py.
    """
    monkeypatch.setattr(
        "src.strategy_registry.execution_mode", lambda *a, **k: "live"
    )


_LIVE_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_live:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_LIVE
        mode: live
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


_DRY_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_paper:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_PAPER
        mode: dry_run
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


def _pkg() -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=80_000.0,
        sl=79_500.0,
        tp=80_500.0,
        confidence=0.42,
        meta={"strategy_name": "vwap"},
    )


@pytest.fixture()
def coord(tmp_path):
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    return Coordinator(units_path=str(units_yaml))


@pytest.fixture()
def live_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_LIVE_ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def dry_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_DRY_ACCOUNTS_YAML)
    return str(p)


def _capture_execute_pkg_calls():
    """Yield a list that collects every ``execute_pkg`` invocation
    so tests can assert on the ``dry_run`` value the dispatcher
    actually passed.
    """
    captured = []

    def _stub(pkg, account_cfg, **kw):
        captured.append({"account_id": account_cfg["account_id"], **kw})
        return f"trade-{account_cfg['account_id']}"

    return captured, _stub


# ---------------------------------------------------------------------------
# Contract 1: live account + no caller override → live dispatch
# ---------------------------------------------------------------------------


def test_no_override_live_account_routes_live(
    coord, live_yaml, monkeypatch,
):
    monkeypatch.setenv("BYBIT_KEY_LIVE", "k")
    monkeypatch.setenv("BYBIT_KEY_LIVE_API_SECRET", "s")
    captured, stub = _capture_execute_pkg_calls()

    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),  # truthy stub
    ) as client_factory:
        results = coord.multi_account_execute(
            _pkg(),
            accounts_path=live_yaml,
            balance_fetcher=lambda _a: 10_000.0,
        )

    assert len(results) == 1
    assert results[0]["error"] is None
    # Client construction must have run for a live account.
    client_factory.assert_called_once()
    # execute_pkg must have been told this is the live path.
    assert len(captured) == 1
    assert captured[0]["dry_run"] is False


# ---------------------------------------------------------------------------
# Contract 2: dry account + no caller override → RiskManager rejects
#
# A ``mode: dry_run`` account loads with ``RiskManager.dry_run=True``.
# Per the autonomous-live-trading rule, ``RiskManager.evaluate(pkg)``
# then returns ``(False, "account_mode_dry_run")`` and the dispatch
# refuses the order at the risk gate — execute_pkg is never called.
# A rejection row lands in the trade journal (PR #382 contract).
# ---------------------------------------------------------------------------


def test_no_override_dry_account_rejected_at_risk_gate(coord, dry_yaml):
    captured, stub = _capture_execute_pkg_calls()

    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        side_effect=AssertionError("client must not be built for dry-run account"),
    ):
        results = coord.multi_account_execute(
            _pkg(),
            accounts_path=dry_yaml,
            balance_fetcher=lambda _a: 10_000.0,
        )

    assert len(results) == 1
    # The risk gate refuses → result row carries the structured reason.
    assert results[0]["error"] is not None
    assert "account_mode_dry_run" in results[0]["error"]
    # execute_pkg must not have been called — RiskBreach short-circuited.
    assert captured == []


# ---------------------------------------------------------------------------
# Contract 3: caller override True wins over live account
# ---------------------------------------------------------------------------


def test_caller_override_dry_true_forces_dry_on_live_account(
    coord, live_yaml, monkeypatch,
):
    """Smoke-test contract: an explicit ``dry_run=True`` short-circuits
    the per-account decision. Used by ``scripts/smoke_test_trade.py``
    and integration tests."""
    monkeypatch.setenv("BYBIT_KEY_LIVE", "k")
    monkeypatch.setenv("BYBIT_KEY_LIVE_API_SECRET", "s")
    captured, stub = _capture_execute_pkg_calls()

    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        side_effect=AssertionError("client must not be built when caller forces dry"),
    ):
        coord.multi_account_execute(
            _pkg(),
            accounts_path=live_yaml,
            dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

    assert len(captured) == 1
    assert captured[0]["dry_run"] is True


# ---------------------------------------------------------------------------
# Contract 4: caller override False on a dry account — dispatch routing
# would go live, but RiskManager.dry_run is still True (set by load_accounts
# from the YAML mode field), so evaluate() still refuses. The caller
# override only controls dispatch-side routing, not the RiskManager.
# This test pins that contract so a future refactor doesn't accidentally
# bypass the RiskManager via the caller override.
# ---------------------------------------------------------------------------


def test_caller_override_does_not_bypass_risk_manager_on_dry_account(coord, dry_yaml):
    captured, stub = _capture_execute_pkg_calls()

    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        results = coord.multi_account_execute(
            _pkg(),
            accounts_path=dry_yaml,
            dry_run=False,
            balance_fetcher=lambda _a: 10_000.0,
        )

    # RiskManager refuses regardless of caller override — the
    # account's mode field set RiskManager.dry_run=True at load time,
    # and that's authoritative. The result row carries the structured
    # rejection reason; execute_pkg is never reached.
    assert results[0]["error"] is not None
    assert "account_mode_dry_run" in results[0]["error"]
    assert captured == []


# ---------------------------------------------------------------------------
# Contract 5: a dry-shelved account with ZERO balance refuses at the SIZER
# (before the risk gate's account_mode_dry_run rejection can fire) — the
# refusal must be tagged ``dry_run_sizing_skip`` so the dispatch-failure
# alert classifies it as a policy hold, not a failure. Pinning case:
# alpaca_live was shelved to dry_run AND defunded on 2026-07-15 (funds moved
# to Bybit); every routed signal then alarmed "1/3 accounts failed to
# dispatch: zero_balance" (operator report 2026-07-20).
# ---------------------------------------------------------------------------


def test_dry_account_zero_balance_tagged_as_expected_skip(coord, dry_yaml):
    from src.runtime.execution_diagnostics import is_expected_dispatch_skip

    captured, stub = _capture_execute_pkg_calls()

    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        side_effect=AssertionError("client must not be built for dry-run account"),
    ):
        results = coord.multi_account_execute(
            _pkg(),
            accounts_path=dry_yaml,
            balance_fetcher=lambda _a: 0.0,
        )

    assert len(results) == 1
    err = results[0]["error"]
    assert err is not None
    # The underlying cause stays visible for the journal/audit...
    assert "zero_balance" in err
    # ...but the effective-dry tag makes the alert classify it as a hold.
    assert err.startswith("dry_run_sizing_skip:")
    assert is_expected_dispatch_skip(err) is True
    assert captured == []


def test_live_account_zero_balance_still_a_genuine_failure(
    coord, live_yaml, monkeypatch,
):
    """The tag is scoped to effective-dry accounts ONLY — a LIVE account
    with a genuinely empty wallet is a real operator-actionable failure and
    must keep alarming."""
    from src.runtime.execution_diagnostics import is_expected_dispatch_skip

    captured, stub = _capture_execute_pkg_calls()

    with patch(
        "src.units.accounts.execute.execute_pkg", side_effect=stub,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        results = coord.multi_account_execute(
            _pkg(),
            accounts_path=live_yaml,
            balance_fetcher=lambda _a: 0.0,
        )

    assert len(results) == 1
    err = results[0]["error"]
    assert err is not None
    assert "zero_balance" in err
    assert not err.startswith("dry_run_sizing_skip:")
    assert is_expected_dispatch_skip(err) is False
    assert captured == []
