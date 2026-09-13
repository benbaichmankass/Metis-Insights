"""MI-278 U32 — the e35 binomial re-derived on a package denominator.

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR rather than only when a session remembers to
invoke them. Each test states what it would catch, and every one was verified by
PLANTING the defect it names and watching it fail.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u32", os.path.join(_HERE, "scripts", "research",
                         "e35_binomial_package_denominator.py"))
U32 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U32)

E, C = "trend_donchian", "trend_donchian_eth"
PRE, POST = "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z"


def row(leg, era, pnl, pid, acct="bybit_1", **kw):
    d = {"strategy_name": leg, "created_at": PRE if era == "pre" else POST,
         "pnl": pnl, "status": "closed", "is_backtest": 0,
         "account_id": acct, "order_package_id": pid}
    d.update(kw)
    return d


def test_module_self_test_passes():
    """The executable controls run green. Plant: any defect below."""
    assert U32._self_test() == 0


# --------------------------------------------------------------------------
# The win reduction, and what it refuses to do
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pnl,expected", [
    (1.0, True), (-1.0, False), (0.0, False), (None, None), ("x", None),
])
def test_win_is_three_valued(pnl, expected):
    """`None` is *we could not look*, never a loss. Plant: return False."""
    assert U32._win({"pnl": pnl}) is expected


def test_package_win_never_arbitrates_a_disagreement():
    """Plant: `return "unanimous", grades[0]`."""
    assert U32.package_win([{"pnl": 1.0}, {"pnl": -1.0}]) == ("disagreement", None)


def test_package_win_separates_ungradeable_from_disagreement():
    """Two different facts. Plant: fold ungradeable into disagreement."""
    assert U32.package_win([{"pnl": None}]) == ("ungradeable", None)
    assert U32.package_win([{"pnl": None}, {"pnl": 2.0}]) == ("unanimous", True)


def test_one_row_package_is_unanimous_by_construction():
    assert U32.package_win([{"pnl": -3.0}]) == ("unanimous", False)


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------

def test_binomial_zero_trials_is_none_not_one():
    """1.0 reads as 'certainly not worse than expected'. Plant: drop `n <= 0`."""
    assert U32.binomial_le(0, 0, 0.5) is None


@pytest.mark.parametrize("k,n,p", [(3, 2, 0.5), (-1, 2, 0.5), (1, 2, 1.4), (1, 2, -0.1)])
def test_binomial_refuses_an_impossible_question(k, n, p):
    assert U32.binomial_le(k, n, p) is None


def test_binomial_matches_a_hand_computed_value():
    assert U32.binomial_le(0, 1, 0.5) == pytest.approx(0.5)
    assert U32.binomial_le(2, 2, 0.3) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# The population is IMPORTED, never re-implemented
# --------------------------------------------------------------------------

def test_rows_basis_reproduces_the_parent_scripts_published_p_exactly():
    """A difference between the two can then only come from the denominator.

    Plant: re-implement `population` or `arm_of` locally and it drifts.
    """
    fx = ([row(E, "post", -1.0, f"E{i}") for i in range(19)]
          + [row(E, "post", 1.0, "E19")]
          + [row(C, "post", 1.0, f"W{i}") for i in range(5)]
          + [row(C, "post", -1.0, f"K{i}") for i in range(19)])
    parent = U32.BA.grade(fx)
    mine = U32.report(fx)
    assert mine["cells"]["rows_rows"]["p_value_one_sided"] == pytest.approx(
        parent["p_value_one_sided"], abs=1e-12)
    assert mine["cells"]["rows_rows"]["n"] == parent["arms"]["e35"]["n_post"]


def test_the_parents_exclusions_still_apply():
    v = U32.report([
        row(E, "post", 1.0, "P1", exit_reason="netting_attributed"),
        row("pairs_sol_eth_a", "post", 1.0, "P2"),
        row(E, "post", 1.0, "P3", acct="ib_paper"),
    ])
    assert v["population_n_rows"] == 0
    assert set(v["excluded"]) == {
        "fabricated_close", "pairs_sleeve_exonerated", "off_venue"}


def test_arm_of_is_behaviour_identical_to_the_inline_rule_it_replaced():
    """Pins the U32 refactor of `e35_break_attribution.grade`."""
    for leg, want in (("trend_donchian", "e35"),
                      ("trend_donchian_eth_4h", "e35"),
                      ("trend_donchian_eth", "control_same_family"),
                      ("btc_pullback_2h", "control_same_family"),
                      ("htf_pullback_x", "control_same_family"),
                      ("ict_scalp_5m", "control_other")):
        assert U32.arm_of(leg) == want


# --------------------------------------------------------------------------
# Fan-out actually collapses, and nothing is lost silently
# --------------------------------------------------------------------------

def test_fanned_out_rows_reduce_to_one_observation():
    v = U32.report([row(E, "post", -1.0, "P1", a) for a in
                    ("bybit_1", "bybit_2", "bybit_portfolio")]
                   + [row(C, "post", 1.0, "P2")])
    assert v["population_n_rows"] == 4 and v["population_n_packages"] == 2
    assert v["cells"]["rows_rows"]["n"] == 3
    assert v["cells"]["packages_packages"]["n"] == 1


def test_a_disagreeing_package_is_dropped_from_n_and_NAMED():
    """Plant: drop it silently and the denominator shrinks unaccountably."""
    v = U32.report([row(E, "post", 1.0, "D1", "bybit_1"),
                    row(E, "post", -1.0, "D1", "bybit_2"),
                    row(C, "post", 1.0, "P2")])
    assert v["cells"]["packages_packages"]["n"] == 0
    assert v["packages_basis"]["packages_disagreeing"] == ["D1"]
    assert v["packages_basis"]["placement"]["win_disagreement"] == 1


def test_a_package_straddling_the_deploy_is_reported_never_guessed():
    """Geometry is fixed at ENTRY, so two entry eras means no single era.

    Plant: assign it to `post` and it silently joins an arm it does not belong to.
    """
    v = U32.report([row(E, "pre", 1.0, "S1"), row(E, "post", 1.0, "S1"),
                    row(C, "post", 1.0, "P2")])
    assert v["packages_basis"]["packages_straddling_deploy"] == ["S1"]
    assert v["packages_basis"]["placement"]["mixed_era"] == 1
    assert v["cells"]["packages_packages"]["n"] == 0


def test_a_row_with_no_package_id_is_counted_never_synthesised():
    """A synthetic id restores the row denominator for exactly the rows we
    cannot place. Plant: `pid = pid or id(r)`."""
    v = U32.report([row(E, "post", -1.0, None), row(E, "post", -1.0, "P1"),
                    row(C, "post", 1.0, "P2")])
    assert v["packages_basis"]["rows_without_package"] == 1
    assert v["packages_basis"]["packages_seen"] == 2


# --------------------------------------------------------------------------
# The four cells, and the attribution they make possible
# --------------------------------------------------------------------------

def _offsetting_fixture():
    """The LIVE pooled shape: e35 1/20 rows, 1/14 packages; control 5/24 rows
    (0.208) but 4/16 packages (0.250)."""
    e35 = ([row(E, "post", 1.0, "EW")]
           + [row(E, "post", -1.0, f"EP{i}", a)
              for i in range(6) for a in ("bybit_1", "bybit_2")]
           + [row(E, "post", -1.0, f"ES{i}") for i in range(7)])
    ctl = ([row(C, "post", 1.0, "CW0", a) for a in ("bybit_1", "bybit_2")]
           + [row(C, "post", 1.0, f"CW{i}") for i in range(1, 4)]
           + [row(C, "post", -1.0, f"CP{i}", a)
              for i in range(7) for a in ("bybit_1", "bybit_2")]
           + [row(C, "post", -1.0, f"CS{i}") for i in range(5)])
    return e35 + ctl


def test_the_two_mixed_cells_isolate_opposite_terms():
    """Plant: `c = _cell(src[n_basis], ...)` — p tied to the n basis. That
    escaped an earlier fixture in which the CONTROL arm did not fan out, so the
    control's row rate and package rate coincided and the axis was untestable."""
    c = U32.report(_offsetting_fixture())["cells"]
    assert c["rows_rows"]["p_null"] != c["rows_packages"]["p_null"]
    assert c["rows_rows"]["n"] != c["packages_rows"]["n"]
    assert c["rows_rows"]["p_null"] == c["packages_rows"]["p_null"]
    assert c["rows_rows"]["n"] == c["rows_packages"]["n"]
    assert c["packages_packages"]["p_null"] == c["rows_packages"]["p_null"]


