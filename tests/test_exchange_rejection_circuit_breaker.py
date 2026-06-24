"""Circuit breaker for exchange-side rejection storms.

The 2026-05-10 layer-2 health review surfaced a 34% rejection rate on
bybit_2 spot-margin Buy orders (Bybit ErrCode 170131, 20/58 trades over
9 h) where the coordinator kept re-firing into a wedged borrow gate
because nothing tracked consecutive rejections or alerted the operator.

CONTRACT CHANGE (Prime Directive, adopted 2026-05-12 — see CLAUDE.md
"Prime Directive" and docs/CLAUDE-RULES-CANONICAL.md): the breaker is
**alert-only**. It MUST NOT switch an account off. "The system never
switches itself off. No auto-flip, no breaker that toggles mode." The
original auto-pause (``set_account_dry_run`` on threshold) was removed
in the safeguards follow-on after the 2026-05-12 silent-flip incident.

Current contract in ``src/core/coordinator.py`` (lines 1387, 1452-1474):
the coordinator increments a per-account ``_EXCHANGE_REJECTION_COUNTS``
counter on every ``exchange_rejected`` result, clears it on a successful
placement (line 1387), and once the counter reaches
``_EXCHANGE_REJECTION_ALERT_THRESHOLD`` (3) it pushes a
``level="critical"`` alert whose message says the account **stays live**
and that the operator should use ``set-account-mode`` to pause manually
if warranted. The account is never auto-flipped; the counter is not
reset at the threshold.

These tests pin that alert-only contract.
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
    """Each test starts with empty rejection counters."""
    coord_mod._EXCHANGE_REJECTION_COUNTS.clear()
    yield
    coord_mod._EXCHANGE_REJECTION_COUNTS.clear()


def _assert_no_flip_mechanism():
    """Prime Directive (post-2026-06-10 dead-code cleanup): there is no
    account-mode flip mechanism at all — the ``_DRY_RUN_OVERRIDES`` dict +
    ``set_account_dry_run`` shim were removed, so the breaker structurally
    cannot toggle an account to dry_run."""
    import src.units.accounts as _acc
    assert not hasattr(_acc, "set_account_dry_run")
    assert not hasattr(_acc, "_DRY_RUN_OVERRIDES")


def _pkg() -> OrderPackage:
    # Inject account_balances_usd so the coordinator's _default_balance_fetcher
    # returns 10_000 USD for bybit_live — without this the sizer sees balance=0,
    # refuses with zero_balance, and never reaches execute_pkg (so the rejection
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
    _assert_no_flip_mechanism()


def test_three_rejections_alerts_but_account_stays_live(coord, live_yaml):
    """Threshold reached — the breaker alerts but the account STAYS LIVE.

    Prime Directive (CLAUDE.md, 2026-05-12): no auto-flip. The counter
    keeps its value (not reset at threshold) and no dry-run override is
    set. Operator pauses manually via set-account-mode if warranted.
    """
    with patch(
        "src.units.accounts.execute.execute_pkg",
        side_effect=_stub_reject,
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=object(),
    ):
        for _ in range(3):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

    # Counter reflects the 3 consecutive rejections (not reset at threshold).
    assert coord_mod._EXCHANGE_REJECTION_COUNTS.get("bybit_live") == 3
    # The account is NOT auto-flipped to dry_run (Prime Directive).
    _assert_no_flip_mechanism()


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
    _assert_no_flip_mechanism()


def test_critical_alert_emitted_at_threshold(coord, live_yaml):
    """At the threshold the breaker emits a level=critical push_alert so
    the operator sees it via the alert channel + Telegram. The message
    states the account STAYS LIVE (no auto-pause — Prime Directive)."""
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
    # Prime Directive: the alert tells the operator the account stays live
    # and to pause manually — it must NOT claim an auto-pause happened.
    assert "stays live" in critical[0]["message"]
    assert "auto-paused" not in critical[0]["message"]
    assert critical[0]["account"] == "bybit_live"
    assert critical[0]["consecutive_rejections"] == 3


def test_breaker_never_auto_pauses_regardless_of_streak(coord, live_yaml):
    """No matter how long the rejection streak, the breaker never flips
    the account to dry_run (Prime Directive). The counter keeps climbing
    past the threshold and the account stays live; each rejection at or
    past the threshold re-emits the critical alert."""
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
        for _ in range(5):
            coord.multi_account_execute(_pkg(), accounts_path=live_yaml)

    # Counter keeps climbing — the account is never auto-paused, so every
    # dispatch still reaches execute_pkg and rejects.
    assert coord_mod._EXCHANGE_REJECTION_COUNTS.get("bybit_live") == 5
    _assert_no_flip_mechanism()
    # Critical alerts fire on each rejection at/after the threshold (3,4,5).
    critical = [a for a in captured_alerts if a.get("level") == "critical"]
    assert len(critical) == 3
