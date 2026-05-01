"""Duplicate-API-key detection for accounts.yaml (BUG-033).

When two accounts in accounts.yaml resolve to the same exchange API
key (because the operator typo'd one of the env-var names, or the
master file populated the wrong slot), every order routed to either
account hits the same wallet. The /accounts_status output looks
correct (two accounts, two balances) but the balances are identical.

This module runs at trader startup and pings the operator on Telegram
when it detects collisions, naming the offending accounts. It does
**not** refuse to start trading — per the operator's preference, the
trader continues and the per-account risk caps still apply (so the
blast radius of running both into one wallet is bounded).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)


def find_duplicate_keys(accounts: Iterable[Any]) -> List[Tuple[str, List[str]]]:
    """Return ``[(api_key_suffix, [account_name, ...]), ...]`` for any
    pair of accounts that resolve to the same API key.

    *accounts* is iterable of TradingAccount-like objects with
    ``.name`` and ``.api_key_env`` attributes.

    The returned suffix is the last 4 chars of the resolved key (never
    the full key — keeps the alert Telegram-safe). Accounts whose
    credentials can't be resolved (missing env var) are skipped.
    """
    from src.units.accounts.clients import resolve_credentials

    by_key: Dict[str, List[str]] = defaultdict(list)
    for acc in accounts:
        api_key_env = getattr(acc, "api_key_env", "")
        if not api_key_env:
            continue
        creds = resolve_credentials({
            "api_key_env": api_key_env,
            "exchange": getattr(acc, "exchange", ""),
        })
        if not creds or not creds.get("api_key"):
            continue
        by_key[creds["api_key"]].append(getattr(acc, "name", "?"))

    duplicates: List[Tuple[str, List[str]]] = []
    for api_key, names in by_key.items():
        if len(names) > 1:
            duplicates.append((api_key[-4:], sorted(names)))
    return duplicates


def warn_on_duplicate_keys(accounts: Iterable[Any]) -> None:
    """Emit one outcomes WARN per collision. Never raises.

    Per CLAUDE.md "Autonomous live-trading rule" the trader must not be
    blocked; this only pings.
    """
    try:
        dups = find_duplicate_keys(accounts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dup_key_check failed: %s", exc)
        return

    if not dups:
        return

    try:
        from src.runtime.outcomes import Level, report
    except Exception:  # noqa: BLE001
        # Outcomes unavailable — fall back to logger.warning.
        for suffix, names in dups:
            logger.warning(
                "DUP_API_KEY: accounts=%s share key …%s",
                ", ".join(names), suffix,
            )
        return

    for suffix, names in dups:
        report(
            "accounts_dup_key",
            "detected",
            level=Level.WARN,
            reason=(
                f"accounts {names} resolve to the same API key (…{suffix}). "
                f"check that the env vars in accounts.yaml point at distinct "
                f"keys and that the master file populated each slot."
            ),
            accounts=",".join(names),
            suffix=suffix,
        )
