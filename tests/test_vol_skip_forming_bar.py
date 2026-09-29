"""The M21 entry vol-skip gate never ranks a still-forming bar.

RQ-20260929-002 / PI-20260929-EXITOPS-0005 — entry-side twin of the
trail_vol fix (#14101). Live, the builders' frame ends on the forming bar; its
partial range distorts its ATR, so the gate ranks the last CLOSED bar instead.
Each unit gets the pair #14101 used: a forming extreme bar must NOT flip the
gate, the same extreme bar once closed MUST. Plus the fetch-limit guard: a
declarer's builder fetches window + 1 bars, or the trim leaves the percentile
undefined and the fail-permissive gate silently never fires.
"""
from __future__ import annotations

import time

import pandas as pd
import pytest
import yaml

from src.units.strategies import htf_pullback_trend_2h as pb
from src.units.strategies import trend_donchian as td

TF_S = 7200
WIN = 200


def _stamp(rows: list[dict], forming_last: bool) -> pd.DataFrame:
    """Bar-open timestamps, 2h apart. forming_last: the last bar opened 7 min
    ago (the median live decision offset); else it closed an hour ago."""
    now = time.time()
    last_open = now - 7 * 60 if forming_last else now - TF_S - 3600
    n = len(rows)
    for k, r in enumerate(rows):
        r["timestamp"] = pd.Timestamp((last_open - (n - 1 - k) * TF_S), unit="s")
        r.setdefault("open", r["close"])
        r.setdefault("volume", 1.0)
    return pd.DataFrame(rows)[["timestamp", "open", "high", "low", "close", "volume"]]


def _bar(c: float, rng: float = 1.0) -> dict:
    return {"close": c, "high": c + rng / 2, "low": c - rng / 2}


# ── htf_pullback (ada_pullback_2h: vol_skip_below_pctl 0.1) ──────────────────

def _pullback_rows(last_rng: float) -> list[dict]:
    # 201 bars with a constant true range (|step| < rng/2 keeps TR == h - l):
    # a steady uptrend, a 9-bar pullback into the lower half of the recent
    # range, then a bullish confirmation bar. Only the LAST bar's range varies.
    closes, c = [], 100.0
    for _ in range(191):
        c += 0.3
        closes.append(c)
    for _ in range(9):
        c -= 0.45
        closes.append(c)
    rows = [_bar(x) for x in closes]
    rows.append(_bar(c + 0.1, last_rng))
    return rows


PB_CFG = {"symbol": "ADAUSDT", "timeframe": "2h", "trend_lookback": 40,
          "pullback_lookback": 10, "pullback_frac": 0.5, "atr_period": 14,
          "atr_stop_mult": 1.5, "trail_mult": 5.0, "tp_r": 4,
          "min_confidence": 0.0, "vol_skip_below_pctl": 0.1,
          "strategy_label": "ada_pullback_2h"}


def test_pullback_setup_fixture_reaches_the_gate():
    # Control for the pair below: with the gate off, the fixture IS a setup,
    # so a missing vol-gate rejection below cannot be "no setup at all".
    df = _stamp(_pullback_rows(0.05), forming_last=True)
    cfg = {k: v for k, v in PB_CFG.items() if k != "vol_skip_below_pctl"}
    assert pb.order_package(cfg, candles_df=df)["direction"] == "long"


def test_pullback_forming_dead_bar_does_not_flip_gate():
    df = _stamp(_pullback_rows(0.05), forming_last=True)
    pkg = pb.order_package(dict(PB_CFG), candles_df=df)  # must not raise
    assert pkg["direction"] == "long"


def test_pullback_closed_dead_bar_does_flip_gate():
    df = _stamp(_pullback_rows(0.05), forming_last=False)
    with pytest.raises(ValueError, match="vol-at-entry gate"):
        pb.order_package(dict(PB_CFG), candles_df=df)


# ── trend_donchian (trend_donchian_xrp_4h: vol_skip_above_pctl 0.9) ──────────

def _donchian_rows(last_rng: float) -> list[dict]:
    # 201 flat bars oscillating inside a band, constant TR, then a breakout
    # bar closing above the 20-bar high. Only the LAST bar's range varies.
    rows = [_bar(100.0 + (0.2 if k % 2 else -0.2)) for k in range(201)]
    rows.append(_bar(102.0, last_rng))
    return rows


TD_CFG = {"symbol": "XRPUSDT", "timeframe": "2h", "donchian": 20,
          "atr_period": 14, "atr_stop_mult": 2.0, "trail_mult": 5.0,
          "tp_r": 3, "min_confidence": 0.0, "vol_skip_above_pctl": 0.9,
          "strategy_label": "trend_donchian_xrp_4h"}


def test_donchian_setup_fixture_reaches_the_gate():
    df = _stamp(_donchian_rows(12.0), forming_last=True)
    cfg = {k: v for k, v in TD_CFG.items() if k != "vol_skip_above_pctl"}
    assert td.order_package(cfg, candles_df=df)["direction"] == "long"


def test_donchian_forming_hot_bar_does_not_flip_gate():
    df = _stamp(_donchian_rows(12.0), forming_last=True)
    pkg = td.order_package(dict(TD_CFG), candles_df=df)  # must not raise
    assert pkg["direction"] == "long"


def test_donchian_closed_hot_bar_does_flip_gate():
    df = _stamp(_donchian_rows(12.0), forming_last=False)
    with pytest.raises(ValueError, match="vol-at-entry gate"):
        td.order_package(dict(TD_CFG), candles_df=df)


# ── fetch limit: the trim must never leave the window unfilled ───────────────

def _declarers() -> dict[str, dict]:
    cfg = yaml.safe_load(open("config/strategies.yaml"))
    blocks = cfg.get("strategies", cfg)
    return {name: b for name, b in blocks.items() if isinstance(b, dict)
            and (b.get("vol_skip_above_pctl") or b.get("vol_skip_below_pctl"))}


def test_every_declarer_found():
    # Denominator for the parametrised guard below (4 on 2026-09-29).
    assert len(_declarers()) >= 1


@pytest.mark.parametrize("name", sorted(_declarers()))
def test_declarer_builder_fetches_window_plus_one(name, monkeypatch):
    from src.runtime import intent_multiplexer, market_data
    from src.runtime import strategy_signal_builders as ssb

    seen: dict = {}

    def fake_fetch(symbol, timeframe, *, limit, exchange_client=None, **_):
        seen["limit"] = limit
        return None  # builders raise on no data; the limit is what we check

    monkeypatch.setattr(market_data, "fetch_candles", fake_fetch)
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda s: object())
    # The mapping the live multiplexer dispatches through, not a guessed name.
    builder = intent_multiplexer._default_intent_builders()[name]
    with pytest.raises(RuntimeError):
        builder({})
    block = _declarers()[name]
    window = int(block.get("vol_pctl_window") or WIN)
    assert seen["limit"] >= window + 1 + int(block.get("confirm_bars") or 0)


def test_non_declarer_fetch_limit_unchanged():
    from src.runtime.strategy_signal_builders import _entry_fetch_limit
    assert _entry_fetch_limit({"timeframe": "2h"}) == 200
    assert _entry_fetch_limit({"vol_skip_below_pctl": 0.1}) == 201
    assert _entry_fetch_limit({"vol_skip_above_pctl": 0.9,
                               "vol_pctl_window": 300, "confirm_bars": 2}) == 303
