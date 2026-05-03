"""Per-account exchange client construction (canonical owner).

This module is the single chokepoint for turning an `account` dict
(loaded from `config/accounts.yaml`) into a live exchange client.
Every other layer — Telegram bot, Coordinator, smoke tests — must
call into here instead of reading exchange env vars directly.

Why this lives in the accounts unit:
    The accounts unit owns "what credentials belong to which account".
    Other layers should treat exchange clients as opaque handles
    handed out by the accounts unit. Routing client construction
    through the bot's data_loaders historically meant a Telegram
    handler bug (e.g. forgetting to pass the account dict through)
    silently fell back to the legacy single-key path and pointed
    every account at one wallet — the BUG-030 root cause.

Resolution order (matches the previous data_loaders implementation):

  1. ``account["api_key_env"]`` — env-var name carrying the API key
     (the accounts.yaml contract). Looks up ``os.environ[<api_key_env>]``
     and the matching ``..._SECRET`` (or ``api_secret_env`` when set
     explicitly).
  2. ``account["env_path"]`` — read the canonical key/secret pair from
     a `.env` file. Legacy single-account path; still used by env-
     discovered accounts.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_BASE_DIR, "..", "..", ".."))


def _read_env_file(env_path: str) -> Dict[str, str]:
    if not env_path or not os.path.exists(env_path):
        return {}
    try:
        from dotenv import dotenv_values  # type: ignore
        values = dotenv_values(env_path)
        return {k: v for k, v in values.items() if v is not None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_read_env_file(%s): %s", env_path, exc)
        return {}


def _derive_secret_env(api_key_env: str, account: Dict[str, Any]) -> str:
    return (
        account.get("api_secret_env")
        or api_key_env.replace("_API_KEY", "_API_SECRET")
    )


def resolve_credentials(account: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Return ``{"api_key": ..., "api_secret": ...}`` or None when missing.

    Pure-data helper: does not import any exchange client library, so it
    is safe to call from environments where pybit / ccxt are not installed
    (tests, lint passes).
    """
    if not isinstance(account, dict):
        return None
    api_key_env = account.get("api_key_env")
    if api_key_env:
        secret_env = _derive_secret_env(api_key_env, account)
        api_key = os.environ.get(api_key_env)
        api_secret = os.environ.get(secret_env)
        if api_key and api_secret:
            return {"api_key": api_key, "api_secret": api_secret}
        return None
    env = _read_env_file(account.get("env_path") or "")
    exchange = str(account.get("exchange", "")).lower()
    if exchange == "binance":
        key, secret = env.get("BINANCE_API_KEY"), env.get("BINANCE_API_SECRET")
    else:
        key, secret = env.get("BYBIT_API_KEY"), env.get("BYBIT_API_SECRET")
    if key and secret:
        return {"api_key": key, "api_secret": secret}
    return None


def bybit_client_for(account: Dict[str, Any]):
    """Return a Bybit HTTP client for *account*, or ``None`` if creds missing."""
    creds = resolve_credentials(account)
    if not creds:
        return None
    from pybit.unified_trading import HTTP  # type: ignore
    testnet = str(os.environ.get("BYBIT_TESTNET", "false")).strip().lower() == "true"
    return HTTP(testnet=testnet, api_key=creds["api_key"], api_secret=creds["api_secret"])


def binance_conn_for(account: Dict[str, Any]):
    """Return a Binance connector for *account*, or ``None`` if creds missing."""
    creds = resolve_credentials(account)
    if not creds:
        return None
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from exchange.binance_connector import BinanceConnector  # type: ignore
    testnet = str(os.environ.get("BINANCE_TESTNET", "false")).strip().lower() == "true"
    return BinanceConnector(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        testnet=testnet,
    )


def velotrade_client_for(account: Dict[str, Any]):
    """Return a DXtrade client for *account*, or ``None`` if creds missing.

    Mirrors :func:`bybit_client_for` and :func:`binance_conn_for` so the
    coordinator's ``multi_account_execute`` client-construction switch
    has a uniform shape: factory returns ``None`` → loader/coordinator
    treats the account as not-configured and emits a diagnostic ping.

    The base URL (sandbox vs prod) is read from
    ``VELOTRADE_BASE_URL`` (account-level override:
    ``account['base_url']``) so the operator can flip between
    environments without touching code. When neither is set, the
    client is constructed with ``base_url=None`` and the eventual SDK
    call site picks the canonical default from the DXtrade contract.

    Returns
    -------
    DXtradeClient | None
        Constructed client when both ``api_key_env`` (e.g.
        ``VELOTRADE_API_KEY_1``) and the matching ``..._SECRET`` env
        var are populated; ``None`` otherwise (account is "not
        fully configured" — downstream code refuses live use and
        pings the operator).
    """
    creds = resolve_credentials(account)
    if not creds:
        return None
    from src.units.accounts.dxtrade_client import DXtradeClient
    base_url = (
        account.get("base_url")
        or os.environ.get("VELOTRADE_BASE_URL")
        or None
    )
    return DXtradeClient(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        base_url=base_url,
    )
