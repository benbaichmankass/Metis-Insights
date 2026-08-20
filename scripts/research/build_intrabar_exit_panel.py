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
from ml.datasets.cross_asset_features import (  # noqa: E402
    CROSS_ASSET_FEATURE_COLUMNS,
    N_PEER_SLOTS,
    compute_cross_asset_feature_rows,
)
from src.runtime.cross_asset_live import peers_for  # noqa: E402

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


def _bar_ts(rec: Dict[str, Any]) -> str:
    """The ONE timestamp form used on both sides of the peer join.

    ``_aligned_return_series`` maps peer bars onto the target grid by EXACT
    string equality, so target and peer timestamps must be normalised through
    the same function or the join silently matches nothing and every peer column
    comes back absent — which would look exactly like "this symbol has no peers".
    """
    return _iso_closed_at(rec.get("timestamp")) or ""


def cross_asset_index(
    candles: List[Dict[str, Any]],
    target_symbol: str,
    peer_series: Optional[Dict[str, List[Dict[str, Any]]]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Per-bar peer-asset features for ``target_symbol``, indexed by bar timestamp.

    Returns ``(index, meta)``. ``meta['state']`` is never collapsed:

    ``no_peers_configured``  the symbol has no row in ``config/cross_asset.yaml``
                             — 18 of 23 traded symbols, and for the twelve
                             non-crypto names that is the honest state rather
                             than a gap to paper over.
    ``no_peer_series``       peers ARE configured but no candle file was supplied
                             for any of them. **Distinct from the above**: one is
                             "nothing to join", the other is "we were asked to
                             join and could not".
    ``joined``               at least one peer series was supplied and aligned.

    ⚠️ **When the state is not ``joined`` the caller emits NO ``xa_`` columns at
    all** rather than a row of zeros. The feature block itself zero-fills an
    absent slot (with a ``present`` flag beside it since 2026-08-20), which is
    right for a model that trained on those columns — but a research panel is
    better served by the column being ABSENT, because the builder's dense-column
    filter then drops it and E2 never spends a feature slot on a constant.

    Leakage: every column the underlying function emits is past-only (its own
    invariant), reading a window that ends at bar ``t`` — the same bound the
    in-trade features use, and disjoint from the label window ``[t+1 ..]``.
    """
    peers = peers_for(target_symbol)
    if not peers:
        return {}, {"state": "no_peers_configured", "target": target_symbol,
                    "peers_configured": [], "peers_joined": [],
                    "bars_indexed": 0, "bar_coverage": None}

    supplied = peer_series or {}
    slots: List[List[Dict[str, Any]]] = []
    joined: List[str] = []
    joined_slots: List[int] = []          # 1-based, matches the xa_peer{n}_ prefix
    for i, peer in enumerate(peers[:N_PEER_SLOTS], start=1):
        rows = supplied.get(peer) or []
        slots.append(rows)
        if rows:
            joined.append(peer)
            joined_slots.append(i)
    if not joined:
        return {}, {"state": "no_peer_series", "target": target_symbol,
                    "peers_configured": list(peers[:N_PEER_SLOTS]),
                    "peers_joined": [], "bars_indexed": 0, "bar_coverage": None}

    target_rows = [{"ts": _bar_ts(c), "close": _f(c.get("close"))} for c in candles]
    try:
        xa_rows = compute_cross_asset_feature_rows(target_rows, slots)
    except Exception as exc:  # noqa: BLE001 — a peer failure must not kill the panel
        return {}, {"state": "no_peer_series", "target": target_symbol,
                    "peers_configured": list(peers[:N_PEER_SLOTS]),
                    "peers_joined": [], "bars_indexed": 0, "bar_coverage": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    index = {str(r.get("ts")): r for r in xa_rows if r.get("ts")}
    return index, {
        "state": "joined",
        "target": target_symbol,
        "peers_configured": list(peers[:N_PEER_SLOTS]),
        "peers_joined": joined,
        "joined_slots": joined_slots,
        "bars_indexed": len(index),
        # How much of the candle frame got a peer row at all. Reported beside the
        # count so a thin join is visible rather than inferred from silence.
        "bar_coverage": round(len(index) / len(candles), 4) if candles else None,
    }


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
    peer_series: Optional[Dict[str, List[Dict[str, Any]]]] = None,
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
    # E1: the EXOGENOUS half of the panel. Computed once over the whole frame
    # (the underlying function is vectorised over bars and past-only), then
    # looked up per decision bar — not recomputed per trade, which would be
    # O(trades x bars) for an identical result.
    xa_index, xa_meta = cross_asset_index(
        candles, str(adapter_opts.get("symbol") or ""), peer_series)
    # Emit ONLY the per-peer columns for slots that actually got a series. An
    # UNSUPPLIED slot emits six constant zeros plus present=0 (the feature block
    # zero-fills by design, which is right for a trained head and wrong for a
    # research panel): the dense filter drops all-NULL columns, not all-CONSTANT
    # ones, so those six would reach E2 as perfectly collinear noise. Which slots
    # exist is already in the manifest as peers_configured vs peers_joined, which
    # says strictly more than a column of zeros would.
    _xa_slots = set(xa_meta.get("joined_slots") or ())
    _xa_cols = [
        c for c in CROSS_ASSET_FEATURE_COLUMNS
        if not c.startswith("xa_peer")
        or any(c.startswith(f"xa_peer{n}_") for n in _xa_slots)
    ]
    xa_hits = 0
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
            # Exogenous peer state at THIS bar. Absent when the symbol has no
            # peers or none were supplied — the columns are then simply not
            # emitted, so the dense filter drops them rather than E2 spending a
            # slot on a constant. See cross_asset_index for the state vocabulary.
            if xa_index:
                xa = xa_index.get(_bar_ts(candles[t]))
                if xa is not None:
                    xa_hits += 1
                    for k in _xa_cols:
                        rec[f"feat_{k}"] = xa.get(k)
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

    # Constant columns are REPORTED, not dropped: constant in THIS panel is not
    # constant in general, so silently removing one would make two panels of the
    # same builder disagree on their own schema. A consumer that should not spend
    # a feature slot on a zero-variance column can read this and skip it.
    constant_feats = sorted(
        k for k in dense_feats
        if len({rec.get(k) for rec in rows if rec.get(k) is not None}) <= 1
    )
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
        # E1 exogenous half. Read `state` BEFORE reading any xa column: a panel
        # with no xa columns and state `no_peers_configured` is a symbol we never
        # had peers for, which is a different fact from `no_peer_series` (we had
        # peers and were handed no data). `row_coverage` is the share of emitted
        # rows that actually received a peer row — a partial join is visible here
        # rather than hiding inside a column that is merely mostly-populated.
        "cross_asset": {
            **xa_meta,
            "rows_with_xa": xa_hits,
            "row_coverage": round(xa_hits / len(rows), 4) if rows else None,
            "xa_feature_cols": [f"feat_{k}" for k in CROSS_ASSET_FEATURE_COLUMNS
                                if f"feat_{k}" in dense_feats],
        },
        "key_cols": list(_KEY_COLS),
        "outcome_cols": list(_OUTCOME_COLS),
        "feature_cols": feature_cols,
        "dense_feature_cols": dense_feats,
        "dropped_all_null_feature_cols": dropped_all_null,
        "constant_feature_cols": constant_feats,
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
    p.add_argument(
        "--peer-data", action="append", default=[], metavar="SYMBOL=PATH",
        help=(
            "Peer candle feed for the E1 exogenous block, repeatable "
            "(e.g. --peer-data ETHUSDT=data/eth.csv). Which peers are USED comes "
            "from config/cross_asset.yaml, not from this flag — supplying a "
            "symbol that is not a configured peer of --symbol is ignored, and a "
            "configured peer with no feed is reported in the manifest rather "
            "than silently zero-filled."
        ),
    )
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
    peer_series: Dict[str, List[Dict[str, Any]]] = {}
    for spec in args.peer_data or []:
        sym, _, path = str(spec).partition("=")
        sym, path = sym.strip(), path.strip()
        if not sym or not path:
            print(f"  note: ignoring malformed --peer-data {spec!r} (want SYMBOL=PATH)")
            continue
        try:
            from scripts.candle_io import load_candles

            pdf = load_candles(path)
            peer_series[sym] = [
                {"ts": _bar_ts(r), "close": _f(r.get("close"))}
                for r in _df_records(pdf)
            ]
        except Exception as exc:  # noqa: BLE001 — a bad peer feed must not kill the panel
            # Reported, never swallowed: an unreadable peer is not the same fact
            # as a peer that was never asked for, and the manifest's
            # `no_peer_series` state would otherwise conflate them.
            print(f"  note: peer feed {sym} unreadable ({type(exc).__name__}: {exc})")

    rows, manifest = build_intrabar_exit_panel(
        harness=args.harness, adapter_opts=adapter_opts, peer_series=peer_series,
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
        xa = manifest.get("cross_asset") or {}
        print(
            f"  cross-asset: {xa.get('state')} · configured={xa.get('peers_configured')} "
            f"joined={xa.get('peers_joined')} · rows_with_xa={xa.get('rows_with_xa')} "
            f"(row_coverage={xa.get('row_coverage')})"
        )
        if manifest["error"]:
            print(f"  note: {manifest['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
