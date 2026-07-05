#!/usr/bin/env python3
"""M19 D1 — resolve the fc-geometry shadow-soak into censoring-aware outcomes.

The live money-box writes ``runtime_logs/fc_geometry_soak.jsonl`` (one row per
opening order: the SL/TP actually placed + the decision-time ``fc_*`` snapshot
served by ``forecast_live`` — see ``src/runtime/fc_geometry_soak.py``). This
trainer-side resolver turns that soak into the comparison the offline backtest
could not honestly make (``MB-20260705-FC-SLTP-GEOMETRY``):

- **real arm** — the trade's ACTUAL realized R, joined from the synced
  ``trade_journal.db`` by (symbol, open-time proximity). Ground truth: real
  fees/monitor/flip/reconciler exits, no re-simulation.
- **fc-scaled arm** — the counterfactual SL/TP (stop/target DISTANCE scaled by
  ``clamp(fc_range_rel / median_fc_range, [lo, hi])``, R:R preserved — the
  same construction as the offline backtest, but built from the fc value the
  money-box ACTUALLY served at decision time) resolved against realized
  candles, with an explicit **censoring status**: a counterfactual that has
  not hit either barrier by the max-hold cap or the candle-data edge is
  reported CENSORED, never silently marked-to-close. (The design requirement
  from the shadow-mode literature — counterfactual exits are only partially
  identified; see the recommendation report § 1.)

Because the scaled arm is still a barrier walk while the real arm is not, the
per-row output keeps the two SEPARATE — the honest read is the paired
comparison on rows where the counterfactual RESOLVED (censored='none'), with
the censoring rate reported loudly next to it. No aggregate here folds
censored rows into a mean.

Reads only the mirrored soak log + ``datasets-out/{forecasts,market_raw}`` +
the synced journal; writes an optional JSON report. Tier-1 research; never
touches the order path.

Run on the trainer (after sync_trainer_data.sh has mirrored the soak log):
    python3 scripts/ml/fc_geometry_resolve.py \
      --soak-log runtime_logs/fc_geometry_soak.jsonl \
      --db data/trade_journal.db --max-hold 96 --json /tmp/fc_geom_resolved.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_right
from pathlib import Path
from typing import Optional

# Reuse the backtest's parsing/loading helpers — same data contracts.
from scripts.ml.fc_sltp_geometry_backtest import (  # noqa: E402
    _epoch,
    _f,
    _load_candles,
)


def _resolve_counterfactual(
    candles: list[dict], cand_ts: list[float], entry_epoch: float,
    entry: float, is_long: bool, sl: float, tp: float, max_hold: int,
) -> dict:
    """Barrier walk from the first bar strictly after entry — censoring-aware.

    Unlike the backtest's ``_simulate`` (which marks-to-close at the cap —
    exactly the rosy behaviour the anchor arm exposed), an unresolved walk is
    returned as CENSORED with the mark kept separate, so no aggregate can
    silently treat it as a resolved outcome.

    Returns ``{outcome, r, censored, bars_walked}`` where outcome ∈
    {"tp_hit", "sl_hit", "censored"}, ``r`` is realized R (None when
    censored), and ``censored`` ∈ {"none", "max_hold", "data_edge"}.
    """
    risk = abs(entry - sl)
    if risk <= 0 or entry <= 0:
        return {"outcome": "censored", "r": None, "censored": "bad_geometry",
                "bars_walked": 0}
    r_tp = abs(tp - entry) / risk
    i = bisect_right(cand_ts, entry_epoch)
    end = min(i + max_hold, len(candles))
    for j in range(i, end):
        c = candles[j]
        if is_long:
            if c["low"] <= sl:
                return {"outcome": "sl_hit", "r": -1.0, "censored": "none",
                        "bars_walked": j - i + 1}
            if c["high"] >= tp:
                return {"outcome": "tp_hit", "r": r_tp, "censored": "none",
                        "bars_walked": j - i + 1}
        else:
            if c["high"] >= sl:
                return {"outcome": "sl_hit", "r": -1.0, "censored": "none",
                        "bars_walked": j - i + 1}
            if c["low"] <= tp:
                return {"outcome": "tp_hit", "r": r_tp, "censored": "none",
                        "bars_walked": j - i + 1}
    censored = "max_hold" if end == i + max_hold else "data_edge"
    mark = None
    if end > i:
        close = candles[end - 1]["close"]
        mark = ((close - entry) if is_long else (entry - close)) / risk
    return {"outcome": "censored", "r": None, "censored": censored,
            "bars_walked": max(0, end - i), "mark_r_not_an_outcome": mark}


def _load_soak(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def _join_real_r(con: sqlite3.Connection, symbol: str, when: float,
                 tolerance_s: float = 900.0) -> Optional[float]:
    """The realized R (pnl / risk_$) of the closed journal trade opened nearest
    ``when`` for ``symbol`` (within tolerance), or None (open / unmatched /
    unpriceable)."""
    try:
        rows = con.execute(
            "SELECT entry_price, stop_loss, position_size, pnl, timestamp, created_at "
            "FROM trades WHERE symbol=? AND status='closed' AND COALESCE(is_backtest,0)=0",
            (symbol,),
        ).fetchall()
    except sqlite3.Error:
        return None
    best, best_dt = None, None
    for r in rows:
        t = _epoch(r["timestamp"] or r["created_at"])
        if t is None:
            continue
        dt = abs(t - when)
        if dt <= tolerance_s and (best_dt is None or dt < best_dt):
            best, best_dt = r, dt
    if best is None:
        return None
    entry, sl, qty, pnl = (_f(best["entry_price"]), _f(best["stop_loss"]),
                           _f(best["position_size"]), _f(best["pnl"]))
    if None in (entry, sl, qty, pnl) or not qty:
        return None
    risk = abs(entry - sl) * qty
    return (pnl / risk) if risk > 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--soak-log", default="runtime_logs/fc_geometry_soak.jsonl")
    ap.add_argument("--db", default="data/trade_journal.db")
    ap.add_argument("--datasets-root", default="datasets-out")
    ap.add_argument("--max-hold", type=int, default=96)
    ap.add_argument("--vol-clamp-lo", type=float, default=0.5)
    ap.add_argument("--vol-clamp-hi", type=float, default=2.0)
    ap.add_argument("--json", default="", help="optional path for the full JSON report")
    args = ap.parse_args()

    soak = _load_soak(Path(args.soak_log))
    if not soak:
        print(f"no soak rows at {args.soak_log} — nothing to resolve")
        return 0
    ds_root = Path(args.datasets_root)
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    lo, hi = args.vol_clamp_lo, args.vol_clamp_hi

    # Per-symbol median fc_range over the soak's OWN logged values — the live
    # counterpart of the backtest's side-stream median; recomputable per run
    # because the writer deliberately does not bake the scaling in.
    by_symbol: dict[str, list[dict]] = {}
    for r in soak:
        by_symbol.setdefault(str(r.get("symbol", "")), []).append(r)

    resolved_rows: list[dict] = []
    counts = {"rows": len(soak), "no_fc": 0, "no_median": 0, "no_candles": 0,
              "bad_row": 0, "resolved": 0, "censored": 0}
    for sym, rows in by_symbol.items():
        candles = _load_candles(sym, ds_root)
        cand_ts = [c["t"] for c in candles]
        ranges = sorted(
            v for v in (
                _f((r.get("fc_row") or {}).get("fc_range_rel")) for r in rows
            ) if v is not None and v > 0
        )
        med = ranges[len(ranges) // 2] if ranges else None
        for r in rows:
            placed = r.get("placed") or {}
            entry, sl, tp = _f(placed.get("entry")), _f(placed.get("sl")), _f(placed.get("tp"))
            when = _epoch(r.get("ts"))
            dirn = str(r.get("direction", "")).lower()
            if None in (entry, sl, tp, when) or not dirn:
                counts["bad_row"] += 1
                continue
            fc_range = _f((r.get("fc_row") or {}).get("fc_range_rel"))
            if fc_range is None or fc_range <= 0:
                counts["no_fc"] += 1
                continue
            if med is None:
                counts["no_median"] += 1
                continue
            if not candles:
                counts["no_candles"] += 1
                continue
            is_long = dirn in ("buy", "long")
            ratio = min(hi, max(lo, fc_range / med))
            sl_d, tp_d = abs(entry - sl) * ratio, abs(tp - entry) * ratio
            v_sl, v_tp = ((entry - sl_d, entry + tp_d) if is_long
                          else (entry + sl_d, entry - tp_d))
            cf = _resolve_counterfactual(candles, cand_ts, when, entry, is_long,
                                         v_sl, v_tp, args.max_hold)
            real_r = _join_real_r(con, sym, when)
            resolved_rows.append({
                "ts": r.get("ts"), "symbol": sym, "strategy": r.get("strategy"),
                "account_id": r.get("account_id"), "direction": dirn,
                "ratio": round(ratio, 4), "placed": placed,
                "scaled": {"sl": v_sl, "tp": v_tp},
                "counterfactual": cf, "real_r": real_r,
            })
            if cf["censored"] == "none":
                counts["resolved"] += 1
            else:
                counts["censored"] += 1

    # Aggregate ONLY over paired, uncensored rows — censored rows are reported,
    # never averaged in.
    paired = [x for x in resolved_rows
              if x["counterfactual"]["censored"] == "none" and x["real_r"] is not None]
    n = len(paired)
    mean_real = sum(x["real_r"] for x in paired) / n if n else None
    mean_cf = sum(x["counterfactual"]["r"] for x in paired) / n if n else None
    total = counts["resolved"] + counts["censored"]
    print(f"soak rows={counts['rows']} scored={total} "
          f"(no_fc={counts['no_fc']} no_median={counts['no_median']} "
          f"no_candles={counts['no_candles']} bad={counts['bad_row']})")
    print(f"censoring: {counts['censored']}/{total} counterfactuals censored "
          f"({(100.0 * counts['censored'] / total):.1f}%)" if total else "censoring: n/a")
    print(f"paired uncensored n={n}: mean real_R="
          f"{mean_real:.3f} vs mean fc-scaled_R={mean_cf:.3f}" if n else
          "paired uncensored n=0 — no comparison yet (keep soaking)")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "counts": counts, "paired_n": n,
            "mean_real_r": mean_real, "mean_fc_scaled_r": mean_cf,
            "clamp": [lo, hi], "max_hold": args.max_hold,
            "rows": resolved_rows,
        }, indent=1, default=str))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
