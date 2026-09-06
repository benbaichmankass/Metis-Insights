"""MI-150 — the two exit-head consumers must not drift.

`trend_donchian` has consumed the M20 exit head since 2026-07-12; `ict_scalp`
consumes it as of MI-150. The firing rule was **factored out** into
`src.runtime.exit_head_apply.exit_head_verdict` rather than copied, and this
file is the mechanism that keeps it that way: it runs the SAME case table
through BOTH call sites and asserts identical verdicts.

⚠️ Why a shared module alone is not enough: nothing stops a future edit from
re-inlining the rule into one unit "for clarity". The parity assertion fails
the moment the two answers differ, whatever the cause — which is the property
`BL-20260814`-class duplication findings say prose reminders do not deliver.
"""
from __future__ import annotations

import pytest

from src.runtime.exit_head_apply import (exit_head_verdict, resolve_mode,
                                         staged_exit_head_decision)
from src.units.strategies.trend_donchian import _exit_head_verdict as donchian_call


def _rec(*, stage="advisory", score=0.05, open_r=-0.9, tau=0.10,
         below_r=0.5, policy="below_half_r", model_id="m1"):
    return {"stage": stage, "score": score, "tau": tau, "below_r": below_r,
            "policy": policy, "model_id": model_id,
            "feature_row": {"open_r": open_r}}


# (label, rec, meta, cfg) — one table, both call sites.
CASES = [
    ("fires_below_half_r", _rec(), {"exit_head_action": "close"}, {}),
    ("fires_via_cfg_declare", _rec(), {}, {"exit_head_action": "close"}),
    ("no_declare_never_fires", _rec(), {}, {}),
    ("shadow_stage_never_fires", _rec(stage="shadow"),
     {"exit_head_action": "close"}, {}),
    ("candidate_stage_never_fires", _rec(stage="candidate"),
     {"exit_head_action": "close"}, {}),
    ("score_above_tau_no_fire", _rec(score=0.9),
     {"exit_head_action": "close"}, {}),
    ("open_r_above_below_r_no_fire", _rec(open_r=1.4),
     {"exit_head_action": "close"}, {}),
    ("model_pin_match", _rec(),
     {"exit_head_action": "close", "exit_head_model": "m1"}, {}),
    ("model_pin_mismatch", _rec(),
     {"exit_head_action": "close", "exit_head_model": "other"}, {}),
    ("threshold_override_widens", _rec(score=0.4),
     {"exit_head_action": "close", "exit_head_threshold": 0.9}, {}),
    # peak heads fire on the OPPOSITE comparison — the MB-20260716 / M20 P4.2
    # bug. A copy that hardcoded below_half_r passes every case above and
    # fails these three.
    ("peak_fires_high_score", _rec(policy="peak", score=0.9),
     {"exit_head_action": "close"}, {}),
    ("peak_no_fire_low_score", _rec(policy="peak", score=0.01),
     {"exit_head_action": "close"}, {}),
    ("peak_winner_needs_open_r", _rec(policy="peak_winner", score=0.9,
                                      open_r=-0.9),
     {"exit_head_action": "close"}, {}),
    ("peak_winner_fires_when_r_in", _rec(policy="peak_winner", score=0.9,
                                         open_r=1.4),
     {"exit_head_action": "close"}, {}),
    # malformed / missing inputs must fail CLOSED, never raise
    ("no_rec", None, {"exit_head_action": "close"}, {}),
    ("empty_rec", {}, {"exit_head_action": "close"}, {}),
    ("nan_score", _rec(score=float("nan")), {"exit_head_action": "close"}, {}),
    ("nan_tau", _rec(tau=float("nan")), {"exit_head_action": "close"}, {}),
    ("missing_open_r", {"stage": "advisory", "score": 0.05, "tau": 0.1,
                        "below_r": 0.5, "feature_row": {}},
     {"exit_head_action": "close"}, {}),
    ("garbage_open_r", _rec(open_r="not-a-number"),
     {"exit_head_action": "close"}, {}),
]


@pytest.mark.parametrize("label,rec,meta,cfg", CASES,
                         ids=[c[0] for c in CASES])
