"""MI-278 U47 — why does realised per-trade risk disperse on one account?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR rather than only when a session remembers
to invoke them. Every test names the defect it would catch, and each was
verified by PLANTING that defect and watching this file fail.
"""
import importlib.util
import json
import os

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u47", os.path.join(_HERE, "scripts", "research",
                         "risk_dispersion_attribution.py"))
U47 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U47)

CFG = {"exchange": "bybit", "market_type": "linear",
       "risk": {"risk_pct": 0.015, "leverage": 3}}
EXP_NONE = [{"policy_declared": False, "max_gross_exposure_pct": None}] * 5


def pkg(pid, entry, rpu):
    return {"order_package_id": pid, "entry": entry,
            "meta": json.dumps({"risk_per_unit": rpu})}


def row(rid, pid, size, pnl=1.0, **kw):
    d = {"id": rid, "account_id": "acct", "order_package_id": pid,
         "position_size": size, "status": "closed", "pnl": pnl,
         "strategy_name": "leg_a", "symbol": "BTCUSDT",
         "created_at": "2026-09-01T00:00:00", "closed_at": "2026-09-01T01:00:00"}
    d.update(kw)
    return d


PKGS = [pkg("p1", 100.0, 1.0), pkg("p2", 100.0, 2.0)]


# --------------------------------------------------------------------------
# the imported constants and the canonical reader
# --------------------------------------------------------------------------
def test_the_risk_constants_are_imported_not_copied():
    """Catches a local copy drifting from risk.py, which owns both."""
    from src.units.accounts.risk import (
        _MARGIN_SAFETY_BUFFER, WHOLE_UNIT_QTY_EXCHANGES)
    assert U47.MARGIN_BUFFER == _MARGIN_SAFETY_BUFFER
    assert set(U47.WHOLE_UNIT_QTY_EXCHANGES) == set(WHOLE_UNIT_QTY_EXCHANGES)


def test_declared_initial_risk_is_the_canonical_reader():
    """The realised-risk term must not be computed from |entry - sl|, which the
    monitor overwrites on every trailing amend."""
    from src.runtime.r_provenance import declared_initial_risk
    assert U47.declared_initial_risk is declared_initial_risk


# --------------------------------------------------------------------------
# pearson — the statistic every `refuted` verdict rests on
# --------------------------------------------------------------------------
def test_pearson_is_none_with_no_variance_rather_than_zero():
    """Catches the defect that turns a FLAT series into a spurious `refuted`:
    r=0 reads as 'tested and not the driver', None reads as 'untestable'."""
    assert U47.pearson([1, 1, 1], [1, 2, 3]) is None
    assert U47.pearson([1, 2, 3], [5, 5, 5]) is None


def test_pearson_refuses_below_n_three():
    assert U47.pearson([1, 2], [1, 2]) is None


