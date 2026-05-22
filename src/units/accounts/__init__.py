"""Accounts package — loader for TradingAccount objects (S-010 PR #1 / S-011 PR #1).

``load_accounts()`` reads config/accounts.yaml and returns a list of
fully-configured TradingAccount instances, each with its own RiskManager.

The dry/live toggle (operator directive 2026-05-03):
* The YAML field ``mode: live | dry_run`` per account is the single
  source of truth. Default = ``live`` per the Autonomous live-trading
  rule (CLAUDE.md).
* The toggle lives on ``RiskManager.dry_run``. ``RiskManager.evaluate()``
  rejects with reason ``"account_mode_dry_run"`` so the executor logs
  the would-be trade but never calls the exchange.
* ``set_account_dry_run()`` flips the value at runtime (Telegram
  ``/accounts dry|live <name>``). The override is applied to every
  subsequent ``load_accounts()`` call.

This is the ONLY dry/live toggle in the codebase. There is no
process-level interlock. ``DRY_RUN`` and ``ALLOW_LIVE_TRADING`` env vars
were removed; ``MODE`` is no longer required.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from src.utils.paths import repo_root as _repo_root

_REPO_ROOT = _repo_root()
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


def _resolve_mode(cfg: dict, name: str) -> bool:
    """Return True when the account is in dry_run mode.

    Resolution order:
      1. Runtime override (``_DRY_RUN_OVERRIDES[name]``) — set by Telegram
         ``/accounts dry|live <name>``.
      2. YAML ``mode`` field — accepts case-insensitive
         ``live`` / ``dry`` / ``dry_run``.
      3. Default = ``live`` per Autonomous live-trading rule.
    """
    if name in _DRY_RUN_OVERRIDES:
        return bool(_DRY_RUN_OVERRIDES[name])
    raw = str(cfg.get("mode", "live")).strip().lower()
    return raw in {"dry", "dry_run", "dry-run", "paper"}


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
        dry_run = _resolve_mode(cfg, name)
        if account_type == "prop":
            # Pass account_name so PropRiskManager can read its own
            # section from runtime_state/prop_state.json on init and
            # write back on each record_trade_result.
            rm = PropRiskManager(cfg, account_name=name, dry_run=dry_run)
        else:
            rm = RiskManager(cfg.get("risk") or {}, dry_run=dry_run, account_id=name)
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
            # Preserve the None / [] distinction so the coordinator can
            # tell "no mapping declared" (legacy fallthrough) from
            # "explicitly empty" (block all). Yaml ``strategies: []``
            # ⇒ ``[]``; yaml that omits the key entirely ⇒ ``None``.
            strategies=(
                None if "strategies" not in cfg
                else list(cfg.get("strategies") or [])
            ),
            configured=configured,
            configured_reason=configured_reason,
            market_type=cfg.get("market_type", "spot"),
            demo=bool(cfg.get("demo", False)),
            # Interactive Brokers connection identity (no API keys). None
            # for non-IB accounts; the coordinator forwards these into the
            # account_cfg dict consumed by ib_client_for.
            ib_host=cfg.get("ib_host"),
            ib_port=cfg.get("ib_port"),
            ib_account=cfg.get("ib_account"),
            ib_client_id=cfg.get("ib_client_id"),
            # accounts.yaml is the single source of truth for which
            # instrument(s) this account trades; the multi-symbol tick
            # loop unions these across configured accounts.
            symbols=(
                None if "symbols" not in cfg
                else list(cfg.get("symbols") or [])
            ),
        )
        # Mirror the resolved mode onto the account object so the
        # legacy ``account.dry_run`` callers (TradingAccount.place_order
        # legacy path, /accounts UI) still see the right state. The
        # authoritative gate is ``rm.dry_run`` — checked inside
        # ``RiskManager.evaluate()``; this attribute is for read-only
        # observability.
        account.dry_run = dry_run
        accounts.append(account)
    return accounts
