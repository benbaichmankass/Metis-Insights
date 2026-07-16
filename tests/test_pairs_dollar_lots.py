"""Unit tests for scripts/research/pairs_dollar_lots.py (G2 — $-and-lots sim).

Verifies the dollar/lot translation of the R-space pairs engine: engine-reuse
parity (collected rows == summarized trade count), lot flooring + both-legs-or-
nothing skip (mirroring the live #6591 gate), the two-leg $ P&L on floored qtys,
and the balance→skip curve. Fully offline (synthetic OU pair), no network."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO_ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


bp = _load("backtest_pairs", "scripts/backtest_pairs.py")
g2 = _load("pairs_dollar_lots", "scripts/research/pairs_dollar_lots.py")


def _ou_frame(n=4000, seed=11):
    rng = np.random.default_rng(seed)
    lb = np.cumsum(rng.normal(0, 0.01, n)) + np.log(100.0)
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = 0.9 * s[i - 1] + rng.normal(0, 0.02)
    la = lb + s
    ts = pd.date_range("2021-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "close_a": np.exp(la), "close_b": np.exp(lb)})


def _rows(m, **kw):
    rows = []
    summary = bp.run_backtest(
        m, lookback=kw.get("lookback", 20), entry_z=2.0, exit_z=0.5, stop_z=2.0,
        max_hold_bars=20, cooldown_bars=1, hedge_beta="one", timeframe="1h",
        pair="A/B", collect_rows=rows)
    return rows, summary


# --- engine-reuse parity -------------------------------------------------

def test_collect_rows_matches_summary_count():
    m = _ou_frame()
    rows, summary = _rows(m)
    n_summary = (summary.get("trades_long") or 0) + (summary.get("trades_short") or 0)
    assert len(rows) == n_summary > 0
    # each row carries the leg-level fields the sim needs
    r0 = rows[0]
    for k in ("entry_price_a", "entry_price_b", "exit_price_a", "exit_price_b",
              "beta", "risk_spread", "direction", "gross_r", "outcome"):
        assert k in r0


def test_collect_rows_omitted_is_unchanged():
    """Omitting collect_rows must not change the summary (byte-for-byte contract)."""
    m = _ou_frame()
    a = bp.run_backtest(m, lookback=20, entry_z=2.0, exit_z=0.5, stop_z=2.0,
                        max_hold_bars=20, cooldown_bars=1, hedge_beta="one",
                        timeframe="1h", pair="A/B")
    b_rows: list = []
    b = bp.run_backtest(m, lookback=20, entry_z=2.0, exit_z=0.5, stop_z=2.0,
                        max_hold_bars=20, cooldown_bars=1, hedge_beta="one",
                        timeframe="1h", pair="A/B", collect_rows=b_rows)
    assert a == b


# --- lot flooring --------------------------------------------------------

def test_floor_to_lot_floors_down_and_checks_min():
    assert g2._floor_to_lot(0.0037, 0.001, 0.001) == (0.003, True)
    # below the min after flooring -> not ok
    q, ok = g2._floor_to_lot(0.0007, 0.001, 0.001)
    assert q == 0.0 and ok is False
    # exact min clears
    q, ok = g2._floor_to_lot(0.001, 0.001, 0.001)
    assert ok is True
    # no rule (step 0) -> passthrough, ok iff >= min
    assert g2._floor_to_lot(5.0, 0.0, 0.0) == (5.0, True)


def test_leg_signs():
    assert g2._leg_signs("long_spread") == (1.0, -1.0)
    assert g2._leg_signs("short_spread") == (-1.0, 1.0)


# --- both-legs-or-nothing skip + balance curve ---------------------------

def test_coarse_lots_skip_everything():
    m = _ou_frame()
    rows, _ = _rows(m)
    out = g2.simulate_dollar_lots(rows, balance=100.0, risk_pct=0.015,
                                  pairs_risk_fraction=1.0,
                                  lot_a=(1e9, 1e9), lot_b=(1e9, 1e9))
    assert out["skip_pct"] == 100.0 and out["n_placed"] == 0
    assert out["expectancy_usd"] is None and out["win_pct"] is None


def test_fine_lots_place_all_and_positive_edge():
    m = _ou_frame()
    rows, _ = _rows(m)
    out = g2.simulate_dollar_lots(rows, balance=1e6, risk_pct=0.015,
                                  pairs_risk_fraction=1.0,
                                  lot_a=(1e-9, 1e-9), lot_b=(1e-9, 1e-9),
                                  fee_bps_roundtrip=0.0)
    assert out["n_skipped"] == 0 and out["n_placed"] == len(rows)
    assert out["net_usd"] > 0  # OU edge is positive; fee-free + hedged


def test_skip_fraction_monotone_in_balance():
    """Higher balance -> larger budget -> fewer sub-min skips (weakly monotone)."""
    m = _ou_frame()
    rows, _ = _rows(m)
    lots = (0.01, 0.01)
    skips = [
        g2.simulate_dollar_lots(rows, balance=b, risk_pct=0.015,
                                pairs_risk_fraction=1.0, lot_a=lots, lot_b=lots)["skip_pct"]
        for b in (100.0, 1000.0, 10000.0, 100000.0)
    ]
    assert all(skips[i] >= skips[i + 1] - 1e-9 for i in range(len(skips) - 1)), skips


def test_budget_scales_with_risk_fraction():
    m = _ou_frame()
    rows, _ = _rows(m)
    full = g2.simulate_dollar_lots(rows, balance=5000.0, risk_pct=0.015,
                                   pairs_risk_fraction=1.0,
                                   lot_a=(1e-9, 1e-9), lot_b=(1e-9, 1e-9))
    half = g2.simulate_dollar_lots(rows, balance=5000.0, risk_pct=0.015,
                                   pairs_risk_fraction=0.5,
                                   lot_a=(1e-9, 1e-9), lot_b=(1e-9, 1e-9))
    assert abs(full["budget_usd"] - 2 * half["budget_usd"]) < 1e-6


def test_run_pair_end_to_end_and_parity_flag():
    m = _ou_frame()

    class _Args:
        lookback = 20
        entry_z = 2.0
        exit_z = 0.5
        stop_z = 2.0
        max_hold_bars = 20
        cooldown_bars = 1
        hedge_beta = "one"
        resample = "1h"
        risk_pct = 0.015
        pairs_risk_fraction = 1.0
        fee_bps_roundtrip = bp.FEE_BPS_ROUNDTRIP
        balances = [200.0, 5000.0, 166000.0]

    res = g2.run_pair(m, _Args(), symbol_a="AAA", symbol_b="BBB")
    assert res["rows_match_summary"] is True
    assert len(res["balance_sweep"]) == 3
    assert res["n_trades"] > 0


def test_real_btc_leg_squeeze_skips_on_small_balance():
    """BTC min 0.001 at a high price makes the BTC leg sub-min on a small budget."""
    # one synthetic trade: A=ETH-like ($3k), B=BTC-like ($100k), small beta.
    rows = [{
        "entry_time": "t0", "exit_time": "t1", "direction": "long_spread",
        "entry_price_a": 3000.0, "entry_price_b": 100000.0,
        "exit_price_a": 3030.0, "exit_price_b": 100100.0,
        "beta": 0.03, "risk_spread": 0.05, "gross_r": 0.5, "outcome": "revert",
    }]
    small = g2.simulate_dollar_lots(rows, balance=500.0, risk_pct=0.015,
                                    pairs_risk_fraction=1.0,
                                    lot_a=(0.01, 0.01), lot_b=(0.001, 0.001))
    # budget = 500*0.015 = $7.5; N_A = 7.5/0.05 = $150 -> qty_a ~0.05 ETH (ok);
    # N_B = 0.03*150 = $4.5 -> qty_b ~0.000045 BTC << 0.001 min -> skip.
    assert small["n_skipped"] == 1 and small["n_placed"] == 0
    assert any("leg_b" in k for k in small["skip_reasons"])
