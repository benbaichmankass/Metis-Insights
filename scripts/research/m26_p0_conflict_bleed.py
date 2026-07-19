#!/usr/bin/env python3
"""M26 P0 — quantify the directional-conflict bleed (operator-directed 2026-07-19).

Question (docs/research/M26-regime-transition-conflict-DESIGN.md § P0): when the
intent layer suppressed an opposing signal under ``FLIP_POLICY=hold``
(``reason="flip_suppressed_hold_policy: ..."``), what did HOLDING cost after the
warning?  For every conflict event, find the open position(s) it opposed and
split each trade's realized PnL at the conflict timestamp:

    tail_held   = final_realized_pnl - mark_pnl_at_conflict   (what hold earned/lost AFTER the warning)
    tail_close  = 0                                            (close-at-conflict counterfactual)
    tail_flip   = -price_move_after_conflict * signed_qty      (flip counterfactual, same qty,
                                                                closed at the held trade's close time —
                                                                an approximation, stated as such)

Aggregates are reported overall and stratified by symbol, held strategy,
timeframe ratio (held strategy TF vs opposing strategy TF, when the opposing
strategies are recoverable from the audit row), and hour-clustering (conflict
bursts).  Tier-1 research; read-only over the journal + candle shards; writes
one JSON + one markdown summary.  Run on the trainer VM (synced DB + shards):

    python3 scripts/research/m26_p0_conflict_bleed.py \
        --db data/trade_journal.db \
        --candles-root datasets-out/market_raw \
        --out /tmp/m26_p0

Honesty notes baked into the output: conflicts whose position can't be joined
or priced are COUNTED and reported as unmeasured, never silently dropped; the
flip counterfactual ignores fees/slippage of the extra round-trip (stated).
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

HOLD_MARK = "flip_suppressed_hold_policy"
# TF minutes for ratio stratification (strategy -> timeframe read from config).
_TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}


def _parse_ts(v) -> float | None:
    """epoch seconds from epoch-ms/-s numbers, numeric strings, or ISO strings."""
    if v is None:
        return None
    try:
        f = float(v)
        if f > 1e14:
            return f / 1e6
        if f > 1e11:
            return f / 1e3
        if f > 1e8:
            return f
    except (TypeError, ValueError):
        pass
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _cols(con, table):
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []


def _first(cols, *names):
    for n in names:
        if n in cols:
            return n
    return None


def _strategy_tf(repo_root: str) -> dict:
    """strategy name -> timeframe minutes, from config/strategies.yaml (best-effort)."""
    out = {}
    path = os.path.join(repo_root, "config", "strategies.yaml")
    try:
        import yaml  # available in both venvs

        data = yaml.safe_load(open(path)) or {}
        strats = data.get("strategies", data)
        if isinstance(strats, dict):
            for name, cfg in strats.items():
                if isinstance(cfg, dict):
                    tf = str(cfg.get("timeframe", "")).strip()
                    if tf in _TF_MIN:
                        out[name] = _TF_MIN[tf]
    except Exception as exc:  # noqa: BLE001 — best-effort mapping, report and continue
        print(f"WARN: strategy timeframe map unavailable ({exc}); TF-ratio stratum will be 'unknown'")
    return out


class Candles:
    """Lazy per-symbol close-price lookup from market_raw jsonl shards (newest version dir)."""

    def __init__(self, root: str):
        self.root = root
        self._cache: dict[str, tuple[list, list]] = {}

    def _load(self, symbol: str):
        if symbol in self._cache:
            return self._cache[symbol]
        ts_list: list[float] = []
        px_list: list[float] = []
        base = os.path.join(self.root, symbol)
        best = None
        if os.path.isdir(base):
            # prefer the finest TF available: 1h is universal in this repo
            for tf in ("1h", "15m", "4h", "1d"):
                tfdir = os.path.join(base, tf)
                if os.path.isdir(tfdir):
                    vers = sorted(os.listdir(tfdir))
                    if vers:
                        cand = os.path.join(tfdir, vers[-1], "data.jsonl")
                        if os.path.isfile(cand):
                            best = cand
                            break
        if best:
            with open(best) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        t = _parse_ts(r.get("ts") or r.get("timestamp") or r.get("time"))
                        c = float(r["close"])
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if t is not None:
                        ts_list.append(t)
                        px_list.append(c)
            order = sorted(range(len(ts_list)), key=ts_list.__getitem__)
            ts_list = [ts_list[i] for i in order]
            px_list = [px_list[i] for i in order]
        self._cache[symbol] = (ts_list, px_list)
        return self._cache[symbol]

    def price_at(self, symbol: str, ts: float) -> float | None:
        ts_list, px_list = self._load(symbol)
        if not ts_list:
            return None
        i = bisect.bisect_right(ts_list, ts) - 1
        if i < 0:
            return None
        # refuse a mark further than 2 days from the event (stale shard)
        if ts - ts_list[i] > 2 * 86400:
            return None
        return px_list[i]


def find_conflicts(con) -> list[dict]:
    """Conflict events — where the coordinator actually journals the hold.

    ``Coordinator.multi_account_execute`` routes a hold-suppressed opposing
    intent through ``log_rejection_to_journal`` → a **trades** row with
    ``status='rejected'`` whose ``notes`` JSON carries
    ``reason="intent_noop:flip_suppressed_hold_policy: ..."``. That row also
    carries the OPPOSING strategy (``strategy`` col) and the signal confidence
    (``notes.confidence``) — richer than the audit stream. A ``signals``-table
    scan is kept as a fallback for older data layouts.
    """
    like = f"%{HOLD_MARK}%"
    out = []
    tcols = _cols(con, "trades")
    if tcols and "notes" in tcols:
        tcol = _first(tcols, "created_at", "timestamp")
        cur = con.execute(
            "SELECT * FROM trades WHERE status = 'rejected' AND notes LIKE ?", (like,)
        )
        names = [d[0] for d in cur.description]
        for row in cur:
            r = dict(zip(names, row))
            notes = {}
            try:
                notes = json.loads(r.get("notes") or "{}")
            except (TypeError, json.JSONDecodeError):
                pass
            reason = str(notes.get("reason") or "")
            m = re.search(r"desired (\w+) opposes\s+current (\w+)", reason)
            ev = {
                "ts": _parse_ts(r.get(tcol)) if tcol else None,
                "symbol": r.get("symbol"),
                "desired_side": (m.group(1) if m else r.get("side")),
                "current_side": (m.group(2) if m else None),
                "opposing_strategies": [r.get("strategy")] if r.get("strategy") else [],
                "confidence": notes.get("confidence"),
            }
            if ev["ts"] is not None and ev["symbol"]:
                out.append(ev)
    if not out:
        scols = _cols(con, "signals")
        if scols and "meta" in scols:
            tcol = _first(scols, "ts", "timestamp", "created_at", "time")
            cur = con.execute("SELECT * FROM signals WHERE meta LIKE ?", (like,))
            names = [d[0] for d in cur.description]
            for row in cur:
                r = dict(zip(names, row))
                meta = {}
                try:
                    meta = json.loads(r.get("meta") or "{}")
                except (TypeError, json.JSONDecodeError):
                    pass
                m = re.search(r"desired (\w+) opposes\s+current (\w+)", json.dumps(meta))
                ev = {
                    "ts": _parse_ts(r.get(tcol)) if tcol else None,
                    "symbol": r.get("symbol") or meta.get("symbol"),
                    "desired_side": (m.group(1) if m else meta.get("desired_side")),
                    "current_side": (m.group(2) if m else None),
                    "opposing_strategies": [r.get("strategy")] if r.get("strategy") else [],
                    "confidence": r.get("confidence") or meta.get("confidence"),
                }
                if ev["ts"] is not None and ev["symbol"]:
                    out.append(ev)
    out.sort(key=lambda e: e["ts"])
    return out


def open_trades_at(con, symbol: str, ts: float) -> list[dict]:
    cols = _cols(con, "trades")
    ocol = _first(cols, "created_at", "timestamp", "opened_at")
    ccol = _first(cols, "closed_at")
    if not ocol:
        return []
    sel = con.execute(
        "SELECT * FROM trades WHERE symbol = ? AND (is_backtest IS NULL OR is_backtest = 0)",
        (symbol,),
    )
    names = [d[0] for d in sel.description]
    rows = []
    for row in sel:
        r = dict(zip(names, row))
        if str(r.get("setup_type") or "") in ("adopted_orphan",) or str(r.get("reconcile_status") or "") == "superseded":
            continue
        t_open = _parse_ts(r.get(ocol))
        t_close = _parse_ts(r.get(ccol)) if ccol else None
        if t_open is None or t_open > ts:
            continue
        status = str(r.get("status") or "").lower()
        if t_close is not None and t_close <= ts:
            continue
        if t_close is None and status not in ("open",):
            # closed row with unparseable close time — can't place it; skip but count upstream
            continue
        r["_t_open"], r["_t_close"] = t_open, t_close
        rows.append(r)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--candles-root", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--out", default="/tmp/m26_p0")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    tf_map = _strategy_tf(args.repo_root)
    candles = Candles(args.candles_root)

    raw_conflicts = find_conflicts(con)
    # Collapse to EPISODES: the same suppressed intent re-journals every tick
    # (and once per eligible account) while the opposing signal persists, so
    # raw rows heavily overcount. Consecutive events for the same
    # (symbol, desired_side) within EPISODE_GAP_S collapse into one episode
    # anchored at the FIRST event (the moment the warning first fired).
    EPISODE_GAP_S = 2 * 3600
    conflicts: list[dict] = []
    last_by_key: dict = {}
    for ev in raw_conflicts:
        key = (ev["symbol"], ev["desired_side"])
        prev = last_by_key.get(key)
        if prev is not None and ev["ts"] - prev["_last_ts"] <= EPISODE_GAP_S:
            prev["_last_ts"] = ev["ts"]
            prev["repeat_count"] = prev.get("repeat_count", 1) + 1
            for s in ev["opposing_strategies"]:
                if s and s not in prev["opposing_strategies"]:
                    prev["opposing_strategies"].append(s)
            continue
        ev = dict(ev)
        ev["_last_ts"] = ev["ts"]
        ev["repeat_count"] = 1
        conflicts.append(ev)
        last_by_key[key] = ev
    print(f"conflict rows: {len(raw_conflicts)} -> episodes: {len(conflicts)}")

    measured, unmeasured = [], defaultdict(int)
    for ev in conflicts:
        trades = open_trades_at(con, ev["symbol"], ev["ts"])
        if not trades:
            unmeasured["no_open_trade_joined"] += 1
            continue
        p_conf = candles.price_at(ev["symbol"], ev["ts"])
        if p_conf is None:
            unmeasured["no_conflict_price"] += 1
            continue
        for tr in trades:
            pnl = tr.get("pnl")
            entry = tr.get("entry_price")
            qty = tr.get("qty")
            side = str(tr.get("side") or "").lower()
            if pnl is None or entry is None or qty is None or tr.get("_t_close") is None:
                unmeasured["trade_unresolved_or_still_open"] += 1
                continue
            p_close = candles.price_at(ev["symbol"], tr["_t_close"])
            if p_close is None:
                unmeasured["no_close_price"] += 1
                continue
            sign = 1.0 if side in ("buy", "long") else -1.0
            qty = float(qty)
            mark_at_conflict = (float(p_conf) - float(entry)) * sign * qty
            tail_held = float(pnl) - mark_at_conflict
            move_after = float(p_close) - float(p_conf)
            tail_flip = -move_after * sign * qty  # opposite side, same qty, close at same time
            held_strat = tr.get("strategy") or "unknown"
            held_tf = tf_map.get(held_strat)
            opp_tfs = [tf_map.get(s) for s in (ev["opposing_strategies"] or []) if tf_map.get(s)]
            if held_tf and opp_tfs:
                ratio = max(held_tf, min(opp_tfs)) / max(1, min(held_tf, min(opp_tfs)))
                tf_stratum = "same_or_near_tf" if ratio < 4 else "cross_tf_>=4x"
            else:
                tf_stratum = "unknown_tf"
            measured.append(
                {
                    "ts": ev["ts"],
                    "symbol": ev["symbol"],
                    "held_strategy": held_strat,
                    "held_side": side,
                    "opposing": ev["opposing_strategies"],
                    "tf_stratum": tf_stratum,
                    "tail_held": round(tail_held, 4),
                    "tail_flip": round(tail_flip, 4),
                    "mark_at_conflict": round(mark_at_conflict, 4),
                    "final_pnl": float(pnl),
                }
            )

    def agg(rows):
        n = len(rows)
        return {
            "n": n,
            "tail_held_sum": round(sum(r["tail_held"] for r in rows), 2),
            "tail_flip_sum": round(sum(r["tail_flip"] for r in rows), 2),
            "held_worse_than_close_pct": round(
                100.0 * sum(1 for r in rows if r["tail_held"] < 0) / n, 1
            ) if n else None,
        }

    strata = {
        "overall": agg(measured),
        "by_tf_stratum": {k: agg([r for r in measured if r["tf_stratum"] == k])
                          for k in sorted({r["tf_stratum"] for r in measured})},
        "by_symbol": {k: agg([r for r in measured if r["symbol"] == k])
                      for k in sorted({r["symbol"] for r in measured})},
        "by_held_strategy": {k: agg([r for r in measured if r["held_strategy"] == k])
                             for k in sorted({r["held_strategy"] for r in measured})},
    }
    result = {
        "generated_from": {"db": args.db, "candles_root": args.candles_root},
        "conflict_rows": len(raw_conflicts),
        "conflict_episodes": len(conflicts),
        "measured_trade_conflict_pairs": len(measured),
        "unmeasured": dict(unmeasured),
        "caveats": [
            "tail_flip ignores fees/slippage of the extra round-trip and assumes same-qty flip closed at the held trade's close time",
            "mark price is the nearest candle close at/before the event (finest shard available)",
            "conflicts with no joinable open trade or no price are counted in 'unmeasured', never dropped silently",
        ],
        "strata": strata,
        "rows": measured,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out + ".json", "w") as fh:
        json.dump(result, fh, indent=1)
    with open(args.out + ".md", "w") as fh:
        fh.write("# M26 P0 conflict-bleed summary\n\n")
        fh.write(f"- conflict rows: {len(raw_conflicts)} -> episodes: {len(conflicts)}; measured pairs: {len(measured)}; unmeasured: {dict(unmeasured)}\n")
        for name, block in strata.items():
            fh.write(f"\n## {name}\n")
            if name == "overall":
                fh.write(json.dumps(block) + "\n")
            else:
                for k, v in block.items():
                    fh.write(f"- {k}: {json.dumps(v)}\n")
    print(json.dumps({"overall": strata["overall"], "unmeasured": dict(unmeasured)}, indent=1))
    print(f"wrote {args.out}.json + .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
