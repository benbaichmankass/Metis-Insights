"""The ict_scalp TARGET must be sweepable, and a target cell must say what it measured.

MI-278 U2 attributed every winner since 2026-08-27 and found NO lever cutting
them short (the lever family is 3 of 49); the only mechanism with attributed
mass is the take-profit — 18 of 49 winners ended exactly at their declared
target — and U2b measured ict_scalp winners landing just under `tp_at_r: 1.5`
(median R 1.216 pre / 1.353 post). So the counterfactual the evidence points at
is "would a further target have held winners longer", and until 2026-09-13 no
harness could run it. The gap was three deep: e35 excludes the scalp family by
design (correctly — its grid walks down from the 50.0 sentinel), this sweep
passed no target at all, and `backtest_ict_scalp.py` had NO `--tp-at-r` flag,
so the only way to move the target was editing `config/strategies.yaml` — a
Tier-3 live file no research run may touch.

BL-20260912-THE-SCALP-FAMILY-S-TARGET-HAS-NO-SWEEP-E35-EXCLUDES-IT-BY-DESIGN-AND-THE-FLEET-SWEEP-DOES-NOT-SWEEP-A-TARGET

These tests pin the properties that make the axis trustworthy rather than
merely present:

1. **The override reaches the LIVE unit.** The target is injected into cfg and
   `ict_scalp.order_package` computes it; the harness never re-derives one. A
   harness that computed its own target would sweep a geometry production does
   not place.
2. **A cell that cannot mean anything is SKIPPED WITH A REASON.** A cell equal
   to the leg's declared target reproduces the base exactly, and its measured
   0.0 delta would read as `tie_no_improvement` — "we measured it and it made
   no difference" — when it was never measurable
   (`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`).
3. **The no-op value is DERIVED from the leg, never hardcoded to 1.5.** All
   eight live legs declare 1.5 today; a hardcoded test keeps skipping 1.5 and
   starts emitting a real no-op the day one of them moves.
4. **The recorded argv says exactly one thing.** `base_args` now passes the
   leg's own target, so a bare concatenation would emit `--tp-at-r` twice.
5. **Every state a summary can report is reachable**, including the ones that
   mean *we did not look*.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m20 = _load("_m20_fleet_target", "scripts/research/m20_fleet_exit_sweep.py")
bis = _load("_backtest_ict_scalp_target", "scripts/backtest_ict_scalp.py")

SCALP_H = m20.SCALP_HARNESS
CFG = {"timeframe": "5m", "symbols": ["BTCUSDT"], "tp_at_r": 1.5}


def _target_cells(cfg, fam=("scalp"), harness=SCALP_H):
    skipped: list = []
    cells = m20.cells_for(dict(cfg), fam, skipped=skipped, harness=harness)
    lever = m20.SCALP_TARGET_MATRIX_LEVER
    return ([t for t, lev, _ in cells if lev == lever],
            [x for x in skipped if x.get("lever") == lever])


# --- 1. the axis exists and is pre-registered ----------------------------- #

def test_scalp_emits_a_target_cell_per_grid_point():
    """POSITIVE CONTROL. Without this, every skip assertion below proves nothing."""
    tags, skipped = _target_cells(CFG)
    assert len(tags) == len(m20.SCALP_TP_AT_R_GRID), (tags, skipped)
    assert not skipped, skipped


def test_grid_brackets_the_live_target_on_both_sides():
    """A grid that only widens cannot refute 'the target is too near'."""
    grid = m20.SCALP_TP_AT_R_GRID
    assert [v for v in grid if v < 1.5], "no point BELOW the live 1.5R target"
    assert [v for v in grid if v > 1.5], "no point ABOVE the live 1.5R target"


def test_grid_omits_the_live_value_itself():
    """1.5 is what every live leg declares — the cell would BE the base."""
    assert 1.5 not in m20.SCALP_TP_AT_R_GRID


def test_cells_land_in_the_bracket_geometry_column_not_a_new_lever():
    """`tp_geometry` is ALREADY TAKEN and means something else — whether a cell
    was measured at live TP parity. The matrix's own legend calls
    `bracket_geometry` "a new DIMENSION, not a ninth lever"; a target cell is a
    slice of that column."""
    assert m20.SCALP_TARGET_MATRIX_LEVER == "bracket_geometry"
    assert m20.SCALP_TARGET_MATRIX_LEVER != "tp_geometry"


# --- 2/3. inertness, derived from the leg --------------------------------- #

def test_a_cell_equal_to_the_declared_target_is_skipped_with_a_reason():
    tags, skipped = _target_cells({**CFG, "tp_at_r": 2.0})
    assert "tp2R" not in tags
    assert any("provable_noop" in x["reason"] for x in skipped), skipped


def test_the_noop_value_is_derived_not_hardcoded_to_1_5():
    """The SAME 1.5 that is skipped on a 1.5-leg must RUN on a 2.0-leg."""
    assert m20.inert_scalp_target_reason({"tp_at_r": 1.5}, 1.5, "scalp", SCALP_H)
    assert m20.inert_scalp_target_reason({"tp_at_r": 2.0}, 1.5, "scalp", SCALP_H) is None


def test_a_harness_without_the_flag_withholds_the_cell():
    """POSITIVE CONTROL PAIRED: the scalp harness HAS the flag, trend does not."""
    assert m20.harness_implements_flag(SCALP_H, "--tp-at-r") is True
    assert m20.harness_implements_flag("scripts/backtest_trend.py", "--tp-at-r") is False
    reason = m20.inert_scalp_target_reason(CFG, 3.0, "scalp", "scripts/backtest_trend.py")
    assert reason and "harness_has_no_tp_at_r_flag" in reason


def test_an_unreadable_harness_is_its_own_state_never_collapsed_into_no_flag():
    """Reporting an unreadable source as 'does not implement' would turn a
    filesystem problem into a fake coverage answer."""
    assert m20.harness_implements_flag("scripts/does_not_exist.py", "--tp-at-r") is None
    reason = m20.inert_scalp_target_reason(CFG, 3.0, "scalp", "scripts/does_not_exist.py")
    assert reason and "harness_unreadable" in reason


def test_an_unreadable_declared_target_is_named_not_guessed():
    reason = m20.inert_scalp_target_reason({"tp_at_r": "wide"}, 3.0, "scalp", SCALP_H)
    assert reason == "declared_tp_at_r_unreadable"


def test_a_non_scalp_family_emits_nothing_and_reports_no_skip_noise():
    """Recording it would put a skipped row on every donchian leg in the fleet."""
    tags, skipped = _target_cells({"trail_mult": 5.0, "timeframe": "1h",
                                   "symbols": ["BTCUSDT"]},
                                  fam="donchian", harness=m20.DONCHIAN_HARNESS)
    assert tags == []
    assert skipped == []


# --- 4. the recorded argv says exactly one thing -------------------------- #

def test_scalp_base_passes_the_legs_own_declared_target():
    """Not the hardcoded `ict_scalp_5m` block every scalp leg used to inherit."""
    base = m20.base_args("ict_scalp_eth_15m", {**CFG, "tp_at_r": 2.25},
                         "scalp", "D.csv", None)
    assert "--tp-at-r" in base
    assert base[base.index("--tp-at-r") + 1] == "2.25"


def test_a_target_cell_emits_the_flag_exactly_once():
    base = m20.base_args("ict_scalp_5m", CFG, "scalp", "D.csv", None)
    cells = m20.cells_for(dict(CFG), "scalp", harness=SCALP_H)
    extra = [e for t, lev, e in cells if t == "tp3R"][0]
    assert (base + extra).count("--tp-at-r") == 2, "the defect this guards"
    argv = m20.compose_cell_argv(base, extra)
    assert argv.count("--tp-at-r") == 1
    assert argv[argv.index("--tp-at-r") + 1] == "3"


def test_compose_repairs_the_preexisting_stale_exit_duplicate():
    """Not hypothetical: `ict_scalp_eth_15m` declares `stale_exit_bars: 12`, and
    the `stale8_lt0R` cell appends its own. The argv has carried both all along."""
    base = ["--data", "x", "--stale-exit-bars", "12", "--giveback-r", "1.0"]
    argv = m20.compose_cell_argv(base, ["--stale-exit-bars", "8"])
    assert argv.count("--stale-exit-bars") == 1
    assert argv[argv.index("--stale-exit-bars") + 1] == "8"


def test_compose_does_not_eat_a_negative_value_as_a_flag():
    argv = m20.compose_cell_argv(
        ["--data", "x", "--stale-exit-below-r", "-0.5", "--tp-at-r", "1.5"],
        ["--stale-exit-below-r", "-0.25"])
    assert argv == ["--data", "x", "--tp-at-r", "1.5",
                    "--stale-exit-below-r", "-0.25"]


def test_compose_strips_a_valueless_flag_without_eating_a_value():
    argv = m20.compose_cell_argv(
        ["--data", "x", "--be-arm-on-touch", "--tp-at-r", "1.5"],
        ["--be-arm-on-touch"])
    assert argv == ["--data", "x", "--tp-at-r", "1.5", "--be-arm-on-touch"]


def test_compose_leaves_a_non_overlapping_cell_byte_identical():
    """The no-overlap path must stay plain concatenation, or every existing
    cell's recorded command changes for no reason."""
    base = ["--data", "x", "--sim-breakeven"]
    assert m20.compose_cell_argv(base, ["--rr-floor", "0.5"]) == \
        base + ["--rr-floor", "0.5"]
    assert m20.compose_cell_argv(base, []) == base


