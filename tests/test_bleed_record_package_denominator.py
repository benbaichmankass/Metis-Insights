"""The bleed record's own rates must state which denominator they rest on.

`BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`
clause 1. `scripts/research/bleed_attribution_2026_09_11.py` is the script behind
the −$38,851.81 figure `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
rests on, and `order_package_id` appears in it ZERO times (positive control:
`account_id` appears twice).

These tests pin the properties that make the re-derivation trustworthy:

1. **The partition is the deliverable, and it is completeness-checked against the
   LIVE `summarise`.** No checker can read which question a rate answers — the
   backlog row says so — but a checker CAN notice a key nobody declared.
2. **A money key is an ACCOUNT fact and must not be re-derived per package.**
   Each fanned-out row is a real position that really lost that money; deduping
   would understate it. This is the likelier mistake, because a package
   denominator sounds uniformly more careful.
3. **`disagreement` is a state, never arbitrated** — U15 measured that which
   fan-out row you believe swings a headline 4.3–28.2pp.
4. **The verdict does not collapse "did the conclusion change" into "did the
   evidence weaken".** A single verdict string got this wrong on live data.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


U31 = _load("_bleed_pkg_denom_test",
            "scripts/research/bleed_record_package_denominator.py")


def _row(pnl, post, sl=None, tp=None, px=None, pkg="p1", acct="bybit_1",
         strat="trend_donchian_eth"):
    return {"status": "closed", "is_backtest": 0, "pnl": pnl,
            "exit_reason": "sl_cross", "strategy_name": strat, "direction": "long",
            "order_package_id": pkg, "account_id": acct,
            "created_at": "2026-09-01T00:00:00Z" if post else "2026-08-01T00:00:00Z",
            "stop_loss": sl, "take_profit_1": tp, "exit_price": px,
            "pnl_source": "local_compute", "exit_price_source": "exchange_fill"}


# --- 1. the partition ----------------------------------------------------- #

def test_the_script_under_audit_really_does_not_read_the_package_id():
    """The premise, with a positive control — not taken from prose."""
    src = open(os.path.join(_REPO,
               "scripts/research/bleed_attribution_2026_09_11.py")).read()
    assert "order_package_id" not in src
    assert "account_id" in src, "positive control: the probe can find a field it DOES read"


def test_every_key_the_live_summarise_emits_is_declared():
    """Checked by CALLING summarise, never by reading it — a key added tomorrow
    must show up as `undeclared`, which is the finding."""
    live = U31.summarise([_row(-1.0, True, sl=10.0, tp=99.0, px=10.0)], 0.001)
    census = U31.basis_census(live)
    assert census["undeclared"] == [], census["undeclared"]
    assert census["complete"]


def test_an_undeclared_key_is_the_finding_never_a_pass():
    live = U31.summarise([_row(-1.0, True)], 0.001)
    assert U31.basis_census({**live, "brand_new_rate": 0.5})["undeclared"] \
        == ["brand_new_rate"]
    assert U31.declare_basis("brand_new_rate") == "undeclared"


def test_undeclared_is_only_ever_derived_never_declared():
    assert "undeclared" not in U31.KEY_BASIS.values()
    assert set(U31.KEY_BASIS.values()) <= set(U31.BASIS_STATES)


@pytest.mark.parametrize("key", ["pnl_sum", "pnl_sum_measured_only", "pnl_mean",
                                 "pnl_median", "loss_mean", "win_mean",
                                 "measured_n", "pnl_provenance", "pnl_coverage"])
def test_money_keys_are_account_facts_and_are_not_re_derived(key):
    """Each fanned-out row is a real position on a real account that really lost
    that money. A package denominator would UNDERSTATE money actually lost."""
    assert U31.declare_basis(key) == "account_fact"


@pytest.mark.parametrize("key", ["win_rate", "win_rate_ci95", "wins",
                                 "stop_rate_adjudicated", "stop_rate_ci95",
                                 "target_rate_adjudicated", "stops_adjudicated",
                                 "gradeable_n", "adjudication"])
def test_rate_keys_are_price_path(key):
    assert U31.declare_basis(key) == "price_path"


def test_the_report_always_carries_the_account_fact_caveat():
    rep = U31.report([_row(-1.0, True, sl=10.0, tp=99.0, px=10.0)], 0.001)
    assert "ACCOUNT FACT" in rep["headline_caveat"]
    assert "UNDERSTATE" in rep["headline_caveat"]


def test_the_report_always_states_its_account_scope():
    """`bleed_attribution`'s headline is bybit_1; `population()` is not scoped,
    so an unscoped run grades the fleet and its pnl_sum is NOT the bleed record."""
    rows = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, acct="bybit_1"),
            _row(-9.0, True, sl=10.0, tp=99.0, px=10.0, acct="bybit_2", pkg="p2")]
    assert U31.report(rows, 0.001)["population"]["account_scope"] == "ALL_ACCOUNTS"
    scoped = U31.report(rows, 0.001, account="bybit_1")
    assert scoped["population"]["account_scope"] == "bybit_1"
    assert scoped["population"]["in_population"] == 1


# --- 2/3. the reduction --------------------------------------------------- #

def test_fan_out_legs_of_one_package_are_one_observation():
    pr = U31.package_rates([_row(-1.0, True, pkg="pA"), _row(-2.0, True, pkg="pA")],
                           0.001)
    assert (pr["packages"], pr["rows"], pr["inflation"]) == (1, 2, 2.0)


def test_a_disagreeing_package_is_reported_never_arbitrated_into_an_arm():
    """U15: which fan-out row you believe swings a headline 4.3–28.2pp."""
    rows = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, pkg="pB"),
            _row(+1.0, True, sl=10.0, tp=99.0, px=99.0, pkg="pB")]
    pr = U31.package_rates(rows, 0.001)
    assert pr["package_states"]["disagreement"] == 1
    assert pr["gradeable_packages"] == 0, "a disagreement must reach NEITHER arm"
    assert pr["stop_rate_adjudicated"] is None


def test_an_empty_denominator_yields_none_never_zero():
    """0.0 would read as 'no stop-outs' when it means 'we could not look'."""
    empty = U31.package_rates([], 0.001)
    assert empty["win_rate"] is None and empty["stop_rate_adjudicated"] is None
    assert empty["stop_rate_ci95"] is None


def test_rows_without_a_package_are_counted_not_dropped_silently():
    rows = [_row(-1.0, True, pkg="pA"), {**_row(-1.0, True), "order_package_id": None}]
    assert U31.package_rates(rows, 0.001)["rows_without_package"] >= 1


# --- 4. the verdict does not collapse two questions ----------------------- #

def test_the_mislabel_that_reached_live_data():
    """b4_geometry, row p=0.3043 -> package p=0.4643. The first version called
    this `loses_significance` — significance it never had."""
    v = U31._verdict(0.3043, 0.4643)
    assert v["significance"] == "neither_significant"
    assert v["strength"] == "weaker"
    assert v["conclusion_changed"] is False


def test_its_twin_a_p_that_improves_without_crossing_changed_no_conclusion():
    """untouched_control, 0.22025 -> 0.10528. The first version called it
    `strengthens`, which reads as a conclusion getting firmer."""
    v = U31._verdict(0.22025, 0.10528)
    assert v["strength"] == "stronger"
    assert v["conclusion_changed"] is False


def test_a_conclusion_that_crosses_alpha_upward_is_lost():
    v = U31._verdict(8e-05, 0.05703)
    assert v["significance"] == "lost" and v["conclusion_changed"] is True


def test_weakening_inside_alpha_changes_no_conclusion():
    v = U31._verdict(0.0001, 0.03)
    assert v["significance"] == "both_significant"
    assert v["strength"] == "weaker" and v["conclusion_changed"] is False


def test_a_missing_arm_is_not_computable_on_both_axes():
    assert U31._verdict(None, 0.01) == {"significance": "not_computable",
                                        "strength": "not_computable",
                                        "conclusion_changed": False}


def test_every_declared_state_is_reachable():
    sig = {U31._verdict(a, b)["significance"] for a, b in
           ((0.001, 0.01), (0.001, 0.20), (0.20, 0.01), (0.20, 0.30), (None, 0.01))}
    assert sig == set(U31.SIGNIFICANCE_STATES)
    st = {U31._verdict(a, b)["strength"] for a, b in
          ((0.01, 0.20), (0.20, 0.01), (0.03, 0.03), (None, 0.01))}
    assert st == set(U31.STRENGTH_STATES)


def test_an_empty_arm_yields_p_none_never_one():
    """p=1.0 would read as 'measured and not significant'."""
    post = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, pkg=f"r{i}") for i in range(3)]
    assert U31._prepost([], post, 0.001)["per_row"]["p"] is None
    assert U31._prepost([], post, 0.001)["per_row"]["state"] == "no_denominator"


def test_with_no_fan_out_both_denominators_agree():
    """THE NULL CONTROL. Without it, every difference below proves nothing."""
    pre = [_row(-1.0, False, sl=10.0, tp=99.0, px=10.0, pkg=f"q{i}") for i in range(4)]
    post = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, pkg=f"r{i}") for i in range(4)]
    f = U31._prepost(pre, post, 0.001)
    assert f["per_row"]["pre"] == f["per_package"]["pre"]
    assert f["per_row"]["post"] == f["per_package"]["post"]


def test_with_fan_out_the_package_denominator_is_smaller():
    pre = [_row(-1.0, False, sl=10.0, tp=99.0, px=10.0, pkg=f"q{i}") for i in range(4)]
    fanned = [_row(-1.0, True, sl=10.0, tp=99.0, px=10.0, pkg="one") for _ in range(4)]
    f = U31._prepost(pre, fanned, 0.001)
    assert f["per_package"]["post"][1] < f["per_row"]["post"][1]


def test_the_self_test_passes():
    assert U31.self_test() == 0
