#!/usr/bin/env python3
"""M20 E0 — exit-head per-bar dataset builder.

Row = (trade, native-TF bar of the hold). Features are computed strictly
from bars <= t (leakage-guarded); labels are pure truncation observables
(the T0.4 lesson — no barrier re-simulation):

  future_r_delta = final_R - mark_R(t)      (regression)
  holding_pays   = future_r_delta >= +0.25  (primary classification)

Two trade sources, per docs/research/M20-exit-head-PROGRAM.md § E0:
  * harness emit JSONLs (``backtest_{trend,pullback}.py --emit-trades``) —
    volume; ``--trades`` (repeatable).
  * live closed trades from the journal — ground truth, the
    distribution-shift validation set; ``--db``.

Candles come from per-symbol CSVs (``--candles SYM=path.csv``, the same
files the harness ran on) resampled to ``--tf``. Stdlib-only (csv/json/
sqlite3) so it runs on the trainer without the venv.

Output: ``<out>/<family>/rows.jsonl`` + ``<out>/build_report.json``
(rows, class balance, per-family/per-year/per-source counts).

Caveat recorded in each row: ``dist_to_stop_r`` is measured against the
INITIAL stop (open_r + 1); the live trailing-stop path is not replayed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TF_S = {"5m": 300, "15m": 900, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}
HOLDING_PAYS_R = 0.25
CHOP_BAND_R = 0.25


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _epoch(v: Any) -> Optional[float]:
    if v is None:
        return None
    x = _f(v)
    if x is not None:
        # epoch-ms heuristic
        return x / 1000.0 if x > 1e11 else x
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None,):
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    return None


def load_csv_candles(path: Path) -> List[dict]:
    out: List[dict] = []
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            t = _epoch(r.get("timestamp") or r.get("time") or r.get("ts"))
            hi, lo = _f(r.get("high")), _f(r.get("low"))
            cl = _f(r.get("close"))
            if t is None or hi is None or lo is None or cl is None:
                continue
            out.append({"t": t, "high": hi, "low": lo, "close": cl})
    out.sort(key=lambda x: x["t"])
    return out


def resample(candles: List[dict], tf_s: int) -> List[dict]:
    out: List[dict] = []
    cur_bucket, cur = None, None
    for c in candles:
        b = int(c["t"] // tf_s) * tf_s
        if b != cur_bucket:
            if cur is not None:
                out.append(cur)
            cur_bucket = b
            cur = {"t": float(b), "high": c["high"], "low": c["low"],
                   "close": c["close"]}
        else:
            cur["high"] = max(cur["high"], c["high"])
            cur["low"] = min(cur["low"], c["low"])
            cur["close"] = c["close"]
    if cur is not None:
        out.append(cur)
    return out


def atr_series(candles: List[dict], n: int = 14) -> List[Optional[float]]:
    """Wilder-smoothed true range; index-aligned with candles."""
    out: List[Optional[float]] = []
    atr = None
    prev_close = None
    for i, c in enumerate(candles):
        if prev_close is None:
            tr = c["high"] - c["low"]
        else:
            tr = max(c["high"] - c["low"], abs(c["high"] - prev_close),
                     abs(c["low"] - prev_close))
        atr = tr if atr is None else (atr * (n - 1) + tr) / n
        out.append(atr if i >= n else None)
        prev_close = c["close"]
    return out


def realized_vol(closes: List[float]) -> Optional[float]:
    """Stdev of close-to-close log returns over the given window."""
    if len(closes) < 3:
        return None
    rets = []
    for a, b in zip(closes, closes[1:]):
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def family_of(strategy: str) -> str:
    s = (strategy or "").lower()
    if "donchian" in s or s.startswith("trend_"):
        return "donchian"
    if "pullback" in s:
        return "pullback"
    if "squeeze" in s:
        return "squeeze"
    if "fade" in s:
        return "fade"
    return s or "unknown"


def load_harness_trades(paths: List[Path]) -> List[dict]:
    trades = []
    for p in paths:
        for line in p.open():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t0 = _epoch(r.get("entry_time"))
            t1 = _epoch(r.get("exit_time"))
            entry, sl = _f(r.get("entry")), _f(r.get("sl"))
            if None in (t0, t1, entry, sl):
                continue
            trades.append({
                "source": "harness",
                "strategy": r.get("strategy") or "unknown",
                "symbol": r.get("symbol") or "unknown",
                "direction": (r.get("direction") or "long").lower(),
                "t_open": t0, "t_close": t1, "entry": entry, "sl": sl,
                "final_r": _f(r.get("net_r")),
                "final_r_source": "harness_net_r",
                "exit_reason": r.get("exit_reason"),
            })
    return trades


def load_live_trades(db: Path) -> List[dict]:
    """Closed, non-backtest, strategy-attributed trades with resolvable
    entry/sl geometry. final_R prefers journal pnl / (risk*qty); rows where
    that isn't derivable fall back to the last bar mark (tagged)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT t.id, t.symbol, t.direction, t.entry_price, t.stop_loss,
                  t.qty, t.pnl, t.created_at, t.closed_at, t.strategy,
                  op.updated_at AS op_updated
           FROM trades t LEFT JOIN order_packages op
                ON t.order_package_id = op.order_package_id
           WHERE t.status='closed'
             AND COALESCE(t.is_backtest,0)=0
             AND t.strategy IS NOT NULL AND t.strategy != ''
             AND COALESCE(t.setup_type,'') NOT IN ('adopted_orphan')
             AND COALESCE(t.reconcile_status,'') != 'superseded'"""
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        t0 = _epoch(r["created_at"])
        t1 = _epoch(r["closed_at"]) or _epoch(r["op_updated"])
        entry, sl = _f(r["entry_price"]), _f(r["stop_loss"])
        if None in (t0, t1, entry, sl) or t1 <= t0:
            continue
        risk = abs(entry - sl)
        qty, pnl = _f(r["qty"]), _f(r["pnl"])
        final_r = None
        src = "last_mark"
        if risk > 0 and qty and pnl is not None:
            denom = risk * qty
            if denom > 0:
                final_r = pnl / denom
                src = "journal_pnl"
        out.append({
            "source": "live", "trade_id": r["id"],
            "strategy": r["strategy"], "symbol": r["symbol"],
            "direction": (r["direction"] or "long").lower(),
            "t_open": t0, "t_close": t1, "entry": entry, "sl": sl,
            "final_r": final_r, "final_r_source": src,
            "exit_reason": None,
        })
    return out


def rows_for_trade(tr: dict, candles: List[dict], cand_ts: List[float],
                   atrs: List[Optional[float]]) -> List[dict]:
    entry, sl = tr["entry"], tr["sl"]
    risk = abs(entry - sl)
    if risk <= 0:
        return []
    is_long = tr["direction"] in ("long", "buy")
    i0 = bisect_right(cand_ts, tr["t_open"])
    j_end = bisect_right(cand_ts, tr["t_close"])
    n = j_end - i0
    if n < 2 or i0 >= len(candles):
        return []
    # entry-time reference stats (bars strictly before entry)
    atr_entry = atrs[i0 - 1] if i0 >= 1 else None
    vol_entry = realized_vol([c["close"] for c in candles[max(0, i0 - 21):i0]])

    marks: List[float] = []
    for k in range(i0, j_end):
        c = candles[k]
        marks.append(((c["close"] - entry) if is_long
                      else (entry - c["close"])) / risk)
    final_r = tr["final_r"]
    fr_src = tr["final_r_source"]
    if final_r is None:
        final_r = marks[-1]
        fr_src = "last_mark"

    fam = family_of(tr["strategy"])
    out: List[dict] = []
    mfe = mae = 0.0
    chop_hits = 0
    stagn_run = 0
    for a, k in enumerate(range(i0, j_end)):
        c = candles[k]
        hi_r = ((c["high"] - entry) if is_long else (entry - c["low"])) / risk
        lo_r = ((c["low"] - entry) if is_long else (entry - c["high"])) / risk
        mfe = max(mfe, hi_r)
        mae = min(mae, lo_r)
        m = marks[a]
        if abs(m) < CHOP_BAND_R:
            chop_hits += 1
            stagn_run += 1
        else:
            stagn_run = 0
        atr_now = atrs[k]
        closes_win = [x["close"] for x in candles[max(0, k - 20):k + 1]]
        vol_now = realized_vol(closes_win)
        # donchian mid distance (20-bar) in ATRs
        dc_lo = min(x["low"] for x in candles[max(0, k - 19):k + 1])
        dc_hi = max(x["high"] for x in candles[max(0, k - 19):k + 1])
        dc_mid = (dc_lo + dc_hi) / 2.0
        dc_dist = ((c["close"] - dc_mid) / atr_now) if atr_now else None
        ts = datetime.fromtimestamp(c["t"], tz=timezone.utc)
        out.append({
            # keys
            "source": tr["source"], "family": fam,
            "strategy": tr["strategy"], "symbol": tr["symbol"],
            "trade_key": tr.get("trade_id") or f"{tr['strategy']}:{tr['symbol']}:{int(tr['t_open'])}",
            "bar_t": int(c["t"]), "year": ts.year,
            # trade state
            "age_bars": a, "open_r": round(m, 4),
            "mfe_r": round(mfe, 4), "mae_r": round(mae, 4),
            "giveback_r": round(mfe - m, 4),
            "chop_frac_so_far": round(chop_hits / (a + 1), 4),
            "stagnation_run": stagn_run,
            "dist_to_stop_r": round(m + 1.0, 4),
            # market state
            "vol_ratio_vs_entry": (round(vol_now / vol_entry, 4)
                                   if vol_now and vol_entry else None),
            "atr_ratio_vs_entry": (round(atr_now / atr_entry, 4)
                                   if atr_now and atr_entry else None),
            "donchian_mid_dist_atr": (round(dc_dist, 4)
                                      if dc_dist is not None else None),
            "hour_of_day": ts.hour, "dayofweek": ts.weekday(),
            # context
            "direction": "long" if is_long else "short",
            # labels
            "final_r": round(final_r, 4), "final_r_source": fr_src,
            "future_r_delta": round(final_r - m, 4),
            "holding_pays": 1 if (final_r - m) >= HOLDING_PAYS_R else 0,
        })
    return out


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", action="append", default=[], metavar="PATH",
                   help="Harness --emit-trades JSONL (repeatable).")
    p.add_argument("--db", default=None,
                   help="trade_journal.db for live closed trades.")
    p.add_argument("--candles", action="append", default=[],
                   metavar="SYMBOL=CSV",
                   help="Per-symbol candle CSV the trades ran on (repeatable).")
    p.add_argument("--tf", required=True, choices=sorted(TF_S),
                   help="Native TF to resample candles to (one per build).")
    p.add_argument("--out", required=True, help="Output dataset dir.")
    a = p.parse_args(argv[1:])

    candle_map: Dict[str, Path] = {}
    for spec in a.candles:
        sym, _, path = spec.partition("=")
        if not path:
            print(f"bad --candles spec: {spec}", file=sys.stderr)
            return 2
        candle_map[sym] = Path(path)

    trades = load_harness_trades([Path(t) for t in a.trades])
    if a.db:
        trades += load_live_trades(Path(a.db))
    if not trades:
        print("no trades loaded", file=sys.stderr)
        return 1

    tf_s = TF_S[a.tf]
    resampled: Dict[str, tuple] = {}
    for sym, path in candle_map.items():
        cs = resample(load_csv_candles(path), tf_s)
        resampled[sym] = (cs, [c["t"] for c in cs], atr_series(cs))
        print(f"candles {sym}: {len(cs)} {a.tf} bars")

    out_root = Path(a.out)
    fams: Dict[str, list] = {}
    skipped = {"no_candles": 0, "unresolvable": 0}
    for tr in trades:
        pack = resampled.get(tr["symbol"])
        if pack is None:
            skipped["no_candles"] += 1
            continue
        rows = rows_for_trade(tr, *pack)
        if not rows:
            skipped["unresolvable"] += 1
            continue
        fams.setdefault(rows[0]["family"], []).extend(rows)

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tf": a.tf, "trades_in": len(trades), "skipped": skipped,
        "holding_pays_threshold_r": HOLDING_PAYS_R, "families": {},
    }
    for fam, rows in sorted(fams.items()):
        d = out_root / fam
        d.mkdir(parents=True, exist_ok=True)
        with (d / "rows.jsonl").open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        pos = sum(r["holding_pays"] for r in rows)
        by_year: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        tk_by_source: Dict[str, set] = {}
        for r in rows:
            by_year[str(r["year"])] = by_year.get(str(r["year"]), 0) + 1
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
            tk_by_source.setdefault(r["source"], set()).add(r["trade_key"])
        report["families"][fam] = {
            "rows": len(rows),
            "trades": {s: len(v) for s, v in tk_by_source.items()},
            "holding_pays_pos": pos,
            "holding_pays_rate": round(pos / len(rows), 4),
            "rows_by_source": by_source, "rows_by_year": by_year,
        }
        print(f"{fam}: {len(rows)} rows, holding_pays {pos/len(rows):.1%}, "
              f"trades {report['families'][fam]['trades']}")
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "build_report.json").write_text(json.dumps(report, indent=2))
    print(f"report -> {out_root / 'build_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
