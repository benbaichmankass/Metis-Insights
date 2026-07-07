"""BUG-042 PR 1 — foundation: ``account_open_positions`` lifted from
``src/units/ui/data_loaders.py`` to ``src/units/accounts/clients.py``.

Per CLAUDE.md § "Architecture rules" § 3, per-account exchange-state
reads are the accounts unit's responsibility. The UI unit previously
owned this helper, which forced every consumer (Telegram bot,
coordinator, monitor loop) to reach across the unit boundary. The
lift fixes the wrong-direction import and gives the upcoming BUG-042
reconciler a direct dependency on the accounts unit.

Five contracts under test:

1. Behaviour-preserving lift — bybit happy path returns the same
   shape the legacy helper produced.
2. Missing creds (``bybit_client_for`` returns ``None``) → ``None``.
3. Exchange SDK exception → ``None`` + warning + ``report_api_failure``
   call (in-line, since the orig path swallowed exceptions).
4. Unsupported exchange → ``None``.
5. Legacy UI delegate (``data_loaders.account_open_positions``) still
   works and returns the new implementation's output.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.units.accounts.clients import account_open_positions
from src.units.ui.data_loaders import (
    account_open_positions as legacy_account_open_positions,
)


@pytest.fixture
def bybit_account():
    # ``market_type: linear`` preserves the pre-2026-05-06 perp-position
    # behaviour these tests pin: ``account_open_positions`` queries the
    # v5 ``/position/list`` endpoint with ``category="linear"``. Spot
    # accounts return ``[]`` without a network call (covered by
    # ``test_spot_category_routing.py``).
    return {
        "account_id": "bybit_2",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "market_type": "linear",
    }


# ---------------------------------------------------------------------------
# 1. Behaviour-preserving lift — bybit happy path
# ---------------------------------------------------------------------------


class TestBybitHappyPath:
    """The lifted implementation must produce the same shape the
    pre-lift ``data_loaders`` version did.
    """

    def test_returns_size_filtered_positions_with_canonical_keys(
        self, bybit_account,
    ):
        # Stub the bybit client so the test runs without exchange
        # creds. ``get_positions`` mirrors the live response shape:
        # ``{"result": {"list": [{...}, ...]}}``.
        fake_resp = {
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.005",
                        "avgPrice": "78250.4",
                        "unrealisedPnl": "1.23",
                    },
                    {
                        # zero-size row → must be filtered out
                        "symbol": "ETHUSDT",
                        "side": "Sell",
                        "size": "0",
                        "avgPrice": "3200.0",
                        "unrealisedPnl": "0",
                    },
                    {
                        "symbol": "SOLUSDT",
                        "side": "Sell",
                        "size": "12",
                        "avgPrice": "150.0",
                        "unrealisedPnl": "-0.4",
                    },
                ],
            },
        }

        class _FakeClient:
            def get_positions(self, **kw):
                return fake_resp

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_FakeClient(),
        ):
            out = account_open_positions(bybit_account)

        assert out is not None
        assert len(out) == 2  # zero-size filtered
        btc, sol = out
        assert btc == {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": 0.005,
            "entry_price": 78250.4,
            "unrealised_pnl": 1.23,
        }
        assert sol["symbol"] == "SOLUSDT"
        assert sol["size"] == 12.0


# ---------------------------------------------------------------------------
# 2. Missing-creds path
# ---------------------------------------------------------------------------


def test_missing_creds_returns_none(bybit_account):
    """When ``bybit_client_for`` returns ``None`` (creds missing) the
    helper must return ``None`` — distinguishable from ``[]`` which
    would mean "no open positions".
    """
    with patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=None,
    ):
        out = account_open_positions(bybit_account)

    assert out is None


# ---------------------------------------------------------------------------
# 3. Exception path — must not raise
# ---------------------------------------------------------------------------


def test_exchange_exception_returns_none_and_reports(bybit_account):
    """The original implementation logged + reported via
    ``report_api_failure`` and returned ``None``. The lift must
    preserve both halves of that contract.
    """

    class _BoomClient:
        def get_positions(self, **kw):
            raise RuntimeError("simulated bybit 5xx")

    with patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=_BoomClient(),
    ), patch(
        "src.runtime.api_reporting.report_api_failure",
    ) as report_mock:
        out = account_open_positions(bybit_account)

    assert out is None
    report_mock.assert_called_once()
    kw = report_mock.call_args.kwargs
    assert kw["exchange"] == "bybit"
    assert kw["op"] == "get_positions"
    assert kw["account_id"] == "bybit_2"
    assert "RuntimeError" in kw["error"]


# ---------------------------------------------------------------------------
# 4. Unsupported exchange / bad arg
# ---------------------------------------------------------------------------


def test_unsupported_exchange_returns_none():
    out = account_open_positions({
        "account_id": "x1",
        "exchange": "not_a_real_exchange",
    })
    assert out is None


def test_non_dict_argument_returns_none():
    assert account_open_positions(None) is None
    assert account_open_positions("bybit_2") is None
    assert account_open_positions(42) is None


# ---------------------------------------------------------------------------
# IB logged-out-but-connected gateway hardening (BL — logged-out false-close)
# ---------------------------------------------------------------------------
#
# A logged-out IB Gateway reports connected=true but net_liquidation=None and
# positions() returns [] — indistinguishable from genuinely flat. The empty
# case must be gated on net_liquidation populated: populated → trustworthy []
# (genuinely flat); not populated / balance() read-failure → None (skip
# conservatively, so the reconciler never false-closes a genuinely-open row).
# A NON-empty snapshot is proof of a live session → returned as-is with NO
# extra balance() health round-trip.


@pytest.fixture
def ib_account():
    return {
        "account_id": "ib_paper",
        "exchange": "interactive_brokers",
        "mode": "live",
    }


class _FakeIBClient:
    """Minimal stand-in for IBClient — records whether balance() was called."""

    def __init__(self, positions, net_liq=None, balance_raises=None):
        self._positions = positions
        self._net_liq = net_liq
        self._balance_raises = balance_raises
        self.balance_calls = 0

    def positions(self):
        return self._positions

    def balance(self):
        self.balance_calls += 1
        if self._balance_raises is not None:
            raise self._balance_raises
        return {"net_liquidation": self._net_liq}


def test_ib_empty_positions_net_liq_populated_returns_flat(ib_account):
    """Empty IB snapshot + net_liquidation populated → trustworthy [] (flat)."""
    fake = _FakeIBClient(positions=[], net_liq=10234.5)
    with patch(
        "src.units.accounts.clients.ib_read_client_for",
        return_value=fake,
    ):
        out = account_open_positions(ib_account)
    assert out == []  # genuinely flat
    assert fake.balance_calls == 1  # health checked on the empty path


def test_ib_empty_positions_net_liq_none_returns_none(ib_account):
    """Logged-out gateway: empty snapshot + net_liquidation None → None (read
    failure), NOT [] — so downstream reconcilers skip and never false-close."""
    fake = _FakeIBClient(positions=[], net_liq=None)
    with patch(
        "src.units.accounts.clients.ib_read_client_for",
        return_value=fake,
    ):
        out = account_open_positions(ib_account)
    assert out is None
    assert fake.balance_calls == 1


def test_ib_empty_positions_net_liq_zero_returns_none(ib_account):
    """net_liquidation 0.0 (the logged-out balance() default — the tag is
    absent) is also "not verified logged-in" → None, not []."""
    fake = _FakeIBClient(positions=[], net_liq=0.0)
    with patch(
        "src.units.accounts.clients.ib_read_client_for",
        return_value=fake,
    ):
        out = account_open_positions(ib_account)
    assert out is None


def test_ib_empty_positions_balance_raises_returns_none(ib_account):
    """If the health balance() read itself raises (IBConnectionError or any
    exception), treat the empty snapshot as a read-failure → None."""
    from src.units.accounts.ib_client import IBConnectionError

    fake = _FakeIBClient(
        positions=[], balance_raises=IBConnectionError("accountSummary timed out")
    )
    with patch(
        "src.units.accounts.clients.ib_read_client_for",
        return_value=fake,
    ):
        out = account_open_positions(ib_account)
    assert out is None
    assert fake.balance_calls == 1


def test_ib_nonempty_positions_returned_as_is_no_health_call(ib_account):
    """A NON-empty IB snapshot is proof of a live session → returned as-is with
    NO extra balance() round-trip (the common path adds no IB latency)."""
    rows = [{
        "symbol": "MES", "side": "long", "size": 2.0,
        "entry_price": 5300.0, "unrealised_pnl": 1.5,
    }]
    fake = _FakeIBClient(positions=rows, net_liq=10234.5)
    with patch(
        "src.units.accounts.clients.ib_read_client_for",
        return_value=fake,
    ):
        out = account_open_positions(ib_account)
    assert out == rows
    assert fake.balance_calls == 0  # NO health round-trip on the has-positions path


def test_ib_positions_raises_connection_error_returns_none(ib_account):
    """positions() raising IBConnectionError (down gateway) → None, unchanged
    behaviour (and no balance() call — we never reach the empty-case gate)."""
    from src.units.accounts.ib_client import IBConnectionError

    class _Boom(_FakeIBClient):
        def positions(self):
            raise IBConnectionError("gateway unreachable")

    fake = _Boom(positions=[], net_liq=10234.5)
    with patch(
        "src.units.accounts.clients.ib_read_client_for",
        return_value=fake,
    ):
        out = account_open_positions(ib_account)
    assert out is None
    assert fake.balance_calls == 0


def test_ib_dry_account_returns_none(ib_account):
    """Dry IB account is never dialled → None (unchanged)."""
    ib_account = dict(ib_account, mode="dry_run")
    out = account_open_positions(ib_account)
    assert out is None


# ---------------------------------------------------------------------------
# Alpaca empty-snapshot / read-failure hardening
# (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE)
# ---------------------------------------------------------------------------
#
# Mirrors the IB hardening above: AlpacaClient.positions() now returns None on a
# read failure (so a transient outage isn't read as "flat"), and the empty-but-
# successful case is gated on balance() confirming a live session before [] is
# trusted as flat. This is what stops the position-snapshot reconciler from
# false-closing a live Alpaca position whose fill hasn't propagated yet.


@pytest.fixture
def alpaca_account():
    return {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live"}


class _FakeAlpacaClient:
    """Stand-in for AlpacaClient — records whether balance() was called."""

    def __init__(self, positions, bal=None):
        self._positions = positions
        self._bal = bal
        self.balance_calls = 0

    def positions(self):
        return self._positions

    def balance(self):
        self.balance_calls += 1
        return self._bal


def test_alpaca_positions_none_read_failure_returns_none(alpaca_account):
    """positions() None (network / non-2xx / missing creds) → None, never []."""
    fake = _FakeAlpacaClient(positions=None)
    with patch(
        "src.units.accounts.clients.alpaca_client_for", return_value=fake,
    ):
        out = account_open_positions(alpaca_account)
    assert out is None
    assert fake.balance_calls == 0  # never reaches the empty-case gate


def test_alpaca_empty_balance_populated_returns_flat(alpaca_account):
    """Empty snapshot + balance() populated → trustworthy [] (genuinely flat)."""
    fake = _FakeAlpacaClient(positions=[], bal=25000.0)
    with patch(
        "src.units.accounts.clients.alpaca_client_for", return_value=fake,
    ):
        out = account_open_positions(alpaca_account)
    assert out == []
    assert fake.balance_calls == 1  # health checked on the empty path


def test_alpaca_empty_balance_none_returns_none(alpaca_account):
    """Empty snapshot + balance() not populated → None (read-failure), NOT [] —
    so the reconciler skips and can't false-close a propagating fill."""
    fake = _FakeAlpacaClient(positions=[], bal=None)
    with patch(
        "src.units.accounts.clients.alpaca_client_for", return_value=fake,
    ):
        out = account_open_positions(alpaca_account)
    assert out is None
    assert fake.balance_calls == 1


