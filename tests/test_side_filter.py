"""side_filter directional-gate capability (Tier-1, 2026-07-30).

One reusable ``side_filter: long|short|both`` gate for the trend + pullback
signal builders, generalizing the pre-existing ``long_only`` flag. Motivated by
the crypto short-only fine-tunes (the alt-crypto LONG side is a persistent
bearish-regime drag on ``sol_pullback_2h`` / ``trend_donchian_xrp_4h`` — see
docs/research/crypto-finetune-proposals-2026-07-30.md).

Covers: the pure resolution helpers, and end-to-end suppression at all three
wired builder sites (flagship trend_donchian, the trend_donchian variant, and
the htf-pullback variant). Fully offline (synthetic OHLCV / monkeypatched
order_package; no exchange / network / secrets).
"""
from __future__ import annotations

import pandas as pd

import src.runtime.strategy_signal_builders as ssb


# ── pure helpers ────────────────────────────────────────────────────────────
def test_resolve_side_filter_precedence():
    # explicit side_filter wins over everything
    assert ssb._resolve_side_filter({"side_filter": "short"}) == "short"
    assert ssb._resolve_side_filter({"side_filter": "long"}) == "long"
    assert ssb._resolve_side_filter({"side_filter": "both"}) == "both"
    # explicit side_filter beats a conflicting legacy long_only
    assert ssb._resolve_side_filter({"side_filter": "short", "long_only": True}) == "short"
    # legacy long_only maps to "long" when side_filter absent
    assert ssb._resolve_side_filter({"long_only": True}) == "long"
    # default is two-sided
    assert ssb._resolve_side_filter({}) == "both"
    assert ssb._resolve_side_filter({"long_only": False}) == "both"
    # case / whitespace tolerant
    assert ssb._resolve_side_filter({"side_filter": " Short "}) == "short"
    # unrecognised value ignored → falls back to long_only/both
    assert ssb._resolve_side_filter({"side_filter": "buy"}) == "both"
    assert ssb._resolve_side_filter({"side_filter": "buy", "long_only": True}) == "long"


def test_side_filter_suppresses():
    assert ssb._side_filter_suppresses("short", "long") is True
    assert ssb._side_filter_suppresses("long", "long") is False
    assert ssb._side_filter_suppresses("long", "short") is True
    assert ssb._side_filter_suppresses("short", "short") is False
    # both never suppresses either side
    assert ssb._side_filter_suppresses("long", "both") is False
    assert ssb._side_filter_suppresses("short", "both") is False


def test_side_filter_reason_backcompat():
    # the long gate keeps the legacy string (downstream analytics key on it)
    assert ssb._side_filter_reason("long") == "short_suppressed_long_only"
    # the new short gate is symmetric
    assert ssb._side_filter_reason("short") == "long_suppressed_short_only"


# ── trend_donchian variant end-to-end (real order_package) ──────────────────
def _trend_frame(direction: str, n: int = 60) -> pd.DataFrame:
    step = 1.0 if direction == "up" else -1.0
    rows = []
    for k in range(n):
        base = 30000.0 + step * k * 50.0
        rows.append([base, base + 25.0, base - 25.0, base + 20.0 * step])
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["ts"] = pd.date_range("2024-01-01", periods=len(df), freq="1h", tz="UTC")
    return df


def _wire_trend_variant(monkeypatch, name, symbol, frame, cfg_extra):
    cfg = {
        "enabled": True, "timeframe": "1h", "donchian": 20, "atr_period": 14,
        "atr_stop_mult": 2.5, "trail_mult": 5.0, "tp_r": 50.0,
        "min_confidence": 0.0, "symbols": [symbol], **cfg_extra,
    }
    import src.units.strategies as units
    monkeypatch.setattr(units, "load_strategy_config",
                        lambda *a, **k: {name: cfg}, raising=False)
    import src.runtime.market_data as md
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: frame, raising=False)
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: None)
    monkeypatch.setattr(ssb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "_emit_shadow_preds", lambda *a, **k: None)
    return ssb._trend_donchian_variant_builder(name, {"SYMBOL": symbol})


def test_trend_variant_short_only_suppresses_long(monkeypatch):
    # side_filter: short — an uptrend (would-be long) must yield none, never buy.
    sig = _wire_trend_variant(
        monkeypatch, "trend_donchian_xrp_4h", "XRPUSDT",
        _trend_frame("up"), {"side_filter": "short"})
    assert sig["side"] in ("none", "sell")
    assert sig["side"] != "buy"
    if sig["side"] == "none":
        assert sig["meta"].get("reason") == "long_suppressed_short_only"


def test_trend_variant_short_only_passes_short(monkeypatch):
    # a downtrend (short) passes through under side_filter: short.
    sig = _wire_trend_variant(
        monkeypatch, "trend_donchian_xrp_4h", "XRPUSDT",
        _trend_frame("down"), {"side_filter": "short"})
    assert sig["side"] in ("sell", "none")
    assert sig["side"] != "buy"


def test_trend_variant_both_default_two_sided(monkeypatch):
    # no gate → a downtrend can still emit a sell (byte-identical to legacy).
    sig = _wire_trend_variant(
        monkeypatch, "trend_donchian_xrp_4h", "XRPUSDT",
        _trend_frame("down"), {})
    assert sig["side"] in ("sell", "none")
    assert sig["meta"].get("reason") != "long_suppressed_short_only"


# ── htf-pullback variant (monkeypatched order_package for deterministic side) ─
def _fake_pkg(direction: str) -> dict:
    return {
        "direction": direction,
        "entry": 100.0, "sl": 98.0, "tp": 110.0, "confidence": 0.5,
        "meta": {"setup_type": "pullback"},
    }


def _wire_pullback_variant(monkeypatch, name, symbol, direction, cfg_extra):
    cfg = {"enabled": True, "timeframe": "2h", "symbols": [symbol], **cfg_extra}
    import src.units.strategies as units
    monkeypatch.setattr(units, "load_strategy_config",
                        lambda *a, **k: {name: cfg}, raising=False)
    import src.runtime.market_data as md
    frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
         "ts": pd.date_range("2024-01-01", periods=1, freq="2h", tz="UTC")})
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: frame, raising=False)
    import src.units.strategies.htf_pullback_trend_2h as unit
    monkeypatch.setattr(unit, "order_package",
                        lambda *a, **k: _fake_pkg(direction), raising=False)
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: None)
    monkeypatch.setattr(ssb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "_emit_shadow_preds", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "_stamp_regime_on_meta", lambda *a, **k: None)
    return ssb._htf_pullback_variant_builder(name, {"SYMBOL": symbol})


def test_pullback_short_only_suppresses_long(monkeypatch):
    # sol_pullback_2h fine-tune: side_filter: short suppresses a long signal.
    sig = _wire_pullback_variant(
        monkeypatch, "sol_pullback_2h", "SOLUSDT", "long",
        {"side_filter": "short"})
    assert sig["side"] == "none"
    assert sig["meta"].get("reason") == "long_suppressed_short_only"


def test_pullback_short_only_passes_short(monkeypatch):
    sig = _wire_pullback_variant(
        monkeypatch, "sol_pullback_2h", "SOLUSDT", "short",
        {"side_filter": "short"})
    assert sig["side"] == "sell"


def test_pullback_both_default_passes_long(monkeypatch):
    # default (no side_filter) is byte-identical: a long passes through.
    sig = _wire_pullback_variant(
        monkeypatch, "sol_pullback_2h", "SOLUSDT", "long", {})
    assert sig["side"] == "buy"
    assert sig["meta"].get("reason") != "long_suppressed_short_only"
