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


# ------------------------------------------------- change detection
# `fingerprint()` is what lets the scheduled caller
# (.github/workflows/broker-bracket-reconcile.yml) record every run but speak
# only when the graded state MOVES. Both of the tool's alarm-shaped outputs are
# true in the steady state -- `total_findings` is 3 and `any_could_not_look` is
# True on every run -- so pinging on either would ping constantly, which
# CLAUDE.md names as its own P1 bug.

def _live_doc(mes_orders=MES_ORDERS, mhg_orders=MHG_ORDERS, ib_paper_read=True):
    doc = {"captured_at": "2026-08-21T06:39:13Z", "accounts": [
        {"account_id": "bybit_1", "read_state": "not_ib", "orders": None},
        {"account_id": "ib_paper",
         "read_state": "orders_read" if ib_paper_read else "could_not_look",
         "orders": (mhg_orders + MGC_ORDERS + list(mes_orders)) if ib_paper_read else None},
        {"account_id": "ib_live", "read_state": "could_not_look", "orders": None},
    ]}
    positions = [dict(t, account="ib_paper") for t in (MHG_TRADE, MGC_TRADE, MES_TRADE)]
    return bbr.reconcile(doc, positions)


def test_fingerprint_is_stable_across_identical_runs():
    """The steady state must NOT move, or the caller alarms every run."""
    assert bbr.fingerprint(_live_doc())[0] == bbr.fingerprint(_live_doc())[0]


def test_fingerprint_is_order_independent():
    """Leg/finding ordering is an artifact of the payload, not a state change."""
    a = bbr.fingerprint(_live_doc(mhg_orders=MHG_ORDERS))[0]
    b = bbr.fingerprint(_live_doc(mhg_orders=list(reversed(MHG_ORDERS))))[0]
    assert a == b


def test_fingerprint_moves_when_an_account_stops_being_readable():
    """ib_paper slipping to could_not_look is a WEDGED GATEWAY -- must alert.

    This is the signal the whole change-detector exists to preserve: ib_live
    sitting at could_not_look forever is silent, but a readable account going
    dark is not.
    """
    assert bbr.fingerprint(_live_doc(ib_paper_read=True))[0] \
        != bbr.fingerprint(_live_doc(ib_paper_read=False))[0]


def test_fingerprint_moves_when_a_new_finding_appears():
    """MHG (the control) losing its resting target must break the silence."""
    stop_only = [o for o in MHG_ORDERS if o["order_type"] == "STP"]
    assert bbr.fingerprint(_live_doc())[0] != bbr.fingerprint(_live_doc(mhg_orders=stop_only))[0]


def test_fingerprint_is_BLIND_to_magnitude__a_stated_limitation():
    """PINS THE DOCUMENTED LIMITATION so it cannot quietly become false.

    MES 4350's stop drifting from 69 ticks away to ~535 is the SAME set of
    problems, so the digest does NOT move and the caller stays silent. That is
    deliberate, and it is why the caller always rewrites the current numbers
    into its tracking record rather than relying on the digest alone. If this
    test ever fails, the docstring on `fingerprint()` is now wrong -- fix the
    prose, do not just re-baseline the test.
    """
    worse = [dict(MES_ORDERS[0], aux_price=7400.0)]
    assert "stop_price_diverges" in _kinds(MES_TRADE, worse, "MES")
    assert bbr.fingerprint(_live_doc())[0] == bbr.fingerprint(_live_doc(mes_orders=worse))[0]


def test_fingerprint_body_is_the_digest_preimage():
    """A reader must be able to see WHY two runs differ, not compare hashes."""
    import hashlib
    digest, body = bbr.fingerprint(_live_doc())
    assert hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] == digest
    assert "A:ib_live=could_not_look" in body
    assert "F:ib_paper:4350:MES:stop_price_diverges" in body
