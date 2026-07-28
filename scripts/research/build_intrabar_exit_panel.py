#!/usr/bin/env python3
"""M30 × M20 — the PER-BAR IN-TRADE EXIT panel builder (the dense-substrate fusion).

**Why this exists — the structural fix.** Every M30 discovery so far nulled or
went not-computed for one reason: the **decision-time** feature vector is
block-sparse (each strategy instruments its own columns), so the listwise-complete
set the out-of-sample multivariate pass needs collapses toward zero — on the live
journal (Studies 1/3/4) AND, decisively, on the pooled backtest substrate
(Study 10, 1574 trades → 0 complete-vector rows). The escape is a feature set that
is **dense by construction**: the **in-trade PATH state**. Given an open position
and its trajectory so far, ``src.research.intrabar_features`` emits — on **every
bar of every trade** — running MFE/MAE in R, how far into the trade we are, the
adverse-growth rate, the cushion left to the stop, the peak profit given back, the
in-trade realized vol, and the taker-imbalance OFI proxy. That vector is populated
on every row, so the OOS pass finally runs at scale.

This is the M30 × M20 fusion: the M30 backtest substrate + C1 leakage discipline
(large N, live-faithful trades) crossed with the M20 exit-management framing
(``exit-management-ml-experiment-DESIGN.md`` § Framing A — *does holding beat
exiting now?*). Each simulated trade contributes one row per in-trade decision
bar; the label is the **triple-barrier + time-stop** outcome of continuing to hold
(``src.research.triple_barrier``), and the meta-label is take/skip (hold vs exit)
+ a sizing magnitude. The panel feeds the dedicated exit-head analyzer
(``analyze_exit_head.py``), which trains the take/skip head **uniqueness-weighted**
(overlapping labels de-correlated) under a **grouped-by-trade purged WF-CV** and
grades it net-of-fee with the **deflated Sharpe / PBO**.

**Leakage discipline (the invariant).** Features at decision bar ``t`` read ONLY
``[entry_index+1 .. t]``; the label reads ONLY ``[t+1 .. t+time_stop_bars]``. The
two windows are disjoint by construction. ``label_t0``/``label_t1`` (absolute feed
bar indices) are emitted so the analyzer can compute concurrency/uniqueness and
purge overlapping labels across fold boundaries; ``trade_id`` groups a trade's bars
so no trade splits across a fold.

**Observe-only, Tier-1.** Reads a candle CSV, runs a backtest harness in-process,
writes a panel file. No DB write, no broker socket, no order path, no live behavior.
Any discovered exit edge routes through the standing net-of-cost backtest gate +
Tier-3 before a live surface (the M20 lifecycle).

Usage::

    python scripts/research/build_intrabar_exit_panel.py \\
        --harness ict_scalp --data data/rp_feed.csv --symbol BTCUSDT --timeframe 5m \\
        --time-stop-bars 12 --tp-r 2.0 \\
        --out runtime_logs/research/exit_head_panel.jsonl
    python scripts/research/analyze_exit_head.py \\
        --panel runtime_logs/research/exit_head_panel.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.research.build_backtest_panel import ADAPTERS, _iso_closed_at  # noqa: E402
from src.research.intrabar_features import (  # noqa: E402
    INTRABAR_FEATURE_NAMES,
    entry_atr_from_prewindow,
    intrabar_features,
)
from src.research.triple_barrier import hold_meta_label, triple_barrier_forward  # noqa: E402

# Outcome (strictly-future) columns + the meta-label. ``trade_realized_r`` is the
# trade's own realized R under the fixed (baseline) exit — the net-of-fee policy
# comparison's baseline, a per-trade constant, never a regressor.
_OUTCOME_COLS = ("forward_r", "advantage_r", "label_hold", "size", "touch", "trade_realized_r")
# Key/index columns (not regressors).
_KEY_COLS = ("strategy", "symbol", "direction", "cohort", "trade_id",
             "decision_time", "closed_at", "label_t0", "label_t1")
_COHORT = "backtest"


def _df_records(df: Any) -> List[Dict[str, Any]]:
    """Whole candle frame as a list of dicts once (indexed repeatedly per bar)."""
    if df is None:
        return []
    try:
        recs = df.to_dict("records")
    except Exception:  # noqa: BLE001
        return []
    return [r for r in recs if isinstance(r, dict)]


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def build_intrabar_exit_panel(
    *,
    harness: str,
    adapter_opts: Dict[str, Any],
    time_stop_bars: int = 12,
    tp_r: float = 2.0,
    cost_r: float = 0.0,
    expected_hold_bars: Optional[float] = None,
    atr_period: int = 14,
    dmae_window: int = 3,
    bar_stride: int = 1,
    max_sample_bars: int = 96,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(rows, manifest)`` — the per-bar in-trade exit panel.

    Tolerant end-to-end: an unknown harness / missing feed / no trades → an empty
    panel + honest manifest, never a crash.
    """
    adapter = ADAPTERS.get(harness)
    rows: List[Dict[str, Any]] = []
    run_info: Dict[str, Any] = {}
    error: Optional[str] = None
    expected = float(expected_hold_bars) if expected_hold_bars else float(time_stop_bars)
    stride = max(1, int(bar_stride))

    if adapter is None:
        error = f"unknown harness '{harness}' (known: {sorted(ADAPTERS)})"
        df, sim_trades = None, []
    else:
        try:
            df, sim_trades, run_info = adapter(**adapter_opts)
        except Exception as exc:  # noqa: BLE001 — surface in the manifest
            error = f"{type(exc).__name__}: {exc}"
            df, sim_trades = None, []

    candles = _df_records(df)
    n_bars = len(candles)
    trades_used = 0
    for trade_id, st in enumerate(sim_trades):
        ei, xi = st.entry_index, st.exit_index
        try:
            ei = int(ei)
            xi = int(xi)
        except (TypeError, ValueError):
            continue
        if ei < 0 or xi <= ei or ei + 1 >= n_bars:
            continue
        entry = _f(st.entry_price)
        stop = _f(st.stop_loss)
        if entry is None or stop is None or abs(entry - stop) <= 0:
            continue  # no R denominator → no exit-timing label
        # ATR-normalized features need a decision-time vol unit; fall back to the
        # trade's own risk (|entry-stop|, always > 0 here) when the pre-entry
        # window is too short — keeps dist_to_stop_atr / in_trade_vol_ratio DENSE.
        entry_atr = entry_atr_from_prewindow(candles, ei, period=atr_period) or abs(entry - stop)

        # Decision bars = the bars the position was actually open [ei+1 .. min(xi, ei+cap)].
        last_decision = min(xi, ei + int(max_sample_bars))
        used_any = False
        for t in range(ei + 1, last_decision + 1, stride):
            if t + 1 > n_bars:
                break
            path = candles[ei + 1 : t + 1]
            feats = intrabar_features(
                path, entry_price=entry, stop_loss=stop, side=st.side,
                entry_atr=entry_atr, expected_hold_bars=expected, dmae_window=dmae_window,
            )
            forward = candles[t + 1 : t + 1 + int(time_stop_bars)]
            tb = triple_barrier_forward(
                forward, entry_price=entry, stop_loss=stop, side=st.side,
                tp_r=tp_r, time_stop_bars=time_stop_bars,
            )
            ml = hold_meta_label(tb.get("forward_r"), feats.get("upnl_r"), cost_r=cost_r)
            if ml.get("label_hold") is None:
                continue  # no usable label at this bar (e.g. no future bars)
            offset = tb.get("touch_offset") or int(time_stop_bars)
            decision_time = _iso_closed_at(candles[t].get("timestamp"))

            side = str(st.side or "").strip().lower()
            direction = "long" if side in ("long", "buy") else ("short" if side in ("short", "sell") else side)
            rec: Dict[str, Any] = {
                "strategy": st.strategy,
                "symbol": st.symbol,
                "direction": direction,
                "cohort": _COHORT,
                "trade_id": trade_id,
                "decision_time": decision_time,
                "closed_at": decision_time,  # the C2 time axis (per-bar decision time)
                "label_t0": t + 1,
                "label_t1": t + int(offset),
                # outcomes (strictly future):
                "forward_r": tb.get("forward_r"),
                "advantage_r": ml.get("advantage_r"),
                "label_hold": ml.get("label_hold"),
                "size": ml.get("size"),
                "touch": tb.get("touch"),
                "trade_realized_r": _f(st.r_multiple),
            }
            # dense in-trade features (feat_ prefix); context cats carried for conditioning
            for k in INTRABAR_FEATURE_NAMES:
                rec[f"feat_{k}"] = feats.get(k)
            for cat_key in ("regime", "vol_regime", "setup_type"):
                v = (st.meta or {}).get(cat_key)
                if v is not None:
                    rec[f"cat_{cat_key}"] = str(v)
            rows.append(rec)
            used_any = True
        if used_any:
            trades_used += 1

    # Drop feature columns that are None across ALL rows (e.g. the taker columns on
    # a feed with no taker volume) so the panel stays DENSE — the whole point.
    feat_keys = {k for rec in rows for k in rec if k.startswith("feat_")}
    dense_feats = sorted(k for k in feat_keys if any(rec.get(k) is not None for rec in rows))
    dropped_all_null = sorted(feat_keys - set(dense_feats))
    if dropped_all_null:
        for rec in rows:
            for k in dropped_all_null:
                rec.pop(k, None)

    cat_cols = sorted({k for rec in rows for k in rec if k.startswith("cat_")})
    feature_cols = dense_feats + cat_cols
    hold_rows = [r for r in rows if r.get("label_hold") is not None]
    base_hold = round(sum(r["label_hold"] for r in hold_rows) / len(hold_rows), 4) if hold_rows else None
    manifest = {
        "source": "backtest",
        "panel": "intrabar_exit_head",
        "harness": harness,
        "adapter_opts": {k: v for k, v in adapter_opts.items() if k != "vol_spec"},
        "run_info": run_info,
        "error": error,
        "cohort": _COHORT,
        "n_feed_bars": n_bars,
        "trades_total": len(sim_trades),
        "trades_used": trades_used,
        "row_count": len(rows),
        "rows_per_trade": round(len(rows) / trades_used, 2) if trades_used else 0.0,
        "base_hold_rate": base_hold,
        "label_config": {
            "time_stop_bars": time_stop_bars,
            "tp_r": tp_r,
            "cost_r": cost_r,
            "expected_hold_bars": expected,
            "atr_period": atr_period,
            "dmae_window": dmae_window,
            "bar_stride": stride,
            "max_sample_bars": max_sample_bars,
        },
        "key_cols": list(_KEY_COLS),
        "outcome_cols": list(_OUTCOME_COLS),
        "feature_cols": feature_cols,
        "dense_feature_cols": dense_feats,
        "dropped_all_null_feature_cols": dropped_all_null,
        "leakage_contract": (
            "feat_* are strictly-PAST in-trade state, computed only from bars "
            "[entry_index+1 .. t]; the outcome_cols (forward_r/advantage_r/label_hold/"
            "size) are the triple-barrier outcome over the strictly-FUTURE window "
            "[t+1 .. t+time_stop_bars]. The two windows are disjoint by construction. "
            "label_t0/label_t1 give each label's absolute feed-bar span for "
            "uniqueness weighting + purge; trade_id groups a trade's bars so no "
            "trade splits across a fold; the CV orders rows by decision_time."
        ),
    }
    return rows, manifest


