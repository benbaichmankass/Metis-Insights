"""Tests for scripts/ops/exit_path_coverage.py.

The load-bearing property is the one this audit exists to protect: it must
never report `absent` for something it did not look at. A coverage audit that
launders "we could not read the broker" into "there is no bracket" is worse
than no audit, because it reads as a clean negative.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "exit_path_coverage", REPO / "scripts" / "ops" / "exit_path_coverage.py")
epc = importlib.util.module_from_spec(_SPEC)
sys.modules["exit_path_coverage"] = epc
_SPEC.loader.exec_module(epc)


def _trade(**kw):
    base = {"id": 1, "status": "open", "is_backtest": 0, "symbol": "XRPUSDT",
            "account_id": "bybit_1", "account_class": "real_money",
            "strategy_name": "xrp_pullback_2h", "stop_loss": 1.0,
            "take_profit_1": 2.0, "timestamp": "2026-07-29T00:00:00Z"}
    base.update(kw)
    return base


def _assess(trade, *, telemetry=None, broker=None, broker_supplied=False,
            cfg=None, reach=None):
    unit_of, src = epc._resolver()
    return epc.assess_trade(
        trade, units=epc.load_units(), cfg=cfg if cfg is not None else epc.load_cfg(),
        reach=reach if reach is not None else epc.load_reachability(),
        telemetry=telemetry or {}, bidx=epc.broker_index(broker),
        broker_supplied=broker_supplied, unit_of=unit_of, builders_src=src)


# --------------------------------------------------------------------------
# "we did not look" is never "it is not there"
# --------------------------------------------------------------------------
def test_no_broker_payload_yields_unknown_not_absent():
    r = _assess(_trade())
    assert r["price_paths"]["broker_stop"] == epc.UNKNOWN
    assert r["price_paths"]["broker_target"] == epc.UNKNOWN
    assert r["broker_basis"] == "no_broker_payload"


def test_account_absent_from_broker_payload_is_unknown():
    """/api/diag/ib_open_orders is IB-only; a bybit row is not covered by it."""
    payload = {"accounts": [{"account_id": "ib_paper",
                             "read_state": "orders_read", "orders": []}]}
    r = _assess(_trade(account_id="bybit_1"), broker=payload, broker_supplied=True)
    assert r["price_paths"]["broker_stop"] == epc.UNKNOWN
    assert r["broker_basis"] == "account_not_in_payload"


def test_could_not_look_account_is_unknown():
    payload = {"accounts": [{"account_id": "bybit_1",
                             "read_state": "could_not_look", "orders": None}]}
    r = _assess(_trade(), broker=payload, broker_supplied=True)
    assert r["price_paths"]["broker_stop"] == epc.UNKNOWN


def test_clean_read_with_no_leg_is_absent():
    """A confirmed clean read that lists nothing IS evidence of absence."""
    payload = {"accounts": [{"account_id": "bybit_1",
                             "read_state": "orders_read", "orders": []}]}
    r = _assess(_trade(), broker=payload, broker_supplied=True)
    assert r["price_paths"]["broker_stop"] == epc.ABSENT
    assert r["price_paths"]["broker_target"] == epc.ABSENT


# --------------------------------------------------------------------------
# leg classification: "STP LMT" contains "LMT"
# --------------------------------------------------------------------------
@pytest.mark.parametrize("order_type,expect", [
    ("STP", {"stop": True, "target": False}),
    ("STP LMT", {"stop": True, "target": False}),
    ("TRAIL", {"stop": True, "target": False}),
    ("LMT", {"stop": False, "target": True}),
])
def test_stop_family_classified_before_limit(order_type, expect):
    idx = epc.broker_index({"accounts": [
        {"account_id": "a", "read_state": "orders_read",
         "orders": [{"symbol": "MGC", "order_type": order_type}]}]})
    assert idx["a"]["by_symbol"]["MGC"] == expect


def test_a_stop_only_book_is_not_target_covered():
    payload = {"accounts": [{"account_id": "bybit_1", "read_state": "orders_read",
                             "orders": [{"symbol": "XRPUSDT",
                                         "order_type": "STP"}]}]}
    r = _assess(_trade(), broker=payload, broker_supplied=True)
    assert r["price_paths"]["broker_stop"] == epc.LIVE
    assert r["price_paths"]["broker_target"] == epc.ABSENT


# --------------------------------------------------------------------------
# per-trade reachability, not per-leg
# --------------------------------------------------------------------------
def test_leg_verdict_alone_does_not_make_a_lever_live():
    """cap_R = 0.099*entry/risk varies per FILL, so a leg-level `reachable`
    is not a statement about this trade. Without the trade's own arm_reach the
    honest answer is unknown."""
    r2 = _assess(_trade(strategy_name="xrp_pullback_2h"),
                 cfg={"xrp_pullback_2h": {"trail_decay_arm_r": 4.49}},
                 reach={"xrp_pullback_2h": {"verdict": "vol_conditional"}})
    assert r2["decision_paths"]["trail_decay"]["state"] == epc.UNKNOWN
    assert r2["verdict"] == "unknown"


def test_trade_level_unreachable_makes_it_absent():
    tel = {"1": {"trade_id": "1", "arm_reach": "unreachable", "cap_r": 3.92}}
    r = _assess(_trade(), telemetry=tel,
                cfg={"xrp_pullback_2h": {"trail_decay_arm_r": 4.49}},
                reach={"xrp_pullback_2h": {"verdict": "vol_conditional"}})
    assert r["decision_paths"]["trail_decay"]["state"] == epc.ABSENT
    assert r["verdict"] == "price_only"


def test_trade_level_reachable_makes_it_live():
    tel = {"1": {"trade_id": "1", "arm_reach": "reachable", "cap_r": 9.0}}
    r = _assess(_trade(), telemetry=tel,
                cfg={"xrp_pullback_2h": {"trail_decay_arm_r": 4.49}},
                reach={"xrp_pullback_2h": {"verdict": "vol_conditional"}})
    assert r["decision_paths"]["trail_decay"]["state"] == epc.LIVE
    assert r["verdict"] == "decision_exit_live"


# --------------------------------------------------------------------------
# the telemetry sentinel must not be rendered as a measurement
# --------------------------------------------------------------------------
# collapsed-state: thin_window — this test is about the SENTINEL, not the
# peak_state taxonomy. `thin_window` is named only because it is the state whose
# rows carry the -1e18 value in production; the audit itself never branches on
# peak_state at all (it branches on the magnitude of peak_r), so there is no
# consumer here to collapse. The taxonomy's own coverage is pinned by
# position_telemetry's tests.
def test_coalesce_sentinel_is_refused_not_reported():
    tel = {"1": {"trade_id": "1", "peak_r": -1e18,
                 "peak_pct_of_cap": -7.6e19, "cap_r": 1.31,
                 "peak_state": "thin_window", "arm_reach": "no_arm_declared"}}
    r = _assess(_trade(), telemetry=tel)
    assert r["telemetry"]["peak_r"] is None
    assert r["telemetry"]["peak_pct_of_cap"] is None
    assert r["telemetry"]["sentinel_peak"] is True
    assert r["telemetry"]["cap_r"] == 1.31  # a real value survives


def test_absent_telemetry_is_not_a_sentinel():
    r = _assess(_trade())
    assert r["telemetry"] == {"present": False, "sentinel_peak": False}


# --------------------------------------------------------------------------
# verdict precedence
# --------------------------------------------------------------------------
def test_unknown_decision_path_outranks_a_live_price_path():
    """We must not report `price_only` while a decision path might be live —
    that would overstate the finding."""
    r = _assess(_trade(strategy_name="xrp_pullback_2h"),
                cfg={"xrp_pullback_2h": {"trail_decay_arm_r": 4.49}},
                reach={})
    assert r["decision_paths"]["trail_decay"]["state"] == epc.UNKNOWN
    assert r["price_paths"]["monitor_sl_cross"] == epc.LIVE
    assert r["verdict"] == "unknown"


def test_module_that_cannot_emit_a_verdict_grades_not_applicable():
    """`not_applicable` (nothing to declare) is not `absent` (a choice made).

    Was asserted on stale_stop until 2026-08-18, when that lever was extracted
    to src/runtime/exit_levers.py and the pullback family gained it. Moved to
    exit_head, which this family still genuinely lacks — it needs an
    advisory-stage trained head that does not exist for it, so shipping the
    plumbing would be a capability that can never fire.
    """
    r = _assess(_trade())
    assert r["decision_paths"]["exit_head"]["state"] == epc.NA
    assert r["decision_paths"]["exit_head"]["why"] == "not_implemented"


def test_the_extracted_levers_are_seen_through_the_shared_import():
    """The companion positive: a source-only grep would answer `not_implemented`
    for both of these, which is how the extraction nearly degraded this audit
    into under-reporting coverage on a live-money family."""
    r = _assess(_trade())
    for lever in ("stale_stop", "giveback_stop"):
        assert r["decision_paths"][lever]["state"] != epc.NA
        assert r["decision_paths"][lever]["why"] == "undeclared"


# --------------------------------------------------------------------------
# population
# --------------------------------------------------------------------------
def test_only_open_non_backtest_rows_are_audited():
    rows = [_trade(id=1), _trade(id=2, status="closed"),
            _trade(id=3, is_backtest=1)]
    assert [r["id"] for r in epc._open_rows(rows)] == [1]
    assert [r["id"] for r in epc._open_rows({"rows": rows})] == [1]


def test_cause_rollup_separates_remedies():
    def mk(whys):
        return {"verdict": "price_only", "trade_id": "x",
                "decision_paths": {str(i): {"state": epc.ABSENT, "why": w}
                                   for i, w in enumerate(whys)}}
    assert epc.price_only_cause(mk(["undeclared", "undeclared"])) == "all_undeclared"
    assert epc.price_only_cause(mk(["not_implemented"] * 3)) == "family_not_implemented"
    assert epc.price_only_cause(
        mk(["declared_but_unreachable(x)", "undeclared"])) == "declared_but_unreachable"
    assert epc.price_only_cause(
        mk(["not_implemented", "undeclared"])) == "mixed"


def test_self_test_planted_controls_pass():
    """A probe that cannot find a known positive proves nothing."""
    assert epc._self_test() == 0


# --------------------------------------------------------------------------
# Bybit broker payload (BL-20260818-NO-BRACKET-READ-SURFACE-FOR-BYBIT-OR-ALPACA)
#
# /api/diag/bybit_open_orders shipped 2026-08-22 and this tool kept grading every
# bybit row `not_ib` for three days, because the CONSUMER was never told. These
# pin the two things that make the bybit read correct rather than merely present.
# --------------------------------------------------------------------------
def test_full_mode_protection_lives_on_the_position_not_an_order():
    """Under BYBIT_TPSL_MODE=full there is NO resting order at all.

    An orders-only indexer reports zero legs for a correctly-protected position,
    and this audit then grades it ABSENT -- manufacturing an alarm rather than
    missing one, which is the worse direction.
    """
    idx = epc.bybit_broker_index({"accounts": [
        {"account_id": "bybit_2", "read_state": "orders_read",
         "result": {"positions": [{"symbol": "BTCUSDT", "stop_loss": 100.0,
                                   "take_profit": 200.0}],
                    "orders": []}}]})
    assert idx["bybit_2"]["by_symbol"]["BTCUSDT"] == {"stop": True, "target": True}


def test_a_bybit_position_with_no_levels_is_measured_unprotected():
    """Distinct from the symbol simply not appearing: this one we DID look at."""
    idx = epc.bybit_broker_index({"accounts": [
        {"account_id": "bybit_2", "read_state": "orders_read",
         "result": {"positions": [{"symbol": "BTCUSDT", "stop_loss": None,
                                   "take_profit": None}], "orders": []}}]})
    assert idx["bybit_2"]["by_symbol"]["BTCUSDT"] == {"stop": False, "target": False}


@pytest.mark.parametrize("order,expect", [
    ({"stop_order_type": "StopLoss"}, "stop"),
    ({"stop_order_type": "PartialStopLoss"}, "stop"),
    ({"stop_order_type": "TrailingStop"}, "stop"),
    ({"stop_order_type": "TakeProfit"}, "target"),
    ({"stop_order_type": "PartialTakeProfit"}, "target"),
    # A resting reduce-only LIMIT carries no stopOrderType and is invisible to
    # the StopOrder filter entirely -- reading only that filter under-reports
    # target protection.
    ({"order_type": "Limit", "reduce_only": True}, "target"),
    # NEITHER side. Crediting an unclassifiable leg to one would manufacture
    # coverage, which is the failure this whole file exists to prevent.
    ({"order_type": "Limit", "reduce_only": False}, None),
    ({}, None),
])
def test_bybit_leg_side_never_guesses(order, expect):
    assert epc._bybit_leg_side(order) == expect


def test_a_not_applicable_sentinel_never_overwrites_a_real_read():
    """The one way merging two venue payloads silently destroys evidence.

    An IB account appears in the bybit payload as `not_bybit` and vice versa. A
    naive dict.update lets that sentinel replace the venue's own `orders_read`
    entry and turn a graded account back into `unknown` -- the quiet direction.
    """
    ib = {"ib_paper": {"read_state": "orders_read",
                       "by_symbol": {"MGC": {"stop": True, "target": True}}}}
    by = {"ib_paper": {"read_state": "not_bybit", "by_symbol": {}}}
    assert epc.merge_broker_index(ib, by)["ib_paper"]["read_state"] == "orders_read"
    # ...and symmetrically, order of arguments must not decide it.
    assert epc.merge_broker_index(by, ib)["ib_paper"]["read_state"] == "orders_read"


def test_supplying_only_bybit_leaves_an_ib_row_unknown_never_absent():
    """A payload for one venue is not evidence about another."""
    bidx = epc.merge_broker_index(epc.bybit_broker_index({"accounts": [
        {"account_id": "bybit_2", "read_state": "orders_read", "result": {}}]}))
    assert epc._broker_paths({"account_id": "ib_paper", "symbol": "MGC"},
                             bidx, True)[:2] == (epc.UNKNOWN, epc.UNKNOWN)


def test_audit_accepts_a_bybit_payload_alone_and_names_which_were_supplied():
    """`broker_supplied` must not read True for a venue never passed.

    A bare "yes" over an IB-only payload is exactly what let every bybit row
    read as unobservable while the broker side LOOKED checked.
    """
    res = epc.audit({"rows": []}, {}, None,
                    {"accounts": [{"account_id": "bybit_2",
                                   "read_state": "orders_read", "result": {}}]})
    assert res["summary"]["broker_supplied"] is True
    assert res["summary"]["broker_payloads"] == {"ib": False, "bybit": True}
    assert epc._payloads_label(res["summary"]) == "bybit; ib NOT supplied"
    none_res = epc.audit({"rows": []}, {}, None, None)
    assert none_res["summary"]["broker_supplied"] is False
    assert "NONE" in epc._payloads_label(none_res["summary"])
