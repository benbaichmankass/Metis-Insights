#!/usr/bin/env python3
# wiring: research/queue units RQ-20261014-d6e7-01..06, RQ-20261024-09a6-01 (run.command) via research-script-run.yml -- the token-free runner
"""First passage in an open trade: P(SL before TP | state at bar t), and an exit-now policy graded net of cost.

WHY THIS EXISTS (checklist row IN-TRADE-EXIT-MODEL; operator direction 2026-10-14, verbatim on the row:
"at any given moment, are we going to hit the stop loss before we hit the take profit? That's basically
the question"). Programme 01 Q5/Q8 and Programme 02 Q3 name it as the destination of the exit line. The
binding gap is per-bar in-trade path data: harness emits are per-TRADE, live position_telemetry is one
upserted row, and MAE is recorded nowhere. This script does NOT wait for that: the committed per-trade
ledgers (`comms/strategy_evidence/runs/<run>/<leg>__trades.jsonl`) carry entry time, entry, initial SL and
direction, and the candle series is public, so each trade's bar path can be RE-WALKED offline.

THE CONSTRUCTION (fixed-bracket reconstruction; say this whenever a number from here is quoted)
  * Entry at the ledger's `entry` price at the close of the bar labelled `entry_time` (harness convention:
    right-labelled, right-closed resample; the walk starts on the NEXT bar, as the harnesses do).
  * The SL is the ledger's INITIAL stop at -1 R. The TP is a FIXED level K R (grid --tp-r). The leg's own
    trailing stop, TP revision and stale/giveback exits are NOT modelled -- this asks the operator's
    question of a plain bracket, it does not replay the leg as it trades (that needs the harness-native
    per-bar emit, checklist row EXIT-PATH-EMIT-OFFLINE, and unit RQ-20261014-d6e7-06).
  * First passage per bar: SL is checked BEFORE TP on a bar that touches both (conservative; the harness
    convention -- every barrier labeller here does this; whether it is a material label error is measured by
    RQ-20261014-da7a-01, filed by PR #18112, not re-filed here).
  * Alignment: `entry_time` labels the ENTRY bar (open-time label on a native feed); the trade is filled at its
    close, so the walk starts at entry + 1 bar and the entry bar's own high/low never count. MEASURED 2026-10-14 on
    31 legs / 4,143 trades: starting AT the entry bar breaches the initial SL before the exit bar on 412 trades,
    starting at entry + 1 on 0. The self-test plants the off-by-one (an entry-bar wick through the SL). No passage within H bars -> timeout, valued at that bar's close. H per leg = max(10, p90
    of the leg's realised holds in bars) -- registered, not tuned.
  * Costs: every arm pays the trade's own ledger `cost_total_r` (fee + slippage + funding from
    execution_costs.resolve_cost_policy() via the harness). An EARLY exit pays the trade's
    `cost_slippage_r` once more (a market exit where the bracket would have rested a limit). Conservative.
  * Positive control (RULE ONE): for every trade whose realised exit bar aligns, the re-walked MFE is
    compared with the ledger's own `mfe_r`. A leg enters the corpus only if >= 80% of its controllable
    trades match within 0.05 R on n >= 10; the rest are reported as `control_failed`, never pooled.
  * Population: newest ledger per leg (bootstrap sub-ledgers excluded, as ledger_metalabel.py), USDT
    symbols only (the runner reaches Binance Vision with `requests`; yfinance is not installed there).
    Every skipped leg is listed with its reason -- the denominator is in the record.

THE NULL THAT MAKES THE QUESTION HONEST
  For a driftless random walk the probability of hitting -1 R before +K R from open R `x` is the gambler's
  ruin p0 = (K - x) / (K + 1). It needs no data. A model of P(SL first | state) has information only if it
  beats p0 out of sample; an exit rule "exit when E[R | hold] < R_now - cost" with p0 plugged in never
  exits (E[R | hold] = x exactly), so any exit the policy takes is the model's information speaking.

MODES
  --mode census    per K: P(TP first) / P(SL first) / P(timeout) vs p0 at entry; among SL-first trades the
                   share that first came within (1-f)K of the TP (f in 0.5, 0.75, 0.9); the "exit at the turn"
                   oracle saving (best close before the SL minus -1 R, minus the extra exit cost) per SL-first
                   trade and per trade; a per-bar calibration table of p0 against observed SL-first by open-R
                   bucket; net R per capital-day of the fixed bracket. Verdict by --pass-saving-r / --fail-saving-r.
  --mode baseline  bar-event rows (state at each bar close before passage; label SL-first=1, TP-first=0,
                   timeouts excluded from the fit) at K=--policy-tp-r; numpy L2 logistic on a feature set:
                   dist (logit p0, distance to TP and SL in ATR, log bars in trade) | simple (+ open R, log bars
                   since peak, giveback from peak) | rich (+ 3/6-bar momentum in ATR, ADX14 and its 3-bar slope,
                   3-bar range / ATR, close location in bar, 100-bar ATR percentile). Graded against --ref: a
                   calibrated reference model fitted on the same train folds (default `dist`), or p0 itself.
                   p0 is NOT a fair null on bars: discrete passage, SL-first on a shared bar and the horizon
                   make a model "beat" p0 on a pure random walk (the self-test shows it), so the information
                   claim is always "beats the calibrated distance-only reference". Time-ordered expanding folds
                   over trades (5 blocks, 4 test folds, purged: a train trade must exit before the test block's
                   first entry). Each trade weighs 1 (its bars share the weight). Metric: trade-weighted OOS
                   log-loss gain vs the reference (and vs the train base rate), trade-bootstrap CI, per-fold
                   sign, calibration slope.
  --mode policy    the fold models drive "exit at bar t if E[R | hold] < E_ref[R | hold] - extra_exit_cost", i.e.
                   (p_model - p_ref)(K + 1) > extra_exit_cost, where the calibrated reference (or p0, under
                   which E[R | hold] is exactly open R) stands in for the value of holding; graded
                   against the fixed bracket on the same OOS trades, per leg and pooled. PRIMARY: net R per
                   trade (the P&L the leg books on the same trades), trade-bootstrap CI, per-fold sign. GUARD:
                   net R per capital-day (sum net R / sum hold days, the operator's headline measure) must not
                   be worse than the bracket's when the bracket's is positive. When the bracket's is <= 0 the
                   per-capital-day ratio rewards exiting fast at a small loss less than holding at a large one
                   in a way that is not monotone in P&L (two negatives over different day counts), so it is
                   reported, not graded. Sanity control: the p0 policy must exit on < 5% of trades (it should
                   never exit: E[R | hold] under p0 is exactly open R).

Writes `<out>/verdict.json` ({verdict, read_state, population, n, measurement, note}) for
script_run.py --derive-record. Tier-1 research code: reads committed ledgers and public candles, no VM,
no journal, no live path; nothing it prints changes a bracket (every flip is Tier-3 via exit-refinement).

    python3 scripts/research/first_passage.py --mode census --out <dir>
    python3 scripts/research/first_passage.py --self-test
    python3 scripts/research/first_passage.py --harness-legs <leg,...> --mode baseline|policy --out <dir>

AS TRADED (FIRST-PASSAGE-ON-PATHS): `--paths <glob>` / `--harness-legs` route to scripts/research/first_passage_paths.py,
which reads the harness's own per-bar emit (the stop and target in force at every bar) instead of re-walking a fixed
bracket. Without those flags this file's output is byte-identical to before (tests/test_first_passage_paths.py).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))
import candle_io  # noqa: E402  (the shared OHLCV reader + the harness resample convention)
from ledger_metalabel import newest_ledgers  # noqa: E402  (the ONE newest-ledger-per-leg rule)

DEFAULT_GLOB = "comms/strategy_evidence/runs/*/*__trades.jsonl"
TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
_TF_SUFFIX = re.compile(r"_(\d+[mhd])(?:_|$)")
# Exit reasons at which the harness breaks BEFORE updating the favourable extreme on the exit bar
# (prop_exit_evidence._walk semantics); close-based exits update on the exit bar.
_INTRABAR_EXITS = {"stop", "sl_hit", "sl", "take_profit", "tp_hit", "target", "tp", "trail_stop",
                   "be_stop", "stop_loss"}
NEAR_F = (0.5, 0.75, 0.9)
CONTROL_TOL_R = 0.05
CONTROL_SHARE = 0.80
CONTROL_MIN_N = 10


# --------------------------------------------------------------------------- corpus
def _tf_of(leg: str, cfg: Optional[dict], rows: List[dict]) -> Optional[str]:
    if cfg and cfg.get("timeframe") in TF_MIN:
        return cfg["timeframe"]
    tf = (rows[0].get("meta") or {}).get("timeframe") if rows else None
    if tf in TF_MIN:
        return tf
    m = _TF_SUFFIX.search(leg)
    return m.group(1) if m and m.group(1) in TF_MIN else None


def load_legs(glob_pat: str, leg_re: Optional[str]) -> Tuple[List[dict], List[dict]]:
    """Return (legs, skipped). A leg: {leg, symbol, tf, rows, ledger}."""
    strat = yaml.safe_load(open(REPO / "config/strategies.yaml"))["strategies"]
    legs, skipped = [], []
    for leg, path in sorted(newest_ledgers(glob_pat).items()):
        if leg_re and not re.search(leg_re, leg):
            continue
        rows = [json.loads(ln) for ln in open(path) if ln.strip()]
        cfg = strat.get(leg)
        sym = (rows[0].get("symbol") if rows else None) or ((cfg or {}).get("symbols") or [None])[0]
        tf = _tf_of(leg, cfg, rows)
        base = {"leg": leg, "ledger": str(path), "n": len(rows), "symbol": sym, "tf": tf}
        if not sym or not tf:
            skipped.append({**base, "reason": "symbol_or_timeframe_unresolved"})
        elif not str(sym).upper().endswith("USDT"):
            skipped.append({**base, "reason": "non_crypto_feed_unavailable_on_runner"})
        else:
            legs.append({**base, "rows": rows})
    return legs, skipped


def fetch_frame(symbol: str, tf: str, days: int, cache: Path) -> pd.DataFrame:
    """Public candles (Binance Vision, the off-VM crypto feed), resampled by the harness convention."""
    cache.mkdir(parents=True, exist_ok=True)
    csv = cache / f"{symbol}_{tf}_{days}d.csv"
    if not csv.exists():
        import regime_debt_matrix as rdm  # lazy: only the network path needs it
        rdm._fetch_csv(rdm.resolve_feed(symbol, tf), days, str(csv))
    return candle_io.resample_ohlcv(candle_io.load_candles(str(csv)), tf)


# --------------------------------------------------------------------------- indicators (numpy, Wilder)
def _wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = np.nanmean(x[:n])
    for i in range(n, len(x)):
        out[i] = out[i - 1] + (x[i] - out[i - 1]) / n
    return out


def indicators(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    h, lo, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(abs(h - pc), abs(lo - pc)))
    atr = _wilder(tr, 14)
    up, dn = h - np.r_[h[0], h[:-1]], np.r_[lo[0], lo[:-1]] - lo
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi = 100 * _wilder(pdm, 14) / atr
        mdi = 100 * _wilder(mdm, 14) / atr
        dx = 100 * abs(pdi - mdi) / (pdi + mdi)
    adx = _wilder(np.nan_to_num(dx, nan=0.0), 14)
    atr_pct = pd.Series(atr).rolling(100, min_periods=20).rank(pct=True).to_numpy()
    return {"atr": atr, "adx": adx, "atr_pct": atr_pct}


# --------------------------------------------------------------------------- the walk
def walk_trade(df: pd.DataFrame, ind: Dict[str, np.ndarray], ei: int, t: dict, H: int) -> Optional[dict]:
    """Per-bar path from the bar after entry for up to H bars: favourable/adverse extremes and close in R."""
    entry, sl = float(t["entry"]), float(t["sl"])
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    long = t.get("direction") == "long"
    j1 = min(len(df), ei + 1 + H)
    if j1 <= ei + 1:
        return None
    h = df["high"].to_numpy(float)[ei + 1:j1]
    lo = df["low"].to_numpy(float)[ei + 1:j1]
    c = df["close"].to_numpy(float)[ei + 1:j1]
    o = df["open"].to_numpy(float)[ei + 1:j1]
    sgn = 1.0 if long else -1.0
    fav = ((h if long else lo) - entry) * sgn / risk
    adv = ((lo if long else h) - entry) * sgn / risk
    cr = (c - entry) * sgn / risk
    rng = np.maximum(h - lo, 1e-12)
    clv = ((c - lo) / rng) if long else ((h - c) / rng)  # 1 = closed at the favourable end
    return {"fav": fav, "adv": adv, "close_r": cr, "open_r_bar": (o - entry) * sgn / risk, "clv": clv,
            "risk": risk, "ei": ei, "atr": ind["atr"][ei + 1:j1], "adx": ind["adx"][ei + 1:j1],
            "atr_pct": ind["atr_pct"][ei + 1:j1], "close_abs": c, "long": long}


def first_passage(p: dict, K: float) -> Tuple[str, int, float]:
    """(outcome, bar index of passage (0-based in the path), R at exit). SL before TP on a shared bar."""
    sl_hit = np.nonzero(p["adv"] <= -1.0)[0]
    tp_hit = np.nonzero(p["fav"] >= K)[0]
    js = sl_hit[0] if len(sl_hit) else None
    jt = tp_hit[0] if len(tp_hit) else None
    if js is not None and (jt is None or js <= jt):
        return "sl", int(js), -1.0
    if jt is not None:
        return "tp", int(jt), float(K)
    n = len(p["close_r"])
    return "timeout", n - 1, float(p["close_r"][-1])


def p0(K: float, x: np.ndarray) -> np.ndarray:
    """Gambler's ruin, driftless: P(-1 before +K | open R x)."""
    return np.clip((K - x) / (K + 1.0), 0.01, 0.99)


