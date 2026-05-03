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
from src.utils.paths import repo_root as _repo_root  # noqa: E402
REPO_ROOT = _repo_root()


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


# ---------------------------------------------------------------------------
# Per-account exchange-state reads
# ---------------------------------------------------------------------------
#
# These helpers belong to the accounts unit per CLAUDE.md
# § "Architecture rules" § 3 — reading exchange state for a specific
# account is the accounts unit's responsibility, not the UI unit's. They
# previously lived under ``src/units/ui/data_loaders.py`` and were
# called *across* the unit boundary by every consumer (Telegram bot,
# coordinator, monitor loop). Lifting them here lets the upcoming
# BUG-042 monitor-loop reconciler depend on the accounts unit directly,
# closes the wrong-direction import (UI → accounts), and gives every
# caller a single canonical entry point.
#
# Behaviour-preserving lift — the original implementation under
# ``src/units/ui/data_loaders.py::account_open_positions`` is kept as a
# thin delegate so legacy callers continue to work.


def _f(x: Any, default: float = 0.0) -> float:
    """Best-effort coerce *x* to ``float``. Mirrors the private helper
    that previously lived in ``data_loaders.py`` so the lifted
    ``account_open_positions`` keeps the same numeric semantics.
    """
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def account_open_positions(
    account: Dict[str, Any],
) -> Optional[list]:
    """Return list of ``{symbol, side, size, entry_price, unrealised_pnl}``
    dicts for the account's exchange-side open positions (``size > 0``).

    ``None`` on any failure path so callers can distinguish "no
    positions" (``[]``) from "could not read" (``None``):

    * non-dict / missing account argument
    * unsupported exchange (anything other than ``bybit`` / ``binance``)
    * missing creds (``bybit_client_for`` / ``binance_conn_for`` returns ``None``)
    * exchange SDK exception (logged + ``report_api_failure``)

    Lifted from ``src/units/ui/data_loaders.py:account_open_positions``
    in the BUG-042 PR 1 foundation step. Behaviour-preserving — the UI
    delegate now imports from here; per-account exchange-state reads
    are the accounts unit's responsibility.
    """
    if not isinstance(account, dict):
        return None
    ex = (account.get("exchange") or "unknown").lower()
    try:
        if ex == "bybit":
            client = bybit_client_for(account)
            if client is None:
                return None
            resp = client.get_positions(category="linear", settleCoin="USDT")
            raw = resp.get("result", {}).get("list", []) if isinstance(resp, dict) else []
            out = []
            for p in raw:
                size = _f(p.get("size"))
                if size <= 0:
                    continue
                out.append({
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "size": size,
                    "entry_price": _f(p.get("avgPrice")),
                    "unrealised_pnl": _f(p.get("unrealisedPnl")),
                })
            return out
        if ex == "binance":
            conn = binance_conn_for(account)
            if conn is None:
                return None
            out = []
            for p in (conn.get_positions() or []):
                size = _f(p.get("contracts", p.get("positionAmt")))
                if size == 0:
                    continue
                out.append({
                    "symbol": p.get("symbol"),
                    "side": p.get("side") or ("long" if size > 0 else "short"),
                    "size": abs(size),
                    "entry_price": _f(p.get("entryPrice")),
                    "unrealised_pnl": _f(
                        p.get("unrealizedPnl", p.get("unrealised_pnl"))
                    ),
                })
            return out
        return None
    except Exception as exc:  # noqa: BLE001
        aid = account.get("account_id") or "unknown"
        logger.warning("account_open_positions(%s): %s", aid, exc)
        try:
            from src.runtime.api_reporting import report_api_failure
            report_api_failure(
                exchange=ex, op="get_positions", account_id=str(aid),
                error=f"{type(exc).__name__}: {exc}", exception=exc,
            )
        except Exception:  # noqa: BLE001
            pass
        return None
