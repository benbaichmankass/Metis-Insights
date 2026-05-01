"""S-021: smoke-test always-LIVE + accounts_status live-balance + pipeline
multi-account dispatch.

The user-visible problems this PR fixes:

1. ``/smoke_test`` from Telegram was silently dry-running when the bot's
   process environment was missing per-account API creds — operators
   thought they were proving the API was hot, when in fact no exchange
   call had been made. The smoke is now always LIVE: missing creds turn
   into an explicit error instead of a silent dry-run, and the ``dry``
   keyword is no longer recognised by the bot.

2. ``/accounts_status`` showed only the local risk state — it never
   touched the exchange API, so a broken integration looked the same
   as a working one. ``Coordinator.accounts_status`` now includes
   ``live_balance_usdt`` and ``live_balance_error`` for every account.

3. Pipeline-generated trade signals only ever submitted through the
   single ``exchange_client`` injected by ``main.py`` — the
   per-account architecture in ``config/accounts.yaml`` was bypassed.
   When ``MULTI_ACCOUNT_DISPATCH=true`` is exported, ``run_pipeline``
   now fans the signal out to every account via
   ``Coordinator.multi_account_execute``, honouring each account's
   own keys + RiskManager.

All tests are offline — mocked clients, tmp_path YAML, no network.
"""
from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage, _PAUSED_ACCOUNTS


ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_one:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_ONE
        risk:
          max_dd_pct: 0.05
          daily_usd: 200
          pos_size: 1000
      bybit_two:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_TWO
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
""")


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def coord(tmp_path, accounts_yaml):
    _PAUSED_ACCOUNTS.clear()
    c = Coordinator(
        units_path=str(tmp_path / "no-units.yaml"),
        accounts_path=accounts_yaml,
    )
    yield c
    _PAUSED_ACCOUNTS.clear()


# ---------------------------------------------------------------------------
# accounts_status: live API balance integration
# ---------------------------------------------------------------------------


class TestAccountsStatusLiveBalance:
    def test_balance_present_when_api_returns_data(self, coord, accounts_yaml):
        """Successful account_balance() → live_balance_usdt populated, no error."""
        from src.bot import data_loaders as dl

        def fake_balance(account):
            return {"total_usdt": 1234.56, "raw": {}}

        with patch.object(dl, "account_balance", side_effect=fake_balance):
            statuses = coord.accounts_status(accounts_yaml)
        assert len(statuses) == 2
        for s in statuses:
            assert s["live_balance_usdt"] == pytest.approx(1234.56)
            assert s["live_balance_error"] is None

    def test_error_set_when_balance_returns_none(self, coord, accounts_yaml):
        """``None`` from account_balance() (missing creds, API failure) →
        live_balance_error explains the integration is broken."""
        from src.bot import data_loaders as dl

        with patch.object(dl, "account_balance", return_value=None):
            statuses = coord.accounts_status(accounts_yaml)
        for s in statuses:
            assert s["live_balance_usdt"] is None
            assert s["live_balance_error"]
            assert "balance unavailable" in s["live_balance_error"]

    def test_exception_in_balance_call_is_captured(self, coord, accounts_yaml):
        """An exception bubbling out of account_balance() must not crash
        accounts_status — the caller renders the error string instead."""
        from src.bot import data_loaders as dl

        with patch.object(dl, "account_balance", side_effect=RuntimeError("HTTP 401")):
            statuses = coord.accounts_status(accounts_yaml)
        for s in statuses:
            assert s["live_balance_usdt"] is None
            assert "API error" in s["live_balance_error"]
            assert "HTTP 401" in s["live_balance_error"]

    def test_legacy_risk_fields_still_present(self, coord, accounts_yaml):
        """The new fields are additive — every existing field on the
        per-account status dict stays in place so the bot's risk-state
        rendering keeps working."""
        from src.bot import data_loaders as dl

        with patch.object(dl, "account_balance", return_value={"total_usdt": 0.0, "raw": {}}):
            statuses = coord.accounts_status(accounts_yaml)
        for s in statuses:
            for key in (
                "name", "exchange", "account_type", "open_positions",
                "daily_pnl", "max_daily_loss_usd", "max_pos_size_usd", "halted",
            ):
                assert key in s


# ---------------------------------------------------------------------------
# smoke_test_run: factory-returns-None now an error in LIVE mode
# ---------------------------------------------------------------------------


SMOKE_UNITS_YAML = textwrap.dedent("""\
    units:
      strategies:
        - name: smoke_test
          enabled: true
      accounts:
        - id: bybit_smoke
          exchange: bybit
          api_key_env: BYBIT_KEY_SMOKE
