"""The geometry stamp must describe what RAN, and there must be one of it.

`tp_geometry` is the field whose entire job is telling a reader which take-profit
geometry produced a verdict, and it has been wrong twice for the same structural
reason: the derivation existed in more than one place, and only one copy got
fixed.

  * `m20_fleet_exit_sweep` was corrected 2026-08-10 to stamp *"the geometry this
    leg actually ran, not the one the run requested"*. The fix never reached
    `m20_exit_head_round`, which read the RUN-LEVEL flag and would have stamped
    `live_parity` on a scalp round whose harness never received the cap.
  * `m20_flip_replay_sweep` called `base_args` positionally, so `tp_cap_pct`
    defaulted to `0.0` and it stamped nothing at all — *"there is not even a
    field to check"*. Every `regime_flip_exit` cell in the matrix was replayed
    against a no-take-profit book, which is why the coverage roll-up pins that
    lever's cutover at the `NEVER` sentinel instead of a date.

So the derivation now lives once, in the module both drivers already import
`base_args` from, and this pins the distinctions a single boolean would lose —
above all that `live_parity_uncapped` and `NO_TAKE_PROFIT` both mean "ran
without a cap" for OPPOSITE reasons.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


S = _load("m20_fleet_exit_sweep", "scripts/research/m20_fleet_exit_sweep.py")
G = S.tp_geometry_for
LIVE = 0.099


# ------------------------------------------- the two states that look alike

def test_uncapped_family_without_a_cap_IS_parity_not_a_defect() -> None:
    """The live scalp unit does not clamp, so withholding the cap is correct."""
    assert G({"scalp"}, 0.0) == "live_parity_uncapped"


def test_capped_family_without_a_cap_is_a_book_production_does_not_run() -> None:
    assert G({"donchian"}, 0.0) == "NO_TAKE_PROFIT"


def test_those_two_are_never_the_same_answer() -> None:
    """Both ran without a cap. Collapsing them is the whole defect."""
    assert G({"scalp"}, 0.0) != G({"donchian"}, 0.0)


# ------------------------------------------------------ the capped run itself

def test_every_capped_family_stamps_live_parity_capped() -> None:
    for fam in sorted(S.LIVE_TP_CAPPED_FAMILIES):
        assert G({fam}, LIVE) == "live_parity_capped", fam


def test_an_uncapped_family_does_not_claim_the_cap_it_never_got() -> None:
    """The run-level flag is on; base_args still withholds the cap here."""
    assert G({"scalp"}, LIVE) == "live_parity_uncapped"


def test_a_mixed_run_refuses_to_pick_the_flattering_label() -> None:
    out = G({"donchian", "scalp"}, LIVE)
    assert out.startswith("MIXED")
    assert "live_parity" not in out


# ------------------------------------------------- a run that measured nothing

def test_no_family_emitted_is_not_a_parity_claim() -> None:
    for cap in (LIVE, 0.0):
        assert G(set(), cap) == "UNOBSERVED", cap


# ------------------------------------------------------------ one definition

def test_both_drivers_call_the_same_function() -> None:
    """A second copy is what let the 2026-08-10 fix miss a sibling."""
    flip = (REPO / "scripts/research/m20_flip_replay_sweep.py").read_text()
    head = (REPO / "scripts/research/m20_exit_head_round.py").read_text()
    for src, who in ((flip, "flip_replay_sweep"), (head, "exit_head_round")):
        assert "tp_geometry_for" in src, who
        assert "MIXED_capped_and_uncapped_families" not in src, (
            f"{who} re-derives the label instead of calling the shared fn")


def test_the_flip_sweep_forwards_the_cap_it_used_to_drop() -> None:
    """The positional call is the defect; it must not come back."""
    src = (REPO / "scripts/research/m20_flip_replay_sweep.py").read_text()
    assert "base_args(name, cfg, fam, data, resample, a.tp_cap_pct)" in src
    assert "base_args(name, cfg, fam, data, resample)" not in src


def test_live_parity_is_the_flip_sweeps_DEFAULT_not_an_opt_in() -> None:
    """A cap you must remember to pass is a cap that will be forgotten."""
    src = (REPO / "scripts/research/m20_flip_replay_sweep.py").read_text()
    assert '"--tp-cap-pct", type=float, default=0.099' in src