# --- 5. the harness side: every state reachable --------------------------- #

@pytest.mark.parametrize("cfg,hint,want", [
    ({}, None, (1.5, "unit_default")),
    ({"tp_at_r": 2.0}, "cli_flag", (2.0, "cli_flag")),
    ({"tp_at_r": 2.0}, None, (2.0, "config")),
    ({"tp_at_r": "wide"}, None, (None, "unknown")),
    ({"tp_at_r": None}, None, (None, "unknown")),
])
def test_every_declared_target_source_state_is_reachable(cfg, hint, want):
    assert bis._effective_tp_at_r(cfg, hint) == want


def test_the_unit_default_is_read_from_the_unit_not_written_as_a_literal():
    """Field beats comment: if the unit's default moves, this must follow it."""
    from src.units.strategies.ict_scalp import _DEFAULTS
    assert bis._effective_tp_at_r({}, None)[0] == float(_DEFAULTS["tp_at_r"])


def test_unknown_is_not_silently_replaced_by_the_default():
    """Substituting the default would state a geometry nobody chose — and the
    live unit would raise on this cfg rather than trade it."""
    assert bis._effective_tp_at_r({"tp_at_r": "wide"}, None)[0] is None


def test_declared_source_states_match_the_ones_the_resolver_can_return():
    got = {bis._effective_tp_at_r(c, h)[1] for c, h in
           (({}, None), ({"tp_at_r": 1.0}, "cli_flag"),
            ({"tp_at_r": 1.0}, None), ({"tp_at_r": "x"}, None))}
    assert got == set(bis.TP_AT_R_SOURCE_STATES)


