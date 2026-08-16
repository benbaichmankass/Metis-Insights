"""M21 entry sweeps must pass `tp_cap_pct` to `base_args`, not default it away.

`base_args(name, cfg, fam, data, resample, tp_cap_pct=0.0, ...)` takes the cap as
its SIXTH positional. Both M21 drivers called it with five, so the cap silently
resolved to 0.0 and every M21 cell was measured on a NO-TAKE-PROFIT book — while
the live units clamp the TP to 9.9% of entry. Measured 2026-08-16: 227 of 227 M21
cells, across all 42 legs, sit on TP-capped families (donchian 147 + pullback 80),
so there was no leg the omission did not reach.

This is the M20 `m20_flip_replay_sweep` defect one milestone over, and the shared
root cause is that a 0.0 default makes the WRONG book the one you get by
forgetting. These tests pin the call shape and the stamp, so a future edit cannot
quietly drop back to the positional form.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

DRIVERS = ("m21_entry_sweep.py", "m21_entry_head_round.py")


def _base_args_calls(src: str) -> list[ast.Call]:
    tree = ast.parse(src)
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "base_args"]


def test_every_base_args_call_passes_the_cap():
    """A 5-positional call is the defect; it must not reappear."""
    seen = 0
    for name in DRIVERS:
        src = (REPO / "scripts" / "research" / name).read_text()
        calls = _base_args_calls(src)
        assert calls, f"{name}: no base_args call found — did the driver move?"
        for c in calls:
            seen += 1
            has_kw = any(k.arg == "tp_cap_pct" for k in c.keywords)
            assert len(c.args) >= 6 or has_kw, (
                f"{name}: base_args called with {len(c.args)} positional args and "
                f"no tp_cap_pct= keyword — the cap defaults to 0.0, which measures "
                f"a no-take-profit book")
    assert seen >= 2, f"expected >=2 call sites, found {seen}"


def test_both_drivers_expose_the_flag_at_live_parity():
    for name in DRIVERS:
        src = (REPO / "scripts" / "research" / name).read_text()
        assert '"--tp-cap-pct"' in src, f"{name}: no --tp-cap-pct flag"
        assert "default=0.099" in src, (
            f"{name}: --tp-cap-pct must default to live parity (0.099). Every M21 "
            f"leg is on a capped family, so 0.0 makes the wrong book the default.")


def test_drivers_stamp_the_geometry_from_the_shared_helper():
    """The stamp must come from tp_geometry_for, not a re-derived local string."""
    for name in DRIVERS:
        src = (REPO / "scripts" / "research" / name).read_text()
        assert "tp_geometry_for" in src, f"{name}: geometry not stamped"
        assert '"tp_geometry"' in src, f"{name}: no tp_geometry field emitted"


def test_shared_helper_agrees_with_the_capped_family_set():
    """Positive control: the helper the drivers import really does distinguish."""
    from m20_fleet_exit_sweep import LIVE_TP_CAPPED_FAMILIES, tp_geometry_for
    assert "donchian" in LIVE_TP_CAPPED_FAMILIES
    assert "pullback" in LIVE_TP_CAPPED_FAMILIES
    assert tp_geometry_for({"donchian"}, 0.099) == "live_parity_capped"
    assert tp_geometry_for({"donchian"}, 0.0) == "NO_TAKE_PROFIT"
