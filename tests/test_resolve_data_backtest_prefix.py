"""The `backtest_` probe must be EXACT-TIMEFRAME and must not enter the grain loop.

`resolve_data` could not see `data/backtest_BTCUSDT_5m.csv` — 647,585 rows of BTC
5m OHLCV, deeper than any canonical alt 5m file, and already the default feed for
all six `walkforward_vol_*` scripts. Its prefix set is `{sym.lower()}` plus the
USDT-stripped base, and `backtest_btcusdt_5m.csv` starts with neither, so two
BTCUSDT 5m legs (`ict_scalp_5m` live, `vwap` shadow) resolved to nothing.

THE FIX HAD A HARMFUL NEIGHBOUR, which is what these tests actually guard.
`DATA_GRAIN` is FINEST-FIRST and the grain loop takes the first present file at
or below the leg's timeframe. So making the prefixed file a grain candidate —
or, equivalently, copying it to `data/BTCUSDT_5m.csv` — would redirect every BTC
leg at 1h/2h/4h/1d off `BTCUSDT_15m.csv` (which runs to 2026-07-10) onto the 5m
file (which stops 2026-05-21), quietly shortening the book behind verdicts
already recorded in the coverage matrix. This module's own `resolve_data`
docstring names that hazard: *"flipping which series a RECORDED verdict was
measured against must not ride along silently inside a reachability fix."*

So the property under test is not "BTC 5m resolves". It is that the probe fires
ONLY at the leg's own timeframe, where by construction nothing resolved before.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from m20_fleet_exit_sweep import DATA_GRAIN, TF_MINUTES, resolve_data  # noqa: E402


def _touch(d: Path, name: str) -> None:
    (d / name).write_text("timestamp,open,high,low,close,volume\n")


def test_the_prefixed_file_resolves_at_its_own_timeframe(tmp_path: Path) -> None:
    _touch(tmp_path, "backtest_BTCUSDT_5m.csv")
    path, proxy, resample = resolve_data("BTCUSDT", "5m", tmp_path)
    assert path is not None and path.endswith("backtest_BTCUSDT_5m.csv")
    assert proxy is False
    assert resample is None, "an exact-timeframe hit must not ask for a resample"


def test_it_does_NOT_leak_into_a_coarser_leg_via_the_grain_loop(tmp_path: Path) -> None:
    """The whole point. A 1h leg must not pick up a 5m `backtest_` file."""
    _touch(tmp_path, "backtest_BTCUSDT_5m.csv")
    for tf in ("1h", "2h", "4h", "1d"):
        path, _proxy, _resample = resolve_data("BTCUSDT", tf, tmp_path)
        assert path is None, (
            f"a {tf} leg resolved {path} from a 5m backtest_ file — the probe has "
            "entered the grain loop, which silently shortens the book behind every "
            "recorded BTC verdict coarser than 15m"
        )


def test_the_canonical_15m_file_still_wins_for_a_coarser_leg(tmp_path: Path) -> None:
    """The live shape: BOTH files present. The 15m one must keep serving 1h.

    This is the regression that a copy-to-canonical-slot "fix" would cause, so
    it is asserted directly rather than inferred from the test above.
    """
    _touch(tmp_path, "backtest_BTCUSDT_5m.csv")
    _touch(tmp_path, "BTCUSDT_15m.csv")
    path, _proxy, resample = resolve_data("BTCUSDT", "1h", tmp_path)
    assert path is not None and path.endswith("BTCUSDT_15m.csv"), path
    assert resample == "1h"


def test_the_native_spelling_still_outranks_the_prefixed_one(tmp_path: Path) -> None:
    _touch(tmp_path, "backtest_BTCUSDT_5m.csv")
    _touch(tmp_path, "BTCUSDT_5m.csv")
    path, _proxy, _resample = resolve_data("BTCUSDT", "5m", tmp_path)
    assert path.endswith("BTCUSDT_5m.csv") and "backtest_" not in Path(path).name


def test_absent_prefixed_file_changes_nothing(tmp_path: Path) -> None:
    """The negative control: the probe must not invent a path."""
    assert resolve_data("BTCUSDT", "5m", tmp_path) == (None, False, None)
    _touch(tmp_path, "SOLUSDT_5m.csv")
    assert resolve_data("BTCUSDT", "5m", tmp_path)[0] is None, (
        "a sibling symbol's file satisfied a BTCUSDT lookup"
    )


def test_grain_order_is_still_finest_first_the_premise_of_all_of_this(tmp_path: Path) -> None:
    """If DATA_GRAIN stops being finest-first, the reasoning above is void.

    Asserting the premise rather than trusting the comment: the leak test would
    pass vacuously under a coarsest-first order while the hazard it describes
    changed shape entirely.
    """
    mins = [TF_MINUTES[g] for g in DATA_GRAIN]
    assert mins == sorted(mins), f"DATA_GRAIN is no longer finest-first: {DATA_GRAIN}"
