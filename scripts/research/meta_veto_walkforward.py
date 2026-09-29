#!/usr/bin/env python3
"""M19 S1-v0 / S1 / S2-sizing — meta-model veto and sizing over the costed harness corpus.

WHY THIS EXISTS (post-mortem finding, docs/research/M19-ai-trader-postmortem-and-staged-path-2026-09-28.md)
------------------------------------------------------------------------------------------------------------
Every retired M19 manifest predicted a price-derived proxy label (volatility
regime, direction) and was graded on macro_f1. None was ever graded on the
net-of-cost R of a trade decision, and the one trade-outcome head had n_eval=20.
The two later attempts at a real trade-selection head (M18/T1.3 ranker, M23
meta-label) both found that the pooled signal was BETWEEN-cell base rate ("this
leg bleeds"), which the roster already answers, and never established a
WITHIN-cell veto. S1-v0 asks that question directly, on the one corpus this
repo now has that is (a) net of the FULL cost stack, (b) config-exact, and
(c) committed: the per-trade rows behind `comms/strategy_evidence/*.json`
(`comms/strategy_evidence/runs/<date>/<leg>__trades.jsonl`).

WHAT IT COMPUTES
----------------
Population: for every evidence record, the trades file beside its own
`source_run` (so each leg is counted once, from the run the record was built
from). Each row is a harness trade with `entry_time`, `exit_time`, `net_r`
(fee + slippage + funding), `confidence`, `entry`, `sl`, `direction`.

Row-level features (decision-time only, all available at `entry_time`):
  confidence, stop_dist_pct = |entry - sl| / entry, direction, hour (sin/cos),
  day-of-week, and the cell's TRAILING expectancy over trades that had already
  EXITED before this entry (`cell_trail_mean_r`, `cell_trail_winrate`,
  `cell_trail_n`) — strictly as-of, never the trade's own outcome.

Market-state features (`--arms`, `--candles-dir`, PI-20260928-VNMMNBJH-0001):
  as-of joined from the leg's own candle file at `entry_time` — returns over
  1/4/12 bars, a realized-vol percentile, Wilder ADX(14), and the trade's
  position within the trailing 20/50-bar range. `funding_bps` is read from an
  optional column on the candle file (most do not carry one) and is `None`
  (never `0.0`) when absent. The advisory regime head's `predict_proba(...)
  ["volatile"]` (regime-selectivity skill Rule 3) is NOT wired in this pass —
  a correct as-of historical replay needs the same feature builder the head
  was trained on (`ml/datasets/embedding_features.py`-adjacent), which is a
  separate build; the `p_volatile` column is carried in the schema and always
  recorded `missing` here rather than risk a wrong number entering a
  Tier-1-gating statistic. Filed as a follow-up (see the module's tail
  comment), not silently faked.

Walk-forward: trades sorted by entry_time; the first `--train-frac` is the
warm-up train set; the remainder is cut into `--folds` contiguous OOS blocks.
For fold k the model is fit on every trade whose EXIT is at least
`--embargo-days` before the block's first entry (purge + embargo), so no
overlapping label leaks in. Model: standardized logistic regression on
`won = 1[net_r > 0]`, full-batch gradient descent, stdlib only.

THE VETO STATISTIC (registered in research/queue/RQ-20260928-002.yaml and
research/queue/blocked/RQ-20260928-003.yaml)
---------------------------------------------------------------------------
Every OOS trade's net_r is DEMEANED WITHIN ITS CELL over the OOS population
(evaluation-time normalisation, applied identically to every comparator), which
removes the between-cell base rate the earlier attempts were fooled by. Then:

    veto_delta_r = mean(demeaned net_r of KEPT trades) - mean(demeaned net_r of ALL trades)

where KEPT = the trades NOT in the lowest `--veto-frac` of model scores within
each OOS fold. `--arms` computes this for THREE arms on identical folds and
demeaned values: (1) row-only (S1-v0's set), (2) row + market-state, (3)
confidence-only. A permutation null (`--perms` random vetoes of the same size
per fold) gives `perm_p` = P(random veto >= observed) for each arm.
`paired_gain` = arm(2)'s delta minus arm(1)'s delta, per RQ-20260928-003's rule.

THE SIZING STATISTIC (`--size-by-score`, research/queue/blocked/RQ-20260928-004.yaml)
---------------------------------------------------------------------------------------
Over the KEPT trades of a veto model (fit inline, or supplied pre-scored via
`--scores-file` so a landed S1/S1-v0 score is sized rather than re-fit), assign
budget-neutral tercile weights (0.5x / 1.0x / 1.5x, re-normalised per fold so
the sum of weights equals the kept count) by within-fold score rank, and
compare to (a) flat 1.0x sizing and (b) the live conviction-sizing rule's own
weight shape (`src/runtime/conviction_sizing.py::compute_conviction_sizing` —
linear in `confidence`, clipped to [0, 1]; `NO_TRADE_FLOOR` is 0.0 today, so no
trade is excluded by the floor, only re-weighted). `dd_ratio` compares the
per-fold max drawdown of the sized cumulative-R path to the flat one. A
permutation null shuffles tercile *labels* among the same kept trades.

`--census` reports the population (legs, rows, folds) and computes NO statistic:
that is how a unit measures its n BEFORE registering a rule against it.

Tier-1 / offline / read-only: reads committed files (and, with `--candles-dir`,
locally-resolved candle files via the canonical resolver — never an implicit
default). Prints JSON, writes nothing unless `--json` or `--emit-result` is
given. No config, no registry, no order path, no VM.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import glob
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EVIDENCE_DIR = os.path.join(ROOT, "comms", "strategy_evidence")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FEATURES = ["confidence", "stop_dist_pct", "is_long", "hour_sin", "hour_cos", "dow",
            "cell_trail_mean_r", "cell_trail_winrate", "cell_trail_n"]

#: As-of, decision-time market state joined from the leg's own candle file.
#: `funding_bps` and `p_volatile` are frequently `None` (missing, never 0.0) —
#: see the module docstring.
MARKET_FEATURES = ["ret_1", "ret_4", "ret_12", "rv_pctile", "adx", "dist_20", "dist_50",
                    "funding_bps", "p_volatile"]

#: Bars of trailing history the market-state join needs before it will emit
#: anything (ADX(14) and the 50-bar range are the binding ones). Below this a
#: trade's market-state block is entirely `None` — never partially fabricated.
MARKET_STATE_MIN_HISTORY = 51

#: Confidence sizing weight below which the live rule assigns no would-be size
#: (`src/runtime/conviction_sizing.py::NO_TRADE_FLOOR`). Read here, not
#: re-declared, so the comparator cannot drift from the live rule's constant.
_NO_TRADE_FLOOR = 0.0


# ---------------------------------------------------------------------------
# loading — per-trade rows
# ---------------------------------------------------------------------------

def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def trades_files_from_evidence(evidence_dir: str) -> Tuple[Dict[str, dict], List[Tuple[str, str]]]:
    """leg -> {trades path, symbol, timeframe}. Second item: legs skipped, with why.

    Carries `symbol`/`timeframe` (read straight off the evidence record) so a
    caller can resolve that leg's own candle file — ``trades_files_from_evidence``
    stays the ONE place that reads the evidence records, so the candle join
    cannot drift onto a different leg->symbol mapping than the trades themselves.
    """
    out: Dict[str, dict] = {}
    skipped: List[Tuple[str, str]] = []
    for f in sorted(glob.glob(os.path.join(evidence_dir, "*.json"))):
        leg = os.path.basename(f)[:-5]
        try:
            rec = json.load(open(f))
        except (OSError, ValueError) as e:  # unreadable is reported, never dropped silently
            skipped.append((leg, f"unreadable record: {e}"))
            continue
        src = rec.get("source_run") or ""
        if isinstance(src, dict):
            src = src.get("path") or ""
        if not src:
            skipped.append((leg, f"no source_run (coverage_state={rec.get('coverage_state')!r})"))
            continue
        tp = os.path.join(ROOT, src.replace("__bt.json", "__trades.jsonl"))
        if not os.path.exists(tp):
            skipped.append((leg, f"trades file missing beside source_run: {src}"))
            continue
        out[leg] = {"trades_path": tp, "symbol": rec.get("symbol"), "timeframe": rec.get("timeframe")}
    return out, skipped


def load_rows(files: Dict[str, dict]) -> Tuple[List[dict], int]:
    rows: List[dict] = []
    unreadable = 0
    for leg, info in files.items():
        path = info["trades_path"] if isinstance(info, dict) else info
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            et, xt = _parse_ts(str(d.get("entry_time", ""))), _parse_ts(str(d.get("exit_time", "")))
            nr = d.get("net_r")
            if et is None or xt is None or not isinstance(nr, (int, float)):
                unreadable += 1
                continue
            entry, sl = d.get("entry"), d.get("sl")
            sd = (abs(float(entry) - float(sl)) / float(entry)
                  if isinstance(entry, (int, float)) and isinstance(sl, (int, float)) and entry else None)
            conf = d.get("confidence")
            rows.append({
                "cell": leg, "entry_time": et, "exit_time": xt, "net_r": float(nr),
                "confidence": float(conf) if isinstance(conf, (int, float)) else None,
                "stop_dist_pct": sd,
                "is_long": 1.0 if str(d.get("direction", "")).lower() == "long" else 0.0,
                "hour_sin": math.sin(2 * math.pi * et.hour / 24.0),
                "hour_cos": math.cos(2 * math.pi * et.hour / 24.0),
                "dow": float(et.weekday()),
            })
    rows.sort(key=lambda r: r["entry_time"])
    return rows, unreadable


def add_trailing_cell_stats(rows: List[dict]) -> None:
    """As-of cell expectancy: only trades of the same cell that EXITED before this entry."""
    by_cell: Dict[str, List[dict]] = {}
    for r in rows:
        by_cell.setdefault(r["cell"], []).append(r)
    for cell, rs in by_cell.items():
        exits = sorted(rs, key=lambda r: r["exit_time"])
        for r in rs:
            prior = [p["net_r"] for p in exits if p["exit_time"] < r["entry_time"]]
            n = len(prior)
            r["cell_trail_n"] = float(n)
            r["cell_trail_mean_r"] = (sum(prior) / n) if n else 0.0
            r["cell_trail_winrate"] = (sum(1 for p in prior if p > 0) / n) if n else 0.0


# ---------------------------------------------------------------------------
# market-state candle join (PI-20260928-VNMMNBJH-0001)
# ---------------------------------------------------------------------------

def load_candle_rows(path: str) -> List[Tuple[datetime, float, float, float, float, float, Optional[float]]]:
    """(ts, open, high, low, close, volume, funding_bps_or_None), sorted ascending.

    `funding_bps` is read from an optional column of that name; absent on
    every candle file this harness has seen so far, which is why it is never
    fabricated as 0.0 — see the module docstring.
    """
    out: List[Tuple[datetime, float, float, float, float, float, Optional[float]]] = []
    with open(path, newline="") as fh:
        r = csv.DictReader(fh)
        has_funding = r.fieldnames is not None and "funding_bps" in r.fieldnames
        for row in r:
            ts = _parse_ts(row.get("timestamp", ""))
            if ts is None:
                continue
            try:
                o, h, lo, c = (float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"]))
                v = float(row.get("volume") or 0.0)
            except (KeyError, ValueError):
                continue
            fb = None
            if has_funding:
                raw = row.get("funding_bps")
                if raw not in (None, ""):
                    try:
                        fb = float(raw)
                    except ValueError:
                        fb = None
            out.append((ts, o, h, lo, c, v, fb))
    out.sort(key=lambda t: t[0])
    return out


def _wilder_adx(closes: Sequence[float], highs: Sequence[float], lows: Sequence[float],
                i: int, period: int = 14) -> Optional[float]:
    """ADX(period) as of bar i (inclusive), Wilder's smoothing. None if not enough history."""
    need = period * 2  # one period to seed the smoothed averages, one to smooth DX into ADX
    if i < need:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for k in range(i - need + 1, i + 1):
        if k == 0:
            trs.append(highs[k] - lows[k])
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue
        up_move = highs[k] - highs[k - 1]
        down_move = lows[k - 1] - lows[k]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        trs.append(max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1])))

    def _wilder_smooth(vals: List[float]) -> List[float]:
        sm = [sum(vals[:period])]
        for v in vals[period:]:
            sm.append(sm[-1] - sm[-1] / period + v)
        return sm

    atr = _wilder_smooth(trs)
    pdm = _wilder_smooth(plus_dm)
    mdm = _wilder_smooth(minus_dm)
    dxs = []
    for a, p, m in zip(atr, pdm, mdm):
        if a <= 0:
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        denom = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)
    if len(dxs) < period:
        return None
    return sum(dxs[-period:]) / period


