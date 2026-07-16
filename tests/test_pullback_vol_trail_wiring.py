"""M20-X — the vol-conditional trail lever is WIRED into the pullback family
monitor (``src/units/strategies/htf_pullback_trend_2h.monitor``), not only
trend_donchian. This is the live path for the qqq_pullback_1h #6510 sweep pass
(vt_hot80_t2.5) and every other pullback leg.

qqq_pullback_1h (and all *_pullback_* strategies) resolve their monitor to the
shared ``htf_pullback_trend_2h`` unit via ``monitor_unit_for``, so a YAML
declare of ``trail_vol_*`` there must actually tighten the trail — otherwise it
is a silent no-op (the MES-stranding class of bug). These tests assert:
  * undeclared ⇒ the pullback monitor's trail is byte-identical (base mult);
  * declared + a HOT-tail bar ⇒ the monitor tightens the ratcheted stop
    (a strictly-closer SL for a long) exactly as resolve_vol_trail_mult dictates.
"""
from __future__ import annotations

import pandas as pd

from src.units.strategies.htf_pullback_trend_2h import monitor

WIN = 200


def _df(ranges):
    # close=100 constant ⇒ TR == per-bar `range` (see test_trail_vol_live), so
    # ATR is the rolling mean of `ranges` and the last bar's percentile is
    # placeable in the hot/cold tail of the trailing window.
    return pd.DataFrame(
        [{"high": 100 + r / 2, "low": 100 - r / 2, "close": 100.0} for r in ranges]
    )


# Calm body then a hot tail ⇒ the last bar's ATR sits in the TOP decile.
_HOT_LAST = _df([0.5] * 230 + [12.0] * 30)

# A long position whose stop is far below ⇒ the chandelier trail will ratchet
# up to `ext - mult*atr`, so the effective mult is directly observable in the SL.
_OPEN_LONG = {"sl": 30.0, "direction": "long", "meta": {}}

_QQQ_CFG = {  # the shipped qqq_pullback_1h declare
    "trail_mult": 5.0, "atr_period": 14,
    "trail_vol_above_pctl": 0.80, "trail_vol_tight_mult": 2.5,
    "vol_pctl_window": WIN,
}
_BASE_CFG = {"trail_mult": 5.0, "atr_period": 14}  # no vol_trail keys


def test_pullback_monitor_undeclared_uses_base_mult():
    out = monitor(_BASE_CFG, _HOT_LAST, dict(_OPEN_LONG))
    # ext(=106 hot-bar high) - 5.0 * atr(=12) = 46.0
    assert out is not None and round(out["sl"], 2) == 46.0


def test_pullback_monitor_declared_tightens_on_hot_bar():
    out = monitor(_QQQ_CFG, _HOT_LAST, dict(_OPEN_LONG))
    # HOT-tail bar fires the above>0.80 gate ⇒ mult = min(5.0, 2.5) = 2.5 ⇒
    # ext(106) - 2.5 * atr(12) = 76.0 — a strictly TIGHTER (higher) long stop.
    assert out is not None and round(out["sl"], 2) == 76.0


def test_pullback_declared_is_strictly_not_looser():
    base = monitor(_BASE_CFG, _HOT_LAST, dict(_OPEN_LONG))["sl"]
    declared = monitor(_QQQ_CFG, _HOT_LAST, dict(_OPEN_LONG))["sl"]
    # For a long, a tighter trail can only RAISE the stop, never lower it.
    assert declared >= base
    assert declared != base  # the wiring actually engaged (not a no-op)


def test_pullback_declared_via_meta_channel():
    # run_monitor_tick can pass cfg={}; the keys threaded into the package meta
    # (order_package) must reach the monitor identically.
    pkg = {"sl": 30.0, "direction": "long", "meta": {
        "trail_mult": 5.0, "atr_period": 14,
        "trail_vol_above_pctl": 0.80, "trail_vol_tight_mult": 2.5,
        "vol_pctl_window": WIN}}
    out = monitor({}, _HOT_LAST, pkg)
    assert out is not None and round(out["sl"], 2) == 76.0