def test_offsetting_is_not_collapsed_into_no_crossing():
    """The live pooled population does exactly this: both PURE bases read
    not-significant while a MIXED cell crosses alpha, because the two
    substitutions move the p-value in opposite directions and cancel.

    Plant: `if False:` on the offsetting branch — reporting a cancellation as a
    confirmation, which is the reassuring direction and therefore the dangerous
    one.
    """
    v = U32.report(_offsetting_fixture())
    c = v["cells"]
    assert (c["rows_rows"]["k"], c["rows_rows"]["n"]) == (1, 20)
    assert (c["packages_packages"]["k"], c["packages_packages"]["n"]) == (1, 14)
    assert c["rows_rows"]["p_null"] == pytest.approx(5 / 24)
    assert c["packages_packages"]["p_null"] == pytest.approx(0.25)
    assert c["rows_rows"]["significant"] is False
    assert c["packages_packages"]["significant"] is False
    assert c["rows_packages"]["significant"] is True
    assert v["driver"]["state"] == "offsetting"


def test_no_crossing_when_the_substitution_changes_no_decision():
    v = U32.report([row(E, "post", 1.0, f"E{i}") for i in range(10)]
                   + [row(C, "post", 1.0, f"W{i}") for i in range(5)]
                   + [row(C, "post", -1.0, f"K{i}") for i in range(5)])
    assert v["driver"]["state"] == "no_crossing"


