#!/usr/bin/env python3
"""Replay the WHOLE entry signal on a FORMING bar (live) vs a CLOSED bar (Stage 0).

RQ-20260929-301 / RULE-RQ0929-301-WHOLE-SIGNAL-FORMING / PI-20260929-VOLSKIP-0001.

Live, the pullback and donchian variant builders call the unit's order_package
every ~2 min on a 200-bar frame whose last row is the still-forming bar, so
close, confirmation, pullback position / breakout, ADX, skip_hours and the vol
gate are all read on a partial bar, and the entry is the partial bar's last
price. The Stage-0 harnesses decide on the closed bar and enter at its close.

Arms (see the queue unit for the rule):
  A_closed     the harness as-is — Stage 0 today
  B_forming    ticks at k = 2, 4, …, tf-2 min into each bar; the LIVE unit's
               order_package (+ the builder's side_filter) on 199 closed bars +
               the bar truncated to k minutes; first firing tick enters at that
               tick's price
  A_liveframe  decomposition: the live unit on the 200 CLOSED bars ending at
               i, entry at close[i]
  A_impl       decomposition: A_liveframe's decision, entry 2 min into bar i+1
               (what a closed-bar Tier-3 change would trade)

Every override arm runs through the harness's research-only ``entry_override``
hook, so exits, the full cost stack and the position/cooldown bookkeeping are
the harness's own in every arm; only the entry decision differs.

Input: Binance USD-M 1m kline zips (a labelled proxy for Bybit), frame builder
reused from vol_skip_forming_bar_replay.py. Optional ``--live-json leg=path``
(diag audit_query pipeline_result rows) adds the live timing cross-check.
Output: one JSON evidence record.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts"))
# ROOT FIRST: scripts/ml would otherwise shadow the repo's ml package, which
# the live builder module imports (ml.datasets) — and the side_filter helpers
# are read from that module, not re-implemented.
sys.path.insert(0, str(ROOT))

import vol_skip_forming_bar_replay as vs  # noqa: E402  (frame builder + harness loaders)
sys.path.insert(0, str(ROOT))  # vs prepends scripts/ again on import
from src.runtime import entry_head_pwin  # noqa: E402

# The P_win head only ANNOTATES a package after every gate has passed (it
# never gates or sizes — see the units' order_package). Stubbed so a replay of
# ~10^5 unit calls does not score a model it cannot change the decision of.
entry_head_pwin.maybe_score_entry_pwin = lambda **_kw: None

WINDOW = 200            # live fetch_candles(limit=200)
TICK_STEP = 2           # live tick cadence, minutes
IMPL_OFFSET = 2         # A_impl: first live tick after the close, minutes
BOOT_N = 10_000
BOOT_SEED = 301

# Gate kwargs the override arms turn OFF in the harness, because the live unit
# already applied them to the decision it hands over.
_ENTRY_GATES_OFF = {
    "pullback": dict(adx_min=None, adx_max=None, min_confidence=0.0,
                     vol_skip_above_pctl=0.0, vol_skip_below_pctl=0.0,
                     skip_hours="", side_filter="both"),
    "trend": dict(adx_min=None, adx_max=None, min_confidence=0.0,
                  vol_skip_above_pctl=0.0, vol_skip_below_pctl=0.0,
                  skip_hours="", side_filter="both"),
}

LEGS: Dict[str, Dict[str, Any]] = {
    "ada_pullback_2h": {
        "base": "ada_pullback_2h", "harness": "pullback",
        "extra_kwargs": {},
    },
    "trend_donchian_xrp_4h": {
        "base": "trend_donchian_xrp_4h", "harness": "trend",
        # Declared in config/strategies.yaml and honoured by the live monitor
        # (trend_donchian.py resolve_trail_mult); the RQ-201 replay omitted it.
        "extra_kwargs": {"trail_decay_arm_r": 2.0, "trail_decay_tight_mult": 2.5},
        "extra_yaml": {"trail_decay_arm_r": 2.0, "trail_decay_tight_mult": 2.5},
    },
}


def _assert_config_exact() -> Dict[str, dict]:
    """RQ-201's drift check for the entry/exit keys it covered, plus the
    extra keys this replay adds. Returns each leg's YAML block."""
    import yaml
    vs._assert_config_exact()
    cfg = yaml.safe_load((ROOT / "config/strategies.yaml").read_text())
    cfg = cfg.get("strategies", cfg)
    out = {}
    for leg, spec in LEGS.items():
        block = cfg[leg]
        for k, v in spec.get("extra_yaml", {}).items():
            if block.get(k) is None or float(block[k]) != float(v):
                raise SystemExit(f"config drift: {leg}.{k} yaml={block.get(k)!r} "
                                 f"script={v!r} — update LEGS first")
        out[leg] = block
    return out


