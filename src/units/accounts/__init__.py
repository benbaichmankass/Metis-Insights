"""Accounts package — loader for TradingAccount objects (S-010 PR #1).

``load_accounts()`` reads config/accounts.yaml and returns a list of
fully-configured TradingAccount instances, each with its own RiskManager.
"""
from __future__ import annotations

import os
from typing import List

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_DEFAULT_ACCOUNTS_YAML = os.path.join(_REPO_ROOT, "config", "accounts.yaml")


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

    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    accounts = []
    for name, cfg in (raw.get("accounts") or {}).items():
        risk_cfg = cfg.get("risk") or {}
        rm = RiskManager(risk_cfg)
        account = TradingAccount(
            name=name,
            exchange=cfg.get("exchange", "bybit"),
            api_key_env=cfg.get("api_key_env", ""),
            risk_manager=rm,
            account_type=cfg.get("type", "regular"),
        )
        accounts.append(account)
    return accounts