def _realized_vol_pctile(closes: Sequence[float], i: int, window: int = 20) -> Optional[float]:
    """Percentile rank (0-1) of the trailing `window`-bar realized vol as of bar i,
    against the EXPANDING history of that same rolling-vol series up to bar i.
    Strictly as-of: only bars <= i ever contribute."""
    if i < window * 2:
        return None
    rets = [math.log(closes[k] / closes[k - 1]) for k in range(1, i + 1) if closes[k - 1] > 0]
    if len(rets) < window * 2:
        return None
    rolling = []
    for k in range(window - 1, len(rets)):
        seg = rets[k - window + 1:k + 1]
        mu = sum(seg) / window
        rolling.append(math.sqrt(sum((x - mu) ** 2 for x in seg) / window))
    if not rolling:
        return None
    current = rolling[-1]
    below = sum(1 for v in rolling if v <= current)
    return below / len(rolling)


def _range_distance(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                    i: int, window: int) -> Optional[float]:
    """Position (0-1) of close[i] within the trailing `window`-bar high/low range."""
    if i < window:
        return None
    hi = max(highs[i - window + 1:i + 1])
    lo = min(lows[i - window + 1:i + 1])
    if hi <= lo:
        return None
    return (closes[i] - lo) / (hi - lo)


def market_state_asof(candles: Sequence[Tuple[datetime, float, float, float, float, float, Optional[float]]],
                      ts_sorted: Sequence[datetime], entry_time: datetime) -> Dict[str, Optional[float]]:
    """As-of market-state features at `entry_time`, using only candles closed at or
    before it. `ts_sorted` is `[c[0] for c in candles]`, passed in so a caller
    scoring many trades against one candle series does not re-derive it per trade."""
    i = bisect.bisect_right(ts_sorted, entry_time) - 1
    out: Dict[str, Optional[float]] = {k: None for k in MARKET_FEATURES}
    if i < MARKET_STATE_MIN_HISTORY - 1:
        return out
    closes = [c[4] for c in candles[:i + 1]]
    highs = [c[2] for c in candles[:i + 1]]
    lows = [c[3] for c in candles[:i + 1]]

    def _ret(n: int) -> Optional[float]:
        if i - n < 0 or closes[i - n] <= 0 or closes[i] <= 0:
            return None
        return math.log(closes[i] / closes[i - n])

    out["ret_1"] = _ret(1)
    out["ret_4"] = _ret(4)
    out["ret_12"] = _ret(12)
    out["rv_pctile"] = _realized_vol_pctile(closes, i, window=20)
    out["adx"] = _wilder_adx(closes, highs, lows, i, period=14)
    out["dist_20"] = _range_distance(highs, lows, closes, i, window=20)
    out["dist_50"] = _range_distance(highs, lows, closes, i, window=50)
    out["funding_bps"] = candles[i][6]
    out["p_volatile"] = None  # never wired this pass — see module docstring
    return out