def test_alpaca_empty_balance_negative_returns_none(alpaca_account):
    """BL-20260707-RECONCILER-MASS-FALSE-CLOSE: empty snapshot + a NEGATIVE
    balance() must NOT be trusted as "genuinely flat". Before the fix, the
    guard was ``if not bal:`` — a negative float is truthy in Python, so a
    deeply negative account (the real incident: alpaca_paper at ~-$67,946)
    sailed through as "verified live" and the empty read got trusted as [],
    letting position_snapshot_reconciler mass-close several still-open
    positions with fabricated PnL. A negative balance is itself an anomalous
    account state — it must return None (skip) exactly like an unreadable
    balance, not a trustworthy [] (flat)."""
    fake = _FakeAlpacaClient(positions=[], bal=-67945.12)
    with patch(
        "src.units.accounts.clients.alpaca_client_for", return_value=fake,
    ):
        out = account_open_positions(alpaca_account)
    assert out is None
    assert fake.balance_calls == 1


def test_alpaca_empty_balance_zero_returns_flat(alpaca_account):
    """Empty snapshot + balance()==0.0 (a real, successful read of a
    genuinely-zero-equity account) is trusted as flat — 0.0 is not None and
    not negative, so it's a legitimate "verified live" reading, distinct from
    the read-failure case (bal is None)."""
    fake = _FakeAlpacaClient(positions=[], bal=0.0)
    with patch(
        "src.units.accounts.clients.alpaca_client_for", return_value=fake,
    ):
        out = account_open_positions(alpaca_account)
    assert out == []
    assert fake.balance_calls == 1


