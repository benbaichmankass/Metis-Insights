"""MI-278 U45 — is an order's size a RISK decision, or the margin ceiling?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR rather than only when a session remembers
to invoke them. Every test names the defect it would catch, and each was
verified by PLANTING that defect and watching this file fail.
"""
import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u45", os.path.join(_HERE, "scripts", "research",
                         "margin_bound_sizing_basis.py"))
U45 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U45)

# The account terms this lane measured against: bybit_1's declared
# risk.risk_pct / risk.leverage, and risk.py's own _MARGIN_SAFETY_BUFFER.
RP, LEV, BUF = 0.015, 3, 0.9


def pkg(pid, entry, rpu):
    return {"order_package_id": pid, "entry": entry,
            "meta": json.dumps({"risk_per_unit": rpu})}


def row(rid, pid, size, *, setup="ict_scalp_avax_5m",
        strategy="ict_scalp_avax_5m", cap=None, acct="bybit_1", **kw):
    notes = {"margin_basis": {"max_qty_by_margin": cap}} if cap is not None else {}
    d = {"id": rid, "account_id": acct, "order_package_id": pid,
         "position_size": size, "setup_type": setup, "strategy_name": strategy,
         "notes": json.dumps(notes) if notes else None}
    d.update(kw)
    return d


# --------------------------------------------------------------------------
# the imported constants — the defect is a COPY drifting from its owner
# --------------------------------------------------------------------------
def test_margin_buffer_is_imported_not_copied():
    """Catches: someone hardcodes 0.9 here while risk.py moves it."""
    from src.units.accounts.risk import _MARGIN_SAFETY_BUFFER
    assert U45.MARGIN_BUFFER is not None
    assert U45.MARGIN_BUFFER == _MARGIN_SAFETY_BUFFER


def test_declared_initial_risk_is_the_canonical_reader():
    """Catches: a second local copy of 'what is the decision-time risk'.

    The whole argument rests on NOT reading `trades.stop_loss` /
    `order_packages.sl`, which the monitor overwrites on every trailing amend.
    """
    from src.runtime.r_provenance import declared_initial_risk
    assert U45.declared_initial_risk is declared_initial_risk


# --------------------------------------------------------------------------
# the arithmetic
# --------------------------------------------------------------------------
def test_binding_distance_is_risk_over_leveraged_buffer():
    assert U45.binding_stop_bp(RP, LEV, BUF) == pytest.approx(55.5556, abs=1e-3)


def test_binding_distance_is_none_not_zero_when_unusable():
    """Catches: returning 0.0, which reads as 'every stop binds'."""
    for bad in ((0.0, LEV, BUF), (RP, 0, BUF), (RP, LEV, 0.0), (None, LEV, BUF)):
        assert U45.binding_stop_bp(*bad) is None


def test_ratio_is_exactly_one_at_the_binding_distance():
    """The identity that ties the two functions together; a drift in either
    breaks this and nothing else would."""
    entry = 100.0
    rpu = entry * U45.binding_stop_bp(RP, LEV, BUF) / 10000.0
    assert U45.margin_bound_ratio(entry, rpu, RP, LEV, BUF) == pytest.approx(1.0)


def test_ratio_is_scale_free_in_price():
    """Catches: an entry term left in that makes a $70k symbol look worse than
    a $7 one purely because of its price."""
    a = U45.margin_bound_ratio(100.0, 1.0, RP, LEV, BUF)
    b = U45.margin_bound_ratio(70000.0, 700.0, RP, LEV, BUF)
    assert a == pytest.approx(b)


def test_ratio_none_never_zero_on_a_missing_input():
    assert U45.margin_bound_ratio(100.0, None, RP, LEV, BUF) is None
    assert U45.margin_bound_ratio(100.0, 0.0, RP, LEV, BUF) is None
    assert U45.margin_bound_ratio(-1.0, 1.0, RP, LEV, BUF) is None


