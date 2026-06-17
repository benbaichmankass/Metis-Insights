"""Exit-ladder soak — P3 of the dynamic-take-profit consistency feature.

**Observe-only — changes nothing the order/ticket path does.** This is the
venue-agnostic soak that accrues, for every executed order, the **laddered**
exit that *would* be used (the materialized ExitPlan from P1/P2, sized against
the order's real qty) next to the **single target** actually placed:

- **API accounts** (Bybit/IBKR/Alpaca/OANDA) — these trade live now, so the soak
  fills immediately. The broker bracket gets a single SL/TP at entry; the record
  shows the multi-rung ladder (bank-partial-then-run) the strategy's ExitPlan
  implies, so we can compare distributions on real fills.
- **Prop (Breakout)** — the assistant gets a single-target bracket ticket; the
  record shows the laddered ticket that would be sent. (Inert until the prop
  account trades live.)

**Nothing reads it back** — the order/ticket placed is unchanged. Graduating the
ladder to the *actual* exit is the behaviour-changing, backtest-gated step (P4
API / P3-live prop) — a deliberate change to the exit path, not the flip of a
dormant switch, so there is **no ``*_ENABLED`` gate** here (Prime Directive).

Mirrors the conviction-sizing soak (``src/runtime/conviction_sizing.py``): a pure
``build_exit_ladder_record`` (never raises → ``None`` on anything un-derivable)
plus a best-effort append-only writer to ``runtime_logs/exit_ladder_soak.jsonl``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "exit_ladder_soak.jsonl"


def build_exit_ladder_record(
    *,
    venue: str,
    strategy: str,
    symbol: str,
    direction: str,
    entry: Any,
    sl: Any,
    tp: Any,
    qty: Any,
    account_id: str = "",
    account_class: str = "",
    timeframe: str = "",
    order_meta: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build the observe-only soak record comparing single-target vs ladder.

    ``venue`` is ``"api"`` (live broker order) or ``"prop"`` (manual ticket).
    ``qty`` is the real sized order qty (API: the placed qty; prop: the ticket's
    ``qty_units``) — the materialized ladder splits it across rungs. ``order_meta``
    carries the package ``meta`` (e.g. ``tp2`` for a turtle-style TP1→TP2 ladder)
    so the derived ExitPlan reflects the real exit structure. ``extra`` adds
    venue-specific fields onto the ``single_target`` block (e.g. prop side/rr).

    Returns a JSON-serialisable record, or ``None`` when the ladder can't be
    derived. Pure; **never raises.**
    """
    try:
        from src.runtime.exit_plan import build_exit_plan_from_legacy
        from src.runtime.exit_plan_materializer import materialize_exit_plan

        e = float(entry or 0.0)
        s = float(sl or 0.0)
        t = float(tp or 0.0)
        q = float(qty or 0.0)
        if e <= 0 or s <= 0 or t <= 0 or q <= 0:
            return None

        exit_plan = build_exit_plan_from_legacy({
            "strategy_name": str(strategy or "strategy"),
            "entry": e, "sl": s, "tp": t,
            "meta": order_meta or {},
        })
        if exit_plan is None:
            return None

        materialized = materialize_exit_plan(
            exit_plan, direction=direction, entry=e, stop=s, qty_total=q,
        )
        if materialized is None:
            return None

        targets = materialized.get("targets") or []
        n_rungs = sum(1 for tg in targets if tg.get("kind") == "rung")
        single_target: Dict[str, Any] = {"entry": e, "sl": s, "tp": t, "qty": q}
        if isinstance(extra, dict):
            single_target.update(extra)
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "venue": str(venue or ""),
            "account_id": str(account_id or ""),
            "account_class": str(account_class or ""),
            "strategy": str(strategy or ""),
            "symbol": str(symbol or ""),
            "direction": str(direction or ""),
            "timeframe": str(timeframe or ""),
            "single_target": single_target,
            "ladder": {
                "n_rungs": n_rungs,
                "n_targets": len(targets),
                "targets": targets,
                "final_trailing": materialized.get("final_trailing"),
                "stop": materialized.get("stop"),
                "residual_qty": materialized.get("residual_qty"),
                "realism_notes": materialized.get("realism_notes") or [],
            },
            "differs_from_single_target": n_rungs > 0 or bool(materialized.get("final_trailing")),
        }
    except Exception:  # noqa: BLE001 — observe-only soak must never crash the path
        return None


def record_exit_ladder_soak(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Build + append the soak record (best-effort). Returns the record or ``None``.

    Never raises — a soak-log write failure must never lose the order/ticket.
    Accepts the same keyword args as :func:`build_exit_ladder_record`.
    """
    record = build_exit_ladder_record(**kwargs)
    if record is None:
        return None
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / SOAK_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        logger.debug(
            "exit_ladder_soak(observe) venue=%s account=%s symbol=%s n_rungs=%s (unchanged)",
            record.get("venue"), record.get("account_id"), record.get("symbol"),
            record["ladder"]["n_rungs"],
        )
    except OSError as exc:
        logger.warning("record_exit_ladder_soak: could not write soak log: %s", exc)
    return record
