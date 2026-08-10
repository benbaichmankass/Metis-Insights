#!/usr/bin/env python3
"""Standalone backtest CLI for the ict_scalp_5m strategy.

Reads an OHLCV CSV (timestamp, open, high, low, close, volume), walks
the frame bar-by-bar from a warm-up offset, invokes the units-layer
``order_package()`` on a rolling window, and simulates fills on the
strategy's own SL/TP using the subsequent bars. Prints a summary and
optionally writes it to JSON.

This script is the pre-live gate referenced by
``.github/workflows/ict-scalp-backtest.yml`` and
``docs/strategies/ict_scalp_5m.md``. The runtime signal builder
(``src/runtime/strategy_signal_builders.py::ict_scalp_signal_builder``)
honours the YAML ``enabled`` flag separately — running this script
does not place orders or change live behaviour.

Exit codes
----------
0  success
1  runtime error (bad data, exception during walk)
2  CLI usage error
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Ensure src/ is importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.runtime import execution_costs  # noqa: E402  (the ONE shared cost model)
import capital_efficiency  # noqa: E402  (the ONE capital-efficiency definition)
from src.units.strategies.ict_scalp import order_package  # noqa: E402
from src.units.strategies import load_strategy_config  # noqa: E402

# Execution-realism cost config (P1 § 3.B). ict_scalp previously had NO cost model
# (gross_r == net_r); these add fee + venue-aware slippage/funding through the ONE
# shared model. Module-global so the CLI can override; slippage/funding default 0.0
# → fee-only, and fee defaults to the shared round-trip constant. run_backtest never
# touches these, so a direct in-process caller (sweep/ML recorder) is unchanged; the
# CLI main() applies the mandatory venue-aware policy.
FEE_BPS_ROUNDTRIP = execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
SLIPPAGE_BPS_ROUNDTRIP = 0.0
FUNDING_BPS_PER_WINDOW = 0.0
FUNDING_WINDOW_HOURS = execution_costs.FUNDING_WINDOW_HOURS


@dataclass
class Trade:
    entry_index: int
    entry_time: Any
    direction: str
    entry: float
    sl: float
    tp: float
    risk: float
    exit_index: Optional[int] = None
    exit_time: Any = None
    exit_price: Optional[float] = None
    outcome: str = "open"           # tp_hit | sl_hit | timeout | open
    r_multiple: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0         # live order_package() confidence (blend)


def _load_candles(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Candle CSV not found: {path}")
    df = pd.read_csv(path)
    needed = {"open", "high", "low", "close"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def _load_yaml_params() -> Dict[str, Any]:
    try:
        cfg = load_strategy_config().get("ict_scalp_5m", {}) or {}
    except Exception:
        cfg = {}
    # Strip fields the unit doesn't consume.
    for k in ("enabled", "model", "signal_prefixes", "symbols", "risk_pct", "shadow_model_ids"):
        cfg.pop(k, None)
    return cfg


def _simulate_exit(
    df: pd.DataFrame,
    *,
    start_idx: int,
    direction: str,
    sl: float,
    tp: float,
    timeout_bars: int,
    entry: Optional[float] = None,
    be_offset_bps: Optional[float] = None,
    stale_exit_bars: Optional[int] = None,
    stale_exit_below_r: float = 0.0,
    giveback_min_mfe_r: float = 0.0,
    be_arm_on_touch: bool = False,
    giveback_r: float = 1.0,
    bank_frac: float = 0.0,
    bank_at_r: float = 1.0,
) -> Dict[str, Any]:
    """Walk forward from start_idx checking SL/TP hits against bar
    extremes. Assumes intra-bar SL/TP fills are at the level (no slippage).
    Returns dict with outcome, exit_index, exit_price, ``banked`` (did the
    M20 partial-TP rung fill?), and (when ``entry`` is given) the
    max-favorable / max-adverse excursion prices seen up to and including
    the exit bar (``mfe_price`` / ``mae_price``) for R:R leak diagnosis.
    MFE/MAE on the exit bar itself uses the full bar range, which slightly
    overstates excursion past the exit level — acceptable for
    distribution-level diagnosis.

    ``banked`` is reported on EVERY return path (asserted structurally by
    tests/test_ict_scalp_exit_levers.py) so the caller can read it strictly
    (``result["banked"]``) rather than via a ``.get`` default that would
    silently mis-report a missed path as "never banked".
    """
    last = min(len(df) - 1, start_idx + timeout_bars)
    best = worst = entry
    # --sim-breakeven state: mirrors the live monitor_breakeven_sl rule —
    # once a bar CLOSES ≥ 1R in favour (the monitor reads the latest close),
    # the stop moves to entry ± be_offset_bps and the outcome of a later
    # stop-out is classified `be_stop` (a scratch, not a full −1R loss).
    risk_1r = abs(entry - sl) if entry is not None else None
    cur_sl = sl
    be_armed = False
    banked = False            # M20 partial-TP rung filled?
    banked_index = None        # ...and on WHICH bar, so capital freed at the
                               # rung can be credited its shorter hold. A
                               # boolean cannot express "how long was the
                               # capital actually committed".
    for j in range(start_idx, last + 1):
        bar_low = float(df["low"].iloc[j])
        bar_high = float(df["high"].iloc[j])
        if entry is not None:
            if direction == "long":
                best = max(best, bar_high)
                worst = min(worst, bar_low)
            else:
                best = min(best, bar_low)
                worst = max(worst, bar_high)
        # M20 partial-TP bank lever (0 = off, byte-identical): bank
        # `bank_frac` of the position when price touches entry ± bank_at_r ×
        # risk. Checked BEFORE the stop so a bar that trades through the rung
        # and then stops out still credits the banked fraction at the rung —
        # the conservative reading (the remainder takes the stop). Identical
        # semantics + ordering to backtest_trend.py / backtest_pullback.py,
        # so a ladder verdict compares across harnesses.
        #
        # NOTE for this harness specifically: ict_scalp is a FIXED-bracket
        # strategy (tp_at_r, 1.5R on every live leg as of 2026-08-09), unlike
        # the trailing trend/pullback families. A rung at bank_at_r >= tp_at_r
        # is a provable no-op — the TP check below returns on the same bar and
        # `bank_frac * tp_at_r + (1 - bank_frac) * tp_at_r == tp_at_r` — so a
        # sweep grid MUST place its rungs strictly below the leg's own tp_at_r
        # or it measures nothing. See scripts/research/m27/ict_scalp_exit_sweep.py.
        if bank_frac > 0.0 and not banked and entry is not None and risk_1r:
            rung = (entry + bank_at_r * risk_1r if direction == "long"
                    else entry - bank_at_r * risk_1r)
            if (bar_high >= rung) if direction == "long" else (bar_low <= rung):
                banked = True
                banked_index = j
        if direction == "long":
            # Pessimistic ordering: if both touched in one bar, count SL first.
            if bar_low <= cur_sl:
                return {"outcome": "be_stop" if be_armed else "sl_hit",
                        "exit_index": j, "exit_price": cur_sl,
                        "mfe_price": best, "mae_price": worst, "banked": banked,
                        "banked_index": banked_index}
            if bar_high >= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp,
                        "mfe_price": best, "mae_price": worst, "banked": banked,
                        "banked_index": banked_index}
        else:
            if bar_high >= cur_sl:
                return {"outcome": "be_stop" if be_armed else "sl_hit",
                        "exit_index": j, "exit_price": cur_sl,
                        "mfe_price": best, "mae_price": worst, "banked": banked,
                        "banked_index": banked_index}
            if bar_low <= tp:
                return {"outcome": "tp_hit", "exit_index": j, "exit_price": tp,
                        "mfe_price": best, "mae_price": worst, "banked": banked,
                        "banked_index": banked_index}
        # M20 exit levers (giveback / stale) — fire at bar CLOSE, and only when
        # neither the stop NOR the TP hit this bar (the SL/TP checks above return
        # first, so stop-first ordering is preserved exactly). Mirrors the
        # backtest_pullback.py lever semantics; both default off, so with no
        # lever configured the baseline is byte-for-byte unchanged.
        if entry is not None and risk_1r and risk_1r > 0 and (
                giveback_min_mfe_r > 0.0 or stale_exit_bars is not None):
            lever_close = float(df["close"].iloc[j])
            open_r = ((lever_close - entry) / risk_1r if direction == "long"
                      else (entry - lever_close) / risk_1r)
            mfe_r = ((best - entry) / risk_1r if direction == "long"
                     else (entry - best) / risk_1r)
            # giveback-stop: once peak open profit >= min_mfe R, exit when
            # >= giveback_r R has been surrendered from that peak.
            if (giveback_min_mfe_r > 0.0 and mfe_r >= giveback_min_mfe_r
                    and (mfe_r - open_r) >= giveback_r):
                return {"outcome": "giveback_stop", "exit_index": j,
                        "exit_price": lever_close,
                        "mfe_price": best, "mae_price": worst, "banked": banked,
                        "banked_index": banked_index}
            # stale-stop: cut a trade held >= N bars that is still below
            # stale_exit_below_r of open profit (default 0.0 = only cut losers).
            if (stale_exit_bars is not None and (j - start_idx) >= stale_exit_bars
                    and open_r < stale_exit_below_r):
                return {"outcome": "stale_stop", "exit_index": j,
                        "exit_price": lever_close,
                        "mfe_price": best, "mae_price": worst, "banked": banked,
                        "banked_index": banked_index}
        if (be_offset_bps is not None and not be_armed
                and entry is not None and risk_1r and risk_1r > 0):
            # ARMING BASIS — close vs TOUCH (BL-20260810 intrabar-monitor work).
            #
            # The live monitor reads `candles_df["close"].iloc[-1]`, so the
            # ratchet arms on a bar CLOSE >= 1R. But MFE is the intrabar HIGH,
            # and the 2026-08-10 census found 24 scalp trades (1.01% of 2,385
            # losers) that reached >= 90% of a 1.5R target and still closed at
            # -1R: price touched near-target, closed back below 1R, and the
            # ratchet never armed. `--be-arm-on-touch` models arming on the
            # intrabar EXTREME instead.
            #
            # CONSERVATIVE BY CONSTRUCTION, and the understatement is
            # deliberate: this block runs AFTER the SL/TP checks have already
            # returned for bar j, so (a) a bar that trades through the stop
            # takes the stop and never arms — SL-first is preserved exactly —
            # and (b) the armed stop only protects from bar j+1 onward, whereas
            # a real intrabar monitor could arm mid-bar. So a positive result
            # here is a FLOOR on the live benefit, not a ceiling. Modelling
            # mid-bar arming would need sub-bar data this harness does not have,
            # and inventing it would be the fabrication class the provenance
            # rules exist to stop.
            if be_arm_on_touch:
                trigger = bar_high if direction == "long" else bar_low
            else:
                trigger = float(df["close"].iloc[j])
            if direction == "long" and trigger >= entry + risk_1r:
                cur_sl = entry * (1 + be_offset_bps / 10000.0)
                be_armed = True
            elif direction == "short" and trigger <= entry - risk_1r:
                cur_sl = entry * (1 - be_offset_bps / 10000.0)
                be_armed = True
    # Timeout: close at the last bar's close.
    return {
        "outcome": "timeout",
        "exit_index": last,
        "exit_price": float(df["close"].iloc[last]),
        "mfe_price": best,
        "mae_price": worst,
        "banked": banked,
        "banked_index": banked_index,
    }


def _build_htf_series(
    df: pd.DataFrame,
    *,
    htf_rule: str,
    ema_period: int,
) -> Optional[pd.DataFrame]:
    """Resample the 5m OHLCV feed to ``htf_rule`` and return a per-row
    DataFrame containing (timestamp, htf_close, htf_ema) aligned to the
    HTF bar. Caller forward-fills onto the 5m index.

    v2 backtest CLI: lets the strategy's HTF bias filter run without a
    second data feed. Returns None when the frame doesn't have a
    ``timestamp`` column or pandas resample fails.
    """
    if "timestamp" not in df.columns:
        return None
    try:
        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        if ts.isna().any():
            return None
        tmp = df.copy()
        tmp["timestamp"] = ts
        tmp = tmp.set_index("timestamp")
        agg = tmp.resample(htf_rule).agg({"close": "last"}).dropna()
        if len(agg) < ema_period + 1:
            return None
        agg["htf_ema"] = agg["close"].ewm(span=ema_period, adjust=False).mean()
        agg = agg.rename(columns={"close": "htf_close"}).reset_index()
        return agg
    except Exception:
        return None


def _stamp_decision_time_regime(
    meta: Dict[str, Any],
    window_200: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    vol_spec: Optional[Dict[str, Any]],
) -> None:
    """Mirror the live builder's ``_stamp_regime_on_meta`` at backtest time.

    Uses the SAME pure functions the live path stamps decision-time regime
    with (``detect_regime`` ADX-14 trend axis; ``vol_regime_from_spec``
    against a frozen registry spec) over the same 200-bar window the live
    builder fetches — so backtest per-cell attribution is parity-honest.
    ``vol_spec`` is the frozen ``{vol_bucket_edges, vol_bucket_labels,
    vol_window_n}`` dict extracted from the registry head that serves the
    live ``(symbol, timeframe)`` stamp (never recomputed from backtest
    data, which would not be parity). Missing spec → ``unknown``.
    Best-effort: any failure leaves the meta unstamped.
    """
    try:
        from src.runtime.regime import detect_regime
        from src.runtime.regime.vol_detector import vol_regime_from_spec

        rg = detect_regime(window_200)
        meta.setdefault("regime", rg.get("regime"))
        meta.setdefault("adx_14", rg.get("adx"))
        meta.setdefault("regime_source", rg.get("source"))
        if vol_spec:
            closes = [float(c) for c in window_200["close"].tolist()]
            vol_regime, vol = vol_regime_from_spec(vol_spec, closes)
            meta.setdefault("vol_regime", vol_regime)
            meta.setdefault("rolling_log_return_vol",
                            round(vol, 8) if vol is not None else None)
            meta.setdefault(
                "vol_regime_source",
                f"vol-bucket-edges:{vol_spec.get('model_id') or 'frozen-spec'}",
            )
        else:
            meta.setdefault("vol_regime", "unknown")
    except Exception:
        pass


def run_backtest(
    df: pd.DataFrame,
    *,
    cfg_overrides: Dict[str, Any],
    timeframe: str,
    symbol: str,
    warmup_bars: int,
    timeout_bars: int,
    cooldown_bars: int,
    htf_rule: str = "1h",
    htf_ema_period: int = 20,
    min_confidence: float = 0.0,
    _collect_trades: bool = False,
    return_trades: bool = False,
    emit_path: Optional[str] = None,
    vol_spec: Optional[Dict[str, Any]] = None,
    stamp_regime: bool = False,
    sim_breakeven: bool = False,
    be_arm_on_touch: bool = False,
    stale_exit_bars: Optional[int] = None,
    stale_exit_below_r: float = 0.0,
    giveback_min_mfe_r: float = 0.0,
    giveback_r: float = 1.0,
    bank_frac: float = 0.0,
    bank_at_r: float = 1.0,
) -> Dict[str, Any]:
    cfg = {"symbol": symbol, "timeframe": timeframe, **cfg_overrides}
    htf_df = _build_htf_series(df, htf_rule=htf_rule, ema_period=htf_ema_period)
    # Align the HTF series onto the base index ONCE via merge_asof (most
    # recent HTF bar at-or-before each base bar) instead of a per-bar linear
    # scan — the scan was O(n * htf_rows), which is hours on a 6yr 5m feed.
    htf_close_arr = htf_ema_arr = None
    if htf_df is not None and "timestamp" in df.columns and len(htf_df):
        aligned = pd.merge_asof(
            df[["timestamp"]].reset_index(drop=True),
            htf_df[["timestamp", "htf_close", "htf_ema"]],
            on="timestamp", direction="backward",
        )
        htf_close_arr = aligned["htf_close"].to_numpy()
        htf_ema_arr = aligned["htf_ema"].to_numpy()
    trades: List[Trade] = []
    n = len(df)
    if n < warmup_bars + 5:
        raise ValueError(
            f"Not enough candles: have {n}, need at least {warmup_bars + 5}"
        )

    # Slide a fixed-size window instead of growing prefix. The strategy
    # only needs max(swing_lookback, sweep_lookback, atr_period) bars
    # of history, so a 80-bar window is sufficient at defaults. This
    # makes the backtest O(n * window) instead of O(n^2), which is the
    # difference between minutes and hours on a 26k-bar feed.
    window_size = max(
        int(cfg.get("swing_lookback_bars", 20)),
        int(cfg.get("sweep_lookback_bars", 12)),
        int(cfg.get("atr_period", 14)),
    ) + 10
    window_size = max(window_size, warmup_bars)

    next_eligible_idx = warmup_bars
    for i in range(warmup_bars, n - 1):
        if i < next_eligible_idx:
            continue
        lo = max(0, i + 1 - window_size)
        window = df.iloc[lo : i + 1]
        # v2 HTF bias: read the pre-aligned htf_close + htf_ema for this bar
        # (O(1)); the strategy reads them from cfg, so copy + augment.
        per_bar_cfg = dict(cfg)
        if htf_close_arr is not None:
            hc = htf_close_arr[i]
            he = htf_ema_arr[i]
            if hc == hc and he == he:  # not NaN
                per_bar_cfg["htf_close"] = float(hc)
                per_bar_cfg["htf_ema"] = float(he)
        try:
            pkg = order_package(per_bar_cfg, candles_df=window)
        except ValueError:
            continue
        direction = pkg["direction"]
        # Confidence is computed by the live order_package() itself, so this
        # gate is an exact mirror of a live min_confidence floor.
        confidence = float(pkg.get("confidence") or 0.0)
        if confidence < min_confidence:
            continue
        entry = float(pkg["entry"])
        sl = float(pkg["sl"])
        tp = float(pkg["tp"])
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        # Fill simulation starts on the next bar.
        result = _simulate_exit(
            df,
            start_idx=i + 1,
            direction=direction,
            sl=sl,
            tp=tp,
            timeout_bars=timeout_bars,
            entry=entry,
            be_arm_on_touch=be_arm_on_touch,
            be_offset_bps=(
                float(cfg.get("be_offset_bps", 0.0) or 0.0)
                if sim_breakeven else None
            ),
            stale_exit_bars=stale_exit_bars,
            stale_exit_below_r=stale_exit_below_r,
            giveback_min_mfe_r=giveback_min_mfe_r,
            giveback_r=giveback_r,
            bank_frac=bank_frac,
            bank_at_r=bank_at_r,
        )
        exit_price = float(result["exit_price"])
        if direction == "long":
            r = (exit_price - entry) / risk
        else:
            r = (entry - exit_price) / risk
        # M20 bank lever: a filled rung realises `bank_frac` of the position at
        # +bank_at_r; the remainder realises the exit R. No-op when bank_frac is
        # 0 (`banked` can only be True when bank_frac > 0). Read STRICTLY — a
        # missing key must raise, not silently read as "never banked".
        banked = bool(result["banked"])
        if banked:
            r = bank_frac * bank_at_r + (1.0 - bank_frac) * r
        # Fee note: banking splits ONE exit into two partial exits whose sizes
        # sum to the original position, so round-trip notional — and therefore
        # the bps cost model in _cost_breakdown — is unchanged to first order.
        ts = df["timestamp"].iloc[i] if "timestamp" in df.columns else i
        exit_ts = (
            df["timestamp"].iloc[result["exit_index"]] if "timestamp" in df.columns else result["exit_index"]
        )
        meta = dict(pkg.get("meta") or {})
        if stamp_regime:
            # Decision-time stamp over the live builder's 200-bar fetch window.
            lo200 = max(0, i + 1 - 200)
            _stamp_decision_time_regime(
                meta, df.iloc[lo200 : i + 1],
                symbol=symbol, timeframe=timeframe, vol_spec=vol_spec,
            )
        mfe_price = result.get("mfe_price")
        mae_price = result.get("mae_price")
        if mfe_price is not None and mae_price is not None:
            sign = 1.0 if direction == "long" else -1.0
            meta["mfe_r"] = round(sign * (float(mfe_price) - entry) / risk, 4)
            meta["mae_r"] = round(sign * (float(mae_price) - entry) / risk, 4)
        bars_held = int(result["exit_index"]) - i
        meta["bars_held"] = bars_held
        # CAPITAL-WEIGHTED hold — the axis a net_R-only gate cannot see.
        # Banking does not close the trade; it releases `bank_frac` of the
        # position at the rung while the remainder rides to the exit. So the
        # capital actually committed is the size-weighted average of the two
        # holds, NOT the full-position hold. Measuring banking on unweighted
        # bars_held would score it identically to doing nothing, which is
        # exactly the blind spot the operator flagged (capital sitting in a
        # chop with nothing to show for it).
        bi = result.get("banked_index")
        if banked and bi is not None:
            cap_bars = (bank_frac * (int(bi) - i)
                        + (1.0 - bank_frac) * bars_held)
        else:
            cap_bars = float(bars_held)
        meta["capital_bars"] = round(cap_bars, 3)
        if bank_frac > 0.0:
            # Rung-fill rate is the denominator a ladder verdict is read over:
            # a cell where almost nothing banks is inert, not a fair negative.
            meta["banked"] = banked
        meta["exit_time"] = str(exit_ts)
        meta["exit_price"] = exit_price
        meta["tp"] = tp
        trades.append(
            Trade(
                entry_index=i,
                entry_time=ts,
                direction=direction,
                entry=entry,
                sl=sl,
                tp=tp,
                risk=risk,
                exit_index=int(result["exit_index"]),
                exit_time=exit_ts,
                exit_price=exit_price,
                outcome=str(result["outcome"]),
                r_multiple=round(float(r), 4),
                meta=meta,
                confidence=round(confidence, 4),
            )
        )
        next_eligible_idx = int(result["exit_index"]) + 1 + cooldown_bars

    # Per-trade JSONL (one object per closed trade) for the ML backtest-label
    # recorder — same schema squeeze/fade emit, consumed by
    # scripts/ml/record_harness_trades.py (S-MLOPT-S6-FU-2). `net_r` is now
    # net-of-full-cost (fee+slippage+funding, § 3.B) — the primary arm — with
    # `net_r_fee_only` alongside for the with/without comparison. `gross_r` stays
    # the pre-cost r_multiple. Slippage/funding are 0 unless the CLI sets them, so
    # a default in-process caller keeps net_r == gross_r (byte-identical).
    if emit_path:
        Path(emit_path).parent.mkdir(parents=True, exist_ok=True)
        with open(emit_path, "w", encoding="utf-8") as fh:
            for t in trades:
                cb = _cost_breakdown(t)
                fh.write(json.dumps({
                    "strategy": "ict_scalp_5m", "entry_time": str(t.entry_time),
                    "direction": t.direction, "gross_r": t.r_multiple,
                    "net_r": round(t.r_multiple - cb["total_cost_r"], 4),
                    "net_r_fee_only": round(t.r_multiple - cb["fee_r"], 4),
                    "cost_fee_r": round(cb["fee_r"], 5),
                    "cost_slippage_r": round(cb["slippage_r"], 5),
                    "cost_funding_r": round(cb["funding_r"], 5),
                    "cost_total_r": round(cb["total_cost_r"], 5),
                    "funding_windows": round(cb["funding_windows"], 3),
                    "entry": t.entry, "sl": t.sl, "risk": t.risk,
                    "outcome": t.outcome,
                    "confidence": t.confidence,
                    # The LIVE order_package meta (sweep_level/sweep_extreme/
                    # displacement_body_to_range/fvg_low/fvg_high/fvg_size/
                    # mitigation_mode/atr + stamped regime/adx_14/vol_regime) —
                    # carried verbatim so the signal-research component-edge
                    # report can attribute entry-component edge over backtest
                    # volume (component_edge_report.py --backtest-log).
                    "meta": t.meta}, default=str) + "\n")

    summary = _summarize(trades, df, timeframe=timeframe, symbol=symbol,
                         bank_frac=bank_frac, bank_at_r=bank_at_r)
    if _collect_trades:
        # (confidence, r_multiple) per trade — lets the sweep filter by
        # threshold without re-walking the (expensive) 5m frame N times.
        summary["_trades"] = [(t.confidence, t.r_multiple) for t in trades]
    if return_trades:
        # Full Trade objects (with entry_index/exit_index/meta/confidence) for an
        # in-process consumer — the M30 backtest-panel bridge
        # (scripts/research/build_backtest_panel.py) slices the caller's own df by
        # entry_index:exit_index to compute native MFE/MAE excursions and extracts
        # the decision-time feature vector from meta. Additive + default-off: every
        # existing caller (CLI, sweep, ML recorder) is byte-for-byte unchanged.
        summary["_trades_full"] = list(trades)
    return summary


def _cost_breakdown(t: Trade) -> Dict[str, float]:
    """Per-trade round-trip cost in R via the ONE shared execution-realism model
    (fee + slippage + funding). Funding counts the 8h perp windows the hold crossed
    (t.entry_time → t.exit_time). Slippage/funding are 0.0 by default → fee-only,
    byte-identical to the legacy no-cost gross_r when everything is 0."""
    if not t.exit_price or t.risk <= 0:
        return {"fee_r": 0.0, "slippage_r": 0.0, "funding_r": 0.0,
                "total_cost_r": 0.0, "funding_windows": 0.0}
    return execution_costs.roundtrip_cost_r(
        entry=t.entry, exit_price=t.exit_price, risk=t.risk,
        entry_time=t.entry_time, exit_time=t.exit_time,
        fee_bps_roundtrip=FEE_BPS_ROUNDTRIP,
        slippage_bps_roundtrip=SLIPPAGE_BPS_ROUNDTRIP,
        funding_bps_per_window=FUNDING_BPS_PER_WINDOW,
        funding_window_hours=FUNDING_WINDOW_HOURS,
    )


def _fee_only_r(t: Trade) -> float:
    """Round-trip FEE-only cost in R (slippage/funding excluded) — the comparison arm."""
    return _cost_breakdown(t)["fee_r"]


def _summarize(
    trades: List[Trade],
    df: pd.DataFrame,
    *,
    timeframe: str,
    symbol: str,
    bank_frac: float = 0.0,
    bank_at_r: float = 1.0,
) -> Dict[str, Any]:
    n = len(trades)
    # Execution-realism cost config in effect for this run (P1 § 3.B) — echoed on
    # every summary so a reader always knows which cost policy produced net_r.
    cost_cfg = {
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "slippage_bps_roundtrip": SLIPPAGE_BPS_ROUNDTRIP,
        "funding_bps_per_window": FUNDING_BPS_PER_WINDOW,
    }
    # M20 partial-TP ladder settings in effect, echoed on every summary for the
    # same reason cost_cfg is: a sweep cell's JSON must state WHICH lever
    # produced its r_multiple, never leave the reader to infer it from a tag.
    # `banked_trades` is the rung-fill DENOMINATOR — a cell whose rung almost
    # never fills is INERT, which is a different finding from a lever that
    # filled often and lost money, and the two must not read alike.
    # ---- CAPITAL EFFICIENCY (operator directive 2026-08-10) ----------------
    # The exit-refinement skill has ALWAYS declared "capital-efficiency
    # tiebreak: net_R per position-day" as part of the lever gate — and no
    # harness ever computed it, so the gate's own declared tiebreak has never
    # been measurable. A net_R-only view cannot distinguish a book that earns
    # 10R in a week from one that earns 10R while sitting in a chop for a
    # month, which is precisely the live complaint (bybit_2 holding through a
    # long chop with nothing to show for it).
    #
    # Bar length is MEASURED from the frame's own timestamps (median spacing),
    # not assumed from the timeframe label — a mislabelled or resampled frame
    # would otherwise silently scale every number here.
    # DEFINITION lives in scripts/capital_efficiency.py — the one home, so a
    # cross-harness comparison means something. This harness owns only the
    # EXTRACTION (its Trade carries a meta dict; backtest_pullback's does not).
    bar_minutes = capital_efficiency.bar_minutes_from_frame(df)
    position_bars = sum(float(t.meta.get("bars_held") or 0) for t in trades)
    # capital_bars is SIZE-WEIGHTED: banking releases bank_frac of the
    # position at the rung, so that fraction stops consuming capital there.
    # Falls back to bars_held for any trade without the field (lever off).
    capital_bars = sum(float(t.meta.get("capital_bars",
                                        t.meta.get("bars_held") or 0))
                       for t in trades)

    banked_n = sum(1 for t in trades if t.meta.get("banked"))
    lever_cfg = {
        "bank_frac": bank_frac,
        "bank_at_r": bank_at_r,
        "banked_trades": banked_n,
        "banked_pct": (round(100.0 * banked_n / len(trades), 2)
                       if trades else 0.0),
    }
    if n == 0:
        return {
            "strategy": "ict_scalp_5m",
            "symbol": symbol,
            "timeframe": timeframe,
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "expectancy_r": 0.0,
            "total_r": 0.0,
            "net_total_r": 0.0,
            "net_expectancy_r": 0.0,
            "net_total_r_fee_only": 0.0,
            "net_expectancy_r_fee_only": 0.0,
            "max_drawdown_r": 0.0,
            "sharpe_r": 0.0,
            "by_outcome": {},
            **capital_efficiency.empty(),
            **cost_cfg,
            **lever_cfg,
            "data_start": str(df["timestamp"].iloc[0]) if "timestamp" in df.columns and len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns and len(df) else None,
            "bars": int(len(df)),
            "run_date": str(date.today()),
        }
    rs = [t.r_multiple for t in trades]
    costs = [_cost_breakdown(t) for t in trades]
    net = [t.r_multiple - c["total_cost_r"] for t, c in zip(trades, costs)]
    # Fee-only arm — always computed so every run carries the with/without comparison.
    net_fee_only = [t.r_multiple - c["fee_r"] for t, c in zip(trades, costs)]
    mean_cost_r = {k: round(sum(c[k] for c in costs) / n, 5)
                   for k in ("fee_r", "slippage_r", "funding_r",
                             "total_cost_r", "funding_windows")}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    by_outcome: Dict[str, int] = {}
    for t in trades:
        by_outcome[t.outcome] = by_outcome.get(t.outcome, 0) + 1
    # Drawdown on the NET (net-of-full-cost) equity curve — the honest one.
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in net:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    mean = sum(rs) / n
    stdev = statistics.pstdev(rs) if n > 1 else 0.0
    sharpe = (mean / stdev) if stdev > 0 else 0.0
    return {
        "strategy": "ict_scalp_5m",
        "symbol": symbol,
        "timeframe": timeframe,
        "total_trades": int(n),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "win_rate_pct": round(100.0 * len(wins) / n, 2),
        "expectancy_r": round(mean, 4),
        "total_r": round(sum(rs), 4),
        # Net-of-full-cost (fee+slippage+funding) — the primary arm — plus the
        # fee-only comparison, so the execution-realism delta shows on every run.
        "net_total_r": round(sum(net), 4),
        "net_expectancy_r": round(sum(net) / n, 4),
        "net_total_r_fee_only": round(sum(net_fee_only), 4),
        "net_expectancy_r_fee_only": round(sum(net_fee_only) / n, 4),
        "mean_cost_r": mean_cost_r,
        "max_drawdown_r": round(max_dd, 4),
        "sharpe_r": round(sharpe, 4),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "by_outcome": by_outcome,
        # Capital efficiency — read BESIDE net_R, not instead of it. A lever
        # that lifts net_r_per_capital_day while costing net_R is buying
        # throughput; one that lifts neither is just smaller.
        **capital_efficiency.summarize(
            bar_minutes=bar_minutes, position_bars=position_bars,
            capital_bars=capital_bars, net_total_r=sum(net),
            n_trades=n),
        **cost_cfg,
        **lever_cfg,
        "data_start": str(df["timestamp"].iloc[0]) if "timestamp" in df.columns else None,
        "data_end": str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else None,
        "bars": int(len(df)),
        "run_date": str(date.today()),
    }


def _format_text(summary: Dict[str, Any]) -> str:
    lines = [
        f"ict_scalp_5m backtest — {summary['symbol']} {summary['timeframe']}",
        f"  data: {summary.get('data_start')} → {summary.get('data_end')} ({summary['bars']} bars)",
        f"  total_trades   : {summary['total_trades']}",
    ]
    if summary["total_trades"]:
        lines.extend([
            f"  win_rate_pct   : {summary['win_rate_pct']}%",
            f"  expectancy_r   : {summary['expectancy_r']}",
            f"  total_r (gross): {summary['total_r']}",
            f"  net_total_r    : {summary.get('net_total_r')} "
            f"(exp {summary.get('net_expectancy_r')}) "
            f"[net-of-full-cost: fee+slippage+funding]",
            f"  net_r_fee_only : {summary.get('net_total_r_fee_only')} "
            f"(exp {summary.get('net_expectancy_r_fee_only')}) "
            f"[fee-only comparison arm]",
            f"  cost           : fee={summary.get('fee_bps_roundtrip')}bps "
            f"slip={summary.get('slippage_bps_roundtrip')}bps "
            f"fund={summary.get('funding_bps_per_window')}bps/8h "
            f"mean_cost_r={summary.get('mean_cost_r')}",
            f"  max_drawdown_r : {summary['max_drawdown_r']} [on net curve]",
            f"  sharpe_r       : {summary['sharpe_r']}",
            f"  avg_win_r      : {summary['avg_win_r']}",
            f"  avg_loss_r     : {summary['avg_loss_r']}",
            f"  by_outcome     : {summary['by_outcome']}",
        ])
    return "\n".join(lines)


def _parse_grid(spec: str) -> List[float]:
    spec = spec.strip()
    if ":" in spec:
        lo, hi, step = (float(x) for x in spec.split(":"))
        out, v = [], lo
        while v <= hi + 1e-9:
            out.append(round(v, 6))
            v += step
        return out
    return [float(x) for x in spec.split(",") if x.strip() != ""]


def _metrics_for(thr: float, pairs: List[tuple]) -> Dict[str, Any]:
    """Compute headline metrics for the subset of (confidence, r) trades at
    or above the threshold."""
    sub = [r for c, r in pairs if c >= thr]
    n = len(sub)
    if n == 0:
        return {"min_confidence": thr, "trades": 0, "win_rate_pct": 0.0,
                "total_r": 0.0, "expectancy_r": 0.0, "max_drawdown_r": 0.0}
    wins = sum(1 for r in sub if r > 0)
    cum = peak = mdd = 0.0
    for r in sub:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {"min_confidence": thr, "trades": n,
            "win_rate_pct": round(100.0 * wins / n, 2),
            "total_r": round(sum(sub), 4),
            "expectancy_r": round(sum(sub) / n, 4),
            "max_drawdown_r": round(mdd, 4)}


def _confidence_sweep(df: pd.DataFrame, grid: List[float], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    # Walk the frame ONCE at min_confidence=0 (the 5m walk is the expensive
    # part — order_package() per bar), then filter the resulting trades by
    # each threshold. NOTE: this is a post-hoc filter, so it does not model
    # the cooldown re-entries that gating at entry would free up — a small,
    # conservative approximation adequate for threshold selection.
    full = run_backtest(df, min_confidence=0.0, _collect_trades=True, **kwargs)
    pairs = full.get("_trades", [])
    rows = [_metrics_for(thr, pairs) for thr in grid]
    best = max(rows, key=lambda r: r["total_r"]) if rows else None
    best_exp = max((r for r in rows if r["trades"] >= 20),
                   key=lambda r: r["expectancy_r"], default=None)
    return {"strategy": "ict_scalp_5m", "symbol": kwargs.get("symbol"),
            "timeframe": kwargs.get("timeframe"),
            "data_start": str(df["timestamp"].iloc[0]) if "timestamp" in df.columns and len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns and len(df) else None,
            "baseline_trades": full.get("total_trades", 0),
            "grid": rows, "best_by_total_r": best,
            "best_by_expectancy_r_min20": best_exp,
            "note": "post-hoc confidence filter on a single walk; ignores cooldown re-entries"}


def _fmt_sweep(sw: Dict[str, Any]) -> str:
    lines = [f"ict_scalp_5m confidence sweep — {sw['symbol']} {sw['timeframe']} "
             f"({sw['data_start']} -> {sw['data_end']})",
             f"  {'min_conf':>8} {'trades':>7} {'WR%':>6} {'total_R':>9} {'exp_R':>8} {'maxDD_R':>8}"]
    for r in sw["grid"]:
        lines.append(f"  {r['min_confidence']:>8.3f} {r['trades']:>7d} {r['win_rate_pct']:>6.1f} "
                     f"{r['total_r']:>9.2f} {r['expectancy_r']:>8.3f} {r['max_drawdown_r']:>8.2f}")
    b = sw.get("best_by_total_r")
    if b:
        lines.append(f"  -> best total_R @ min_confidence={b['min_confidence']} "
                     f"(total_R={b['total_r']}, trades={b['trades']})")
    be = sw.get("best_by_expectancy_r_min20")
    if be:
        lines.append(f"  -> best expectancy_R (>=20 trades) @ min_confidence={be['min_confidence']} "
                     f"(exp={be['expectancy_r']}, trades={be['trades']})")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP, SLIPPAGE_BPS_ROUNDTRIP, FUNDING_BPS_PER_WINDOW, FUNDING_WINDOW_HOURS
    p = argparse.ArgumentParser(
        description="Backtest ict_scalp_5m (net-of-cost: fee+slippage+funding).")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"),
                   help="OHLCV CSV path (default: $BACKTEST_DATA_PATH or data/backtest_candles.csv).")
    p.add_argument("--timeframe", default="5m", help="Strategy timeframe label (default: 5m).")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--warmup-bars", type=int, default=50,
                   help="Skip the first N bars to give lookback windows room (default: 50).")
    p.add_argument("--timeout-bars", type=int, default=24,
                   help="Force-close a trade after N bars if neither SL nor TP hits (default: 24 → 2h on 5m).")
    p.add_argument("--cooldown-bars", type=int, default=3,
                   help="Skip N bars after each exit before re-evaluating (default: 3).")
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP,
                   help="Round-trip fee in bps of notional (default: the shared "
                        "execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP).")
    p.add_argument("--slippage-bps-roundtrip", type=float, default=None,
                   help="Execution-realism (P1 § 3.B): round-trip slippage in bps of "
                        "notional (half-spread + impact, both sides). DEFAULT (unset) = "
                        "the venue-aware default (execution_costs.slippage_bps_roundtrip_for, "
                        "~5 bps). Pass 0 for the fee-only comparison arm.")
    p.add_argument("--funding-bps-per-window", type=float, default=None,
                   help="Execution-realism (P1 § 3.B): perp funding magnitude in bps of "
                        "notional per 8h window; the hold is charged for every window it "
                        "crosses. DEFAULT (unset) = the VENUE-AWARE default "
                        "(execution_costs.funding_bps_per_window_for): ~1 bps/8h for a "
                        "crypto PERP, 0 for futures/equity/fx (they pay no perp funding). "
                        "Pass 0 for the fee-only arm. Directionless drag at P1.")
    p.add_argument("--funding-window-hours", type=float, default=FUNDING_WINDOW_HOURS,
                   help="Perp funding window length in hours (default 8.0).")
    p.add_argument("--htf-rule", default="1h",
                   help="HTF resample rule for the bias filter (default: 1h).")
    p.add_argument("--htf-ema-period", type=int, default=20,
                   help="HTF EMA period for the bias filter (default: 20).")
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write summary to this JSON file. '-' means stdout.")
    p.add_argument("--ignore-yaml", action="store_true",
                   help="Ignore config/strategies.yaml; use unit defaults only.")
    p.add_argument("--min-confidence", type=float, default=0.0,
                   help="Skip entries whose live order_package() confidence is below this.")
    p.add_argument("--confidence-sweep", default=None, metavar="GRID",
                   help="Sweep min_confidence over GRID ('0:0.6:0.05' or '0,0.1,0.2') and tabulate.")
    p.add_argument("--emit-trades", default=None, metavar="PATH",
                   help="Write one per-trade JSONL object per closed trade to PATH "
                        "(for the ML backtest-label recorder; single-run only, not with --confidence-sweep).")
    p.add_argument("--stamp-regime", action="store_true",
                   help="Stamp decision-time regime (ADX-14 trend + frozen-spec vol) onto each "
                        "trade's meta, mirroring the live builder's _stamp_regime_on_meta over "
                        "a 200-bar window (Phase-0 per-cell attribution).")
    p.add_argument("--vol-spec-json", default=None, metavar="PATH",
                   help="Frozen vol spec JSON ({vol_bucket_edges, vol_bucket_labels, vol_window_n, "
                        "model_id}) extracted from the registry head serving the live vol stamp. "
                        "Without it --stamp-regime leaves vol_regime=unknown (never recomputed "
                        "from backtest data — that would not be parity).")
    p.add_argument("--be-arm-on-touch", action="store_true",
                   help="Arm the break-even ratchet on an intrabar TOUCH of 1R "
                        "(bar high/low) instead of a bar CLOSE >= 1R. Models the "
                        "intrabar monitor; only meaningful with --sim-breakeven. "
                        "Conservative: the armed stop protects from the NEXT bar, "
                        "and SL-first ordering is preserved, so a positive result "
                        "is a FLOOR on the live benefit (BL-20260810).")
    p.add_argument("--sim-breakeven", action="store_true",
                   help="Simulate the LIVE monitor's break-even trail (monitor_breakeven_sl: once a "
                        "bar closes >= 1R in favour, SL moves to entry +/- be_offset_bps from YAML). "
                        "The legacy harness (and the -467R demotion baseline) did NOT model this — "
                        "static SL/TP + timeout only.")
    # M20 exit levers (mirror scripts/backtest_pullback.py; both default off).
    p.add_argument("--stale-exit-bars", type=int, default=None, metavar="N",
                   help="M20 stale-stop lever: cut a trade held >= N bars whose open "
                        "R is below --stale-exit-below-r (None=off, legacy behaviour).")
    p.add_argument("--stale-exit-below-r", type=float, default=0.0, metavar="R",
                   help="Threshold R for --stale-exit-bars (default 0.0 = only cut "
                        "trades still under water at N bars).")
    p.add_argument("--giveback-min-mfe-r", type=float, default=0.0, metavar="R",
                   help="M20 giveback-stop lever: arm once peak open profit reaches "
                        "this many R (0.0=off).")
    p.add_argument("--bank-frac", type=float, default=0.0, metavar="F",
                   help="M20 partial-TP ladder lever: fraction of the position "
                        "banked at +--bank-at-r R (0=off, legacy behaviour). "
                        "Mirrors backtest_trend.py / backtest_pullback.py.")
    p.add_argument("--bank-at-r", type=float, default=1.0, metavar="R",
                   help="Rung for --bank-frac, in R (default 1.0). MUST be "
                        "strictly below the leg's tp_at_r (1.5R on every live "
                        "ict_scalp leg) — at or above it the rung coincides "
                        "with the fixed TP and the lever is a provable no-op.")
    p.add_argument("--giveback-r", type=float, default=1.0, metavar="R",
                   help="Once armed, exit when >= this many R has been surrendered "
                        "from the peak (default 1.0).")
    args = p.parse_args(argv[1:])
    # Mandatory venue-aware cost policy (operator directive 2026-08-04): a faithful
    # backtest is net-of-real-cost by default. Unset flags resolve to the venue-aware
    # defaults (funding is perp-only → 0 for a non-perp, never a fabricated cost); an
    # explicit value (incl. 0 for the fee-only comparison arm) always wins.
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip
    SLIPPAGE_BPS_ROUNDTRIP = (
        execution_costs.slippage_bps_roundtrip_for(args.symbol)
        if args.slippage_bps_roundtrip is None else args.slippage_bps_roundtrip)
    FUNDING_BPS_PER_WINDOW = (
        execution_costs.funding_bps_per_window_for(args.symbol)
        if args.funding_bps_per_window is None else args.funding_bps_per_window)
    FUNDING_WINDOW_HOURS = args.funding_window_hours

    try:
        df = _load_candles(args.data)
    except Exception as exc:
        print(f"ERROR: failed to load candles from {args.data}: {exc}", file=sys.stderr)
        return 1

    cfg_overrides = {} if args.ignore_yaml else _load_yaml_params()
    vol_spec = None
    if args.vol_spec_json:
        try:
            vol_spec = json.loads(Path(args.vol_spec_json).read_text())
        except Exception as exc:
            print(f"WARNING: could not read --vol-spec-json {args.vol_spec_json}: {exc}; "
                  "vol_regime will stamp as unknown", file=sys.stderr)
    bt_kwargs = dict(
        cfg_overrides=cfg_overrides,
        timeframe=args.timeframe,
        symbol=args.symbol,
        warmup_bars=int(args.warmup_bars),
        timeout_bars=int(args.timeout_bars),
        cooldown_bars=int(args.cooldown_bars),
        htf_rule=str(args.htf_rule),
        htf_ema_period=int(args.htf_ema_period),
        vol_spec=vol_spec,
        stamp_regime=bool(args.stamp_regime),
        sim_breakeven=bool(args.sim_breakeven),
        be_arm_on_touch=bool(args.be_arm_on_touch),
        stale_exit_bars=(int(args.stale_exit_bars)
                         if args.stale_exit_bars is not None else None),
        stale_exit_below_r=float(args.stale_exit_below_r),
        giveback_min_mfe_r=float(args.giveback_min_mfe_r),
        giveback_r=float(args.giveback_r),
        bank_frac=float(args.bank_frac),
        bank_at_r=float(args.bank_at_r),
    )

    try:
        if args.confidence_sweep:
            summary = _confidence_sweep(df, _parse_grid(args.confidence_sweep), bt_kwargs)
            print(_fmt_sweep(summary))
        else:
            summary = run_backtest(df, min_confidence=float(args.min_confidence),
                                   emit_path=args.emit_trades, **bt_kwargs)
            print(_format_text(summary))
    except Exception as exc:
        print(f"ERROR: backtest failed: {exc}", file=sys.stderr)
        return 1

    if args.json_out:
        payload = json.dumps(summary, indent=2, default=str)
        if args.json_out == "-":
            print(payload)
        else:
            Path(args.json_out).write_text(payload)
            print(f"\nJSON written to {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