def test_pearson_is_exact_on_a_line():
    assert abs(U47.pearson([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-9
    assert abs(U47.pearson([1, 2, 3], [6, 4, 2]) + 1.0) < 1e-9


# --------------------------------------------------------------------------
# the population
# --------------------------------------------------------------------------
def test_realised_risk_is_size_times_declared_risk():
    assert U47.realised_risk(row(1, "p1", 4.0), PKGS[0]) == 4.0


def test_realised_risk_is_none_on_a_missing_declared_risk():
    assert U47.realised_risk(row(1, "p1", 4.0),
                             {"order_package_id": "p1", "meta": "{}"}) is None


def test_every_exclusion_is_counted_rather_than_dropped():
    """Catches a silently shrinking denominator."""
    rows = [row(1, "p1", 4.0),
            row(2, "p1", 1.0, account_id="other"),
            row(3, "p1", 1.0, is_backtest=True),
            row(4, "missing", 1.0),
            row(5, "p1", 1.0, status="open", pnl=None)]
    pop, sk = U47.build_population(rows, PKGS, account_id="acct")
    assert len(pop) == 1
    assert sk["other_account"] == 1 and sk["backtest"] == 1
    assert sk["package_absent_from_pull"] == 1 and sk["not_closed_with_pnl"] == 1


def test_the_budget_is_the_max_observed_and_none_when_empty():
    """It is an ESTIMATE. None, never 0.0, when there is nothing to estimate
    from — a 0 budget would make every realised/B infinite."""
    pop, _ = U47.build_population([row(1, "p1", 4.0), row(2, "p1", 3.0)],
                                  PKGS, account_id="acct")
    assert U47.budget_estimate(pop) == 4.0
    assert U47.budget_estimate([]) is None


# --------------------------------------------------------------------------
# `unreachable` — established by a CONFIG READ, never by a correlation
# --------------------------------------------------------------------------
def _v(rows, cfg=CFG, exp=EXP_NONE):
    pop, _ = U47.build_population(rows, PKGS, account_id="acct")
    return U47.candidate_verdicts(pop, account_cfg=cfg, exposure_rows=exp)[0]


def test_one_declared_risk_pct_makes_per_leg_config_unreachable():
    """The strongest verdict in the module, and the one a correlation can never
    give: the sizer reads no per-strategy risk field, so all legs share a
    budget by construction."""
    assert _v([row(1, "p1", 4.0)])["per_leg_risk_config"]["verdict"] == U47.UNREACHABLE


def test_confidence_sizing_off_is_unreachable_but_linear_is_untestable():
    """Catches: calling a REACHABLE mechanism unreachable because it happened
    not to fire."""
    on = json.loads(json.dumps(CFG))
    on["risk"]["confidence_sizing"] = "linear"
    assert _v([row(1, "p1", 4.0)])["confidence_scalar"]["verdict"] == U47.UNREACHABLE
    assert _v([row(1, "p1", 4.0)], cfg=on)["confidence_scalar"]["verdict"] == U47.UNTESTABLE


def test_whole_unit_is_unreachable_on_bybit_linear_and_reachable_elsewhere():
    alp = {"exchange": "alpaca", "market_type": "linear", "risk": {"risk_pct": 0.02}}
    fut = {"exchange": "ib", "market_type": "futures", "risk": {"risk_pct": 0.015}}
    assert _v([row(1, "p1", 4.0)])["whole_unit_roundup"]["verdict"] == U47.UNREACHABLE
    assert _v([row(1, "p1", 4.0)], cfg=alp)["whole_unit_roundup"]["verdict"] == U47.UNTESTABLE
    assert _v([row(1, "p1", 4.0)], cfg=fut)["whole_unit_roundup"]["verdict"] == U47.UNTESTABLE


def test_an_undeclared_exposure_policy_is_unreachable():
    assert _v([row(1, "p1", 4.0)])["exposure_ceiling"]["verdict"] == U47.UNREACHABLE


def test_a_declared_exposure_policy_is_not_called_unreachable():
    """Catches the dangerous direction: declaring a live clamp dead."""
    v = _v([row(1, "p1", 4.0)], exp=[{"policy_declared": True}])
    assert v["exposure_ceiling"]["verdict"] == U47.UNTESTABLE


def test_no_exposure_soak_is_untestable_never_unreachable():
    """THE load-bearing refusal on this axis. 'We did not look' must not read
    as 'the clamp cannot run'."""
    pop, _ = U47.build_population([row(1, "p1", 4.0)], PKGS, account_id="acct")
    v = U47.candidate_verdicts(pop, account_cfg=CFG, exposure_rows=None)[0]
    assert v["exposure_ceiling"]["verdict"] == U47.UNTESTABLE


# --------------------------------------------------------------------------
# the lot floor, and the post-entry rewrite
# --------------------------------------------------------------------------
def test_an_exact_lot_multiple_loses_nothing_to_flooring():
    """The live finding: 33 of 33 sized rows were exact multiples, so the lot
    floor cannot be the driver however suggestive the per-leg lot COUNTS look."""
    v = _v([row(i, "p1", 4.0) for i in range(3)])
    assert v["lot_floor"]["verdict"] == U47.REFUTED
    assert v["lot_floor"]["rows_with_remainder"] == 0


def test_a_netting_stamp_makes_the_post_entry_rewrite_contribute():
    rows = [row(1, "p1", 1.0, notes=json.dumps({"netting_attributed_qty": 3.0})),
            row(2, "p1", 4.0), row(3, "p1", 3.0)]
    v = _v(rows)
    assert v["post_entry_rewrite"]["verdict"] == U47.CONTRIBUTES
    assert v["post_entry_rewrite"]["stamped_rows"] == 1


def test_a_truncated_notes_blob_is_untestable_never_refuted():
    """MI-278 U33's lesson: the stamp rides under `dump_capped(notes, 500)` and
    is not protected, so a truncated blob is *we could not look*."""
    rows = [row(1, "p1", 1.0, notes=json.dumps({"_truncated": True})),
            row(2, "p1", 4.0), row(3, "p1", 3.0)]
    v = _v(rows)
    assert v["post_entry_rewrite"]["verdict"] == U47.UNTESTABLE
    assert v["post_entry_rewrite"]["notes_truncated_we_could_not_look"] == 1


def test_no_stamp_anywhere_is_refuted():
    v = _v([row(i, "p1", float(i + 2)) for i in range(3)])
    assert v["post_entry_rewrite"]["verdict"] == U47.REFUTED


# --------------------------------------------------------------------------
# THE headline: narrowing the field is not an attribution
# --------------------------------------------------------------------------
def test_a_run_with_no_contributes_does_not_claim_an_attribution():
    """THE control. Four `unreachable` and three `refuted` verdicts feel like a
    result; they are a narrowed field with an unexplained residue, and the
    verdict string has to say so."""
    flat = [row(1, "p1", 4.0), row(2, "p1", 4.0, strategy_name="leg_b"),
            row(3, "p1", 4.0)]
    rep = U47.report(flat, PKGS, account_id="acct", account_cfg=CFG,
                     exposure_rows=EXP_NONE)
    assert rep["counts"].get(U47.CONTRIBUTES, 0) == 0
    assert rep["verdict"].startswith("no_candidate_survives")
    assert "nothing is attributed" in rep["verdict"]


def test_a_flat_population_makes_both_correlations_untestable():
    """Catches a spurious `refuted` from a zero-variance series."""
    flat = [row(i, "p1", 4.0) for i in range(3)]
    rep = U47.report(flat, PKGS, account_id="acct", account_cfg=CFG,
                     exposure_rows=EXP_NONE)
    assert rep["candidates"]["daily_loss_budget"]["verdict"] == U47.UNTESTABLE
    assert rep["candidates"]["margin_ceiling"]["verdict"] == U47.UNTESTABLE


def test_a_contributing_candidate_yields_attributed_in_part():
    rows = [row(1, "p1", 1.0, notes=json.dumps({"netting_attributed_qty": 3.0})),
            row(2, "p1", 4.0), row(3, "p1", 3.0)]
    rep = U47.report(rows, PKGS, account_id="acct", account_cfg=CFG,
                     exposure_rows=EXP_NONE)
    assert rep["verdict"] == "attributed_in_part"


def test_an_empty_population_reports_no_budget_and_no_spread():
    """Catches a 0.0 budget or a 1.0 spread rendering as a measurement of a
    population that does not exist."""
    rep = U47.report([], [], account_id="acct", account_cfg=CFG)
    assert rep["budget_estimate"] is None
    assert rep["realised_over_budget"] is None
    assert rep["verdict"].startswith("no_candidate_survives")


def test_the_spread_is_max_over_min_of_realised_risk():
    rows = [row(1, "p1", 4.0), row(2, "p2", 2.0, strategy_name="leg_b"),
            row(3, "p1", 3.0)]
    rep = U47.report(rows, PKGS, account_id="acct", account_cfg=CFG,
                     exposure_rows=EXP_NONE)
    assert rep["realised_over_budget"]["spread"] == 1.3333


def test_per_leg_rows_are_ordered_by_median_realised_risk():
    rows = [row(1, "p1", 4.0), row(2, "p2", 2.0, strategy_name="leg_b"),
            row(3, "p1", 1.0, strategy_name="leg_c")]
    rep = U47.report(rows, PKGS, account_id="acct", account_cfg=CFG,
                     exposure_rows=EXP_NONE)
    meds = [rep["per_leg"][k]["median_risk_usd"] for k in rep["per_leg"]]
    assert meds == sorted(meds)


def test_render_never_raises_on_any_reachable_report():
    for rows in ([], [row(1, "p1", 4.0), row(2, "p1", 3.0), row(3, "p1", 2.0)]):
        rep = U47.report(rows, PKGS, account_id="acct", account_cfg=CFG,
                         exposure_rows=EXP_NONE)
        assert isinstance(U47.render(rep), str)


def test_the_module_self_test_passes():
    assert U47._self_test() == 0
