"""Tests for the strategy selection gate (scripts/strategy_gate.py, M7).

Proves the gate reaches the audit-established verdicts on the known evidence
and that missing inputs yield insufficient_data (never a silent pass).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))
import strategy_gate as sg  # noqa: E402


def _eval(**kw):
    base = dict(
        net_of_fee=None, insystem=None, book_return_dd=None,
        book_correlation=None, oos_retention=None,
    )
    base.update(kw)
    strategy = base.pop("strategy", "x")
    current_gate = base.pop("current_gate", "live")
    return sg.evaluate_strategy(strategy, current_gate, **base)


# ---- recommended_action logic ----

def test_live_with_insystem_drag_proposes_demote():
    """turtle_soup/ict_scalp/fade: live + net-negative in-system -> demote."""
    card = _eval(strategy="fade_breakout_4h", current_gate="live",
                 net_of_fee={"net_pnl": -120.0, "fee_pct_of_gross": 60.0, "fill_count": 50},
                 insystem={"pnl": -673.0, "trades": 250})
    assert card.recommended_action == "PROPOSE_DEMOTE_TO_SHADOW"
    fired = [d.name for d in card.demotion_triggers if d.status == "fail"]
    assert "D1_sustained_net_negative" in fired
    assert "D2_insystem_drag" in fired


def test_live_winner_keeps_live():
    """trend_donchian: live + net-positive in-system, no trigger fires."""
    card = _eval(strategy="trend_donchian", current_gate="live",
                 net_of_fee={"net_pnl": 250.0, "fee_pct_of_gross": 18.0, "fill_count": 300},
                 insystem={"pnl": 1480.0, "trades": 300})
    assert card.recommended_action == "KEEP_LIVE"
    assert all(d.status == "pass" for d in card.demotion_triggers)


def test_new_shadow_strategy_holds_for_data():
    """fvg_range_15m: shadow, no fills yet -> HOLD (collect data), never promote."""
    card = _eval(strategy="fvg_range_15m", current_gate="shadow",
                 net_of_fee=None,            # no live/shadow fills yet
                 insystem={"pnl": 373.0, "trades": 50},
                 book_return_dd=2.0, book_correlation=0.04, oos_retention=1.2)
    assert card.recommended_action == "HOLD_SHADOW_COLLECT_DATA"
    p1 = next(g for g in card.promotion_gates if g.name == "P1_sample_sufficiency")
    assert p1.status == "insufficient_data"


def test_shadow_clearing_all_gates_proposes_promote():
    card = _eval(strategy="candidate", current_gate="shadow",
                 net_of_fee={"net_pnl": 120.0, "fee_pct_of_gross": 25.0, "fill_count": 40},
                 insystem={"pnl": 200.0, "trades": 40},
                 book_return_dd=1.5, book_correlation=0.2, oos_retention=0.8)
    assert card.recommended_action == "PROPOSE_PROMOTE_TO_LIVE"
    assert all(g.status == "pass" for g in card.promotion_gates)


def test_vwap_fee_runaway_demote():
    """vwap pathology: fee% way over 100 -> D3 fires (if it were live)."""
    card = _eval(strategy="vwap", current_gate="live",
                 net_of_fee={"net_pnl": -35.82, "fee_pct_of_gross": 418.0, "fill_count": 167},
                 insystem={"pnl": -36.0, "trades": 167})
    assert card.recommended_action == "PROPOSE_DEMOTE_TO_SHADOW"
    d3 = next(d for d in card.demotion_triggers if d.name == "D3_fee_runaway")
    assert d3.status == "fail"


# ---- missing data is never a silent pass ----

def test_missing_net_of_fee_is_insufficient_not_pass():
    card = _eval(strategy="x", current_gate="shadow", net_of_fee=None)
    for name in ("P1_sample_sufficiency", "P2_net_positive_after_fees", "P3_fee_drag"):
        g = next(g for g in card.promotion_gates if g.name == name)
        assert g.status == "insufficient_data"


def test_missing_insystem_demote_trigger_does_not_fire():
    """No in-system data -> D2 is insufficient_data, NOT a demotion trigger."""
    card = _eval(strategy="x", current_gate="live", insystem=None,
                 net_of_fee={"net_pnl": 5.0, "fee_pct_of_gross": 10.0, "fill_count": 50})
    d2 = next(d for d in card.demotion_triggers if d.name == "D2_insystem_drag")
    assert d2.status == "insufficient_data"
    # No trigger fired -> stays live.
    assert card.recommended_action == "KEEP_LIVE"


def test_degraded_flag_propagates():
    card = _eval(strategy="x", current_gate="live", degraded=True,
                 net_of_fee={"net_pnl": 5.0, "fee_pct_of_gross": 10.0, "fill_count": 50},
                 insystem={"pnl": 5.0, "trades": 50})
    assert card.to_dict()["degraded_net_of_fee"] is True


def test_p4_oos_decay_fails_promotion():
    """fade's OOS expectancy decays ~half -> below 0.50 retention bar."""
    card = _eval(strategy="fade_breakout_4h", current_gate="shadow",
                 net_of_fee={"net_pnl": 10.0, "fee_pct_of_gross": 20.0, "fill_count": 40},
                 insystem={"pnl": 50.0, "trades": 40},
                 book_return_dd=1.5, book_correlation=0.04, oos_retention=0.45)
    p4 = next(g for g in card.promotion_gates if g.name == "P4_oos_retention")
    assert p4.status == "fail"
    assert card.recommended_action == "KEEP_SHADOW"
