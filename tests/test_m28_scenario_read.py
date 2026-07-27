"""M36 Track C · C1 — tests for the scenario_read adapter (M29→M28 merge seam).

Pure, offline, synthetic ensembles — no sysdyn run, no I/O. Exercises the
distribution summary, the direction-aligned c_scenario lens, and the conservative
observe-only fold into a TradeThesis (annotate, never override).
"""

from __future__ import annotations

from src.units.strategies.macro_thesis.scenario_read import (
    apply_to_thesis,
    read_scenario,
    summarize_ensemble,
)
from src.units.strategies.macro_thesis.thesis import TradeThesis


def _thesis(**kw) -> TradeThesis:
    base = dict(thesis_id="mth-t1", created_at="2026-07-27T00:00:00Z",
                updated_at="2026-07-27T00:00:00Z", status="draft", direction="long")
    base.update(kw)
    return TradeThesis(**base)


def test_summarize_ensemble_stats():
    # base 100; outcomes mostly above → p_up high, positive expected move
    s = summarize_ensemble([104, 108, 96, 110, 102], 100, driver="MNG", horizon_days=14)
    assert s.n == 5
    assert s.base_value == 100.0
    assert s.p_up == 0.8  # 4 of 5 above 100
    assert s.mean == 104.0
    assert abs(s.expected_move_pct - 0.04) < 1e-9
    assert s.q50 == 104.0
    assert s.dispersion is not None and s.dispersion > 0


def test_summarize_ensemble_empty_and_no_base():
    e = summarize_ensemble([], 100, driver="X")
    assert e.n == 0 and e.p_up is None and "empty" in (e.note or "")
    nb = summarize_ensemble([1, 2, 3], None, driver="X")
    assert nb.n == 3 and nb.p_up is None and nb.expected_move_pct is None
    assert "no base_value" in (nb.note or "")


def test_summarize_drops_nonnumeric():
    s = summarize_ensemble([100, "bad", None, True, 120], 100)
    assert s.n == 2  # only 100 and 120 (True is rejected as bool)


def test_read_scenario_long_vs_short():
    s = summarize_ensemble([104, 108, 96, 110, 102], 100)  # p_up 0.8
    long_read = read_scenario(s, "long")
    assert long_read.c_scenario == 0.8
    assert abs(long_read.conviction_signed - 0.6) < 1e-9
    short_read = read_scenario(s, "short")
    assert abs(short_read.c_scenario - 0.2) < 1e-9  # 1 - 0.8
    assert abs(short_read.conviction_signed - (-0.6)) < 1e-9


def test_read_scenario_no_direction_or_no_base():
    s = summarize_ensemble([104, 108, 96], 100)
    none_dir = read_scenario(s, None)
    assert none_dir.c_scenario is None  # direction-agnostic
    assert none_dir.snapshot["p_up"] is not None  # distribution still carried
    bad_dir = read_scenario(s, "sideways")
    assert bad_dir.c_scenario is None
    nb = read_scenario(summarize_ensemble([1, 2, 3], None), "long")
    assert nb.c_scenario is None  # no base → no p_up → no lens


def test_apply_annotates_but_never_overrides():
    th = _thesis(direction="long")  # no target, no horizon, no conviction
    s = summarize_ensemble([104, 108, 96, 110, 102], 100, driver="MNG", horizon_days=14)
    read = read_scenario(s, "long")
    out = apply_to_thesis(th, read, updated_at="2026-07-27T01:00:00Z")

    # snapshot + provenance recorded
    assert out.macro_context["scenario"]["driver"] == "MNG"
    assert out.macro_context["scenario"]["c_scenario"] == 0.8
    assert out.conviction_provenance["c_scenario"]["value"] == 0.8
    # target + horizon filled from the model (were unset)
    assert out.target["source"] == "scenario"
    assert abs(out.target["expected_move_pct"] - 0.04) < 1e-9
    assert out.horizon_days == 14
    # NEVER touched: direction, thesis_conviction
    assert out.direction == "long"
    assert out.thesis_conviction is None
    # immutability: the input thesis is unchanged
    assert th.target == {} and th.horizon_days is None


def test_apply_does_not_overwrite_existing_target_or_horizon():
    th = _thesis(direction="short", target={"source": "former", "price": 88.0}, horizon_days=30)
    s = summarize_ensemble([90, 85, 80], 100, driver="TLT", horizon_days=14)
    out = apply_to_thesis(th, read_scenario(s, "short"), updated_at="x")
    # former's target + horizon win; scenario only annotates macro_context/provenance
    assert out.target == {"source": "former", "price": 88.0}
    assert out.horizon_days == 30
    assert out.macro_context["scenario"]["driver"] == "TLT"
    assert out.conviction_provenance["c_scenario"]["driver"] == "TLT"
