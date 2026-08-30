"""A non-actionable tick must say WHY, and "we were gated" is not "nobody voted".

WHY THIS EXISTS, measured on the live audit log 2026-08-29 (SOLUSDT):

    14:59:03.701891  trend_donchian_sol_eval        side=buy
    14:59:03.994185  trend_donchian_sol_prop_eval   side=buy
    14:59:04.639159  pipeline_result  strategy=multiplexed_intents
                     side=none  status=skipped  reason='no_signal'

Two legs signalled and the audit recorded that none had. The journal had no row
AND the audit affirmatively said nothing signalled, so both surfaces agreed on a
false negative for two months
(`BL-20260830-TREND-DONCHIAN-SOL-SIGNALS-144-TIMES-AND-JOURNALS-NOTHING-ON-BYBIT-1`,
`BL-20260830-PIPELINE-RESULT-REPORTS-NO-SIGNAL-WHEN-TWO-LEGS-SIGNALLED-AND-LOST-ARBITRATION`).

The information died in two places, and both are pinned here.
"""
from __future__ import annotations

from unittest.mock import patch

from src.runtime.intents import StrategyIntent, aggregate_intents
from src.runtime.pipeline import skip_reason


# --- pipeline: the audit row must carry the aggregator's reason -------------


def test_meta_reason_is_carried_not_overwritten():
    """THE case: the aggregator said why, and the audit row must not replace it."""
    sig = {"symbol": "SOLUSDT", "side": "none",
           "meta": {"strategy_name": "multiplexed_intents",
                    "reason": "all_intents_gated"}}
    assert skip_reason(sig) == "all_intents_gated"


def test_absent_reason_still_reads_no_signal():
    """A plain single-strategy tick must read exactly as it did before."""
    assert skip_reason({"symbol": "BTCUSDT", "side": "none"}) == "no_signal"
    assert skip_reason({"symbol": "BTCUSDT", "side": "none", "meta": {}}) == "no_signal"


def test_unreadable_reason_falls_back_rather_than_writing_none():
    """An unreadable reason is not a reason. Never stamp `None`/blank/non-str
    onto the audit row — a null there is worse than the generic literal,
    because it reads as a field that was never populated."""
    for bad in (None, "", "   ", 42, ["all_intents_gated"], {"r": 1}):
        assert skip_reason({"side": "none", "meta": {"reason": bad}}) == "no_signal"


def test_a_malformed_signal_cannot_raise_on_the_live_path():
    """This runs on every non-actionable tick; it must never be the thing that
    breaks one."""
    assert skip_reason({}) == "no_signal"
    assert skip_reason({"meta": None}) == "no_signal"
    assert skip_reason({"meta": "not-a-dict"}) == "no_signal"


# --- intents: "gated" and "nobody voted" are different facts ----------------

_POLICY = {r: {"vwap": {"long": "off", "short": "off"}}
           for r in ("chop", "transitional", "trending")}


def _intent(strategy="vwap", side="long", regime="trending"):
    # Mirrors tests/test_aggregate_intents_regime_hard.py::_make_intent — the
    # canonical shape. A test-local schema that drifts from the real one is how
    # a suite goes green against a struct production does not have.
    return StrategyIntent(
        strategy=strategy, symbol="BTCUSDT", side=side, target_qty=1.0,
        regime=regime, adx_14=15.0, vol_regime=None,
        entry=70000.0, sl=69000.0, tp=72000.0,
    )


def _flat_reason(intents):
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal"):
        return aggregate_intents(intents, symbol="BTCUSDT")


def test_no_intents_at_all_reads_no_intents_for_symbol():
    r = _flat_reason([])
    assert r.side == "flat"
    assert r.reason == "no_intents_for_symbol"


def test_every_intent_gated_is_NOT_reported_as_nobody_voted():
    """THE distinction. Strategies actively wanted to trade and were refused —
    the opposite claim about the same silence."""
    r = _flat_reason([_intent(), _intent(side="short")])
    assert r.side == "flat"
    assert r.reason == "all_intents_gated", (
        "an all-gated tick must not read as 'no intents for symbol' — that "
        "denies that anything wanted to trade")


def test_the_two_flat_reasons_are_distinguishable():
    """Non-vacuity guard on the split itself: if a future change ever collapses
    these back into one string, this fails rather than the distinction quietly
    disappearing."""
    assert _flat_reason([]).reason != _flat_reason([_intent()]).reason


def test_gated_flat_carries_its_counts():
    """State the population: how many intents existed, and how many the gate
    removed — so a reader can tell a fully-gated tick from a partially-gated
    one without re-deriving it."""
    r = _flat_reason([_intent(), _intent(side="short")])
    assert r.meta.get("intents_before_gate") == 2
    assert r.meta.get("intents_removed_by_gate") == 2
