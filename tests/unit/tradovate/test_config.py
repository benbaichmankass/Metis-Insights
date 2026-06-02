"""Tests for TradovateConfig: env resolution + cred validation."""
from __future__ import annotations

import pytest

from src.units.accounts.tradovate.config import TradovateConfig, TradovateEnv
from src.units.accounts.tradovate.exceptions import TradovateConfigError


_FULL_CREDS = {
    "TRADOVATE_USERNAME": "u", "TRADOVATE_PASSWORD": "p",
    "TRADOVATE_APP_ID": "appid", "TRADOVATE_APP_VERSION": "1.0",
    "TRADOVATE_CID": "1234", "TRADOVATE_SECRET": "s",
    "TRADOVATE_DEVICE_ID": "dev",
}


def test_demo_is_default():
    cfg = TradovateConfig.load(_FULL_CREDS)
    assert cfg.env is TradovateEnv.DEMO
    assert cfg.is_demo
    assert "demo.tradovateapi.com" in cfg.urls.rest_base
    assert "demo.tradovateapi.com" in cfg.urls.ws_trading


def test_live_endpoints_resolved():
    cfg = TradovateConfig.load({**_FULL_CREDS, "TRADOVATE_ENV": "live"})
    assert cfg.env is TradovateEnv.LIVE
    assert not cfg.is_demo
    assert "live.tradovateapi.com" in cfg.urls.rest_base
    assert "live.tradovateapi.com" in cfg.urls.ws_trading


def test_missing_creds_raise_with_var_names():
    bad = {**_FULL_CREDS, "TRADOVATE_PASSWORD": ""}
    with pytest.raises(TradovateConfigError) as exc:
        TradovateConfig.load(bad)
    assert "TRADOVATE_PASSWORD" in str(exc.value)
    # never leaks the partial value
    assert "p" not in str(exc.value).split("TRADOVATE_PASSWORD")[1][:20]


def test_invalid_env_raises():
    with pytest.raises(TradovateConfigError):
        TradovateConfig.load({**_FULL_CREDS, "TRADOVATE_ENV": "staging"})


def test_dry_run_default_true():
    cfg = TradovateConfig.load(_FULL_CREDS)
    assert cfg.dry_run is True


def test_dry_run_false_when_set():
    cfg = TradovateConfig.load({**_FULL_CREDS, "TRADOVATE_DRY_RUN": "false"})
    assert cfg.dry_run is False


def test_allowed_symbols_csv():
    cfg = TradovateConfig.load(
        {**_FULL_CREDS, "TRADOVATE_ALLOWED_SYMBOLS": "mesm6, mnqm6"}
    )
    assert cfg.allowed_symbols == frozenset({"MESM6", "MNQM6"})


def test_auth_payload_uses_int_cid_when_numeric():
    cfg = TradovateConfig.load(_FULL_CREDS)
    p = cfg.auth_payload()
    assert p["cid"] == 1234
    assert p["name"] == "u"


def test_auth_payload_keeps_string_cid_when_not_numeric():
    cfg = TradovateConfig.load({**_FULL_CREDS, "TRADOVATE_CID": "abc"})
    assert cfg.auth_payload()["cid"] == "abc"
