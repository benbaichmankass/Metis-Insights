"""scripts/ops/mandate_resolver.py — every refusal clause, plus FIRE controls.

Each refusal test starts from the SAME passing fixture and breaks exactly one
thing, so a test that goes red names the clause that broke. The FIRE tests are
the positive controls: without them, a resolver that refused everything would
pass every refusal test.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import mandate_resolver as mr  # noqa: E402

LEG = "ada_pullback_2h"
LEG_CFG = {"enabled": True, "execution": "live", "timeframe": "2h",
           "symbols": ["ADAUSDT"], "atr_stop_mult": 1.5}
EQ_LEG = "spy_trend_long_1d"
EQ_CFG = {"enabled": True, "execution": "live", "timeframe": "1d", "symbols": ["SPY"]}

BAR = {"min_n_closed": 30, "expectancy_gt": 0, "fold_majority": True, "cost_tolerance_bps": 0.0}
CAP = {"basis": mr.CAP_BASIS, "max_auto_share": 0.25}


def _mandate(mid, direction, **extra):
    m = {"id": mid, "grants": "x", "direction": direction, "granted_by": "operator",
         "granted_at": "2026-09-24", "bar": dict(BAR)}
    if direction == "add_risk":
        m["cap"] = dict(CAP)
    m.update(extra)
    return m


MANDATES = {"mandates": [
    _mandate("MD-PROMOTE-S0-S1", "add_risk"),
    _mandate("MD-PROMOTE-S1-S2", "add_risk"),
    _mandate("MD-DEMOTE-S2-S1", "derisk_only"),
    _mandate("MD-DEMOTE-S1-OFF", "derisk_only"),
], "proposed": []}


def _acct(cls, exchange, legs, risk_pct=0.015):
    return {"account_class": cls, "exchange": exchange, "mode": "live",
            "risk": {"risk_pct": risk_pct}, "strategies": list(legs)}


ACCOUNTS = {"accounts": {
    "bybit_1": _acct("paper", "bybit", [LEG, "l1", "l2"]),
    "bybit_2": _acct("real_money", "bybit", ["a", "b", "c", "d"]),
    "bybit_portfolio": _acct("paper", "bybit", ["a", "b", "c", "d"]),
    "alpaca_paper": _acct("paper", "alpaca", [EQ_LEG]),
    "alpaca_live": _acct("real_money", "alpaca", ["e", "f", "g"], 0.02),
    "alpaca_portfolio": _acct("paper", "alpaca", ["e", "f", "g"]),
}}


def _record(leg, cfg, *, slippage=5.0, n=56, folds_net=(4.8583, 2.2583, 8.3058, 3.5996)):
    net = round(sum(folds_net), 4)
    return {
        "strategy": leg, "config_fingerprint": mr.b1.config_fingerprint(cfg),
        "coverage_state": "measured", "harness": "pullback",
        "n_trades_oos": n, "net_r_oos": net, "expectancy_r_oos": round(net / n, 4),
        "folds": len(folds_net), "folds_positive": sum(1 for x in folds_net if x > 0),
        "fold_detail": [{"fold": i + 1, "net_r": x} for i, x in enumerate(folds_net)],
        "cost_stack": {"fees": 7.5, "slippage": slippage, "funding": 1.0},
        "decision_rule": {"id": "RULE-D1", "rule": "net_r_oos > 0 net of full cost",
                          "registered_at": "2026-09-22T05:34:08Z", "verdict": "pass"},
        "generated_at": "2026-09-24T12:41:53+00:00",
        "source_run": f"comms/strategy_evidence/runs/x/{leg}__trades.jsonl",
    }


def _r3(verdicts=None, *, modelled=5.0, as_of="2026-09-25T13:23:21+00:00"):
    """A record in the shape `scripts/research/r3_cost_fidelity.py` writes.

    `verdicts` maps leg -> the rule's per-leg verdict string, or to a full
    `{"verdict": ..., "from_account": ..., "basis": ...}` block when a test
    cares about the deciding cell. `modelled` is what R3 says it GRADED
    against, which the resolver cross-checks against the committed record.
    """
    if verdicts is None:
        # The shape of the real 2026-09-25 record: the perp leg has a market
        # cell that decided, the equity leg has no measured exits at all.
        verdicts = {LEG: "consistent", EQ_LEG: "insufficient_n"}
    per_leg = {}
    for leg, v in verdicts.items():
        block = dict(v) if isinstance(v, dict) else {
            "verdict": v,
            "from_account": "bybit_2" if leg == LEG else None,
            "basis": "market" if leg == LEG else None,
        }
        per_leg[leg] = {"modelled_slippage_bps": modelled, "leg": block}
    return {"as_of": as_of, "rule": {"id": mr.R3_RULE_ID}, "per_leg": per_leg}


R3_DATED = f"{mr.R3_DIR_REL}/2026-09-25.json"


def _w(root: Path, rel: str, data, as_yaml=False):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data) if as_yaml else json.dumps(data), encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    def build(mandates=None, accounts=None, records=None, r3=..., firings=(), mirror=None):
        if r3 is ...:
            r3 = _r3()
        _w(tmp_path, mr.MANDATES_REL, mandates or MANDATES, as_yaml=True)
        _w(tmp_path, mr.ACCOUNTS_REL, accounts or ACCOUNTS, as_yaml=True)
        _w(tmp_path, mr.STRATEGIES_REL, {"strategies": {LEG: LEG_CFG, EQ_LEG: EQ_CFG}}, as_yaml=True)
        recs = records if records is not None else {LEG: _record(LEG, LEG_CFG),
                                                    EQ_LEG: _record(EQ_LEG, EQ_CFG)}
        for leg, rec in recs.items():
            _w(tmp_path, f"{mr.EVIDENCE_DIR_REL}/{leg}.json", rec)
            if rec.get("source_run"):
                (tmp_path / rec["source_run"]).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / rec["source_run"]).write_text("{}\n")
        if r3 is not None:
            _w(tmp_path, R3_DATED, r3)
        for i, f in enumerate(firings):
            _w(tmp_path, f"{mr.FIRINGS_DIR_REL}/{i}.json", f)
        if mirror is not None:
            _w(tmp_path, f"{mr.MIRROR_DIR_REL}/{mirror['leg']}.json", mirror)
            if mirror.get("source_run"):
                (tmp_path / mirror["source_run"]).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / mirror["source_run"]).write_text("{}\n")
        return tmp_path
    return build


def _s1s2(root, leg=LEG, account="bybit_2", **kw):
    return mr.resolve(leg, "S1", "S2", account, root=root, **kw)


# ── positive controls ──────────────────────────────────────────────────────
def test_fire_s1_to_s2_proposes_live_and_mirror(repo):
    res = _s1s2(repo())
    assert res["verdict"] == "FIRE", res
    assert res["proposal"]["roster_add"] == {"bybit_2": [LEG], "bybit_portfolio": [LEG]}
    assert res["proposal"]["writes_nothing"] is True
    assert res["caveats"] == []  # perps are measured, no placeholder caveat
    cf = res["evidence"]["cost_fidelity"]
    assert cf["verdict"] == "consistent"          # the Gate-1 pass, per leg
    assert cf["from_account"] == "bybit_2" and cf["basis"] == "market"
    assert cf["record"] == R3_DATED and cf["rule"] == mr.R3_RULE_ID
    assert cf["basis_of_comparison"] == "MEASURED"
    # 1 auto leg on a 5-leg roster = 20% <= 25%
    assert res["evidence"]["cap"]["auto_promoted_after"] == 1
    assert res["evidence"]["cap"]["roster_after"] == 5


def test_fire_equity_leg_carries_the_provisional_cost_caveat(repo):
    res = _s1s2(repo(), leg=EQ_LEG, account="alpaca_live")
    assert res["verdict"] == "FIRE", res
    assert "E62" in res["caveats"][0] and "PLACEHOLDER" in res["caveats"][0]
    assert res["caveats"][0] == mr.PROVISIONAL_CAVEAT
    # ...and a SECOND caveat naming the state R3 is actually in, so a reader is
    # never told "provisional" without being told there is no verdict either.
    assert len(res["caveats"]) == 2
    assert "insufficient_n" in res["caveats"][1]
    assert "NO DECISIVE COST-FIDELITY VERDICT" in res["caveats"][1]
    assert res["evidence"]["cost_fidelity"]["basis_of_comparison"] == "PROVISIONAL"


def test_fire_s0_to_s1(repo):
    root = repo(accounts={"accounts": {**ACCOUNTS["accounts"],
                                       "bybit_1": _acct("paper", "bybit", ["l1", "l2", "l3"])}})
    res = mr.resolve(LEG, "S0", "S1", "bybit_1", root=root)
    assert res["verdict"] == "FIRE", res
    assert res["proposal"]["roster_add"] == {"bybit_1": [LEG]}


def test_fire_demote_s2_to_s1_on_a_negative_mirror_window(repo):
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_2"]["strategies"].append(LEG)
    accts["accounts"]["bybit_portfolio"]["strategies"].append(LEG)
    root = repo(accounts=accts, mirror={"leg": LEG, "n_closed": 40, "net_r_net_of_full_cost": -3.2,
                                        "source_run": "comms/mandate_evidence/runs/m.jsonl"})
    res = mr.resolve(LEG, "S2", "S1", "bybit_2", root=root)
    assert res["verdict"] == "FIRE", res
    assert res["proposal"]["roster_remove"] == {"bybit_2": [LEG], "bybit_portfolio": [LEG]}
    assert res["proposal"]["roster_add"] == {}


def test_proposal_never_writes(repo):
    root = repo()
    before = (root / mr.ACCOUNTS_REL).read_text()
    assert _s1s2(root)["verdict"] == "FIRE"
    assert (root / mr.ACCOUNTS_REL).read_text() == before


# ── refusals: the ones the dispatch named ──────────────────────────────────
def _refused(res, clause):
    assert res["verdict"] == "REFUSE", res
    assert res["clause"] == clause, res
    assert res["proposal"] is None


def test_refuse_missing_record(repo):
    _refused(_s1s2(repo(records={EQ_LEG: _record(EQ_LEG, EQ_CFG)})), "R-RECORD-MISSING")


def test_refuse_source_run_absent_from_repo(repo):
    rec = _record(LEG, LEG_CFG)
    root = repo(records={LEG: rec})
    (root / rec["source_run"]).unlink()
    _refused(_s1s2(root), "R-SOURCE-RUN-ABSENT")


def test_refuse_source_run_outside_the_repo(repo):
    rec = dict(_record(LEG, LEG_CFG), source_run="../../etc/passwd")
    rec_root = repo(records={LEG: rec})
    _refused(_s1s2(rec_root), "R-SOURCE-RUN-ABSENT")


def test_refuse_n_below_30(repo):
    rec = _record(LEG, LEG_CFG, n=29)
    _refused(_s1s2(repo(records={LEG: rec})), "R-N")


def test_n_of_exactly_30_passes(repo):
    rec = _record(LEG, LEG_CFG, n=30)
    assert _s1s2(repo(records={LEG: rec}))["verdict"] == "FIRE"


def test_refuse_no_fold_majority(repo):
    rec = _record(LEG, LEG_CFG, folds_net=(6.0, 5.0, -0.1, -0.2))  # 2 of 4 is not a majority
    _refused(_s1s2(repo(records={LEG: rec})), "R-FOLDS")


def test_refuse_fold_count_that_disagrees_with_fold_detail(repo):
    rec = _record(LEG, LEG_CFG, folds_net=(6.0, 5.0, -0.1, -0.2))
    rec["folds_positive"] = 3
    _refused(_s1s2(repo(records={LEG: rec})), "R-FOLDS")


def test_refuse_cap_exceeded(repo):
    # bybit_2 already carries one auto-promoted leg ("a"): adding a second makes
    # 2 of 5 = 40% > 25%.
    root = repo(firings=[{"mandate": "MD-PROMOTE-S1-S2", "action": "add", "leg": "a",
                          "accounts": ["bybit_2", "bybit_portfolio"]}])
    _refused(_s1s2(root), "R-CAP")


def test_cap_ignores_hand_placed_and_departed_legs(repo):
    # a firing for a leg no longer on the roster does not consume the cap
    root = repo(firings=[{"action": "add", "leg": "gone", "accounts": ["bybit_2"]}])
    assert _s1s2(root)["verdict"] == "FIRE"


def test_refuse_derisk_only_mandate_asked_to_add(repo):
    res = mr.resolve(LEG, "S1", "S2", "bybit_2", root=repo(), mandate_id="MD-DEMOTE-S1-OFF")
    _refused(res, "R-DERISK-ADD")


def test_refuse_demotion_that_would_add_to_the_soak_book(repo):
    # S2 -> S1 for a leg that never soaked would ADD it to bybit_1: derisk_only refuses.
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_1"]["strategies"].remove(LEG)
    accts["accounts"]["bybit_2"]["strategies"].append(LEG)
    res = mr.resolve(LEG, "S2", "S1", "bybit_2", root=repo(accounts=accts))
    _refused(res, "R-DERISK-ADD")


# ── refusals: the rest of the clauses ──────────────────────────────────────
def test_refuse_expectancy_not_positive(repo):
    rec = _record(LEG, LEG_CFG, folds_net=(1.0, 1.0, 1.0, -4.0))
    _refused(_s1s2(repo(records={LEG: rec})), "R-EXPECTANCY")


def test_refuse_expectancy_that_disagrees_with_net_over_n(repo):
    rec = _record(LEG, LEG_CFG)
    rec["expectancy_r_oos"] = 0.9
    _refused(_s1s2(repo(records={LEG: rec})), "R-EXPECTANCY")


def test_refuse_fee_only_record_via_b1_c3(repo):
    rec = _record(LEG, LEG_CFG)
    del rec["cost_stack"]
    _refused(_s1s2(repo(records={LEG: rec})), "R-B1-C3")


def test_refuse_post_hoc_rule_via_b1_c4(repo):
    rec = _record(LEG, LEG_CFG)
    rec["decision_rule"]["registered_at"] = "2026-09-25T00:00:00Z"
    _refused(_s1s2(repo(records={LEG: rec})), "R-B1-C4")


def test_refuse_stale_record_via_b1_identity(repo):
    rec = _record(LEG, dict(LEG_CFG, atr_stop_mult=2.0))
    _refused(_s1s2(repo(records={LEG: rec})), "R-B1-IDENTITY")


def test_refuse_mandate_in_proposed_only(repo):
    m = copy.deepcopy(MANDATES)
    moved = [x for x in m["mandates"] if x["id"] == "MD-PROMOTE-S1-S2"]
    m["mandates"] = [x for x in m["mandates"] if x["id"] != "MD-PROMOTE-S1-S2"]
    m["proposed"] = moved
    _refused(_s1s2(repo(mandates=m)), "R-MANDATE-NOT-GRANTED")


def test_refuse_mandate_without_a_grantor(repo):
    m = copy.deepcopy(MANDATES)
    for x in m["mandates"]:
        x.pop("granted_by")
    _refused(_s1s2(repo(mandates=m)), "R-MANDATE-NOT-GRANTED")


def test_refuse_mandate_without_a_stated_bar(repo):
    m = copy.deepcopy(MANDATES)
    for x in m["mandates"]:
        x.pop("bar")
    _refused(_s1s2(repo(mandates=m)), "R-MANDATE-NOT-GRANTED")


def test_refuse_blocked_mandate(repo):
    m = copy.deepcopy(MANDATES)
    for x in m["mandates"]:
        x["blocked_until"] = "D3 passes"
    _refused(_s1s2(repo(mandates=m)), "R-MANDATE-BLOCKED")


def test_refuse_not_soaked(repo):
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_1"]["strategies"].remove(LEG)
    _refused(_s1s2(repo(accounts=accts)), "R-NOT-SOAKED")


def test_refuse_promotion_on_a_divergent_r3_verdict(repo):
    res = _s1s2(repo(r3=_r3({LEG: "divergent"})))
    _refused(res, "R-COST-FIDELITY")
    assert "divergent" in res["detail"]
    assert res["evidence"]["cost_fidelity"]["verdict"] == "divergent"


def test_refuse_promotion_on_an_inconclusive_r3_verdict(repo):
    # The CI straddles the threshold: the measurement exists and cannot decide.
    res = _s1s2(repo(r3=_r3({LEG: "inconclusive"})))
    _refused(res, "R-COST-FIDELITY")
    assert "inconclusive" in res["detail"]


def test_insufficient_n_does_not_fire_and_consistent_does(repo):
    """The negative control: ONE field differs between these two runs.

    Without the FIRE half, a resolver that refused every promotion outright
    would pass the refusal half, which is the whole reason this is one test.
    """
    assert _s1s2(repo(r3=_r3({LEG: "consistent"})))["verdict"] == "FIRE"
    res = _s1s2(repo(r3=_r3({LEG: "insufficient_n"})))
    _refused(res, "R-COST-FIDELITY")
    assert "insufficient_n" in res["detail"]


def test_refuse_promotion_on_a_no_record_r3_verdict(repo):
    _refused(_s1s2(repo(r3=_r3({LEG: "no_record"}))), "R-COST-FIDELITY")


def test_refuse_promotion_when_the_leg_is_absent_from_the_r3_record(repo):
    # "graded and found too few fills" and "never graded" are different facts;
    # both refuse, and the detail says which.
    res = _s1s2(repo(r3=_r3({EQ_LEG: "consistent"})))
    _refused(res, "R-COST-FIDELITY")
    assert "carries no entry" in res["detail"]


def test_refuse_promotion_when_a_verdict_is_outside_the_rules_vocabulary(repo):
    res = _s1s2(repo(r3=_r3({LEG: "probably_fine"})))
    _refused(res, "R-COST-FIDELITY")
    assert "not one of" in res["detail"]


def test_refuse_promotion_when_no_r3_record_is_committed(repo):
    res = _s1s2(repo(r3=None))
    _refused(res, "R-COST-FIDELITY")
    assert mr.R3_DIR_REL in res["detail"]


def test_refuse_promotion_on_a_verdict_graded_against_a_different_record(repo):
    # The record was regenerated at 5.0 bps; R3 graded it at 3.0. The verdict
    # answers a question about a record that no longer exists.
    res = _s1s2(repo(r3=_r3({LEG: "consistent"}, modelled=3.0)))
    _refused(res, "R-COST-FIDELITY")
    assert "has since changed" in res["detail"]
    assert res["evidence"]["cost_fidelity"]["stale"]


def test_a_stale_verdict_on_a_provisional_venue_still_names_itself_in_a_caveat(repo):
    # A stale verdict folds into the same "no decisive measurement" state as a
    # missing one, so the promotion rests on the placeholder -- and SAYS so.
    res = _s1s2(repo(r3=_r3({EQ_LEG: "consistent"}, modelled=3.0)),
                leg=EQ_LEG, account="alpaca_live")
    assert res["verdict"] == "FIRE", res
    assert len(res["caveats"]) == 2 and "has since changed" in res["caveats"][1]
    assert res["evidence"]["cost_fidelity"]["stale"]


def test_a_divergent_verdict_bites_on_a_provisional_venue_too(repo):
    # The placeholder is a FALLBACK, not an exemption: it applies only while no
    # decisive verdict exists. Before this change the provisional branch
    # returned early and never looked at a measurement at all.
    res = _s1s2(repo(r3=_r3({EQ_LEG: "divergent"})), leg=EQ_LEG, account="alpaca_live")
    _refused(res, "R-COST-FIDELITY")
    assert "divergent" in res["detail"]


def test_the_newest_dated_record_decides_and_a_pull_log_is_not_a_record(repo):
    """Regression: the producer writes three files per run into this directory.

    A bare `*.json` glob sorts `<date>__fills_pull_log.json` last and picks it.
    That is what the D3 reader this replaces actually did against the committed
    tree, and because the pull log is a JSON *list* the clause then refused with
    "cannot compare" no matter what the measurement said.
    """
    root = repo(r3=_r3({LEG: "divergent"}))            # the OLDER record
    _w(root, f"{mr.R3_DIR_REL}/2026-09-26.json", _r3({LEG: "consistent"}))
    _w(root, f"{mr.R3_DIR_REL}/2026-09-27__fills_pull_log.json",
       [{"account_id": "bybit_1", "count": 1000}])     # sorts last, is not a record
    res = _s1s2(root)
    assert res["verdict"] == "FIRE", res
    assert res["evidence"]["cost_fidelity"]["record"].endswith("2026-09-26.json")


def test_refuse_equity_record_costed_below_the_placeholder(repo):
    rec = _record(EQ_LEG, EQ_CFG, slippage=2.0)
    res = _s1s2(repo(records={EQ_LEG: rec}), leg=EQ_LEG, account="alpaca_live")
    _refused(res, "R-COST-FIDELITY")
    assert res["caveats"] == [mr.PROVISIONAL_CAVEAT]  # the caveat rides refusals too


def test_refuse_wrong_stage_account(repo):
    _refused(_s1s2(repo(), account="bybit_portfolio"), "R-ACCOUNT")


def test_refuse_account_class_drift(repo):
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_2"]["account_class"] = "paper"
    _refused(_s1s2(repo(accounts=accts)), "R-ACCOUNT")


def test_refuse_unknown_transition(repo):
    _refused(mr.resolve(LEG, "S0", "S2", "bybit_2", root=repo()), "R-TRANSITION")


def test_refuse_kill_question_is_not_a_roster_transition(repo):
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo(), mandate_id="MD-KILL-QUESTION")
    _refused(res, "R-TRANSITION")


def test_refuse_no_op(repo):
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_2"]["strategies"].append(LEG)
    accts["accounts"]["bybit_portfolio"]["strategies"].append(LEG)
    _refused(_s1s2(repo(accounts=accts)), "R-NO-OP")


def test_refuse_demotion_on_a_positive_mirror_window(repo):
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_2"]["strategies"].append(LEG)
    root = repo(accounts=accts, mirror={"leg": LEG, "n_closed": 40, "net_r_net_of_full_cost": 1.0,
                                        "source_run": "comms/mandate_evidence/runs/m.jsonl"})
    _refused(mr.resolve(LEG, "S2", "S1", "bybit_2", root=root), "R-EXPECTANCY")


def test_refuse_demotion_without_a_mirror_record(repo):
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_2"]["strategies"].append(LEG)
    _refused(mr.resolve(LEG, "S2", "S1", "bybit_2", root=repo(accounts=accts)), "R-RECORD-MISSING")


def test_s1_off_fires_on_a_divergent_verdict(repo):
    fired = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo(r3=_r3({LEG: "divergent"})))
    assert fired["verdict"] == "FIRE", fired
    assert fired["proposal"]["roster_remove"] == {"bybit_1": [LEG]}
    assert fired["evidence"]["cost_fidelity"]["verdict"] == "divergent"


def test_s1_off_refuses_on_a_consistent_verdict(repo):
    _refused(mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo()), "R-COST-FIDELITY")


@pytest.mark.parametrize("verdict", ["insufficient_n", "inconclusive", "no_record"])
def test_s1_off_refuses_on_anything_that_is_not_divergent(repo, verdict):
    """The mandate's own `never:`: "could not measure" must not read as
    "diverged". Under a per-leg rule those words ARE `insufficient_n` and
    `inconclusive`, and demoting on either takes a leg off its soak book on the
    strength of a measurement that said it could not decide."""
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo(r3=_r3({LEG: verdict})))
    _refused(res, "R-COST-FIDELITY")
    assert verdict in res["detail"] and "does not support a demotion" in res["detail"]


def test_s1_off_refuses_when_there_is_no_verdict_to_read(repo):
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo(r3=None))
    _refused(res, "R-RECORD-MISSING")
    assert "not 'diverged'" in res["detail"]


def test_s1_off_refuses_on_a_stale_verdict(repo):
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1",
                     root=repo(r3=_r3({LEG: "divergent"}, modelled=3.0)))
    _refused(res, "R-COST-FIDELITY")
    assert "has since changed" in res["detail"]


def test_cli_exit_codes(repo):
    root = repo()
    assert mr.main(["--leg", LEG, "--from", "S1", "--to", "S2", "--account", "bybit_2",
                    "--root", str(root)]) == 0
    assert mr.main(["--leg", LEG, "--from", "S1", "--to", "S2", "--account", "bybit_1",
                    "--root", str(root)]) == 1


def test_the_verdict_vocabulary_matches_the_producer_that_owns_it():
    """`scripts/research/r3_cost_fidelity.py` OWNS the five verdict strings; this
    module mirrors them. A mirror with no assertion drifts silently and, worse,
    fails OPEN in one direction: a renamed verdict stops matching `R3_CONSISTENT`
    (safe, refuses) but also stops matching `R3_DIVERGENT`, so a demotion that
    should fire would quietly stop firing. Compare them instead of trusting the
    copy."""
    sys.path.insert(0, str(REPO / "scripts" / "research"))
    import r3_cost_fidelity as r3src

    assert mr.R3_VERDICTS == {r3src.CONSISTENT, r3src.DIVERGENT, r3src.INCONCLUSIVE,
                              r3src.INSUFFICIENT, r3src.NO_RECORD}
    assert (mr.R3_CONSISTENT, mr.R3_DIVERGENT) == (r3src.CONSISTENT, r3src.DIVERGENT)
    assert mr.R3_RULE_ID == r3src.RULE_ID and mr.R3_RULE_DOC == r3src.RULE_DOC


def test_the_reader_finds_a_positive_in_the_COMMITTED_r3_record():
    """RULE ONE: prove the probe can find a positive before trusting its silence.

    Every other test here grades a fixture this file wrote, so all of them would
    still pass if the committed record's shape and the reader's expectations had
    drifted apart. This one reads the real artifact. It pins the SHAPE (a dated
    record is found, a leg in it resolves to a verdict in the rule's vocabulary,
    a leg not in it does not) and deliberately NOT the verdict VALUE, which
    moves every time the measurement is re-run.
    """
    rel, doc = mr.latest_r3(REPO)
    assert rel and mr.DATED_RECORD.match(Path(rel).name), rel
    graded = sorted((doc or {}).get("per_leg") or {})
    assert graded, f"{rel} grades no legs"
    hit = mr.r3_leg_verdict(graded[0], REPO)
    assert hit["verdict"] in mr.R3_VERDICTS, hit        # the positive
    miss = mr.r3_leg_verdict("definitely_not_a_leg", REPO)
    assert miss["verdict"] is None and "carries no entry" in miss["why"]  # the negative


def test_real_repo_refuses_rather_than_crashing():
    # Against the committed tree the answer depends on what is granted; it must
    # always be a verdict with a clause, never an exception.
    res = mr.resolve(LEG, "S1", "S2", "bybit_2", root=REPO)
    assert res["verdict"] in (mr.FIRE, mr.REFUSE) and res["clause"]
