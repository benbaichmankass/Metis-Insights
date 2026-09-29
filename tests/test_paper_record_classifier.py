"""Tests for src.analysis.paper_record_classifier — bucket A/B/C taxonomy.

Anchored on the real records pulled in the 2026-06-26 zero-qty investigation
(diag #4654): intent_reduce reconciler closes, netting-guard / hold-policy
suppressions, zero_balance refusals, and a clean bracket exit.
"""
import json
import os

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
    assert s["by_bucket"] == {"A": 1, "B": 1, "C": 1, "P": 0}
    assert s["gradeable_pct"] == 33.3
    assert s["by_strategy"]["sol_pullback_2h"] == {"A": 1, "B": 1, "C": 1, "P": 0}


# ─────────────────────────────────────────────────────────────────────────
# Bucket P — pairs sleeve bilateral management exit
# (PI-20260927-YZRZQ725-0004 / review-pack item D4)
# ─────────────────────────────────────────────────────────────────────────
#
# The classifier had no bucket for the M22 pairs sleeve's own joint
# spread-exit close (pairs_executor._close_pair, exit_reason=f"pairs_{outcome}"
# where outcome in {"revert","stop","timeout"} — src/units/strategies/
# pairs_engine.py::exit_signal — plus the half-open-cleanup path, outcome=
# "half_open_cleanup"). Depending on whether the input row happened to carry
# the executor's backstop SL/TP levels, such a row fell through to either the
# generic `unclassified` fallback (bucket B — no bracket / API-shaped row) or
# the generic `unknown_exit` fallback (bucket C — DB-native row, which DOES
# carry the backstop levels `_has_bracket` accepts as a real bracket). Either
# way it was NOT recognised as what it actually is: a deliberate, correct
# strategy-level close with a real fill and a real PnL — not a technical
# artifact, and not a broker-truncated position needing reconstruction.

def test_pairs_bilateral_revert_is_bucket_P_not_B():
    # Real shape (trimmed) of journal row id 6199 in the fixture below:
    # pairs_sol_eth_a closing pairs_revert with the executor's backstop
    # SL/TP levels present (so pre-fix this fell to bucket C, not B — see
    # the fixture-driven test below for the row that DOES fall to bucket B
    # pre-fix). This unit test additionally covers the no-bracket case
    # (API-shaped row, no stop_loss/take_profit) to show the fix is not
    # bracket-presence-dependent.
    with_bracket = classify_record(_rec(
        strategy_name="pairs_sol_eth_a", symbol="SOLUSDT",
        exit_reason="pairs_revert", entry_price=178.5, stop_loss=89.25,
        take_profit_1=357.0, notes="{}", pnl=3.21,
    ))
    assert with_bracket.bucket == "P"
    assert with_bracket.category == "pairs_bilateral_exit:pairs_revert"
    assert not with_bracket.gradeable and not with_bracket.reconstructable

    no_bracket = classify_record(_rec(
        strategy_name="pairs_bnb_btc_b", symbol="BTCUSDT",
        exit_reason="pairs_stop", entry_price=None, stop_loss=None,
        take_profit_1=None, notes="{}", pnl=0.61,
    ))
    assert no_bracket.bucket == "P"
    assert no_bracket.category == "pairs_bilateral_exit:pairs_stop"


def test_pairs_timeout_and_half_open_cleanup_are_bucket_P():
    # `pairs_timeout` and `pairs_half_open_cleanup` are the other two
    # legitimate exit_reason values the executor writes (pairs_executor.py
    # comment: "pairs_revert / pairs_stop / pairs_half_open_cleanup appear on
    # 99 trade rows"; pairs_engine.exit_signal also returns "timeout"). Match
    # is on the PREFIX, not an enumerated set, so any future pairs_* outcome
    # is covered without a follow-up edit here.
    assert classify_record(_rec(
        strategy_name="pairs_sol_eth_b", exit_reason="pairs_timeout",
        notes="{}",
    )).bucket == "P"
    assert classify_record(_rec(
        strategy_name="pairs_sol_eth_a", exit_reason="pairs_half_open_cleanup",
        notes="{}",
    )).bucket == "P"