""")


@pytest.fixture()
def smoke_coord(tmp_path):
    _PAUSED_ACCOUNTS.clear()
    units = tmp_path / "units.yaml"
    units.write_text(SMOKE_UNITS_YAML)
    c = Coordinator(
        units_path=str(units),
        accounts_path=str(tmp_path / "no-accounts.yaml"),
    )
    yield c
    _PAUSED_ACCOUNTS.clear()


@pytest.fixture()
def smoke_journal(tmp_path, monkeypatch):
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return str(db)


class TestSmokeTestNoSilentDryRun:
    def test_factory_none_in_live_mode_yields_error(self, smoke_coord, smoke_journal):
        result = smoke_coord.smoke_test_run(
            exchange_client_factory=lambda acc: None,
            dry_run=False,
        )
        assert result["ok"] is False
        r = result["results"][0]
        assert r["status"] == "error"
        assert "missing API credentials" in r["reason"]

    def test_factory_raising_in_live_mode_yields_error_with_cause(self, smoke_coord, smoke_journal):
        def boom(acc):
            raise RuntimeError("BYBIT_KEY_SMOKE not set")

        result = smoke_coord.smoke_test_run(
            exchange_client_factory=boom,
            dry_run=False,
        )
        r = result["results"][0]
        assert r["status"] == "error"
        assert "missing API credentials" in r["reason"]
        assert "BYBIT_KEY_SMOKE not set" in r["reason"]

    def test_explicit_dry_run_still_works_for_tests(self, smoke_coord, smoke_journal):
        """Tests / scripts that explicitly request dry_run=True still get
        the dry path. Only the silent fallback in LIVE mode is closed."""
        result = smoke_coord.smoke_test_run(
            exchange_client_factory=lambda acc: None,
            dry_run=True,
        )
        assert result["ok"] is True
        assert result["results"][0]["status"] == "dry_run"

    def test_live_with_mocked_client_still_rejects_too_small(
        self, smoke_coord, smoke_journal,
    ):
        """When creds are present (factory returns a client), Bybit's
        below-min-lot rejection is still captured as 'rejected_too_small'."""
        client = MagicMock()
        client.get_wallet_balance.return_value = {
            "result": {"list": [{"coin": [{"usdValue": "10000"}]}]}
        }
        client.place_order.return_value = {
            "retCode": 10001, "retMsg": "qty invalid", "result": {},
        }
        result = smoke_coord.smoke_test_run(
            exchange_client_factory=lambda acc: client,
            dry_run=False,
        )
        assert result["ok"] is True
        assert result["results"][0]["status"] == "rejected_too_small"


# ---------------------------------------------------------------------------
# Pipeline multi-account dispatch
# ---------------------------------------------------------------------------


class TestPipelineMultiAccountDispatch:
    def test_signal_to_order_package_long(self):
        from src.runtime.pipeline import _signal_to_order_package

        sig = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.01,
            "price": 70000.0,
            "stop_loss": 68600.0,
            "take_profit": 71400.0,
            "meta": {"strategy_name": "vwap", "confidence": 0.55},
        }
        pkg = _signal_to_order_package(sig, settings={})
        assert isinstance(pkg, OrderPackage)
        assert pkg.strategy == "vwap"
        assert pkg.symbol == "BTCUSDT"
        assert pkg.direction == "long"
        assert pkg.entry == 70000.0
        assert pkg.sl == 68600.0
        assert pkg.tp == 71400.0
        assert pkg.confidence == 0.55

    def test_signal_to_order_package_short(self):
        from src.runtime.pipeline import _signal_to_order_package

        sig = {
            "symbol": "BTCUSDT",
            "side": "sell",
            "qty": 0.01,
            "entry_price": 70000.0,
            "stop_loss": 71400.0,
            "take_profit": 68600.0,
            "meta": {"strategy_name": "turtle_soup"},
        }
        pkg = _signal_to_order_package(sig, settings={})
        assert pkg.direction == "short"
        assert pkg.strategy == "turtle_soup"

    def test_signal_to_order_package_missing_levels_raises(self):
        from src.runtime.pipeline import _signal_to_order_package

        sig = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 0.01,
            "price": 70000.0,
            "meta": {"strategy_name": "vwap"},
        }
        with pytest.raises(ValueError, match="missing entry/sl/tp"):
            _signal_to_order_package(sig, settings={})

    def test_signal_to_order_package_invalid_side_raises(self):
        from src.runtime.pipeline import _signal_to_order_package

        sig = {"symbol": "BTCUSDT", "side": "hold", "qty": 0.01}
        with pytest.raises(ValueError, match="side must be"):
            _signal_to_order_package(sig, settings={})

    def test_dispatch_flag_default_off(self, monkeypatch):
        from src.runtime.pipeline import _multi_account_dispatch_enabled

        monkeypatch.delenv("MULTI_ACCOUNT_DISPATCH", raising=False)
        assert _multi_account_dispatch_enabled({}) is False

    def test_dispatch_flag_via_env(self, monkeypatch):
        from src.runtime.pipeline import _multi_account_dispatch_enabled

        monkeypatch.setenv("MULTI_ACCOUNT_DISPATCH", "true")
        assert _multi_account_dispatch_enabled({}) is True

    def test_dispatch_flag_via_settings(self):
        from src.runtime.pipeline import _multi_account_dispatch_enabled

        assert _multi_account_dispatch_enabled({"MULTI_ACCOUNT_DISPATCH": "yes"}) is True
        assert _multi_account_dispatch_enabled({"MULTI_ACCOUNT_DISPATCH": "false"}) is False
