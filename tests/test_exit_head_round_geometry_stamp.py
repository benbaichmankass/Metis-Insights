"""The round's `tp_geometry` must describe what RAN, not what was requested.

`base_args` applies `--tp-cap-pct` ONLY when the leg's family is in
`LIVE_TP_CAPPED_FAMILIES` ({donchian, pullback, fade, squeeze}), because only
those live units carry `_TP_SENTINEL_CAP_PCT` — measured 2026-08-14:
`grep -c _TP_SENTINEL_CAP_PCT src/units/strategies/ict_scalp.py` -> 0, against
4 for `trend_donchian.py`. So withholding the cap from a scalp round is
CORRECT; the live scalp unit does not clamp.

The defect was the LABEL. `_round_meta` read the run-level `--tp-cap-pct` and
stamped `live_parity` for every leg regardless of family, so a scalp round
would self-report a geometry its harness never received. That is
`diagnostic-provenance-guard` sub-class A in the one field whose entire job is
to tell a reader which geometry produced the numbers underneath it — and
`m20_fleet_exit_sweep` had already been fixed for exactly this on 2026-08-10
("THE GEOMETRY THIS LEG ACTUALLY RAN, not the one the run requested"). The fix
never reached this sibling driver. A 15m scalp round launched 2026-08-14 came
within one relay of writing that false stamp into the committed evidence file
`docs/research/m20-exit-head-rounds.jsonl`, whose whole value is that its
geometry stamps can be trusted.

The two uncapped states are distinguished on purpose: they both run without a
cap, for OPPOSITE reasons.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "scripts" / "research" / "m20_exit_head_round.py"
# The derivation moved here on 2026-08-16, unchanged, when
# `m20_flip_replay_sweep.py` needed the same answer — a second copy is precisely
# how the 2026-08-10 fleet-sweep fix failed to reach this driver in the first
# place. These grep-shaped assertions had to follow it: a test that pins WHERE
# the strings live fails on a legitimate extraction, which is what happened. The
# behavioural coverage is in `tests/test_m20_tp_geometry_stamp.py`, which calls
# the function instead of reading for it; what stays here is the driver-side
# half — that this driver still derives from emitted families and still ships
# the evidence for its label.
OWNER = REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py"


def test_the_stamp_is_derived_from_families_that_actually_emitted() -> None:
    src = DRIVER.read_text()
    assert "fams_seen" in src, "the geometry stamp no longer tracks emitted families"
    assert "tp_geometry_for(fams_seen" in src, (
        "the driver no longer derives its stamp from the families that emitted, "
        "so it is back to echoing the run-level flag"
    )
    # The label must not be a bare function of the CLI arg any more.
    assert not re.search(
        r'"tp_geometry":\s*\(\s*"live_parity"\s+if\s+a\.tp_cap_pct', src
    ), "tp_geometry is being derived from the run-level flag again"


def test_all_three_geometry_states_exist_and_are_distinct() -> None:
    """`live_parity_uncapped` and `NO_TAKE_PROFIT` must not collapse.

    Both mean "no cap was applied". One is parity (the live unit has no clamp);
    the other is a book production does not run. Collapsing them re-creates the
    bug in the opposite direction.
    """
    src = OWNER.read_text()
    for state in ("live_parity_capped", "live_parity_uncapped", "NO_TAKE_PROFIT"):
        assert state in src, f"geometry state missing: {state}"
    assert "MIXED_capped_and_uncapped_families" in src, (
        "a round spanning both kinds of family must say so rather than pick one"
    )


def test_the_derivation_has_exactly_one_home() -> None:
    """Two copies is the defect that produced this test file."""
    producers = [
        p for p in (REPO / "scripts" / "research").glob("*.py")
        if "MIXED_capped_and_uncapped_families" in p.read_text()
        and p.name != "m20_fleet_exit_sweep.py"
    ]
    assert not producers, (
        "these files re-derive the geometry label instead of calling "
        f"m20_fleet_exit_sweep.tp_geometry_for: {[p.name for p in producers]}"
    )


def test_the_stamp_ships_the_evidence_it_was_derived_from() -> None:
    """A reader must be able to CHECK the label, not just trust it."""
    src = DRIVER.read_text()
    for key in ("families", "cap_applied_to_families", "cap_withheld_from_families"):
        assert f'"{key}"' in src, f"_round_meta no longer records {key}"


def test_the_capped_family_set_still_excludes_scalp_for_the_stated_reason() -> None:
    """If ict_scalp ever gains a live clamp, this test should force a re-think.

    The exclusion is not a preference — it rests on the live unit carrying no
    `_TP_SENTINEL_CAP_PCT`. Asserting the code rather than the claim keeps the
    two from drifting.
    """
    import sys

    sys.path.insert(0, str(REPO / "scripts" / "research"))
    from m20_fleet_exit_sweep import LIVE_TP_CAPPED_FAMILIES

    assert "scalp" not in LIVE_TP_CAPPED_FAMILIES
    assert {"donchian", "pullback"} <= LIVE_TP_CAPPED_FAMILIES

    scalp_unit = REPO / "src" / "units" / "strategies" / "ict_scalp.py"
    donchian_unit = REPO / "src" / "units" / "strategies" / "trend_donchian.py"
    if scalp_unit.is_file() and donchian_unit.is_file():
        # The positive control matters: a zero count proves nothing unless the
        # same probe finds a non-zero somewhere it should.
        assert donchian_unit.read_text().count("_TP_SENTINEL_CAP_PCT") > 0, (
            "the probe finds no cap in trend_donchian either — it is broken, "
            "so the scalp zero below would be meaningless"
        )
        assert scalp_unit.read_text().count("_TP_SENTINEL_CAP_PCT") == 0, (
            "ict_scalp now carries a live TP cap — LIVE_TP_CAPPED_FAMILIES and "
            "the geometry stamp both need revisiting"
        )