# --------------------------------------------------------------------------- corpus build
def build(legs: List[dict], cache: Path, now: datetime) -> Tuple[List[dict], List[dict]]:
    """Walk every trade of every leg; apply the MFE positive control per leg. Returns (trades, leg_reports)."""
    out, reports = [], []
    # One fetch per (symbol, timeframe) at the widest window any leg needs: legs sharing a series (e.g. two
    # ETHUSDT 2h ledgers of 394 and 749 days) would otherwise each pay a multi-minute Binance Vision fetch.
    need: Dict[Tuple[str, str], int] = {}
    for L in legs:
        ent0 = pd.to_datetime([r["entry_time"] for r in L["rows"]], utc=True, errors="coerce")
        if len(ent0) and ent0.notna().any():
            d = int((now - ent0.min().to_pydatetime()).days) + 10
            need[(L["symbol"], L["tf"])] = max(need.get((L["symbol"], L["tf"]), 0), d)
    frames: Dict[Tuple[str, str], pd.DataFrame] = {}
    for L in legs:
        rows = L["rows"]
        tfm = TF_MIN[L["tf"]]
        ent = pd.to_datetime([r["entry_time"] for r in rows], utc=True, errors="coerce")
        ext = pd.to_datetime([r.get("exit_time") for r in rows], utc=True, errors="coerce")
        holds = ((ext - ent).total_seconds() / 60.0 / tfm).to_numpy()
        holds = holds[np.isfinite(holds)]
        H = int(max(10, np.percentile(holds, 90))) if len(holds) else 10
        rep: Dict[str, Any] = {"leg": L["leg"], "symbol": L["symbol"], "tf": L["tf"], "n_ledger": len(rows),
                               "horizon_bars": H}
        try:
            key = (L["symbol"], L["tf"])
            if L.get("frame") is not None:
                df = L["frame"]
            else:
                if key not in frames:
                    frames[key] = fetch_frame(L["symbol"], L["tf"], need.get(key, 30), cache)
                df = frames[key]
        except Exception as e:  # noqa: BLE001  # allow-silent: recorded as state=feed_failed with the error in the leg report; that leg is excluded and listed, never a pooled zero
            reports.append({**rep, "state": "feed_failed", "error": str(e)[:200]})
            continue
        ind = indicators(df)
        idx = {pd.Timestamp(ts): i for i, ts in enumerate(df["timestamp"])}
        trades, ctrl_n, ctrl_ok, unaligned = [], 0, 0, 0
        for r, te, tx in zip(rows, ent, ext):
            ei = idx.get(pd.Timestamp(te)) if te is not pd.NaT else None
            if ei is None or r.get("entry") is None or r.get("sl") is None:
                unaligned += 1
                continue
            p = walk_trade(df, ind, ei, r, H)
            if p is None:
                unaligned += 1
                continue
            xi = idx.get(pd.Timestamp(tx)) if tx is not pd.NaT else None
            if xi is not None and r.get("mfe_r") is not None and xi > ei:
                last = (xi - 1) if str(r.get("exit_reason") or r.get("outcome")) in _INTRABAR_EXITS else xi
                n_bars = last - ei
                if n_bars >= 1:
                    full = walk_trade(df, ind, ei, r, n_bars)
                    if full is not None:
                        ctrl_n += 1
                        mfe = max(0.0, float(np.max(full["fav"])))
                        ctrl_ok += abs(mfe - float(r["mfe_r"])) <= CONTROL_TOL_R
            trades.append({"leg": L["leg"], "tf": L["tf"], "tfm": tfm, "entry_time": pd.Timestamp(te),
                           "exit_time": pd.Timestamp(tx) if tx is not pd.NaT else None,
                           "cost_total_r": float(r.get("cost_total_r") or 0.0),
                           "cost_slippage_r": float(r.get("cost_slippage_r") or 0.0),
                           "ledger_net_r": r.get("net_r"), "path": p})
        share = (ctrl_ok / ctrl_n) if ctrl_n else None
        passed = ctrl_n >= CONTROL_MIN_N and share is not None and share >= CONTROL_SHARE
        rep.update({"n_walked": len(trades), "n_unaligned": unaligned, "control_n": ctrl_n,
                    "control_match_share": None if share is None else round(share, 3),
                    "state": "included" if passed else "control_failed",
                    "data_start": str(df["timestamp"].iloc[0]), "data_end": str(df["timestamp"].iloc[-1])})
        reports.append(rep)
        if passed:
            out.extend(trades)
    return out, reports


