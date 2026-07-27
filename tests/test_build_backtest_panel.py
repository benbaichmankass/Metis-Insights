"""M30 · C1-for-backtests — tests for scripts/research/build_backtest_panel.py.

Wiring is exercised OFFLINE with an INJECTED synthetic adapter (a tiny pandas
candle frame + hand-built SimTrades) — no backtest harness run, no network, no
live DB — so the panel schema, the decision-time feature extraction, the native
MFE/MAE excursion join, the leakage-stamped manifest, and the C1-shape contract
are all deterministic. Loaded via importlib (scripts/research is not a package).
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(
    os.path.dirname(_HERE), "scripts", "research", "build_backtest_panel.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("build_backtest_panel", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bb = _load()


def _candle_df():
    # 10 bars; the trade under test opens at index 2 and exits at index 6, so the
    # excursion window is iloc[3:7] (the 4 post-entry bars 3..6). Highs/lows there
    # are chosen so MFE/MAE are exact for a long at entry=100, risk=10.
    highs = [101, 102, 103, 105, 108, 102, 101, 100, 100, 100]
    lows = [99, 98, 97, 98, 99, 97, 100, 99, 99, 99]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC"),
            "open": [100] * 10,
            "high": highs,
            "low": lows,
            "close": [100] * 10,
            "volume": [1.0] * 10,
        }
    )


_META = {
    # ict_scalp_5m strategy-specific specs
    "sweep_extreme": 100.0,
    "sweep_level": 110.0,  # |100-110|/atr(10) = 1.0
    "displacement_body_to_range": 0.8,
    "fvg_size": 20.0,  # /atr(10) = 2.0
    "mitigation_mode": "wick_rejection",
    "htf_filter_active": True,
    "atr": 10.0,
    # common stamped specs
    "adx_14": 25.0,
    "regime": "trending",
    "vol_regime": "high",
    "setup_tf": "5m",
    "killzone": "london",
    "bias": "bullish",
    # an OUTCOME key the harness also stamps on meta — must NEVER become a feature
    "mfe_r": 999.0,
    "exit_price": 104.0,
    "bars_held": 4,
}


def _sim_trades():
    winner = bb.SimTrade(
        strategy="ict_scalp_5m",
        symbol="BTCUSDT",
        side="long",
        entry_price=100.0,
        stop_loss=90.0,  # risk = 10
        exit_price=104.0,  # realized_r = 0.4
        r_multiple=0.4,
        entry_index=2,
        exit_index=6,
        exit_time=pd.Timestamp("2024-01-01T00:30:00Z"),
        meta=dict(_META),
        confidence=0.77,
    )
    # a trade with unusable indices → honest excursion_present:false
    no_window = bb.SimTrade(
        strategy="ict_scalp_5m",
        symbol="BTCUSDT",
        side="short",
        entry_price=100.0,
        stop_loss=110.0,
        exit_price=95.0,
        r_multiple=0.5,
        entry_index=None,
        exit_index=None,
        exit_time=pd.Timestamp("2024-01-01T01:00:00Z"),
        meta=dict(_META),
        confidence=0.5,
    )
    return [winner, no_window]


def _register_fake_adapter(monkeypatch_registry=True):
    df = _candle_df()
    trades = _sim_trades()

    def _fake(**_opts):
        return df, trades, {"harness": "fake", "n_bars": len(df)}

    bb.ADAPTERS["_fake"] = _fake
    return df, trades


def test_panel_schema_and_features():
    _register_fake_adapter()
    rows, manifest = bb.build_backtest_panel(harness="_fake", adapter_opts={})
    assert manifest["error"] is None
    assert manifest["row_count"] == 2
    assert manifest["cohort"] == "backtest"

    # decision-time features from component_vector.extract(ict_scalp_5m meta)
    win = rows[0]
    assert win["strategy"] == "ict_scalp_5m"
    assert win["symbol"] == "BTCUSDT"
    assert win["cohort"] == "backtest"
    assert win["feat_sweep_depth_atr"] == 1.0
    assert win["feat_displacement_strength"] == 0.8
    assert win["feat_fvg_size_atr"] == 2.0
    assert win["feat_adx_14"] == 25.0
    assert win["feat_confidence"] == 0.77
    assert win["cat_mitigation_mode"] == "wick_rejection"
    assert win["cat_regime"] == "trending"
    assert win["cat_setup_type"] == "5m"
    assert win["gate_htf_bias_aligned"] is True
    # closed_at is a sortable ISO-8601 string (the WF-CV time column)
    assert isinstance(win["closed_at"], str) and win["closed_at"].startswith("2024-01-01T00:30")


def test_native_excursions_are_exact():
    _register_fake_adapter()
    rows, _ = bb.build_backtest_panel(harness="_fake", adapter_opts={})
    win = rows[0]
    assert win["excursion_present"] is True
    # long, entry 100, risk 10, window highs 105/108/102/101, lows 98/99/97/100
    # MFE = 108-100 = 8 → 0.8R ; MAE = 100-97 = 3 → 0.3R
    assert win["mfe_r"] == 0.8
    assert win["mae_r"] == 0.3
    # realized (exit 104) = 0.4R ; giveback = 0.8 - 0.4 = 0.4 ; capture = 0.4/0.8 = 0.5
    assert win["realized_r"] == 0.4
    assert win["giveback_r"] == 0.4
    assert win["capture_ratio"] == 0.5
    assert win["bars_held"] == 4


def test_unusable_indices_are_honest_null():
    _register_fake_adapter()
    rows, manifest = bb.build_backtest_panel(harness="_fake", adapter_opts={})
    nw = rows[1]
    assert nw["excursion_present"] is False
    for col in bb._EXCURSION_COLS:
        assert nw[col] is None
    # still a valid outcome row (win/r from the harness r_multiple)
    assert nw["r"] == 0.5
    assert nw["win"] == 1
    assert manifest["excursion_covered"] == 1
    assert manifest["excursion_coverage_pct"] == 50.0


def test_leakage_contract_clean():
    _register_fake_adapter()
    _, manifest = bb.build_backtest_panel(harness="_fake", adapter_opts={})
    feats = set(manifest["feature_cols"])
    outs = set(manifest["outcome_cols"])
    # the excursion + win/r/pnl columns are OUTCOMES, never features
    assert not (feats & outs)
    # the outcome-only meta key (mfe_r=999) never became a feature column
    assert "feat_mfe_r" not in feats
    assert "feat_exit_price" not in feats
    # excursion outcomes are stamped in outcome_cols
    for col in ("mfe_r", "mae_r", "giveback_r", "capture_ratio"):
        assert col in outs
    # win/r consistency
    assert manifest["outcome_cols"][:3] == ["pnl", "win", "r"]


def test_pnl_is_r_proxy():
    _register_fake_adapter()
    rows, manifest = bb.build_backtest_panel(harness="_fake", adapter_opts={})
    assert rows[0]["pnl"] == rows[0]["r"] == 0.4
    assert "R-proxy" in manifest["pnl_note"]


def test_unknown_harness_is_tolerant():
    rows, manifest = bb.build_backtest_panel(harness="_does_not_exist", adapter_opts={})
    assert rows == []
    assert manifest["row_count"] == 0
    assert "unknown harness" in (manifest["error"] or "")


def test_adapter_exception_is_surfaced_not_raised():
    def _boom(**_opts):
        raise RuntimeError("harness blew up")

    bb.ADAPTERS["_boom"] = _boom
    rows, manifest = bb.build_backtest_panel(harness="_boom", adapter_opts={})
    assert rows == []
    assert "harness blew up" in (manifest["error"] or "")
