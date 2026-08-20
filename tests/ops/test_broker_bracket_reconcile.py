"""`broker_bracket_reconcile.py` — the declared-vs-resting bracket detector.

The condition it exists for was invisible to every prior check because those
checks measure QUANTITY and SIDE, never PRICE: on 2026-08-20 MES trade 4350
graded fully stop-covered (`stop_qty` 15 of 15, correct side) while the only
resting stop sat 69 ticks from the level the strategy declared.

MHG IS THE DISCRIMINATING CONTROL and it is the point of this file. A detector
that flags all three live positions is exactly as broken as one that flags
none, so the control is asserted alongside every positive.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "broker_bracket_reconcile",
        _ROOT / "scripts" / "ops" / "broker_bracket_reconcile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bbr = _load()
TICKS = {"MES": 0.25, "MGC": 0.10, "MHG": 0.0005}


def _kinds(trade, orders, sym):
    r = bbr.reconcile_position(trade, orders, TICKS[sym])
    return {f["kind"] for f in r["findings"]}


# --------------------------------------------------------------- fixtures
# All three are the LIVE state measured at 2026-08-20T20:23:39Z via
# /api/diag/ib_open_orders + /api/bot/positions?include_paper=true.
MHG_TRADE = {"id": "4796", "symbol": "MHG", "qty": 29.0,
             "stopLoss": 6.22171429, "takeProfit": 7.141302}
MHG_ORDERS = [
    {"symbol": "MHG", "order_type": "STP", "total_quantity": 29.0,
     "aux_price": 6.2215, "lmt_price": 0.0, "oca_group": "308977633", "order_id": 399},
    {"symbol": "MHG", "order_type": "LMT", "total_quantity": 29.0,
     "aux_price": 0.0, "lmt_price": 7.1415, "oca_group": "308977633", "order_id": 398},
]
MGC_TRADE = {"id": "4773", "symbol": "MGC", "qty": 95.0,
             "stopLoss": 4371.1469, "takeProfit": 4393.02071429}
MGC_ORDERS = [{"symbol": "MGC", "order_type": "STP", "total_quantity": 95.0,
               "aux_price": 4371.1, "lmt_price": 0.0,
               "oca_group": "oca-protect-389", "order_id": 391}]
MES_TRADE = {"id": "4350", "symbol": "MES", "qty": 15.0,
             "stopLoss": 7533.696429, "takeProfit": 8390.59025}
MES_ORDERS = [{"symbol": "MES", "order_type": "STP", "total_quantity": 15.0,
               "aux_price": 7516.5, "lmt_price": 0.0,
               "oca_group": "oca-protect-336", "order_id": 338}]


# ------------------------------------------------------- the control
def test_MHG_the_control_is_clean():
    """MHG holds a stop AND a target at the declared levels in one OCA group.

    If this ever fails, the detector is flagging correctly-bracketed
    positions and every other assertion here is worthless.
    """
    assert _kinds(MHG_TRADE, MHG_ORDERS, "MHG") == set()


# ------------------------------------------------------- the positives
def test_MGC_target_naked_with_a_declared_target_is_flagged():
    assert "target_naked_declared" in _kinds(MGC_TRADE, MGC_ORDERS, "MGC")


def test_MGC_sub_tick_stop_rounding_is_not_a_divergence():
    """4371.1469 declared vs 4371.1 resting is the venue snapping a fractional
    level onto the 0.10 grid — correct behaviour, and reporting it would bury
    the real 69-tick finding in noise."""
    assert "stop_price_diverges" not in _kinds(MGC_TRADE, MGC_ORDERS, "MGC")


def test_MES_stop_price_divergence_is_flagged():
    assert "stop_price_diverges" in _kinds(MES_TRADE, MES_ORDERS, "MES")


def test_MES_is_not_reported_as_stop_naked():
    """The whole point: the QUANTITY is right (15 of 15) and the SIDE is right.
    A quantity-only grade calls this fully covered, which is what it did."""
    k = _kinds(MES_TRADE, MES_ORDERS, "MES")
    assert "stop_naked" not in k and "stop_partial" not in k


def test_the_divergence_is_reported_in_ticks_and_absolute_terms():
    r = bbr.reconcile_position(MES_TRADE, MES_ORDERS, TICKS["MES"])
    f = next(x for x in r["findings"] if x["kind"] == "stop_price_diverges")
    assert f["ticks"] == pytest.approx(68.79, abs=0.1)
    assert f["resting"] == 7516.5 and f["order_id"] == 338


# ----------------------------------------- the over-cover / naked-short pair
OVER = MES_ORDERS + [{"symbol": "MES", "order_type": "STP", "total_quantity": 15.0,
                      "aux_price": 7533.75, "lmt_price": 0.0,
                      "oca_group": "oca-protect-373", "order_id": 375}]


def test_over_cover_is_flagged():
    """30 of stop against a 15 long — ported from the Bybit `over_covered`
    signal, which is the venue-independent gap that row named."""
    assert "stop_over_cover" in _kinds(MES_TRADE, OVER, "MES")


def test_disjoint_oca_groups_are_flagged_separately():
    """THE load-bearing assumption: stops in DIFFERENT OCA groups are NOT
    mutually cancelling. ocaType=1 cancels the rest of the SAME group only, so
    one firing flattens the position and the other sells into a naked short.
    Treating 30-vs-15 as merely 'redundant protection' is what made it read
    safe."""
    assert "stop_disjoint_oca" in _kinds(MES_TRADE, OVER, "MES")


def test_two_stops_in_one_group_are_neither_over_cover_nor_disjoint():
    same = [dict(o, oca_group="oca-protect-336") for o in OVER]
    k = _kinds(MES_TRADE, same, "MES")
    assert "stop_over_cover" not in k and "stop_disjoint_oca" not in k


def test_the_divergence_appeared_when_the_matching_leg_was_cancelled():
    """With order 375 (7533.75) present the declared level IS represented, so
    there is no price finding. The divergence is a consequence of cancelling
    the journal-matching leg and keeping the stray."""
    assert "stop_price_diverges" not in _kinds(MES_TRADE, OVER, "MES")


# ------------------------------------------------------- classification
@pytest.mark.parametrize("otype,expected", [
    ("STP", "stop"), ("STP LMT", "stop"), ("TRAIL", "stop"),
    ("TRAIL LIMIT", "stop"), ("STOP", "stop"),
    ("LMT", "target"), ("LIMIT", "target"),
    ("MKT", None), ("", None), (None, None),
])
def test_leg_classification(otype, expected):
    assert bbr.protective_leg_side(otype) == expected


def test_a_stop_limit_never_manufactures_target_coverage():
    """'STP LMT' contains 'LMT'. An LMT-first test would invent a target that
    does not exist — strictly worse than the bug it replaces."""
    stp_lmt = [{"symbol": "MES", "order_type": "STP LMT", "total_quantity": 15.0,
                "aux_price": 7533.70, "lmt_price": 7533.50,
                "oca_group": "g1", "order_id": 1}]
    assert "target_naked_declared" in _kinds(MES_TRADE, stp_lmt, "MES")


def test_it_agrees_with_the_enforcing_classifier_in_ib_client():
    """A second definition free to drift from the one that actually gates the
    live sweep would be its own defect."""
    src = (_ROOT / "src" / "units" / "accounts" / "ib_client.py").read_text()
    assert "def _protective_leg_side" in src, "the enforcing classifier moved"
    ns: dict = {}
    body = src.split("def _protective_leg_side", 1)[1]
    body = body.split("\nclass ", 1)[0]
    exec("def _protective_leg_side" + body, ns)  # noqa: S102 — pinning parity
    theirs = ns["_protective_leg_side"]
    for otype in ("STP", "STP LMT", "TRAIL", "TRAIL LIMIT", "LMT", "LIMIT",
                  "MKT", "STOP", "STOP LIMIT", "", None):
        assert bbr.protective_leg_side(otype) == theirs(otype), otype


# ------------------------------------------------------- never-collapsed
def test_an_undeclared_target_is_info_not_a_finding():
    """A missing stop may be closed blind; a missing TARGET is decision-time
    geometry. Imposing one on a strategy that never chose it is Tier-3."""
    no_tp = {"id": "1", "symbol": "MES", "qty": 15.0,
             "stopLoss": 7516.5, "takeProfit": None}
    r = bbr.reconcile_position(no_tp, MES_ORDERS, TICKS["MES"])
    assert r["clean"] and "target_absent_undeclared" in r["info"]


def test_could_not_look_is_never_zero_findings():
    res = bbr.reconcile({"accounts": [
        {"account_id": "ib_paper", "read_state": "could_not_look", "orders": None}]}, [])
    acct = res["accounts"][0]
    assert acct["findings"] is None, "a could-not-look account must not report 0"
    assert res["any_could_not_look"] is True


def test_not_ib_is_distinct_from_could_not_look():
    """'there is nothing to read' is not 'we failed to read'."""
    res = bbr.reconcile({"accounts": [
        {"account_id": "bybit_1", "read_state": "not_ib", "orders": None}]}, [])
    assert res["accounts"][0]["state"] == "not_ib"
    assert res["any_could_not_look"] is False


def test_an_unreadable_leg_quantity_is_not_graded_clean():
    bad = [{"symbol": "MES", "order_type": "STP", "total_quantity": None,
            "aux_price": 7533.70, "lmt_price": 0.0, "oca_group": "g", "order_id": 1}]
    assert "coverage_ungradeable" in _kinds(MES_TRADE, bad, "MES")


# ------------------------------------------------------- end to end
def test_the_live_population_flags_MGC_and_MES_and_passes_MHG():
    """The discrimination requirement, asserted as one statement."""
    doc = {"captured_at": "2026-08-20T20:23:39Z", "accounts": [
        {"account_id": "bybit_1", "read_state": "not_ib", "orders": None},
        {"account_id": "ib_paper", "read_state": "orders_read",
         "orders": MHG_ORDERS + MGC_ORDERS + MES_ORDERS},
    ]}
    positions = [dict(t, account="ib_paper") for t in (MHG_TRADE, MGC_TRADE, MES_TRADE)]
    res = bbr.reconcile(doc, positions)
    ib = next(a for a in res["accounts"] if a["account_id"] == "ib_paper")
    by_sym = {p["symbol"]: p for p in ib["positions"]}
    assert by_sym["MHG"]["clean"] is True
    assert by_sym["MGC"]["clean"] is False
    assert by_sym["MES"]["clean"] is False
    assert res["total_findings"] == 3
    assert res["any_could_not_look"] is False


def test_self_test_passes():
    assert bbr._self_test() == 0