def _t(entry, tp, outcome="tp_hit"):
    return bis.Trade(entry_index=0, entry_time=0, direction="long", entry=entry,
                     sl=entry * 0.99, tp=tp, risk=entry * 0.01, outcome=outcome)


def test_placeability_no_trades_is_not_the_clean_negative():
    """No trades means NO distance exists — that is not 'all placeable'."""
    p = bis._placeability([])
    assert p["placeability_state"] == "not_measured"
    assert p["n_beyond_venue_cap"] is None
    assert p["max_target_distance_pct"] is None


def test_placeability_flags_a_target_past_the_venue_cap():
    """ict_scalp does NOT clamp, so a too-far target is SENT and refused at the
    venue — unlike the four clamping families, where it becomes a nearer one."""
    near, far = _t(100.0, 101.5), _t(100.0, 130.0)
    assert bis._placeability([near])["placeability_state"] == "all_within_venue_cap"
    both = bis._placeability([near, far])
    assert both["placeability_state"] == "some_beyond_venue_cap"
    assert both["n_beyond_venue_cap"] == 1
    assert both["unit_clamps"] is False


def test_the_scalp_unit_really_does_not_clamp():
    """The claim the placeability census rests on, with a positive control."""
    from src.runtime.tp_venue_cap import CLAMPING_FAMILIES
    assert "scalp" not in CLAMPING_FAMILIES
    assert {"donchian", "pullback"} <= set(CLAMPING_FAMILIES)


def test_target_binding_separates_never_reached_from_no_trades():
    """Past some width no trade reaches the target and every wider cell returns
    the SAME book. Three identical rows in a verdict table read as three data
    points; they are ONE observation."""
    assert bis._target_binding([]) == "not_measured"
    assert bis._target_binding([_t(100.0, 130.0, "timeout")]) == "never_reached"
    assert bis._target_binding([_t(100.0, 101.5, "tp_hit")]) == "binds"


def test_every_declared_binding_and_placeability_state_is_reachable():
    reached = {bis._target_binding([]),
               bis._target_binding([_t(100.0, 130.0, "timeout")]),
               bis._target_binding([_t(100.0, 101.5, "tp_hit")])}
    assert reached == set(bis.TARGET_BINDING_STATES)
    reached_p = {bis._placeability([])["placeability_state"],
                 bis._placeability([_t(100.0, 101.5)])["placeability_state"],
                 bis._placeability([_t(100.0, 130.0)])["placeability_state"]}
    assert reached_p == set(bis.PLACEABILITY_STATES)
