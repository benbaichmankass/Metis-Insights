"""The fleet sweep must forward a leg's live direction gate (side_filter).

`base_args` forwarded only `long_only`, so a `side_filter: short` leg
(`trend_donchian_xrp_4h`, `sol_pullback_2h`) was swept on a TWO-SIDED book, which
live never trades. Found 2026-10-07 (lane EXIT-PARITY-CHECK) when the 2026-10-04
`trail4` pass on `trend_donchian_xrp_4h` failed its live-parity check.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import m20_fleet_exit_sweep as sweep  # noqa: E402


def _args(fam, **cfg):
    base = {"timeframe": "4h", "symbols": ["XRPUSDT"]}
    base.update(cfg)
    return sweep.base_args("leg", base, fam, "x.csv", None)


def test_short_filter_is_forwarded_for_both_families():
    for fam in ("donchian", "pullback"):
        a = _args(fam, side_filter="short")
        i = a.index("--side-filter")
        assert a[i + 1] == "short", fam
        assert "--long-only" not in a


def test_long_only_wins_and_stays_as_before():
    a = _args("donchian", long_only=True, side_filter="short")
    assert "--long-only" in a and "--side-filter" not in a


def test_both_or_absent_changes_nothing():
    for kw in ({}, {"side_filter": "both"}):
        a = _args("donchian", **kw)
        assert "--side-filter" not in a and "--long-only" not in a
