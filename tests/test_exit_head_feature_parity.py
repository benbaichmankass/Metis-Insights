"""M20 P4.3 — offline (E0 builder) vs live (exit_head_shadow) feature parity.

The exhaustion feature block lands in BOTH the dataset builder and the live
scorer in one PR (momentum-exhaustion design § P4.3 guardrail: live-parity
twins ship together). This test feeds the SAME synthetic candle history +
trade geometry to both implementations and asserts every shared feature key
matches — the structural guard against train/serve skew (the class of bug the
2026-07-12 closed-bar-trim + entry-anchor parity fixes patched reactively).
"""
from __future__ import annotations

import importlib.util
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "build_exit_head_dataset", REPO / "scripts/ml/build_exit_head_dataset.py")
builder = importlib.util.module_from_spec(spec)
sys.modules["build_exit_head_dataset"] = builder
spec.loader.exec_module(builder)

from src.runtime import exit_head_shadow as ehs  # noqa: E402

SHARED_FEATURES = [
    "age_bars", "open_r", "mfe_r", "mae_r", "giveback_r",
    "chop_frac_so_far", "stagnation_run", "dist_to_stop_r",
    "vol_ratio_vs_entry", "atr_ratio_vs_entry", "donchian_mid_dist_atr",
    "hour_of_day", "dayofweek",
    # P4.3 exhaustion block
    "bars_since_peak", "mom_8", "mom_decay", "atr_impulse_phase",
    "vol_at_peak_ratio", "band_ext_pctile", "failure_swing",
]


def _synthetic(n=90, seed=7, with_volume=True):
    rng = random.Random(seed)
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    price = 100.0
    rows = []
    for i in range(n):
        drift = 0.25 if i < 55 else -0.08  # run up then fade (a real peak)
        price += drift + rng.uniform(-0.15, 0.15)
        hi = price + rng.uniform(0.05, 0.5)
        lo = price - rng.uniform(0.05, 0.5)
        row = {"t": (t0 + timedelta(hours=i)).timestamp(),
               "high": hi, "low": lo, "close": price}
        row["volume"] = rng.uniform(50, 150) if with_volume else None
        rows.append(row)
    return rows, t0


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("with_volume", [True, False])
def test_builder_and_live_twin_agree(direction, with_volume):
    candles, t0 = _synthetic(with_volume=with_volume)
    cand_ts = [c["t"] for c in candles]
    atrs = builder.atr_series(candles)
    entry_bar = 30
    entry = candles[entry_bar]["close"]
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tr = {"source": "harness", "strategy": "trend_donchian",
          "symbol": "SYN", "direction": direction,
          "t_open": candles[entry_bar]["t"],
          "t_close": candles[-1]["t"],
          "entry": entry, "sl": sl, "final_r": 1.0,
          "final_r_source": "harness_net_r", "exit_reason": "trail_stop"}
    rows = builder.rows_for_trade(tr, candles, cand_ts, atrs)
    assert rows, "builder produced no rows"
    off = rows[-1]

    df_rows = [{"timestamp": datetime.fromtimestamp(c["t"], tz=timezone.utc)
                .isoformat(),
                "open": c["close"], "high": c["high"], "low": c["low"],
                "close": c["close"],
                **({"volume": c["volume"]} if with_volume else {})}
               for c in candles]
    df = pd.DataFrame(df_rows)
    # live anchor: strictly-after t_open == builder's bisect_right
    ts = pd.to_datetime(df["timestamp"], utc=True)
    entry_idx = int((ts > pd.to_datetime(tr["t_open"], unit="s", utc=True))
                    .to_numpy().argmax())
    live = ehs._feature_row(df, entry, abs(entry - sl), direction, entry_idx)
    assert live is not None

    for key in SHARED_FEATURES:
        off_v, live_v = off.get(key), live.get(key)
        if off_v is None or live_v is None:
            assert off_v == live_v, f"{key}: offline={off_v} live={live_v}"
        else:
            assert math.isclose(float(off_v), float(live_v),
                                rel_tol=0, abs_tol=1e-4), \
                f"{key}: offline={off_v} live={live_v}"


def test_peak_is_in_label_present_and_consistent():
    candles, _ = _synthetic()
    cand_ts = [c["t"] for c in candles]
    atrs = builder.atr_series(candles)
    entry_bar = 30
    entry = candles[entry_bar]["close"]
    tr = {"source": "harness", "strategy": "trend_donchian", "symbol": "SYN",
          "direction": "long", "t_open": candles[entry_bar]["t"],
          "t_close": candles[-1]["t"], "entry": entry, "sl": entry - 5.0,
          "final_r": 1.0, "final_r_source": "harness_net_r",
          "exit_reason": "trail_stop"}
    rows = builder.rows_for_trade(tr, candles, cand_ts, atrs)
    final_mfe = max(r["mfe_r"] for r in rows)
    for r in rows:
        assert "peak_is_in" in r and "future_mfe_delta" in r
        assert math.isclose(r["future_mfe_delta"], final_mfe - r["mfe_r"],
                            abs_tol=1e-3)
        assert r["peak_is_in"] == (1 if r["future_mfe_delta"] <= 0.25 else 0)
    # the synthetic path peaks around bar 55 then fades: the LAST row must
    # read peak-is-in, the first rows must not
    assert rows[-1]["peak_is_in"] == 1
    assert rows[0]["peak_is_in"] == 0
