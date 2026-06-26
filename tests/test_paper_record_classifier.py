"""Tests for src.analysis.paper_record_classifier — bucket A/B/C taxonomy.

Anchored on the real records pulled in the 2026-06-26 zero-qty investigation
(diag #4654): intent_reduce reconciler closes, netting-guard / hold-policy
suppressions, zero_balance refusals, and a clean bracket exit.
"""
import json

from src.analysis.paper_record_classifier import classify_record, classify_records


def _rec(**kw):
    base = {
        "id": 1, "strategy_name": "sol_pullback_2h", "symbol": "SOLUSDT",
        "account_id": "bybit_1", "account_class": "paper", "direction": "long",
        "entry_price": 67.19, "stop_loss": 64.0, "take_profit_1": 72.0,
        "status": "closed", "exit_reason": "tp", "setup_type": None,
        "reconcile_status": None, "is_backtest": 0, "is_demo": 1, "notes": "{}",
        "pnl": 10.0,
    }
    base.update(kw)
    return base


def test_clean_tp_is_bucket_A_gradeable():
    c = classify_record(_rec(exit_reason="tp"))
    assert c.bucket == "A" and c.gradeable and not c.reconstructable
    c2 = classify_record(_rec(exit_reason="sl", pnl=-5.0))
    assert c2.bucket == "A" and c2.gradeable


def test_intent_reduce_leg_is_bucket_B():
    # The real paper SOL +141 record: intent_reduce reconciler_filled.
    notes = json.dumps({"intent_reduce": True, "intent_action": "reduce",
                        "intent_current_qty": -382.2})
    c = classify_record(_rec(exit_reason="reconciler_filled", notes=notes, pnl=141.375))
    assert c.bucket == "B" and c.category == "intent_reduce_or_flip"
    assert not c.gradeable and not c.reconstructable


def test_intent_reduce_even_on_bracket_exit_is_B():
    notes = json.dumps({"intent_reduce": True, "intent_action": "reduce"})
    c = classify_record(_rec(exit_reason="tp", notes=notes))
    assert c.bucket == "B"  # reduce precedes the clean-exit check


def test_zero_balance_refusal_is_bucket_B():
    notes = json.dumps({"reason": "zero_balance: gate_balance=0.00 USD (no funds available to size against)"})
    c = classify_record(_rec(status="rejected", exit_reason=None, notes=notes, pnl=None))
    assert c.bucket == "B" and "refusal" in c.category


def test_netting_and_hold_suppressions_are_bucket_B():
    for reason in ("reentry_suppressed_netting_guard:increase",
                   "intent_noop:flip_suppressed_hold_policy: desired short opposes current long"):
        c = classify_record(_rec(status="rejected", exit_reason=None,
                                 notes=json.dumps({"reason": reason})))
        assert c.bucket == "B"


def test_orphan_and_flap_are_bucket_B():
    assert classify_record(_rec(setup_type="adopted_orphan")).bucket == "B"
    assert classify_record(_rec(reconcile_status="superseded")).bucket == "B"
    assert classify_record(_rec(exit_reason="exchange_flat_reconciled")).bucket == "B"


def test_backtest_and_smoke_are_bucket_B():
    assert classify_record(_rec(is_backtest=1)).bucket == "B"
    assert classify_record(_rec(setup_type="smoke_test")).bucket == "B"


def test_truncated_full_position_with_bracket_is_bucket_C():
    # A reconciler_filled FULL position (not a reduce) with entry+sl+tp present.
    c = classify_record(_rec(exit_reason="reconciler_filled", notes="{}"))
    assert c.bucket == "C" and c.reconstructable and not c.gradeable


def test_truncated_without_bracket_falls_to_B():
    c = classify_record(_rec(exit_reason="reconciler_filled", notes="{}",
                             entry_price=None, stop_loss=None, take_profit_1=None))
    assert c.bucket == "B"


def test_open_at_window_edge_with_bracket_is_C():
    c = classify_record(_rec(status="open", exit_reason=None))
    assert c.bucket == "C" and c.reconstructable


def test_summary_rollup():
    recs = [
        _rec(id=1, exit_reason="tp"),                                   # A
        _rec(id=2, exit_reason="reconciler_filled",
             notes=json.dumps({"intent_reduce": True})),               # B
        _rec(id=3, exit_reason="reconciler_filled", notes="{}"),       # C
    ]
    out = classify_records(recs)
    s = out["summary"]
    assert s["total"] == 3
    assert s["by_bucket"] == {"A": 1, "B": 1, "C": 1}
    assert s["gradeable_pct"] == 33.3
    assert s["by_strategy"]["sol_pullback_2h"] == {"A": 1, "B": 1, "C": 1}
