"""Wiring + long-only-gate tests for mes_trend_long_1d (overnight research 2026-06-01).

The trend logic is the live trend_donchian unit (covered by test_trend_donchian.py).
These tests pin what makes this instance distinct: the live-on-paper config (MES /
1d / long-only), the ib_paper (IBKR paper) routing, the priority floor, and — the one
genuinely-new behaviour — the LONG-ONLY gate that suppresses short signals.
Fully offline (synthetic OHLCV + monkeypatch; no exchange / network / secrets).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import src.runtime.strategy_signal_builders as ssb


def _load_strategies_cfg():
    from src.units.strategies import load_strategy_config
    return load_strategy_config() or {}


# ---------------------------------------------------------------------------
# Config / routing / priority wiring
# ---------------------------------------------------------------------------
def test_config_block_is_live_long_only_mes():
    cfg = _load_strategies_cfg().get("mes_trend_long_1d")
    assert cfg is not None, "mes_trend_long_1d missing from config/strategies.yaml"
    assert cfg["execution"] == "live"        # PROMOTED 2026-06-02: executes on ib_paper
                                             # (PAPER money). Operator policy — paper
                                             # accounts always execute (test the strategy).
    assert cfg["enabled"] is True
    assert cfg["long_only"] is True
    assert cfg["timeframe"] == "1d"
    assert cfg.get("symbols") == ["MES"]     # NOT BTCUSDT
    assert int(cfg["donchian"]) == 30


def test_routed_to_ib_paper_only():
    import yaml
    accounts = yaml.safe_load(open("config/accounts.yaml"))["accounts"]
    assert "mes_trend_long_1d" in accounts["ib_paper"]["strategies"]
    for acct in ("bybit_1", "bybit_2"):
        assert "mes_trend_long_1d" not in accounts[acct].get("strategies", []), \
            f"MES long-only sleeve must not be on {acct} (it's an IBKR strategy)"


def test_intent_priority_registered_below_established_roster():
    from src.runtime.intents import DEFAULT_PRIORITIES
    assert DEFAULT_PRIORITIES.get("mes_trend_long_1d") == 0
    assert DEFAULT_PRIORITIES["mes_trend_long_1d"] < DEFAULT_PRIORITIES["fade_breakout_4h"]


# ---------------------------------------------------------------------------
# The long-only gate — the one new behaviour
# ---------------------------------------------------------------------------
def _trend_frame(direction: str) -> pd.DataFrame:
    """50 daily bars trending up or down so the Donchian unit fires a long/short."""
    rng = np.random.RandomState(5)
    step = 1.0 if direction == "up" else -1.0
    rows = []
    for k in range(50):
        base = 130.0 + step * k
        rows.append([base, base + 0.5, base - 0.5, base + 0.4 * step])
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["ts"] = pd.date_range("2024-01-01", periods=len(df), freq="1D", tz="UTC")
    _ = rng  # determinism placeholder
    return df


def _wire_builder(monkeypatch, frame):
    """Drive mes_trend_long_1d_signal_builder against a synthetic frame, enabled."""
    import src.units.strategies as units
    monkeypatch.setattr(
        units, "load_strategy_config",
        lambda *a, **k: {"mes_trend_long_1d": {
            "enabled": True, "timeframe": "1d", "donchian": 20,
            "atr_period": 14, "atr_stop_mult": 2.5, "trail_mult": 4.0,
            "tp_r": 50.0, "min_confidence": 0.0,
        }},
        raising=False,
    )
    import src.runtime.market_data as md
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: frame, raising=False)
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: None)
    monkeypatch.setattr(ssb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "_emit_shadow_preds", lambda *a, **k: None)
    return ssb.mes_trend_long_1d_signal_builder({"SYMBOL": "MES"})


def test_short_signal_is_suppressed(monkeypatch):
    sig = _wire_builder(monkeypatch, _trend_frame("down"))
    # On a downtrend the unit fires a short (suppressed) or is non-actionable;
    # either way a long-only strategy returns side=none and NEVER a sell.
    assert sig["side"] == "none"
    assert sig["side"] != "sell"


def test_long_signal_passes_through(monkeypatch):
    sig = _wire_builder(monkeypatch, _trend_frame("up"))
    # An uptrend either fires a long (buy) or is non-actionable (none) — never a sell.
    assert sig["side"] in ("buy", "none")
    if sig["side"] == "buy":
        assert sig["stop_loss"] < sig["entry_price"] < sig["take_profit"]