def test_donchian_call_site_matches_the_shared_module(label, rec, meta, cfg):
    """The donchian shim must be a pure pass-through."""
    price = 100.0
    assert donchian_call(rec, meta, cfg, price) == exit_head_verdict(
        rec, meta, cfg, price), label


@pytest.mark.parametrize("label,rec,meta,cfg", CASES,
                         ids=[c[0] for c in CASES])
def test_ict_scalp_apply_mode_matches_the_donchian_verdict(label, rec, meta,
                                                           cfg):
    """At `apply`, ict_scalp reaches the SAME verdict donchian would.

    This is the drift assertion. The two units differ in HOW they are staged
    (ict_scalp carries a mode knob donchian does not), never in WHAT the head
    decides.
    """
    price = 100.0
    staged = staged_exit_head_decision(rec, meta, cfg, price, "apply")
    assert staged["verdict"] == donchian_call(rec, meta, cfg, price), label


@pytest.mark.parametrize("label,rec,meta,cfg", CASES,
                         ids=[c[0] for c in CASES])
def test_annotate_computes_the_same_decision_but_acts_on_nothing(label, rec,
                                                                 meta, cfg):
    """`annotate` must COMPUTE the verdict and DISCARD it — not skip it.

    An annotate mode that returns early produces no observation, which is
    BL-20260831-STRAY-OCA-SWEEP-ANNOTATE-COMPUTES-A-VERDICT-AND-DISCARDS-IT.
    `would_close` must therefore still track the real decision.
    """
    price = 100.0
    staged = staged_exit_head_decision(rec, meta, cfg, price, "annotate")
    assert staged["verdict"] is None, label
    assert staged["acted"] is False, label
    assert staged["would_close"] == (
        donchian_call(rec, meta, cfg, price) is not None), label


def test_off_mode_runs_nothing():
    d = staged_exit_head_decision(_rec(), {"exit_head_action": "close"}, {},
                                  100.0, "off")
    assert d["decision_state"] == "mode_off"
    assert d["verdict"] is None and d["would_close"] is False


def test_not_scored_is_distinct_from_no_fire():
    """*We did not look* must never render as *the head declined*.

    This is the ONLY state any ict_scalp leg can reach today (the live mirror
    publishes 1h artifacts only, so the scorer's tf guard refuses every 5m/15m
    leg). Collapsing it into `scored_no_fire` would present a consumer that
    cannot run as one that ran and found nothing.
    """
    not_scored = staged_exit_head_decision(None, {"exit_head_action": "close"},
                                           {}, 100.0, "annotate")
    no_fire = staged_exit_head_decision(_rec(score=0.9),
                                        {"exit_head_action": "close"}, {},
                                        100.0, "annotate")
    assert not_scored["decision_state"] == "not_scored"
    assert no_fire["decision_state"] == "scored_no_fire"
    assert not_scored["decision_state"] != no_fire["decision_state"]


@pytest.mark.parametrize("raw,expected", [
    ("apply", "apply"), ("annotate", "annotate"), ("off", "off"),
    ("APPLY", "apply"), ("  apply  ", "apply"),
    # the safety property: anything unrecognised lands on annotate
    ("aply", "annotate"), ("", "annotate"), ("1", "annotate"),
    ("true", "annotate"), ("on", "annotate"), ("disabled", "annotate"),
])
def test_mode_falls_back_to_annotate_never_to_apply_or_off(raw, expected):
    assert resolve_mode({"ICT_SCALP_EXIT_HEAD_MODE": raw}) == expected


def test_mode_unset_is_annotate():
    assert resolve_mode({}) == "annotate"


def test_shipped_default_arms_nothing():
    """The shipped state must be inert — verified in code, not from a doc.

    Both halves are required to arm: the mode AND the leg's YAML declaration.
    With the default mode and no declaration, no case in the table closes.
    """
    mode = resolve_mode({})
    for label, rec, meta, cfg in CASES:
        meta_no_declare = {k: v for k, v in meta.items()
                           if k != "exit_head_action"}
        cfg_no_declare = {k: v for k, v in cfg.items()
                          if k != "exit_head_action"}
        d = staged_exit_head_decision(rec, meta_no_declare, cfg_no_declare,
                                      100.0, mode)
        assert d["verdict"] is None, label
        assert d["acted"] is False, label
