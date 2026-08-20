"""`scripts/research/m20_split_dispersion.py` — is a verdict stable, or an artifact
of where the IS/OOS boundary happened to fall?

Born from a measured 5.14x swing in dOOS on `sol_pullback_2h` when only
`--split-target-oos` moved (50 -> 35), flipping the pre-registered rule from
PASS to FAIL. These tests pin the properties that keep the answer honest: the
gate is the LIVE one, the re-derived metrics are checked against the harness,
and an unmeasured band is never reported as a stable one.
"""
import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "m20_split_dispersion", REPO / "scripts/research/m20_split_dispersion.py")
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)


def tr(t, net):
    return {"exit_time": t, "net_r": net}


BASE = [tr(f"2025-{m:02d}-01", -1.0) for m in range(1, 13)]
REPORTED = {"net_total_r": -12.0, "max_drawdown_r": 12.0, "total_trades": 12}


def test_self_test_passes():
    assert sd.main(["--self-test"]) == 0


# --- the gate is imported, not copied ---------------------------------------

def test_verdict_uses_the_live_gate():
    """A second implementation of a decision predicate is the drift this repo
    keeps paying for — and this session already shipped one by accident."""
    src = (REPO / "scripts/research/m20_split_dispersion.py").read_text()
    assert "from scripts.research.m20_fleet_exit_sweep import" in src
    assert "beats" in src


def test_the_imported_gate_is_the_one_the_sweep_uses():
    from scripts.research.m20_fleet_exit_sweep import beats
    better = {"net_total_r": -4.0, "max_drawdown_r": 4.0}
    base = {"net_total_r": -9.0, "max_drawdown_r": 9.0}
    assert beats(better, base) is True
    assert beats(base, better) is False


# --- metrics, and the check that makes them trustworthy ---------------------

def test_drawdown_is_path_dependent_so_order_matters():
    up = [tr("2025-01-01", 5.0), tr("2025-02-01", -3.0)]
    assert sd.metrics(up)["max_drawdown_r"] == 3.0
    assert sd.metrics(up)["net_total_r"] == 2.0


def test_rows_are_ordered_by_exit_not_input_order():
    shuffled = [tr("2025-02-01", -3.0), tr("2025-01-01", 5.0)]
    assert sd.metrics(shuffled)["max_drawdown_r"] == 3.0


def test_ungradeable_rows_are_counted_not_silently_dropped():
    m = sd.metrics(BASE + [{"exit_time": "2025-12-31"}])
    assert m["total_trades"] == 13 and m["rows_ungradeable"] == 1


def test_verification_catches_a_re_derivation_that_does_not_reproduce():
    assert sd.verify_against_harness(BASE, REPORTED)["ok"] is True
    assert sd.verify_against_harness(BASE, {"net_total_r": -99.0})["ok"] is False


def test_nothing_to_verify_against_is_not_a_pass():
    """An unchecked second implementation is the whole hazard."""
    assert sd.verify_against_harness(BASE, {})["ok"] is False


def test_analyse_refuses_without_harness_figures():
    r = sd.analyse(BASE, BASE)
    assert r["state"] == "refused" and "no harness figures" in r["why"]


def test_analyse_refuses_when_the_check_fails():
    r = sd.analyse(BASE, BASE, base_reported={"net_total_r": -99.0})
    assert r["state"] == "refused"
    assert r["harness_agreement"]["ok"] is False
    assert "split_sensitive" not in r, "a refused run must not publish a verdict"


# --- the finding it exists to surface ---------------------------------------

def test_a_stable_cell_is_not_flagged():
    cell = [tr(f"2025-{m:02d}-01", -0.5) for m in range(1, 13)]
    r = sd.analyse(BASE, cell, targets=(3, 4, 5), base_reported=REPORTED,
                   min_oos_trades=1)
    assert r["state"] == "measured"
    assert r["split_sensitive"] is False and r["pass_fraction"] == 1.0


def test_a_cell_whose_verdict_flips_on_the_boundary_IS_flagged():
    """The sol_pullback_2h shape, in miniature."""
    cell = ([tr(f"2025-{m:02d}-01", -0.5) for m in range(1, 10)] +
            [tr(f"2025-{m:02d}-01", -1.2) for m in range(10, 13)])
    r = sd.analyse(BASE, cell, targets=(3, 9), base_reported=REPORTED,
                   min_oos_trades=1)
    assert r["split_sensitive"] is True
    assert r["pass_fraction"] == 0.5
    by_t = {row["target"]: row["is_oos_pass"] for row in r["rows"]}
    assert by_t[3] is False and by_t[9] is True


def test_an_unsatisfiable_target_is_reported_not_clamped():
    r = sd.analyse(BASE, BASE, targets=(500,), base_reported=REPORTED)
    assert r["rows"][0]["state"] == "insufficient_population"


def test_a_band_that_never_graded_is_not_measured_never_stable():
    """`split_sensitive: False` and 'nobody could grade it' are opposite claims."""
    r = sd.analyse(BASE, BASE, targets=(500,), base_reported=REPORTED)
    assert r["state"] == "not_measured"
    assert r["split_sensitive"] is None


def test_thin_oos_windows_are_flagged_against_the_live_floor():
    from scripts.research.m20_fleet_exit_sweep import MIN_OOS_TRADES
    cell = [tr(f"2025-{m:02d}-01", -0.5) for m in range(1, 13)]
    r = sd.analyse(BASE, cell, targets=(3,), base_reported=REPORTED)
    assert r["min_oos_trades"] == MIN_OOS_TRADES
    assert r["rows"][0]["below_oos_floor"] is True


def test_default_band_brackets_the_two_targets_that_disagreed():
    """35 and 50 are the values the matrix and the sweep actually used; a band
    excluding the disagreeing pair would be useless."""
    assert 35 in sd.DEFAULT_TARGETS and 50 in sd.DEFAULT_TARGETS
