"""M30 · C1-for-backtests — tests for the backtest_system (portfolio) adapter
in scripts/research/build_backtest_panel.py.

Offline + deterministic: the heavy ``run_system_backtest`` is monkeypatched to
return a hand-built closed ledger (real ``_ClosedTrade`` dataclasses) + a tiny
clock frame, so the adapter's normalization (per-row strategy=owner, R from
entry/stop/exit, native excursion window slice) is exercised with no harness run,
no network, no DB. Loaded via importlib (scripts/research is not a package).
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPT = os.path.join(_ROOT, "scripts", "research", "build_backtest_panel.py")


def _load():
    spec = importlib.util.spec_from_file_location("build_backtest_panel", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bb = _load()


def test_r_multiple_from_prices():
    # long winner: (104-100)/|100-90| = 0.4
    assert bb._r_multiple_from_prices("long", 100.0, 90.0, 104.0) == 0.4
    # short winner: (100-95)/|100-110| = 0.5
    assert bb._r_multiple_from_prices("short", 100.0, 110.0, 95.0) == 0.5
    # degenerate stop distance → None (never a fabricated 0)
    assert bb._r_multiple_from_prices("long", 100.0, 100.0, 104.0) is None
    # missing price → None
    assert bb._r_multiple_from_prices("long", None, 90.0, 104.0) is None


def _clock_df():
    highs = [101, 102, 103, 105, 108, 102, 101, 100, 100, 100]
    lows = [99, 98, 97, 98, 99, 97, 100, 99, 99, 99]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=10, freq="15min", tz="UTC"),
            "open": [100] * 10,
            "high": highs,
            "low": lows,
            "close": [100] * 10,
            "volume": [1.0] * 10,
        }
    )


def _fake_summary():
    import scripts.backtest_system as BS

    df = _clock_df()
    # winner: long, entry idx 2 → exit idx 6, entry 100, entry-stop 90 (risk 10),
    # exit 104 → r = 0.4. meta carries the ict_scalp decision-time specs.
    meta = {
        "sweep_extreme": 100.0, "sweep_level": 110.0,
        "displacement_body_to_range": 0.8, "fvg_size": 20.0,
        "mitigation_mode": "wick_rejection", "htf_filter_active": True,
        "atr": 10.0, "adx_14": 25.0, "regime": "trending",
        # an OUTCOME key the harness also stamps — must never leak to a feature
        "mfe_r": 999.0, "exit_price": 104.0,
    }
    closed = [
        BS._ClosedTrade(
            owner="ict_scalp_5m", side="long", entry_ts=df["timestamp"].iloc[2],
            exit_ts=df["timestamp"].iloc[6], entry=100.0, exit=104.0, qty=1.0,
            pnl=4.0, fee=0.1, reason="tp", bars_held=4, regime="trending",
            vol_regime="high", entry_idx=2, exit_idx=6, sl=90.0, meta=dict(meta),
            confidence=0.77,
        ),
    ]
    return {"closed_trades": closed, "clock_frame": df, "net_pnl": 4.0}


def test_backtest_system_adapter_normalizes(monkeypatch):
    import scripts.backtest_system as BS

    monkeypatch.setattr(BS, "_load_candles", lambda _p: _clock_df())
    monkeypatch.setattr(BS, "run_system_backtest", lambda *a, **k: _fake_summary())

    df, sim, info = bb._adapter_backtest_system(data_path="x.csv", symbol="BTCUSDT")
    assert len(sim) == 1
    st = sim[0]
    assert st.strategy == "ict_scalp_5m"        # per-row strategy = the winning owner
    assert st.r_multiple == 0.4                 # computed from entry/stop/exit
    assert st.entry_index == 2 and st.exit_index == 6
    assert st.confidence == 0.77
    assert info["harness"] == "backtest_system"
    assert info["harness_total_trades"] == 1


def test_backtest_system_panel_end_to_end(monkeypatch):
    import scripts.backtest_system as BS

    monkeypatch.setattr(BS, "_load_candles", lambda _p: _clock_df())
    monkeypatch.setattr(BS, "run_system_backtest", lambda *a, **k: _fake_summary())

    rows, manifest = bb.build_backtest_panel(
        harness="backtest_system",
        adapter_opts={"data_path": "x.csv", "symbol": "BTCUSDT"},
    )
    assert manifest["harness"] == "backtest_system"
    assert manifest["row_count"] == 1
    row = rows[0]
    assert row["strategy"] == "ict_scalp_5m"
    assert row["r"] == 0.4 and row["win"] == 1
    # native MFE/MAE excursions computed from the clock window
    assert row["excursion_present"] is True
    assert row["mfe_r"] is not None and row["mae_r"] is not None
    # leakage: the outcome key the harness stamped on meta never became a feature
    assert "feat_mfe_r" not in row and "feat_exit_price" not in row
    # decision-time specs DID become features
    assert any(k.startswith("feat_") for k in row)
