"""M30 · tests for src/research/microstructure.py — the decision-bar intrabar-
OHLCV-shape feature extractor.

Covers the pure feature math, the tolerant/PIT contract, and a PARITY check
against the S0/S2 feasibility probe (scripts/micro/microstructure_probe.py) so the
two implementations of the same five features can never silently drift.

Offline, deterministic, no network / DB.
"""
from __future__ import annotations

import math
import os
import sys

from src.research import microstructure as ms

# Load the probe (scripts/micro is not a package) for the parity test — same
# pattern as tests/test_m30_microstructure_probe.py.
_MICRO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "micro"
)
if _MICRO not in sys.path:
    sys.path.insert(0, _MICRO)
import microstructure_probe as mp  # noqa: E402


def _synthetic_bars(n: int = 30) -> list:
    """A non-degenerate OHLCV series (varying closes + volumes) in the probe's
    {t,o,h,l,c,v} spelling — which the module reads via its alias accessors."""
    bars = []
    price = 100.0
    for i in range(n):
        price *= 1.0 + 0.01 * math.sin(i / 2.0)  # varying, non-constant returns
        rng = 1.0 + 0.5 * (i % 3)
        c = price
        o = price - 0.2
        h = max(o, c) + rng * 0.4
        lo = min(o, c) - rng * 0.6
        v = 1000.0 + 50.0 * (i % 5)
        bars.append({"t": 1_700_000_000_000 + i * 3_600_000, "o": o, "h": h, "l": lo, "c": c, "v": v})
    return bars


# ---- pure feature math -----------------------------------------------------


def test_range_position_math():
    # close on the high → 1.0 ; on the low → 0.0 ; mid → 0.5
    assert ms.range_position({"high": 110, "low": 100, "close": 110}) == 1.0
    assert ms.range_position({"high": 110, "low": 100, "close": 100}) == 0.0
    assert ms.range_position({"high": 110, "low": 100, "close": 105}) == 0.5
    # zero-range / malformed → None
    assert ms.range_position({"high": 100, "low": 100, "close": 100}) is None
    assert ms.range_position({"high": None, "low": 100, "close": 105}) is None


def test_realized_vol_and_term_structure():
    rets = [0.01, -0.02, 0.015, -0.005, 0.02]
    rv = ms.realized_vol(rets, 24)
    assert rv is not None and rv > 0
    # < 2 available → None
    assert ms.realized_vol([0.01], 24) is None
    # term structure: constant returns → both legs 0 → None (no fabricated ratio)
    assert ms.rv_term_structure([0.0, 0.0, 0.0, 0.0], 2, 4) is None


def test_volume_zscore_math():
    # last volume above the trailing mean → positive z
    z = ms.volume_zscore([100, 100, 100, 200], 24)
    assert z is not None and z > 0
    # zero dispersion → None
    assert ms.volume_zscore([100, 100, 100, 100], 24) is None


def test_accepts_both_candle_spellings():
    panel_spelling = {"open": 100, "high": 110, "low": 100, "close": 105, "volume": 10}
    probe_spelling = {"o": 100, "h": 110, "l": 100, "c": 105, "v": 10}
    assert ms.range_position(panel_spelling) == ms.range_position(probe_spelling) == 0.5


# ---- decision-bar block ----------------------------------------------------


def test_decision_bar_features_present_and_named():
    feats = ms.decision_bar_features(_synthetic_bars(30))
    # all five features are computable on a non-degenerate 30-bar window
    assert set(feats) == set(ms.FEATURE_NAMES)
    assert all(isinstance(v, float) for v in feats.values())
    # range_position is the decision bar's own (last bar), in [0, 1]
    assert 0.0 <= feats["ms_range_position"] <= 1.0


def test_decision_bar_omits_undeterminable_features():
    # a single bar → no returns → vol/term/autocorr/zscore omitted; only
    # range_position (uses the bar itself) survives.
    feats = ms.decision_bar_features([{"high": 110, "low": 100, "close": 105, "volume": 10}])
    assert feats == {"ms_range_position": 0.5}


def test_empty_or_malformed_is_total():
    assert ms.decision_bar_features([]) == {}
    assert ms.decision_bar_features([None, "x", 3]) == {}  # type: ignore[list-item]


# ---- PARITY vs the S0/S2 probe (drift guard) -------------------------------


def test_parity_with_probe_last_bar():
    """decision_bar_features(window) must equal the probe's build_feature_panel
    last-bar row for the five shared features — the single-source-of-truth guard.
    """
    bars = _synthetic_bars(24)  # n <= vol_long so both use the full history
    probe_row = mp.build_feature_panel(bars, vol_short=6, vol_long=24)[-1]
    mine = ms.decision_bar_features(bars, vol_short=6, vol_long=24)
    for probe_key, ms_key in (
        ("realized_vol", "ms_realized_vol"),
        ("rv_term_structure", "ms_rv_term_structure"),
        ("ret_autocorr_lag1", "ms_ret_autocorr_lag1"),
        ("range_position", "ms_range_position"),
        ("volume_zscore", "ms_volume_zscore"),
    ):
        pv = probe_row[probe_key]
        if pv is None:
            assert ms_key not in mine, f"{ms_key} present but probe had None"
        else:
            assert ms_key in mine, f"{ms_key} missing but probe computed {pv}"
            assert abs(mine[ms_key] - pv) < 1e-6, f"{ms_key}: {mine[ms_key]} != {pv}"