# --------------------------------------------------------------------------
# qty_provenance — the axis that keeps a non-sizer quantity out of the verdict
# --------------------------------------------------------------------------
@pytest.mark.parametrize("r,expected", [
    ({"setup_type": "ict_scalp_avax_5m", "strategy_name": "ict_scalp_avax_5m"},
     U45.QTY_SIZER),
    ({"setup_type": "intent_reduce", "strategy_name": "sol_pullback_2h"},
     U45.QTY_INTENT_REDUCE),
    ({"setup_type": "adopted_orphan", "strategy_name": "ict_scalp_sol_15m"},
     U45.QTY_VENUE_ADOPTED),
    ({"setup_type": "pairs_sol_eth_a", "strategy_name": "pairs_sol_eth_a"},
     U45.QTY_ISOLATED_PATH),
    ({"setup_type": "", "strategy_name": "ict_scalp_5m"}, U45.QTY_UNKNOWN),
])
def test_every_qty_provenance_state_is_reachable(r, expected):
    assert U45.qty_provenance(r) == expected


def test_intent_reduce_is_caught_from_notes_when_setup_type_does_not_say_so():
    """Catches: keying only on setup_type. The live rows carry BOTH, but the
    notes marker is the one written by the intent multiplexer itself."""
    assert U45.qty_provenance({
        "setup_type": "sol_pullback_2h", "strategy_name": "sol_pullback_2h",
        "notes": json.dumps({"intent_reduce": True}),
    }) == U45.QTY_INTENT_REDUCE


def test_a_missing_setup_type_is_unknown_and_never_sizer_output():
    """Catches the dangerous default. Treating 'we could not tell' as a sizer
    output is what lets a venue-adopted quantity be inverted for a risk number.
    """
    assert U45.qty_provenance({"strategy_name": "x"}) == U45.QTY_UNKNOWN


# --------------------------------------------------------------------------
# the stamped-ceiling reader — three states, never two
# --------------------------------------------------------------------------
def test_absent_stamp_is_none_not_false():
    """Catches: rendering 'no stamp' as 'did not reach the ceiling'. The stamp
    ships on refusal rows only, from 2026-08-13, so absence is common and says
    nothing."""
    assert U45.at_stamped_ceiling({"position_size": 90.0}) is None


def test_at_ceiling_true_and_false_are_both_reachable():
    cap = {"margin_basis": {"max_qty_by_margin": 100.0}}
    assert U45.at_stamped_ceiling(
        {"position_size": 100.0, "notes": json.dumps(cap)}) is True
    assert U45.at_stamped_ceiling(
        {"position_size": 90.0, "notes": json.dumps(cap)}) is False


def test_venue_precision_equality_counts_as_at_ceiling():
    """The live rows are floored to the instrument lot, so an exact float
    comparison reports 'not at ceiling' on a row that IS. Measured on the
    2026-08-22 row the backlog was filed on: 33265.4 vs 33265.434696282246.
    """
    assert U45.at_stamped_ceiling({
        "position_size": 33265.4,
        "notes": json.dumps({"margin_basis": {
            "max_qty_by_margin": 33265.434696282246}}),
    }) is True


# --------------------------------------------------------------------------
# size_basis — five states, and the ordering is a decision
# --------------------------------------------------------------------------
def test_provable_beats_observed_because_it_is_the_stronger_statement():
    """A row that is both provable and at its stamped ceiling grades
    provably_margin_bound: that holds at ANY balance, not only the one
    recorded."""
    r = row(1, "p", 100.0, cap=100.0)
    state, _ = U45.size_basis(r, 100.0, 0.0046 * 100, RP, LEV, BUF)
    assert state == U45.BASIS_PROVABLY_BOUND


def test_slack_and_unstamped_is_not_provable_not_risk_derived():
    """THE load-bearing test. Calling this 'risk_derived' would turn a
    false-negative rate into a finding — the inequality is slack by
    equity/basis_usd and misses most real bindings."""
    state, _ = U45.size_basis(row(1, "p", 10.0), 100.0, 2.0, RP, LEV, BUF)
    assert state == U45.BASIS_NOT_PROVABLE


def test_stamped_below_ceiling_is_the_only_risk_derived_verdict():
    state, _ = U45.size_basis(row(1, "p", 90.0, cap=100.0), 100.0, 2.0,
                              RP, LEV, BUF)
    assert state == U45.BASIS_RISK_DERIVED


def test_no_basis_on_a_zero_quantity_or_a_missing_declared_risk():
    assert U45.size_basis(row(1, "p", 0.0), 100.0, 1.0, RP, LEV, BUF)[0] \
        == U45.BASIS_NO_BASIS
    assert U45.size_basis(row(1, "p", 10.0), 100.0, None, RP, LEV, BUF)[0] \
        == U45.BASIS_NO_BASIS


