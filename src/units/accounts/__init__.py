"""Accounts package — loader for TradingAccount objects (S-010 PR #1 / S-011 PR #1).

``load_accounts()`` reads config/accounts.yaml and returns a list of
fully-configured TradingAccount instances, each with its own RiskManager.

``set_account_dry_run()`` persists a dry/live toggle for an account across
``load_accounts()`` calls (module-level dict, process lifetime).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_DEFAULT_ACCOUNTS_YAML = os.path.join(_REPO_ROOT, "config", "accounts.yaml")

# Persistent dry/live overrides: {account_name: dry_run_bool}
# Set via set_account_dry_run(); applied by load_accounts() on each call.
_DRY_RUN_OVERRIDES: Dict[str, bool] = {}


def set_account_dry_run(name: str, dry_run: bool) -> None:
    """Persist a dry/live override for *name* across load_accounts() calls."""
    _DRY_RUN_OVERRIDES[name] = dry_run


def get_dry_run_overrides() -> Dict[str, bool]:
    """Return a copy of the current override dict (for status display)."""
    return dict(_DRY_RUN_OVERRIDES)


def load_accounts(config_path: str = _DEFAULT_ACCOUNTS_YAML) -> "List":
    """Load and return TradingAccount instances from *config_path*.

    Parameters
    ----------
    config_path : str
        Path to accounts.yaml.

    Returns
    -------
    list[TradingAccount]
        One TradingAccount per entry in the YAML ``accounts`` section.

    Raises
    ------
    FileNotFoundError
        When *config_path* does not exist.
    """
    import yaml
    from src.units.accounts.account import TradingAccount
    from src.units.accounts.risk import RiskManager
    from src.units.accounts.prop_risk import PropRiskManager
    from src.units.accounts.clients import resolve_credentials

    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    accounts = []
    for name, cfg in (raw.get("accounts") or {}).items():
        # Velotrade integration: prop accounts get the mission-aware
        # PropRiskManager which adds account-state, mission, and
        # overnight/weekend skip reasons on top of the base gates.
        # Regular bybit accounts continue to use the unchanged
        # RiskManager — keeps the live Bybit path bit-identical.
        account_type = cfg.get("type", "regular")
        if account_type == "prop":
            # Pass account_name so PropRiskManager can read its own
            # section from runtime_state/prop_state.json on init and
            # write back on each record_trade_result.
            rm = PropRiskManager(cfg, account_name=name)
        else:
            rm = RiskManager(cfg.get("risk") or {})
        # Forward-compat: skip accounts explicitly disabled in YAML.
        # (Velotrade scaffold ships with ``enabled: false`` until
        # credentials + SDK wiring land in a follow-up sprint.)
        if cfg.get("enabled") is False:
            continue
        # Velotrade phase-2: detect "not fully configured" accounts
        # (env-var creds missing). Such accounts still load — they
        # appear in /accounts_status with ``configured=False`` and
        # any live action against them refuses + emits a diagnostic
        # ping naming the missing env var.
        api_key_env = cfg.get("api_key_env", "") or ""
        configured = True
        configured_reason: Optional[str] = None
        if api_key_env:
            creds = resolve_credentials({
                "api_key_env": api_key_env,
                "api_secret_env": cfg.get("api_secret_env"),
                "exchange": cfg.get("exchange"),
            })
            if not creds:
                configured = False
                derived_secret = cfg.get("api_secret_env") or (
                    api_key_env.replace("_API_KEY", "_API_SECRET")
                )
                configured_reason = (
                    f"{api_key_env} and/or {derived_secret} not set in env"
                )
        account = TradingAccount(
            name=name,
            exchange=cfg.get("exchange", "bybit"),
            api_key_env=api_key_env,
            risk_manager=rm,
            account_type=account_type,
            strategies=list(cfg.get("strategies", []) or []),
            configured=configured,
            configured_reason=configured_reason,
        )
        # Apply persistent dry/live override if set
        if name in _DRY_RUN_OVERRIDES:
            account.dry_run = _DRY_RUN_OVERRIDES[name]
        accounts.append(account)
    return accounts
