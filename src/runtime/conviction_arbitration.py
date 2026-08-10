"""Conviction-driven competing-trade arbitration — P3 of the unified-confidence
risk redesign (design § 3.4).

**Advisory / observe-only — no gate.** When `aggregate_intents` resolves a
symbol's competing strategy intents — a same-direction *reinforcement* or a
long-vs-short *conflict* (today: highest `effective_priority()` wins, then
earliest timestamp, then strategy name) — this computes what **conviction-based**
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


# Why the qty half of this record is null in production
# (BL-20260810-INTENT-TARGET-QTY-ALWAYS-ZERO-TWO-CONSEQUENCES).
#
# ``StrategyIntent.target_qty`` is 0.0 for EVERY directional intent on the live
# path: ``intent_multiplexer.build_multiplexed_signal`` passes
# ``target_qty_hint=0.0`` unconditionally, and StrategyIntent's own validator
# documents 0.0-on-a-directional-side as the deliberate sentinel for "the
# per-account ``RiskManager.position_size`` decides the qty". The intent layer
# never sizes.
#
# This module used to write that sentinel out as a literal ``0.0``, so every
# qty field in the soak read as a measured zero quantity. It is not zero — it
# is NOT MEASURED HERE, and the two are opposite claims. A reader (or a future
# aggregate) cannot tell "the aggregator chose a zero target" from "this layer
# does not know the target", which is the fabricated-zero defect the exposure
# soak already avoids by emitting ``measured: false`` + a null multiple rather
# than ``0.0``.
#
# So: the qty fields are NULL with an explicit reason, and the record states
# whether any qty was measurable at all. The real per-account quantities live in
# the sibling ``conviction_sizing`` soak, which runs downstream of
# ``position_size`` and does carry them.
_QTY_UNMEASURED_REASON = (
    "not_sized_at_intent_layer: StrategyIntent.target_qty is the "
    "'RiskManager decides per-account' sentinel (always 0.0 here); real "
    "quantities are in the conviction_sizing soak, downstream of position_size"
)


def _reported_target_qty(intent: Any) -> float | None:
    """The intent's target qty for REPORTING, or ``None`` when unsized.

    Returns ``None`` for the sentinel rather than 0.0 — see the note above.
    A genuinely non-zero target (a caller that really did pre-size) is reported
    as itself, so this stays correct if the intent layer ever gains sizing.
    """
    try:
        raw = getattr(intent, "target_qty", None)
        if raw is None:
            return None
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0.0 else None


def _sort_qty(intent: Any) -> float:
    """Qty coerced to a float for ORDERING only.

    Deliberately distinct from ``_reported_target_qty``: an unsized intent must
    sort as it always has (0.0) so this change cannot alter which strategy the
    annotator names as the would-be conviction winner. Never reported.
    """
    val = _reported_target_qty(intent)
    return 0.0 if val is None else val


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
            "target_qty": _reported_target_qty(i),
        }
        for i in intents
    ]
    # Whether ANY intent on this tick carried a real (non-sentinel) qty. Read the
    # qty fields only under this flag — they are null, not zero, when it is false.
    qty_measured = any(row["target_qty"] is not None for row in per_intent)
    qty_block: dict = {"qty_measured": qty_measured}
    if not qty_measured:
        qty_block["qty_unmeasured_reason"] = _QTY_UNMEASURED_REASON

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
            **qty_block,
            "per_intent": per_intent,
        }

    # same_direction reinforcement — the aggregator's kept target is nominally
    # max(target_qty), but every live target_qty is the unsized sentinel, so that
    # key is inert and the real pick falls through to
    # effective_priority/timestamp/name.
    # See BL-20260810-INTENT-TARGET-QTY-ALWAYS-ZERO-TWO-CONSEQUENCES.
    # Conviction would (a) pick by confidence, and (b) offer a conviction-weighted
    # blended target as the "weight by conviction instead of max" alternative.
    # `_sort_qty` (not `_reported_target_qty`) is used inside the ordering key so
    # the would-be winner this annotator names is byte-for-byte what it always was.
    conv_winner = max(
        intents,
        key=lambda i: (
            _confidence(i),
            _sort_qty(i),
            getattr(i, "timestamp", 0.0),
        ),
    )
    conv_winner_strategy = str(getattr(conv_winner, "strategy", "") or "")
    conf_sum = sum(_confidence(i) for i in intents)
    # Null, not 0.0, when nothing was sized — a conviction-weighted mean of
    # sentinels is not a target of zero, it is no target at all.
    weighted_target = None
    if qty_measured and conf_sum > 0:
        weighted_target = sum(
            _confidence(i) * (_reported_target_qty(i) or 0.0) for i in intents
        ) / conf_sum
    return {
        "resolution": resolution,
        "actual_winner": actual_winner_strategy,
        "actual_target_qty": actual_target_qty,
        "conviction_winner": conv_winner_strategy,
        "conviction_winner_confidence": _confidence(conv_winner),
        "conviction_winner_target_qty": _reported_target_qty(conv_winner),
        "conviction_weighted_target_qty": weighted_target,
        "agrees_with_actual": conv_winner_strategy == actual_winner_strategy,
        **qty_block,
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
