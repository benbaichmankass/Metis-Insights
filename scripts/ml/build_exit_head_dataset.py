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
            out.append({"t": t, "high": hi, "low": lo, "close": cl,
                        "volume": _f(r.get("volume"))})
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
                   "close": c["close"], "volume": c.get("volume")}
        else:
            cur["high"] = max(cur["high"], c["high"])
            cur["low"] = min(cur["low"], c["low"])
            cur["close"] = c["close"]
            v = c.get("volume")
            cur["volume"] = ((cur.get("volume") or 0.0) + v
                             if v is not None else cur.get("volume"))
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


def _load_multipliers(path: Path) -> Dict[str, float]:
    """contract_value_usd per symbol from instruments.yaml — tiny indent
    parser (no yaml dep on the trainer's system python); same shape as
    scripts/research/m20_exit_analysis.py."""
    import re
    out: Dict[str, float] = {}
    if not path.exists():
        return out
    sym = None
    for line in path.read_text().splitlines():
        m = re.match(r"^  ([A-Z0-9_]+):\s*$", line)
        if m:
            sym = m.group(1)
            continue
        m = re.match(r"^\s+contract_value_usd:\s*([0-9.]+)", line)
        if m and sym:
            out[sym] = float(m.group(1))
    return out


def load_live_trades(db: Path, instruments: Path) -> List[dict]:
    """Closed, non-backtest, strategy-attributed journal trades with
    resolvable entry/sl geometry (same exclusions as m20_exit_analysis:
    intent_reduce legs, adopted orphans, superseded flap rows). final_R
    prefers journal pnl / (|entry-sl| * qty * contract multiplier); rows
    where that isn't derivable fall back to the last bar mark (tagged)."""
    mult = _load_multipliers(instruments)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, timestamp, closed_at, symbol, direction, entry_price, "
        "stop_loss, position_size, pnl, strategy_name "
        "FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
        "AND strategy_name IS NOT NULL AND strategy_name != '' "
        "AND COALESCE(setup_type,'') NOT IN ('intent_reduce','adopted_orphan') "
        "AND COALESCE(notes,'') NOT LIKE '%\"intent_reduce\": true%' "
        "AND COALESCE(reconcile_status,'') != 'superseded'"
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        t0 = _epoch(r["timestamp"])
        t1 = _epoch(r["closed_at"])
        entry, sl = _f(r["entry_price"]), _f(r["stop_loss"])
        if None in (t0, t1, entry, sl) or t1 <= t0:
            continue
        qty, pnl = _f(r["position_size"]), _f(r["pnl"])
        risk_usd = (abs(entry - sl) * abs(qty)
                    * mult.get(str(r["symbol"]), 1.0)) if qty else 0.0
        final_r = None
        src = "last_mark"
        if risk_usd > 0 and pnl is not None:
            final_r = pnl / risk_usd
            src = "journal_pnl"
        out.append({
            "source": "live", "trade_id": r["id"],
            "strategy": r["strategy_name"], "symbol": r["symbol"],
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
    # M20 P4.3 exhaustion-state trackers (design doc § P4.3): the bar index /
    # market state AT the trade's favourable extreme, so features can measure
    # "how has the move decayed since its peak". All strictly bars <= t.
    peak_a = 0
    atr_at_peak: Optional[float] = None
    mom8_at_peak: Optional[float] = None
    vol_at_peak: Optional[float] = None
    dc_hist: List[float] = []
    sign = 1.0 if is_long else -1.0
    for a, k in enumerate(range(i0, j_end)):
        c = candles[k]
        hi_r = ((c["high"] - entry) if is_long else (entry - c["low"])) / risk
        lo_r = ((c["low"] - entry) if is_long else (entry - c["high"])) / risk
        # favourable-signed 8-bar close momentum (pre-entry bars allowed —
        # they are <= t)
        mom_8 = None
        if k >= 8 and candles[k - 8]["close"] > 0:
            mom_8 = sign * (c["close"] / candles[k - 8]["close"] - 1.0)
        new_peak = hi_r > mfe
        mfe = max(mfe, hi_r)
        mae = min(mae, lo_r)
        if new_peak or a == 0:
            peak_a = a
            atr_at_peak = atrs[k]
            mom8_at_peak = mom_8
            vol_at_peak = c.get("volume")
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
        # P4.3 features (leakage-guarded — everything from bars <= t)
        bars_since_peak = a - peak_a
        mom_decay = ((mom8_at_peak - mom_8)
                     if mom_8 is not None and mom8_at_peak is not None else None)
        atr_impulse_phase = ((atr_now / atr_at_peak)
                             if atr_now and atr_at_peak else None)
        vol_win = [x.get("volume") for x in candles[max(0, k - 19):k + 1]]
        vol_win = sorted(v for v in vol_win if v is not None and v > 0)
        vol_med = vol_win[len(vol_win) // 2] if len(vol_win) >= 5 else None
        vol_at_peak_ratio = ((vol_at_peak / vol_med)
                             if vol_at_peak and vol_med else None)
        band_ext_pctile = None
        if dc_dist is not None:
            fav_dc = sign * dc_dist
            if len(dc_hist) >= 3:
                band_ext_pctile = round(
                    sum(1 for x in dc_hist if x <= fav_dc) / len(dc_hist), 4)
            dc_hist.append(fav_dc)
        failure_swing = (1 if a > 0 and bars_since_peak <= 2
                         and m < marks[a - 1] else 0)
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
            # P4.3 exhaustion features (momentum-exhaustion design § P4.3)
            "bars_since_peak": bars_since_peak,
            "mom_8": round(mom_8, 6) if mom_8 is not None else None,
            "mom_decay": round(mom_decay, 6) if mom_decay is not None else None,
            "atr_impulse_phase": (round(atr_impulse_phase, 4)
                                  if atr_impulse_phase is not None else None),
            "vol_at_peak_ratio": (round(vol_at_peak_ratio, 4)
                                  if vol_at_peak_ratio is not None else None),
            "band_ext_pctile": band_ext_pctile,
            "failure_swing": failure_swing,
            # context
            "direction": "long" if is_long else "short",
            # labels
            "final_r": round(final_r, 4), "final_r_source": fr_src,
            "future_r_delta": round(final_r - m, 4),
            "holding_pays": 1 if (final_r - m) >= HOLDING_PAYS_R else 0,
        })
    # M20 P4.2 label (design § P4.2): peak_is_in — no meaningful new MFE from
    # bar t onward. Pure truncation observable (final trade MFE - MFE(t)),
    # same eps as holding_pays; additive second pass so existing labels are
    # byte-unchanged.
    final_mfe = mfe
    for r in out:
        fmd = final_mfe - r["mfe_r"]
        r["future_mfe_delta"] = round(fmd, 4)
        r["peak_is_in"] = 1 if fmd <= HOLDING_PAYS_R else 0
    # M21 E-3 P_win labels (entry-refinement design § E-3): per-TRADE,
    # truncation-observable from the same bar path — did the trade touch
    # +1R (bar high basis) BEFORE it touched -1R (bar low basis)? A bar
    # that crosses both is counted conservatively as the loss touching
    # first (the stop is intrabar-first everywhere else in this repo).
    # Stamped on every row (constant per trade); the entry head trains on
    # the age_bars==0 slice with entry-time features only.
    first_touch_1r = 0
    reaches_2r = 0
    for a, k in enumerate(range(i0, j_end)):
        c = candles[k]
        hi_r = ((c["high"] - entry) if is_long else (entry - c["low"])) / risk
        lo_r = ((c["low"] - entry) if is_long else (entry - c["high"])) / risk
        if lo_r <= -1.0:
            break
        if hi_r >= 1.0:
            first_touch_1r = 1
            # keep scanning (loss can no longer pre-empt) for the 2R touch
            for k2 in range(k, j_end):
                c2 = candles[k2]
                h2 = ((c2["high"] - entry) if is_long
                      else (entry - c2["low"])) / risk
                if h2 >= 2.0:
                    reaches_2r = 1
                    break
            break
    for r in out:
        r["first_touch_1r"] = first_touch_1r
        r["reaches_2r"] = reaches_2r
    return out


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", action="append", default=[], metavar="PATH",
                   help="Harness --emit-trades JSONL (repeatable).")
    p.add_argument("--db", default=None,
                   help="trade_journal.db for live closed trades.")
    p.add_argument("--instruments", default="config/instruments.yaml",
                   help="instruments.yaml for contract_value_usd multipliers.")
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
        trades += load_live_trades(Path(a.db), Path(a.instruments))
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
