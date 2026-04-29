"""Data loaders for the Telegram bot — see docs/TELEGRAM-SPEC.md (Sprint S-001).

Single source of truth for **dynamic** runtime data the bot needs to render
its 11 commands. Every loader catches its own exceptions and returns a
neutral fallback (``[]`` / ``None`` / ``"⚠️ unavailable"``); command
handlers never see exceptions originating outside their own rendering code.

This module is delivered incrementally:

* PR-B1 (this PR) — account registry, strategies, trader services.
* PR-B2 — DB readers (signals, backtests, logs).
* PR-B3 — exchange-aware account queries (balance, positions, last trade).

Account registry (PM decision §8.1): ``config/accounts.yaml`` (optional —
PyYAML is **not** in requirements.txt and S-001 forbids new deps, so this
branch is gracefully skipped if PyYAML is unavailable) plus ``<repo>/.env``
and ``<repo>/.env.<account_id>`` files.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_BASE_DIR, "..", ".."))

ACCOUNTS_YAML_PATH = os.path.join(REPO_ROOT, "config", "accounts.yaml")
LEGACY_LIVE_SERVICE = "ict-trader-live"
LEGACY_LIVE_ACCOUNT_ID = "live"
TRADER_SERVICE_PREFIX = "ict-trader-"


# -- Strategies / services ----------------------------------------------------

def list_live_strategies() -> List[str]:
    """Return ``STRATEGIES`` from src.runtime.pipeline; ``[]`` on import failure."""
    try:
        from src.runtime.pipeline import STRATEGIES  # type: ignore
        return list(STRATEGIES)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_live_strategies: %s", exc)
        return []


def list_trader_services(deploy_dir: Optional[str] = None) -> List[str]:
    """Return systemd unit names matching ``ict-trader-*.service`` in ``deploy/``."""
    if deploy_dir is None:
        deploy_dir = os.path.join(REPO_ROOT, "deploy")
    try:
        if not os.path.isdir(deploy_dir):
            return []
        units = []
        for name in sorted(os.listdir(deploy_dir)):
            if name.endswith(".service"):
                stem = name[: -len(".service")]
                if stem.startswith(TRADER_SERVICE_PREFIX):
                    units.append(stem)
        return units
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_trader_services: %s", exc)
        return []


# -- Accounts -----------------------------------------------------------------

_ENV_RE = re.compile(r"^\.env\.(?P<account_id>[A-Za-z0-9_\-]+)$")


def _exchange_from_env(env_path: str) -> str:
    try:
        with open(env_path, "r", encoding="utf-8", errors="replace") as fh:
            blob = fh.read().upper()
    except Exception:
        return "unknown"
    if "BYBIT_API_KEY" in blob:
        return "bybit"
    if "BINANCE_API_KEY" in blob:
        return "binance"
    return "unknown"


def _load_yaml_accounts() -> List[Dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    if not os.path.exists(ACCOUNTS_YAML_PATH):
        return []
    try:
        with open(ACCOUNTS_YAML_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_load_yaml_accounts: %s", exc)
        return []
    raw = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("account_id") or item.get("id") or "").strip()
        if not aid:
            continue
        out.append({
            "account_id": aid,
            "exchange": str(item.get("exchange", "")).strip().lower() or "unknown",
            "env_path": str(item.get("env_path", "")).strip() or None,
            "service": str(item.get("service", "")).strip()
                       or f"{TRADER_SERVICE_PREFIX}{aid}",
            "strategies": list(item.get("strategies", [])) or list_live_strategies(),
            "source": "yaml",
        })
    return out


def _load_env_accounts(repo_root: Optional[str] = None) -> List[Dict[str, Any]]:
    """``.env`` (no suffix) → legacy single account ``live`` on ``ict-trader-live``;
    ``.env.<account_id>`` → one account each on ``ict-trader-<account_id>``."""
    repo_root = repo_root or REPO_ROOT
    if not os.path.isdir(repo_root):
        return []
    out: List[Dict[str, Any]] = []
    legacy = os.path.join(repo_root, ".env")
    if os.path.isfile(legacy):
        out.append({
            "account_id": LEGACY_LIVE_ACCOUNT_ID,
            "exchange": _exchange_from_env(legacy),
            "env_path": legacy,
            "service": LEGACY_LIVE_SERVICE,
            "strategies": list_live_strategies(),
            "source": "env",
        })
    try:
        entries = sorted(os.listdir(repo_root))
    except Exception as exc:  # noqa: BLE001
        logger.warning("_load_env_accounts: %s", exc)
        return out
    for name in entries:
        m = _ENV_RE.match(name)
        if not m:
            continue
        aid = m.group("account_id")
        if aid == LEGACY_LIVE_ACCOUNT_ID:
            continue
        env_path = os.path.join(repo_root, name)
        out.append({
            "account_id": aid,
            "exchange": _exchange_from_env(env_path),
            "env_path": env_path,
            "service": f"{TRADER_SERVICE_PREFIX}{aid}",
            "strategies": list_live_strategies(),
            "source": "env",
        })
    return out


def list_accounts() -> List[Dict[str, Any]]:
    """YAML entries first (if PyYAML installed), then ``.env`` discovery.
    Deduplicated by ``account_id`` (first wins). Each dict has: ``account_id``,
    ``exchange``, ``env_path``, ``service``, ``strategies``, ``source``."""
    try:
        out, seen = [], set()
        for acc in _load_yaml_accounts() + _load_env_accounts():
            aid = acc["account_id"]
            if aid in seen:
                continue
            seen.add(aid)
            out.append(acc)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_accounts: %s", exc)
        return []
