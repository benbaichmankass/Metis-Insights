"""M18 scorer-quality probe — per-candidate (features -> forward net-R) dataset.

The sizing-normalized A/B (allocator_multisymbol_backtest.py) showed the cost-aware
EV_R scorer does NOT rank contested cross-symbol candidates any better than a dumb
symbol-priority order. Before investing in a learned ranker, this probe answers the
prior question: **is there ANY decision-time feature that separates winning from
losing candidates?** If nothing separates, no scorer can beat priority (clean
negative). If something does, it's the substrate for a ranker.

Method (NO LOOKAHEAD, one clean labelled row per taken candidate):
  For each symbol INDEPENDENTLY, walk the shared clock. Whenever an actionable
  candidate appears and the symbol holds no open position, OPEN it (single-position
  netting, exactly the baseline-independent arm), record its decision-time FEATURES,
  then let the REAL monitor()/SL/TP run it to close and record the FORWARD OUTCOME
  (net-R = pnl / risk_usd, win, bars_held, reason). risk_usd is held CONSTANT
  (fixed notional) so net-R is a pure per-trade return in R-units, balance-free.

This reuses the validated harness internals (build states, collect candidate, manage
open) — it does NOT alter the A/B harness or any live path. Tier-1 research; the only
output is the --out CSV + a printed EDA summary.

Features captured at decision time (all past-only):
  confidence (c_strat, the current P_win proxy), ev_r (current scorer), rr (reward:risk),
  stop_dist_pct, tp_dist_pct, ret_1h/4h/12h (shared-bar momentum), vol_1h, mom_align_1h
  (does 1h momentum agree with the trade side), hour_utc, dow, symbol, owner, side.
Label: net_r, pnl, win, bars_held, reason.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _SCRIPT_DIR]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.research.allocator_multisymbol_backtest import (  # noqa: E402
    _OpenPos,
    _build_sym_states,
    _collect_symbol_candidate,
    _manage_open,
    _parse_symbols_and_data,
)
from src.runtime.allocator_ev import DEFAULT_FEE_BPS_ROUNDTRIP  # noqa: E402

_FIXED_NOTIONAL_RISK_USD = 100.0  # constant risk-$ per trade -> net_r is balance-free


def _safe_ret(close: np.ndarray, i: int, k: int) -> Optional[float]:
    if i - k < 0:
        return None
    p0 = float(close[i - k])
    if p0 == 0:
        return None
    return float(close[i]) / p0 - 1.0


def _vol(close: np.ndarray, i: int, k: int) -> Optional[float]:
    if i - k < 1:
        return None
    seg = close[max(0, i - k):i + 1]
    if len(seg) < 3:
        return None
    rets = np.diff(seg) / seg[:-1]
    return float(np.std(rets))


def _features(st, i: int, cand) -> Dict[str, Any]:
    close = st.close
    entry, sl, tp = cand.entry, cand.sl, cand.tp
    stop_dist = abs(entry - sl)
    rr = (abs(tp - entry) / stop_dist) if stop_dist > 0 else None
    ts = pd.Timestamp(st.ts.iloc[i])
    ret_1h = _safe_ret(close, i, 12)
    feat: Dict[str, Any] = {
        "symbol": cand.symbol,
        "owner": cand.owner,
        "side": cand.side,
        "confidence": round(float(cand.confidence), 6),
        "ev_r": (round(float(cand.ev_r), 6) if cand.ev_r is not None else None),
        "rr": (round(rr, 6) if rr is not None else None),
        "stop_dist_pct": (round(stop_dist / entry, 6) if entry else None),
        "tp_dist_pct": (round(abs(tp - entry) / entry, 6) if entry else None),
        "ret_1h": (round(ret_1h, 6) if ret_1h is not None else None),
        "ret_4h": (lambda v: round(v, 6) if v is not None else None)(_safe_ret(close, i, 48)),
        "ret_12h": (lambda v: round(v, 6) if v is not None else None)(_safe_ret(close, i, 144)),
        "vol_1h": (lambda v: round(v, 6) if v is not None else None)(_vol(close, i, 12)),
        "hour_utc": int(ts.hour),
        "dow": int(ts.dayofweek),
    }
    if ret_1h is None:
        feat["mom_align_1h"] = None
    else:
        aligned = (ret_1h > 0 and cand.side == "long") or (ret_1h < 0 and cand.side == "short")
        feat["mom_align_1h"] = int(bool(aligned))
    return feat


def collect_dataset(
    *, symbols: List[str], data: Dict[str, str], rosters: Dict[str, List[str]],
    start, end, clock_tf: str, signal_ttl_bars: int, fee_bps: float, refresh: bool,
) -> List[Dict[str, Any]]:
    shared_ts, states = _build_sym_states(
        symbols=symbols, data=data, rosters=rosters, start=start, end=end,
        clock_tf=clock_tf, refresh=refresh)
    fee_rate = fee_bps / 10_000.0
    rows: List[Dict[str, Any]] = []
    n = len(shared_ts)
    for sym in symbols:
        st = states[sym]
        st._fee_rate = fee_rate  # type: ignore[attr-defined]
        st.latest = {}
        st.latest_idx = {}
        st.pos = None
        pending_feat: Optional[Dict[str, Any]] = None
        for i in range(n):
            cl = _manage_open(st, i)
            if cl is not None and pending_feat is not None:
                risk_usd = _FIXED_NOTIONAL_RISK_USD
                net_r = cl.pnl / risk_usd if risk_usd else None
                row = dict(pending_feat)
                row.update({
                    "entry_ts": str(cl.entry_ts), "exit_ts": str(cl.exit_ts),
                    "pnl": round(float(cl.pnl), 4),
                    "net_r": (round(float(net_r), 6) if net_r is not None else None),
                    "win": int(cl.pnl > 0), "bars_held": int(cl.bars_held),
                    "reason": cl.reason,
                })
                rows.append(row)
                pending_feat = None
            if st.pos is not None:
                continue
            cand = _collect_symbol_candidate(st, i, signal_ttl_bars, fee_bps, sym)
            if cand is None:
                continue
            stop_dist = abs(cand.entry - cand.sl)
            if stop_dist <= 0:
                continue
            qty = _FIXED_NOTIONAL_RISK_USD / stop_dist
            if qty <= 0:
                continue
            pending_feat = _features(st, i, cand)
            st.pos = _OpenPos(symbol=sym, side=cand.side, qty=qty, entry=cand.entry,
                              sl=cand.sl, tp=cand.tp, owner=cand.owner, entry_ts=st.ts.iloc[i],
                              entry_idx=i, meta=cand.meta, risk_usd=_FIXED_NOTIONAL_RISK_USD)
        # drop a still-open trailing position (no realized label) — pending_feat discarded
    return rows


# --------------------------------------------------------------------------
# EDA — does any single feature separate winners from losers?
# --------------------------------------------------------------------------
def _auc(scores: List[float], labels: List[int]) -> Optional[float]:
    """Rank AUC of `scores` predicting label==1 (Mann-Whitney). None if degenerate."""
    pairs = [(s, y) for s, y in zip(scores, labels) if s is not None and not (isinstance(s, float) and math.isnan(s))]
    if not pairs:
        return None
    pos = [s for s, y in pairs if y == 1]
    neg = [s for s, y in pairs if y == 0]
    if not pos or not neg:
        return None
    order = sorted(pairs, key=lambda t: t[0])
    ranks = {}
    k = 0
    while k < len(order):
        j = k
        while j + 1 < len(order) and order[j + 1][0] == order[k][0]:
            j += 1
        avg = (k + j) / 2.0 + 1.0  # 1-based average rank for ties
        for m in range(k, j + 1):
            ranks[m] = avg
        k = j + 1
    sum_pos_ranks = sum(ranks[idx] for idx, (s, y) in enumerate(order) if y == 1)
    n_pos, n_neg = len(pos), len(neg)
    u = sum_pos_ranks - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def _corr(xs: List[float], ys: List[float]) -> Optional[float]:
    pairs = [(x, y) for x, y in zip(xs, ys)
             if x is not None and y is not None
             and not (isinstance(x, float) and math.isnan(x))
             and not (isinstance(y, float) and math.isnan(y))]
    if len(pairs) < 5:
        return None
    xa = np.array([p[0] for p in pairs], float)
    ya = np.array([p[1] for p in pairs], float)
    if xa.std() == 0 or ya.std() == 0:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


_NUMERIC_FEATS = ["confidence", "ev_r", "rr", "stop_dist_pct", "tp_dist_pct",
                  "ret_1h", "ret_4h", "ret_12h", "vol_1h", "mom_align_1h", "hour_utc", "dow"]


def eda(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "no candidates collected — nothing to analyse"
    n = len(rows)
    wins = sum(r["win"] for r in rows)
    net_rs = [r["net_r"] for r in rows if r["net_r"] is not None]
    mean_net_r = float(np.mean(net_rs)) if net_rs else float("nan")
    L = [
        f"candidates={n}  win_rate={100*wins/n:.1f}%  mean_net_R={mean_net_r:+.3f}  "
        f"total_net_R={sum(net_rs):+.1f}",
        "",
        "per-feature signal vs forward outcome (AUC: P(feature ranks winners above losers); "
        "corr: Pearson(feature, net_R)). |AUC-0.5|>~0.03 or |corr|>~0.05 = a real edge.",
        f"  {'feature':<14}{'AUC(win)':>10}{'corr(netR)':>12}{'n':>8}",
    ]
    labels = [r["win"] for r in rows]
    for f in _NUMERIC_FEATS:
        xs = [r.get(f) for r in rows]
        auc = _auc(xs, labels)
        corr = _corr(xs, [r.get("net_r") for r in rows])
        nn = sum(1 for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x)))
        a = f"{auc:.3f}" if auc is not None else "  —  "
        c = f"{corr:+.3f}" if corr is not None else "  —  "
        L.append(f"  {f:<14}{a:>10}{c:>12}{nn:>8}")
    # per-symbol + per-owner win rates (is "owner/symbol identity" the only real signal?)
    L.append("")
    L.append("per-symbol:  " + "  ".join(
        f"{s}={100*sum(r['win'] for r in rows if r['symbol']==s)/max(1,sum(1 for r in rows if r['symbol']==s)):.0f}%"
        f"(n{sum(1 for r in rows if r['symbol']==s)})" for s in sorted({r['symbol'] for r in rows})))
    owners = sorted({r["owner"] for r in rows})
    L.append("per-owner:")
    for o in owners:
        orows = [r for r in rows if r["owner"] == o]
        ow = sum(r["win"] for r in orows)
        onet = sum(r["net_r"] for r in orows if r["net_r"] is not None)
        L.append(f"  {o:<26} n={len(orows):>4}  win={100*ow/len(orows):>5.1f}%  net_R={onet:+.1f}")
    return "\n".join(L)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="M18 scorer-quality probe: per-candidate features -> forward net-R + EDA.")
    p.add_argument("--symbols", required=True)
    p.add_argument("--data", action="append", default=[], metavar="SYM=path")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--roster", action="append", default=[], metavar="SYM=a,b,c")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--clock-tf", default="5m")
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=DEFAULT_FEE_BPS_ROUNDTRIP)
    p.add_argument("--refresh-signals", action="store_true")
    p.add_argument("--out", default=None, help="CSV path for the per-candidate rows")
    args = p.parse_args(argv[1:])

    symbols, data, rosters = _parse_symbols_and_data(args)
    rows = collect_dataset(
        symbols=symbols, data=data, rosters=rosters, start=args.start, end=args.end,
        clock_tf=args.clock_tf, signal_ttl_bars=args.signal_ttl_bars,
        fee_bps=args.fee_bps_roundtrip, refresh=args.refresh_signals)
    print(eda(rows))
    if args.out and rows:
        cols = list(rows[0].keys())
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV -> {args.out}  ({len(rows)} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
