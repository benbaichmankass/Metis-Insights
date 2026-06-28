"""Accounts package — loader for TradingAccount objects (S-010 PR #1 / S-011 PR #1).

``load_accounts()`` reads config/accounts.yaml and returns a list of
fully-configured TradingAccount instances, each with its own RiskManager.

The dry/live toggle (operator directive 2026-05-03):
* The YAML field ``mode: live | dry_run`` per account is the single
  source of truth. Default = ``live`` per the Autonomous live-trading
  rule (CLAUDE.md). The only sanctioned writer is the
  ``set-account-mode`` system-action (``scripts/ops/set_account_mode.sh``),
  which edits the YAML and restarts the trader.
* The toggle lives on ``RiskManager.dry_run``. ``RiskManager.evaluate()``
  rejects with reason ``"account_mode_dry_run"`` so the executor logs
  the would-be trade but never calls the exchange.

This is the ONLY dry/live toggle in the codebase. There is no
process-level interlock and no in-memory override layer (the legacy
``_DRY_RUN_OVERRIDES`` / ``set_account_dry_run()`` shim was removed in
the 2026-06-10 dead-code cleanup — it had no remaining caller once the
breaker auto-flip and the Telegram ``/accounts`` writer were retired).
``DRY_RUN`` and ``ALLOW_LIVE_TRADING`` env vars were removed; ``MODE``
is no longer required.
"""
from __future__ import annotations

import os
from typing import List, Optional

from src.utils.paths import repo_root as _repo_root

_REPO_ROOT = _repo_root()
_DEFAULT_ACCOUNTS_YAML = os.path.join(_REPO_ROOT, "config", "accounts.yaml")


def _resolve_mode(cfg: dict, name: str) -> bool:
    """Return True when the account is in dry_run mode.

    Resolution order:
      1. YAML ``mode`` field — accepts case-insensitive
         ``live`` / ``dry`` / ``dry_run`` / ``paper``.
      2. Default = ``live`` per Autonomous live-trading rule.

    (``name`` is accepted for signature stability with the callers that
    pass it; there is no longer a per-name override layer.)
    """
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
        # Prop accounts get the mission-aware PropRiskManager which adds
        # account-state, mission, and overnight/weekend skip reasons on
        # top of the base gates. Regular bybit accounts continue to use
        # the unchanged RiskManager — keeps the live Bybit path
        # bit-identical.
        account_type = cfg.get("type", "regular")
        dry_run = _resolve_mode(cfg, name)
        if account_type == "prop":
            # Pass account_name so PropRiskManager can read its own
            # section from runtime_state/prop_state.json on init and
            # write back on each record_trade_result.
            rm = PropRiskManager(cfg, account_name=name, dry_run=dry_run)
        else:
            rm = RiskManager(cfg.get("risk") or {}, dry_run=dry_run, account_id=name)
        # Forward-compat: skip accounts explicitly disabled in YAML
        # (``enabled: false``).
        if cfg.get("enabled") is False:
            continue
        # Detect "not fully configured" accounts (env-var creds
        # missing). Such accounts still load — they appear in
        # /accounts_status with ``configured=False`` and any live
        # action against them refuses + emits a diagnostic ping naming
        # the missing env var.
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
            # Paper-vs-real-money funding category (single source of truth
            # for the paper/real reporting axis). Default "real_money";
            # TradingAccount normalises + coerces an invalid value.
            account_class=str(cfg.get("account_class") or "real_money"),
            # Interactive Brokers connection identity (no API keys). None
            # for non-IB accounts; the coordinator forwards these into the
            # account_cfg dict consumed by ib_client_for.
            ib_host=cfg.get("ib_host"),
            ib_port=cfg.get("ib_port"),
            ib_account=cfg.get("ib_account"),
            ib_client_id=cfg.get("ib_client_id"),
            # Alpaca/OANDA host selector (paper vs live) + optional base_url
            # override. WITHOUT plumbing these, alpaca_client_for /
            # oanda_client_for default to the paper/practice host, so a LIVE
            # account's live key is sent to the wrong endpoint and 401s
            # ("request is not authorized"). BL-20260628-ALPACA-LIVE-HOST.
            alpaca_env=cfg.get("alpaca_env"),
            base_url=cfg.get("base_url"),
            oanda_env=cfg.get("oanda_env"),
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