# --------------------------------------------------------------------------- census
def census(trades: List[dict], tps: List[float]) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    for K in tps:
        per_leg: Dict[str, Dict[str, Any]] = {}
        rows = []
        calib: Dict[str, List[float]] = {}
        for t in trades:
            p = t["path"]
            oc, j, r_exit = first_passage(p, K)
            near = {}
            save = None
            if oc == "sl":
                peak = float(np.max(p["fav"][:j + 1])) if j >= 0 else 0.0
                # the SL bar's own high is not reachable before its low (SL-first convention): use bars < j
                peak_pre = float(np.max(p["fav"][:j])) if j > 0 else 0.0
                for f in NEAR_F:
                    near[f] = peak_pre >= f * K
                best_close = float(np.max(p["close_r"][:j])) if j > 0 else -1.0
                save = max(0.0, best_close - (-1.0) - t["cost_slippage_r"]) if best_close > -1.0 else 0.0
                _ = peak
            bars = j + 1
            net = r_exit - t["cost_total_r"]
            days = bars * t["tfm"] / 1440.0
            rows.append({"leg": t["leg"], "oc": oc, "near": near, "save": save, "net": net, "days": days})
            # per-bar calibration of the null: at each bar close before passage, bucket open R
            for b in range(0, j):
                x = float(p["close_r"][b])
                if oc == "timeout":
                    continue
                key = f"{math.floor(x * 2) / 2:+.1f}"
                calib.setdefault(key, []).append(1.0 if oc == "sl" else 0.0)
        for leg in sorted({r["leg"] for r in rows}):
            per_leg[leg] = _census_summary([r for r in rows if r["leg"] == leg], K)
        cal = {k: {"n_bar_events": len(v), "obs_sl_first": round(float(np.mean(v)), 4),
                   "p0_at_bucket_mid": round(float(p0(K, np.array([float(k) + 0.25]))[0]), 4)}
               for k, v in sorted(calib.items(), key=lambda kv: float(kv[0])) if len(v) >= 50}
        res[f"K={K:g}"] = {"pooled": _census_summary(rows, K), "per_leg": per_leg,
                           "bar_calibration_vs_p0": cal}
    return res


