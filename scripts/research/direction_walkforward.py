#!/usr/bin/env python3
"""Directional temporal-stability (walk-forward) analysis over emitted trades.

Reads a `--emit-trades` JSONL (from backtest_trend.py / backtest_pullback.py),
partitions the trades into N contiguous time-folds by entry_time, and reports
per-fold per-direction net-R. Answers: does a directional edge (e.g. "long side
is a net drag, short side carries it") hold OUT-OF-SAMPLE across time, or is it
one-window luck?

Because these strategies are rule-based with FIXED live params (no in-sample
fitting), a contiguous time-fold split IS the walk-forward test — each fold is
genuinely out-of-sample relative to the others; there is no leakage to guard.

Usage:
  python scripts/research/direction_walkforward.py --trades t.jsonl --folds 4 --json
  # pool several symbols of one family:
  python scripts/research/direction_walkforward.py --trades a.jsonl b.jsonl c.jsonl --folds 4 --label pullback_2h --json

Verdict fields (per direction):
  folds_negative / folds_positive — how many folds that direction's net-R was < 0 / > 0
  stable_drag  — True if the LONG side is < 0 in a strict majority of folds AND
                 pooled long net-R < 0 (the "long is a durable drag" claim)
  stable_edge  — True if the SHORT side is > 0 in a strict majority of folds AND
                 pooled short net-R > 0
Neither is a trade recommendation — it is the evidence gate the backlog item
(PB-20260729-CRYPTO-PULLBACK-2H-LONG-DRAG) requires before any Tier-3 proposal.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone


def _parse_ts(s: str) -> datetime:
    s = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # tolerate "YYYY-MM-DD HH:MM:SS+00:00" already handled; last resort date-only
        dt = datetime.fromisoformat(s.split(".")[0])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if "direction" not in r or "net_r" not in r:
                    continue
                r["_t"] = _parse_ts(r.get("entry_time") or r.get("exit_time"))
                rows.append(r)
    rows.sort(key=lambda r: r["_t"])
    return rows


def _dir_stats(trades: list[dict]) -> dict:
    longs = [t for t in trades if str(t["direction"]).lower().startswith("l")]
    shorts = [t for t in trades if str(t["direction"]).lower().startswith("s")]
    lr = round(sum(float(t["net_r"]) for t in longs), 4)
    sr = round(sum(float(t["net_r"]) for t in shorts), 4)
    return {
        "trades": len(trades),
        "long_n": len(longs), "long_r": lr,
        "short_n": len(shorts), "short_r": sr,
        "net_r": round(lr + sr, 4),
    }


def analyze(paths: list[str], folds: int, label: str | None) -> dict:
    rows = _load(paths)
    n = len(rows)
    out: dict = {"label": label, "sources": paths, "total_trades": n, "folds": folds}
    if n == 0:
        out["error"] = "no trades"
        return out
    out["window"] = {"start": rows[0]["_t"].date().isoformat(),
                     "end": rows[-1]["_t"].date().isoformat()}
    # contiguous equal-count folds (by trade order in time)
    per = n / folds
    fold_rows: list[list[dict]] = [[] for _ in range(folds)]
    for i, r in enumerate(rows):
        fold_rows[min(folds - 1, int(i // per))].append(r)
    fold_stats = []
    for k, fr in enumerate(fold_rows):
        s = _dir_stats(fr)
        s["fold"] = k + 1
        if fr:
            s["from"] = fr[0]["_t"].date().isoformat()
            s["to"] = fr[-1]["_t"].date().isoformat()
        fold_stats.append(s)
    out["by_fold"] = fold_stats
    out["pooled"] = _dir_stats(rows)
    long_neg = sum(1 for s in fold_stats if s["long_n"] and s["long_r"] < 0)
    short_pos = sum(1 for s in fold_stats if s["short_n"] and s["short_r"] > 0)
    out["verdict"] = {
        "long_folds_negative": long_neg,
        "short_folds_positive": short_pos,
        "of_folds": folds,
        "stable_drag": long_neg > folds / 2 and out["pooled"]["long_r"] < 0,
        "stable_edge": short_pos > folds / 2 and out["pooled"]["short_r"] > 0,
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trades", nargs="+", required=True,
                    help="one or more emit-trades JSONL files (pooled)")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--label", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = analyze(args.trades, max(2, args.folds), args.label)
    if args.json:
        print(json.dumps(res))
        return 0
    print(f"== {res.get('label') or 'walk-forward'} == trades={res.get('total_trades')} "
          f"folds={res.get('folds')} window={res.get('window')}")
    for s in res.get("by_fold", []):
        print(f"  fold {s['fold']} [{s.get('from')}..{s.get('to')}]: "
              f"n={s['trades']} long_r={s['long_r']} (n{s['long_n']}) "
              f"short_r={s['short_r']} (n{s['short_n']}) net={s['net_r']}")
    p = res.get("pooled", {})
    v = res.get("verdict", {})
    print(f"  POOLED: long_r={p.get('long_r')} (n{p.get('long_n')}) "
          f"short_r={p.get('short_r')} (n{p.get('short_n')})")
    print(f"  VERDICT: long_neg {v.get('long_folds_negative')}/{v.get('of_folds')} · "
          f"short_pos {v.get('short_folds_positive')}/{v.get('of_folds')} · "
          f"stable_drag={v.get('stable_drag')} stable_edge={v.get('stable_edge')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
