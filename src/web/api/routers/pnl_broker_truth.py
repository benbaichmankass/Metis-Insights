"""Broker-truth realized-PnL surface — ``GET /api/bot/pnl/broker-truth``.

Read-only Tier-1 view over the committed ``comms/broker_truth_ledger.json`` so the
dashboard can show an account's **authoritative** broker wallet-truth realized PnL
next to the journal's approximate per-row figure — for accounts (e.g. ``bybit_2``)
whose spot/perp/sub-account-switch history makes the live journal pnl unreliable
(BL-20260713-BYBIT2-PNL-UNDERRECORD; see
``docs/audits/bybit2-broker-reconciliation-2026-07-13.md``).

The sibling of the M19 ``/api/bot/gpu/spend`` committed-ledger surface. Best-effort:
a missing/garbled ledger returns a ``present:false`` envelope, never a 5xx.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/pnl", tags=["pnl"])


@router.get("/broker-truth")
def get_broker_truth(account_id: Optional[str] = Query(default=None)) -> dict[str, Any]:
    """Authoritative per-account realized PnL from the committed broker-truth ledger.

    Shape: ``{present, count, account_id, accounts:[{account_id, realized_usd,
    fees_usd, funding_usd, as_of, window_start, window_end, source, sub_accounts,
    note}], updated_at}``. ``account_id`` filters to one account (still a list;
    empty when unknown). Nulls where a figure isn't recorded (render as em-dash,
    never 0).
    """
    try:
        from src.runtime import broker_truth
    except Exception as exc:  # allow-silent: read endpoint must never 5xx — logs + empty envelope
        logger.warning("broker_truth: module not importable: %s", exc)
        return {"present": False, "error": "broker_truth_unavailable", "accounts": []}

    try:
        return broker_truth.summarize_broker_truth(account_id=account_id)
    except Exception as exc:  # allow-silent: read endpoint must never 5xx — logs + empty envelope
        logger.warning("broker_truth: summarize failed: %s", exc)
        return {"present": False, "error": "broker_truth_error", "accounts": []}