def _census_summary(rows: List[dict], K: float) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    sl = [r for r in rows if r["oc"] == "sl"]
    out = {"n": n, "p_tp_first": round(sum(r["oc"] == "tp" for r in rows) / n, 4),
           "p_sl_first": round(len(sl) / n, 4),
           "p_timeout": round(sum(r["oc"] == "timeout" for r in rows) / n, 4),
           "p0_sl_first_at_entry": round(K / (K + 1.0), 4),
           "n_sl_first": len(sl),
           "fixed_bracket_net_r_per_trade": round(float(np.mean([r["net"] for r in rows])), 4),
           "fixed_bracket_net_r_per_capital_day": _rpcd(rows)}
    if sl:
        for f in NEAR_F:
            out[f"sl_first_share_reached_{f:g}K"] = round(sum(r["near"][f] for r in sl) / len(sl), 4)
        out["turn_oracle_saving_r_per_sl_trade"] = round(float(np.mean([r["save"] for r in sl])), 4)
        out["turn_oracle_saving_r_per_trade"] = round(float(np.sum([r["save"] for r in sl])) / n, 4)
    return out


def _rpcd(rows: List[dict], net_key: str = "net", day_key: str = "days") -> Optional[float]:
    d = float(sum(r[day_key] for r in rows))
    return round(float(sum(r[net_key] for r in rows)) / d, 4) if d > 0 else None


# --------------------------------------------------------------------------- bar events + model
#: "Where are we": distance to both barriers and time in trade. The calibrated REFERENCE every richer state
#: must beat -- p0 itself is not a fair null on bars (discrete passage, SL-first on a shared bar, the
#: horizon), and the self-test shows a model "beats" p0 on a pure random walk by learning exactly that.
DIST = ["logit_p0", "dist_tp_atr", "dist_sl_atr", "log_bars"]
#: The task's simple baseline: + open R, bars since peak, giveback from peak (path shape, no indicators).
SIMPLE = DIST + ["open_r", "log_since_peak", "giveback"]
RICH = SIMPLE + ["mom3_atr", "mom6_atr", "adx", "adx_slope3", "range3_atr", "clv", "atr_pct"]
FEATURE_SETS = {"dist": DIST, "simple": SIMPLE, "rich": RICH}


def bar_events(t: dict, K: float) -> Tuple[List[dict], str, int]:
    p = t["path"]
    oc, j, _ = first_passage(p, K)
    ev = []
    peak, peak_b = 0.0, -1
    for b in range(0, j):  # state at the close of bar b, passage not yet happened (it happens at j > b)
        if p["fav"][b] > peak:
            peak, peak_b = float(p["fav"][b]), b
        x = float(p["close_r"][b])
        atr_r = p["atr"][b] / p["risk"] if np.isfinite(p["atr"][b]) and p["atr"][b] > 0 else np.nan
        pz = float(p0(K, np.array([x]))[0])
        c = p["close_r"]
        f = {"logit_p0": math.log(pz / (1 - pz)),
             "dist_tp_atr": (K - x) / atr_r if atr_r == atr_r else np.nan,
             "dist_sl_atr": (x + 1.0) / atr_r if atr_r == atr_r else np.nan,
             "log_bars": math.log1p(b + 1), "open_r": x,
             "log_since_peak": math.log1p(b - peak_b) if peak_b >= 0 else math.log1p(b + 1),
             "giveback": max(0.0, peak - x),
             "mom3_atr": (x - (c[b - 3] if b >= 3 else 0.0)) / atr_r if atr_r == atr_r else np.nan,
             "mom6_atr": (x - (c[b - 6] if b >= 6 else 0.0)) / atr_r if atr_r == atr_r else np.nan,
             "adx": p["adx"][b], "adx_slope3": (p["adx"][b] - p["adx"][b - 3]) if b >= 3 else 0.0,
             "range3_atr": ((np.max(p["fav"][max(0, b - 2):b + 1]) - np.min(p["adv"][max(0, b - 2):b + 1]))
                            / atr_r) if atr_r == atr_r else np.nan,
             "clv": p["clv"][b], "atr_pct": p["atr_pct"][b]}
        ev.append({"b": b, "x": x, "f": f})
    return ev, oc, j