# ── per-minute partial-bar matrices ────────────────────────────────────────

def minute_matrices(m1: pd.DataFrame, bars: pd.DataFrame, tf_min: int) -> Dict[str, np.ndarray]:
    """Per bar (row, aligned to ``bars``) × minute-into-bar (col): the running
    high / low / last close the live fetch sees after that minute, and the
    remaining high / low from that minute on (for the entry-bar remainder)."""
    key = m1["ts"].dt.floor(f"{tf_min}min")
    row_of = pd.Series(np.arange(len(bars)), index=pd.DatetimeIndex(bars["timestamp"]))
    rows = row_of.reindex(key).to_numpy()
    ok = ~np.isnan(rows)
    r = rows[ok].astype(int)
    c = ((m1["ts"] - key).dt.total_seconds() // 60).astype(int).to_numpy()[ok]
    shape = (len(bars), tf_min)
    H = np.full(shape, np.nan)
    L = np.full(shape, np.nan)
    C = np.full(shape, np.nan)
    H[r, c] = m1["high"].to_numpy(float)[ok]
    L[r, c] = m1["low"].to_numpy(float)[ok]
    C[r, c] = m1["close"].to_numpy(float)[ok]
    run_h = np.fmax.accumulate(H, axis=1)
    run_l = np.fmin.accumulate(L, axis=1)
    last_c = pd.DataFrame(C).ffill(axis=1).to_numpy()
    rest_h = np.fmax.accumulate(H[:, ::-1], axis=1)[:, ::-1]
    rest_l = np.fmin.accumulate(L[:, ::-1], axis=1)[:, ::-1]
    return {"run_h": run_h, "run_l": run_l, "last_c": last_c,
            "rest_h": rest_h, "rest_l": rest_l}


def direction_prefilter(harness: str, bars: pd.DataFrame, block: dict,
                        close_tick: np.ndarray) -> np.ndarray:
    """EXACT necessary condition for the unit to return a package, from
    quantities that do not depend on the forming row (every channel below is
    shift(1)), evaluated at each tick's close. True = call order_package."""
    hi_s, lo_s, cl = bars["high"], bars["low"], bars["close"]
    if harness == "pullback":
        tl, pl = int(block["trend_lookback"]), int(block["pullback_lookback"])
        pf = float(block["pullback_frac"])
        mid = ((hi_s.rolling(tl).max().shift(1) + lo_s.rolling(tl).min().shift(1)) / 2).to_numpy()
        rhi = hi_s.rolling(pl).max().shift(1).to_numpy()
        rlo = lo_s.rolling(pl).min().shift(1).to_numpy()
        prev = cl.shift(1).to_numpy()
        rng = (rhi - rlo)[:, None]
        with np.errstate(invalid="ignore", divide="ignore"):
            pos = (close_tick - rlo[:, None]) / rng
            lng = (close_tick > mid[:, None]) & (pos <= pf) & (close_tick > prev[:, None])
            sht = (close_tick < mid[:, None]) & (pos >= 1 - pf) & (close_tick < prev[:, None])
        sf = str(block.get("side_filter") or "both")
        return (lng if sf != "short" else False) | (sht if sf != "long" else False)
    dn = int(block["donchian"])
    hi = hi_s.rolling(dn).max().shift(1).to_numpy()[:, None]
    lo = lo_s.rolling(dn).min().shift(1).to_numpy()[:, None]
    sf = str(block.get("side_filter") or "both")
    with np.errstate(invalid="ignore"):
        lng, sht = close_tick > hi, close_tick < lo
    return (lng if sf != "short" else False) | (sht if sf != "long" else False)


# ── live-unit evaluation (worker) ──────────────────────────────────────────

_W: Dict[str, Any] = {}


def _init_worker(leg: str, block: dict, bars: pd.DataFrame, mats: Dict[str, np.ndarray]) -> None:
    from src.runtime.strategy_signal_builders import (_resolve_side_filter,
                                                      _side_filter_suppresses)
    if LEGS[leg]["harness"] == "pullback":
        from src.units.strategies.htf_pullback_trend_2h import order_package
    else:
        from src.units.strategies.trend_donchian import order_package
    tf = str(block.get("timeframe"))
    sym = str((block.get("symbols") or [""])[0])
    _W.update(leg=leg, order_package=order_package,
              cfg={"symbol": sym, "timeframe": tf, **block, "strategy_label": leg},
              sf=_resolve_side_filter(block), supp=_side_filter_suppresses,
              base=bars[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True),
              mats=mats)


def _evaluate(frame: pd.DataFrame) -> Optional[dict]:
    """The live builder's decision on one frame: order_package, then the
    builder's side_filter. None = side 'none'."""
    try:
        pkg = _W["order_package"](dict(_W["cfg"]), candles_df=frame)
    except ValueError:
        return None
    if _W["supp"](pkg["direction"], _W["sf"]):
        return None
    return pkg


def _forming_chunk(args: Tuple[List[int], List[List[int]]]) -> Dict[int, dict]:
    idxs, ticks_per = args
    base, M = _W["base"], _W["mats"]
    out: Dict[int, dict] = {}
    for i, ticks in zip(idxs, ticks_per):
        fr = base.iloc[i - WINDOW + 1:i + 1].reset_index(drop=True).copy()
        for k in ticks:                        # ascending: first firing tick wins
            col = k - 1                        # minutes [0, k) closed at tick k
            fr.loc[WINDOW - 1, ["high", "low", "close"]] = (
                M["run_h"][i, col], M["run_l"][i, col], M["last_c"][i, col])
            pkg = _evaluate(fr)
            if pkg is None:
                continue
            rest = (M["rest_h"][i, k], M["rest_l"][i, k]) if k < M["rest_h"].shape[1] else (np.nan, np.nan)
            out[i] = {"direction": pkg["direction"], "entry": float(pkg["entry"]),
                      "atr": float(pkg["meta"]["atr"]), "tick_min": k,
                      "rest_high": None if np.isnan(rest[0]) else float(rest[0]),
                      "rest_low": None if np.isnan(rest[1]) else float(rest[1])}
            break
    return out


def _closed_chunk(idxs: List[int]) -> Dict[int, dict]:
    base = _W["base"]
    out: Dict[int, dict] = {}
    for i in idxs:
        pkg = _evaluate(base.iloc[i - WINDOW + 1:i + 1].reset_index(drop=True))
        if pkg is not None:
            out[i] = {"direction": pkg["direction"], "entry": float(pkg["entry"]),
                      "atr": float(pkg["meta"]["atr"])}
    return out


def _chunks(seq: List[Any], n: int) -> List[List[Any]]:
    return [seq[j::n] for j in range(n)] if seq else []


# ── harness runs + statistics ──────────────────────────────────────────────

def run_arm(leg: str, spec: dict, bars: pd.DataFrame, tmp: Path,
            override: Optional[Dict[int, dict]]) -> List[dict]:
    base = vs.LEGS[leg]
    mod = vs.backtest_pullback if spec["harness"] == "pullback" else vs.backtest_trend
    kw = {**base["kwargs"], **base["gate"], **spec["extra_kwargs"]}
    if override is not None:
        kw.update(_ENTRY_GATES_OFF[spec["harness"]])
        if spec["harness"] == "trend":
            kw["side_filter"] = "both"
    emit = tmp / f"{leg}.jsonl"
    mod.run_backtest(bars[["timestamp", "open", "high", "low", "close"]].copy(),
                     timeframe=base["timeframe"], symbol=base["symbol"],
                     emit_path=str(emit), strategy_name=leg,
                     entry_override=override, **kw)
    return [json.loads(x) for x in emit.read_text().splitlines() if x.strip()]


def _key(t: dict) -> Tuple[str, str]:
    return (t["entry_time"], t["direction"])


def _summ(ts: List[dict]) -> Dict[str, Any]:
    return {"n": len(ts), "net_r": round(sum(t["net_r"] for t in ts), 4),
            "gross_r": round(sum(t["gross_r"] for t in ts), 4)}


def compare(a: List[dict], b: List[dict]) -> Dict[str, Any]:
    ka, kb = {_key(t): t for t in a}, {_key(t): t for t in b}
    both = sorted(set(ka) & set(kb))
    only_a = [ka[k] for k in sorted(set(ka) - set(kb))]
    only_b = [kb[k] for k in sorted(set(kb) - set(ka))]
    union = len(set(ka) | set(kb))
    return {
        "jaccard": round(len(both) / union, 4) if union else None,
        "shared_n": len(both),
        "shared_net_r_closed": round(sum(ka[k]["net_r"] for k in both), 4),
        "shared_net_r_arm": round(sum(kb[k]["net_r"] for k in both), 4),
        "only_closed": _summ(only_a), "only_arm": _summ(only_b),
        "only_closed_entries": [(t["entry_time"], t["direction"], t["net_r"]) for t in only_a],
        "only_arm_entries": [(t["entry_time"], t["direction"], t["net_r"]) for t in only_b],
    }


def delta_stats(a: List[dict], b: List[dict], first: str, last: str) -> Dict[str, Any]:
    """ΔR = net R(b) − net R(a): calendar-month block bootstrap (months with
    no trade count as 0) and the ex-max-year sign."""
    months = pd.period_range(pd.Timestamp(first).tz_localize(None).to_period("M"),
                             pd.Timestamp(last).tz_localize(None).to_period("M"), freq="M")
    def by(ts: List[dict], f: str) -> pd.Series:
        if not ts:
            return pd.Series(dtype=float)
        s = pd.Series([t["net_r"] for t in ts],
                      index=pd.to_datetime([t["entry_time"] for t in ts], utc=True).tz_localize(None).to_period(f))
        return s.groupby(level=0).sum()
    dm = by(b, "M").reindex(months, fill_value=0.0) - by(a, "M").reindex(months, fill_value=0.0)
    d = float(dm.sum())
    rng = np.random.default_rng(BOOT_SEED)
    draws = dm.to_numpy()[rng.integers(0, len(dm), size=(BOOT_N, len(dm)))].sum(axis=1)
    lo, hi = np.percentile(draws, [5, 95])
    se = float(draws.std(ddof=1))
    dy = by(b, "Y").sub(by(a, "Y"), fill_value=0.0)
    max_year = str(dy.abs().idxmax()) if len(dy) else None
    ex = float(dy.drop(dy.abs().idxmax()).sum()) if len(dy) > 1 else None
    return {"delta_net_r": round(d, 4), "ci90": [round(float(lo), 4), round(float(hi), 4)],
            "boot_se": round(se, 4), "mde_80pct_power": round((1.645 + 0.842) * se, 4),
            "months": len(dm), "per_year_delta": {str(k): round(float(v), 4) for k, v in dy.items()},
            "max_year": max_year, "ex_max_year_delta": None if ex is None else round(ex, 4)}


def grade(j: Optional[float], ds: Dict[str, Any]) -> Dict[str, Any]:
    lo, hi = ds["ci90"]
    ex = ds["ex_max_year_delta"]
    robust_neg = hi < 0 and ex is not None and ex < 0 and abs(ds["delta_net_r"]) >= 5.0
    robust_pos = lo > 0 and ex is not None and ex > 0
    if robust_neg:
        verdict = "MOVE_LIVE_TO_CLOSED"
    elif j is not None and j < 0.80:
        verdict = "HARNESS_MODELS_FORMING"
    else:
        verdict = "NO_ACTION"
    return {"verdict": verdict, "research_flag_forming_better": bool(robust_pos),
            "underpowered": abs(ds["delta_net_r"]) < ds["mde_80pct_power"]}


def live_cross_check(rows: List[dict], bars: pd.DataFrame, tf_min: int,
                     b_ovr: Dict[int, dict], a_trades: List[dict],
                     b_trades: List[dict]) -> Dict[str, Any]:
    idx_of = {pd.Timestamp(t): i for i, t in enumerate(bars["timestamp"])}
    a_bars = {t["entry_time"]: t["direction"] for t in a_trades}
    b_bars = {t["entry_time"]: t["direction"] for t in b_trades}
    last_bar = pd.Timestamp(bars["timestamp"].iloc[-1])
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: x["logged_at_utc"]):
        ts = pd.Timestamp(r["logged_at_utc"])
        bo = ts.floor(f"{tf_min}min")
        side = {"buy": "long", "sell": "short"}.get(r.get("side"), r.get("side"))
        i = idx_of.get(bo)
        ov = b_ovr.get(i) if i is not None else None
        out.append({
            "logged_at_utc": r["logged_at_utc"], "bar_open": str(bo), "side": side,
            "live_min_into_bar": round((ts - bo).total_seconds() / 60, 1),
            "live_price": r.get("price"), "status": r.get("status"),
            "in_proxy_data": i is not None,
            "duplicate_bar": str(bo) in seen,
            "B_fires_bar": None if i is None else bool(ov and ov["direction"] == side),
            "B_tick_min": None if not ov else ov["tick_min"],
            "B_entry": None if not ov else ov["entry"],
            "B_trades_bar": None if i is None else b_bars.get(str(bo)) == side,
            "A_trades_bar": None if i is None else a_bars.get(str(bo)) == side,
        })
        seen.add(str(bo))
    uniq = [x for x in out if not x["duplicate_bar"] and x["in_proxy_data"]]
    first_live = min((pd.Timestamp(x["bar_open"]) for x in out), default=None)
    b_only = []
    if first_live is not None:
        live_bars = {x["bar_open"] for x in out}
        b_only = [(t["entry_time"], t["direction"]) for t in b_trades
                  if pd.Timestamp(t["entry_time"]) >= first_live.floor("D")
                  and pd.Timestamp(t["entry_time"]) <= last_bar
                  and t["entry_time"] not in live_bars]
    offs = sorted(x["live_min_into_bar"] for x in uniq)
    b_offs = sorted(x["B_tick_min"] for x in uniq if x["B_tick_min"] is not None)
    return {
        "live_dispatch_rows": len(out), "unique_bars": len({x["bar_open"] for x in out}),
        "unique_bars_in_proxy_data": len(uniq),
        "B_fires_same_bar_same_side": sum(1 for x in uniq if x["B_fires_bar"]),
        "B_trades_same_bar_same_side": sum(1 for x in uniq if x["B_trades_bar"]),
        "A_trades_same_bar_same_side": sum(1 for x in uniq if x["A_trades_bar"]),
        "median_live_min_into_bar": offs[len(offs) // 2] if offs else None,
        "median_B_tick_min_on_matched": b_offs[len(b_offs) // 2] if b_offs else None,
        "B_trades_in_window_without_live_dispatch": b_only,
        "rows": out,
    }


def replay_leg(leg: str, block: dict, klines_dir: str, tmp: Path, workers: int,
               live_rows: Optional[List[dict]]) -> Dict[str, Any]:
    spec, base = LEGS[leg], vs.LEGS[leg]
    tf_min = vs.TF_MIN[base["timeframe"]]
    m1 = vs.load_1m(klines_dir, base["symbol"])
    bars = vs.build_bars(m1, tf_min)
    mats = minute_matrices(m1, bars, tf_min)
    del m1
    ticks = list(range(TICK_STEP, tf_min - 1, TICK_STEP))
    close_tick = mats["last_c"][:, [k - 1 for k in ticks]]
    pre_f = direction_prefilter(spec["harness"], bars, block, close_tick)
    pre_c = direction_prefilter(spec["harness"], bars, block,
                                bars["close"].to_numpy()[:, None])[:, 0]
    rng_i = range(WINDOW - 1, len(bars) - 1)
    f_idx = [i for i in rng_i if pre_f[i].any()]
    f_ticks = [[ticks[c] for c in np.flatnonzero(pre_f[i])] for i in f_idx]
    c_idx = [i for i in rng_i if pre_c[i]]
    print(f"{leg}: {len(bars)} bars, forming candidates {len(f_idx)} bars / "
          f"{sum(map(len, f_ticks))} ticks, closed candidates {len(c_idx)}",
          file=sys.stderr, flush=True)
    with ProcessPoolExecutor(workers, initializer=_init_worker,
                             initargs=(leg, block, bars, mats)) as ex:
        b_ovr: Dict[int, dict] = {}
        for part in ex.map(_forming_chunk, list(zip(_chunks(f_idx, workers * 8),
                                                    _chunks(f_ticks, workers * 8)))):
            b_ovr.update(part)
        a_live: Dict[int, dict] = {}
        for part in ex.map(_closed_chunk, _chunks(c_idx, workers * 8)):
            a_live.update(part)
    a_impl: Dict[int, dict] = {}
    for i, ov in a_live.items():
        px = mats["last_c"][i + 1, IMPL_OFFSET - 1] if i + 1 < len(bars) else np.nan
        if not np.isnan(px):
            a_impl[i] = {**ov, "entry": float(px)}

    costs = vs._set_costs(vs.backtest_pullback if spec["harness"] == "pullback"
                          else vs.backtest_trend, base["symbol"])
    trades = {
        "A_closed": run_arm(leg, spec, bars, tmp, None),
        "B_forming": run_arm(leg, spec, bars, tmp, b_ovr),
        "A_liveframe": run_arm(leg, spec, bars, tmp, a_live),
        "A_impl": run_arm(leg, spec, bars, tmp, a_impl),
    }
    first, last = str(bars["timestamp"].iloc[WINDOW - 1]), str(bars["timestamp"].iloc[-1])
    # Population: entries on bars where a 200-bar live frame exists (every
    # arm). A_closed's harness warm-up is shorter, so its few earlier entries
    # are outside the comparison, not counted as "only closed".
    trades = {name: [t for t in ts if t["entry_time"] >= first] for name, ts in trades.items()}
    arms = {name: {**_summ(ts), "per_trade_net_r": round(sum(t["net_r"] for t in ts) / len(ts), 4) if ts else None}
            for name, ts in trades.items()}
    cmp_b = compare(trades["A_closed"], trades["B_forming"])
    ds = delta_stats(trades["A_closed"], trades["B_forming"], first, last)
    tick_hist = pd.Series([ov["tick_min"] for ov in b_ovr.values()]).describe().round(2).to_dict() if b_ovr else {}
    rec = {
        "symbol": base["symbol"], "timeframe": base["timeframe"],
        "harness_kwargs": {**base["kwargs"], **base["gate"], **spec["extra_kwargs"]},
        "costs": costs, "bars": len(bars), "first_decision_bar": first, "last_bar": last,
        "ticks_per_bar": len(ticks), "tick_minutes": [ticks[0], ticks[-1], TICK_STEP],
        "forming_signal_bars": len(b_ovr), "forming_first_tick_minutes": tick_hist,
        "entry_bar_remainder_exits_B": sum(1 for t in trades["B_forming"]
                                            if t.get("exit_time") == t["entry_time"]),
        "arms": arms,
        "A_closed_vs_B_forming": {**cmp_b, **ds},
        "A_closed_vs_A_liveframe": {k: v for k, v in compare(trades["A_closed"], trades["A_liveframe"]).items() if not k.endswith("_entries")},
        "A_closed_vs_A_impl": {**{k: v for k, v in compare(trades["A_closed"], trades["A_impl"]).items() if not k.endswith("_entries")},
                               **delta_stats(trades["A_closed"], trades["A_impl"], first, last)},
        "oos_2026": {name: _summ([t for t in ts if t["entry_time"] >= "2026-01-01"])
                     for name, ts in trades.items()},
        "grade": grade(cmp_b["jaccard"], ds),
    }
    if live_rows is not None:
        rec["live_cross_check"] = live_cross_check(live_rows, bars, tf_min, b_ovr,
                                                   trades["A_closed"], trades["B_forming"])
    return rec


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--klines-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--leg", action="append", default=None)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--live-json", action="append", default=[],
                    help="leg=path to a diag audit_query pipeline_result JSON")
    args = ap.parse_args(argv)
    blocks = _assert_config_exact()
    legs = args.leg or list(LEGS)
    live = {}
    for spec in args.live_json:
        leg, path = spec.split("=", 1)
        live[leg] = json.loads(Path(path).read_text())["rows"]
    if args.fetch:
        for leg in legs:
            vs.fetch_1m(args.klines_dir, vs.LEGS[leg]["symbol"])
    tmp = Path(args.out).with_suffix(".tmp.d")
    tmp.mkdir(parents=True, exist_ok=True)
    rec = {
        "unit": "RQ-20260929-301", "rule": "RULE-RQ0929-301-WHOLE-SIGNAL-FORMING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "Binance USD-M perp 1m klines (data.binance.vision); a "
                       "LABELLED PROXY for Bybit (api.bybit.com geo-blocked "
                       "from the research container)",
        "live_source": "diag audit_query event=pipeline_result since 2026-07-14T00:00:00Z",
        "legs": {},
    }
    for leg in legs:
        part = tmp / f"{leg}.result.json"
        if part.exists():
            rec["legs"][leg] = json.loads(part.read_text())
        else:
            rec["legs"][leg] = replay_leg(leg, blocks[leg], args.klines_dir, tmp,
                                          args.workers, live.get(leg))
            part.write_text(json.dumps(rec["legs"][leg], default=str))
        print(f"{leg}: {rec['legs'][leg]['grade']}", flush=True)
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rec, indent=1, default=str) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
