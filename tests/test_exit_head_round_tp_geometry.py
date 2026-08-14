"""The exit-head ROUND driver must build its book at live-parity TP geometry.

WHY THIS TEST EXISTS (BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP).

`m20_exit_head_round.py` called `base_args(leg, cfg, fam, data, resample)` with
five positional args, so `tp_cap_pct` took its default `0.0`; `base_args` only
forwards `--tp-r` / `--tp-cap-pct` when that value is `> 0.0`; and the driver's
argparse had no `--tp-cap-pct` option at all, so a caller could not ask for one.
Every harness therefore ran at `tp_cap_pct=0.0, tp_r=50.0` (the sentinel) — a
book with NO take-profit. Measured 2026-08-14: 11 of the 13 round directories
with a readable `exit_reason` contain ZERO take-profit exits, `donchian_1h_nested`
(the round behind the three shipped `exit_head_ml` cells) among them.

A head tuned on a book that cannot take profit is tuned on a book production does
not run — the same class as
`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`, which was fixed for the
lever sweeps and never applied here.

These assert the two properties that make the defect impossible to reintroduce
silently: the flag EXISTS with a live-parity DEFAULT (not an opt-in a caller can
forget), and the value REACHES the harness argv. The last test is the can-fail
control — it pins the exact mechanism, so if a future edit re-breaks the
forwarding these fail rather than passing quietly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "scripts" / "research" / "m20_exit_head_round.py"

sys.path.insert(0, str(REPO / "scripts" / "research"))
from m20_fleet_exit_sweep import base_args  # noqa: E402

# A donchian leg: `donchian` is in LIVE_TP_CAPPED_FAMILIES, so this family is
# one the cap applies to. (scalp/fvg place a real target their harness already
# models and are deliberately outside that set.)
_CFG = {
    "timeframe": "1h",
    "symbols": ["SOLUSDT"],
    "donchian": 20,
    "atr_period": 14,
    "atr_stop_mult": 2.5,
    "trail_mult": 3.5,
    "tp_r": 6.0,
    "min_confidence": 0.8,
    "long_only": True,
}


def test_driver_exposes_tp_cap_pct_at_all():
    """The flag must exist. Its absence WAS the defect — a caller could not ask."""
    out = subprocess.run([sys.executable, str(DRIVER), "--help"],
                         capture_output=True, text=True, timeout=120)
    assert "--tp-cap-pct" in out.stdout, (
        "the round driver has no --tp-cap-pct option, so no caller can request "
        "live-parity geometry; that is the original defect")


def test_default_is_live_parity_not_opt_in():
    """DEFAULT, not opt-in. An opt-in is a flag every future caller can forget."""
    out = subprocess.run([sys.executable, str(DRIVER), "--help"],
                         capture_output=True, text=True, timeout=120)
    assert "0.099" in out.stdout, (
        "live parity must be the DEFAULT; a default of 0 reproduces the "
        "no-take-profit book that made 11 rounds untransferable")


def test_tp_cap_reaches_the_harness_argv():
    """The value must actually arrive in the harness command line."""
    argv = base_args("trend_donchian_sol_prop", _CFG, "donchian",
                     "/tmp/SOLUSDT_5m.csv", "1h", 0.099)
    assert "--tp-cap-pct" in argv, "cap requested but never forwarded"
    assert argv[argv.index("--tp-cap-pct") + 1] == "0.099"
    # tp_r rides with it; without the cap the harness uses its 50.0 sentinel.
    assert "--tp-r" in argv, "tp_r must accompany the cap"
    assert argv[argv.index("--tp-r") + 1] == "6.0", (
        "must forward the LEG's declared tp_r (6.0 here), not a default")


def test_can_fail_zero_cap_forwards_neither_flag():
    """CAN-FAIL CONTROL: pins the exact mechanism that produced the defect.

    At tp_cap_pct=0.0 neither flag is forwarded, so the harness falls back to
    tp_cap_pct=0.0 / tp_r=50.0 = no take-profit. If this ever stops holding the
    forwarding logic changed and the tests above are no longer measuring what
    they claim.
    """
    argv = base_args("trend_donchian_sol_prop", _CFG, "donchian",
                     "/tmp/SOLUSDT_5m.csv", "1h", 0.0)
    assert "--tp-cap-pct" not in argv
    assert "--tp-r" not in argv, (
        "at cap 0 the leg's tp_r must NOT be forwarded — this is precisely why "
        "every historical round modelled no take-profit")
