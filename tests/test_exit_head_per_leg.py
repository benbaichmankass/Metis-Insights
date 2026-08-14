"""Per-leg verdict cut in the M20 exit-head trainer.

WHY THESE EXIST. `train_exit_head.py` trains and evaluates per FAMILY — one E0
dir pools every symbol in it. That is correct for training (it is what breaks
the label-count wall the exit-head program doc describes) and wrong as a
verdict unit, because `docs/research/exit-refinement-coverage.json` carries one
row per LEG.

Writing a pooled family verdict into each of that family's leg rows is
`BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS` — the bug the matrix rows
were exploded per-leg to kill, reappearing one layer up where it is harder to
see. `per_leg_summary` is the guard against that, so it needs tests that fail
when the guard stops guarding.

Each test below was verified against a planted defect: the assertion was
confirmed to FAIL when the behaviour it names is removed. A test that passes
against a broken implementation is worse than no test.
"""
from __future__ import annotations

import copy
import json
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

np = pytest.importorskip("numpy")


def _load():
    spec = importlib.util.spec_from_file_location(
        "train_exit_head", REPO / "scripts" / "ml" / "train_exit_head.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


teh = _load()


def block(n, auc, best_net, best_dd, actual_net, actual_dd, hard_net):
    """One leg's block inside one fold, in `eval_split`'s shape."""
    return {
        "n_trades": n, "auc": auc,
        "actual": {"net_r": actual_net, "max_dd_r": actual_dd},
        "stale_8_0": {"net_r": hard_net, "max_dd_r": 5.0},
        "giveback_1_1": {"net_r": hard_net - 1, "max_dd_r": 5.0},
        "model": {"tau_0.10": {"net_r": best_net, "max_dd_r": best_dd}},
    }


@pytest.fixture()
def folds():
    """Two folds, three legs with deliberately different truths.

    legA fat + head wins · legB fat + head loses · legC thin + head "wins".
    Pooled, these are one number (146 OOS trades, mixed) — which is precisely
    the conflation under test.
    """
    return [
        {"year": year, "per_leg": {
            "legA": block(30, 0.62, 10.0, 4.0, 5.0, 6.0, 7.0),
            "legB": block(40, 0.51, 2.0, 9.0, 8.0, 4.0, 7.0),
            "legC": block(3, 0.70, 9.0, 1.0, 1.0, 5.0, 2.0),
        }}
        for year in (2024, 2025)
    ]


def test_the_floor_is_single_homed_to_the_fleet_sweep():
    """The floor must be IMPORTED, not mirrored.

    Two copies of a threshold governing one matrix is how they drift apart.
    """
    from m20_fleet_exit_sweep import MIN_OOS_TRADES as sweep_floor
    assert teh.MIN_OOS_TRADES == sweep_floor == 25


def test_each_leg_gets_its_own_verdict_not_the_family_number(folds):
    s = teh.per_leg_summary(folds, teh.MIN_OOS_TRADES)
    assert s["legA"]["verdict"] == "candidate"
    assert s["legB"]["verdict"] == "honest_negative"
    # Three legs, three different verdicts, from ONE pooled model+fold set.
    assert len({s[leg]["verdict"] for leg in s}) == 3


def test_a_thin_leg_is_insufficient_base_and_not_a_negative(folds):
    """`insufficient_base` must stay distinct from `honest_negative`.

    Folding a too-thin book into the failure bucket makes "we could not judge"
    indistinguishable from "we judged and the lever lost" — opposite claims.
    """
    s = teh.per_leg_summary(folds, teh.MIN_OOS_TRADES)
    legc = s["legC"]
    assert legc["oos_trades"] == 6 < teh.MIN_OOS_TRADES
    assert legc["verdict"] == "insufficient_base"
    assert legc["verdict"] != "honest_negative"
    # The counterfactual keeps the floor's effect auditable.
    assert legc["would_have_been"] == "candidate"
    assert str(teh.MIN_OOS_TRADES) in legc["insufficient_base_why"]


def test_an_unimportable_floor_withholds_verdicts_rather_than_inventing_one(folds):
    """floor=None is a THIRD state, not a default.

    Grading against a locally invented number would silently produce verdicts
    under a threshold no operator set.
    """
    s = teh.per_leg_summary(folds, None)
    assert {v["verdict"] for v in s.values()} == {"ungraded_no_floor"}
    assert all("would_have_been" not in v for v in s.values())
    assert all(v["min_oos_trades_floor"] is None for v in s.values())


def test_a_leg_absent_from_a_fold_reduces_usable_folds_rather_than_losing_it(folds):
    """A leg that did not trade in a fold cannot vote either way.

    Counting its absence as a loss would make a leg that traded in one fold
    look like a leg that failed in two.
    """
    f = copy.deepcopy(folds)
    del f[0]["per_leg"]["legA"]
    s = teh.per_leg_summary(f, teh.MIN_OOS_TRADES)
    assert s["legA"]["usable_folds"] == 1
    assert s["legA"]["beats_actual_folds"] == 1


def test_worse_drawdown_fails_even_when_net_r_improves(folds):
    """net_R alone is not the gate — the maxDD clause is load-bearing.

    Dropping it is how a lever that buys return with risk ships looking clean.
    """
    f = copy.deepcopy(folds)
    for fold in f:
        fold["per_leg"]["legA"]["model"]["tau_0.10"]["max_dd_r"] = 99.0
    s = teh.per_leg_summary(f, teh.MIN_OOS_TRADES)
    assert s["legA"]["verdict"] == "honest_negative"


def test_beating_actual_but_not_the_hard_lever_is_not_a_candidate(folds):
    """The gate is vs the best HARD rule, not vs doing nothing.

    A head that only beats the un-levered book has not earned a place over the
    stale/giveback stop that already exists.
    """
    f = copy.deepcopy(folds)
    for fold in f:
        fold["per_leg"]["legA"]["stale_8_0"]["net_r"] = 50.0
    s = teh.per_leg_summary(f, teh.MIN_OOS_TRADES)
    assert s["legA"]["beats_hard_folds"] == 0
    assert s["legA"]["verdict"] == "honest_negative"


def test_split_by_leg_partitions_on_the_strategy_field():
    trades = {
        "t1": [{"strategy": "ict_scalp_sol_15m", "age_bars": 0}],
        "t2": [{"strategy": "ict_scalp_xrp_15m", "age_bars": 0}],
        "t3": [{"strategy": "ict_scalp_sol_15m", "age_bars": 0}],
    }
    out = teh.split_by_leg(trades)
    assert set(out) == {"ict_scalp_sol_15m", "ict_scalp_xrp_15m"}
    assert set(out["ict_scalp_sol_15m"]) == {"t1", "t3"}


# --------------------------------------------------------------- vintage cut
#
# The coverage headline is computed over a population whose verdicts were
# largely measured against a TP geometry production does not run
# (BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP). These pin the
# caveat's scoping, because a caveat that over-claims becomes alarm fatigue and
# one that under-claims hides the defect.

def _rollup():
    spec = importlib.util.spec_from_file_location(
        "m20_coverage_rollup",
        REPO / "scripts" / "research" / "m20_coverage_rollup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rollup = _rollup()


def _matrix(rows):
    return {"lever_columns": ["stale_stop"], "legend": {}, "rows": rows}


def _row(strategy, status="honest_negative", ref="sweep 2026-07-12"):
    return {"strategy": strategy, "symbol": "X", "tf": "1h",
            "execution": "live", "stale_stop": {"status": status, "ref": ref}}


def test_the_scalp_family_is_not_swept_into_the_geometry_caveat():
    """ict_scalp's harness MODELS its TP, so its verdicts are not stale.

    Blanket-flagging every family would make the caveat unusable — the
    desensitized-alarm failure the repo treats as its own P1.
    """
    v = rollup.evidence_vintage(_matrix([_row("ict_scalp_sol_15m")]))
    assert v["classifier_available"]
    assert v["affected_legs"] == 0 and v["clean_legs"] == 1
    assert v["pre_cutover"] == 0


def test_an_affected_family_leg_with_old_evidence_is_counted_stale():
    v = rollup.evidence_vintage(_matrix([_row("trend_donchian_eth_4h")]))
    assert v["affected_legs"] == 1
    assert v["pre_cutover"] == 1
    assert v["stale_cells"][0][4] == "2026-07-12"


def test_evidence_at_or_after_the_cutover_is_not_stale():
    v = rollup.evidence_vintage(
        _matrix([_row("trend_donchian_eth_4h", ref="re-swept 2026-08-11")]))
    assert v["pre_cutover"] == 0 and v["post_cutover"] == 1


def test_an_undated_ref_is_its_own_bucket_not_silently_clean():
    """No date is 'we cannot tell', which is not the same as 'current'."""
    v = rollup.evidence_vintage(
        _matrix([_row("trend_donchian_eth_4h", ref="no date here")]))
    assert v["undated"] == 1
    assert v["pre_cutover"] == 0 and v["post_cutover"] == 0


def test_an_open_cell_is_not_counted_as_stale_evidence():
    """A pending cell owes a measurement regardless — counting it as stale
    would double-count it against the done-condition."""
    v = rollup.evidence_vintage(
        _matrix([_row("trend_donchian_eth_4h", status="pending", ref="x 2026-07-12")]))
    assert v["pre_cutover"] == 0 and v["undated"] == 0


def test_the_vintage_denominator_equals_the_declared_lever_set():
    """Guards the comment's claim that the lever filter currently filters
    nothing — if a lever is dropped from the sensitive set, the caveat's
    denominator silently shrinks and the staleness reads better than it is."""
    matrix = json.loads(
        (REPO / "docs" / "research" / "exit-refinement-coverage.json").read_text())
    assert set(matrix["lever_columns"]) <= rollup.GEOMETRY_SENSITIVE_LEVERS


# ------------------------------------------- per-lever cutover + tp_geometry
#
# ONE CUTOVER DATE WAS A WRONG ANSWER SHAPED LIKE A RIGHT ONE
# (BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP). `GEOMETRY_CUTOVER` is
# when the LEVER-SWEEP harness learned to place the live capped TP;
# `exit_head_ml` rides `m20_exit_head_round.py`, whose own fix landed four days
# later. Grading both against one date cleared twelve cells that were still
# measured on a no-take-profit book.
#
# And the date is only a PROXY. It failed outright on the three SHIPPED
# `trend_donchian*` 1h cells: their `RE-SWEPT 2026-08-14` ref is a genuine
# measurement on that date which re-read the EXISTING round dirs, so the cell is
# fresh by date and stale by geometry simultaneously. No cutover date separates
# those — hence `tp_geometry`, a declared measurement that overrides the date in
# both directions.

def _ehm_matrix(rows):
    return {"lever_columns": ["exit_head_ml"], "legend": {}, "rows": rows}


def _ehm_row(strategy, status="shipped", ref="swept 2026-08-11", geom=None):
    cell = {"status": status, "ref": ref}
    if geom is not None:
        cell["tp_geometry"] = geom
    return {"strategy": strategy, "symbol": "X", "tf": "1h",
            "execution": "live", "exit_head_ml": cell}


def test_exit_head_ml_uses_its_own_later_cutover():
    """CAN-FAIL CONTROL: the same date, stale on one lever and not the other.

    Pins the mechanism rather than the outcome — if the per-lever map is
    dropped, this fails instead of the caveat quietly under-reporting.
    """
    assert rollup.cutover_for("exit_head_ml") > rollup.cutover_for("stale_stop")
    date = "swept 2026-08-11"          # after the sweep fix, before the driver fix
    ehm = rollup.evidence_vintage(_ehm_matrix([_ehm_row("trend_donchian_eth_4h",
                                                        ref=date)]))
    other = rollup.evidence_vintage(_matrix([_row("trend_donchian_eth_4h",
                                                  ref=date)]))
    assert ehm["pre_cutover"] == 1, "exit_head_ml must grade against its own fix"
    assert other["post_cutover"] == 1, "the default lever must be unaffected"


def test_an_unlisted_lever_falls_back_to_the_default_cutover():
    """The map is an override list, not the source of truth for every lever."""
    assert rollup.cutover_for("a_lever_that_does_not_exist") == \
        rollup.GEOMETRY_CUTOVER


def test_declared_no_take_profit_beats_a_fresh_date():
    """The measured fact overrides the proxy. This is the case a date CANNOT
    catch: a real re-sweep, dated today, that re-read a no-TP round."""
    v = rollup.evidence_vintage(_ehm_matrix([
        _ehm_row("trend_donchian_eth_4h", ref="RE-SWEPT 2026-09-30",
                 geom=rollup.GEOMETRY_NO_TP)]))
    assert v["pre_cutover"] == 1
    assert [d[1] for d in v["stale_decisions"]] == ["exit_head_ml"]


def test_declared_live_parity_beats_an_old_date():
    """Overrides in BOTH directions, or it is a one-way alarm rather than a
    statement of fact — a round that DID place the live TP is not stale just
    because it ran before the fix date."""
    v = rollup.evidence_vintage(_ehm_matrix([
        _ehm_row("trend_donchian_eth_4h", ref="swept 2026-07-01",
                 geom=rollup.GEOMETRY_LIVE_PARITY)]))
    assert v["stale_decisions"] == []
    assert v["pre_cutover"] == 0


def test_an_undeclared_geometry_is_counted_not_assumed_clean():
    """'We did not look' must stay visible beside the date-graded verdict."""
    v = rollup.evidence_vintage(_ehm_matrix([_ehm_row("trend_donchian_eth_4h")]))
    assert v["geometry_undeclared"] == 1
    v2 = rollup.evidence_vintage(_ehm_matrix([
        _ehm_row("trend_donchian_eth_4h", geom=rollup.GEOMETRY_NO_TP)]))
    assert v2["geometry_undeclared"] == 0


def test_the_three_shipped_donchian_1h_cells_are_live_stale_decisions():
    """The real matrix, not a fixture: these three change exit behaviour on
    real money today, on a round measured to contain zero take-profit exits.
    They evaded the stale list for two days because the only test was a date."""
    matrix = json.loads(
        (REPO / "docs" / "research" / "exit-refinement-coverage.json").read_text())
    v = rollup.evidence_vintage(matrix)
    flagged = {leg for leg, lever, *_ in v["stale_decisions"]
               if lever == "exit_head_ml"}
    assert flagged == {"trend_donchian", "trend_donchian_eth",
                       "trend_donchian_sol"}, flagged


# ---------------------------------------------------------------------------
# resolve_data: the PROXY map must not SHADOW native data for a consumer that
# refuses proxies (BL-20260814-PROXY-MAP-SHADOWS-NATIVE-DATA). The map is
# documented as being "for futures without their own file", but it was applied
# unconditionally — so `m20_exit_head_round`, which SKIPs any proxied leg
# ("native history required for head training"), could never see a native pull.
# Depth-vs-fidelity is a real trade-off, so these pin BOTH directions: the
# lever-sweep default must stay proxy-first (the proxy is the deeper series).

def _sweep():
    spec = importlib.util.spec_from_file_location(
        "m20_fleet_exit_sweep",
        REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sweep = _sweep()


def test_mgc_is_proxied_to_gc_f_so_the_fixture_is_meaningful():
    """Denominator for the two tests below: if MGC ever stops being proxied
    they would pass vacuously, testing nothing."""
    assert sweep.PROXY_DATA.get("MGC") == "GC_F"


def test_the_default_still_prefers_the_deep_proxy_over_shallow_native(tmp_path):
    """The lever sweeps WANT the proxy: GC_F reaches the full fold structure
    while native IBKR history is ~1y. Preferring native here would collapse a
    6-fold walk-forward and silently invalidate every recorded verdict."""
    (tmp_path / "GC_F_1d.csv").write_text("timestamp,open,high,low,close,volume\n")
    (tmp_path / "MGC_1d.csv").write_text("timestamp,open,high,low,close,volume\n")
    path, proxy, _ = sweep.resolve_data("MGC", "1d", tmp_path)
    assert proxy is True
    assert Path(path).name == "GC_F_1d.csv"


def test_prefer_native_reaches_a_native_file_the_default_cannot_see(tmp_path):
    """The head-training path. Without this the refusal below is
    unconditional for every symbol in PROXY_DATA, whatever is on disk."""
    (tmp_path / "GC_F_1d.csv").write_text("timestamp,open,high,low,close,volume\n")
    (tmp_path / "MGC_1d.csv").write_text("timestamp,open,high,low,close,volume\n")
    path, proxy, _ = sweep.resolve_data("MGC", "1d", tmp_path, prefer_native=True)
    assert proxy is False, "native data present but still reported as proxied"
    assert Path(path).name == "MGC_1d.csv"


def test_prefer_native_falls_back_to_the_proxy_when_no_native_exists(tmp_path):
    """prefer_native is a PREFERENCE, not a requirement — a leg with only
    proxy data must still resolve (and still be flagged proxied)."""
    (tmp_path / "GC_F_1d.csv").write_text("timestamp,open,high,low,close,volume\n")
    path, proxy, _ = sweep.resolve_data("MGC", "1d", tmp_path, prefer_native=True)
    assert proxy is True
    assert Path(path).name == "GC_F_1d.csv"


def test_a_missing_proxy_file_still_reads_data_missing_by_default(tmp_path):
    """The default must NOT gain a native fallback: `data_missing` is more
    honest than silently resolving a shallow native file into a 1-fold run."""
    (tmp_path / "MGC_1d.csv").write_text("timestamp,open,high,low,close,volume\n")
    path, proxy, _ = sweep.resolve_data("MGC", "1d", tmp_path)
    assert path is None
    assert proxy is True


def test_a_symbol_with_no_proxy_is_unaffected_in_both_modes(tmp_path):
    """Crypto legs have no PROXY_DATA entry, so neither mode may change them."""
    (tmp_path / "BTCUSDT_1h.csv").write_text("timestamp,open,high,low,close,volume\n")
    for kw in ({}, {"prefer_native": True}):
        path, proxy, resample = sweep.resolve_data("BTCUSDT", "1h", tmp_path, **kw)
        assert proxy is False and resample is None
        assert Path(path).name == "BTCUSDT_1h.csv"


def test_the_exit_head_round_asks_for_native_first():
    """The wiring, not just the capability: the refusal and the preference
    must live in the same call or the cells stay unreachable."""
    src = (REPO / "scripts" / "research" / "m20_exit_head_round.py").read_text()
    assert "prefer_native=True" in src
