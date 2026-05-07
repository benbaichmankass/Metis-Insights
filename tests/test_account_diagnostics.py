"""Tests for the S-023 PR2 specific account diagnostics surface."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


for _mod in ("dotenv", "pybit", "pybit.unified_trading"):
    sys.modules.setdefault(_mod, MagicMock())


# ---------------------------------------------------------------------------
# credentials_check
# ---------------------------------------------------------------------------


def test_credentials_check_missing_both_vars(monkeypatch):
    from src.bot.data_loaders import credentials_check
    monkeypatch.delenv("BYBIT_API_KEY_1", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_1", raising=False)
    err = credentials_check({"api_key_env": "BYBIT_API_KEY_1"})
    assert err is not None
    assert "BYBIT_API_KEY_1" in err
    assert "BYBIT_API_SECRET_1" in err
    assert "EnvironmentFile" in err  # actionable hint


def test_credentials_check_missing_only_secret(monkeypatch):
    from src.bot.data_loaders import credentials_check
    monkeypatch.setenv("BYBIT_API_KEY_1", "x")
    monkeypatch.delenv("BYBIT_API_SECRET_1", raising=False)
    err = credentials_check({"api_key_env": "BYBIT_API_KEY_1"})
    assert err is not None
    assert "BYBIT_API_SECRET_1" in err
    assert "BYBIT_API_KEY_1" not in err  # the present one is NOT listed


def test_credentials_check_present(monkeypatch):
    from src.bot.data_loaders import credentials_check
    monkeypatch.setenv("BYBIT_API_KEY_1", "x")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "y")
    assert credentials_check({"api_key_env": "BYBIT_API_KEY_1"}) is None


def test_credentials_check_custom_secret_env(monkeypatch):
    from src.bot.data_loaders import credentials_check
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.delenv("OTHER_SECRET", raising=False)
    err = credentials_check({
        "api_key_env": "MY_KEY",
        "api_secret_env": "OTHER_SECRET",
    })
    assert "MY_KEY" in err and "OTHER_SECRET" in err


def test_credentials_check_legacy_env_path(tmp_path):
    from src.bot.data_loaders import credentials_check
    legacy = tmp_path / ".env.legacy"
    err = credentials_check({"env_path": str(legacy)})
    assert err is not None and "does not exist" in err
    legacy.write_text("BYBIT_API_KEY=x\nBYBIT_API_SECRET=y\n")
    assert credentials_check({"env_path": str(legacy)}) is None


def test_credentials_check_no_config_at_all():
    from src.bot.data_loaders import credentials_check
    err = credentials_check({"account_id": "weird"})
    assert err is not None and "no api_key_env" in err


def test_credentials_check_non_dict():
    from src.bot.data_loaders import credentials_check
    assert credentials_check("not a dict") == "account config is not a mapping"


# ---------------------------------------------------------------------------
# _bybit_response_error
# ---------------------------------------------------------------------------


def test_bybit_response_error_success():
    from src.bot.data_loaders import _bybit_response_error
    assert _bybit_response_error({"retCode": 0, "result": {}}) is None


def test_bybit_response_error_failure_includes_retcode_retmsg():
    from src.bot.data_loaders import _bybit_response_error
    err = _bybit_response_error({
        "retCode": 10003,
        "retMsg": "API key is invalid.",
    })
    assert "10003" in err and "API key is invalid" in err


def test_bybit_response_error_truncates_long_retmsg():
    from src.bot.data_loaders import _bybit_response_error
    long_msg = "x" * 500
    err = _bybit_response_error({"retCode": 1, "retMsg": long_msg})
    # Truncated to 200 chars
    assert err is not None
    assert len(err) < 250


def test_bybit_response_error_handles_string_retcode():
    """Some Bybit endpoints return retCode as a string."""
    from src.bot.data_loaders import _bybit_response_error
    assert _bybit_response_error({"retCode": "0"}) is None
    err = _bybit_response_error({"retCode": "10003", "retMsg": "bad"})
    assert "10003" in err


# ---------------------------------------------------------------------------
# account_balance_with_diagnostic
# ---------------------------------------------------------------------------


def _account(api_key_env="BYBIT_API_KEY_1", exchange="bybit", **kw):
    return {
        "account_id": kw.pop("account_id", "bybit_1"),
        "exchange": exchange,
        "api_key_env": api_key_env,
        **kw,
    }


def test_balance_diag_missing_creds_short_circuits(monkeypatch):
    from src.bot.data_loaders import account_balance_with_diagnostic
    monkeypatch.delenv("BYBIT_API_KEY_1", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_1", raising=False)
    diag = account_balance_with_diagnostic(_account())
    assert diag["status"] == "missing_creds"
    assert "BYBIT_API_KEY_1" in diag["error"]
    assert diag["total_usdt"] is None


def test_balance_diag_api_error_propagates_retcode(monkeypatch):
    from src.bot import data_loaders as dl
    monkeypatch.setenv("BYBIT_API_KEY_1", "x")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "y")
    fake_client = MagicMock()
    fake_client.get_wallet_balance.return_value = {
        "retCode": 10003,
        "retMsg": "API key is invalid.",
        "result": {},
    }
    with patch.object(dl, "bybit_client_for", return_value=fake_client):
        diag = dl.account_balance_with_diagnostic(_account())
    assert diag["status"] == "api_error"
    assert "10003" in diag["error"]
    assert "API key is invalid" in diag["error"]
    assert diag["raw"]["retCode"] == 10003  # raw response preserved


def test_balance_diag_exchange_exception_classified_as_api_error(monkeypatch):
    from src.bot import data_loaders as dl
    monkeypatch.setenv("BYBIT_API_KEY_1", "x")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "y")
    fake_client = MagicMock()
    fake_client.get_wallet_balance.side_effect = ConnectionError("timed out")
    with patch.object(dl, "bybit_client_for", return_value=fake_client):
        diag = dl.account_balance_with_diagnostic(_account())
    assert diag["status"] == "api_error"
    assert "ConnectionError" in diag["error"]
    assert "timed out" in diag["error"]


def test_balance_diag_ok(monkeypatch):
    from src.bot import data_loaders as dl
    monkeypatch.setenv("BYBIT_API_KEY_1", "x")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "y")
    fake_client = MagicMock()
    fake_client.get_wallet_balance.return_value = {
        "retCode": 0,
        "result": {"list": [{"coin": [{"usdValue": "1234.56"}]}]},
    }
    with patch.object(dl, "bybit_client_for", return_value=fake_client):
        diag = dl.account_balance_with_diagnostic(_account())
    assert diag["status"] == "ok"
    assert abs(diag["total_usdt"] - 1234.56) < 1e-9
    assert diag["error"] is None


def test_balance_diag_unsupported_exchange():
    from src.bot.data_loaders import account_balance_with_diagnostic
    diag = account_balance_with_diagnostic({
        "account_id": "weird", "exchange": "kraken",
        "api_key_env": "KRAKEN_KEY",
    })
    # First fails on credentials_check (env not set), but if creds were
    # set the unsupported branch should fire. Let's exercise both.
    assert diag["status"] in {"missing_creds", "unsupported"}


def test_balance_diag_non_dict_account():
    from src.bot.data_loaders import account_balance_with_diagnostic
    diag = account_balance_with_diagnostic("not a dict")
    assert diag["status"] == "unsupported"
    assert "mapping" in diag["error"]


# ---------------------------------------------------------------------------
# Backward-compat: account_balance() still returns dict-or-None
# ---------------------------------------------------------------------------


def test_account_balance_back_compat_ok(monkeypatch):
    from src.bot import data_loaders as dl
    monkeypatch.setenv("BYBIT_API_KEY_1", "x")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "y")
    fake_client = MagicMock()
    fake_client.get_wallet_balance.return_value = {
        "retCode": 0, "result": {"list": [{"coin": [{"usdValue": "100"}]}]},
    }
    with patch.object(dl, "bybit_client_for", return_value=fake_client):
        bal = dl.account_balance(_account())
    assert isinstance(bal, dict)
    assert bal.get("total_usdt") == 100.0
    assert "raw" in bal


def test_account_balance_back_compat_failure_returns_none(monkeypatch):
    from src.bot.data_loaders import account_balance
    monkeypatch.delenv("BYBIT_API_KEY_1", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_1", raising=False)
    assert account_balance(_account()) is None


# ---------------------------------------------------------------------------
# Coordinator.accounts_status uses the diagnostic
# ---------------------------------------------------------------------------


def test_accounts_status_propagates_specific_error(monkeypatch, tmp_path):
    """End-to-end: a missing env var renders as 'missing env vars: ...' in
    the live_balance_error field, not the old generic message.
    """
    sys.modules.setdefault("pandas", MagicMock())
    from src.bot import data_loaders as dl
    from src.core.coordinator import Coordinator

    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        "accounts:\n"
        "  bybit_1:\n"
        "    type: regular\n"
        "    exchange: bybit\n"
        "    api_key_env: BYBIT_API_KEY_1\n"
        "    strategies: [vwap]\n"
        "    risk:\n"
        "      max_dd_pct: 0.05\n"
        "      daily_usd: 100\n"
        "      pos_size: 500\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "ACCOUNTS_YAML_PATH", str(accounts_yaml))
    monkeypatch.delenv("BYBIT_API_KEY_1", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_1", raising=False)

    coord = Coordinator()
    # Coordinator looks up its own accounts.yaml path; override via the
    # accounts_path param.
    statuses = coord.accounts_status(accounts_path=str(accounts_yaml))
    assert len(statuses) == 1
    err = statuses[0].get("live_balance_error") or ""
    assert "BYBIT_API_KEY_1" in err
    assert "BYBIT_API_SECRET_1" in err
    # Make sure we are NOT seeing the old generic message
    assert "missing API creds or exchange rejected" not in err


def test_accounts_status_propagates_api_retcode(monkeypatch, tmp_path):
    """When env is set but Bybit returns retCode != 0, the retCode +
    retMsg should appear in live_balance_error verbatim.
    """
    sys.modules.setdefault("pandas", MagicMock())
    from src.bot import data_loaders as dl
    from src.core.coordinator import Coordinator

    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(
        "accounts:\n"
        "  bybit_1:\n"
        "    type: regular\n"
        "    exchange: bybit\n"
        "    api_key_env: BYBIT_API_KEY_1\n"
        "    strategies: [vwap]\n"
        "    risk:\n"
        "      max_dd_pct: 0.05\n"
        "      daily_usd: 100\n"
        "      pos_size: 500\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "ACCOUNTS_YAML_PATH", str(accounts_yaml))
    monkeypatch.setenv("BYBIT_API_KEY_1", "x")
    monkeypatch.setenv("BYBIT_API_SECRET_1", "y")

    fake_client = MagicMock()
    fake_client.get_wallet_balance.return_value = {
        "retCode": 10003, "retMsg": "API key is invalid.", "result": {},
    }
    with patch.object(dl, "bybit_client_for", return_value=fake_client):
        coord = Coordinator()
        statuses = coord.accounts_status(accounts_path=str(accounts_yaml))

    assert len(statuses) == 1
    err = statuses[0].get("live_balance_error") or ""
    assert "10003" in err
    assert "API key is invalid" in err
