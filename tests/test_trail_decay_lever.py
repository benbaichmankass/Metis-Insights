"""M20 P4.1 — live trail-decay lever (shared runtime helper + both monitors).

Contract (mirrors the stale/giveback lever tests):
  * Declared (``trail_decay_tight_mult`` + an arm in meta) ⇒ the monitor's
    trail ratchets with the TIGHTENED mult once armed (a higher stop for a
    long than the base trail would give).
  * Undeclared ⇒ behaviour byte-unchanged, but a stalled trade writes ONE
    observe-only annotate row (reference cell stall-6).
  * Fail-safe skips: missing risk/entry_time ⇒ base mult; never raises.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.units.strategies import htf_pullback_trend_2h as pb
from src.units.strategies import trend_donchian as td


def _candles(n: int = 40, tf_minutes: int = 60) -> pd.DataFrame:
    """Flat tape with a peak at bar 20, then a stall (no new high)."""
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        price = 100.0 + (i * 0.3 if i <= 20 else 6.0 - (i - 20) * 0.05)
        rows.append({"timestamp": (start + timedelta(minutes=tf_minutes * i))
                     .isoformat(),
                     "open": price, "high": price + 0.2, "low": price - 0.2,
                     "close": price, "volume": 1.0})
    return pd.DataFrame(rows)


def _pkg(df, unit="donchian", declared=False, entry_bar=5):
    meta = {"atr": 1.0, "atr_period": 14, "trail_mult": 4.0,
            "risk_per_unit": 2.0,
            "entry_time": df["timestamp"].iloc[entry_bar],
            "timeframe": "1h" if unit == "donchian" else "2h"}
    if declared:
        meta["trail_decay_stall_bars"] = 6
        meta["trail_decay_tight_mult"] = 2.0
    entry = float(df["close"].iloc[entry_bar])
    return {"symbol": "SOLUSDT", "direction": "long", "entry": entry,
            "sl": entry - 2.0, "tp": entry * 3, "meta": meta,
            "order_package_id": f"pkg-decay-{unit}-{declared}"}


@pytest.fixture(autouse=True)
def _isolated_soak(tmp_path, monkeypatch):
    from src.runtime import exit_lever_soak

    monkeypatch.setattr(exit_lever_soak, "soak_log_path",
                        lambda: tmp_path / "exit_lever_soak.jsonl")
    exit_lever_soak._ANNOTATED.clear()
    yield tmp_path


@pytest.mark.parametrize("mod", [td, pb])
def test_declared_stall_tightens_the_trail(mod):
    df = _candles()
    unit = "donchian" if mod is td else "pullback"
    base = mod.monitor({}, df, _pkg(df, unit, declared=False))
    tight = mod.monitor({}, df, _pkg(df, unit, declared=True))
    # peak high = 106.2 at bar 20; base trail 4*1.0 -> 102.2; tightened
    # 2*1.0 -> 104.2. Both ratchet (> sl), tight must sit HIGHER.
    assert base and "sl" in base and tight and "sl" in tight
    assert tight["sl"] > base["sl"]
    assert abs(tight["sl"] - (base["sl"] + 2.0)) < 1e-6


@pytest.mark.parametrize("mod", [td, pb])
def test_not_armed_before_stall(mod):
    df = _candles(n=24)  # only ~3 bars past the peak — stall 6 not reached
    unit = "donchian" if mod is td else "pullback"
    base = mod.monitor({}, df, _pkg(df, unit, declared=False))
    tight = mod.monitor({}, df, _pkg(df, unit, declared=True))
    if base is None:
        assert tight is None or "sl" not in tight
    else:
        assert tight == base  # declared but unarmed ⇒ identical trail


def test_undeclared_annotates_on_stall(_isolated_soak):
    df = _candles()
    td.monitor({}, df, _pkg(df, "donchian", declared=False))
    log = _isolated_soak / "exit_lever_soak.jsonl"
    assert log.exists()
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    decay = [r for r in rows if r["lever"] == "trail_decay"]
    assert len(decay) == 1
    assert decay[0]["mode"] == "annotate"
    assert decay[0]["params"]["trail_decay_stall_bars"] == 6
    assert decay[0]["state"]["bars_since_peak"] >= 6
    # dedup: second tick, still one row
    td.monitor({}, df, _pkg(df, "donchian", declared=False))
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert len([r for r in rows if r["lever"] == "trail_decay"]) == 1


def test_missing_risk_or_entry_time_is_fail_safe():
    df = _candles()
    pkg = _pkg(df, "donchian", declared=True)
    pkg["meta"].pop("entry_time")
    v = td.monitor({}, df, pkg)
    # without entry_time the decay never arms; verdict equals the base-mult
    # path computed on the full-frame fallback window — no exception is the
    # contract here
    assert v is None or isinstance(v, dict)
    pkg2 = _pkg(df, "donchian", declared=True)
    pkg2["meta"]["risk_per_unit"] = 0
    v2 = td.monitor({}, df, pkg2)
    assert v2 is None or isinstance(v2, dict)


def test_order_package_threads_decay_params_donchian():
    n = 40
    df = _candles(n)
    df.loc[df.index[-1], "close"] = 120.0
    df.loc[df.index[-1], "high"] = 120.5
    cfg = {"symbol": "SOLUSDT", "min_confidence": 0.0,
           "trail_decay_stall_bars": 6, "trail_decay_tight_mult": 2.0}
    pkg = td.order_package(cfg, df)
    assert pkg["meta"]["trail_decay_stall_bars"] == 6
    assert pkg["meta"]["trail_decay_tight_mult"] == 2.0
