"""Allocator shadow-soak — M18 P0c (portfolio capital allocator).

**Observe-only — changes nothing the order path does.** Each tick the intent
multiplexer surfaces the FULL candidate set (M18 P0b, ``candidate_signal_packages``
= every strategy's actionable intent before ``aggregate_intents`` collapses them
to one). This soak records, for each tick where a genuine *choice* exists
(≥ 2 candidates), what a capital allocator **would** pick (the top-ranked
candidate) next to what the system **actually** routed (the aggregator's winner),
and the **regret** between them — "did we leave EV on the table by routing a
worse candidate than was available?".

Mirrors the conviction-sizing / exit-ladder soaks: a pure
``build_allocator_soak_record`` (never raises → ``None`` on anything
un-derivable) + a best-effort append-only writer to
``runtime_logs/allocator_soak.jsonl`` + a pure ``read_soak_records`` for the
``/api/bot/allocator/soak`` read surface.

**Nothing reads it back** — routing is unchanged. The ranking score is
**pluggable** (``score_fn``): P0c ships the strategy-**confidence proxy**; M18 P1
swaps in the cost-aware EV scorer (``allocator_ev``) without touching this
harness. Graduating the allocator to actually *select* the subset is the
behaviour-changing, backtest-gated step (M18 P2+), operator-gated — so there is
**no ``*_ENABLED`` gate** here (Prime Directive).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "allocator_soak.jsonl"

# A candidate must carry a real directional side to be ranked.
_ACTIONABLE_SIDES = ("long", "short")


def _candidate_score(candidate: Any) -> float:
    """Default P0c ranking score: the strategy confidence proxy.

    Reads ``source_context['confidence']`` off the ``SignalPackage`` the
    multiplexer stamped (M18 P0b). M18 P1 replaces this with the cost-aware
    ``EV_net`` scorer. Pure; never raises → ``0.0`` on anything un-readable.
    """
    try:
        ctx = getattr(candidate, "source_context", None) or {}
        return float(ctx.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _brief(candidate: Any, score: float) -> Dict[str, Any]:
    """A small JSON-serialisable view of a scored candidate."""
    ctx = getattr(candidate, "source_context", None) or {}
    raw = getattr(candidate, "raw", None) or {}
    return {
        "strategy_id": getattr(candidate, "strategy_id", None),
        "symbol": getattr(candidate, "symbol", None),
        "side": getattr(candidate, "side", None),
        "entry": getattr(candidate, "entry_price", None),
        "sl": getattr(candidate, "stop_loss", None),
        "tp": getattr(candidate, "take_profit", None),
        "confidence": ctx.get("confidence"),
        "priority": ctx.get("priority"),
        "score": round(float(score), 6),
        # M18 Phase A (observe-only): the P_win entry head's score, stamped
        # by the signal builder when a matching artifact is mirrored — the
        # side-by-side evidence Phase B's candidate_p_win swap is gated on.
        # None until the head covers this candidate's family/tf.
        "head_p_win": raw.get("head_p_win"),
        "head_p_win_model": raw.get("head_p_win_model"),
    }


def build_allocator_soak_record(
    candidates: Sequence[Any],
    *,
    symbol: str,
    executed_strategy_id: Optional[str],
    executed_side: Optional[str] = None,
    score_fn: Callable[[Any], float] = _candidate_score,
    score_kind: str = "confidence_proxy",
) -> Optional[Dict[str, Any]]:
    """Build the observe-only soak record: would-pick vs executed + regret.

    ``candidates`` are the per-tick ``SignalPackage``s (M18 P0b). Only the
    actionable (``long``/``short``) ones are ranked. Returns ``None`` (no row)
    unless there are **≥ 2** actionable candidates — i.e. a genuine choice the
    allocator would make; a single-candidate tick has no allocation decision.

    The allocator's choice is ``argmax(score_fn)`` (ties broken by the candidate's
    ``priority`` then stable order). ``regret_score = top_score − executed_score``
    (``≥ 0``); ``executed_score`` is the score of the candidate the aggregator
    actually routed (``executed_strategy_id``), or ``None`` when the system routed
    nothing / something outside the candidate set (then regret = the full top
    score — a candidate existed and we took none of it). Pure; **never raises.**
    """
    try:
        actionable = [
            c for c in (candidates or [])
            if str(getattr(c, "side", "")).lower() in _ACTIONABLE_SIDES
        ]
        if len(actionable) < 2:
            return None

        scored = [(c, float(score_fn(c))) for c in actionable]
        # Rank: highest score first; tie → higher priority; then stable.
        def _rank_key(item):
            c, s = item
            ctx = getattr(c, "source_context", None) or {}
            try:
                prio = float(ctx.get("priority") or 0.0)
            except (TypeError, ValueError):
                prio = 0.0
            return (s, prio)

        ranked = sorted(scored, key=_rank_key, reverse=True)
        top_c, top_score = ranked[0]

        executed_score: Optional[float] = None
        if executed_strategy_id:
            for c, s in scored:
                if getattr(c, "strategy_id", None) == executed_strategy_id:
                    executed_score = s
                    break

        if executed_score is None:
            regret = top_score
            agree = False
        else:
            regret = top_score - executed_score
            agree = getattr(top_c, "strategy_id", None) == executed_strategy_id

        return {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "score_kind": score_kind,
            "n_candidates": len(actionable),
            "executed_strategy_id": executed_strategy_id,
            "executed_side": executed_side,
            "executed_score": (round(executed_score, 6) if executed_score is not None else None),
            "allocator_choice": _brief(top_c, top_score),
            "top_score": round(top_score, 6),
            "agree": agree,
            "regret_score": round(max(0.0, regret), 6),
            "candidates": [_brief(c, s) for c, s in ranked],
        }
    except Exception:  # noqa: BLE001 — observe-only; a soak build must never break a tick
        logger.debug("build_allocator_soak_record: skipped (un-derivable)", exc_info=False)
        return None


def record_allocator_soak(
    candidates: Sequence[Any],
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Build + append the soak record (best-effort). Returns the record or ``None``.

    Never raises — a soak-log write failure must never lose/strand a signal.
    Accepts the same keyword args as :func:`build_allocator_soak_record`.
    """
    record = build_allocator_soak_record(candidates, **kwargs)
    if record is None:
        return None
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / SOAK_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        logger.debug(
            "allocator_soak(observe) symbol=%s n=%d agree=%s regret=%.4f (unchanged)",
            record.get("symbol"), record.get("n_candidates"),
            record.get("agree"), record.get("regret_score"),
        )
    except OSError as exc:
        logger.warning("record_allocator_soak: could not write soak log: %s", exc)
    return record