def test_an_empty_control_arm_refuses_in_every_field():
    """Plant: `bool(val is not None and val < ALPHA)` — an uncomputable cell
    reading `significant=False` has not been SHOWN non-significant."""
    v = U32.report([row(E, "post", -1.0, "E1")])
    c = v["cells"]["rows_rows"]
    assert c["p_value_one_sided"] is None
    assert c["significant"] is None
    assert c["degenerate_null"] is None
    assert v["driver"]["state"] == "not_computable"


def test_every_driver_state_is_declared():
    for fx in ([row(E, "post", -1.0, "E1")],
               _offsetting_fixture(),
               [row(E, "post", 1.0, f"E{i}") for i in range(4)]
               + [row(C, "post", 1.0, "W0"), row(C, "post", -1.0, "K0")]):
        assert U32.report(fx)["driver"]["state"] in U32.DRIVER_STATES


# --------------------------------------------------------------------------
# A degenerate null is flagged, not hidden and not refused
# --------------------------------------------------------------------------

def test_a_control_arm_with_zero_wins_is_flagged_vacuous():
    """P(X<=k | p=0) is 1.0 for every k — real arithmetic carrying no evidence.
    Plant: `"degenerate_null": False`."""
    v = U32.report([row(E, "post", -1.0, f"E{i}") for i in range(5)]
                   + [row(C, "post", -1.0, f"K{i}") for i in range(4)])
    c = v["cells"]["rows_rows"]
    assert c["p_null"] == 0.0
    assert c["degenerate_null"] is True
    assert c["p_value_one_sided"] == pytest.approx(1.0)
    assert "vacuous" in U32.render(v)


def test_the_degenerate_flag_has_a_negative_case():
    v = U32.report([row(E, "post", -1.0, "E1"), row(C, "post", 1.0, "W1"),
                    row(C, "post", -1.0, "K1")])
    assert v["cells"]["rows_rows"]["degenerate_null"] is False


# --------------------------------------------------------------------------
# Scope is ALWAYS stated — U31's transferable finding
# --------------------------------------------------------------------------

def test_scope_is_named_pooled_rather_than_left_implicit():
    assert U32.report([row(E, "post", 1.0, "P1")])["account_scope"] == "pooled:bybit_*"


def test_a_single_account_scope_has_no_fanout_to_collapse():
    """Fan-out is CROSS-account, so a per-account scope is essentially immune.
    Plant: filter on something other than account_id and inflation moves."""
    fx = [row(E, "post", -1.0, "P1", a) for a in ("bybit_1", "bybit_2")] + \
         [row(C, "post", 1.0, "P2", "bybit_1")]
    assert U32.report(fx)["inflation"] > 1.0
    v = U32.report(fx, account="bybit_1")
    assert v["account_scope"] == "bybit_1"
    assert v["inflation"] == 1.0
    assert v["population_n_rows"] == 2


def test_render_never_crashes_on_a_refusing_report():
    assert "not_computable" in U32.render(U32.report([row(E, "post", -1.0, "E1")]))



# --------------------------------------------------------------------------
# The DESCRIPTION is re-derived alongside the inference
# --------------------------------------------------------------------------

def test_an_arms_pre_side_collapses_too_not_only_its_post_side():
    v = U32.report([row(E, "pre", 1.0, "QP", a) for a in ("bybit_1", "bybit_2")]
                   + [row(E, "post", -1.0, "QQ")]
                   + [row(C, "pre", 1.0, "RP"), row(C, "post", -1.0, "RQ")])
    d = v["deltas"]["e35"]
    assert d["rows"]["n_pre"] == 2 and d["packages"]["n_pre"] == 1
    assert d["stability"] == "stable"


def test_a_delta_sign_flip_is_its_own_state():
    """The live family-matched control does this: +0.8pp on rows, -8.3pp on
    packages — opposite conclusions about whether the break is e35-specific.

    Plant: `elif False:` on the sign-flip branch, which reports a reversal as
    stability.
    """
    v = U32.report([row(C, "pre", 1.0, "SW")]
                   + [row(C, "pre", -1.0, f"SP{i}", a)
                      for i in range(2) for a in ("bybit_1", "bybit_2")]
                   + [row(C, "post", 1.0, "TW")]
                   + [row(C, "post", -1.0, f"TL{i}") for i in range(3)]
                   + [row(E, "post", -1.0, "E1")])
    d = v["deltas"]["control_same_family"]
    assert d["rows"]["delta_pp"] == pytest.approx(5.0)
    assert d["packages"]["delta_pp"] == pytest.approx(-100.0 / 12)
    assert d["stability"] == "sign_flip"
    assert "SIGN FLIP" in U32.render(v)


def test_an_empty_era_reports_none_not_zero():
    """Plant: `_pct` returning 0.0 on an empty cell."""
    v = U32.report([row(E, "post", -1.0, "E1")])
    d = v["deltas"]["e35"]
    assert d["rows"]["win_rate_pre"] is None
    assert d["stability"] == "not_computable"


def test_every_stability_state_is_declared():
    v = U32.report([row(E, "pre", 1.0, "A"), row(E, "post", -1.0, "B"),
                    row(C, "post", 1.0, "C")])
    for arm in U32.ARMS:
        assert v["deltas"][arm]["stability"] in U32.DELTA_STABILITY_STATES