# --------------------------------------------------------------------------
# report() — the population rules and the control
# --------------------------------------------------------------------------
def test_futures_is_refused_not_graded():
    """Catches: printing a margin ratio for an account whose sizer skips the
    crypto margin cap entirely. A number about a gate that does not run."""
    v = U45.report([], [], account_id="ib_paper", risk_pct=RP, leverage=1,
                   market_type="futures")
    assert v["refused"]
    assert v["size_basis"] == {}


def test_a_planted_false_positive_falsifies_the_control():
    """The instrument's own honesty check. A row predicted bound whose stamp
    says it did NOT reach the ceiling contradicts the inequality."""
    pkgs = [pkg("p1", 100.0, 0.046)]           # 4.6 bp -> ratio ~12
    bad = [row(1, "p1", 50.0, cap=100.0)]
    v = U45.report(bad, pkgs, account_id="bybit_1", risk_pct=RP, leverage=LEV,
                   buffer_=BUF)
    assert v["control"]["sound"] is False
    assert v["control"]["false_positive_ids"] == [1]


def test_the_expected_slack_is_counted_rather_than_hidden():
    """A wide-stop row sitting at its ceiling is a false NEGATIVE and must show
    up as one; suppressing it would make the test look tighter than it is."""
    pkgs = [pkg("p2", 100.0, 2.0)]             # 200 bp -> ratio ~0.28
    v = U45.report([row(1, "p2", 100.0, cap=100.0)], pkgs, account_id="bybit_1",
                   risk_pct=RP, leverage=LEV, buffer_=BUF)
    assert v["control"]["not_predicted_and_at_ceiling"] == 1
    assert v["control"]["sound"] is True


def test_an_empty_control_is_ungraded_never_a_pass():
    """Catches: reporting 'zero false positives' over zero stamped rows."""
    pkgs = [pkg("p2", 100.0, 2.0)]
    v = U45.report([row(1, "p2", 10.0)], pkgs, account_id="bybit_1",
                   risk_pct=RP, leverage=LEV, buffer_=BUF)
    assert v["control"]["stamped_rows"] == 0
    assert v["control"]["sound"] is None


def test_non_sizer_quantities_never_reach_the_sizing_verdict():
    """Catches the defect that broke this lane's first attempt: inverting an
    intent_reduce / adopted_orphan / pairs quantity as if the risk sizer had
    produced it."""
    pkgs = [pkg("p1", 100.0, 0.046)]
    rows = [
        row(1, "p1", 99.0, setup="intent_reduce"),
        row(2, "p1", 99.0, setup="adopted_orphan"),
        row(3, "p1", 99.0, setup="x", strategy="pairs_sol_eth_a"),
    ]
    v = U45.report(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                   leverage=LEV, buffer_=BUF)
    assert v["sizer_rows"] == 0
    assert v["size_basis"] == {}
    assert v["qty_provenance"] == {
        U45.QTY_INTENT_REDUCE: 1, U45.QTY_VENUE_ADOPTED: 1,
        U45.QTY_ISOLATED_PATH: 1}


def test_provable_coverage_is_none_not_zero_when_nothing_is_gradeable():
    """Catches: a 0.0 coverage that reads as 'we looked and found none
    definite' when in fact nothing was gradeable at all."""
    pkgs = [pkg("p1", 100.0, 0.046)]
    v = U45.report([row(1, "p1", 99.0, setup="intent_reduce")], pkgs,
                   account_id="bybit_1", risk_pct=RP, leverage=LEV, buffer_=BUF)
    assert v["provable_coverage"] is None


def test_provable_coverage_is_zero_when_gradeable_but_none_definite():
    pkgs = [pkg("p2", 100.0, 2.0)]
    v = U45.report([row(1, "p2", 10.0)], pkgs, account_id="bybit_1",
                   risk_pct=RP, leverage=LEV, buffer_=BUF)
    assert v["provable_coverage"] == 0.0


def test_an_absent_package_is_no_basis_and_is_counted_in_skipped():
    """Catches: a row whose package fell outside the pull being silently
    dropped, which would shrink the denominator without saying so."""
    v = U45.report([row(1, "nope", 10.0)], [pkg("p1", 100.0, 0.046)],
                   account_id="bybit_1", risk_pct=RP, leverage=LEV, buffer_=BUF)
    assert v["size_basis"].get(U45.BASIS_NO_BASIS) == 1
    assert v["population"]["skipped"].get("package_absent_from_pull") == 1