def _matrix(evs: List[dict], feats: List[str]) -> np.ndarray:
    return np.array([[e["f"][k] for k in feats] for e in evs], dtype=float)


class Logit:
    """L2 logistic regression by Newton/IRLS on standardised features, NaN -> train median."""

    def __init__(self, l2: float = 1.0):
        self.l2 = l2

    def fit(self, X: np.ndarray, y: np.ndarray, w: np.ndarray) -> "Logit":
        self.med = np.nanmedian(X, axis=0)
        self.med = np.where(np.isfinite(self.med), self.med, 0.0)
        X = np.where(np.isfinite(X), X, self.med)
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        Z = np.c_[np.ones(len(X)), (X - self.mu) / self.sd]
        b = np.zeros(Z.shape[1])
        reg = np.eye(Z.shape[1]) * self.l2
        reg[0, 0] = 0.0
        for _ in range(50):
            pr = 1 / (1 + np.exp(-np.clip(Z @ b, -30, 30)))
            g = Z.T @ (w * (pr - y)) + reg @ b
            Hm = (Z * (w * pr * (1 - pr))[:, None]).T @ Z + reg
            step = np.linalg.solve(Hm, g)
            b -= step
            if np.max(abs(step)) < 1e-8:
                break
        self.b = b
        return self

    def predict_proba_sl(self, X: np.ndarray) -> np.ndarray:
        """P(label = 1) = P(SL first) for a binary logistic head -- a probability, never max(proba)."""
        X = np.where(np.isfinite(X), X, self.med)
        Z = np.c_[np.ones(len(X)), (X - self.mu) / self.sd]
        return np.clip(1 / (1 + np.exp(-np.clip(Z @ self.b, -30, 30))), 1e-4, 1 - 1e-4)


