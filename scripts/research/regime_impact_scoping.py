#!/usr/bin/env python3
# wiring: research/queue/RQ-20260930-701 via .github/workflows/research-script-run.yml
"""Regime impact SCOPING — how much could gating each leg's losing trend cells
be worth, as a function of classifier accuracy?

⚠️ THIS IS AN UPPER-BOUND SCOPING INSTRUMENT, NOT A PROMOTION CASE. It answers
"is there enough headroom on the TREND axis to justify regime work, and is the
bottleneck the classifier's accuracy?". Nothing it prints is evidence that any
cell should be authored or flipped (that needs ``regime_cell_walkforward.py``
on the real 2-D cell plus a Tier-3 packet — `.claude/skills/regime-selectivity`).

Method (registered in research/queue/RQ-20260930-701.yaml BEFORE the first run)
--------------------------------------------------------------------------------
Population: the newest committed ledger per leg under
``comms/strategy_evidence/runs/*/<leg>__trades.jsonl`` with n >= MIN_LEG_N (30)
whose symbol is a Binance-vision USDT perp (candles are fetched, never
committed). Everything else is reported ``not_attempted`` with its reason.

Label: ADX-14 (Wilder, ``src.runtime.regime.detector.wilder_adx``) of the leg's
OWN timeframe, read on the last bar CLOSED at or before ``entry_time`` (no
look-ahead); chop < 20 <= transitional < 25 <= trending (detector cut-points).
Cell = (trend label, trade direction), the granularity regime_policy.yaml gates.

Selection (TRAIN FOLDS ONLY): trades are split chronologically into K=4 equal
blocks; for each test block k in 1..3 the train set is blocks < k (expanding).
A cell is SELECTED when its train n >= MIN_CELL_N (8) and its train net R < 0.
Selection uses the TRUE offline label (ADX is computable offline).

Application: test-block trades whose PREDICTED cell is selected are dropped;
ΔnetR = -(sum of net_r of the dropped test trades). Predicted label = true
label with prob A, else a fixed confusion (``ordinal``: errors go to the
adjacent class, transitional splits chop/trending evenly; ``uniform`` as a
sensitivity arm). A in {0.55, 0.60, 0.70} and the oracle (A = 1, no noise).
>= 200 seeds (default 300); one seed = one noise draw over every trade.

Reported: ΔnetR per leg and pooled, as mean and the 5th-95th percentile band
over seeds. The pooled band also re-samples legs with replacement per seed, so
it carries between-leg heterogeneity; the per-leg band is label-noise only.

VOL axis (RQ-20260930-704): ``--axis vol --vol-labels SYMBOL=labels.jsonl`` swaps the ADX label for the live ML vol
label (``ml_vol_label_replay.py`` output), two classes, graded on the oracle arm (the head's labels ARE the runtime
read), floor 3 legs. Trend-axis defaults are unchanged.

Assumptions that make this an UPPER bound or otherwise limit it (all stated in
the verdict note): dropping a trade leaves every other trade unchanged (true
for the ledgers' fixed-bracket trades, false if a dropped entry would have
changed later position state); the TREND axis only — the VOL axis is NOT
covered (no vol labels are committed; needs a head replay or frozen edges);
legs on the same symbol share regimes and trades, so pooled legs are NOT
independent and the pooled band is optimistic; net_r is the ledger's
full-cost-stack ``net_r`` (fee + slippage + funding), backtest-priced.

Verdict ladder (pre-registered; first match wins)::

    fewer than MIN_LEGS measured                       -> no_data / could_not_measure
    A=0.70 pooled mean >= 0.03 R/trade AND p5 > 0      -> pass       (headroom_at_realistic_accuracy)
    oracle pooled mean >= 0.03 R/trade AND p5 > 0      -> indeterminate (headroom_only_with_oracle)
    otherwise                                          -> fail       (no_headroom_trend_axis)

Tier-1 research code: reads committed ledgers, fetches public candles, writes
only under ``--out``. No config, strategy, sizing or risk change.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "ops"))

MIN_LEG_N = 30
MIN_CELL_N = 8
MIN_LEGS = 5
MIN_LEGS_VOL = 3   # RULE-RQ0930-704: fewer than 3 head-covered legs -> could_not_measure
K_BLOCKS = 4
PER_TRADE_BAR = 0.03   # R per test trade
ACCURACIES = (0.55, 0.60, 0.70)
LABELS = ("chop", "transitional", "trending")
_TF_RE = re.compile(r"_(\d+)(m|h|d)$")
_BYBIT_INTERVAL = {"5m": "5", "15m": "15", "1h": "60", "2h": "120", "4h": "240", "1d": "D"}
_TF_MIN = {"5m": 5, "15m": 15, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
_PERP_SUFFIX = "USDT"


# ── population ─────────────────────────────────────────────────────────────
def leg_timeframe(leg: str, strategies: Optional[dict] = None) -> Optional[str]:
    """Timeframe from config/strategies.yaml, else the `_<n><unit>` name suffix."""
    if strategies and isinstance(strategies.get(leg), dict) and strategies[leg].get("timeframe"):
        return str(strategies[leg]["timeframe"])
    m = _TF_RE.search(leg)
    return f"{m.group(1)}{m.group(2)}" if m else None


def leg_symbol(leg: str, df: pd.DataFrame, strategies: Optional[dict] = None) -> str:
    """The ledger's own symbol, else the leg's first configured symbol, else ''."""
    if "symbol" in df and df["symbol"].notna().any():
        return str(df["symbol"].dropna().iloc[0])
    cfg = (strategies or {}).get(leg)
    syms = cfg.get("symbols") if isinstance(cfg, dict) else None
    return str(syms[0]) if syms else ""


def policy_cell_legs(policy_path: Path = REPO / "config/regime_policy.yaml") -> set:
    """Every key anywhere under the policy's cell blocks — the legs that have a cell.
    Coverage-debt annotation only: a leg absent here has NO live regime cell."""
    import yaml
    keys: set = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                keys.add(str(k))
                walk(v)
    doc = yaml.safe_load(policy_path.read_text()) or {}
    for sect in ("trending", "transitional", "chop", "trend_vol"):
        walk(doc.get(sect) or {})
    return keys


def newest_ledgers(glob_pat: str) -> Dict[str, Path]:
    """{leg: newest ledger}, newest = lexicographically last run directory.
    Skips `-control` dirs and `__B0/__M`-style variant files (different arms)."""
    out: Dict[str, Path] = {}
    for f in sorted(glob.glob(glob_pat)):
        p = Path(f)
        if p.parent.name.endswith("-control") or "__" in p.name.replace("__trades.jsonl", ""):
            continue
        out[p.name.replace("__trades.jsonl", "")] = p   # sorted => later dir overwrites
    return out


def load_ledger(path: Path) -> pd.DataFrame:
    rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    df = pd.DataFrame(rows)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    return df.sort_values("entry_time").reset_index(drop=True)


# ── labels ─────────────────────────────────────────────────────────────────
def trend_labels(candles: pd.DataFrame, entry_times: pd.Series, tf: str) -> List[Optional[str]]:
    """ADX-14 label on the last bar CLOSED at/before each entry time."""
    from src.runtime.regime.detector import wilder_adx, regime_label

    adx = wilder_adx(candles[["high", "low", "close"]].astype(float))
    close_ns = (pd.to_datetime(candles["open_time"], utc=True) + pd.Timedelta(minutes=_TF_MIN[tf])
                ).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    entry_ns = pd.to_datetime(entry_times, utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    idx = np.searchsorted(close_ns, entry_ns, side="right") - 1
    out: List[Optional[str]] = []
    for i in idx:
        lab = regime_label(float(adx.iloc[i])) if i >= 30 else "unknown"
        out.append(None if lab == "unknown" else lab)
    return out


def fetch_candles(symbol: str, tf: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    from fetch_backtest_candles import fetch_klines_binance_vision  # noqa: E402

    warm = timedelta(minutes=_TF_MIN[tf] * 60)
    rows = fetch_klines_binance_vision(symbol, _BYBIT_INTERVAL[tf],
                                       int((start - warm).timestamp() * 1000),
                                       int((end + timedelta(days=1)).timestamp() * 1000))
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["timestamp"], utc=True)
    for c in ("high", "low", "close"):
        df[c] = df[c].astype(float)
    return df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


# ── noise + walk-forward ───────────────────────────────────────────────────
def confusion(model: str, accuracy: float, k: int = 3) -> np.ndarray:
    """Row = TRUE label, col = PREDICTED. k=3: (chop, transitional, trending); k=2 (vol axis): (calm, volatile)."""
    a, e = accuracy, 1.0 - accuracy
    if k == 2:   # both noise models coincide for two classes
        return np.array([[a, e], [e, a]])
    if model == "uniform":
        return np.array([[a, e / 2, e / 2], [e / 2, a, e / 2], [e / 2, e / 2, a]])
    return np.array([[a, e, 0.0], [e / 2, a, e / 2], [0.0, e, a]])   # ordinal


def cell_ids(labels: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """int cell id = label_index*2 + (1 if short else 0); -1 when unlabelled."""
    return np.where(labels < 0, -1, labels * 2 + (direction == "short").astype(int))


def fold_plan(n: int) -> List[Tuple[np.ndarray, np.ndarray]]:
    blocks = np.array_split(np.arange(n), K_BLOCKS)
    return [(np.concatenate(blocks[:k]), blocks[k]) for k in range(1, K_BLOCKS)]


#: Selection strictness. The defaults ARE RQ-20260930-701's registered rule (train n >= 8 and train net
#: R < 0). RQ-20260930-703 re-runs the same harness with a conservative rule: --min-cell-n 20 and
#: --loss-se 1.0 (a cell is selected only when its train MEAN net R is below -1 standard error, so a
#: cell that is merely a little negative on a thin sample is not gated).
SELECTION = {"min_cell_n": MIN_CELL_N, "loss_se": 0.0}

#: Which regime axis is being scoped. Default = the trend axis (ADX-14), i.e. 701/703 exactly. ``--axis vol``
#: (RQ-20260930-704) swaps in the live ML vol label (advisory head P(volatile) >= 0.5, replayed offline by
#: ``ml_vol_label_replay.py``), two classes.
VOL_LABELS = ("calm", "volatile")
AXIS = {"name": "trend", "labels": LABELS}
AXIS_DESC = {"trend": "trend (ADX-14); vol NOT covered",
             "vol": "vol (live advisory-head P(volatile)>=0.5, in-sample replay); trend NOT covered"}


def select_cells(true_cell: np.ndarray, net_r: np.ndarray, train: np.ndarray) -> np.ndarray:
    sel = []
    for c in np.unique(true_cell[train]):
        if c < 0:
            continue
        m = train[true_cell[train] == c]
        if len(m) < SELECTION["min_cell_n"]:
            continue
        x = net_r[m]
        se = float(x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else 0.0
        if x.mean() < -SELECTION["loss_se"] * se and x.sum() < 0:
            sel.append(c)
    return np.array(sel, dtype=int)


def leg_delta(true_lab: np.ndarray, direction: np.ndarray, net_r: np.ndarray,
              pred_lab: np.ndarray) -> Tuple[float, int, int]:
    """(ΔnetR over test blocks, n test trades, n dropped) for ONE noise draw."""
    true_cell, pred_cell = cell_ids(true_lab, direction), cell_ids(pred_lab, direction)
    delta, n_test, n_drop = 0.0, 0, 0
    for train, test in fold_plan(len(net_r)):
        sel = select_cells(true_cell, net_r, train)
        drop = test[np.isin(pred_cell[test], sel)] if len(sel) else test[:0]
        delta -= float(net_r[drop].sum())
        n_test += len(test)
        n_drop += len(drop)
    return delta, n_test, n_drop


def noisy(true_lab: np.ndarray, conf: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = true_lab.copy()
    for t in range(len(conf)):
        idx = np.where(true_lab == t)[0]
        if len(idx):
            out[idx] = rng.choice(len(conf), size=len(idx), p=conf[t])
    return out


def band(x: np.ndarray) -> Dict[str, float]:
    return {"mean": round(float(np.mean(x)), 4), "p5": round(float(np.percentile(x, 5)), 4),
            "p95": round(float(np.percentile(x, 95)), 4)}


def scope(legs: Dict[str, dict], seeds: int, base_seed: int = 7) -> dict:
    """legs: {name: {true_lab, direction, net_r}} (labels as int 0..2, -1 unlabelled)."""
    names = sorted(legs)
    n_test = {k: sum(len(t) for _, t in fold_plan(len(legs[k]["net_r"]))) for k in names}
    ungated = {k: float(sum(legs[k]["net_r"][t].sum() for _, t in fold_plan(len(legs[k]["net_r"])))) for k in names}
    res: Dict[str, dict] = {}
    for model in ("ordinal", "uniform"):
        arms = {"oracle": None, **{f"A={a:.2f}": a for a in ACCURACIES}}
        for arm, a in arms.items():
            per_leg = {k: np.zeros(seeds) for k in names}
            drops = {k: np.zeros(seeds) for k in names}
            for s in range(seeds):
                rng = np.random.default_rng([base_seed, s])
                for k in names:
                    L = legs[k]
                    pred = L["true_lab"] if a is None else noisy(L["true_lab"], confusion(model, a, len(AXIS["labels"])), rng)
                    d, _, nd = leg_delta(L["true_lab"], L["direction"], L["net_r"], pred)
                    per_leg[k][s], drops[k][s] = d, nd
            # pooled: per seed, resample legs with replacement (between-leg heterogeneity)
            rs = np.random.default_rng([base_seed, 10_000])
            pooled = np.zeros(seeds)
            pooled_trade = np.zeros(seeds)
            for s in range(seeds):
                pick = rs.integers(0, len(names), size=len(names))
                num = sum(per_leg[names[i]][s] for i in pick)
                den = sum(n_test[names[i]] for i in pick)
                pooled[s], pooled_trade[s] = num, num / den
            res.setdefault(model, {})[arm] = {
                "pooled_delta_r": band(pooled), "pooled_delta_r_per_test_trade": band(pooled_trade),
                "mean_dropped_trades_total": round(float(sum(drops[k].mean() for k in names)), 2),
                "per_leg": {k: {**band(per_leg[k]), "mean_dropped": round(float(drops[k].mean()), 2)} for k in names},
            }
    return {"n_legs": len(names), "test_trades_total": int(sum(n_test.values())),
            "ungated_test_net_r_total": round(sum(ungated.values()), 3),
            "ungated_test_net_r_per_leg": {k: round(v, 3) for k, v in ungated.items()},
            "seeds": seeds, "results": res}


def grade(results: dict, n_legs: int) -> Tuple[str, str]:
    vol = AXIS["name"] == "vol"
    if n_legs < (MIN_LEGS_VOL if vol else MIN_LEGS):
        return "indeterminate", "could_not_measure"
    o = results["ordinal"]

    def ok(arm):
        b = o[arm]["pooled_delta_r_per_test_trade"]
        return b["mean"] >= PER_TRADE_BAR and b["p5"] > 0
    if vol:   # RULE-RQ0930-704: the head's own labels ARE the runtime read, so the oracle arm is the verdict
        return ("pass", "vol_axis_headroom") if ok("oracle") else ("fail", "no_headroom_vol_axis")
    if ok("A=0.70"):
        return "pass", "headroom_at_realistic_accuracy"
    if ok("oracle"):
        return "indeterminate", "headroom_only_with_oracle"
    return "fail", f"no_headroom_{AXIS['name']}_axis"


# ── driver ─────────────────────────────────────────────────────────────────
def vol_axis_labels(label_rows: Dict[str, str], entry_times: pd.Series) -> List[Optional[str]]:
    """Live-vol label ({ts: 'calm'|'volatile'|'unknown'}) of the last bar CLOSED at/before each entry.

    The label file's ``ts`` is treated as the bar OPEN and the bar width is inferred from the file's own
    median spacing, so a trade never reads a label from a bar still forming (if ``ts`` is really the
    close, this is one bar conservative, never look-ahead). A trade more than 2 bar-widths after the
    last-closed label (stale / past the file's end) is unlabelled rather than as-of-matched to a weeks-old bar.
    """
    ts = pd.to_datetime(pd.Series(list(label_rows)), utc=True).dt.tz_convert(None)
    order = np.argsort(ts.to_numpy())
    open_ns = ts.to_numpy(dtype="datetime64[ns]")[order]
    labs = np.array([label_rows[k] for k in label_rows], dtype=object)[order]
    if len(open_ns) < 2:
        return [None] * len(entry_times)
    width = np.median(np.diff(open_ns)).astype("timedelta64[ns]")
    close_ns = open_ns + width
    entry_ns = pd.to_datetime(entry_times, utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    idx = np.searchsorted(close_ns, entry_ns, side="right") - 1
    out: List[Optional[str]] = []
    for e, i in zip(entry_ns, idx):
        ok = i >= 0 and (e - close_ns[i]) <= 2 * width and labs[i] in VOL_LABELS
        out.append(str(labs[i]) if ok else None)
    return out


def load_vol_label_file(path: Path) -> Dict[str, str]:
    rows: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("ts") and r.get("vol_regime"):
                rows[str(r["ts"])] = str(r["vol_regime"])
    return rows


def build_legs(glob_pat: str, strategies: dict, candle_source: Callable[..., Optional[pd.DataFrame]],
               vol_labels: Optional[Dict[str, Dict[str, str]]] = None
               ) -> Tuple[Dict[str, dict], Dict[str, str]]:
    legs, skipped = {}, {}
    cache: Dict[Tuple[str, str], Optional[pd.DataFrame]] = {}
    for leg, path in sorted(newest_ledgers(glob_pat).items()):
        df = load_ledger(path)
        if len(df) < MIN_LEG_N:
            skipped[leg] = f"n={len(df)} < {MIN_LEG_N}"
            continue
        symbol = leg_symbol(leg, df, strategies)
        tf = leg_timeframe(leg, strategies)
        if not symbol.endswith(_PERP_SUFFIX):
            skipped[leg] = f"symbol {symbol!r} is not a Binance-vision USDT perp (no committed candle source) — not_attempted"
            continue
        if tf not in _TF_MIN:
            skipped[leg] = f"timeframe {tf!r} unresolved — not_attempted"
            continue
        if AXIS["name"] == "vol":
            rows = (vol_labels or {}).get(symbol)
            if not rows:
                skipped[leg] = f"no advisory-head vol labels for {symbol} — not_attempted"
                continue
            labs = vol_axis_labels(rows, df["entry_time"])
            idx = {name: i for i, name in enumerate(VOL_LABELS)}
            true_lab = np.array([idx.get(x, -1) for x in labs])
            if (true_lab >= 0).sum() < MIN_LEG_N:
                skipped[leg] = f"only {(true_lab >= 0).sum()} trades vol-labelled — no_data"
                continue
            legs[leg] = {"true_lab": true_lab, "direction": df["direction"].astype(str).to_numpy(),
                         "net_r": df["net_r"].astype(float).to_numpy(),
                         "meta": {"ledger": path.relative_to(REPO).as_posix() if path.is_absolute() and REPO in path.parents else str(path),
                                  "symbol": symbol, "timeframe": tf, "n": int(len(df)),
                                  "label_counts": {name: int((true_lab == i).sum()) for i, name in enumerate(VOL_LABELS)},
                                  "unlabelled": int((true_lab < 0).sum())}}
            continue
        key = (symbol, tf)
        if key not in cache:
            cache[key] = candle_source(symbol, tf, df["entry_time"].min().to_pydatetime(),
                                       df["entry_time"].max().to_pydatetime())
        candles = cache[key]
        if candles is None or candles.empty:
            skipped[leg] = f"no candles for {symbol} {tf} — no_data"
            continue
        labs = trend_labels(candles, df["entry_time"], tf)
        idx = {name: i for i, name in enumerate(LABELS)}
        true_lab = np.array([idx.get(x, -1) for x in labs])
        if (true_lab >= 0).sum() < MIN_LEG_N:
            skipped[leg] = f"only {(true_lab >= 0).sum()} trades labelled — no_data"
            continue
        legs[leg] = {"true_lab": true_lab, "direction": df["direction"].astype(str).to_numpy(),
                     "net_r": df["net_r"].astype(float).to_numpy(),
                     "meta": {"ledger": path.relative_to(REPO).as_posix() if path.is_absolute() and REPO in path.parents else str(path),
                              "symbol": symbol, "timeframe": tf, "n": int(len(df)),
                              "label_counts": {name: int((true_lab == i).sum()) for i, name in enumerate(LABELS)},
                              "unlabelled": int((true_lab < 0).sum())}}
    return legs, skipped


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--ledgers", default=str(REPO / "comms/strategy_evidence/runs/*/*__trades.jsonl"))
    ap.add_argument("--min-cell-n", type=int, default=MIN_CELL_N,
                    help="train trades a cell needs before it can be selected (701: 8)")
    ap.add_argument("--loss-se", type=float, default=0.0,
                    help="select a cell only if its train mean net R < -loss_se * SE (701: 0 = any loss)")
    ap.add_argument("--axis", choices=("trend", "vol"), default="trend",
                    help="trend = ADX-14 (701/703); vol = live ML vol label from --vol-labels (704)")
    ap.add_argument("--vol-labels", action="append", default=[], metavar="SYMBOL=labels.jsonl",
                    help="per-symbol ml_vol_label_replay.py output ({ts, vol_regime}); repeatable; --axis vol only")
    args = ap.parse_args(argv)
    if args.axis == "vol" and not args.vol_labels:
        ap.error("--axis vol requires at least one --vol-labels SYMBOL=path")
    if args.seeds < 200:
        ap.error("--seeds must be >= 200 (pre-registered floor)")
    if args.min_cell_n < 1 or args.loss_se < 0:
        ap.error("--min-cell-n must be >= 1 and --loss-se >= 0")
    SELECTION.update(min_cell_n=args.min_cell_n, loss_se=args.loss_se)
    vol_labels: Dict[str, Dict[str, str]] = {}
    if args.axis == "vol":
        AXIS.update(name="vol", labels=VOL_LABELS)
        for spec in args.vol_labels:
            sym, _, path = spec.partition("=")
            if not sym or not path:
                ap.error(f"--vol-labels expects SYMBOL=path, got {spec!r}")
            vol_labels[sym] = load_vol_label_file(Path(path))
    import yaml
    strategies = (yaml.safe_load((REPO / "config/strategies.yaml").read_text()) or {})
    strategies = strategies.get("strategies", strategies)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    legs, skipped = build_legs(args.ledgers, strategies, fetch_candles, vol_labels)
    scoped = scope(legs, args.seeds) if legs else {"n_legs": 0, "results": {}}
    v, label = grade(scoped["results"], scoped["n_legs"]) if legs else ("indeterminate", "could_not_measure")
    measured = scoped["n_legs"] >= (MIN_LEGS_VOL if AXIS["name"] == "vol" else MIN_LEGS)
    pop = (f"{scoped['n_legs']} crypto-perp legs with n>=30 in the newest committed ledger; "
           f"{len(skipped)} other ledgers not measured (reasons in measurement.skipped); {AXIS_DESC[AXIS['name']]}")
    cells = policy_cell_legs()
    for k in legs:
        legs[k]["meta"]["has_live_policy_cell"] = k in cells
    measurement = {"label": label, "scoping_only_not_a_promotion_case": True, "axis": AXIS_DESC[AXIS["name"]],
                   "selection": dict(SELECTION),
                   "bar": {"per_test_trade_r": PER_TRADE_BAR, "p5_gt": 0, "min_cell_n": SELECTION["min_cell_n"], "min_leg_n": MIN_LEG_N,
                           "min_legs": MIN_LEGS, "blocks": K_BLOCKS, "seeds": args.seeds},
                   "skipped": skipped, "legs": {k: legs[k]["meta"] for k in legs}, **scoped}
    note = (f"{label}. Upper-bound scoping, NOT a promotion case: ΔnetR = net R of dropped test-block trades negated, "
            "cells selected on earlier blocks only. Pooled legs share symbols, so the pooled band is optimistic. "
            f"{AXIS_DESC[AXIS['name']]}.")
    verdict = {"verdict": v, "read_state": "measured" if measured else "no_data", "population": pop,
               "n": scoped.get("test_trades_total"), "measurement": measurement, "note": note}
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(f"regime_impact_scoping: {label} ({v}); legs={scoped['n_legs']} skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
