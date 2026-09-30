"""RQ-20260929-401's registered partition, pinned BEFORE the run."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "exit_head_final_fold_grade", REPO / "scripts/research/exit_head_final_fold_grade.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


def _net(n=80, base=1.0, head=2.0, state="ok", **kw):
    return {"state": state, "n_oos": n, "baseline_net_r": base, "head_net_r": head,
            "recovered_r_oos": head - base, **kw}


def _ok(n, rec):
    return {"state": "ok", "n_oos": n, "recovered_r_oos": rec, "leg": "x"}


def test_recovered_is_head_minus_baseline_unrounded():
    s = G.leg_stat(_net(base=1.234567, head=0.234561), "leg")
    assert s["state"] == "ok" and s["recovered_r_oos"] == 0.234561 - 1.234567   # not rounded to 0.01


def test_stored_delta_must_equal_head_minus_baseline():
    bad = _net(base=1.0, head=2.0)
    bad["recovered_r_oos"] = 5.0
    assert G.leg_stat(bad, "leg")["state"] == "malformed"


def test_thin_final_year_is_underpowered_not_a_producer_failure():
    s = G.leg_stat({"state": "final_fold_missing", "n_test": 12}, "leg")
    assert s["state"] == "final_fold_missing"
    g = G.grade([s, _ok(100, 1), _ok(98, 1)])
    assert g["verdict"] == "pass" and g["read_state"] == "measured"      # excluded from Q, rest graded


def test_any_producer_problem_is_not_applicable_never_silently_dropped():
    for st in G.PRODUCER_STATES:
        g = G.grade([{"leg": "a", "state": st}, _ok(100, 1), _ok(98, 1)])
        assert g["verdict"] == "not_applicable" and g["read_state"] == "producer_failed", st
        assert "a=" + st in g["why"]
    # a missing report file and a replay that crashed map the same way
    assert G.leg_stat({"state": "replay_failed", "error": "x"}, "l")["state"] == "replay_failed"
    assert G.leg_stat({"state": "something_new"}, "l")["state"] == "malformed"


def test_partition_pass_refine_fail_and_floor():
    assert G.grade([_ok(100, 1), _ok(98, .1), _ok(84, -3)])["verdict"] == "pass"
    assert G.grade([_ok(100, 1), _ok(98, -.1), _ok(84, -3)])["verdict"] == "indeterminate"   # refine
    assert G.grade([_ok(100, -1), _ok(98, 0.0), _ok(84, -3)])["verdict"] == "fail"           # 0.0 is not > 0
    g = G.grade([_ok(63, 9), _ok(100, 1), _ok(98, -1)])       # a positive leg below the floor is excluded
    assert g["verdict"] == "indeterminate" and g["n_legs_graded"] == 2 and g["n_legs_positive"] == 1
    g = G.grade([_ok(63, 9), _ok(62, 9), _ok(100, 1)])
    assert g["verdict"] == "indeterminate" and "underpowered" in g["why"]


def test_corrupt_or_non_object_report_is_malformed_and_never_crashes_the_grader(tmp_path):
    import json
    for name, body in (("a", "{not json"), ("b", "[1, 2]"), ("c", "\xff\xfe\x00bad")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "final_fold_net.json").write_bytes(body.encode("latin-1"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "final_fold_net.json").write_text(json.dumps(_net(n=80)))
    stats = [G.read_leg(tmp_path, leg) for leg in "abcd"] + [G.read_leg(tmp_path, "missing")]
    assert [s["state"] for s in stats] == ["malformed", "malformed", "malformed", "ok", "no_report"]
    g = G.grade(stats)
    assert g["verdict"] == "not_applicable" and g["read_state"] == "producer_failed"
    assert "a=malformed" in g["why"] and "missing=no_report" in g["why"]


def test_cost_policy_unverified_is_a_producer_state():
    assert "cost_policy_unverified" in G.PRODUCER_STATES
    g = G.grade([{"leg": "a", "state": "cost_policy_unverified"}, _ok(100, 1), _ok(98, 1)])
    assert g["verdict"] == "not_applicable" and "a=cost_policy_unverified" in g["why"]


def test_single_leg_rule_rq_20260930_402():
    g = G.grade_single_leg
    assert g([_ok(108, 0.01)])["verdict"] == "pass"
    assert g([_ok(64, 0.01)])["verdict"] == "pass"                     # floor is inclusive
    assert g([_ok(108, 0.0)])["verdict"] == "fail"                     # 0.0 is not > 0
    assert g([_ok(108, -2.0)])["verdict"] == "fail"
    assert g([_ok(63, 9.0)])["verdict"] == "indeterminate"             # floor never lowered
    assert g([{"leg": "x", "state": "final_fold_missing"}])["verdict"] == "indeterminate"
    r = g([{"leg": "x", "state": "replay_mismatch"}])
    assert r["verdict"] == "not_applicable" and r["read_state"] == "producer_failed"
    assert g([_ok(100, 1), _ok(100, 1)])["verdict"] == "not_applicable"   # exactly one leg
    assert G.grade([_ok(108, 1)])["verdict"] == "indeterminate"        # family rule unchanged


def test_stale_other_year_file_is_never_graded_as_this_fold(tmp_path):
    """Manager review of #14514: a copied round dir carries the 2026 final_fold_net.json; a crashed
    2025 replay writes nothing, so that file must not grade as a 2025 PASS."""
    import json
    d = tmp_path / "leg"
    d.mkdir()
    stale = {"state": "ok", "fold_year": 2026, "n_oos": 95, "baseline_net_r": -4.556,
             "head_net_r": -4.346, "recovered_r_oos": -4.346 + 4.556}
    (d / "final_fold_net.json").write_text(json.dumps(stale))
    s = G.read_leg(tmp_path, "leg", 2025)
    assert s["state"] == "malformed"
    g = G.grade_single_leg([s])
    assert g["verdict"] == "not_applicable" and g["read_state"] == "producer_failed"
    stale.pop("fold_year")                                              # unlabelled is refused too
    (d / "final_fold_net.json").write_text(json.dumps(stale))
    assert G.read_leg(tmp_path, "leg", 2025)["state"] == "malformed"
    stale["fold_year"] = 2025
    (d / "final_fold_net.json").write_text(json.dumps(stale))
    assert G.read_leg(tmp_path, "leg", 2025)["state"] == "ok"
    (d / "final_fold_net.json").write_text(json.dumps({"state": "final_fold_missing", "expect_year": 2026}))
    assert G.read_leg(tmp_path, "leg", 2025)["state"] == "malformed"
    assert G.read_leg(tmp_path, "leg")["state"] == "final_fold_missing"   # family rule: no year check