def _ll(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def folds(trades: List[dict], k: int = 5) -> List[Tuple[List[int], List[int]]]:
    order = sorted(range(len(trades)), key=lambda i: trades[i]["entry_time"])
    blocks = np.array_split(np.array(order), k)
    out = []
    for f in range(1, k):
        test = list(blocks[f])
        t0 = trades[test[0]]["entry_time"] if test else None
        train = [i for b in blocks[:f] for i in b
                 if t0 is None or (trades[i]["exit_time"] is not None and trades[i]["exit_time"] < t0)]
        out.append((train, test))
    return out


def fit_eval(trades: List[dict], K: float, feats: List[str], seed: int = 7,
             ref_feats: Optional[List[str]] = None) -> Dict[str, Any]:
    """OOS per-trade log-loss of the model vs p0 (or vs a reference model) and vs the train base rate."""
    cache = [bar_events(t, K) for t in trades]
    per_trade: Dict[int, Dict[str, float]] = {}
    fold_rep, models = [], []
    for fi, (tr, te) in enumerate(folds(trades)):
        Xs, ys, ws = [], [], []
        for i in tr:
            ev, oc, _ = cache[i]
            if oc == "timeout" or not ev:
                continue
            Xs.append(_matrix(ev, feats))
            ys.append(np.full(len(ev), 1.0 if oc == "sl" else 0.0))
            ws.append(np.full(len(ev), 1.0 / len(ev)))
        if not Xs:
            fold_rep.append({"fold": fi, "state": "empty_train"})
            models.append(None)
            continue
        X, y, w = np.vstack(Xs), np.concatenate(ys), np.concatenate(ws)
        m = Logit().fit(X, y, w)
        ref = Logit().fit(np.vstack([_matrix(cache[i][0], ref_feats) for i in tr
                                     if cache[i][1] != "timeout" and cache[i][0]]), y, w) if ref_feats else None
        base = float(np.average(y, weights=w))
        models.append((m, ref))
        g_ref, g_base = [], []
        for i in te:
            ev, oc, _ = cache[i]
            if oc == "timeout" or not ev:
                continue
            yy = np.full(len(ev), 1.0 if oc == "sl" else 0.0)
            pm = m.predict_proba_sl(_matrix(ev, feats))
            pr = ref.predict_proba_sl(_matrix(ev, ref_feats)) if ref else p0(K, np.array([e["x"] for e in ev]))
            lm, lr, lb = _ll(yy, pm).mean(), _ll(yy, pr).mean(), _ll(yy, np.full(len(ev), base)).mean()
            per_trade[i] = {"gain_vs_ref": float(lr - lm), "gain_vs_base": float(lb - lm), "fold": fi,
                            "logit_pm": [float(v) for v in np.log(pm / (1 - pm))], "y": float(yy[0])}
            g_ref.append(lr - lm)
            g_base.append(lb - lm)
        fold_rep.append({"fold": fi, "n_train_trades": len(tr), "n_test_trades": len(g_ref),
                         "gain_vs_ref_nats": round(float(np.mean(g_ref)), 5) if g_ref else None,
                         "gain_vs_base_nats": round(float(np.mean(g_base)), 5) if g_base else None})
    gains = np.array([v["gain_vs_ref"] for v in per_trade.values()])
    rng = np.random.default_rng(seed)
    boot = [float(np.mean(rng.choice(gains, len(gains)))) for _ in range(1000)] if len(gains) else []
    # calibration slope: label ~ a + s * logit(p_model), every OOS bar event, trade-weighted
    lz, ly, lw = [], [], []
    for v in per_trade.values():
        lz.extend(v["logit_pm"])
        ly.extend([v["y"]] * len(v["logit_pm"]))
        lw.extend([1.0 / len(v["logit_pm"])] * len(v["logit_pm"]))
    slope = None
    if lz:
        cm = Logit(l2=1e-3).fit(np.clip(np.array(lz), -8, 8)[:, None], np.array(ly), np.array(lw))
        slope = round(float(cm.b[1] / cm.sd[0]), 3)
    return {"n_oos_trades": len(gains), "mean_gain_nats": round(float(np.mean(gains)), 5) if len(gains) else None,
            "ci95": [round(float(np.percentile(boot, 2.5)), 5), round(float(np.percentile(boot, 97.5)), 5)]
            if boot else None,
            "folds_positive": sum(1 for f in fold_rep if (f.get("gain_vs_ref_nats") or 0) > 0),
            "folds": fold_rep, "calibration_slope": slope, "reference": "model" if ref_feats else "p0_gamblers_ruin",
            "_models": models, "_cache": cache}


def policy_eval(trades: List[dict], K: float, fe: Dict[str, Any], feats: List[str],
                ref_feats: Optional[List[str]] = None, seed: int = 11) -> Dict[str, Any]:
    cache, models = fe["_cache"], fe["_models"]
    recs = []
    exits_null = n_null = 0
    for fi, (_, te) in enumerate(folds(trades)):
        mm = models[fi]
        if mm is None:
            continue
        m, ref = mm
        for i in te:
            t = trades[i]
            ev, oc, j = cache[i]
            p = t["path"]
            r_fix = -1.0 if oc == "sl" else (K if oc == "tp" else float(p["close_r"][j]))
            fixed = {"net": r_fix - t["cost_total_r"], "days": (j + 1) * t["tfm"] / 1440.0}
            pol = dict(fixed)
            c_x = t["cost_slippage_r"]
            if ev:
                xs = np.array([e["x"] for e in ev])
                pm = m.predict_proba_sl(_matrix(ev, feats))
                pr = ref.predict_proba_sl(_matrix(ev, ref_feats)) if ref is not None else p0(K, xs)
                # E[R | hold] - E_ref[R | hold] = (p_ref - p_model)(K + 1). The calibrated reference stands in
                # for "hold is worth open R" (exactly so under p0), so its discretisation bias cancels.
                hit = np.nonzero((pm - pr) * (K + 1.0) > c_x)[0]
                if len(hit):
                    e = ev[int(hit[0])]
                    pol = {"net": e["x"] - t["cost_total_r"] - c_x, "days": (e["b"] + 1) * t["tfm"] / 1440.0,
                           "exited_early": True}
                n_null += 1
                pz = p0(K, xs)
                exits_null += int(np.any((1 - pz) * K - pz < xs - c_x))
            recs.append({"leg": t["leg"], "fold": fi, "fixed_net": fixed["net"], "fixed_days": fixed["days"],
                         "pol_net": pol["net"], "pol_days": pol["days"], "early": bool(pol.get("exited_early"))})
    if not recs:
        return {"n_oos_trades": 0}

    def summ(rs):
        fx = _rpcd(rs, "fixed_net", "fixed_days")
        po = _rpcd(rs, "pol_net", "pol_days")
        return {"n": len(rs), "fixed_net_r_per_capital_day": fx, "policy_net_r_per_capital_day": po,
                "diff_r_per_capital_day": None if fx is None or po is None else round(po - fx, 4),
                "fixed_net_r_per_trade": round(float(np.mean([r["fixed_net"] for r in rs])), 4),
                "policy_net_r_per_trade": round(float(np.mean([r["pol_net"] for r in rs])), 4),
                "early_exit_share": round(float(np.mean([r["early"] for r in rs])), 4)}
    rng = np.random.default_rng(seed)
    idx = np.arange(len(recs))
    boot = []
    for _ in range(1000):
        s = [recs[k] for k in rng.choice(idx, len(idx))]
        a, b = _rpcd(s, "pol_net", "pol_days"), _rpcd(s, "fixed_net", "fixed_days")
        if a is not None and b is not None:
            boot.append(a - b)
    dd = np.array([r["pol_net"] - r["fixed_net"] for r in recs])
    tboot = [float(np.mean(rng.choice(dd, len(dd)))) for _ in range(1000)]
    per_fold = {}
    for fi in sorted({r["fold"] for r in recs}):
        per_fold[str(fi)] = summ([r for r in recs if r["fold"] == fi])
    return {"pooled": summ(recs), "per_leg": {lg: summ([r for r in recs if r["leg"] == lg])
                                              for lg in sorted({r["leg"] for r in recs})},
            "per_fold": per_fold,
            "folds_positive_per_trade": sum(1 for v in per_fold.values()
                                            if v["policy_net_r_per_trade"] > v["fixed_net_r_per_trade"]),
            "trade_diff_ci95": [round(float(np.percentile(tboot, 2.5)), 4), round(float(np.percentile(tboot, 97.5)), 4)],
            "capital_day_diff_ci95": [round(float(np.percentile(boot, 2.5)), 4), round(float(np.percentile(boot, 97.5)), 4)]
            if boot else None,
            "null_policy_exit_share": round(exits_null / n_null, 4) if n_null else None}


# --------------------------------------------------------------------------- verdicts
def verdict_census(res: Dict[str, Any], K: float, pass_r: float, fail_r: float, min_n: int) -> Tuple[str, str]:
    pooled = res[f"K={K:g}"]["pooled"]
    if pooled.get("n_sl_first", 0) < min_n:
        return "indeterminate", f"n_sl_first {pooled.get('n_sl_first', 0)} < {min_n} at K={K:g}"
    s = pooled.get("turn_oracle_saving_r_per_trade") or 0.0
    if s >= pass_r:
        return "pass", f"turn-oracle saving {s:.3f} R/trade >= {pass_r} at K={K:g} (n_sl_first {pooled['n_sl_first']})"
    if s < fail_r:
        return "fail", f"turn-oracle saving {s:.3f} R/trade < {fail_r} at K={K:g}"
    return "indeterminate", f"turn-oracle saving {s:.3f} R/trade between the bars"


def verdict_model(fe: Dict[str, Any], min_gain: float, min_folds: int, min_n: int) -> Tuple[str, str]:
    n, g, ci, s = fe["n_oos_trades"], fe["mean_gain_nats"], fe["ci95"], fe["calibration_slope"]
    if n < min_n or g is None:
        return "indeterminate", f"n_oos_trades {n} < {min_n}"
    ok_slope = s is not None and 0.7 <= s <= 1.3
    if g >= min_gain and ci[0] > 0 and fe["folds_positive"] >= min_folds and ok_slope:
        return "pass", f"gain {g:.4f} nats >= {min_gain}, CI [{ci[0]}, {ci[1]}], {fe['folds_positive']}/4 folds, slope {s}"
    if ci[1] < min_gain:
        return "fail", f"CI upper {ci[1]} < {min_gain} nats vs {fe['reference']}"
    return "indeterminate", f"gain {g}, CI {ci}, folds+ {fe['folds_positive']}, slope {s}"


def verdict_policy(pe: Dict[str, Any], min_gain_r: float, min_folds: int, min_n: int) -> Tuple[str, str]:
    if pe.get("n_oos_trades") == 0 or pe["pooled"]["n"] < min_n:
        return "indeterminate", f"n_oos_trades < {min_n}"
    nz = pe["null_policy_exit_share"]
    if nz is not None and nz >= 0.05:
        return "indeterminate", f"sanity control failed: p0 policy exits on {nz:.3f} of trades (>= 0.05)"
    P, ci = pe["pooled"], pe["trade_diff_ci95"]
    dt = P["policy_net_r_per_trade"] - P["fixed_net_r_per_trade"]
    fx, po = P["fixed_net_r_per_capital_day"], P["policy_net_r_per_capital_day"]
    guard = not (fx is not None and fx > 0 and (po is None or po < fx))
    if dt >= min_gain_r and ci[0] > 0 and pe["folds_positive_per_trade"] >= min_folds and guard:
        return "pass", (f"policy - fixed {dt:+.4f} R/trade, CI {ci}, {pe['folds_positive_per_trade']}/4 folds; "
                        f"R/capital-day fixed {fx} policy {po}")
    if ci[1] < min_gain_r:
        return "fail", f"policy - fixed CI {ci} R/trade, upper < {min_gain_r}"
    return "indeterminate", (f"per-trade {dt:+.4f}, CI {ci}, folds+ {pe['folds_positive_per_trade']}, "
                             f"capital-day guard {'ok' if guard else 'BREACHED'} (fixed {fx}, policy {po})")


def write_verdict(out: Path, v: Dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "verdict.json").write_text(json.dumps(v, indent=2, default=str) + "\n")


def _strip(fe: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in fe.items() if not k.startswith("_")}


def run(a: argparse.Namespace, legs_override: Optional[List[dict]] = None) -> Dict[str, Any]:
    if legs_override is not None:
        legs, skipped = legs_override, []
    else:
        legs, skipped = load_legs(a.ledgers, a.leg_re)
    out = Path(a.out)
    trades, reports = build(legs, Path(a.cache_dir) if a.cache_dir else out / "candles",
                            datetime.now(timezone.utc))
    pop = (f"newest committed ledger per leg ({a.ledgers}), USDT legs whose re-walk passes the MFE control; "
           f"{len(trades)} trades on {sum(r['state'] == 'included' for r in reports)} legs; "
           f"fixed-bracket reconstruction (initial SL = -1 R, TP = K R), not the leg as traded")
    meas: Dict[str, Any] = {"legs": reports, "skipped": [{k: v for k, v in s.items() if k != "rows"} for s in skipped],
                            "construction": "fixed_bracket_reconstruction_from_ledger_plus_public_candles"}
    K = a.policy_tp_r
    if a.mode == "census":
        res = census(trades, a.tp_r)
        meas["census"] = res
        verdict, why = (("indeterminate", "no trades passed the control") if not trades else
                        verdict_census(res, K, a.pass_saving_r, a.fail_saving_r, a.min_n))
    else:
        feats = FEATURE_SETS[a.features]
        ref = None if a.ref == "p0" else FEATURE_SETS[a.ref]
        fe = fit_eval(trades, K, feats, ref_feats=ref) if trades else {"n_oos_trades": 0, "mean_gain_nats": None,
                                                                       "ci95": None, "calibration_slope": None,
                                                                       "folds_positive": 0, "reference": "-"}
        meas["model"] = {**_strip(fe), "features": feats, "K": K}
        if a.mode == "baseline":
            verdict, why = verdict_model(fe, a.min_gain_nats, a.min_folds, a.min_n)
        else:
            pe = policy_eval(trades, K, fe, feats, ref) if trades else {"n_oos_trades": 0}
            meas["policy"] = pe
            verdict, why = verdict_policy(pe, a.min_trade_gain_r, a.min_folds, a.min_n)
    v = {"verdict": verdict, "read_state": "measured" if trades else "unmeasured", "population": pop,
         "n": len(trades), "measurement": meas, "note": f"{a.mode}: {why}"}
    write_verdict(out, v)
    return v


def _args(argv: Optional[List[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["census", "baseline", "policy"], default="census")
    ap.add_argument("--ledgers", default=DEFAULT_GLOB)
    ap.add_argument("--leg-re", default=None)
    ap.add_argument("--tp-r", type=lambda s: [float(x) for x in s.split(",")], default=[1.0, 1.5, 2.0, 3.0])
    ap.add_argument("--policy-tp-r", type=float, default=2.0, help="K for the verdict, the model and the policy")
    ap.add_argument("--features", choices=sorted(FEATURE_SETS), default="simple")
    ap.add_argument("--ref", choices=["p0"] + sorted(FEATURE_SETS), default="dist",
                    help="what the model is graded against: p0 (closed form) or a calibrated reference model")
    ap.add_argument("--min-trade-gain-r", type=float, default=0.05)
    ap.add_argument("--pass-saving-r", type=float, default=0.25)
    ap.add_argument("--fail-saving-r", type=float, default=0.10)
    ap.add_argument("--min-gain-nats", type=float, default=0.002)
    ap.add_argument("--min-folds", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=300)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-test", action="store_true")
    from first_passage_paths import add_args  # the AS-TRADED reader's options (--paths / --harness-legs)
    add_args(ap)
    return ap.parse_args(argv)


# --------------------------------------------------------------------------- self-test
def _synthetic(drift_after_peak: bool, n_trades: int = 400, seed: int = 3) -> Tuple[pd.DataFrame, List[dict]]:
    """A candle series and long trades on it. drift_after_peak=False: a driftless random walk (Markov: the
    path so far says nothing beyond where price is now). True: a planted MOMENTUM TURN -- inside each trade,
    once the trade has been >= +1 R and then not made a new high for 5 bars, price drifts down 0.4/bar -- information that lives
    in bars-since-peak and giveback, which the distance-only state cannot see."""
    rng = np.random.default_rng(seed)
    n = n_trades * 40 + 200
    eps = rng.normal(0, 0.5, n)
    d = eps.copy()
    if drift_after_peak:
        c = 100 + np.cumsum(eps)
        for k in range(n_trades):
            ei = 150 + k * 40
            lvl, peak, since, owed = c[ei], c[ei], 0, 0.0
            for j in range(ei + 1, ei + 40):
                step = (0.4 if (since >= 5 and (peak - c[ei]) >= 2.0) else 0.0)  # stalled 5 bars after >= +1 R
                d[j] = eps[j] - step
                owed += step
                lvl += d[j]
                peak, since = (lvl, 0) if lvl > peak else (peak, since + 1)
            d[ei + 40] += owed  # repaid on the next trade's entry bar, outside every trade's walk
            c = 100 + np.cumsum(d)
    c = 100 + np.cumsum(d)
    c = np.maximum(c, 5.0)
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    rows = []
    for k in range(n_trades):
        ei = 150 + k * 40
        entry = c[ei]
        rows.append({"entry_time": str(ts[ei]), "exit_time": str(ts[ei + 30]), "entry": entry, "sl": entry - 2.0,
                     "direction": "long", "net_r": 0.0, "mfe_r": None, "cost_total_r": 0.05,
                     "cost_slippage_r": 0.02, "exit_reason": "timeout"})
    noise = np.abs(rng.normal(0, 0.15, n))
    df = pd.DataFrame({"timestamp": ts, "open": np.r_[c[0], c[:-1]], "high": c + noise, "low": c - noise, "close": c})
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    for r in rows:  # make the ledger's own mfe_r agree with the walk so the control passes
        ei = int(np.nonzero(ts == pd.Timestamp(r["entry_time"]))[0][0])
        r["mfe_r"] = round(max(0.0, float((df["high"].iloc[ei + 1:ei + 31].max() - r["entry"]) / 2.0)), 4)
    return df, rows


def _self_test() -> int:
    ok = True
    # 1. gambler's ruin is the closed form
    assert abs(float(p0(2.0, np.array([0.0]))[0]) - 2 / 3) < 1e-9
    # 2. first passage: SL before TP on a shared bar
    pth = {"adv": np.array([-0.2, -1.1]), "fav": np.array([0.5, 2.5]), "close_r": np.array([0.1, 0.0])}
    assert first_passage(pth, 2.0) == ("sl", 1, -1.0)
    # 3. ALIGNMENT (manager finding, PR #18112): ledger entry_time labels the ENTRY bar (open-time label on a native
    #    feed); the trade is entered at that bar's close, so its own high/low must NOT count. Plant the off-by-one:
    #    the entry bar wicks through the SL, every later bar holds and reaches TP. Walking from the entry bar would
    #    read SL-first; walking from entry + 1 (this file's convention) must read TP-first. Measured 2026-10-14 on
    #    31 legs / 4,143 committed trades: start@entry_bar breaches the initial SL before the exit bar on 412,
    #    start@entry+1 on 0 (trend_donchian_xrp_4h: 27/44 vs 0/44, as #18112 found).
    ts = pd.date_range("2025-01-01", periods=6, freq="4h", tz="UTC")
    al = pd.DataFrame({"timestamp": ts, "open": [100, 100, 100.5, 101, 102, 103],
                       "high": [100.5, 100.5, 101, 102, 104.5, 104], "low": [99.5, 97.0, 100, 100.5, 101.5, 102],
                       "close": [100, 100, 100.8, 101.8, 104, 103.5]})
    tr = {"entry": 100.0, "sl": 98.0, "direction": "long"}
    pa = walk_trade(al, indicators(al), 1, tr, 4)
    oc = first_passage(pa, 2.0)[0]
    early = float((al["low"].iloc[1] - 100.0) / 2.0)
    if not (oc == "tp" and early <= -1.0 and float(pa["adv"][0]) > -1.0):
        print(f"FAIL alignment: entry-bar wick counted (outcome {oc}, first adv {float(pa['adv'][0]):.2f})")
        ok = False
    with tempfile.TemporaryDirectory() as td:
        for drift, expect_model in ((False, ("fail", "indeterminate")), (True, ("pass",))):
            df, rows = _synthetic(drift)
            legs = [{"leg": "synth_1h", "symbol": "SYNUSDT", "tf": "1h", "rows": rows, "ledger": "-", "n": len(rows),
                     "frame": df}]
            base = ["--out", str(Path(td) / f"o{drift}"), "--policy-tp-r", "2.0", "--min-n", "50"]
            vc = run(_args(["--mode", "census"] + base), legs_override=legs)
            L = vc["measurement"]["legs"][0]
            if L["state"] != "included":
                print(f"FAIL control did not pass on synthetic data: {L}")
                ok = False
                continue
            vb = run(_args(["--mode", "baseline"] + base), legs_override=legs)
            if vb["verdict"] not in expect_model:
                print(f"FAIL drift={drift}: baseline verdict {vb['verdict']} not in {expect_model}: {vb['note']}")
                ok = False
            vp = run(_args(["--mode", "policy"] + base), legs_override=legs)
            nz = vp["measurement"]["policy"].get("null_policy_exit_share")
            if nz is None or nz >= 0.05:
                print(f"FAIL drift={drift}: null policy exit share {nz} (expected < 0.05)")
                ok = False
            if not drift and vp["verdict"] == "pass":
                print(f"FAIL drift=False: policy PASSED on a random walk: {vp['note']}")
                ok = False
            if drift and vp["verdict"] != "pass":
                print(f"FAIL drift=True: policy verdict {vp['verdict']}: {vp['note']}")
                ok = False
            print(f"  drift={drift}: census {vc['verdict']} ({vc['note']}); baseline {vb['verdict']} ({vb['note']}); "
                  f"policy {vp['verdict']} ({vp['note']})")
    print("SELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    a = _args(argv)
    if a.self_test:
        return _self_test()
    if not a.out:
        print("--out is required", file=sys.stderr)
        return 2
    if a.paths or a.harness_legs:  # AS TRADED (harness per-bar paths); --ledgers mode below is unchanged
        from first_passage_paths import run_paths
        v = run_paths(a)
    else:
        v = run(a)
    print(json.dumps({k: v[k] for k in ("verdict", "read_state", "n", "note")}, indent=2))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
