"""Paper-record bucketing — separate gradeable strategy round-trips from
technical artifacts and broker-truncated-but-reconstructable trades.

Why
---
Per-strategy performance (win-rate / expectancy) is distorted by closed/rejected
records that are NOT clean strategy round-trips: intent-layer reduce/flip legs,
netting-guard / hold-policy suppressions, reconciler closes with no classifiable
bracket reason, orphan/re-adopt flaps, and credential/funding refusals. Blending
those into the scorecard makes every aggregate wrong (see
``docs/audits/order-packages-zero-qty-2026-06-26.md`` § Follow-up).

This module is a **pure, stdlib-only classifier** (no DB, no network, no pandas)
so it is trivially testable and safe to import from the ``performance-review``
skill. It assigns each record to one of three buckets:

- ``A`` GRADEABLE — opened and reached a genuine SL/TP (or strategy ``monitor()``)
  exit; the realized PnL is trustworthy. These are the ONLY rows that should drive
  per-strategy win-rate / expectancy.
- ``B`` TECHNICAL ARTIFACT — intent reduce/flip legs, netting-guard / hold-policy
  suppressions, unclassified reconciler closes that are reduce legs, orphan/flap
  rows, smoke tests, credential/funding refusals. Excluded from the strategy
  scorecard; routed to the technical/health backlog instead.
- ``C`` RECONSTRUCTABLE — a full position the broker/reconciler/stuck-watchdog
  closed mid-flight (or one still open at the window edge) where we DO have
  entry+SL+TP. The live exit is an artifact, but the *decision* is still gradeable
  and the would-be outcome can be reconstructed from candles (see
  ``trade_reconstruction.py``).

Record shape
------------
The classifier accepts the ``trade_journal.db::trades`` row dict shape returned by
``/api/diag/journal?table=trades`` and ``/api/bot/trades/closed`` — fields used:
``status``, ``exit_reason``, ``setup_type``, ``reconcile_status``, ``is_backtest``,
``is_demo``/``account_class``, ``entry_price``, ``stop_loss``,
``take_profit_1``/``take_profit``, ``direction`` and the ``notes`` blob
(``intent_reduce`` / ``intent_action`` / ``reason`` / ``is_test``). Unknown/missing
fields degrade safely — a row that can't be classified lands in ``B`` with category
``unclassified`` rather than silently polluting bucket A.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


# Genuine bracket / strategy exits — a trustworthy, gradeable outcome. Beyond
# the bracket hits (sl/tp/sl_cross/tp_cross), this also covers the strategy's
# own monitor()-driven exits: `exit_head` (the M20 ML exit-head early-close,
# src/units/strategies/trend_donchian.py::_exit_head_verdict) and `stale_stop`
# (the M20 stale-stop lever). Both are deliberate, in-strategy exit decisions
# with a trustworthy realized PnL — NOT a technical artifact — so a row
# closing this way belongs in bucket A same as a bracket hit
# (BL-20260722-CLASSIFIER-MISSING-CLEAN-EXITS: a real trend_donchian
# exit_head close was misbucketed `unclassified` before this fix, understating
# gradeable coverage). Other exit-reason literals seen in the codebase
# (`time_decay`, `timeout`, `options_expiry_assignment`, ...) were NOT added
# here without individually verifying each is a genuine, trustworthy
# strategy-driven close rather than a different kind of artifact — see the
# performance-review-backlog follow-up opened alongside this fix.
_CLEAN_EXIT_REASONS = frozenset({"sl", "tp", "sl_cross", "tp_cross", "exit_head", "stale_stop"})

# Reconciler / watchdog closes that are NOT a classified bracket hit. A FULL
# position closed here (and not a reduce/orphan) is broker-truncated → bucket C.
_TRUNCATING_EXIT_REASONS = frozenset({
    "reconciler_filled", "reconciler", "stuck_strategy_watchdog",
})

# Closes that are themselves a technical artifact regardless of bracket levels.
_ARTIFACT_EXIT_REASONS = frozenset({
    "adopted_orphan_disappeared", "exit_coverage_no_strategy",
    "exchange_flat_reconciled",
})

# Rejection-reason substrings (in notes.reason) that mark a decision/plumbing
# row that never opened a real position → bucket B.
_REFUSAL_MARKERS = (
    "zero_balance", "sizing_failed", "below_venue_min_qty", "dry_run_sizing_skip",
    "reentry_suppressed_netting_guard", "flip_suppressed_hold_policy",
    "intent_noop", "dry_run_no_order_placed", "hold_to_bracket_reduce",
    "exchange_client_unavailable", "account_mode_dry_run", "unsupported exchange",
)


@dataclass(frozen=True)
class ClassifiedRecord:
    """One record's bucket verdict."""
    trade_id: Any
    strategy: Optional[str]
    symbol: Optional[str]
    account_id: Optional[str]
    account_class: Optional[str]
    status: Optional[str]
    exit_reason: Optional[str]
    bucket: str                 # 'A' | 'B' | 'C'
    category: str               # fine-grained sub-reason
    gradeable: bool             # bucket A
    reconstructable: bool       # bucket C
    reason: str                 # human-readable one-liner
    pnl: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)


