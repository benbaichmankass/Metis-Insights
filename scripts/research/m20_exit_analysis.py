#!/usr/bin/env python3
"""M20 Exit Refinement — trainer-side exit analysis (Tier-1, read-only research).

One self-contained (stdlib-only) pass over the synced trade journal + the
mirrored exit soaks + the ``datasets-out/market_raw`` candle side-streams,
producing the evidence the M20 session prompt asks for:

  S1  Coverage & sufficiency — candle spans per symbol, journal freshness,
      soak row counts (the honest denominators before any verdict).
  S2  Exit-ladder soak counterfactual — for soaked orders, three arms
      (real realized R anchor / flat SL-TP re-sim / laddered re-sim),
      censoring-aware: an unresolved walk is CENSORED, never marked-to-close.
  S3  Chop-hold diagnostics + truncation counterfactuals — per closed trade,
      the 15m mark-to-market path in R units (MFE/MAE, time-to-peak, giveback,
      chop-time fraction), then TIME-STOP / STAGNATION-STOP counterfactuals.
      These are TRUNCATIONS of real trades: the counterfactual exit value is
      the observed market price at the truncation bar, so — unlike a full
      re-simulation — the comparison does not depend on a barrier engine
      reproducing live exit behaviour (the T0.4 calibration failure).
  S4  Cross-timeframe exit triggers — does a FASTER-TF trend flip (1h EMA
      cross) or a chop-regime read make a better exit for slower-TF positions?
      Same truncation construction.

Honesty rules baked in: real/paper are never blended (split throughout);
reduce legs / superseded / adopted-orphan rows excluded (the
PERF-20260601-001 artifact class); R uses multiplier-aware risk_usd; rows a
lever does not touch contribute Δ=0 (reported via n_affected); censored
counterfactuals are counted, never averaged in.

Run on the trainer:
    python3 scripts/research/m20_exit_analysis.py \
        --db data/trade_journal.db --datasets-root datasets-out \
        --ladder-soak runtime_logs/exit_ladder_soak.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_BAR_S = 900.0  # fallback bar spacing (15m) when a symbol's interval can't be inferred


# ---------------------------------------------------------------- utilities
def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _epoch(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 12:  # epoch-ms string (reconciler closed_at)
        try:
            return int(s) / 1000.0
        except (ValueError, OverflowError):
            return None
    if s.isdigit():
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:  # SQLite CURRENT_TIMESTAMP "YYYY-MM-DD HH:MM:SS"
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _load_multipliers(path: Path) -> Dict[str, float]:
    """contract_value_usd per symbol from instruments.yaml — tiny indent parser
    (no yaml dep on the trainer's system python)."""
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


def _newest_data_jsonl(d: Path) -> Optional[Path]:
    cands = sorted(d.glob("*/data.jsonl"))
    return cands[-1] if cands else None


def _infer_bar_seconds(candles: List[dict]) -> float:
    """Bar spacing (seconds) from the median of consecutive timestamp deltas.

    Robust to weekend gaps in daily equity bars (Fri→Mon = 3d) because the
    median over the full series is dominated by the regular cadence. Falls back
    to the 15m default when <2 candles are present.
    """
    ts = [c["t"] for c in candles]
    if len(ts) < 2:
        return _DEFAULT_BAR_S
    deltas = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if not deltas:
        return _DEFAULT_BAR_S
    return float(median(deltas))


def load_candles(sym: str, ds_root: Path) -> Tuple[List[dict], float]:
    """OHLC from market_raw; returns (candles, bar_seconds).

    Tries the intraday intervals first, then daily (``1d``) so equities/metals
    fleet shards resolve too. The resolved bar spacing is returned so downstream
    time math (hold-hours, time-to-MFE) is correct per-symbol rather than
    assuming 15m — a strategy fleet can span 15m crypto and 1d equities.
    """
    base = ds_root / "market_raw" / sym
    for interval in ("15m", "5m", "1h", "1d"):
        p = _newest_data_jsonl(base / interval)
        if p is None:
            continue
        out = []
        for line in p.open():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = _epoch(r.get("ts") or r.get("time") or r.get("timestamp"))
            hi, lo, cl = _f(r.get("high")), _f(r.get("low")), _f(r.get("close"))
            if t is None or hi is None or lo is None or cl is None:
                continue
            out.append({"t": t, "high": hi, "low": lo, "close": cl})
        if out:
            out.sort(key=lambda x: x["t"])
            return out, _infer_bar_seconds(out)
    return [], _DEFAULT_BAR_S


TF_S = {"5m": 300, "15m": 900, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}


def _resample(candles: List[dict], tf_s: int) -> List[dict]:
    """15m → native-TF bars (UTC-aligned buckets)."""
    if not candles:
        return []
    out: List[dict] = []
    cur_bucket = None
    cur: Optional[dict] = None
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


def _ema(vals: List[float], n: int) -> List[float]:
    out, k = [], 2.0 / (n + 1)
    e = None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


# ------------------------------------------------------------ S2 ladder sim
def sim_flat(candles, cand_ts, t0, entry, is_long, sl, tp, max_bars) -> dict:
    risk = abs(entry - sl)
    if risk <= 0:
        return {"outcome": "bad", "r": None, "censored": "bad_geometry"}
    r_tp = abs(tp - entry) / risk
    i = bisect_right(cand_ts, t0)
    end = min(i + max_bars, len(candles))
    for j in range(i, end):
        c = candles[j]
        sl_hit = c["low"] <= sl if is_long else c["high"] >= sl
        tp_hit = c["high"] >= tp if is_long else c["low"] <= tp
        if sl_hit:  # conservative: stop first on ambiguous bars
            return {"outcome": "sl", "r": -1.0, "censored": "none",
                    "bars": j - i + 1}
        if tp_hit:
            return {"outcome": "tp", "r": r_tp, "censored": "none",
                    "bars": j - i + 1}
    return {"outcome": "censored", "r": None,
            "censored": "max_hold" if end == i + max_bars else "data_edge"}


def sim_ladder(candles, cand_ts, t0, entry, is_long, ladder, max_bars) -> dict:
    """Walk the materialized ladder: partial exits at each rung/final, stop for
    the remainder. Stop-first on ambiguous bars (conservative). Weighted R.
    Trailing finals are skipped (counted by caller)."""
    stop = _f((ladder.get("stop") or {}).get("price"))
    targets = [t for t in (ladder.get("targets") or [])
               if _f(t.get("price")) is not None and _f(t.get("qty_pct"))]
    if stop is None or not targets:
        return {"outcome": "bad", "r": None, "censored": "bad_geometry"}
    risk = abs(entry - stop)
    if risk <= 0:
        return {"outcome": "bad", "r": None, "censored": "bad_geometry"}
    # near→far
    targets = sorted(targets, key=lambda t: abs(_f(t["price"]) - entry))
    rem = 1.0
    realized = 0.0
    i = bisect_right(cand_ts, t0)
    end = min(i + max_bars, len(candles))
    ti = 0
    for j in range(i, end):
        c = candles[j]
        if (c["low"] <= stop) if is_long else (c["high"] >= stop):
            realized += rem * (-1.0)
            return {"outcome": "resolved", "r": realized, "censored": "none",
                    "bars": j - i + 1, "rungs_filled": ti}
        while ti < len(targets):
            price = _f(targets[ti]["price"])
            hit = (c["high"] >= price) if is_long else (c["low"] <= price)
            if not hit:
                break
            frac = min(rem, float(targets[ti].get("qty_pct") or 0.0))
            r_t = ((price - entry) if is_long else (entry - price)) / risk
            realized += frac * r_t
            rem = max(0.0, rem - frac)
            ti += 1
            if rem <= 1e-9:
                return {"outcome": "resolved", "r": realized,
                        "censored": "none", "bars": j - i + 1,
                        "rungs_filled": ti}
    return {"outcome": "censored", "r": None,
            "censored": "max_hold" if end == i + max_bars else "data_edge",
            "realized_partial": realized, "remaining_frac": rem}


# ------------------------------------------------------- trade path metrics
def trade_path(candles, cand_ts, t_open, t_close, entry, sl, is_long
               ) -> Optional[dict]:
    risk = abs(entry - sl) if sl else 0.0
    if risk <= 0 or not candles:
        return None
    i = bisect_right(cand_ts, t_open)
    j_end = bisect_right(cand_ts, t_close) if t_close else len(candles)
    if j_end <= i:
        return None
    marks: List[float] = []
    mfe = mae = 0.0
    t_mfe = 0
    for k in range(i, j_end):
        c = candles[k]
        hi_r = ((c["high"] - entry) if is_long else (entry - c["low"])) / risk
        lo_r = ((c["low"] - entry) if is_long else (entry - c["high"])) / risk
        cl_r = ((c["close"] - entry) if is_long else (entry - c["close"])) / risk
        if hi_r > mfe:
            mfe, t_mfe = hi_r, k - i
        mae = min(mae, lo_r)
        marks.append(cl_r)
    n = len(marks)
    chop = sum(1 for m in marks if abs(m) < 0.25) / n
    # longest stagnation run (|mark| < 0.25R)
    run = best_run = 0
    for m in marks:
        run = run + 1 if abs(m) < 0.25 else 0
        best_run = max(best_run, run)
    return {"bars": n, "marks": marks, "mfe": mfe, "mae": mae,
            "t_mfe_bars": t_mfe, "chop_frac": chop, "stagn_run": best_run,
            "i0": i}


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/trade_journal.db")
    ap.add_argument("--datasets-root", default="datasets-out")
    ap.add_argument("--ladder-soak", default="runtime_logs/exit_ladder_soak.jsonl")
    ap.add_argument("--instruments", default="config/instruments.yaml")
    ap.add_argument("--max-hold-bars", type=int, default=672)  # 7d of 15m
    ap.add_argument("--since-days", type=float, default=90.0)
    args = ap.parse_args()

    ds_root = Path(args.datasets_root)
    mult = _load_multipliers(Path(args.instruments))
    now = datetime.now(timezone.utc).timestamp()

    # ---------- journal
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, timestamp, closed_at, symbol, direction, entry_price, "
        "exit_price, stop_loss, take_profit_1, position_size, pnl, status, "
        "strategy_name, account_id, account_class, is_demo, setup_type, "
        "exit_reason, notes, fee_taker_usd, funding_paid_usd "
        "FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
        "AND pnl IS NOT NULL "
        "AND COALESCE(setup_type,'') NOT IN ('intent_reduce','adopted_orphan') "
        "AND COALESCE(notes,'') NOT LIKE '%\"intent_reduce\": true%' "
        "AND COALESCE(reconcile_status,'') != 'superseded'"
    ).fetchall()

    trades = []
    for r in rows:
        t_open = _epoch(r["timestamp"])
        t_close = _epoch(r["closed_at"]) or t_open
        if t_open is None or (now - t_open) > args.since_days * 86400:
            continue
        entry, sl, qty = _f(r["entry_price"]), _f(r["stop_loss"]), _f(r["position_size"])
        if not entry or not qty:
            continue
        m = mult.get(str(r["symbol"]), 1.0)
        risk_usd = abs(entry - (sl or 0)) * abs(qty) * m if sl else None
        real_r = (r["pnl"] / risk_usd) if (risk_usd and risk_usd > 0) else None
        trades.append({
            "id": r["id"], "symbol": r["symbol"],
            "strategy": r["strategy_name"] or "?",
            "dir": str(r["direction"] or "").lower(),
            "cls": (r["account_class"] or ("paper" if r["is_demo"] else "real_money")),
            "account": r["account_id"],
            "t_open": t_open, "t_close": t_close,
            "entry": entry, "sl": sl, "qty": qty,
            "pnl": r["pnl"], "real_r": real_r,
            "exit_reason": r["exit_reason"] or "",
        })

    # ---------- candles per symbol
    syms = sorted({t["symbol"] for t in trades})
    candles: Dict[str, List[dict]] = {}
    cand_ts: Dict[str, List[float]] = {}
    bar_seconds: Dict[str, float] = {}
    coverage = {}
    for s in syms:
        c, bs = load_candles(s, ds_root)
        candles[s], cand_ts[s] = c, [x["t"] for x in c]
        bar_seconds[s] = bs
        coverage[s] = {
            "bars": len(c),
            "bar_seconds": bs,
            "span": [datetime.fromtimestamp(c[0]["t"], tz=timezone.utc).date().isoformat(),
                     datetime.fromtimestamp(c[-1]["t"], tz=timezone.utc).date().isoformat()]
            if c else None,
        }

    print("\n===S1 COVERAGE===")
    max_close = max((t["t_close"] for t in trades), default=None)
    print(json.dumps({
        "closed_trades_in_window": len(trades),
        "by_class": {c: sum(1 for t in trades if t["cls"] == c)
                     for c in {t["cls"] for t in trades}},
        "journal_latest_close": datetime.fromtimestamp(
            max_close, tz=timezone.utc).isoformat() if max_close else None,
        "candle_coverage": coverage,
    }, indent=1))

    # ---------- S2 ladder soak
    soak_rows = []
    p = Path(args.ladder_soak)
    if p.exists():
        for line in p.open():
            line = line.strip()
            if line:
                try:
                    soak_rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    s2 = {"total": len(soak_rows), "differing": 0, "by_venue": {},
          "by_strategy_differing": {}, "trailing_skipped": 0,
          "arms": []}
    for rr in soak_rows:
        v = rr.get("venue", "?")
        s2["by_venue"][v] = s2["by_venue"].get(v, 0) + 1
        if not rr.get("differs_from_single_target"):
            continue
        s2["differing"] += 1
        st = rr.get("strategy", "?")
        s2["by_strategy_differing"][st] = s2["by_strategy_differing"].get(st, 0) + 1
        ladder = rr.get("ladder") or {}
        if ladder.get("final_trailing"):
            s2["trailing_skipped"] += 1
            continue
        stgt = rr.get("single_target") or {}
        entry, sl, tp = _f(stgt.get("entry")), _f(stgt.get("sl")), _f(stgt.get("tp"))
        t0 = _epoch(rr.get("ts"))
        sym = rr.get("symbol", "")
        if None in (entry, sl, tp, t0) or not candles.get(sym):
            continue
        is_long = str(rr.get("direction", "")).lower() in ("buy", "long")
        flat = sim_flat(candles[sym], cand_ts[sym], t0, entry, is_long, sl, tp,
                        args.max_hold_bars)
        lad = sim_ladder(candles[sym], cand_ts[sym], t0, entry, is_long,
                         ladder, args.max_hold_bars)
        # real anchor: nearest closed trade within 15 min
        near = [t for t in trades if t["symbol"] == sym
                and abs(t["t_open"] - t0) <= 900 and t["real_r"] is not None]
        real_r = near[0]["real_r"] if near else None
        s2["arms"].append({"ts": rr.get("ts"), "strategy": st, "symbol": sym,
                           "venue": v, "real_r": real_r,
                           "flat": flat, "ladder": lad})
    paired = [a for a in s2["arms"]
              if a["flat"]["censored"] == "none" and a["ladder"]["censored"] == "none"]
    s2["paired_resolved_n"] = len(paired)
    if paired:
        s2["mean_flat_r"] = round(sum(a["flat"]["r"] for a in paired) / len(paired), 3)
        s2["mean_ladder_r"] = round(sum(a["ladder"]["r"] for a in paired) / len(paired), 3)
        anch = [a for a in paired if a["real_r"] is not None]
        s2["anchored_n"] = len(anch)
        if anch:
            s2["mean_real_r_on_anchored"] = round(
                sum(a["real_r"] for a in anch) / len(anch), 3)
    print("\n===S2 LADDER SOAK===")
    print(json.dumps({k: v for k, v in s2.items() if k != "arms"}, indent=1))
    print(json.dumps(s2["arms"][:40], default=str))

    # ---------- S3 chop-hold diagnostics + truncation counterfactuals
    diag_by_strat: Dict[Tuple[str, str], List[dict]] = {}
    enriched = []
    for t in trades:
        if t["real_r"] is None or not t["sl"]:
            continue
        c, ts_ = candles.get(t["symbol"]), cand_ts.get(t["symbol"])
        if not c:
            continue
        pathm = trade_path(c, ts_, t["t_open"], t["t_close"], t["entry"],
                           t["sl"], t["dir"] in ("buy", "long"))
        if pathm is None or pathm["bars"] < 1:
            continue
        t2 = dict(t)
        t2.update({k: pathm[k] for k in
                   ("bars", "mfe", "mae", "t_mfe_bars", "chop_frac", "stagn_run")})
        t2["marks"] = pathm["marks"]
        t2["giveback"] = pathm["mfe"] - t["real_r"]
        bs = bar_seconds.get(t["symbol"], _DEFAULT_BAR_S)
        t2["bar_s"] = bs
        t2["hold_h"] = pathm["bars"] * bs / 3600
        t2["t_mfe_h"] = pathm["t_mfe_bars"] * bs / 3600
        # intraday == bar spacing fine enough for the 15m-bar-calibrated exit
        # levers below (time/stagnation-stop, cross-TF); daily-bar equity legs
        # feed the diagnostics but are excluded from those bar-indexed levers.
        t2["intraday"] = bs <= 900.0
        enriched.append(t2)
        diag_by_strat.setdefault((t["strategy"], t["cls"]), []).append(t2)

    def agg(ts: List[dict]) -> dict:
        n = len(ts)
        return {
            "n": n,
            "mean_r": round(sum(x["real_r"] for x in ts) / n, 3),
            "sum_r": round(sum(x["real_r"] for x in ts), 1),
            "mean_hold_h": round(sum(x["hold_h"] for x in ts) / n, 1),
            "med_t_mfe_h": round(median(x["t_mfe_h"] for x in ts), 1),
            "mean_mfe": round(sum(x["mfe"] for x in ts) / n, 2),
            "mean_giveback": round(sum(x["giveback"] for x in ts) / n, 2),
            "mean_chop_frac": round(sum(x["chop_frac"] for x in ts) / n, 2),
            "roundtrippers_pct": round(100 * sum(
                1 for x in ts if x["mfe"] >= 1.0 and x["real_r"] < 0) / n, 1),
        }

    print("\n===S3 CHOP DIAGNOSTICS (per strategy x class)===")
    print(json.dumps({f"{k[0]}|{k[1]}": agg(v)
                      for k, v in sorted(diag_by_strat.items())
                      if len(v) >= 3}, indent=1))
    print("===S3 ALL===")
    for cls in sorted({t["cls"] for t in enriched}):
        sub = [t for t in enriched if t["cls"] == cls]
        if sub:
            print(cls, json.dumps(agg(sub)))
    print("===S3 TOP GIVEBACKS (real_money)===")
    top = sorted((t for t in enriched if t["cls"] == "real_money"),
                 key=lambda t: t["giveback"], reverse=True)[:12]
    for t in top:
        print(json.dumps({
            "id": t["id"], "st": t["strategy"], "sym": t["symbol"],
            "dir": t["dir"],
            "open": datetime.fromtimestamp(t["t_open"], tz=timezone.utc
                                           ).isoformat()[:16],
            "hold_h": round(t["hold_h"], 1),
            "r": round(t["real_r"], 2), "mfe": round(t["mfe"], 2),
            "t_mfe_h": round(t["t_mfe_h"], 1),
            "chop_frac": round(t["chop_frac"], 2),
            "exit": t["exit_reason"][:24]}))

    # The bar-indexed exit-lever counterfactuals below (time-stop, stagnation,
    # cross-TF) are calibrated in 15m-bar units (e.g. T=16 bars == "4h"), so
    # they are only meaningful for intraday-bar legs. Daily-bar equity legs feed
    # the diagnostics above but are excluded here rather than mislabeled.
    diag_intraday = {k: [t for t in v if t.get("intraday", True)]
                     for k, v in diag_by_strat.items()}
    n_daily_excl = sum(1 for v in diag_by_strat.values()
                       for t in v if not t.get("intraday", True))
    if n_daily_excl:
        print(f"\n[note] {n_daily_excl} daily-bar (non-intraday) trades excluded "
              "from the 15m-bar-calibrated exit-lever counterfactuals below")

    # truncation counterfactual levers
    def truncate_cf(t: dict, exit_bar: int) -> float:
        """Counterfactual R if exited at close of path-bar ``exit_bar``;
        the rest of the trade is discarded (real exit replaced)."""
        return t["marks"][min(exit_bar, len(t["marks"]) - 1)]

    levers = []
    for T, thresh, name in [
        (16, 0.0, "time_stop_4h_flat"), (32, 0.0, "time_stop_8h_flat"),
        (96, 0.0, "time_stop_24h_flat"), (192, 0.0, "time_stop_48h_flat"),
        (96, 0.25, "time_stop_24h_lt_.25R"), (192, 0.25, "time_stop_48h_lt_.25R"),
    ]:
        levers.append((name, T, thresh))

    print("\n===S3 TIME-STOP COUNTERFACTUALS===")
    out_levers = {}
    for name, T, thresh in levers:
        per = {}
        for (strat, cls), ts_list in diag_intraday.items():
            if len(ts_list) < 3:
                continue
            deltas, n_aff = [], 0
            for t in ts_list:
                if t["bars"] > T and t["marks"][T] < thresh:
                    cf = truncate_cf(t, T)
                    deltas.append(cf - t["real_r"])
                    n_aff += 1
                else:
                    deltas.append(0.0)
            per[f"{strat}|{cls}"] = {
                "n": len(ts_list), "n_affected": n_aff,
                "sum_dR": round(sum(deltas), 2),
                "mean_dR": round(sum(deltas) / len(deltas), 3),
            }
        tot = {cls: round(sum(v["sum_dR"] for k, v in per.items()
                              if k.endswith("|" + cls)), 1)
               for cls in ("real_money", "paper")}
        out_levers[name] = {"total_sum_dR": tot, "per_strategy": per}
    print(json.dumps(out_levers, indent=1))

    # stagnation-stop: exit when |mark|<0.25R for K consecutive bars, age>A
    print("\n===S3 STAGNATION-STOP COUNTERFACTUALS===")
    stag_out = {}
    for K, A, name in [(32, 32, "stagn_8h_after_8h"), (64, 32, "stagn_16h_after_8h"),
                       (96, 96, "stagn_24h_after_24h")]:
        per = {}
        for (strat, cls), ts_list in diag_intraday.items():
            if len(ts_list) < 3:
                continue
            deltas, n_aff = [], 0
            for t in ts_list:
                run = 0
                fired = None
                for i2, m in enumerate(t["marks"]):
                    run = run + 1 if abs(m) < 0.25 else 0
                    if i2 >= A and run >= K:
                        fired = i2
                        break
                if fired is not None:
                    deltas.append(t["marks"][fired] - t["real_r"])
                    n_aff += 1
                else:
                    deltas.append(0.0)
            per[f"{strat}|{cls}"] = {
                "n": len(ts_list), "n_affected": n_aff,
                "sum_dR": round(sum(deltas), 2),
                "mean_dR": round(sum(deltas) / len(deltas), 3)}
        stag_out[name] = per
    print(json.dumps(stag_out, indent=1))

    # ---------- S4 cross-TF exit triggers
    print("\n===S4 CROSS-TF EXIT TRIGGERS===")
    # per symbol: 1h resample + EMA9/21; exit when cross against position
    # persists 2 consecutive 1h closes AND trade age > 8h.
    ema_cache: Dict[str, Tuple[List[float], List[float], List[float]]] = {}
    for s in syms:
        h1 = _resample(candles.get(s) or [], 3600)
        closes = [c["close"] for c in h1]
        ema_cache[s] = ([c["t"] for c in h1], _ema(closes, 9), _ema(closes, 21))
    s4 = {}
    for (strat, cls), ts_list in diag_intraday.items():
        if len(ts_list) < 3:
            continue
        deltas, n_aff = [], 0
        for t in ts_list:
            h1_ts, e9, e21 = ema_cache[t["symbol"]]
            if not h1_ts:
                deltas.append(0.0)
                continue
            is_long = t["dir"] in ("buy", "long")
            fired_bar = None
            streak = 0
            for i2 in range(len(t["marks"])):
                bar_t = t["t_open"] + (i2 + 1) * t.get("bar_s", _DEFAULT_BAR_S)
                if bar_t - t["t_open"] < 8 * 3600:
                    continue
                j = bisect_right(h1_ts, bar_t) - 1
                if j < 1:
                    continue
                against = (e9[j] < e21[j]) if is_long else (e9[j] > e21[j])
                streak = streak + 1 if against else 0
                if streak >= 8:  # 8 x 15m checks ~ 2h of sustained 1h flip
                    fired_bar = i2
                    break
            if fired_bar is not None:
                deltas.append(t["marks"][fired_bar] - t["real_r"])
                n_aff += 1
            else:
                deltas.append(0.0)
        s4[f"{strat}|{cls}"] = {"n": len(ts_list), "n_affected": n_aff,
                                "sum_dR": round(sum(deltas), 2),
                                "mean_dR": round(sum(deltas) / len(deltas), 3)}
    print(json.dumps({"lever": "exit_on_1h_ema9x21_flip_sustained2h_age_gt_8h",
                      "per_strategy": s4}, indent=1))

    # compact per-trade dump for PM-side drill-down (aggregate-safe fields only)
    print("\n===S3 PER-TRADE (compact)===")
    comp = [{
        "id": t["id"], "st": t["strategy"], "sym": t["symbol"], "cls": t["cls"],
        "dir": t["dir"], "open": datetime.fromtimestamp(
            t["t_open"], tz=timezone.utc).isoformat()[:16],
        "hold_h": round(t["hold_h"], 1),
        "r": round(t["real_r"], 2), "mfe": round(t["mfe"], 2),
        "mae": round(t["mae"], 2),
        "t_mfe_h": round(t["t_mfe_h"], 1),
        "chop": round(t["chop_frac"], 2), "exit": t["exit_reason"][:24],
    } for t in enriched]
    body = json.dumps(comp, separators=(",", ":"))
    if len(body) > 24000:
        import base64
        import gzip
        gz = base64.b64encode(gzip.compress(body.encode())).decode()
        print("GZB64:" + gz if len(gz) < 30000 else
              json.dumps(comp[:120], separators=(",", ":")) + "\nTRUNCATED")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