def test_backtest_and_other_accounts_are_excluded_and_counted():
    pkgs = [pkg("p1", 100.0, 0.046)]
    rows = [
        row(1, "p1", 10.0, acct="bybit_2"),
        row(2, "p1", 10.0, is_backtest=True),
        row(3, "p1", 10.0),
    ]
    v = U45.report(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                   leverage=LEV, buffer_=BUF)
    assert v["population"]["rows_for_this_account"] == 1
    assert v["population"]["skipped"]["other_account"] == 1
    assert v["population"]["skipped"]["backtest"] == 1


def test_render_never_raises_on_any_reachable_report():
    pkgs = [pkg("p1", 100.0, 0.046), pkg("p2", 100.0, 2.0)]
    for rows, kw in (
        ([], {}),
        ([row(1, "p1", 100.0, cap=100.0)], {}),
        ([row(1, "p2", 10.0)], {}),
        ([], {"market_type": "futures"}),
    ):
        v = U45.report(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                       leverage=LEV, buffer_=BUF, **kw)
        assert isinstance(U45.render(v), str)


def test_the_module_self_test_passes():
    """The executable controls and this file must not diverge."""
    assert U45._self_test() == 0


# --------------------------------------------------------------------------
# the reconciliation — the assertion inside the transform
# --------------------------------------------------------------------------
def test_the_per_strategy_table_sums_to_its_own_census():
    """THE defect this field was added for, found by arithmetic on live data.

    The first cut keyed the per-strategy table on 'has a usable ratio', which
    admitted rows whose position_size is 0.0 — graded `no_basis` by the census.
    The table then reported 62 provably-bound orders against a census of 43,
    and nothing in the output disagreed with itself.
    """
    pkgs = [pkg("p1", 100.0, 0.046), pkg("p2", 100.0, 2.0)]
    rows = [
        row(1, "p1", 10.0),          # sized, provably bound
        row(2, "p1", 0.0),           # geometry only: bound ratio, NO size
        row(3, "p2", 10.0),          # sized, slack
    ]
    v = U45.report(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                   leverage=LEV, buffer_=BUF)
    rc = v["reconciles"]
    assert rc["ok"] is True
    assert rc["per_strategy_n"] == rc["gradeable_rows"] == 2
    assert rc["per_strategy_bound"] == rc["census_provably_bound"] == 1


def test_a_zero_size_row_is_geometry_only_and_never_pooled_into_the_table():
    """A signal that was never sized is not an order whose size was the
    ceiling. Pooling the two is what inflated the table."""
    pkgs = [pkg("p1", 100.0, 0.046)]
    v = U45.report([row(1, "p1", 0.0)], pkgs, account_id="bybit_1",
                   risk_pct=RP, leverage=LEV, buffer_=BUF)
    assert v["geometry_only"]["rows"] == 1
    assert v["geometry_only"]["would_be_bound"] == 1
    assert v["per_strategy"] == {}
    assert v["size_basis"].get(U45.BASIS_NO_BASIS) == 1


def test_geometry_only_rows_are_not_dropped_silently():
    """Catches: filtering them out entirely, which shrinks a denominator
    without saying so."""
    pkgs = [pkg("p2", 100.0, 2.0)]
    v = U45.report([row(1, "p2", 0.0), row(2, "p2", 0.0)], pkgs,
                   account_id="bybit_1", risk_pct=RP, leverage=LEV, buffer_=BUF)
    assert v["geometry_only"]["rows"] == 2
    assert v["geometry_only"]["would_be_bound"] == 0
    assert v["geometry_only"]["per_strategy"]["ict_scalp_avax_5m"]["n"] == 2


# --------------------------------------------------------------------------
# outcomes() — the pooled arms and the composition control
# --------------------------------------------------------------------------
def closed(rid, pid, size, pnl, *, strategy, src="exchange_fill", **kw):
    r = row(rid, pid, size, setup=strategy, strategy=strategy, **kw)
    r.update({"status": "closed", "pnl": pnl, "exit_price_source": src})
    return r


