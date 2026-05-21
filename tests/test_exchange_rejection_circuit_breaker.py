"""Circuit breaker for exchange-side rejection storms.

The 2026-05-10 layer-2 health review surfaced a 34% rejection rate on
bybit_2 spot-margin Buy orders (Bybit ErrCode 170131, 20/58 trades over
9 h) where the coordinator kept re-firing into a wedged borrow gate
because nothing tracked consecutive rejections or paused the account.

The circuit breaker in src/core/coordinator.py increments a per-account
counter on every ``exchange_rejected`` result, resets it on a successful
placement, and once the counter reaches
``_EXCHANGE_REJECTION_PAUSE_THRESHOLD`` it calls
``set_account_dry_run(account, True)`` plus a ``level="critical"``
alert.

These tests pin the contract.
"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from src.core import coordinator as coord_mod
from src.core.coordinator import Coordinator, OrderPackage


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
    monkeypatch.setenv("BYBIT_KEY_LIVE", "test-value")
    monkeypatch.setattr(
        "src.units.accounts.execute._fetch_balance",
        lambda client, account_cfg, **kwargs: _HAPPY_SPOT_BALANCES["total_account_usd"],
    )


@pytest.fixture(autouse=True)
def _reset_circuit_breaker_state():
    """Each test starts with empty rejection counters + clean dry-run
    overrides."""
    coord_mod._EXCHANGE_REJECTION_COUNTS.clear()
    from src.units.accounts import _DRY_RUN_OVERRIDES
    _DRY_RUN_OVERRIDES.clear()
    yield
    coord_mod._EXCHANGE_REJECTION_COUNTS.clear()
    _DRY_RUN_OVERRIDES.clear()


def _pkg() -> OrderPackage:
    # Inject account_balances_usd so the coordinator's _default_balance_fetcher
    # returns 10_000 USD for bybit_live — without this the sizer sees balance=0,
    # refuses below_min_balance, and never reaches execute_pkg (so the rejection
    # counter never increments).
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=80_000.0,
        sl=79_500.0,
        tp=80_500.0,
        confidence=0.42,
        meta={
            "strategy_name": "vwap",
            "account_balances_usd": {"bybit_live": 10_000.0},
        },
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


def _stub_reject(_pkg_arg, _cfg, **_kwargs):
    """Simulate the exchange-side rejection that the catch block at
    coordinator.py:1099 sees — any non-RiskBreach exception escaping
    execute_pkg is logged as ``exchange_rejected``."""
    raise RuntimeError(
        "Bybit retCode 170131: Insufficient balance "
        "(spot-margin Buy isLeverage=1)"
    )


def _stub_succeed(_pkg_arg, account_cfg, **_kwargs):
    return f"trade-{account_cfg['account_id']}"


# ---------------------------------------------------------------------------
# Counter increments + threshold trip
# ---------------------------------------------------------------------------


def test_two_rejections_below_threshold_no_auto_pause(coord, live_yaml):
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=_stub_reject,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        for _ in range(2):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

    # Two rejections accumulated — threshold is 3, so no auto-pause yet.
    assert coord_mod._EXCHANGE_REJECTION_COUNTS.get("bybit_live") == 2
    from src.units.accounts import _DRY_RUN_OVERRIDES
    assert "bybit_live" not in _DRY_RUN_OVERRIDES


def test_three_rejections_trips_circuit_breaker(coord, live_yaml):
    """Threshold reached — account auto-flipped to dry_run on the 3rd
    consecutive exchange_rejected."""
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=_stub_reject,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        for _ in range(3):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

    # Counter cleared after the auto-pause (so we don't double-fire).
    assert coord_mod._EXCHANGE_REJECTION_COUNTS.get("bybit_live", 0) == 0
    # set_account_dry_run was called → override is set.
    from src.units.accounts import _DRY_RUN_OVERRIDES
    assert _DRY_RUN_OVERRIDES.get("bybit_live") is True


def test_successful_placement_resets_counter(coord, live_yaml):
    """A clean fill clears the consecutive-rejection counter."""
    with patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        # Two rejections, then a success.
        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=_stub_reject,
        ):
            for _ in range(2):
                coord.multi_account_execute(_pkg(), accounts_path=live_yaml)
        assert coord_mod._EXCHANGE_REJECTION_COUNTS.get("bybit_live") == 2

        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=_stub_succeed,
        ):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

    # Counter must be cleared.
    assert "bybit_live" not in coord_mod._EXCHANGE_REJECTION_COUNTS

    # And the next two rejections must not trip the breaker (they're a
    # fresh streak, not a continuation of the prior two).
    from src.units.accounts import _DRY_RUN_OVERRIDES
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=_stub_reject,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        for _ in range(2):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)
    assert coord_mod._EXCHANGE_REJECTION_COUNTS.get("bybit_live") == 2
    assert "bybit_live" not in _DRY_RUN_OVERRIDES


def test_critical_alert_emitted_on_pause(coord, live_yaml):
    """The auto-pause emits a level=critical push_alert so the operator
    sees it via the alert channel + Telegram."""
    captured_alerts = []
    real_push = coord.push_alert

    def _capture(message, **kwargs):
        captured_alerts.append({"message": message, **kwargs})
        return real_push(message, **kwargs)

    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=_stub_reject,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ), patch.object(coord, "push_alert", side_effect=_capture):
        for _ in range(3):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

    critical = [a for a in captured_alerts if a.get("level") == "critical"]
    assert len(critical) == 1, (
        f"expected exactly one critical alert; got {len(critical)} "
        f"of {len(captured_alerts)} total"
    )
    assert "auto-paused" in critical[0]["message"]
    assert critical[0]["account"] == "bybit_live"
    assert critical[0]["consecutive_rejections"] == 3


def test_pause_does_not_double_fire_on_subsequent_rejections(coord, live_yaml):
    """After the breaker trips and counter resets, the next rejection
    starts a new streak — it must not immediately re-pause."""
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=_stub_reject,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        for _ in range(3):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

        # Account is now in dry_run. Subsequent dispatches go through the
        # dry path (no exchange call, so no _stub_reject). The counter
        # should stay zero because the dispatch succeeded in dry mode.
        for _ in range(2):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

    # Either zero (dispatch succeeded as dry) or absent (popped on
    # success) — both shapes mean the breaker did not double-fire.
    assert coord_mod._EXCHANGE_REJECTION_COUNTS.get("bybit_live", 0) == 0
