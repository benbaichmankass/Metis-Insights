#!/usr/bin/env python3
"""M21 E-1 — entry-quality baseline: quantify WHERE each leg's entries lose.

Consumes a leg's harness-emitted trades (``--emit-trades`` jsonl — e.g. the
files the flip-replay sweep left under runtime_logs/m20_flip_replay/<date>/)
plus the leg's candle file, and computes per-leg entry diagnostics strictly
from the in-trade bar path (no re-simulation):

- MAE-before-peak distribution (how deep do WINNERS draw down first?) —
  the direct input for stop/entry-price placement and confirmation filters.
- Immediate-reversal rate: trades whose first `--early-bars` closed bars go
  net against the entry (never above +0R) — the false-breakout share.
- Bars-to-peak distribution (how long momentum persists after entry).
- Entry-hour / day-of-week PnL split (net_R per bucket, min-n guarded).
- Entry-bar context of winners vs losers: ADX-14 label + ATR percentile at
  the ENTRY bar (strictly bars <= entry — no lookahead).

Output: one JSON per leg + a ranked SUMMARY.md (legs by entry-quality
deficit, with the most promising filter axis per leg). Tier-1 research
tooling — reads files, writes reports, never touches config.

Usage (trainer, after a flip-replay/fleet sweep left emits):
  python3 scripts/research/m21_entry_baseline.py \
      --emits-dir runtime_logs/m20_flip_replay/2026-07-13 \
      --data-dir data --out runtime_logs/m21_entry_baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))

from src.runtime.regime.detector import regime_label, wilder_adx  # noqa: E402
from m20_fleet_exit_sweep import resolve_data  # noqa: E402
from m20_regime_flip_replay import load_candles  # noqa: E402


def pctl(sorted_vals: List[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    return round(sorted_vals[int(p * (len(sorted_vals) - 1))], 4)


def analyze_leg(trades: List[dict], candles: pd.DataFrame,
                early_bars: int) -> Dict[str, Any]:
    ts = pd.to_datetime(candles["timestamp"], utc=True)
    ts_list = [t.timestamp() for t in ts]
    highs = candles["high"].astype(float).to_numpy()
    lows = candles["low"].astype(float).to_numpy()
    adx = wilder_adx(candles, period=14).to_numpy()
    # entry-bar ATR percentile (Wilder TR-mean proxy over 14, ranked vs its
    # own trailing year) — strictly information available at the entry bar
    tr = (candles["high"] - candles["low"]).astype(float)
    atr14 = tr.rolling(14).mean().to_numpy()

    rows = []
    for t in trades:
        try:
            entry = float(t["entry"])
            risk = abs(entry - float(t["sl"]))
            if risk <= 0:
                continue
            direction = t["direction"]
            t_open = pd.to_datetime(t["entry_time"], utc=True).timestamp()
            t_close = pd.to_datetime(t["exit_time"], utc=True).timestamp()
            net_r = float(t.get("net_r") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        i0 = bisect_right(ts_list, t_open)       # first bar AFTER entry
        i1 = min(bisect_right(ts_list, t_close) - 1, len(ts_list) - 1)
        if i0 > i1:
            continue
        sign = 1.0 if direction == "long" else -1.0
        peak_r = mae_r = mae_before_peak = 0.0
        peak_i = i0
        for i in range(i0, i1 + 1):
            fav = sign * ((highs[i] if sign > 0 else lows[i]) - entry) / risk
            adv = sign * ((lows[i] if sign > 0 else highs[i]) - entry) / risk
            if fav > peak_r:
                peak_r, peak_i = fav, i
                # freeze the pre-peak MAE at the moment of each new peak
                mae_before_peak = mae_r
            if adv < mae_r:
                mae_r = adv
        early_hi = max((sign * ((highs[i] if sign > 0 else lows[i]) - entry)
                        / risk for i in range(i0, min(i0 + early_bars, i1 + 1))),
                       default=0.0)
        eb = max(i0 - 1, 0)  # the ENTRY bar itself (signal bar)
        atr_now = atr14[eb]
        window = atr14[max(0, eb - 365):eb]
        window = window[~pd.isna(window)]
        atr_pct = (round(float((window < atr_now).mean()), 3)
                   if len(window) >= 30 and not pd.isna(atr_now) else None)
        dt = datetime.fromtimestamp(t_open, tz=timezone.utc)
        rows.append({
            "net_r": net_r, "win": net_r > 0,
            "mfe_r": round(peak_r, 4), "mae_r": round(mae_r, 4),
            "mae_before_peak_r": round(mae_before_peak, 4),
            "bars_to_peak": peak_i - i0,
            "early_fail": early_hi <= 0.0,
            "hour": dt.hour, "dow": dt.weekday(),
            "entry_adx_label": regime_label(adx[eb]) if not pd.isna(adx[eb])
                               else "unknown",
            "entry_atr_pctile": atr_pct,
        })
    if not rows:
        return {"trades": 0}

    winners = [r for r in rows if r["win"]]
    losers = [r for r in rows if not r["win"]]
    w_mae = sorted(-r["mae_before_peak_r"] for r in winners)
    out: Dict[str, Any] = {
        "trades": len(rows),
        "win_rate": round(100.0 * len(winners) / len(rows), 1),
        "net_r_total": round(sum(r["net_r"] for r in rows), 2),
        "early_fail_rate": round(100.0 * sum(r["early_fail"] for r in rows)
                                 / len(rows), 1),
        "early_fail_net_r": round(sum(r["net_r"] for r in rows
                                      if r["early_fail"]), 2),
        "winner_mae_before_peak_p50": pctl(w_mae, 0.5),
        "winner_mae_before_peak_p80": pctl(w_mae, 0.8),
        "winner_bars_to_peak_p50": pctl(
            sorted(float(r["bars_to_peak"]) for r in winners), 0.5),
    }
    # buckets (min 10 trades to report)
    def bucket(key, values):
        b = {}
        for v in values:
            sub = [r for r in rows if r[key] == v]
            if len(sub) >= 10:
                b[str(v)] = {"n": len(sub),
                             "net_r": round(sum(r["net_r"] for r in sub), 2),
                             "win_rate": round(100.0 * sum(r["win"] for r in sub)
                                               / len(sub), 1)}
        return b
    out["by_entry_regime"] = bucket("entry_adx_label",
                                    ["chop", "transitional", "trending"])
    out["by_hour"] = bucket("hour", range(24))
    out["by_dow"] = bucket("dow", range(7))
    # entry-bar ATR percentile split: winners vs losers mean
    wa = [r["entry_atr_pctile"] for r in winners if r["entry_atr_pctile"] is not None]
    la = [r["entry_atr_pctile"] for r in losers if r["entry_atr_pctile"] is not None]
    out["entry_atr_pctile_winners_mean"] = round(sum(wa) / len(wa), 3) if wa else None
    out["entry_atr_pctile_losers_mean"] = round(sum(la) / len(la), 3) if la else None
    return out


def deficit_score(a: Dict[str, Any]) -> float:
    """Rank legs by entry-quality deficit: money lost to immediate failures."""
    if not a.get("trades"):
        return 0.0
    return -(a.get("early_fail_net_r") or 0.0)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emits-dir", required=True,
                    help="dir of <leg>_trades.jsonl harness emits")
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--early-bars", type=int, default=3)
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m21_entry_baseline"))
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    out_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for emit in sorted(Path(a.emits_dir).glob("*_trades.jsonl")):
        leg = emit.name[:-len("_trades.jsonl")]
        cfg = strategies.get(leg)
        if not isinstance(cfg, dict):
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        data, proxy, _ = resolve_data(str(sym), tf, Path(a.data_dir))
        if data is None:
            results[leg] = {"trades": 0, "error": "data_missing"}
            continue
        candles = load_candles(data, tf)
        trades = [json.loads(x) for x in emit.read_text().splitlines()]
        r = analyze_leg(trades, candles, a.early_bars)
        r["proxy"] = proxy
        results[leg] = r
        print(f"{leg:28s} n={r.get('trades',0):4d} early_fail%="
              f"{r.get('early_fail_rate','-')} early_fail_netR="
              f"{r.get('early_fail_net_r','-')}", flush=True)
        (out_dir / f"{leg}.json").write_text(json.dumps(r, indent=1))

    ranked = sorted(results.items(), key=lambda kv: -deficit_score(kv[1]))
    lines = ["# M21 E-1 entry-quality baseline", "",
             "| leg | trades | win% | net_R | early-fail% | early-fail net_R | winner MAE-pre-peak p80 | worst axis hint |",
             "|---|---|---|---|---|---|---|---|"]
    for leg, r in ranked:
        if not r.get("trades"):
            continue
        hints = []
        reg = r.get("by_entry_regime") or {}
        for lbl, b in reg.items():
            if b["net_r"] < 0:
                hints.append(f"regime:{lbl} {b['net_r']}R/{b['n']}t")
        neg_hours = [(h, b) for h, b in (r.get("by_hour") or {}).items()
                     if b["net_r"] < 0]
        if neg_hours:
            worst = min(neg_hours, key=lambda x: x[1]["net_r"])
            hints.append(f"hour:{worst[0]} {worst[1]['net_r']}R/{worst[1]['n']}t")
        lines.append(
            f"| {leg} | {r['trades']} | {r['win_rate']} | {r['net_r_total']} "
            f"| {r['early_fail_rate']} | {r['early_fail_net_r']} "
            f"| {r.get('winner_mae_before_peak_p80')} "
            f"| {'; '.join(hints) or '—'} |")
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print(f"done -> {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