def write_panel(rows, manifest, out_path: Path) -> Tuple[Path, Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    manifest_path = out_path.with_suffix(out_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out_path, manifest_path


def main(argv: Optional[List[str]] = None) -> int:
    import os

    p = argparse.ArgumentParser(
        description=(
            "Build the M30×M20 per-bar in-trade EXIT panel (dense in-trade PATH "
            "features + triple-barrier/time-stop hold-vs-exit meta-label) from a "
            "backtest harness. Read-only, observe-only, Tier-1."
        )
    )
    p.add_argument("--harness", choices=sorted(ADAPTERS), default="ict_scalp")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--stamp-regime", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--ignore-yaml", action="store_true")
    p.add_argument("--roster", default=None, help="[backtest_system] comma roster.")
    p.add_argument("--clock-tf", default="15m", help="[backtest_system] clock tf.")
    # label / feature config
    p.add_argument("--time-stop-bars", type=int, default=12,
                   help="Vertical barrier (the time-stop) — bars forward from the decision bar.")
    p.add_argument("--tp-r", type=float, default=2.0, help="Upper barrier profit target in R.")
    p.add_argument("--cost-r", type=float, default=0.0, help="Net-of-fee buffer (R) on the hold-vs-exit label.")
    p.add_argument("--expected-hold-bars", type=float, default=None,
                   help="Reference hold for bars_in_trade_frac (default: time-stop-bars).")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--dmae-window", type=int, default=3)
    p.add_argument("--bar-stride", type=int, default=1, help="Sample every Nth in-trade bar.")
    p.add_argument("--max-sample-bars", type=int, default=96, help="Cap decision bars per trade.")
    p.add_argument("--out", default="runtime_logs/research/exit_head_panel.jsonl")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    adapter_opts = {
        "data_path": args.data,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "stamp_regime": bool(args.stamp_regime),
        "min_confidence": float(args.min_confidence),
        "ignore_yaml": bool(args.ignore_yaml),
        "roster": args.roster,
        "clock_tf": str(args.clock_tf),
    }
    rows, manifest = build_intrabar_exit_panel(
        harness=args.harness, adapter_opts=adapter_opts,
        time_stop_bars=args.time_stop_bars, tp_r=args.tp_r, cost_r=args.cost_r,
        expected_hold_bars=args.expected_hold_bars, atr_period=args.atr_period,
        dmae_window=args.dmae_window, bar_stride=args.bar_stride,
        max_sample_bars=args.max_sample_bars,
    )
    out_path, manifest_path = write_panel(rows, manifest, Path(args.out))
    if not args.quiet:
        print(
            f"intrabar exit panel [{args.harness}]: {manifest['row_count']} rows "
            f"from {manifest['trades_used']}/{manifest['trades_total']} trades "
            f"({manifest['rows_per_trade']} rows/trade), base_hold={manifest['base_hold_rate']}, "
            f"{len(manifest['dense_feature_cols'])} dense feats → {out_path}"
        )
        if manifest["error"]:
            print(f"  note: {manifest['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
