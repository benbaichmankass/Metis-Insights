#!/usr/bin/env python3
"""M20 P4.5 — regime-flip exit OFFLINE replay (momentum-exhaustion design § P4.5).

Counterfactual: exit an open harness trade at the FIRST closed bar whose
frozen ADX-14 trend label maps to an OFF cell for (policy_key, direction) in
config/regime_policy.yaml — "the regime that gated the entry has flipped".
Compared per year-fold against the trades' ACTUAL exits on net_R AND maxDD —
the same fast-gate axes as the fleet sweep.

Frozen-label v0 (the per-symbol ML label needs historical head scores and is
only live for BTC); labels are computed bar-by-bar over the leg's own
timeframe with the SAME pure detector the live gate uses
(src.runtime.regime.detector.wilder_adx / regime_label), so offline == live
by construction. Truncation-observable: the flip exit marks to the flip
bar's CLOSE (no barrier re-simulation — the T0.4 lesson); the same
round-trip fee as the harness (7.5 bps on entry notional, in R) is charged.

Tier-1 research tooling — never touches config; any live regime-flip exit is
Tier-3 and would also touch the regime plumbing (design guardrail). Usage:

  python3 scripts/research/m20_regime_flip_replay.py \
      --data data/SOLUSDT_15m.csv --symbol SOLUSDT --timeframe 2h \
      --policy-key htf_pullback_trend_2h \
      --trades /tmp/sol_pb_trades.jsonl --json /tmp/flip_sol.json
"""
from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.runtime.regime.detector import regime_label, wilder_adx  # noqa: E402
from src.runtime.regime.policy import load_policy  # noqa: E402

FEE_BPS_ROUNDTRIP = 7.5
TF_RULE = {"5m": "5min", "15m": "15min", "1h": "1h", "2h": "2h",
           "4h": "4h", "1d": "1D"}


def load_candles(path: str, tf: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    ts_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col).sort_index()
    rule = TF_RULE.get(tf)
    if rule:
        df = df.resample(rule, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        ).dropna(subset=["close"])
    return df.reset_index().rename(columns={df.index.name or ts_col: "timestamp"})


def off_cell(policy: Dict[str, Any], label: str, key: str, direction: str) -> bool:
    cell = ((policy.get(label) or {}).get(key) or {})
    v = cell.get(direction, "on")
    # PyYAML maps `on`/`off` -> True/False; mirror policy._evaluate_trend_cell.
    return v is False or (isinstance(v, str) and v.lower() == "off")


def replay(trades: List[dict], candles: pd.DataFrame, adx: pd.Series,
           policy: Dict[str, Any], key: str) -> List[dict]:
    ts = pd.to_datetime(candles["timestamp"], utc=True)
    ts_list = [t.timestamp() for t in ts]
    closes = candles["close"].astype(float).to_numpy()
    labels = [regime_label(a) for a in adx.to_numpy()]
    out = []
    for tr in trades:
        try:
            entry = float(tr["entry"])
            risk = abs(entry - float(tr["sl"]))
            if risk <= 0:
                continue
            direction = tr["direction"]
            t_open = pd.to_datetime(tr["entry_time"], utc=True).timestamp()
            t_close = pd.to_datetime(tr["exit_time"], utc=True).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        i0 = bisect_right(ts_list, t_open)          # first bar AFTER entry
        i1 = bisect_right(ts_list, t_close) - 1     # last in-trade bar
        fee_r = entry * (FEE_BPS_ROUNDTRIP / 10000.0) / risk
        actual_r = float(tr.get("net_r") if tr.get("net_r") is not None
                         else tr.get("gross_r", 0.0))
        flip_i = None
        for i in range(i0, min(i1 + 1, len(labels))):
            if labels[i] != "unknown" and off_cell(policy, labels[i], key,
                                                   direction):
                flip_i = i
                break
        if flip_i is None:
            r = actual_r
            reason = "no_flip"
        else:
            px = closes[flip_i]
            gross = ((px - entry) if direction == "long" else (entry - px)) / risk
            r = round(gross - fee_r, 4)
            reason = f"flip_{labels[flip_i]}"
        out.append({"entry_time": tr["entry_time"], "direction": direction,
                    "year": str(tr["entry_time"])[:4], "actual_r": actual_r,
                    "flip_r": r, "flip_reason": reason})
    return out


def fold_metrics(rows: List[dict], field: str) -> Dict[str, Any]:
    total = cum = peak = dd = 0.0
    for r in rows:
        v = float(r[field])
        total += v
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {"net_total_r": round(total, 4), "max_drawdown_r": round(dd, 4),
            "trades": len(rows)}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", required=True)
    ap.add_argument("--policy-key", required=True,
                    help="strategy key in config/regime_policy.yaml (family "
                         "base, e.g. htf_pullback_trend_2h / trend_donchian)")
    ap.add_argument("--adx-period", type=int, default=14)
    ap.add_argument("--trades", required=True,
                    help="harness --emit-trades jsonl")
    ap.add_argument("--policy", default=None,
                    help="policy yaml path (default config/regime_policy.yaml)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv[1:])

    candles = load_candles(a.data, a.timeframe)
    adx = wilder_adx(candles, period=a.adx_period)
    policy = load_policy(a.policy)
    trades = [json.loads(x) for x in Path(a.trades).read_text().splitlines()]
    rows = replay(trades, candles, adx, policy, a.policy_key)

    years = sorted({r["year"] for r in rows})
    folds = {}
    wins = usable = 0
    for y in years:
        yr = [r for r in rows if r["year"] == y]
        act = fold_metrics(yr, "actual_r")
        flp = fold_metrics(yr, "flip_r")
        ok = (flp["net_total_r"] >= act["net_total_r"]
              and flp["max_drawdown_r"] <= act["max_drawdown_r"])
        folds[y] = {"actual": act, "flip": flp, "beats": ok}
        usable += 1
        wins += 1 if ok else 0
    flipped = sum(1 for r in rows if r["flip_reason"] != "no_flip")
    result = {
        "symbol": a.symbol, "timeframe": a.timeframe,
        "policy_key": a.policy_key, "trades": len(rows),
        "flip_exits": flipped,
        "flip_pct": round(100.0 * flipped / len(rows), 1) if rows else 0.0,
        "overall_actual": fold_metrics(rows, "actual_r"),
        "overall_flip": fold_metrics(rows, "flip_r"),
        "folds": folds,
        "walkforward": f"{wins}/{usable}",
        "verdict": ("PASS" if usable >= 4 and wins * 3 >= usable * 2
                    else "fail"),
    }
    print(json.dumps(result, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