def _resample_candles(rows: list, target_tf: str) -> Optional[list]:
    """Resample finer-grain `rows` (as `load_candle_rows` returns them) up to
    `target_tf`, reusing `scripts/backtest_trend.py::_resample` — the SAME
    aggregation (`label="right", closed="right"`, OHLC agg) every backtest
    harness in this repo already runs on, imported rather than re-derived
    (RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED: a second resample
    implementation could disagree with the canonical one on a bucket
    boundary and nothing would catch it). `None` on any failure (pandas
    unavailable, import failure) — the caller skips the leg rather than
    silently reading finer-grain bars as if they were the coarser leg's own,
    which would compute every as-of feature on the wrong bar size."""
    try:
        import importlib.util
        import pandas as pd  # noqa: F401 -- import-checked here so the except below is honest
        p = os.path.join(ROOT, "scripts", "backtest_trend.py")
        spec = importlib.util.spec_from_file_location("_backtest_trend_for_resample", p)
        mod = importlib.util.module_from_spec(spec)
        # Registered in sys.modules BEFORE exec: backtest_trend.py declares
        # @dataclass classes, and dataclass's own field-type resolution looks
        # the defining module up via `sys.modules[cls.__module__]` — which is
        # None (AttributeError) for a module executed without ever being
        # registered under its own __name__.
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        df = pd.DataFrame(
            [{"timestamp": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
             for r in rows])
        out = mod._resample(df, target_tf)
    except Exception:  # noqa: BLE001  # allow-silent: fail CLOSED to "cannot resample" (pandas/import unavailable, malformed frame) -- the caller skips the leg with a named reason rather than reading finer-grain bars as this leg's own; never re-raised because this must never break the walk-forward run over one leg's optional feature
        return None
    return [(row.timestamp.to_pydatetime(), float(row.open), float(row.high),
            float(row.low), float(row.close), 0.0, None) for row in out.itertuples()]


def resolve_candles_for_legs(cell_symtf: Dict[str, Tuple[Optional[str], Optional[str]]],
                             candles_dir: str) -> Tuple[Dict[str, list], Dict[str, List[datetime]], List[Tuple[str, str]]]:
    """cell -> candles, cell -> [ts...] (parallel, for bisect), and (cell, why) for
    every cell whose candle file could not be resolved. Resolution is via the
    canonical `(symbol, timeframe) -> file` resolver — never an implicit
    default; a cell with no symbol/timeframe on its evidence record, no file
    for that pair, or a resample the resolver calls for but this pass cannot
    perform (pandas unavailable) is a named skip, not a silent empty join or
    a silent read of the wrong bar size."""
    from scripts.ops import backtest_data_source as bds

    candles: Dict[str, list] = {}
    ts_index: Dict[str, List[datetime]] = {}
    skipped: List[Tuple[str, str]] = []
    # Two small caches keyed by (symbol, timeframe), separate from `candles`
    # (which is keyed by CELL): several cells legitimately share one pair
    # (e.g. two accounts trading the same leg), and this is what lets them
    # share one resolve + one parsed/resampled series instead of repeating either.
    resolved_by_symtf: Dict[Tuple[str, str], Any] = {}
    reason_by_symtf: Dict[Tuple[str, str], str] = {}
    for cell, (symbol, timeframe) in cell_symtf.items():
        if not symbol or not timeframe:
            skipped.append((cell, f"evidence record carries no symbol/timeframe "
                                  f"(symbol={symbol!r} timeframe={timeframe!r})"))
            continue
        key = (str(symbol), str(timeframe))
        if key not in resolved_by_symtf:
            res = bds.resolve_or_refuse(symbol, timeframe, None, data_dir=candles_dir)
            if not res.ok:
                resolved_by_symtf[key] = None
                reason_by_symtf[key] = bds.refusal_message(res, harness="meta_veto_walkforward")
            else:
                base_rows = load_candle_rows(res.path)
                if res.resample:
                    rs = _resample_candles(base_rows, res.resample)
                    if rs is None:
                        resolved_by_symtf[key] = None
                        reason_by_symtf[key] = (
                            f"{res.path} is a finer grain than {timeframe} and the resolver called "
                            f"for a resample to {res.resample!r}, but this run could not perform it "
                            f"(pandas/backtest_trend import unavailable) -- never reading the finer "
                            f"bars as if they were this leg's own {timeframe} bars")
                    else:
                        resolved_by_symtf[key] = rs
                else:
                    resolved_by_symtf[key] = base_rows
        series = resolved_by_symtf[key]
        if series is None:
            skipped.append((cell, reason_by_symtf.get(key, f"no candle file resolved for {key}")))
            continue
        candles[cell] = series
        ts_index[cell] = [c[0] for c in series]
    return candles, ts_index, skipped


def attach_market_state(rows: List[dict], candles_dir: str,
                        cell_symtf: Dict[str, Tuple[Optional[str], Optional[str]]]) -> dict:
    """Mutates `rows` in place, adding MARKET_FEATURES + `market_state_available`.
    Returns a census dict: which cells resolved, which did not, and why."""
    candles, ts_index, skipped = resolve_candles_for_legs(cell_symtf, candles_dir)
    n_available = 0
    for r in rows:
        cell = r["cell"]
        if cell in ts_index:
            ms = market_state_asof(candles[cell], ts_index[cell], r["entry_time"])
        else:
            ms = {k: None for k in MARKET_FEATURES}
        r.update(ms)
        avail = all(ms[k] is not None for k in MARKET_FEATURES if k not in ("funding_bps", "p_volatile"))
        r["market_state_available"] = avail
        if avail:
            n_available += 1
    return {
        "candles_dir": candles_dir,
        "cells_resolved": sorted(set(ts_index)),
        "cells_skipped": [{"cell": c, "why": w} for c, w in skipped],
        "rows_with_market_state": n_available,
        "rows_total": len(rows),
        "p_volatile_wired": False,
    }


# ---------------------------------------------------------------------------
# model — standardized logistic regression, full-batch GD (stdlib)
# ---------------------------------------------------------------------------

def _matrix(rows: Sequence[dict], features: Sequence[str] = FEATURES) -> List[List[float]]:
    return [[(r.get(f) if r.get(f) is not None else 0.0) for f in features] for r in rows]


class Logit:
    def __init__(self, l2: float = 1e-3, steps: int = 400, lr: float = 0.1):
        self.l2, self.steps, self.lr = l2, steps, lr
        self.mu: List[float] = []
        self.sd: List[float] = []
        self.w: List[float] = []
        self.b = 0.0

    def fit(self, X: List[List[float]], y: List[int]) -> "Logit":
        n, k = len(X), len(X[0])
        self.mu = [sum(row[j] for row in X) / n for j in range(k)]
        self.sd = [math.sqrt(sum((row[j] - self.mu[j]) ** 2 for row in X) / n) or 1.0 for j in range(k)]
        Z = [[(row[j] - self.mu[j]) / self.sd[j] for j in range(k)] for row in X]
        self.w, self.b = [0.0] * k, 0.0
        for _ in range(self.steps):
            gw, gb = [0.0] * k, 0.0
            for z, t in zip(Z, y):
                p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, sum(wj * zj for wj, zj in zip(self.w, z)) + self.b))))
                e = p - t
                gb += e
                for j in range(k):
                    gw[j] += e * z[j]
            self.w = [wj - self.lr * (gw[j] / n + self.l2 * wj) for j, wj in enumerate(self.w)]
            self.b -= self.lr * gb / n
        return self

    def score(self, X: List[List[float]]) -> List[float]:
        out = []
        for row in X:
            z = sum(self.w[j] * (row[j] - self.mu[j]) / self.sd[j] for j in range(len(row))) + self.b
            out.append(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z)))))
        return out