def test_pairs_leg_reconciler_truncation_is_still_bucket_C_positive_control():
    # A pairs leg the BROKER/reconciler force-closed (not the executor's own
    # decision) does NOT match the "pairs_" exit_reason prefix
    # (`reconciler_filled` doesn't start with "pairs_") and must keep going
    # through the ordinary truncating-reconciler path — this is the real
    # journal row id 6157 in the fixture below. Positive control: proves the
    # new bucket only catches the executor's OWN close, not every close on a
    # pairs-prefixed strategy.
    c = classify_record(_rec(
        strategy_name="pairs_sol_eth_b", exit_reason="reconciler_filled",
        notes="{}",
    ))
    assert c.bucket == "C" and c.reconstructable and not c.gradeable


def test_pairs_leg_refusal_is_still_bucket_B_positive_control():
    # A pairs leg that never opened (a decision/plumbing refusal) must still
    # land in B — the pairs-P check requires status == "closed", and refusal
    # is checked (step 2) before the pairs check (step 3.5) regardless.
    notes = json.dumps({"reason": "zero_balance: gate_balance=0.00 USD"})
    c = classify_record(_rec(
        strategy_name="pairs_sol_eth_a", status="rejected", exit_reason=None,
        notes=notes, pnl=None,
    ))
    assert c.bucket == "B" and "refusal" in c.category


def test_non_pairs_unclassified_artifact_is_unchanged_bucket_B_positive_control():
    # A genuine non-pairs artifact (credential/exchange refusal on a normal
    # single-instrument strategy) must be completely unaffected by this
    # change — it never matches the "pairs_" strategy-name prefix.
    c = classify_record(_rec(
        strategy_name="ict_scalp_eth_15m", exit_reason="exchange_client_unavailable",
        status="rejected", notes=json.dumps(
            {"reason": "exchange_client_unavailable: bybit_2 credentials missing"}),
        pnl=None,
    ))
    assert c.bucket == "B" and "refusal" in c.category


# ─────────────────────────────────────────────────────────────────────────
# Real-journal population: the 2026-09-24T16:00Z–2026-09-27T07:29:44Z
# performance-review window (PR #13085 / docs/plans/review-pack-2026-09-27.md
# § D4).
#
# POPULATION, stated per RULE ONE: 52 rows, pulled 2026-09-27 via
#   scripts/ops/diag_fetch.sh 'journal?table=trades&limit=700'
# (direct HTTPS GET against https://ict-bot.duckdns.org/api/diag/journal,
# served from the live ict-bot-arm VM's trade_journal.db — NOT a constructed
# fixture), then filtered locally to
#   is_backtest != 1 AND (account_class == "paper" OR (account_class is null
#   AND is_demo == 1)) AND status == "closed" AND closed_at in
#   [2026-09-24T16:00:00Z, 2026-09-27T07:29:44Z].
# The limit=700 pull's oldest row (min closed_at across all 700 fetched rows)
# is 2026-09-06, well before the window start, so the window is NOT truncated
# by the pull's own limit. Only the fields classify_record() reads were kept
# (see tests/fixtures/perf_review_20260924_20260927_window.json); every other
# journal column was dropped, not fabricated.
#
# This 52-row count does not exactly equal the review-pack's stated "51
# closed trades" — the two counts were taken ~minutes apart against a live,
# continuously-trading system, so they are not the same snapshot; this test
# does not depend on matching 51, only on this test's own stated 52.
def _load_window_fixture():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "fixtures", "perf_review_20260924_20260927_window.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_review_window_population_is_52_rows_as_pulled():
    # States the population explicitly rather than assuming it.
    rows = _load_window_fixture()
    assert len(rows) == 52


