"""scripts/ops/mandate_resolver.py — every clause, across all THREE verdicts.

Each REFUSE/NEEDS_DATA test starts from the SAME passing fixture and breaks
exactly one thing, so a test that goes red names the clause that broke. The
FIRE tests are the positive controls: without them, a resolver that refused
everything would pass every REFUSE test, and a resolver that answered
NEEDS_DATA to everything would pass every NEEDS_DATA test.
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
import pipeline  # noqa: E402

LEG = "ada_pullback_2h"
LEG_CFG = {"enabled": True, "execution": "live", "timeframe": "2h",
           "symbols": ["ADAUSDT"], "atr_stop_mult": 1.5}
EQ_LEG = "spy_trend_long_1d"
EQ_CFG = {"enabled": True, "execution": "live", "timeframe": "1d", "symbols": ["SPY"]}
SLV_LEG = "slv_trend_1h"
SLV_CFG = {"enabled": True, "execution": "shadow", "timeframe": "1h", "symbols": ["SLV"]}

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

#: Committed setups per leg, in the shape build_strategy_evidence.py's
#: source_run carries. Both directions, so the side_filter population is real.
SETUPS = {
    LEG: [{"symbol": "ADAUSDT", "entry_time": f"2026-09-{d:02d} 00:00:00+00:00",
           "direction": "long" if d % 2 else "short", "entry": 0.70,
           "sl": 0.68 if d % 2 else 0.72, "confidence": 1.0} for d in range(1, 11)],
    EQ_LEG: [{"symbol": "SPY", "entry_time": f"2026-09-{d:02d} 00:00:00+00:00",
              "direction": "long" if d % 2 else "short", "entry": 500.0,
              "sl": 490.0 if d % 2 else 510.0, "confidence": 1.0} for d in range(1, 11)],
}


def _snapshot(account, equity, *, age_days=0.0, buying_power=None):
    from datetime import datetime, timedelta, timezone
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {"account_id": account, "captured_at": ts.isoformat(), "equity_usd": equity,
            "buying_power_usd": equity if buying_power is None else buying_power,
            "source": "test"}


#: Default: both Stage-2 accounts richly funded, so R-AFFORD passes and every
#: pre-existing test keeps meaning what it meant. Affordability tests override.
SNAPSHOTS = {"bybit_2": 100_000.0, "alpaca_live": 100_000.0}


def _w(root: Path, rel: str, data, as_yaml=False):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data) if as_yaml else json.dumps(data), encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    def build(mandates=None, accounts=None, records=None, r3=..., firings=(), mirror=None,
              snapshots=None, setups=None):
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
                rows = (setups or SETUPS).get(leg, [])
                (tmp_path / rec["source_run"]).write_text(
                    "".join(json.dumps(r) + "\n" for r in rows) or "{}\n")
        for acct, snap in (SNAPSHOTS if snapshots is None else snapshots).items():
            _w(tmp_path, f"{mr.ACCOUNT_SNAPSHOT_DIR_REL}/{acct}.json",
               snap if isinstance(snap, dict) else _snapshot(acct, snap))
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


def test_equity_leg_with_no_decisive_verdict_is_needs_data_not_fire(repo):
    # ⚠️ CORRECTED 2026-09-29 (operator directive, PR #13698): before this
    # change, a provisional-venue leg with an `insufficient_n` R3 verdict
    # FIRED, carrying two caveats instead of blocking. That is the exact shape
    # of the #13698 near-miss (slv_trend_1h, alpaca_equities, insufficient_n)
    # -- a caveat is not a substitute for a decision. It must be NEEDS_DATA.
    res = _s1s2(repo(), leg=EQ_LEG, account="alpaca_live")
    _needs_data(res, "R-COST-FIDELITY")
    assert "insufficient_n" in res["detail"]
    assert res["evidence"]["cost_fidelity"]["basis_of_comparison"] == "PROVISIONAL"
    # The provisional-venue caveat still rides the result -- it is set in
    # `_decide()` before the cost-fidelity clause runs -- but it is no longer
    # the ONLY thing standing between this leg and real money.
    assert res["caveats"] == [mr.PROVISIONAL_CAVEAT]
    assert "insufficient_n" in res["data_task"]["what"]


def test_fire_s0_to_s1(repo):
    root = repo(accounts={"accounts": {**ACCOUNTS["accounts"],
                                       "bybit_1": _acct("paper", "bybit", ["l1", "l2", "l3"])}})
    res = mr.resolve(LEG, "S0", "S1", "bybit_1", root=root)
    assert res["verdict"] == "FIRE", res
    assert res["proposal"]["roster_add"] == {"bybit_1": [LEG]}


def _stage2_accts():
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_2"]["strategies"].append(LEG)
    accts["accounts"]["bybit_portfolio"]["strategies"].append(LEG)
    return accts


def _t3_mirror(root, windows=(-6.0, -6.0), stage0=(1.0, -0.8, 0.6, -1.0, 1.2), **over):
    """Give LEG a Stage-0 per-trade stream and write the T3 mirror-window record
    the gate would write for `windows` (oldest first)."""
    src = (mr._json(root / f"{mr.EVIDENCE_DIR_REL}/{LEG}.json") or {})["source_run"]
    (root / src).write_text("".join(json.dumps({"net_r": x}) + "\n" for x in stage0))
    p = mr.stage0_block_p10(LEG, root)
    rec = {"leg": LEG, "rule": mr.T3_RULE_ID, "n_closed": 40,
           "net_r_net_of_full_cost": round(sum(windows), 4),
           "windows": [{"n_closed": 20, "net_r": w} for w in windows],
           "p10_threshold": p["p10"], "bootstrap_seed": mr.T3_BOOTSTRAP_SEED,
           "evidence_record": p["evidence_record"],
           "source_run": "comms/mandate_evidence/runs/m.jsonl", **over}
    _w(root, f"{mr.MIRROR_DIR_REL}/{LEG}.json", rec)
    (root / rec["source_run"]).parent.mkdir(parents=True, exist_ok=True)
    (root / rec["source_run"]).write_text("{}\n")
    return p


def test_fire_demote_s2_to_s1_when_both_t3_windows_are_below_p10(repo):
    root = repo(accounts=_stage2_accts())
    p = _t3_mirror(root)
    assert -6.0 < p["p10"] < 0, p
    res = mr.resolve(LEG, "S2", "S1", "bybit_2", root=root)
    assert res["verdict"] == "FIRE", res
    assert res["proposal"]["roster_remove"] == {"bybit_2": [LEG], "bybit_portfolio": [LEG]}
    assert res["proposal"]["roster_add"] == {}
    assert res["evidence"]["t3_p10"] == p["p10"]


@pytest.mark.parametrize("windows,clause", [
    ((+2.0, -6.0), "R-T3"),              # only one window bad: T3 not met
    ((-1.0, -6.0), "R-T3"),              # both negative, one above p10
    ((-6.0, +7.0), "R-EXPECTANCY"),      # 40-trade net non-negative: never-clause
])
def test_refuse_demotion_when_t3_is_not_met(repo, windows, clause):
    root = repo(accounts=_stage2_accts())
    _t3_mirror(root, windows=windows)
    _refused(mr.resolve(LEG, "S2", "S1", "bybit_2", root=root), clause)


def test_positive_p10_never_fires_on_a_positive_window(repo):
    root = repo(accounts=_stage2_accts())
    p = _t3_mirror(root, windows=(0.5, 0.5), stage0=(1.5, 0.9, 1.1, -0.2, 1.3))
    assert p["p10"] > 1.0
    _refused(mr.resolve(LEG, "S2", "S1", "bybit_2", root=root), "R-EXPECTANCY")


def test_needs_data_on_a_pre_t3_record_or_short_windows_or_no_stage0(repo):
    root = repo(accounts=_stage2_accts())
    _t3_mirror(root, rule=None)
    res = mr.resolve(LEG, "S2", "S1", "bybit_2", root=root)
    assert res["verdict"] == mr.NEEDS_DATA and res["clause"] == "R-T3-WINDOWS"
    _t3_mirror(root, n_closed=39)
    res = mr.resolve(LEG, "S2", "S1", "bybit_2", root=root)
    assert res["verdict"] == mr.NEEDS_DATA and res["clause"] == "R-N"
    _t3_mirror(root, stage0=(0.4,))
    res = mr.resolve(LEG, "S2", "S1", "bybit_2", root=root)
    assert res["verdict"] == mr.NEEDS_DATA and res["clause"] == "R-T3-NO-EVIDENCE"
    assert res["data_task"]["what"]


def test_refuse_a_record_that_contradicts_itself_or_its_threshold(repo):
    root = repo(accounts=_stage2_accts())
    _t3_mirror(root, net_r_net_of_full_cost=-20.0)
    _refused(mr.resolve(LEG, "S2", "S1", "bybit_2", root=root), "R-T3-WINDOWS")
    _t3_mirror(root, p10_threshold=5.0)
    _refused(mr.resolve(LEG, "S2", "S1", "bybit_2", root=root), "R-T3-THRESHOLD")


def test_stage0_p10_is_deterministic_and_seeded(repo):
    root = repo(accounts=_stage2_accts())
    _t3_mirror(root)
    a, b = mr.stage0_block_p10(LEG, root), mr.stage0_block_p10(LEG, root)
    assert a == b and a["seed"] == mr.T3_BOOTSTRAP_SEED
    assert mr.stage0_block_p10(LEG, root, seed=1)["p10"] != a["p10"]


def test_real_stage2_thresholds_match_the_operators_pricing():
    """The operator was told xrp_4h, ief and iaum have POSITIVE p10 and the
    other Stage-2 legs negative (SIGNAL-0005). Pin that on the real records."""
    for leg, positive in (("trend_donchian_xrp_4h", True), ("ief_pullback_1d", True),
                          ("iaum_pullback_1d", True), ("xrp_pullback_2h", False),
                          ("ada_pullback_2h", False), ("trend_donchian_eth_4h", False),
                          ("slv_pullback_1d", False)):
        p = mr.stage0_block_p10(leg, REPO)
        assert p["p10"] is not None, (leg, p["why"])
        assert (p["p10"] > 0) == positive, (leg, p["p10"])


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
    assert res["data_task"] is None, res  # collapsed-state check: never both


def _needs_data(res, clause):
    assert res["verdict"] == mr.NEEDS_DATA, res
    assert res["clause"] == clause, res
    assert res["proposal"] is None
    task = res["data_task"]
    assert isinstance(task, dict) and task.get("clears_when") and task.get("what"), res
    assert task.get("next_action") in pipeline.NEXT_ACTIONS, res


def test_needs_data_missing_record(repo):
    # No Stage-0 backtest exists for this leg at all -- that is an absent
    # measurement, not a decisive one, so it is NEEDS_DATA, never REFUSE.
    _needs_data(_s1s2(repo(records={EQ_LEG: _record(EQ_LEG, EQ_CFG)})), "R-RECORD-MISSING")


def test_refuse_source_run_absent_from_repo(repo):
    rec = _record(LEG, LEG_CFG)
    root = repo(records={LEG: rec})
    (root / rec["source_run"]).unlink()
    _refused(_s1s2(root), "R-SOURCE-RUN-ABSENT")


def test_refuse_source_run_outside_the_repo(repo):
    rec = dict(_record(LEG, LEG_CFG), source_run="../../etc/passwd")
    rec_root = repo(records={LEG: rec})
    _refused(_s1s2(rec_root), "R-SOURCE-RUN-ABSENT")


def test_needs_data_n_below_30(repo):
    # Below the mandate's own floor is "not enough closed trades yet", the
    # same shape as R3's insufficient_n -- NEEDS_DATA, never REFUSE.
    rec = _record(LEG, LEG_CFG, n=29)
    _needs_data(_s1s2(repo(records={LEG: rec})), "R-N")


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


def test_needs_data_not_soaked(repo):
    # Never been on the Stage-1 soak roster at all -- absent evidence, not
    # decisive evidence.
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_1"]["strategies"].remove(LEG)
    _needs_data(_s1s2(repo(accounts=accts)), "R-NOT-SOAKED")


def test_needs_data_execution_shadow(repo):
    # #13698: ON the Stage-1 soak roster, but `execution: shadow` -- it logs
    # order packages and places no real order, so roster membership there is
    # not a soak. Must NEVER fire on this alone.
    root = repo()
    strat = yaml.safe_load((root / mr.STRATEGIES_REL).read_text())
    strat["strategies"][LEG]["execution"] = "shadow"
    (root / mr.STRATEGIES_REL).write_text(yaml.safe_dump(strat))
    res = _s1s2(root)
    _needs_data(res, "R-EXECUTION-SHADOW")
    assert "shadow" in res["detail"]


def test_refuse_promotion_on_a_divergent_r3_verdict(repo):
    res = _s1s2(repo(r3=_r3({LEG: "divergent"})))
    _refused(res, "R-COST-FIDELITY")
    assert "divergent" in res["detail"]
    assert res["evidence"]["cost_fidelity"]["verdict"] == "divergent"


def test_needs_data_promotion_on_an_inconclusive_r3_verdict(repo):
    # The CI straddles the threshold: the measurement exists and cannot decide
    # -- that is an absent decision, never a refusal.
    res = _s1s2(repo(r3=_r3({LEG: "inconclusive"})))
    _needs_data(res, "R-COST-FIDELITY")
    assert "inconclusive" in res["detail"]


def test_insufficient_n_needs_data_and_consistent_fires(repo):
    """The negative control: ONE field differs between these two runs.

    Without the FIRE half, a resolver that answered NEEDS_DATA to every
    promotion would pass the NEEDS_DATA half, which is the whole reason this
    is one test. This is also the directive's headline correction: before
    2026-09-29 `insufficient_n` FIRED on a provisional venue (see the caveat
    tests below); it must never fire on ANY venue.
    """
    assert _s1s2(repo(r3=_r3({LEG: "consistent"})))["verdict"] == "FIRE"
    res = _s1s2(repo(r3=_r3({LEG: "insufficient_n"})))
    _needs_data(res, "R-COST-FIDELITY")
    assert "insufficient_n" in res["detail"]


def test_needs_data_promotion_on_a_no_record_r3_verdict(repo):
    _needs_data(_s1s2(repo(r3=_r3({LEG: "no_record"}))), "R-COST-FIDELITY")


def test_needs_data_promotion_when_the_leg_is_absent_from_the_r3_record(repo):
    # "graded and found too few fills" and "never graded" are different facts;
    # neither is decisive, and the detail says which.
    res = _s1s2(repo(r3=_r3({EQ_LEG: "consistent"})))
    _needs_data(res, "R-COST-FIDELITY")
    assert "carries no entry" in res["detail"]


def test_needs_data_promotion_when_a_verdict_is_outside_the_rules_vocabulary(repo):
    res = _s1s2(repo(r3=_r3({LEG: "probably_fine"})))
    _needs_data(res, "R-COST-FIDELITY")
    assert "not one of" in res["detail"]


def test_needs_data_promotion_when_no_r3_record_is_committed(repo):
    res = _s1s2(repo(r3=None))
    _needs_data(res, "R-COST-FIDELITY")
    assert mr.R3_DIR_REL in res["detail"]


def test_needs_data_promotion_on_a_verdict_graded_against_a_different_record(repo):
    # The record was regenerated at 5.0 bps; R3 graded it at 3.0. The verdict
    # answers a question about a record that no longer exists -- not decisive.
    res = _s1s2(repo(r3=_r3({LEG: "consistent"}, modelled=3.0)))
    _needs_data(res, "R-COST-FIDELITY")
    assert "has since changed" in res["detail"]
    assert res["evidence"]["cost_fidelity"]["stale"]


def test_a_stale_verdict_on_a_provisional_venue_is_needs_data_not_fire(repo):
    # ⚠️ CORRECTED 2026-09-29 (operator directive, PR #13698): this used to
    # assert FIRE-with-a-caveat. A stale verdict is "no decisive measurement",
    # exactly like insufficient_n, and it must never fire either.
    res = _s1s2(repo(r3=_r3({EQ_LEG: "consistent"}, modelled=3.0)),
                leg=EQ_LEG, account="alpaca_live")
    _needs_data(res, "R-COST-FIDELITY")
    assert "has since changed" in res["detail"]
    assert res["evidence"]["cost_fidelity"]["stale"]
    # The provisional-venue caveat is set unconditionally in `_decide()` before
    # the cost-fidelity clause runs, so it still rides a NEEDS_DATA result.
    assert res["caveats"] == [mr.PROVISIONAL_CAVEAT]


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


def test_needs_data_demotion_without_a_mirror_record(repo):
    # The mirror window has not accrued yet -- absent evidence, not decisive.
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_2"]["strategies"].append(LEG)
    res = mr.resolve(LEG, "S2", "S1", "bybit_2", root=repo(accounts=accts))
    _needs_data(res, "R-RECORD-MISSING")


def test_s1_off_fires_on_a_divergent_verdict(repo):
    fired = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo(r3=_r3({LEG: "divergent"})))
    assert fired["verdict"] == "FIRE", fired
    assert fired["proposal"]["roster_remove"] == {"bybit_1": [LEG]}
    assert fired["evidence"]["cost_fidelity"]["verdict"] == "divergent"


def test_s1_off_refuses_on_a_consistent_verdict(repo):
    # `consistent` is DECISIVE: realized cost matches what was modelled, so
    # the evidence affirmatively does not support a demotion. REFUSE, not
    # NEEDS_DATA.
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo())
    _refused(res, "R-COST-FIDELITY")
    assert "consistent" in res["detail"]


@pytest.mark.parametrize("verdict", ["insufficient_n", "inconclusive", "no_record"])
def test_s1_off_needs_data_on_anything_that_is_not_divergent_or_consistent(repo, verdict):
    """The mandate's own `never:`: "could not measure" must not read as
    "diverged" -- and, symmetrically, must not read as "does not diverge"
    either. Under a per-leg rule "could not measure" IS `insufficient_n` /
    `inconclusive` / `no_record`, and DECIDING either way on it takes a leg
    off (or keeps it on) its soak book on the strength of a measurement that
    said it could not decide. NEEDS_DATA, never REFUSE or FIRE."""
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo(r3=_r3({LEG: verdict})))
    _needs_data(res, "R-COST-FIDELITY")
    assert verdict in res["detail"]


def test_s1_off_needs_data_when_there_is_no_verdict_to_read(repo):
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1", root=repo(r3=None))
    _needs_data(res, "R-RECORD-MISSING")
    assert "not 'diverged'" in res["detail"]


def test_s1_off_needs_data_on_a_stale_verdict(repo):
    res = mr.resolve(LEG, "S1", "OFF", "bybit_1",
                     root=repo(r3=_r3({LEG: "divergent"}, modelled=3.0)))
    _needs_data(res, "R-COST-FIDELITY")
    assert "has since changed" in res["detail"]


def test_cli_exit_codes(repo):
    root = repo()
    assert mr.main(["--leg", LEG, "--from", "S1", "--to", "S2", "--account", "bybit_2",
                    "--root", str(root)]) == 0
    assert mr.main(["--leg", LEG, "--from", "S1", "--to", "S2", "--account", "bybit_1",
                    "--root", str(root)]) == 1
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["bybit_1"]["strategies"].remove(LEG)
    root2 = repo(accounts=accts)
    assert mr.main(["--leg", LEG, "--from", "S1", "--to", "S2", "--account", "bybit_2",
                    "--root", str(root2)]) == 3


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
    assert res["verdict"] in (mr.FIRE, mr.REFUSE, mr.NEEDS_DATA) and res["clause"]
    # collapsed-state check on the real tree, not just the fixtures: data_task
    # is present-and-a-dict on NEEDS_DATA only, never on the other two.
    if res["verdict"] == mr.NEEDS_DATA:
        assert isinstance(res["data_task"], dict)
    else:
        assert res["data_task"] is None


# ── #13698: the real regression this change was written for ────────────────
def test_real_slv_trend_1h_never_fires_while_execution_is_shadow():
    """Regression pin, read from the ACTUAL committed tree, not a fixture.

    This session ran the PRE-FIX resolver against `slv_trend_1h -> alpaca_live`
    and it answered FIRE: the leg is `execution: shadow` (zero real exits ever)
    and R3 grades its Stage-1 cost fidelity `insufficient_n`, resting the whole
    promotion on the provisional placeholder. This is exactly what PR #13698
    exposed. The premise is asserted explicitly so this test fails LOUDLY,
    by name, the day it stops describing the real leg -- a silently-vacuous
    pin is worse than no pin (RULE ONE: prove the probe can find a positive).
    """
    strategies = (mr._yaml(REPO, mr.STRATEGIES_REL) or {}).get("strategies") or {}
    cfg = strategies.get("slv_trend_1h") or {}
    assert str(cfg.get("execution", "live")).strip().lower() == "shadow", (
        "slv_trend_1h is no longer execution: shadow on main -- this pin's "
        "premise has changed; re-verify #13698 is actually resolved before "
        "updating or retiring this test")
    res = mr.resolve("slv_trend_1h", "S1", "S2", "alpaca_live", root=REPO)
    assert res["verdict"] == mr.NEEDS_DATA, res
    assert res["clause"] == "R-EXECUTION-SHADOW", res
    assert res["verdict"] != mr.FIRE


def test_the_slv_shape_reproduced_in_a_fixture(repo):
    """The same case as above, but hermetic -- pinned to a fixture rather than
    to live repo state, so it survives whatever happens to the real roster."""
    root = repo(records={LEG: _record(LEG, LEG_CFG), EQ_LEG: _record(EQ_LEG, EQ_CFG),
                         SLV_LEG: _record(SLV_LEG, SLV_CFG)})
    strat = yaml.safe_load((root / mr.STRATEGIES_REL).read_text())
    strat["strategies"][SLV_LEG] = SLV_CFG
    (root / mr.STRATEGIES_REL).write_text(yaml.safe_dump(strat))
    accts = yaml.safe_load((root / mr.ACCOUNTS_REL).read_text())
    accts["accounts"]["alpaca_paper"]["strategies"].append(SLV_LEG)
    (root / mr.ACCOUNTS_REL).write_text(yaml.safe_dump(accts))
    res = mr.resolve(SLV_LEG, "S1", "S2", "alpaca_live", root=root)
    _needs_data(res, "R-EXECUTION-SHADOW")


# ── needs_data_pipeline_item() / file_needs_data() ──────────────────────────
def test_needs_data_pipeline_item_validates(repo):
    res = _s1s2(repo(), leg=EQ_LEG, account="alpaca_live")  # insufficient_n
    assert res["verdict"] == mr.NEEDS_DATA
    item = mr.needs_data_pipeline_item(res, "session_TESTFIXTURE")
    pipeline.validate(item)  # raises PipelineError if malformed
    assert item["due_when"]["kind"] == "observation"
    assert "insufficient_n" in item["what"]
    assert res["clause"] in item["what"] and res["mandate"] in item["what"]
    assert item["origin"]["rerun"].startswith("python3 scripts/ops/mandate_resolver.py")
    assert item["state"] == "queued"


def test_needs_data_pipeline_item_refuses_a_fire_result(repo):
    res = _s1s2(repo())
    assert res["verdict"] == mr.FIRE
    with pytest.raises(ValueError, match="not a NEEDS_DATA result"):
        mr.needs_data_pipeline_item(res, "session_TESTFIXTURE")


def test_file_needs_data_appends_a_readable_item(tmp_path, repo):
    res = _s1s2(repo(accounts={**ACCOUNTS, "accounts": {
        **ACCOUNTS["accounts"],
        "bybit_1": _acct("paper", "bybit", ["l1", "l2"]),  # LEG removed -> not soaked
    }}))
    _needs_data(res, "R-NOT-SOAKED")
    store = tmp_path / "pipeline"
    filed = mr.file_needs_data(res, "session_TESTFIXTURE", store=store)
    log = pipeline.read_log(store)
    assert filed["id"] in log.items
    assert log.items[filed["id"]]["what"] == filed["what"]


# ── R-AFFORD: can the Stage-2 account size the leg at all? ──────────────────
# PI-20260928-E8Y3BGBS-0003. On 2026-09-28 the armed MD-PROMOTE-S1-S2 FIREd
# gld_pullback_1h onto alpaca_live, a ~$194 whole-share cash book, with one GLD
# share at ~$393. The clause re-sizes the leg's committed setups through the
# live sizer (RiskManager.position_size) at the account's measured equity.

GLD_LIKE = {EQ_LEG: [{"symbol": "GLD", "entry_time": f"2026-09-{d:02d} 17:00:00+00:00",
                      "direction": "long" if d % 2 else "short", "entry": 393.01,
                      "sl": 390.23 if d % 2 else 395.79, "confidence": 1.0}
                     for d in range(1, 11)]}
EQ_R3 = {EQ_LEG: {"verdict": "consistent", "from_account": "alpaca_live", "basis": "market"}}


def test_afford_refuses_a_leg_whose_one_share_exceeds_the_account(repo):
    """The 2026-09-28 fire, reproduced: FIRE on a funded book (control), REFUSE
    at the $193.56 measured on alpaca_live 2026-09-30T05:36Z. One field differs."""
    ok = _s1s2(repo(setups=GLD_LIKE, r3=_r3(EQ_R3)), leg=EQ_LEG, account="alpaca_live")
    assert ok["verdict"] == "FIRE", ok
    assert ok["evidence"]["affordability"]["setups_sized"] == 10
    res = _s1s2(repo(setups=GLD_LIKE, r3=_r3(EQ_R3),
                     snapshots={"bybit_2": 100_000.0, "alpaca_live": 193.56}),
                leg=EQ_LEG, account="alpaca_live")
    _refused(res, "R-AFFORD")
    assert "0 of 10" in res["detail"] and "whole-unit" in res["detail"]
    assert res["evidence"]["affordability"]["ref_price"] == 393.01


def test_afford_is_decided_before_cost_fidelity(repo):
    """An unaffordable leg must not come back NEEDS_DATA "collect more fills"."""
    res = _s1s2(repo(setups=GLD_LIKE, r3=_r3({EQ_LEG: "insufficient_n"}),
                     snapshots={"bybit_2": 100_000.0, "alpaca_live": 193.56}),
                leg=EQ_LEG, account="alpaca_live")
    _refused(res, "R-AFFORD")


def test_afford_counts_only_directions_the_account_permits(repo):
    """side_filter: long on alpaca_live -- an all-short leg never trades there."""
    accts = copy.deepcopy(ACCOUNTS)
    accts["accounts"]["alpaca_live"]["side_filter"] = "long"
    shorts = {EQ_LEG: [dict(r, direction="short", sl=510.0) for r in SETUPS[EQ_LEG]]}
    res = _s1s2(repo(accounts=accts, setups=shorts, r3=_r3(EQ_R3)),
                leg=EQ_LEG, account="alpaca_live")
    _refused(res, "R-AFFORD")
    assert "side_filter:long" in res["detail"]
    mixed = _s1s2(repo(accounts=accts, r3=_r3(EQ_R3)), leg=EQ_LEG, account="alpaca_live")
    assert mixed["verdict"] == "FIRE", mixed
    assert mixed["evidence"]["affordability"]["setups_permitted"] == 5


def test_afford_reprices_old_setups_to_the_latest_committed_price(repo):
    """A cheap 2025 setup is not evidence the leg is affordable today."""
    old_cheap = [dict(r, entry=150.0, sl=148.0 if r["direction"] == "long" else 152.0,
                      entry_time="2025-01-01 00:00:00+00:00") for r in GLD_LIKE[EQ_LEG][:-1]]
    rows = {EQ_LEG: old_cheap + GLD_LIKE[EQ_LEG][-1:]}
    res = _s1s2(repo(setups=rows, r3=_r3(EQ_R3),
                     snapshots={"bybit_2": 100_000.0, "alpaca_live": 193.56}),
                leg=EQ_LEG, account="alpaca_live")
    _refused(res, "R-AFFORD")


@pytest.mark.parametrize("snap", [
    None,                                              # never measured
    {"account_id": "alpaca_live", "captured_at": "2026-01-01T00:00:00+00:00",
     "equity_usd": 1e6},                               # stale
    {"account_id": "alpaca_live", "captured_at": None, "equity_usd": 1e6},
    {"account_id": "bybit_2", "captured_at": "2099-01-01T00:00:00+00:00",
     "equity_usd": 1e6},                               # another account's reading
])
def test_afford_needs_data_when_equity_is_not_measured(repo, snap):
    snaps = {"bybit_2": 100_000.0}
    if snap is not None:
        snaps["alpaca_live"] = snap
    res = _s1s2(repo(setups=GLD_LIKE, r3=_r3(EQ_R3), snapshots=snaps),
                leg=EQ_LEG, account="alpaca_live")
    _needs_data(res, "R-AFFORD")
    assert "broker_account_status?account_id=alpaca_live" in res["data_task"]["what"]


def test_afford_on_the_real_committed_gld_record(tmp_path):
    """The real 2026-09-28 gld_pullback_1h record, source_run, mandates and
    accounts from THIS tree, with alpaca_live at its measured $193.56 (fresh
    timestamp, so the test does not rot as the committed snapshot ages)."""
    import shutil
    leg = "gld_pullback_1h"
    rec = json.loads((REPO / mr.EVIDENCE_DIR_REL / f"{leg}.json").read_text())
    for rel in (mr.MANDATES_REL, mr.ACCOUNTS_REL, mr.STRATEGIES_REL,
                f"{mr.EVIDENCE_DIR_REL}/{leg}.json", rec["source_run"]):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    _w(tmp_path, f"{mr.ACCOUNT_SNAPSHOT_DIR_REL}/alpaca_live.json",
       _snapshot("alpaca_live", 193.56))
    res = mr.resolve(leg, "S1", "S2", "alpaca_live", root=tmp_path)
    _refused(res, "R-AFFORD")
    aff = res["evidence"]["affordability"]
    assert aff["setups_sized"] == 0 and aff["setups_permitted"] > 0
    assert aff["ref_price"] > 193.56


def test_snapshot_writer_round_trips_the_diag_shape(tmp_path):
    import snapshot_account_for_mandate as snapw
    diag = {"captured_at": "2026-09-30T05:36:08.978715+00:00",
            "requested_account_id": "alpaca_live",
            "accounts": [{"account_id": "alpaca_live", "exchange": "alpaca", "error": None,
                          "status_flags": {"shorting_enabled": False, "capacity": {
                              "multiplier": "1", "buying_power": "193.56",
                              "cash": "193.56", "equity": "193.56"}}}]}
    snap = snapw.build(diag, "alpaca_live")
    assert snap["equity_usd"] == 193.56 and snap["buying_power_usd"] == 193.56
    assert snap["captured_at"] == diag["captured_at"]
    _w(tmp_path, f"{mr.ACCOUNT_SNAPSHOT_DIR_REL}/alpaca_live.json", snap)
    assert mr._json(tmp_path / mr.ACCOUNT_SNAPSHOT_DIR_REL / "alpaca_live.json") == snap
    bad = copy.deepcopy(diag)
    bad["accounts"][0]["status_flags"]["capacity"] = {}
    bad["accounts"][0]["available_margin"] = {"could_not_look": True}
    with pytest.raises(SystemExit):
        snapw.build(bad, "alpaca_live")   # never writes a number it did not read


def test_afford_rejects_a_future_dated_snapshot(repo):
    """A captured_at ahead of now would have a negative age and never go stale."""
    snap = _snapshot("alpaca_live", 1e6, age_days=-2)
    res = _s1s2(repo(setups=GLD_LIKE, r3=_r3(EQ_R3),
                     snapshots={"bybit_2": 100_000.0, "alpaca_live": snap}),
                leg=EQ_LEG, account="alpaca_live")
    _needs_data(res, "R-AFFORD")
    assert "future" in res["detail"]
    # Within the 1h skew allowance it is still a reading (positive control).
    ok = _s1s2(repo(setups=GLD_LIKE, r3=_r3(EQ_R3),
                    snapshots={"bybit_2": 100_000.0,
                               "alpaca_live": _snapshot("alpaca_live", 1e6, age_days=-0.5 / 24)}),
               leg=EQ_LEG, account="alpaca_live")
    assert ok["verdict"] == "FIRE", ok


@pytest.mark.parametrize("field", ["equity_usd", "buying_power_usd"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_afford_rejects_non_finite_equity(repo, field, bad):
    """json accepts NaN/Infinity; an infinite equity would size anything."""
    snap = _snapshot("alpaca_live", 1e6)
    snap[field] = bad
    res = _s1s2(repo(setups=GLD_LIKE, r3=_r3(EQ_R3),
                     snapshots={"bybit_2": 100_000.0, "alpaca_live": snap}),
                leg=EQ_LEG, account="alpaca_live")
    _needs_data(res, "R-AFFORD")


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_afford_needs_data_on_a_non_finite_setup_confidence(repo, bad):
    """PI-20260930-MGR01HYQ-0001: an unreadable confidence used to default to
    1.0 and size at full risk. Present-but-non-finite is NEEDS_DATA; a MISSING
    confidence keeps the documented 1.0 default (positive control)."""
    rows = {EQ_LEG: [dict(r, confidence=bad) for r in GLD_LIKE[EQ_LEG]]}
    res = _s1s2(repo(setups=rows, r3=_r3(EQ_R3)), leg=EQ_LEG, account="alpaca_live")
    _needs_data(res, "R-AFFORD")
    assert "confidence" in res["detail"]
    missing = {EQ_LEG: [{k: v for k, v in r.items() if k != "confidence"}
                        for r in GLD_LIKE[EQ_LEG]]}
    ok = _s1s2(repo(setups=missing, r3=_r3(EQ_R3)), leg=EQ_LEG, account="alpaca_live")
    assert ok["verdict"] == "FIRE", ok


# ── MD-S1-CUT-ON-STAGE0-FAIL ────────────────────────────────────────────────
# Same discipline as above: one passing (FIRE) fixture, each test breaks one thing.
CUT_ID = "MD-S1-CUT-ON-STAGE0-FAIL"
CUT_LEG = "eth_pullback_2h"
CUT_CFG = {"enabled": True, "execution": "live", "timeframe": "2h", "symbols": ["ETHUSDT"]}
CUT_BAR = {"min_n_closed": 30, "expectancy_gt": 0, "fold_majority": True, "affordability": True,
           "soak_states": ["ready", "overdue"], "soak_snapshot_max_age_days": 7}


def _cut_mandates(**over):
    m = {"id": CUT_ID, "grants": "x", "direction": "derisk_only", "granted_by": "operator",
         "granted_at": "2026-10-04", "scope_accounts": ["bybit_1", "alpaca_paper"],
         "bar": dict(CUT_BAR)}
    m.update(over)
    return {"mandates": MANDATES["mandates"] + [m], "proposed": []}


def _cut_accounts(extra_on=None):
    a = copy.deepcopy(ACCOUNTS)
    a["accounts"]["bybit_1"]["strategies"].append(CUT_LEG)
    if extra_on:
        a["accounts"][extra_on]["strategies"].append(CUT_LEG)
    return a


def _soak_snap(state="ready", *, age_days=0.0, leg=CUT_LEG, account="bybit_1"):
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    return {"captured_at": ts, "source": "test",
            "soaks": [{"id": f"SOAK-stage1-{account}-{leg}", "leg": leg, "state": state,
                       "progress": "38/10 closes"}]}


@pytest.fixture
def cut_repo(repo):
    def build(*, record=None, mandates=None, accounts=None, soak=..., execution="live",
              snapshots=None):
        root = repo(mandates=mandates or _cut_mandates(), accounts=accounts or _cut_accounts(),
                    records={LEG: _record(LEG, LEG_CFG), EQ_LEG: _record(EQ_LEG, EQ_CFG)},
                    snapshots=snapshots)
        cfg = dict(CUT_CFG, execution=execution)
        _w(root, mr.STRATEGIES_REL, {"strategies": {LEG: LEG_CFG, EQ_LEG: EQ_CFG, CUT_LEG: cfg}},
           as_yaml=True)
        rec = record if record is not None else _record(
            CUT_LEG, CUT_CFG, n=62, folds_net=(-1.0, -0.5, 0.9, -1.1))
        _w(root, f"{mr.EVIDENCE_DIR_REL}/{CUT_LEG}.json", rec)
        (root / rec["source_run"]).parent.mkdir(parents=True, exist_ok=True)
        (root / rec["source_run"]).write_text("{}\n")
        if soak is ...:
            soak = _soak_snap()
        if soak is not None:
            _w(root, f"{mr.SOAK_SNAPSHOT_DIR_REL}/2026-10-04.json", soak)
        return root
    return build


def _cut(root, account="bybit_1", leg=CUT_LEG):
    return mr.resolve(leg, "S1", "OFF", account, root=root, mandate_id=CUT_ID)


def test_cut_fires_on_a_failing_record_after_a_complete_soak(cut_repo):
    res = _cut(cut_repo())
    assert res["verdict"] == "FIRE", res
    assert res["proposal"]["roster_remove"] == {"bybit_1": [CUT_LEG]}
    assert res["proposal"]["roster_add"] == {}
    clauses = {f["clause"] for f in res["evidence"]["stage0_failures"]}
    assert clauses == {"R-CUT-EXPECTANCY", "R-CUT-FOLDS"}
    assert res["evidence"]["soak"]["state"] == "ready"


def test_cut_fires_on_an_overdue_soak(cut_repo):
    assert _cut(cut_repo(soak=_soak_snap("overdue")))["verdict"] == "FIRE"


def test_cut_fires_on_folds_alone_and_on_n_alone(cut_repo):
    folds = _record(CUT_LEG, CUT_CFG, n=62, folds_net=(3.0, 2.0, -0.5, -0.4))  # 2/4, net > 0
    res = _cut(cut_repo(record=folds))
    assert res["verdict"] == "FIRE" and \
        [f["clause"] for f in res["evidence"]["stage0_failures"]] == ["R-CUT-FOLDS"]
    small = _record(CUT_LEG, CUT_CFG, n=18)  # passing record, n below the floor
    res = _cut(cut_repo(record=small))
    assert res["verdict"] == "FIRE" and \
        [f["clause"] for f in res["evidence"]["stage0_failures"]] == ["R-CUT-N"]


def test_cut_fires_on_a_failing_registered_rule(cut_repo):
    rec = _record(CUT_LEG, CUT_CFG, n=62)
    rec["decision_rule"]["verdict"] = "fail"
    res = _cut(cut_repo(record=rec))
    assert res["verdict"] == "FIRE"
    assert [f["clause"] for f in res["evidence"]["stage0_failures"]] == ["R-CUT-RULE"]


def test_cut_refuses_when_the_record_passes(cut_repo):
    # A passing record whose committed setups size on the Stage-2 account.
    rec = _record(CUT_LEG, CUT_CFG, n=62)
    root = cut_repo(record=rec)
    (root / rec["source_run"]).write_text(json.dumps(
        {"symbol": "ETHUSDT", "entry_time": "2026-09-01 00:00:00+00:00", "direction": "long",
         "entry": 4000.0, "sl": 3900.0, "confidence": 1.0}) + "\n")
    _refused(_cut(root), "R-STAGE0-PASSES")
    # ...and one with no setups to size is not "unaffordable": NEEDS-DATA.
    _needs_data(_cut(cut_repo(record=rec)), "R-CUT-AFFORD")


def test_cut_refuses_a_leg_also_on_a_real_money_or_mirror_roster(cut_repo):
    for acct in ("bybit_2", "bybit_portfolio"):
        _refused(_cut(cut_repo(accounts=_cut_accounts(extra_on=acct))), "R-REAL-MONEY")


def test_cut_refuses_outside_scope_accounts(cut_repo):
    root = cut_repo(mandates=_cut_mandates(scope_accounts=["alpaca_paper"]))
    _refused(_cut(root), "R-ACCOUNT")


def test_cut_refuses_a_shadow_leg(cut_repo):
    _refused(_cut(cut_repo(execution="shadow")), "R-EXECUTION-SHADOW")


def test_cut_refuses_when_not_granted(cut_repo):
    root = cut_repo(mandates={"mandates": MANDATES["mandates"], "proposed": []})
    _refused(_cut(root), "R-MANDATE-NOT-GRANTED")


def test_cut_refuses_any_other_transition(cut_repo):
    res = mr.resolve(CUT_LEG, "S2", "S1", "bybit_2", root=cut_repo(), mandate_id=CUT_ID)
    assert res["verdict"] == "REFUSE", res


@pytest.mark.parametrize("soak", [None, _soak_snap("accruing"), _soak_snap("dead"),
                                  _soak_snap("unknown"), _soak_snap(age_days=8),
                                  _soak_snap(leg="other_leg")])
def test_cut_needs_data_unless_the_soak_is_complete_and_fresh(cut_repo, soak):
    _needs_data(_cut(cut_repo(soak=soak)), "R-SOAK-INCOMPLETE")


def test_cut_needs_data_on_a_record_that_is_not_decisive(cut_repo):
    fee_only = _record(CUT_LEG, CUT_CFG, n=62, folds_net=(-1.0, -0.5, 0.9, -1.1))
    fee_only["cost_stack"] = {"fees": 7.5}
    _needs_data(_cut(cut_repo(record=fee_only)), "R-B1-C3")
    stale = _record(CUT_LEG, dict(CUT_CFG, atr_stop_mult=9.0), n=62,
                    folds_net=(-1.0, -0.5, 0.9, -1.1))
    _needs_data(_cut(cut_repo(record=stale)), "R-B1-IDENTITY")


def test_cut_afford_fires_only_when_stage2_sizes_nothing(cut_repo):
    rec = _record(CUT_LEG, CUT_CFG, n=62)  # passes every other clause
    rec["source_run"] = f"comms/strategy_evidence/runs/x/{CUT_LEG}__trades.jsonl"
    root = cut_repo(record=rec, snapshots={"bybit_2": 0.01, "alpaca_live": 100_000.0})
    (root / rec["source_run"]).write_text("".join(json.dumps(
        {"symbol": "ETHUSDT", "entry_time": f"2026-09-{d:02d} 00:00:00+00:00", "direction": "long",
         "entry": 4000.0, "sl": 3900.0, "confidence": 1.0}) + "\n" for d in range(1, 6)))
    res = _cut(root)
    assert res["verdict"] == "FIRE", res
    assert [f["clause"] for f in res["evidence"]["stage0_failures"]] == ["R-CUT-AFFORD"]
