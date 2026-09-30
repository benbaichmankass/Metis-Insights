"""MD-SOAK-EXIT-CELL-PATHB -- mandate_resolver.resolve_exit_cell().

Same shape as tests/test_mandate_resolver.py: every REFUSE/NEEDS_DATA test starts
from ONE passing fixture and breaks exactly one thing, so a red test names the
clause. The FIRE test is the positive control -- without it a resolver that
refused everything would pass every REFUSE test.

The mandate entry is read from the REAL config/mandates.yaml (`proposed:`), so a
change to its numbers is exercised here rather than shadowed by a test copy.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import mandate_resolver as mr  # noqa: E402

MID = mr.EXIT_CELL_MANDATE_ID
LEG, CELL = "spy_pullback_1h", "sm1.5"

# spy_pullback_1h sm1.5, the fields #14332 cleared on (docs/research/e35-bracket-corpus.jsonl,
# newest graded row, sweep 2026-08-31).
ROW = {
    "leg": LEG, "cell": CELL, "family": "pullback", "axis": "stop", "stop_mult": 1.5,
    "tp_r": None, "timeout": None, "state": "measured", "gate_path": "B",
    "gate_verdict": "path_b_wf_pass", "gate_is_d_net_r": 23.4505, "gate_oos_d_net_r": 4.4566,
    "capital_oos_d_net_r_per_capital_day": 0.0407, "base_oos_trades": 58,
    "wf_ran": True, "wf_usable": 6, "wf_wins_effective": 5,
    "base_net_total_r": 12.1674, "base_max_drawdown_r": 15.208, "d_net_r": 28.1569, "d_max_dd": 3.6421,
    "leverage": {"leverage_multiple": 1.6589, "state": "measured"},
    "sweep_generated_at": "2026-08-31T16:14:00+00:00", "measurement_key": "k", "source": "s",
    # timeout_binding_audit needs these on the base arm (the graded ROW is the timeout=None arm)
    "timeframe": "1h", "net_total_r": 40.0, "tp_cap_pct": 0.099,
}


def audit_arms(leg=LEG, *, binding=False):
    """The `to24` / `to400` sibling arms timeout_binding_audit.audit() pairs with ROW.
    to24 MOVES (so the probe has power); to400 equals the base unless `binding`."""
    common = dict(leg=leg, family="pullback", timeframe="1h", tp_r=None, stop_mult=1.5, tp_cap_pct=0.099)
    return [dict(common, cell="sm1.5_to24", timeout=24, net_total_r=30.0),
            dict(common, cell="sm1.5_to400", timeout=400, net_total_r=41.0 if binding else 40.0)]


def _acct(cls, legs):
    return {"account_class": cls, "exchange": "alpaca", "mode": "live",
            "risk": {"risk_pct": 0.015}, "strategies": list(legs)}


def _build(tmp_path, *, row=None, rows=None, accounts=None, cur=2.5, granted=False,
           binding=False, matrix_note=None, audit=True):
    real = yaml.safe_load((REPO / "config/mandates.yaml").read_text())
    entry = next(m for m in real["mandates"] if m["id"] == MID)
    # `granted=False` re-files the REAL entry under `proposed:` to exercise the dry path.
    doc = {"mandates": [entry] if granted else [], "proposed": [] if granted else [entry]}
    accts = accounts or {"alpaca_paper": _acct("paper", [LEG]),
                         "alpaca_live": _acct("real_money", ["x"])}
    files = {"config/mandates.yaml": yaml.safe_dump(doc),
             "config/accounts.yaml": yaml.safe_dump({"accounts": accts}),
             "config/strategies.yaml": yaml.safe_dump({"strategies": {LEG: {"execution": "live",
                                                                             "atr_stop_mult": cur}}})}
    for rel, txt in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(txt)
    rs = list(rows if rows is not None else [dict(ROW, **(row or {}))])
    if audit:
        rs += audit_arms(binding=binding)
    if matrix_note is not None:
        m = tmp_path / mr.COVERAGE_MATRIX_REL
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"rows": [{"strategy": LEG, "bracket_geometry": {"timeout_binding": matrix_note}}]}))
    corpus = tmp_path / mr.BRACKET_CORPUS_REL
    corpus.parent.mkdir(parents=True, exist_ok=True)
    corpus.write_text("\n".join(json.dumps(r) for r in rs) + "\n")
    return tmp_path


def _run(tmp_path, **kw):
    return mr.resolve_exit_cell(LEG, CELL, root=_build(tmp_path, **kw), allow_proposed=True)


@pytest.mark.parametrize("kw,verdict,clause", [
    ({"binding": True}, mr.REFUSE, "R-TIMEOUT-BINDING"),                            # audit says contaminated
    ({"matrix_note": "MEASURED: leg CONTAMINATED -- x"}, mr.REFUSE, "R-TIMEOUT-BINDING"),  # note says so, audit clean
    ({"binding": True, "matrix_note": "MEASURED: leg CLEAN"}, mr.REFUSE, "R-TIMEOUT-BINDING"),  # either source trips it
    ({"audit": False}, mr.NEEDS_DATA, "R-TIMEOUT-BINDING"),                          # cannot audit -> not a pass
    ({"matrix_note": "MEASURED: leg CLEAN -- x"}, mr.FIRE, "ALL-CLAUSES-PASS"),      # positive control
])
def test_timeout_binding_bound(tmp_path, kw, verdict, clause):
    r = _run(tmp_path, **kw)
    assert (r["verdict"], r["clause"]) == (verdict, clause), r["detail"]
    assert "timeout_binding" in r["evidence"] or verdict == mr.NEEDS_DATA


def test_the_grant_must_state_the_bound(tmp_path):
    root = _build(tmp_path)
    doc = yaml.safe_load((root / "config/mandates.yaml").read_text())
    del doc["proposed"][0]["bar"]["refuse_timeout_contaminated"]
    (root / "config/mandates.yaml").write_text(yaml.safe_dump(doc))
    r = mr.resolve_exit_cell(LEG, CELL, root=root, allow_proposed=True)
    assert (r["verdict"], r["clause"]) == (mr.REFUSE, "R-MANDATE-NOT-GRANTED")


def test_dry_fire_is_the_positive_control_and_says_it_authorizes_nothing(tmp_path):
    r = _run(tmp_path)
    assert r["verdict"] == mr.FIRE and r["authorized"] is False
    assert any("PROPOSED" in c for c in r["caveats"])
    assert r["proposal"]["landing"] == "hold"
    assert r["proposal"]["config_edit"] == {"file": mr.STRATEGIES_REL, "leg": LEG,
                                            "key": "atr_stop_mult", "from": 2.5, "to": 1.5}


def test_proposed_entry_never_authorizes_without_the_dry_flag(tmp_path):
    root = _build(tmp_path)
    r = mr.resolve_exit_cell(LEG, CELL, root=root)
    assert r["verdict"] == mr.REFUSE and r["clause"] == "R-MANDATE-NOT-GRANTED"


def test_granted_entry_fires_authorized(tmp_path):
    root = _build(tmp_path, granted=True)
    r = mr.resolve_exit_cell(LEG, CELL, root=root)
    assert r["verdict"] == mr.FIRE and r["authorized"] is True


@pytest.mark.parametrize("accounts,why", [
    ({"alpaca_paper": _acct("paper", [LEG]), "alpaca_live": _acct("real_money", [LEG])}, "real-money roster"),
    # THE TRAP: class `paper`, but it is the Stage-2 mirror. A class-only check passes this.
    ({"alpaca_paper": _acct("paper", [LEG]), "alpaca_portfolio": _acct("paper", [LEG])}, "paper-class Stage-2 mirror"),
    ({"alpaca_paper": _acct("paper", [LEG]), "breakout_1": _acct("prop", [LEG])}, "prop"),
    ({"alpaca_paper": _acct("paper", [LEG]), "alpaca_options_paper": _acct("paper", [LEG])}, "unlisted paper account"),
    ({"alpaca_paper": _acct("paper", ["other"])}, "on no roster"),
])
def test_soak_only_refuses(tmp_path, accounts, why):
    r = _run(tmp_path, accounts=accounts)
    assert (r["verdict"], r["clause"]) == (mr.REFUSE, "R-SOAK-ONLY"), why


@pytest.mark.parametrize("row,clause,verdict", [
    ({"timeout": 24}, "R-CELL-SHAPE", mr.REFUSE),
    ({"tp_r": 6.0}, "R-CELL-SHAPE", mr.REFUSE),
    ({"gate_path": "A"}, "R-CELL-SHAPE", mr.REFUSE),
    ({"family": "scalp"}, "R-COST-STACK", mr.REFUSE),
    ({"state": "unmeasured"}, "R-RECORD-MISSING", mr.NEEDS_DATA),
    ({"gate_oos_d_net_r": -0.1}, "R-NET-R", mr.REFUSE),
    ({"gate_is_d_net_r": 0.0}, "R-NET-R", mr.REFUSE),
    ({"capital_oos_d_net_r_per_capital_day": 0.0}, "R-NET-R", mr.REFUSE),
    ({"base_oos_trades": None}, "R-BASE-N", mr.NEEDS_DATA),
    ({"base_oos_trades": 24}, "R-BASE-N", mr.REFUSE),
    ({"wf_wins_effective": 4}, "R-FOLDS", mr.REFUSE),          # 4/6: the sweep passes it, this mandate does not
    ({"wf_usable": 4, "wf_wins_effective": 4}, "R-FOLDS", mr.REFUSE),
    ({"wf_ran": False}, "R-FOLDS", mr.NEEDS_DATA),
    ({"d_max_dd": 5.4}, "R-DRAWDOWN", mr.REFUSE),               # 0.355 of base > 0.35
    ({"base_net_total_r": -1.0}, "R-DRAWDOWN", mr.REFUSE),
    ({"leverage": {"leverage_multiple": 2.5}}, "R-LEVERAGE", mr.REFUSE),
    ({"leverage": {"leverage_multiple": None}}, "R-LEVERAGE", mr.NEEDS_DATA),
])
def test_each_clause_breaks_alone(tmp_path, row, clause, verdict):
    r = _run(tmp_path, row=row)
    assert (r["verdict"], r["clause"]) == (verdict, clause), r["detail"]


def test_drawdown_at_the_cap_passes_just_over_refuses(tmp_path):
    ok = _run(tmp_path / "a", row={"d_max_dd": 0.35 * 15.208})
    assert ok["verdict"] == mr.FIRE
    over = _run(tmp_path / "b", row={"d_max_dd": 0.351 * 15.208})
    assert over["clause"] == "R-DRAWDOWN"


def test_exchange_rate_is_enforced_beside_the_cap(tmp_path):
    # small net gain on a strong base: allowance = D_b * dN/N_b = 15.208*0.5/12.1674 ~ 0.62,
    # well under both the cap and the ask.
    r = _run(tmp_path, row={"d_net_r": 0.5, "d_max_dd": 2.0})
    assert (r["verdict"], r["clause"]) == (mr.REFUSE, "R-DRAWDOWN") and "allowance" in r["detail"]


def test_any_disagreeing_graded_row_vetoes_the_cell(tmp_path):
    rows = [dict(ROW), dict(ROW, gate_verdict="wf_fail", sweep_generated_at="2026-08-29T00:00:00+00:00")]
    r = _run(tmp_path, rows=rows)
    assert (r["verdict"], r["clause"]) == (mr.REFUSE, "R-CORPUS-DISAGREES")


def test_no_row_is_needs_data_not_refuse(tmp_path):
    r = _run(tmp_path, rows=[])
    assert (r["verdict"], r["clause"]) == (mr.NEEDS_DATA, "R-RECORD-MISSING") and r["data_task"]


def test_already_declared_is_a_noop(tmp_path):
    r = _run(tmp_path, cur=1.5)
    assert (r["verdict"], r["clause"]) == (mr.REFUSE, "R-NO-OP")


def test_the_roster_resolver_refuses_this_id_by_design():
    # direction: exit_geometry is not add_risk|derisk_only -- `_decide()` must never evaluate it.
    real = yaml.safe_load((REPO / "config/mandates.yaml").read_text())
    entry = next(m for m in real["mandates"] if m["id"] == MID)
    assert entry["direction"] == "exit_geometry"


def test_committed_entry_is_granted_by_the_operator_with_no_autoland():
    real = yaml.safe_load((REPO / "config/mandates.yaml").read_text())
    assert MID not in [m["id"] for m in real.get("proposed") or []]
    entry = next(m for m in real["mandates"] if m["id"] == MID)
    assert entry["granted_by"].startswith("operator") and entry["granted_at"] == "2026-09-30"
    assert "PATHB-MANDATE" in entry["source"]
    assert entry["amended_by"].startswith("operator") and entry["bar"]["refuse_timeout_contaminated"] is True
    assert "autoland" not in entry  # operator: a fire opens a PR and pings; the manager merges