def _decode_notes(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _is_paper(rec: Dict[str, Any]) -> bool:
    ac = str(rec.get("account_class") or "").lower()
    if ac:
        return ac == "paper"
    return bool(rec.get("is_demo"))


def _has_bracket(rec: Dict[str, Any]) -> bool:
    entry = rec.get("entry_price")
    sl = rec.get("stop_loss")
    tp = rec.get("take_profit_1") if rec.get("take_profit_1") is not None else rec.get("take_profit")
    try:
        return (
            entry is not None and sl is not None and tp is not None
            and float(entry) > 0 and float(sl) > 0 and float(tp) > 0
        )
    except (TypeError, ValueError):
        return False


def _str(v: Any) -> str:
    return str(v).strip().lower() if v is not None else ""


def classify_record(rec: Dict[str, Any]) -> ClassifiedRecord:
    """Classify a single trade-journal record into bucket A / B / C.

    Precedence (first match wins): backtest/smoke → orphan/flap → refusal →
    intent reduce/flip → clean SL/TP (A) → truncating reconciler close (C) →
    open-at-window-edge (C) → unclassified (B).
    """
    notes = _decode_notes(rec.get("notes"))
    status = _str(rec.get("status"))
    exit_reason = _str(rec.get("exit_reason"))
    setup_type = _str(rec.get("setup_type"))
    reconcile_status = _str(rec.get("reconcile_status"))
    reason_str = _str(notes.get("reason"))
    intent_action = _str(notes.get("intent_action"))

    def out(bucket, category, gradeable, reconstructable, reason):
        return ClassifiedRecord(
            trade_id=rec.get("id"),
            strategy=rec.get("strategy_name"),
            symbol=rec.get("symbol"),
            account_id=rec.get("account_id"),
            account_class=rec.get("account_class"),
            status=rec.get("status"),
            exit_reason=rec.get("exit_reason"),
            bucket=bucket, category=category,
            gradeable=gradeable, reconstructable=reconstructable,
            reason=reason,
            pnl=rec.get("pnl"),
            meta={
                "setup_type": rec.get("setup_type"),
                "reconcile_status": rec.get("reconcile_status"),
                "intent_action": notes.get("intent_action"),
                "is_paper": _is_paper(rec),
            },
        )

    # 0. Backtest / smoke-test rows are never strategy-live records.
    if rec.get("is_backtest") or setup_type == "smoke_test" or notes.get("is_test"):
        return out("B", "backtest_or_smoke", False, False,
                   "backtest/smoke-test row — not a live strategy trade")

    # 1. Orphan-adopt / re-adopt flap and reconcile artifacts.
    if (
        setup_type == "adopted_orphan"
        or reconcile_status in ("superseded", "unreconciled")
        or exit_reason in _ARTIFACT_EXIT_REASONS
    ):
        return out("B", "orphan_or_flap", False, False,
                   f"orphan/reconcile artifact (setup_type={setup_type!r} "
                   f"reconcile_status={reconcile_status!r} exit={exit_reason!r})")

    # 2. Decision/plumbing refusal — never opened a real position.
    if status == "rejected" or any(m in reason_str for m in _REFUSAL_MARKERS):
        cat = next((m for m in _REFUSAL_MARKERS if m in reason_str), "rejected")
        return out("B", f"refusal:{cat}", False, False,
                   f"decision/plumbing refusal — no position opened ({cat})")

    # 3. Intent-layer reduce / flip leg — a partial position adjustment, not a
    #    strategy round-trip (even when it crossed a bracket).
    if (
        notes.get("intent_reduce")
        or setup_type == "intent_reduce"
        or intent_action in ("reduce", "flip")
    ):
        return out("B", "intent_reduce_or_flip", False, False,
                   f"intent-layer {intent_action or 'reduce'} leg — partial "
                   "position adjustment, not a gradeable round-trip")

    # 4. GRADEABLE — opened and reached a genuine SL/TP exit.
    if status == "closed" and exit_reason in _CLEAN_EXIT_REASONS:
        return out("A", f"clean_exit:{exit_reason}", True, False,
                   f"clean {exit_reason} exit — realized PnL is gradeable")

    # 5. RECONSTRUCTABLE — broker/reconciler/watchdog truncated a full position,
    #    but we have entry+SL+TP to reconstruct the would-be outcome.
    if status == "closed" and exit_reason in _TRUNCATING_EXIT_REASONS:
        if _has_bracket(rec):
            return out("C", f"truncated:{exit_reason}", False, True,
                       f"broker-truncated close ({exit_reason}) with bracket — "
                       "reconstruct would-be SL/TP outcome")
        return out("B", f"truncated_no_bracket:{exit_reason}", False, False,
                   f"truncated close ({exit_reason}) without usable bracket")

    # 6. Still open at the window edge — reconstructable if it has a bracket.
    if status == "open":
        if _has_bracket(rec):
            return out("C", "open_at_window_edge", False, True,
                       "open at window edge with bracket — reconstruct progress")
        return out("B", "open_no_bracket", False, False,
                   "open without usable bracket")

    # 7. Fallback — a closed row with an unrecognised exit reason. Prefer
    #    reconstruction when a bracket exists rather than silently grading an
    #    unknown exit.
    if status == "closed" and _has_bracket(rec):
        return out("C", f"unknown_exit:{exit_reason or 'none'}", False, True,
                   f"closed with unrecognised exit ({exit_reason!r}) — reconstruct")
    return out("B", f"unclassified:{exit_reason or status or 'none'}", False, False,
               "unclassified — excluded from strategy scorecard by default")


def classify_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify a batch and return ``{classified:[...], summary:{...}}``.

    ``summary`` carries per-bucket counts, a per-category breakdown, and a
    per-strategy split of bucket A vs B vs C so a reviewer sees at a glance how
    much of each strategy's record set is gradeable.
    """
    classified: List[ClassifiedRecord] = [classify_record(r) for r in (records or [])]
    by_bucket: Dict[str, int] = {"A": 0, "B": 0, "C": 0}
    by_category: Dict[str, int] = {}
    by_strategy: Dict[str, Dict[str, int]] = {}
    for c in classified:
        by_bucket[c.bucket] = by_bucket.get(c.bucket, 0) + 1
        by_category[c.category] = by_category.get(c.category, 0) + 1
        s = c.strategy or "(unknown)"
        by_strategy.setdefault(s, {"A": 0, "B": 0, "C": 0})
        by_strategy[s][c.bucket] += 1
    total = len(classified)
    return {
        "classified": classified,
        "summary": {
            "total": total,
            "by_bucket": by_bucket,
            "gradeable_pct": round(100.0 * by_bucket["A"] / total, 1) if total else 0.0,
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "by_strategy": by_strategy,
        },
    }
