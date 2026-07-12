"""Exit-lever annotate soak — M20 stale-stop rollout, phase 1 (observe-only).

The M20 evidence memo (docs/research/M20-exit-refinement-2026-07-12.md § 5)
proposes a strategy-declared conditional **stale-stop** for the donchian
family. The operator-approved rollout is annotate-first: while a strategy's
YAML does NOT declare ``stale_exit_bars``, the monitor evaluates the lever at
the proposed reference parameters and — when it *would* have fired — writes
one observe-only row here instead of closing anything. Once the YAML declares
the params, the same check switches from annotate to a real ``stale_stop``
close for that strategy only.

Mirrors ``exit_ladder_soak`` / ``conviction_sizing``: a pure record builder
(never raises → ``None``) + a best-effort append-only writer to
``runtime_logs/exit_lever_soak.jsonl``. **Nothing reads it back** — the soak
is the evidence trail for flipping the YAML (Tier-3), not an input to any
decision. Deduped in-process per (order_package_id, lever) so the persistent
would-fire condition logs once per trade per process lifetime (a restart may
re-log once — harmless for an audit log).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "exit_lever_soak.jsonl"

# In-process dedup: one annotate row per (order_package_id, lever).
_ANNOTATED: set = set()


def soak_log_path():
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / SOAK_LOG_NAME


def record_exit_lever_annotation(
    *,
    lever: str,
    strategy: str,
    symbol: str,
    direction: str,
    order_package_id: Any = None,
    params: Optional[Dict[str, Any]] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Append one observe-only "lever would exit here" row (best-effort).

    ``params`` are the reference lever parameters evaluated; ``state`` is the
    decision-time context (age_bars, open_r, price, entry). Returns the record,
    or ``None`` when deduped / unwritable. **Never raises.**
    """
    try:
        key = (str(order_package_id or ""), str(lever))
        if key in _ANNOTATED:
            return None
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "lever": str(lever),
            "mode": "annotate",
            "strategy": str(strategy or ""),
            "symbol": str(symbol or ""),
            "direction": str(direction or ""),
            "order_package_id": order_package_id,
            "params": params or {},
            "state": state or {},
        }
        path = soak_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        _ANNOTATED.add(key)
        logger.debug(
            "exit_lever_soak(annotate) lever=%s strategy=%s pkg=%s (unchanged)",
            lever, strategy, order_package_id,
        )
        return record
    except Exception:  # noqa: BLE001 — observe-only soak must never crash monitor()
        return None