def soak_log_path():
    """Resolve the soak-log path under the canonical runtime-logs dir."""
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / SOAK_LOG_NAME


def read_soak_records(
    *,
    limit: int = 100,
    symbol: Optional[str] = None,
    only_regret: bool = False,
) -> Dict[str, Any]:
    """Read newest-first soak records + a small aggregate summary.

    Pure read path backing ``/api/bot/allocator/soak``. Filters (all optional):
    ``symbol`` and ``only_regret`` (rows where the allocator would have picked a
    different, higher-scored candidate than the system routed). ``limit`` caps
    the returned rows after filtering. **Never raises** — returns a well-formed
    envelope (``present:false`` before the writer's first row, ``error`` on a
    read failure).

    The ``summary`` aggregates over **all** rows scanned: tick count, how many
    disagree (allocator ≠ executed), the disagreement %, and the mean regret —
    the headline "is the per-cell routing leaving EV on the table vs ranking the
    full opportunity set?".
    """
    path = soak_log_path()
    if not path.exists():
        return {"present": False, "log_path": str(path), "count": 0,
                "records": [], "summary": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("read_soak_records: could not read %s — %s", path, exc)
        return {"present": True, "log_path": str(path), "count": 0,
                "records": [], "summary": {}, "error": str(exc)}

    s_filter = str(symbol) if symbol else None
    total = 0
    disagree = 0
    regret_sum = 0.0
    records: List[Dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if s_filter is not None and str(rec.get("symbol", "")) != s_filter:
            continue
        not_agree = not bool(rec.get("agree"))
        if only_regret and not not_agree:
            continue
        total += 1
        if not_agree:
            disagree += 1
        try:
            regret_sum += float(rec.get("regret_score") or 0.0)
        except (TypeError, ValueError):
            pass
        if len(records) < limit:
            records.append(rec)

    return {
        "present": True,
        "log_path": str(path),
        "count": len(records),
        "records": records,
        "summary": {
            "total_scanned": total,
            "disagree": disagree,
            "disagree_pct": round(100.0 * disagree / total, 1) if total else 0.0,
            "mean_regret": round(regret_sum / total, 6) if total else 0.0,
        },
    }