def test_alpaca_nonempty_returned_normalised_no_health_call(alpaca_account):
    """A non-empty snapshot is proof of a live session → returned normalised to
    the canonical shape with NO extra balance() round-trip."""
    fake = _FakeAlpacaClient(
        positions=[{
            "symbol": "IWM", "side": "buy", "qty": 9.0,
            "avg_price": 298.53, "unrealized_pnl": 1.2,
        }],
        bal=25000.0,
    )
    with patch(
        "src.units.accounts.clients.alpaca_client_for", return_value=fake,
    ):
        out = account_open_positions(alpaca_account)
    assert out == [{
        "symbol": "IWM", "side": "long", "size": 9.0,
        "entry_price": 298.53, "unrealised_pnl": 1.2,
    }]
    assert fake.balance_calls == 0


def test_alpaca_dry_account_returns_none(alpaca_account):
    """Dry alpaca account is never dialled → None."""
    out = account_open_positions(dict(alpaca_account, mode="dry_run"))
    assert out is None


# ---------------------------------------------------------------------------
# 5. Legacy UI delegate
# ---------------------------------------------------------------------------


class TestLegacyUiDelegate:
    """``data_loaders.account_open_positions`` must keep working —
    existing callers (Telegram bot ``/balance``, dashboards) import
    from there.
    """

    def test_legacy_delegate_returns_lifted_implementations_output(
        self, bybit_account,
    ):
        fake_resp = {
            "result": {
                "list": [{
                    "symbol": "BTCUSDT", "side": "Buy",
                    "size": "0.01", "avgPrice": "80000",
                    "unrealisedPnl": "0",
                }],
            },
        }

        class _FakeClient:
            def get_positions(self, **kw):
                return fake_resp

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_FakeClient(),
        ):
            out = legacy_account_open_positions(bybit_account)

        assert out is not None
        assert len(out) == 1
        assert out[0]["symbol"] == "BTCUSDT"
        assert out[0]["size"] == 0.01
