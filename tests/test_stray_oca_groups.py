"""Tests for `src.runtime.stray_oca_groups` — the keyed re-arm's stray-group plan.

The scenarios are the LIVE ones measured 2026-08-26 on `ib_paper`, not invented:
MHG carried `oca-protect-446` + `oca-protect-465` (legacy reqId form) beside the
keyed `oca-protect-t4796`, and MGC carried the bare-numeric `834864174` beside
`oca-protect-t5007`.
"""

import pytest

from src.runtime.stray_oca_groups import (
    KEEP_TARGET,
    MODE_ANNOTATE,
    MODE_APPLY,
    MODE_OFF,
    NOT_PROTECTIVE,
    SIBLING_KEYED,
    STRAY_UNKEYED,
    UNGROUPED,
    classify_leg,
    is_keyed_group,
    plan_stray_cancels,
    resolve_mode,
)
from src.units.accounts.ib_client import _protective_leg_side


def leg(group, order_type="STP", **kw):
    row = {"oca_group": group, "order_type": order_type}
    row.update(kw)
    return row


def plan(legs, keep):
    return plan_stray_cancels(legs, keep, _protective_leg_side)


# ── is_keyed_group ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("oca-protect-t4796", True),
    ("oca-protect-t5007", True),
    ("oca-protect-465", False),   # legacy reqId form, measured live on MHG
    ("oca-protect-446", False),
    ("834864174", False),         # bare-numeric form, measured live on MGC
    ("", False),
    (None, False),
    ("oca-protect-t", False),     # empty key identifies no trade
])
def test_is_keyed_group(name, expected):
    assert is_keyed_group(name) is expected


# ── classify_leg ─────────────────────────────────────────────────────────────

def test_classify_covers_every_state():
    keep = "oca-protect-t4796"
    assert classify_leg(leg(keep), keep, "stop") == KEEP_TARGET
    assert classify_leg(leg("oca-protect-t9999"), keep, "stop") == SIBLING_KEYED
    assert classify_leg(leg("oca-protect-465"), keep, "stop") == STRAY_UNKEYED
    assert classify_leg(leg("834864174"), keep, "target") == STRAY_UNKEYED
    assert classify_leg(leg(""), keep, "stop") == UNGROUPED
    assert classify_leg(leg("oca-protect-465"), keep, None) == NOT_PROTECTIVE


def test_stop_limit_is_a_stop_not_a_target():
    """`STP LMT` contains `LMT`; the stop family must win, or a stop-limit
    would be filed as a take-profit and manufacture false target coverage."""
    assert _protective_leg_side("STP LMT") == "stop"
    assert classify_leg(leg("834864174", "STP LMT"), "oca-protect-t1", "stop") == STRAY_UNKEYED


# ── plan_stray_cancels — the live MHG scenario ───────────────────────────────

def test_live_mhg_300pct_scenario_cancels_both_legacy_groups():
    keep = "oca-protect-t4796"
    legs = [
        leg(keep, "STP"), leg(keep, "LMT"),                    # this trade's own
        leg("oca-protect-446", "STP"), leg("oca-protect-446", "LMT"),
        leg("oca-protect-465", "STP"), leg("oca-protect-465", "LMT"),
    ]
    out = plan(legs, keep)
    assert sorted(out["stray_groups"]) == ["oca-protect-446", "oca-protect-465"]
    assert len(out["cancel"]) == 4
    # the keep group is re-placed by the existing keyed pre-cancel, never here
    assert all(row["oca_group"] != keep for row in out["cancel"])
    assert out["by_state"][KEEP_TARGET] == 2
    assert out["by_state"][STRAY_UNKEYED] == 4


def test_live_mgc_bare_numeric_group_is_a_stray():
    keep = "oca-protect-t5007"
    legs = [leg(keep, "STP"), leg(keep, "LMT"),
            leg("834864174", "STP"), leg("834864174", "LMT")]
    out = plan(legs, keep)
    assert out["stray_groups"] == ["834864174"]
    assert len(out["cancel"]) == 2


# ── the guard that matters most ──────────────────────────────────────────────

def test_a_siblings_keyed_group_is_never_cancelled():
    """POSITIVE CONTROL for BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY.

    IB nets per contract, so a symbol can host one protective group per open
    trade. Stripping a sibling's legs is a BIGGER blast radius than the stray
    it would clear, so a keyed group that is not ours must survive untouched.
    """
    keep = "oca-protect-t4796"
    sibling = "oca-protect-t5150"
    legs = [leg(keep, "STP"), leg(sibling, "STP"), leg(sibling, "LMT")]
    out = plan(legs, keep)
    assert out["cancel"] == []
    assert out["stray_groups"] == []
    assert out["preserved_groups"] == [sibling]


def test_ungrouped_legs_are_reported_but_never_cancelled():
    """A leg with no ocaGroup cannot be shown to be this trade's abandoned
    protection — it could be a hand-placed exit. Report, do not cancel."""
    keep = "oca-protect-t4796"
    out = plan([leg("", "STP"), leg("", "LMT")], keep)
    assert out["cancel"] == []
    assert out["ungrouped_seen"] == 2
    assert out["by_state"][UNGROUPED] == 2


def test_no_keep_group_never_widens_the_fallback_path():
    """Without a keyed group this is the symbol-wide fallback, a different code
    path with its own documented hazard. It must not be widened from here."""
    out = plan([leg("oca-protect-465", "STP"), leg("834864174", "LMT")], "")
    assert out["cancel"] == []
    # still REPORTED, so the state is observable even when nothing is actioned
    assert sorted(out["stray_groups"]) == ["834864174", "oca-protect-465"]


def test_non_protective_orders_are_left_alone():
    out = plan([leg("834864174", "MKT"), leg("834864174", "MOC")], "oca-protect-t1")
    assert out["cancel"] == []
    assert out["by_state"][NOT_PROTECTIVE] == 2


# ── mode resolver ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("off", MODE_OFF), ("apply", MODE_APPLY), ("annotate", MODE_ANNOTATE),
    ("APPLY", MODE_APPLY), ("  off  ", MODE_OFF),
    ("", MODE_ANNOTATE), (None, MODE_ANNOTATE),
    ("aply", MODE_ANNOTATE),   # a typo must not switch a live order path on
    ("0", MODE_ANNOTATE), ("false", MODE_ANNOTATE),
])
def test_resolve_mode(raw, expected):
    assert resolve_mode(raw) == expected