def test_outcomes_grades_pnl_through_the_canonical_provenance_module():
    """Catches: a dollar figure quoted without a provenance split. A
    `candle_at_close` row is ESTIMATED, not MEASURED, and must not land in the
    measured total."""
    pkgs = [pkg("p1", 100.0, 0.046)]
    rows = [
        closed(1, "p1", 10.0, 100.0, strategy="ict_scalp_a"),
        closed(2, "p1", 10.0, 900.0, strategy="ict_scalp_a",
               src="candle_at_close"),
    ]
    o = U45.outcomes(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                     leverage=LEV, buffer_=BUF)
    b = o["pooled"]["bound"]
    assert b["n"] == 2 and b["pnl"] == 1000.0
    assert b["n_measured"] == 1 and b["pnl_measured"] == 100.0
    assert b["pnl_coverage"] == 0.5


def test_outcomes_names_a_leg_that_contributes_to_one_arm_only():
    """THE control. A leg with zero bound rows dumps its whole PnL into the
    unbound arm, which is how a pooled comparison manufactures a difference."""
    pkgs = [pkg("tight", 100.0, 0.046), pkg("wide", 100.0, 2.0)]
    rows = [
        closed(1, "tight", 10.0, 50.0, strategy="ict_scalp_a"),
        closed(2, "wide", 10.0, -50.0, strategy="ict_scalp_a"),
        closed(3, "wide", 10.0, -9000.0, strategy="ict_scalp_b"),
    ]
    o = U45.outcomes(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                     leverage=LEV, buffer_=BUF)
    assert o["legs_with_no_bound_rows"] == ["ict_scalp_b"]
    assert o["pooled"]["unbound"]["pnl"] == -9050.0
    assert o["per_leg"]["ict_scalp_a"]["unbound"]["pnl"] == -50.0


def test_outcomes_marks_only_legs_where_both_arms_reach_three():
    """Catches: a per-leg 'comparison' on n=1 arms being read as a control."""
    pkgs = [pkg("tight", 100.0, 0.046), pkg("wide", 100.0, 2.0)]
    rows = ([closed(i, "tight", 10.0, 1.0, strategy="ict_scalp_a")
             for i in range(3)]
            + [closed(10 + i, "wide", 10.0, 1.0, strategy="ict_scalp_a")
               for i in range(3)]
            + [closed(20, "tight", 10.0, 1.0, strategy="ict_scalp_b"),
               closed(21, "wide", 10.0, 1.0, strategy="ict_scalp_b")])
    o = U45.outcomes(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                     leverage=LEV, buffer_=BUF)
    assert o["legs_with_both_arms_n_ge_3"] == 1
    assert o["per_leg"]["ict_scalp_a"].get("comparable") is True
    assert "comparable" not in o["per_leg"]["ict_scalp_b"]


def test_outcomes_excludes_non_sizer_quantities():
    """An adopted-orphan or intent-reduce close is not an order the sizer
    produced, so it cannot speak to whether the sizer was clamped."""
    pkgs = [pkg("p1", 100.0, 0.046)]
    rows = [closed(1, "p1", 10.0, 500.0, strategy="ict_scalp_a")]
    rows[0]["setup_type"] = "adopted_orphan"
    o = U45.outcomes(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                     leverage=LEV, buffer_=BUF)
    assert o["pooled"]["bound"]["n"] == 0


def test_outcomes_reports_none_not_zero_for_an_empty_arm():
    """Catches: an empty arm rendering as a $0.00 result, which reads as
    'we looked and it netted nothing'."""
    o = U45.outcomes([], [], account_id="bybit_1", risk_pct=RP, leverage=LEV,
                     buffer_=BUF)
    assert o["pooled"]["bound"]["pnl"] is None
    assert o["pooled"]["bound"]["pnl_coverage"] is None


def test_render_outcomes_prints_the_control_beside_the_pooled_row():
    pkgs = [pkg("tight", 100.0, 0.046), pkg("wide", 100.0, 2.0)]
    rows = [closed(1, "tight", 10.0, 50.0, strategy="ict_scalp_a"),
            closed(2, "wide", 10.0, -9000.0, strategy="ict_scalp_b")]
    o = U45.outcomes(rows, pkgs, account_id="bybit_1", risk_pct=RP,
                     leverage=LEV, buffer_=BUF)
    txt = U45.render_outcomes(o)
    assert "CONFOUNDED BY LEG COMPOSITION" in txt
    assert "PER LEG (the control)" in txt
    assert "ict_scalp_b" in txt
