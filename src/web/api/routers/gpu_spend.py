"""GPU-burst spend surface — ``GET /api/bot/gpu/spend`` (M19 Tier-1).

Read-only Tier-1 view over the committed ``comms/gpu_spend_ledger.json`` so the
dashboard can show **per-training-session cost + the running monthly total vs the
$10 cap**. The sibling of the M13 ``/api/bot/insights/usage`` LLM-spend surface.
Best-effort: a missing/garbled ledger returns a zeroed envelope, never a 5xx.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/gpu", tags=["gpu"])


@router.get("/spend")
def get_gpu_spend() -> dict[str, Any]:
    """Month-to-date + per-run GPU-burst spend from the committed ledger.

    Shape: ``{present, provider, currency, budget_usd_per_month, current_month,
    current_month_usd, current_month_runs, budget_remaining_usd, over_budget,
    lifetime_usd, run_count, by_month:[{month,usd,runs}], runs:[{run_id, started_at,
    ended_at, experiment, gpu_type, gpu_hours, rate_usd_per_hour, cost_usd, status,
    month, cumulative_month_usd}]}`` — runs newest-first.
    """
    try:
        from src.runtime import gpu_spend as spend_mod
    except Exception as exc:  # pragma: no cover - defensive import
        logger.warning("gpu_spend: module not importable: %s", exc)
        return {"present": False, "error": "gpu_spend_unavailable", "runs": []}

    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        return spend_mod.summarize_spend(current_month=current_month)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("gpu_spend: summarize failed: %s", exc)
        return {"present": False, "error": "gpu_spend_error", "runs": []}
