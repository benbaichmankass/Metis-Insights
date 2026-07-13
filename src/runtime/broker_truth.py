"""Broker-truth realized-PnL ledger — the committed source of truth for an
account's **authoritative** lifetime realized PnL when the live per-row journal
can't be trusted for that account.

Why this exists (BL-20260713-BYBIT2-PNL-UNDERRECORD): for a **netting** account
that also mixed spot + perp and switched Bybit sub-accounts mid-history
(``bybit_2``), the journal's per-row ``pnl`` under-records vs the broker's own
wallet truth (reconciler exit=entry false-closes + uncaptured fees + a spot /
sub-account-switch conversion cohort that per-fill FIFO can't attribute — see
``docs/audits/bybit2-broker-reconciliation-2026-07-13.md``). The exchange-fills
store's self-FIFO realized *also* mis-attributes such an account.

The only trustworthy figure for those accounts is the **account-level wallet
delta** (Bybit UM ``Change`` − transfers, which nets fees + funding +
conversions). This module records that authoritative number in a **committed**
JSON ledger (``comms/broker_truth_ledger.json``) so the dashboard can surface it
next to the journal's approximate figure — *without* rewriting any money-DB row
(no fabricated per-row precision).

Same shape/behaviour contract as ``src.runtime.gpu_spend`` (the committed
``gpu_spend_ledger.json`` surfaced at ``/api/bot/gpu/spend``): a **file** (not a
DB table) because it's written by a reviewed reconciliation run + committed;
stdlib-only; **best-effort read** — a missing/garbled ledger degrades to an
empty summary, never raises to the API.

Authoritative-by-record: ``realized_usd`` is whatever the reconciliation run
computed from the operator's UM export (wallet-truth); ``fees_usd`` /
``funding_usd`` are carried for display. It is NOT re-derived here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.utils.paths import repo_root

LEDGER_ENV = "BROKER_TRUTH_LEDGER"


def ledger_path() -> Path:
    """Resolve the ledger file: ``$BROKER_TRUTH_LEDGER`` → ``<repo>/comms/broker_truth_ledger.json``."""
    env = os.environ.get(LEDGER_ENV)
    if env:
        return Path(env)
    return Path(repo_root()) / "comms" / "broker_truth_ledger.json"


def _empty() -> dict[str, Any]:
    return {"schema_version": 1, "accounts": [], "updated_at": None}


def load_ledger(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """The raw ledger dict. Missing/garbled → a default empty ledger (never raises)."""
    p = Path(path) if path is not None else ledger_path()
    if not p.is_file():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        data["accounts"] = []
    data.setdefault("schema_version", 1)
    data.setdefault("updated_at", None)
    return data


def _coerce_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _clean_account(rec: Any) -> dict[str, Any] | None:
    """Coerce one ledger record into the wire shape, or None if it's not usable."""
    if not isinstance(rec, dict):
        return None
    account_id = rec.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        return None
    return {
        "account_id": account_id,
        "realized_usd": _coerce_float(rec.get("realized_usd")),
        "fees_usd": _coerce_float(rec.get("fees_usd")),
        "funding_usd": _coerce_float(rec.get("funding_usd")),
        "as_of": rec.get("as_of"),
        "window_start": rec.get("window_start"),
        "window_end": rec.get("window_end"),
        "source": rec.get("source"),
        "sub_accounts": rec.get("sub_accounts") if isinstance(rec.get("sub_accounts"), list) else None,
        "note": rec.get("note"),
    }


def summarize_broker_truth(
    path: str | os.PathLike[str] | None = None,
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Roll the ledger up for the API / dashboard.

    Returns ``{present, count, account_id, accounts:[...], updated_at}``. When
    ``account_id`` is given, ``accounts`` is filtered to that account (still a
    list; empty when unknown). ``present`` is True when the ledger file parsed to
    at least one usable account record. Best-effort — never raises.
    """
    ledger = load_ledger(path)
    accounts = [a for a in (_clean_account(r) for r in ledger.get("accounts", [])) if a is not None]
    present = bool(accounts)
    if account_id is not None:
        accounts = [a for a in accounts if a["account_id"] == account_id]
    return {
        "present": present,
        "count": len(accounts),
        "account_id": account_id,
        "accounts": accounts,
        "updated_at": ledger.get("updated_at"),
    }


def upsert_account_truth(
    record: dict[str, Any],
    path: str | os.PathLike[str] | None = None,
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Insert or replace one account's truth record (keyed by ``account_id``) and
    write the ledger back. Returns the written ledger dict.

    Used by the reconciliation run (``scripts/ops/reconcile_netting_pnl.py
    --emit-ledger``) to record the authoritative figure after an operator has
    reviewed the dry-run. ``updated_at`` is passed in (the module never reads a
    wall clock — keeps it deterministic/testable).
    """
    account_id = record.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError("record must carry a non-empty 'account_id'")
    p = Path(path) if path is not None else ledger_path()
    ledger = load_ledger(p)
    others = [r for r in ledger.get("accounts", []) if not (isinstance(r, dict) and r.get("account_id") == account_id)]
    others.append(record)
    ledger["accounts"] = others
    if updated_at is not None:
        ledger["updated_at"] = updated_at
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return ledger