def test_review_window_25_pairs_legs_reclassify_out_of_generic_buckets_into_P():
    rows = _load_window_fixture()
    out = classify_records(rows)
    s = out["summary"]

    # 26 rows in the window carry a pairs_* strategy_name (7 pairs_sol_eth_a +
    # 7 pairs_sol_eth_b + 6 pairs_bnb_btc_a + 6 pairs_bnb_btc_b — MEASURED
    # from this fixture, matching the review pack's "pairs_sol_eth_* /
    # pairs_bnb_btc_*" naming exactly). Of those 26, 25 are the executor's own
    # bilateral exit (bucket P) and exactly 1 (journal id 6157,
    # pairs_sol_eth_b, exit_reason=reconciler_filled) is a genuine
    # broker/reconciler truncation — the positive control that this fix does
    # not swallow every pairs-strategy close into bucket P.
    pairs_rows = [c for c in out["classified"]
                 if (c.strategy or "").startswith("pairs_")]
    assert len(pairs_rows) == 26

    pairs_p = [c for c in pairs_rows if c.bucket == "P"]
    pairs_not_p = [c for c in pairs_rows if c.bucket != "P"]
    assert len(pairs_p) == 25
    assert [c.trade_id for c in pairs_not_p] == [6157]
    assert pairs_not_p[0].bucket == "C"
    assert pairs_not_p[0].category == "truncated:reconciler_filled"

    # Bucket totals for this exact population, MEASURED with the fix applied:
    #   A=12, B=1, C=14, P=25  (total 52).
    # Before the fix (no bucket P / no pairs-aware step), these same 25 rows
    # distributed across B and C depending on bracket presence in the
    # DB-native row shape this fixture carries — reproduced by temporarily
    # reverting src/analysis/paper_record_classifier.py to origin/main and
    # re-running this exact assertion, which failed (see PR body for the
    # before/after counts and the "verified failing without the fix" note).
    assert s["by_bucket"] == {"A": 12, "B": 1, "C": 14, "P": 25}


# --- FIX-SA-03 / REVIEW-14241 round 2: the Alpaca re-arm preflight's own exits.
def test_protection_rearm_exhausted_is_truncating_bucket_C():
    notes = json.dumps({"exit_reason_source": "rearm_preflight"})
    c = classify_record(_rec(exit_reason="protection_rearm_exhausted", notes=notes,
                             exit_price=735.0))
    assert c.bucket == "C" and c.reconstructable and not c.gradeable
    assert c.category == "truncated:protection_rearm_exhausted"


def test_rearm_preflight_sl_exit_without_a_fill_price_is_never_clean():
    notes = json.dumps({"exit_reason_source": "rearm_preflight"})
    c = classify_record(_rec(exit_reason="sl", notes=notes, exit_price=None, pnl=None))
    assert c.bucket == "C" and not c.gradeable
    assert c.category == "truncated:rearm_preflight_unpriced"
    c2 = classify_record(_rec(exit_reason="sl", notes=notes, exit_price=None,
                              entry_price=None, stop_loss=None, take_profit_1=None))
    assert c2.bucket == "B" and not c2.gradeable


def test_rearm_preflight_sl_exit_with_a_venue_fill_is_bucket_A_positive_control():
    notes = json.dumps({"exit_reason_source": "rearm_preflight",
                        "exit_price_source": "exchange_fill"})
    c = classify_record(_rec(exit_reason="sl", notes=notes, exit_price=709.8, pnl=-7.0))
    assert c.bucket == "A" and c.gradeable


def test_ordinary_sl_without_exit_price_is_unchanged_positive_control():
    """Only the monitor-initiated source is gated — a bracket sl row that
    simply lacks an exit_price column keeps its pre-existing bucket A."""
    c = classify_record(_rec(exit_reason="sl", pnl=-5.0))
    assert c.bucket == "A"