def auc(scores: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    pos = [s for s, lab in zip(scores, labels) if lab == 1]
    neg = [s for s, lab in zip(scores, labels) if lab == 0]
    if not pos or not neg:
        return None
    # rank-based, ties count half
    allv = sorted(set(scores))
    rank = {v: i for i, v in enumerate(allv)}
    wins = 0.0
    neg_sorted = sorted(rank[s] for s in neg)
    for s in pos:
        r = rank[s]
        lo = bisect.bisect_left(neg_sorted, r)
        hi = bisect.bisect_right(neg_sorted, r)
        wins += lo + 0.5 * (hi - lo)
    return wins / (len(pos) * len(neg))


# ---------------------------------------------------------------------------
# walk-forward + veto statistic
# ---------------------------------------------------------------------------

def make_folds(rows: List[dict], folds: int, train_frac: float) -> List[List[int]]:
    n = len(rows)
    start = int(n * train_frac)
    idx = list(range(start, n))
    size = max(1, len(idx) // folds)
    blocks = [idx[i * size:(i + 1) * size] for i in range(folds)]
    if len(idx) > folds * size:
        blocks[-1].extend(idx[folds * size:])
    return [b for b in blocks if b]


def veto_delta(demeaned: Sequence[float], keep: Sequence[bool]) -> Optional[float]:
    kept = [d for d, k in zip(demeaned, keep) if k]
    if not kept or len(kept) == len(demeaned):
        return None
    return sum(kept) / len(kept) - sum(demeaned) / len(demeaned)


def veto_mask(scores: Sequence[float], frac: float) -> List[bool]:
    """Keep everything except the lowest `frac` of scores (ties broken by order)."""
    n = len(scores)
    k = int(round(n * frac))
    order = sorted(range(n), key=lambda i: (scores[i], i))
    drop = set(order[:k])
    return [i not in drop for i in range(n)]


def _fold_blocks(rows: List[dict], folds: int, train_frac: float,
                 embargo_days: int, min_train: int) -> Tuple[List[List[int]], List[dict]]:
    """The fold structure ALONE (which OOS index belongs to which fold, and its
    training index set) — computed once, shared by every arm so `--arms`
    genuinely scores identical folds rather than three independent splits."""
    blocks = make_folds(rows, folds, train_frac)
    fold_defs = []
    for fi, block in enumerate(blocks):
        first_entry = rows[block[0]]["entry_time"]
        cutoff = first_entry - timedelta(days=embargo_days)
        train = [i for i in range(block[0]) if rows[i]["exit_time"] <= cutoff]
        if len(train) < min_train:
            fold_defs.append({"fold": fi, "state": "skipped_insufficient_train",
                              "n_train": len(train), "block": block})
            continue
        y = [1 if rows[i]["net_r"] > 0 else 0 for i in train]
        if len(set(y)) < 2:
            fold_defs.append({"fold": fi, "state": "skipped_single_class_train",
                              "n_train": len(train), "block": block})
            continue
        fold_defs.append({"fold": fi, "state": "scored", "n_train": len(train),
                          "n_test": len(block), "block": block, "train": train,
                          "test_from": rows[block[0]]["entry_time"].isoformat(),
                          "test_to": rows[block[-1]]["entry_time"].isoformat()})
    return blocks, fold_defs


def _score_arm(rows: List[dict], fold_defs: List[dict], features: Sequence[str]) -> Tuple[
        List[int], List[float], List[int]]:
    """Fit+score one feature set over the SAME fold structure. Returns
    (oos_idx, scores, fold_of) in fold order."""
    oos_idx: List[int] = []
    scores: List[float] = []
    fold_of: List[int] = []
    for fd in fold_defs:
        if fd["state"] != "scored":
            continue
        X = _matrix([rows[i] for i in fd["train"]], features)
        y = [1 if rows[i]["net_r"] > 0 else 0 for i in fd["train"]]
        m = Logit().fit(X, y)
        s = m.score(_matrix([rows[i] for i in fd["block"]], features))
        oos_idx.extend(fd["block"])
        scores.extend(s)
        fold_of.extend([fd["fold"]] * len(fd["block"]))
    return oos_idx, scores, fold_of


def _veto_stat_for_scores(dem: List[float], scores: List[float], fold_of: List[int],
                          veto_frac: float, perms: int, rng: random.Random) -> dict:
    keep_all: List[bool] = [True] * len(scores)
    deltas: List[Optional[float]] = []
    for fi in sorted(set(fold_of)):
        ids = [k for k, f in enumerate(fold_of) if f == fi]
        mask = veto_mask([scores[k] for k in ids], veto_frac)
        for k, keep in zip(ids, mask):
            keep_all[k] = keep
        deltas.append(veto_delta([dem[k] for k in ids], mask))
    delta = veto_delta(dem, keep_all)
    null: List[float] = []
    for _ in range(perms):
        rs = [rng.random() for _ in scores]
        keep_p: List[bool] = [True] * len(scores)
        for fi in sorted(set(fold_of)):
            ids = [k for k, f in enumerate(fold_of) if f == fi]
            mask = veto_mask([rs[k] for k in ids], veto_frac)
            for k, keep in zip(ids, mask):
                keep_p[k] = keep
        d = veto_delta(dem, keep_p)
        if d is not None:
            null.append(d)
    perm_p = (sum(1 for d in null if d >= (delta or 0.0)) + 1) / (len(null) + 1) if null else None
    return {
        "veto_delta_r": delta, "fold_deltas": deltas,
        "folds_scored": len([d for d in deltas if d is not None]),
        "folds_positive": sum(1 for d in deltas if d is not None and d > 0),
        "perm_p": perm_p, "perms": len(null), "keep_mask": keep_all,
    }


def run(rows: List[dict], folds: int, train_frac: float, embargo_days: int, veto_frac: float,
        perms: int, seed: int, min_train: int = 50, arms: bool = False) -> dict:
    add_trailing_cell_stats(rows)
    blocks, fold_defs = _fold_blocks(rows, folds, train_frac, embargo_days, min_train)
    fold_notes = [{k: v for k, v in fd.items() if k != "train"} for fd in fold_defs]
    for fn in fold_notes:
        fn.pop("block", None)
    rng = random.Random(seed)

    oos_idx, model_scores, fold_of = _score_arm(rows, fold_defs, FEATURES)
    if not oos_idx:
        return {"read_state": "no_data", "n_oos": 0, "folds": fold_notes}
    conf_scores = [(rows[i]["confidence"] if rows[i]["confidence"] is not None else 0.0) for i in oos_idx]

    net = [rows[i]["net_r"] for i in oos_idx]
    cells = [rows[i]["cell"] for i in oos_idx]
    cell_mean: Dict[str, float] = {}
    for c in set(cells):
        v = [n for n, cc in zip(net, cells) if cc == c]
        cell_mean[c] = sum(v) / len(v)
    dem = [n - cell_mean[c] for n, c in zip(net, cells)]
    won = [1 if n > 0 else 0 for n in net]
    dem_pos = [1 if d > 0 else 0 for d in dem]

    model_stat = _veto_stat_for_scores(dem, model_scores, fold_of, veto_frac, perms, rng)
    conf_stat = _veto_stat_for_scores(dem, conf_scores, fold_of, veto_frac, perms, rng)
    keep_model = model_stat.pop("keep_mask")
    conf_stat.pop("keep_mask", None)

    out = {
        "read_state": "measured",
        "n_oos": len(oos_idx),
        "n_vetoed": sum(1 for k in keep_model if not k),
        "n_cells_oos": len(cell_mean),
        "veto_frac": veto_frac,
        "veto_delta_r": model_stat["veto_delta_r"],
        "fold_deltas": model_stat["fold_deltas"],
        "folds_scored": model_stat["folds_scored"],
        "folds_positive": model_stat["folds_positive"],
        "perm_p": model_stat["perm_p"],
        "perms": model_stat["perms"],
        "auc_oos_won": auc(model_scores, won),
        "auc_oos_demeaned_pos": auc(model_scores, dem_pos),
        "baseline_confidence": {"veto_delta_r": conf_stat["veto_delta_r"],
                                "fold_deltas": conf_stat["fold_deltas"],
                                "perm_p": conf_stat["perm_p"], "auc_oos_won": auc(conf_scores, won)},
        "take_all_mean_net_r_oos": sum(net) / len(net),
        "kept_mean_net_r_oos": (sum(n for n, k in zip(net, keep_model) if k) / max(1, sum(keep_model))),
        "features": FEATURES,
        "folds": fold_notes,
        "oos_idx": oos_idx,
        "keep_model": keep_model,
        "dem": dem,
        "fold_of": fold_of,
    }

    if arms:
        market_feats = FEATURES + MARKET_FEATURES
        _, market_scores, market_fold_of = _score_arm(rows, fold_defs, market_feats)
        assert market_fold_of == fold_of, "arms must share the identical fold structure"
        market_stat = _veto_stat_for_scores(dem, market_scores, fold_of, veto_frac, perms, rng)
        market_stat.pop("keep_mask", None)
        paired_gain_folds = [
            (m - r) if (m is not None and r is not None) else None
            for m, r in zip(market_stat["fold_deltas"], model_stat["fold_deltas"])
        ]
        paired_gain = ((market_stat["veto_delta_r"] - model_stat["veto_delta_r"])
                       if market_stat["veto_delta_r"] is not None and model_stat["veto_delta_r"] is not None
                       else None)
        out["arms"] = {
            "row_only": {"veto_delta_r": model_stat["veto_delta_r"], "perm_p": model_stat["perm_p"],
                        "folds_positive": model_stat["folds_positive"], "fold_deltas": model_stat["fold_deltas"],
                        "auc_oos_won": auc(model_scores, won)},
            "row_plus_market": {"veto_delta_r": market_stat["veto_delta_r"], "perm_p": market_stat["perm_p"],
                                "folds_positive": market_stat["folds_positive"],
                                "fold_deltas": market_stat["fold_deltas"], "auc_oos_won": auc(market_scores, won)},
            "confidence_only": {"veto_delta_r": conf_stat["veto_delta_r"], "perm_p": conf_stat["perm_p"],
                                "folds_positive": conf_stat["folds_positive"],
                                "fold_deltas": conf_stat["fold_deltas"], "auc_oos_won": auc(conf_scores, won)},
            "paired_gain": paired_gain,
            "paired_gain_fold_deltas": paired_gain_folds,
            "paired_gain_folds_positive": sum(1 for d in paired_gain_folds if d is not None and d > 0),
            "market_features": MARKET_FEATURES,
        }
    return out


# ---------------------------------------------------------------------------
# sizing statistic (--size-by-score, RQ-20260928-004)
# ---------------------------------------------------------------------------

def _clip01(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return max(0.0, min(1.0, v))


def _tercile_weights(scores: Sequence[float]) -> List[float]:
    """Budget-neutral tercile weights {0.5, 1.0, 1.5} by within-fold score rank,
    re-normalised so the weights sum to len(scores) (flat sizing's own total)."""
    n = len(scores)
    order = sorted(range(n), key=lambda i: (scores[i], i))
    raw = [0.0] * n
    third = n / 3.0
    for rank, i in enumerate(order):
        if rank < third:
            raw[i] = 0.5
        elif rank < 2 * third:
            raw[i] = 1.0
        else:
            raw[i] = 1.5
    total = sum(raw)
    if total <= 0:
        return [1.0] * n
    scale = n / total
    return [w * scale for w in raw]


def _conviction_weights(rows_kept: Sequence[dict]) -> List[float]:
    """The live sizing rule's own shape (`conviction_sizing.compute_conviction_sizing`):
    linear in `confidence`, clipped to [0, 1]; `NO_TRADE_FLOOR` (0.0 today) would
    zero anything below it. Missing `confidence` gets the fold's own mean
    conviction (a documented neutral fallback for this comparator only — the
    live rule instead returns "no_conviction" and sizes nothing for that trade,
    which this research statistic cannot reproduce without dropping trades and
    breaking the paired design), then the whole set is re-normalised so the
    weights sum to the kept count (budget-neutral, same convention as the
    tercile weights)."""
    raw = [_clip01(r.get("confidence")) for r in rows_kept]
    defined = [w for w in raw if w is not None]
    fallback = (sum(defined) / len(defined)) if defined else 0.5
    raw2 = [(w if w is not None else fallback) for w in raw]
    raw2 = [(w if w >= _NO_TRADE_FLOOR else 0.0) for w in raw2]
    total = sum(raw2)
    n = len(raw2)
    if total <= 0:
        return [1.0] * n
    scale = n / total
    return [w * scale for w in raw2]


def _max_drawdown(path_r: Sequence[float]) -> float:
    """Largest peak-to-trough drop of the cumulative-R path, in R. 0.0 for a
    monotone-non-decreasing path (never negative, never undefined)."""
    peak = 0.0
    cum = 0.0
    mdd = 0.0
    for r in path_r:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def load_scores_file(path: str) -> Dict[Tuple[str, str], float]:
    """`--scores-file`: JSONL of {"cell":..., "entry_time":..., "score":...},
    keyed (cell, entry_time as given) so it lines up with each row's own
    `entry_time.isoformat()`. A landed S1/S1-v0 score file, so S2-sizing sizes
    on the model that actually PASSED rather than re-fitting one inline."""
    out: Dict[Tuple[str, str], float] = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out[(str(d["cell"]), str(d["entry_time"]))] = float(d["score"])
    return out


def size_by_score(rows: List[dict], folds: int, train_frac: float, embargo_days: int,
                  veto_frac: float, perms: int, seed: int, min_train: int = 50,
                  scores_by_key: Optional[Dict[Tuple[str, str], float]] = None) -> dict:
    """S2-sizing statistic. Sizes the KEPT trades of a veto model — either
    re-fit inline (row-only, S1-v0's own recipe: "else RQ-20260928-002's
    model") or, when `scores_by_key` is given, a landed score per trade
    (RQ-20260928-003/-004's "S1 arm (2) if it passed")."""
    add_trailing_cell_stats(rows)
    blocks, fold_defs = _fold_blocks(rows, folds, train_frac, embargo_days, min_train)
    rng = random.Random(seed)

    if scores_by_key is not None:
        oos_idx: List[int] = []
        model_scores: List[float] = []
        fold_of: List[int] = []
        missing_scores = 0
        for fd in fold_defs:
            if fd["state"] != "scored":
                continue
            for i in fd["block"]:
                key = (rows[i]["cell"], rows[i]["entry_time"].isoformat())
                s = scores_by_key.get(key)
                if s is None:
                    missing_scores += 1
                    continue
                oos_idx.append(i)
                model_scores.append(s)
                fold_of.append(fd["fold"])
        score_source = "scores_file"
    else:
        oos_idx, model_scores, fold_of = _score_arm(rows, fold_defs, FEATURES)
        missing_scores = 0
        score_source = "inline_row_only_refit"
    if not oos_idx:
        return {"read_state": "no_data", "n_oos": 0, "score_source": score_source}

    net = [rows[i]["net_r"] for i in oos_idx]
    cells = [rows[i]["cell"] for i in oos_idx]
    cell_mean: Dict[str, float] = {}
    for c in set(cells):
        v = [n for n, cc in zip(net, cells) if cc == c]
        cell_mean[c] = sum(v) / len(v)
    dem = [n - cell_mean[c] for n, c in zip(net, cells)]

    keep_all: List[bool] = [True] * len(model_scores)
    for fi in sorted(set(fold_of)):
        ids = [k for k, f in enumerate(fold_of) if f == fi]
        mask = veto_mask([model_scores[k] for k in ids], veto_frac)
        for k, keep in zip(ids, mask):
            keep_all[k] = keep
    kept_idx = [k for k, keep in enumerate(keep_all) if keep]
    if not kept_idx:
        return {"read_state": "no_data", "n_oos": len(oos_idx), "n_kept": 0, "score_source": score_source}

    def _fold_stat(weight_fn) -> Tuple[Optional[float], List[Optional[float]], List[Optional[float]]]:
        """weight_fn(kept_scores, kept_rows) -> weights. Returns
        (pooled_sized_delta_r, per-fold sized_delta_r, per-fold dd_ratio)."""
        pooled_num = 0.0
        pooled_den = 0
        fold_deltas: List[Optional[float]] = []
        fold_dd: List[Optional[float]] = []
        for fi in sorted(set(fold_of[k] for k in kept_idx)):
            ids = [k for k in kept_idx if fold_of[k] == fi]
            if not ids:
                fold_deltas.append(None)
                fold_dd.append(None)
                continue
            kept_scores = [model_scores[k] for k in ids]
            kept_rows = [rows[oos_idx[k]] for k in ids]
            w = weight_fn(kept_scores, kept_rows)
            demk = [dem[k] for k in ids]
            sized_delta = sum(wi * di for wi, di in zip(w, demk)) - sum(demk)
            fold_deltas.append(sized_delta)
            pooled_num += sum(wi * di for wi, di in zip(w, demk)) - sum(demk)
            pooled_den += 1
            order = sorted(range(len(ids)), key=lambda k: rows[oos_idx[ids[k]]]["entry_time"])
            netk = [rows[oos_idx[ids[k]]]["net_r"] for k in order]
            sized_path = [netk[k] * w[order[k]] for k in range(len(order))]
            flat_path = netk
            dd_sized = _max_drawdown(sized_path)
            dd_flat = _max_drawdown(flat_path)
            fold_dd.append((dd_sized / dd_flat) if dd_flat > 0 else None)
        pooled = pooled_num if pooled_den else None
        return pooled, fold_deltas, fold_dd

    sized_delta, sized_fold_deltas, dd_ratios = _fold_stat(
        lambda scores, _rows: _tercile_weights(scores))
    conv_delta, conv_fold_deltas, conv_dd = _fold_stat(
        lambda _scores, rows_k: _conviction_weights(rows_k))

    null: List[float] = []
    for _ in range(perms):
        perm_delta, _, _ = _fold_stat(lambda scores, _rows: _tercile_weights(
            [rng.random() for _ in scores]))
        if perm_delta is not None:
            null.append(perm_delta)
    perm_p = (sum(1 for d in null if d >= (sized_delta or 0.0)) + 1) / (len(null) + 1) if null else None
    folds_positive = sum(1 for d, r in zip(sized_fold_deltas, dd_ratios)
                         if d is not None and d > 0 and (r is None or r <= 1.05))

    return {
        "read_state": "measured",
        "score_source": score_source,
        "missing_scores": missing_scores,
        "n_oos": len(oos_idx),
        "n_kept": len(kept_idx),
        "veto_frac": veto_frac,
        "sized_delta_r": sized_delta,
        "sized_fold_deltas": sized_fold_deltas,
        "dd_ratio_by_fold": dd_ratios,
        "perm_p": perm_p,
        "perms": len(null),
        "folds_positive": folds_positive,
        "conviction_comparator": {"sized_delta_r": conv_delta, "fold_deltas": conv_fold_deltas,
                                  "dd_ratio_by_fold": conv_dd},
    }


# ---------------------------------------------------------------------------
# research_result.py hand-off (--emit-result)
# ---------------------------------------------------------------------------

def _load_research_result_module():
    import importlib.util
    p = os.path.join(ROOT, "scripts", "research", "research_result.py")
    spec = importlib.util.spec_from_file_location("_research_result_for_meta_veto", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def emit_result(*, out: dict, args: argparse.Namespace) -> str:
    """Land `out` (a `run()`/`size_by_score()` result) via research_result.py's
    own schema and write() — the "research_result.py --emit hand-off" this
    unit's build item calls for, so a run's own numbers are landed without a
    second, hand-transcribed command."""
    rr = _load_research_result_module()
    n = out.get("n_oos")
    population = args.population or (
        f"OOS costed harness trades ({out.get('n_cells_oos', '?')} cells), "
        f"folds={args.folds} train_frac={args.train_frac} embargo_days={args.embargo_days} "
        f"veto_frac={args.veto_frac} perms={args.perms} seed={args.seed}")
    record = rr.build(
        research_unit=args.research_unit, decision_rule_id=args.decision_rule_id,
        decision_rule_registered_at=args.decision_rule_registered_at,
        verdict=args.verdict, read_state=out.get("read_state", "measured"),
        population_description=population, n=n, workflow=args.workflow,
        run_id=args.run_id, run_attempt=None, run_url=None, commit_sha=args.commit_sha,
        tool="scripts/research/meta_veto_walkforward.py", measurement=out,
        artifact_store=args.artifact_store, artifact_locator=args.artifact_locator,
        rows_landed=None, power_state=args.power_state, note=args.note,
    )
    path = rr.write([record], research_unit=args.research_unit, run_id=args.run_id)
    return str(path)


# ---------------------------------------------------------------------------
# self-test — planted positives and nulls, all synthetic
# ---------------------------------------------------------------------------

def _synthetic(n_cells: int, per_cell: int, signal: float, seed: int) -> List[dict]:
    rng = random.Random(seed)
    rows: List[dict] = []
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for c in range(n_cells):
        base = rng.uniform(-0.3, 0.3)  # between-cell base rate the statistic must IGNORE
        for k in range(per_cell):
            et = t0 + timedelta(hours=6 * k + c)
            conf = rng.random()
            # within-cell: high confidence -> better net_r when signal > 0
            nr = base + signal * (conf - 0.5) + rng.gauss(0, 0.6)
            rows.append({"cell": f"c{c}", "entry_time": et, "exit_time": et + timedelta(hours=4),
                         "net_r": nr, "confidence": conf, "stop_dist_pct": rng.uniform(0.005, 0.03),
                         "is_long": float(rng.random() < 0.5),
                         "hour_sin": math.sin(2 * math.pi * et.hour / 24), "hour_cos": math.cos(2 * math.pi * et.hour / 24),
                         "dow": float(et.weekday())})
    rows.sort(key=lambda r: r["entry_time"])
    return rows


def _synthetic_candles(n_bars: int, seed: int, trend_regime: bool = True,
                       bar_delta: timedelta = timedelta(minutes=1)) -> List[Tuple[
        datetime, float, float, float, float, float, Optional[float]]]:
    """A synthetic OHLCV series (bar spacing `bar_delta`) with enough history for
    ADX/range/vol-pctile. Vol REGIME switches every ~50 bars (not just drift), so
    the realized-vol percentile the harness reads actually varies bar to bar —
    a constant-vol path would make every as-of read collapse to the same value."""
    rng = random.Random(seed)
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    out = []
    for k in range(n_bars):
        drift = 0.02 if (trend_regime and (k // 200) % 2 == 0) else -0.005
        vol_mult = 3.0 if (k // 50) % 2 == 0 else 1.0  # alternating calm/volatile regime
        price = max(1.0, price * (1 + drift * 0.01 + rng.gauss(0, 0.004 * vol_mult)))
        o = price
        h = price * (1 + abs(rng.gauss(0, 0.002 * vol_mult)))
        lo = price * (1 - abs(rng.gauss(0, 0.002 * vol_mult)))
        c = price * (1 + rng.gauss(0, 0.001 * vol_mult))
        v = abs(rng.gauss(100, 10))
        out.append((t0 + k * bar_delta, o, h, lo, c, v, None))
    return out


def _synthetic_with_market_state(n_cells: int, per_cell: int, signal: float, seed: int) -> Tuple[
        List[dict], Dict[str, list], Dict[str, List[datetime]]]:
    """Rows whose net_r depends on a MARKET-STATE feature the row-only model
    cannot see, each cell backed by its own synthetic candle series so the
    as-of join (`market_state_asof`) runs for real against real history.

    The planted driver OVERWRITES `ret_1` with an IID-per-trade draw rather
    than driving net_r from the join's own (autocorrelated) `rv_pctile`
    reading. That is not a shortcut around the join — every other feature on
    the row, and `ret_1` itself for every OTHER assertion in this self-test,
    still comes straight from `market_state_asof`. It is there because a
    REGIME-persistent driver (`rv_pctile` moves in slow blocks) leaks into
    `cell_trail_mean_r`/`cell_trail_winrate` through ordinary autocorrelation
    — a recent run of wins already says "we are in the good regime" even to a
    model that has never seen a market-state feature — which defeats the one
    thing this test needs to isolate: whether the ARM MACHINERY (identical
    folds, per-arm refit, paired_gain) correctly credits market state when
    trailing performance genuinely cannot proxy it. An IID-per-trade driver
    has no persistence for a trailing average to pick up, so the row-only arm
    is cleanly at chance and the effect is attributable to the join alone.
    """
    rng = random.Random(seed)
    rows: List[dict] = []
    candles: Dict[str, list] = {}
    ts_index: Dict[str, List[datetime]] = {}
    t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    # bar spacing matches the trade cadence (2h apart) so the candle series
    # actually spans the trades' entry times instead of running out after a
    # few hours and collapsing every later as-of read to the same last bar.
    bar_delta = timedelta(hours=2)
    n_bars = per_cell + MARKET_STATE_MIN_HISTORY + 60
    for c in range(n_cells):
        cell = f"m{c}"
        cds = _synthetic_candles(n_bars, seed=1000 + c, bar_delta=bar_delta)
        candles[cell] = cds
        ts_index[cell] = [x[0] for x in cds]
        base = rng.uniform(-0.2, 0.2)
        for k in range(per_cell):
            et = t0 + (MARKET_STATE_MIN_HISTORY + 30) * bar_delta + k * bar_delta
            ms = dict(market_state_asof(cds, ts_index[cell], et))
            iid_driver = rng.uniform(-0.5, 0.5)
            ms["ret_1"] = iid_driver
            nr = base + signal * iid_driver + rng.gauss(0, 0.4)
            row = {"cell": cell, "entry_time": et, "exit_time": et + timedelta(hours=1),
                  "net_r": nr, "confidence": rng.random(), "stop_dist_pct": rng.uniform(0.005, 0.03),
                  "is_long": float(rng.random() < 0.5),
                  "hour_sin": math.sin(2 * math.pi * et.hour / 24), "hour_cos": math.cos(2 * math.pi * et.hour / 24),
                  "dow": float(et.weekday())}
            row.update(ms)
            row["market_state_available"] = all(
                ms[f] is not None for f in MARKET_FEATURES if f not in ("funding_bps", "p_volatile"))
            rows.append(row)
    rows.sort(key=lambda r: r["entry_time"])
    return rows, candles, ts_index


def _self_test() -> int:
    fails = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            fails.append(label)

    pos = run(_synthetic(6, 250, signal=1.2, seed=1), folds=4, train_frac=0.4, embargo_days=1,
              veto_frac=0.25, perms=60, seed=1)
    check("planted positive: measured", pos["read_state"] == "measured")
    check("planted positive: veto_delta_r > 0", (pos["veto_delta_r"] or 0) > 0)
    check("planted positive: perm_p < 0.05", (pos["perm_p"] or 1) < 0.05)
    check("planted positive: every scored fold positive", pos["folds_positive"] == pos["folds_scored"] > 0)
    null = run(_synthetic(6, 250, signal=0.0, seed=2), folds=4, train_frac=0.4, embargo_days=1,
               veto_frac=0.25, perms=60, seed=2)
    check("null: measured", null["read_state"] == "measured")
    check("null: perm_p not significant", (null["perm_p"] or 0) > 0.05)
    check("null: |veto_delta_r| small", abs(null["veto_delta_r"] or 0) < 0.08)
    # between-cell base rate alone must NOT register as within-cell veto information
    base_only = _synthetic(6, 250, signal=0.0, seed=3)
    for r in base_only:
        r["confidence"] = 0.5  # no within-cell signal at all; only cell base rates differ
    bo = run(base_only, folds=4, train_frac=0.4, embargo_days=1, veto_frac=0.25, perms=60, seed=3)
    check("base-rate-only: perm_p not significant", (bo["perm_p"] or 0) > 0.05)
    # embargo/purge: no training trade may exit inside the embargo before the test block
    blocks = make_folds(pos_rows := _synthetic(3, 100, 0.5, 4), 4, 0.4)
    add_trailing_cell_stats(pos_rows)
    first = pos_rows[blocks[0][0]]["entry_time"]
    train = [i for i in range(blocks[0][0]) if pos_rows[i]["exit_time"] <= first - timedelta(days=1)]
    check("purge: all training exits precede test start minus embargo",
          all(pos_rows[i]["exit_time"] <= first - timedelta(days=1) for i in train))
    # as-of trailing stats never see the trade's own outcome
    check("trailing stats are as-of (first trade of a cell has n=0)",
          all(r["cell_trail_n"] == 0.0 for r in pos_rows if r["entry_time"] == min(
              x["entry_time"] for x in pos_rows if x["cell"] == r["cell"])))

    # --- market-state (PI-20260928-VNMMNBJH-0001): planted positive ---
    ms_rows, _, _ = _synthetic_with_market_state(6, 220, signal=1.5, seed=5)
    ms_pos = run(ms_rows, folds=4, train_frac=0.4, embargo_days=1, veto_frac=0.25, perms=60, seed=5, arms=True)
    check("market-state planted positive: arms present", "arms" in ms_pos)
    if "arms" in ms_pos:
        rp, rm = ms_pos["arms"]["row_only"], ms_pos["arms"]["row_plus_market"]
        check("market-state planted positive: row+market perm_p significant",
              (rm["perm_p"] or 1) < 0.05)
        check("market-state planted positive: paired_gain > 0 (market beats row-only)",
              (ms_pos["arms"]["paired_gain"] or -1) > 0)
        check("market-state planted positive: row-only NOT significant (signal invisible to it)",
              (rp["perm_p"] or 0) > 0.05)
    # as-of: market state before MARKET_STATE_MIN_HISTORY bars is None, never fabricated
    early_cds = _synthetic_candles(400, seed=1000)
    early_ts = [c[0] for c in early_cds]
    early_ms = market_state_asof(early_cds, early_ts, early_cds[10][0])
    check("market-state as-of: insufficient history -> every feature None (never fabricated)",
          all(v is None for v in early_ms.values()))
    late_ms = market_state_asof(early_cds, early_ts, early_cds[380][0])
    check("market-state as-of: sufficient history -> core features present",
          all(late_ms[k] is not None for k in ("ret_1", "rv_pctile", "adx", "dist_20", "dist_50")))
    check("market-state as-of: p_volatile always missing this pass (documented, never fabricated)",
          late_ms["p_volatile"] is None)
    # no-lookahead: a feature computed as-of bar i must not change if later bars change
    mutated = [list(t) for t in early_cds]
    for k in range(390, 400):
        mutated[k][4] *= 1.5  # inflate close on bars AFTER the as-of point
    mutated_t = [tuple(t) for t in mutated]
    ms_before = market_state_asof(early_cds, early_ts, early_cds[380][0])
    ms_after = market_state_asof(mutated_t, early_ts, early_cds[380][0])
    check("market-state as-of: mutating FUTURE bars does not change a past as-of read",
          ms_before == ms_after)

    # --- sizing (PI-20260928-VNMMNBJH-0001): planted positive ---
    sz_rows = _synthetic(6, 250, signal=0.0, seed=7)
    # plant a score correlated with a WITHIN-cell sizing edge: score = confidence,
    # and net_r rewards high confidence directly (independent of the veto test).
    for r in sz_rows:
        r["net_r"] = r["net_r"] + 0.9 * (r["confidence"] - 0.5)
    sz = size_by_score(sz_rows, folds=4, train_frac=0.4, embargo_days=1, veto_frac=0.25,
                       perms=60, seed=7)
    check("sizing planted positive: measured", sz["read_state"] == "measured")
    check("sizing planted positive: sized_delta_r > 0", (sz["sized_delta_r"] or 0) > 0)
    check("sizing planted positive: perm_p < 0.05", (sz["perm_p"] or 1) < 0.05)
    check("sizing planted positive: dd_ratio recorded for at least one fold",
          any(r is not None for r in sz["dd_ratio_by_fold"]))
    # budget neutrality: tercile weights always sum to the kept count in a fold
    check("sizing: tercile weights are budget-neutral (sum == n)",
          abs(sum(_tercile_weights([0.1, 0.5, 0.2, 0.9, 0.4, 0.7])) - 6.0) < 1e-9)
    # --scores-file path: a landed score sizes the SAME way as an inline refit shape
    scores_by_key = {(r["cell"], r["entry_time"].isoformat()): r["confidence"] for r in sz_rows}
    sz_from_file = size_by_score(sz_rows, folds=4, train_frac=0.4, embargo_days=1, veto_frac=0.25,
                                 perms=60, seed=7, scores_by_key=scores_by_key)
    check("sizing --scores-file: measured, uses the supplied scores",
          sz_from_file["read_state"] == "measured" and sz_from_file["score_source"] == "scores_file"
          and sz_from_file["missing_scores"] == 0)

    # --- resample fallback: never read a finer grain as the leg's own bars ---
    # Starts at 00:15 (not 00:00) so every bucket is COMPLETE: pandas resample
    # with label="right", closed="right" (the canonical backtest_trend.py
    # convention this reuses) puts a bar timestamped exactly ON an hour
    # boundary into the bucket ENDING there, so an 00:00-anchored series
    # would open with a lone 1-bar partial bucket -- a real edge case, just
    # not the one this test is checking.
    fine = [(datetime(2024, 1, 1, 0, 15, tzinfo=timezone.utc) + timedelta(minutes=15 * k),
            100.0 + k, 100.0 + k + 1.0, 100.0 + k - 1.0, 100.0 + k + 0.5, 10.0, None)
           for k in range(8)]  # 8 x 15m bars, 00:15..02:00 = exactly two complete 1h buckets
    rs = _resample_candles(fine, "1h")
    try:
        import pandas as _pd  # noqa: F401
        pandas_here = True
    except ImportError:
        pandas_here = False
    if pandas_here:
        check("resample: pandas available -> produces bars, never None", rs is not None)
        if rs is not None:
            check("resample: two complete 15m->1h buckets from 8 bars", len(rs) == 2)
            first_bucket = fine[0:4]
            check("resample: bucket open is the FIRST sub-bar's open",
                  abs(rs[0][1] - first_bucket[0][1]) < 1e-9)
            check("resample: bucket close is the LAST sub-bar's close",
                  abs(rs[0][4] - first_bucket[-1][4]) < 1e-9)
            check("resample: bucket high is the MAX of sub-bar highs",
                  abs(rs[0][2] - max(b[2] for b in first_bucket)) < 1e-9)
            check("resample: bucket low is the MIN of sub-bar lows",
                  abs(rs[0][3] - min(b[3] for b in first_bucket)) < 1e-9)
            check("resample: bucket is right-LABELED (01:00, not 00:15)",
                  rs[0][0] == datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc))
    else:
        check("resample: pandas unavailable -> fails CLOSED (None), never a wrong-grain read",
              rs is None)

    print(f"meta_veto_walkforward --self-test: {'OK' if not fails else 'FAILED ' + str(fails)}")
    return 0 if not fails else 1


# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence-dir", default=EVIDENCE_DIR)
    ap.add_argument("--trades", action="append", default=[],
                    help="explicit trades.jsonl path(s); overrides --evidence-dir. Cell = file stem.")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--train-frac", type=float, default=0.4)
    ap.add_argument("--embargo-days", type=int, default=7)
    ap.add_argument("--veto-frac", type=float, default=0.25)
    ap.add_argument("--perms", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--census", action="store_true", help="population only; compute NO statistic")
    ap.add_argument("--json", default=None, help="write the result JSON here")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--arms", action="store_true",
                    help="compute row-only / row+market-state / confidence-only on identical "
                         "folds. Requires --candles-dir (never an implicit default).")
    ap.add_argument("--candles-dir", default=None,
                    help="directory the canonical (symbol, timeframe) -> candle-file resolver "
                         "reads (scripts/ops/backtest_data_source.py). Required by --arms.")
    ap.add_argument("--size-by-score", action="store_true",
                    help="S2-sizing statistic over the kept trades of a veto model.")
    ap.add_argument("--scores-file", default=None,
                    help="JSONL of {cell, entry_time, score} — size a landed S1/S1-v0 score "
                         "instead of refitting inline.")
    ap.add_argument("--emit-result", action="store_true",
                    help="land the run via research_result.py's own writer (see --research-unit "
                         "and the other research_result.py-shaped flags below).")
    ap.add_argument("--research-unit", default=None)
    ap.add_argument("--decision-rule-id", default=None)
    ap.add_argument("--decision-rule-registered-at", default=None)
    ap.add_argument("--verdict", default=None)
    ap.add_argument("--power-state", default=None)
    ap.add_argument("--workflow", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--commit-sha", default=None)
    ap.add_argument("--population", default=None)
    ap.add_argument("--artifact-store", default=None)
    ap.add_argument("--artifact-locator", default=None)
    ap.add_argument("--note", default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.arms and not a.candles_dir:
        print("meta_veto_walkforward: --arms requires --candles-dir (never an implicit default) "
              "-- refusing rather than silently running row-only", file=sys.stderr)
        return 2

    if a.trades:
        files = {os.path.basename(p).replace("__trades.jsonl", "").replace(".jsonl", ""):
                {"trades_path": p, "symbol": None, "timeframe": None} for p in a.trades}
        skipped: List[Tuple[str, str]] = []
    else:
        files, skipped = trades_files_from_evidence(a.evidence_dir)
    rows, unreadable = load_rows(files)
    census = {
        "population": "one costed harness trade per row; one trades file per evidence record, "
                      "read from the record's own source_run",
        "legs": len(files), "rows": len(rows), "unreadable_rows": unreadable,
        "legs_skipped": [{"leg": leg, "why": why} for leg, why in skipped],
        "entry_time_min": rows[0]["entry_time"].isoformat() if rows else None,
        "entry_time_max": rows[-1]["entry_time"].isoformat() if rows else None,
        "folds": a.folds, "train_frac": a.train_frac, "embargo_days": a.embargo_days,
    }

    market_census = None
    if a.arms or (a.candles_dir and a.size_by_score):
        cell_symtf = {leg: (info.get("symbol"), info.get("timeframe")) for leg, info in files.items()}
        market_census = attach_market_state(rows, a.candles_dir, cell_symtf)
        census["market_state"] = market_census

    if a.census:
        blocks = make_folds(rows, a.folds, a.train_frac)
        census["oos_rows"] = sum(len(b) for b in blocks)
        census["expected_vetoed_at_veto_frac"] = int(round(census["oos_rows"] * a.veto_frac))
        out = {"census": census, "statistic_computed": False}
    elif a.size_by_score:
        scores_by_key = load_scores_file(a.scores_file) if a.scores_file else None
        res = size_by_score(rows, a.folds, a.train_frac, a.embargo_days, a.veto_frac, a.perms,
                            a.seed, scores_by_key=scores_by_key)
        out = {"census": census, "statistic_computed": True, "result": res}
    else:
        res = run(rows, a.folds, a.train_frac, a.embargo_days, a.veto_frac, a.perms, a.seed,
                 arms=a.arms)
        out = {"census": census, "statistic_computed": True, "result": res}

    if a.emit_result:
        res_for_emit = out.get("result", {})
        landed = emit_result(out=res_for_emit, args=a)
        out["landed_at"] = landed

    def _default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    printable = {k: v for k, v in out.items()}
    if "result" in printable and isinstance(printable["result"], dict):
        printable["result"] = {k: v for k, v in printable["result"].items()
                               if k not in ("oos_idx", "keep_model", "dem", "fold_of")}
    text = json.dumps(printable, indent=2, default=_default)
    print(text)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
