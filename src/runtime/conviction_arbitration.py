"""Conviction-driven competing-trade arbitration — P3 of the unified-confidence
risk redesign (design § 3.4).

**Advisory / observe-only — no gate.** When `aggregate_intents` resolves a
symbol's competing strategy intents — a same-direction *reinforcement* (today:
`max(target_qty)` wins) or a long-vs-short *conflict* (today: highest
`effective_priority()` wins) — this computes what **conviction-based**
arbitration *would* have decided (higher-conviction intent wins the conflict;
conviction-weighted reinforcement target instead of plain max-qty) and logs the
comparison to a soak log. It **never changes the aggregator's decision** — it is
the exact analogue of the P1 `meta.conviction` stamp and the P2
`conviction_sizing` annotator: accrue the evidence (would-be vs actual) so the
distribution can be reviewed before conviction arbitration graduates to actually
driving the pick.

There is deliberately **no on/off flag** (no `*_MODE`, no `*_ENABLED`, no
allowlist) — a default-off gate in front of an observe-only annotator is the
stranding trap the Prime Directive / design § 8 forbid (the same reason the P2
gate was removed). It mirrors the regime router's `_shadow_regime_gate`
observe-half, but without even an enforced sibling yet: graduation to actually
arbitrating by conviction is a future deliberate change to `aggregate_intents`
itself, governed by the normal Tier-3 PR gate.

Conviction signal: `StrategyIntent.confidence` (intents.py — "the hook already
exists, currently ignored", design § 3.4). The calibrated multi-lens conviction
blend (P1) is stamped on the order *package* downstream of aggregation, so at
the intent-aggregation stage the per-intent `confidence` is the available
conviction proxy; when the conviction lens is fully wired this annotator's input
is swapped without changing its observe-only contract.

Fail-permissive: any error → nothing logged, nothing changed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Sequence

logger = logging.getLogger(__name__)


def _confidence(intent: Any) -> float:
    try:
        return float(getattr(intent, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _conflict_conviction_winner(intents: Sequence[Any]) -> Any:
    """The intent conviction-arbitration WOULD pick on a conflict: highest
    ``confidence``, with the SAME deterministic tiebreakers the priority
    resolver uses (earliest timestamp, then strategy name) so the would-be pick
    is reproducible."""
    return min(
        intents,
        key=lambda i: (
            -_confidence(i),
            getattr(i, "timestamp", 0.0),
            str(getattr(i, "strategy", "")).lower(),
        ),
    )


def compute_conviction_arbitration(
    non_flat_intents: Sequence[Any],
    *,
    resolution: str,
    actual_winner_strategy: str,
    actual_target_qty: float,
) -> dict | None:
    """Resolve the would-be conviction-arbitrated outcome for an already-decided
    aggregation. Returns the soak record, or ``None`` when there's nothing
    meaningful to compare (fewer than 2 intents, or no positive conviction).
    Pure — never raises, never mutates.

    *resolution* is the aggregator's branch tag: ``"same_direction"`` (reinforce)
    or ``"priority_conflict"``.
    """
    intents = [i for i in non_flat_intents]
    if len(intents) < 2:
        return None  # a single intent is its own winner — no arbitration happened
    if not any(_confidence(i) > 0.0 for i in intents):
        return None  # no conviction signal to arbitrate on (all 0.0)

    per_intent = [
        {
            "strategy": str(getattr(i, "strategy", "") or ""),
            "side": str(getattr(i, "side", "") or ""),
            "confidence": _confidence(i),
            "priority": int(i.effective_priority())
            if hasattr(i, "effective_priority") else None,
            "target_qty": float(getattr(i, "target_qty", 0.0) or 0.0),
        }
        for i in intents
    ]

    if resolution == "priority_conflict":
        conv_winner = _conflict_conviction_winner(intents)
        conv_winner_strategy = str(getattr(conv_winner, "strategy", "") or "")
        agrees = conv_winner_strategy == actual_winner_strategy
        return {
            "resolution": resolution,
            "actual_winner": actual_winner_strategy,
            "conviction_winner": conv_winner_strategy,
            "conviction_winner_side": str(getattr(conv_winner, "side", "") or ""),
            "conviction_winner_confidence": _confidence(conv_winner),
            "agrees_with_actual": agrees,
            "per_intent": per_intent,
        }

    # same_direction reinforcement — today max(target_qty) picks the kept target.
    # Conviction would (a) pick by confidence, and (b) offer a conviction-weighted
    # blended target as the "weight by conviction instead of max" alternative.
    conv_winner = max(
        intents,
        key=lambda i: (
            _confidence(i),
            float(getattr(i, "target_qty", 0.0) or 0.0),
            getattr(i, "timestamp", 0.0),
        ),
    )
    conv_winner_strategy = str(getattr(conv_winner, "strategy", "") or "")
    conf_sum = sum(_confidence(i) for i in intents)
    weighted_target = (
        sum(_confidence(i) * float(getattr(i, "target_qty", 0.0) or 0.0)
            for i in intents) / conf_sum
        if conf_sum > 0 else None
    )
    return {
        "resolution": resolution,
        "actual_winner": actual_winner_strategy,
        "actual_target_qty": actual_target_qty,
        "conviction_winner": conv_winner_strategy,
        "conviction_winner_confidence": _confidence(conv_winner),
        "conviction_winner_target_qty": float(getattr(conv_winner, "target_qty", 0.0) or 0.0),
        "conviction_weighted_target_qty": weighted_target,
        "agrees_with_actual": conv_winner_strategy == actual_winner_strategy,
        "per_intent": per_intent,
    }


def annotate_conviction_arbitration(
    non_flat_intents: Sequence[Any],
    *,
    symbol: str,
    resolution: str,
    actual_winner_strategy: str,
    actual_target_qty: float,
) -> None:
    """Compute + log the would-be conviction arbitration; **never returns or
    changes anything** (advisory / observe-only).

    Runs on every multi-intent aggregation and only accrues soak evidence
    (`runtime_logs/conviction_arbitration.jsonl`). Never raises — on any error
    nothing is logged and the caller's decision is untouched.
    """
    try:
        record = compute_conviction_arbitration(
            non_flat_intents,
            resolution=resolution,
            actual_winner_strategy=actual_winner_strategy,
            actual_target_qty=actual_target_qty,
        )
        if record is None:
            return
        _log_conviction_arbitration(symbol, record)
        if not record.get("agrees_with_actual", True):
            logger.debug(
                "conviction_arbitration(observe) symbol=%s resolution=%s "
                "actual=%s conviction=%s DIFFER (decision unchanged)",
                symbol, resolution, actual_winner_strategy,
                record.get("conviction_winner"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "annotate_conviction_arbitration failed (decision unchanged): %s", exc
        )


def _log_conviction_arbitration(symbol: str, record: dict) -> None:
    """Append the would-be arbitration decision to the soak log (best-effort)."""
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / "conviction_arbitration.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            **record,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("_log_conviction_arbitration write failed: %s", exc)
