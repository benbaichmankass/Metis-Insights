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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TF_S = {"5m": 300, "15m": 900, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}
HOLDING_PAYS_R = 0.25
CHOP_BAND_R = 0.25
# M21 E-3c: trailing window (bars) for the decision-bar ATR-percentile
# feature — matches the shipped vol-at-entry gate's default (#6434).
ENTRY_ATR_PCTL_WINDOW = 200


_HEAVY_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_HEAVY_REPO / "scripts" / "ml"))
from _heavy_queue import take_heavy_queue  # noqa: E402


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


def load_harness_trades(paths: List[Path], report: Optional[dict] = None) -> List[dict]:
    """Load emitted harness trades, COUNTING what is dropped and why.

    ⚠️ THE SKIP COUNTERS ARE THE POINT, not decoration. Every unusable row was
    silently `continue`d and the only downstream signal was `no trades loaded`
    on an empty list — an unasserted denominator (CLAUDE.md § "Diagnostic
    provenance", sub-class C): a total drop and an empty input file produce the
    identical message, so a reader cannot tell "the harness emitted nothing"
    from "the harness emitted 1170 rows in a shape I reject".

    That is not hypothetical. Measured 2026-08-12: the ict_scalp round emitted
    387 + 422 + 361 = 1170 trades and this function returned ZERO, because
    `backtest_ict_scalp.py` wrote `entry_time` but no `exit_time` — one missing
    key dropping 100% of the population, reported as "no trades loaded". The
    round had never been runnable for that family, and the message could not
    say so.
    """
    trades = []
    skipped: Counter = Counter()
    seen = 0
    # PER-FAMILY accounting (2026-08-13).
    # BL-20260813-E0-LOAD-STAGE-DROPS-INVISIBLE-ON-PARTIAL-FAILURE
    # (kept on ONE line: the id was previously wrapped mid-token, so the full id
    # appeared nowhere in this file and a grep for it found nothing here.)
    # The aggregate counters below say HOW MANY rows were
    # rejected and WHY, but not WHOSE — so "trend dropped 100% while pullback
    # loaded fine" was not computable from the report at all, which is the exact
    # case the counters exist for. `family_of` reads only `strategy`, which
    # survives every rejection reason here, so a row can be attributed even when
    # the keys that disqualified it are missing.
    fam_seen: Counter = Counter()
    fam_loaded: Counter = Counter()
    for p in paths:
        for line in p.open():
            line = line.strip()
            if not line:
                continue
            seen += 1
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                skipped["bad_json"] += 1
                # Unparseable: no strategy to attribute it to. It gets its own
                # bucket rather than being dropped from the denominator or
                # charged to a neighbouring family.
                fam_seen["<unparseable>"] += 1
                continue
            # `<no_strategy>` is kept DISTINCT from a resolved family: a row the
            # harness emitted without a strategy name is its own defect (three
            # harnesses did exactly that until #8889), and folding it into
            # "unknown" would hide it behind a legitimate bucket.
            _fam = family_of(r.get("strategy")) if r.get("strategy") else "<no_strategy>"
            fam_seen[_fam] += 1
            t0 = _epoch(r.get("entry_time"))
            t1 = _epoch(r.get("exit_time"))
            entry, sl = _f(r.get("entry")), _f(r.get("sl"))
            if None in (t0, t1, entry, sl):
                # Name the MISSING FIELD, not just "unusable" — the whole cost
                # of the 2026-08-12 failure was not knowing which key was absent.
                for key, val in (("entry_time", t0), ("exit_time", t1),
                                 ("entry", entry), ("sl", sl)):
                    if val is None:
                        skipped[f"missing:{key}"] += 1
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
                # M21 E-3: live-parity setup-quality feature (breakout /
                # trigger depth in ATRs, clipped 0..1) — absent in older
                # emits, None-safe downstream.
                "confidence": _f(r.get("confidence")),
            })
            fam_loaded[_fam] += 1
    if report is not None:
        report["rows_seen"] = seen
        report["rows_loaded"] = len(trades)
        report["skipped"] = dict(skipped)
        report["per_family_load"] = {
            fam: {"seen": n, "loaded": fam_loaded.get(fam, 0)}
            for fam, n in sorted(fam_seen.items())
        }
        # STARVED = emitted rows, loaded NONE. Reported as its own list so the
        # asymmetric case (one family at 0 beside a healthy sibling) is a value
        # a caller can branch on, not a comparison every reader must do by eye.
        report["families_starved"] = sorted(
            fam for fam, n in fam_seen.items() if n > 0 and fam_loaded.get(fam, 0) == 0
        )
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


def load_live_trades(db: Path, instruments: Path,
                     report: Optional[dict] = None,
                     legs: Optional[List[str]] = None) -> List[dict]:
    """Closed, non-backtest, strategy-attributed journal trades with
    resolvable entry/sl geometry (same exclusions as m20_exit_analysis:
    intent_reduce legs, adopted orphans, superseded flap rows). final_R
    prefers journal pnl / (|entry-sl| * qty * contract multiplier); rows
    where that isn't derivable fall back to the last bar mark (tagged).

    ⚠️ REPORTS THE TABLE COUNT, not just what survived the filters — because
    "this DB has no trades at all" and "this DB has trades, none of which
    qualify" are opposite problems and the caller could not tell them apart.

    Measured 2026-08-12: an E1 round reported `live_trades: 0` and the
    live-sign-agreement arm of the E1->E2 gate was silently skipped. The cause
    was not data accrual — the trainer holds TWO journal copies, an 8.2 MB stub
    at `<repo>/trade_journal.db` (mtime Aug 2, `trades` EMPTY) beside the real
    767 MB synced copy at `<repo>/data/trade_journal.db` (4585 trades, 1087
    closed, live scalp legs present). The round was pointed at the stub, and
    nothing in the output distinguished that from a genuine absence of live
    trades. `trades_in_table` makes the wrong-DB case self-evident: 0 there
    means the DB is empty, while a large count with 0 loaded means the filters
    excluded everything.

    ⚠️ `legs` SCOPES THE LIVE ARM TO THE ROUND'S OWN LEGS (2026-09-02,
    `BL-20260813-EXIT-HEAD-LIVE-ARM-DROPPED-ON-NO-CANDLES` defect 1). Without
    it this pulls EVERY strategy-attributed closed trade in the journal and the
    caller then buckets them with `family_of()`, which keys on the strategy
    NAME — so a scalp round asking for `ict_scalp_xrp_15m` also inhaled
    `xrp_pullback_2h` and friends and manufactured `donchian` / `pullback`
    families it never asked for. MEASURED 2026-08-13 (trainer relays
    #8854/#8855): `runtime_logs/m20_exit_head/scalp_15m/build_report.json`
    showed `families: donchian {live: 3}, pullback {live: 6}` beside
    `ict_scalp_xrp_15m {harness: 353}` — the graded leg's own live count was
    ZERO while two families the round never named carried live rows. Those
    spurious families then went to training with 0 harness rows and died on
    `ValueError: Expected 2D array, got 1D array instead: array=[]`.

    ⚠️ THE FILTER IS REPORTED, NEVER SILENT. `legs_filter_state` is three
    values and they are not collapsed — `not_requested` (no scoping asked for)
    is not `applied_no_match` (we scoped and the journal holds nothing for
    these legs), and reading the second as the first is how "the live arm is
    empty" gets blamed on data accrual again. `legs_dropped` names the strategy
    names that were excluded and their row counts, so the pull this prevents
    stays VISIBLE rather than merely fixed.
    """
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
    total_in_table = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    con.close()
    rows_matching_filters = len(rows)
    # Applied in Python, AFTER the SELECT, so `rows_matching_filters` keeps
    # meaning exactly what it meant before this flag existed — a reader
    # comparing that number across rounds must not be silently handed a
    # different population under the same key.
    wanted = {str(x) for x in (legs or []) if str(x).strip()}
    dropped: Dict[str, int] = {}
    if wanted:
        kept = []
        for r in rows:
            name = str(r["strategy_name"])
            if name in wanted:
                kept.append(r)
            else:
                dropped[name] = dropped.get(name, 0) + 1
        rows = kept
    if report is not None:
        report["db"] = str(db)
        report["trades_in_table"] = total_in_table
        report["rows_matching_filters"] = rows_matching_filters
        report["legs_filter"] = sorted(wanted)
        report["legs_filter_state"] = (
            "not_requested" if not wanted
            else ("applied" if rows else "applied_no_match"))
        report["rows_after_leg_filter"] = len(rows)
        report["legs_dropped"] = dict(sorted(dropped.items()))
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
    entry_conf = _f(tr.get("confidence"))
    # M21 E-3 SIGNAL-BAR entry features (constant per trade): computed at the
    # DECISION bar k0 = i0-1 (ts == t_open — the bar whose close triggered
    # the entry), NOT the first in-trade bar, so the live allocator scorer
    # (which only has the decision bar at signal time) is train/serve
    # identical. All strictly bars <= the decision bar — no lookahead.
    k0 = i0 - 1
    entry_mom_8 = entry_dc_dist = None
    entry_hour = entry_dow = None
    entry_atr_pctl = None
    if k0 >= 0:
        c0 = candles[k0]
        cl0 = c0["close"]
        if k0 >= 8 and candles[k0 - 8]["close"] > 0:
            entry_mom_8 = round(
                (1.0 if is_long else -1.0) * (cl0 / candles[k0 - 8]["close"] - 1.0), 6)
        atr0 = atrs[k0]
        if atr0:
            lo20 = min(c["low"] for c in candles[max(0, k0 - 19):k0 + 1])
            hi20 = max(c["high"] for c in candles[max(0, k0 - 19):k0 + 1])
            entry_dc_dist = round((cl0 - (lo20 + hi20) / 2.0) / atr0, 4)
        # M21 E-3c decision-bar ATR trailing percentile (the causal
        # vol-at-entry signal shipped as an ENTRY GATE #6434 — here as a
        # continuous ML feature). Rank of atrs[k0] within the trailing
        # ENTRY_ATR_PCTL_WINDOW bars (INCLUDING the decision bar; pct rank,
        # ties = fraction <=), matching the harness lever's
        # rolling(window).rank(pct=True). None until the window fills (no
        # lookahead, fail-permissive) so the trainer drops those rows.
        w0 = k0 - ENTRY_ATR_PCTL_WINDOW + 1
        if w0 >= 0:
            window = atrs[w0:k0 + 1]
            if all(a is not None for a in window) and atrs[k0] is not None:
                le = sum(1 for a in window if a <= atrs[k0])
                entry_atr_pctl = round(le / len(window), 4)
        ts0 = datetime.fromtimestamp(c0["t"], tz=timezone.utc)
        entry_hour, entry_dow = ts0.hour, ts0.weekday()
    for r in out:
        r["first_touch_1r"] = first_touch_1r
        r["reaches_2r"] = reaches_2r
        # M21 E-3 entry-time features (constant per trade; confidence is None
        # when the source emit predates the field or the trade is live).
        r["entry_confidence"] = entry_conf
        r["entry_mom_8"] = entry_mom_8
        r["entry_dc_dist_atr"] = entry_dc_dist
        r["entry_hour"] = entry_hour
        r["entry_dayofweek"] = entry_dow
        r["entry_atr_pctl"] = entry_atr_pctl  # M21 E-3c
    return out


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", action="append", default=[], metavar="PATH",
                   help="Harness --emit-trades JSONL (repeatable).")
    p.add_argument("--db", default=None,
                   help="trade_journal.db for live closed trades.")
    p.add_argument("--legs", default=None, metavar="CSV",
                   help="Restrict the LIVE arm (--db) to these strategy_name "
                        "values. WITHOUT IT the live arm loads every "
                        "strategy-attributed closed trade in the journal, and "
                        "family_of() then buckets same-symbol siblings into "
                        "families the round never asked for -- which both "
                        "starves the graded leg's own live count and creates "
                        "0-harness families that crash training "
                        "(BL-20260813-EXIT-HEAD-LIVE-ARM-DROPPED-ON-NO-CANDLES). "
                        "Reported as `legs_filter_state` in build_report.json; "
                        "`not_requested` and `applied_no_match` are NOT the "
                        "same answer. No effect without --db.")
    p.add_argument("--instruments", default="config/instruments.yaml",
                   help="instruments.yaml for contract_value_usd multipliers.")
    p.add_argument("--candles", action="append", default=[],
                   metavar="SYMBOL=CSV",
                   help="Per-symbol candle CSV the trades ran on (repeatable).")
    p.add_argument("--tf", required=True, choices=sorted(TF_S),
                   help="Native TF to resample candles to (one per build).")
    p.add_argument("--out", required=True, help="Output dataset dir.")
    a = p.parse_args(argv[1:])
    # Trainer heavy-job queue, taken at the ENTRYPOINT so every caller
    # (incl. a direct relay invocation) is covered — see scripts/ml/
    # _heavy_queue.py. Bound to a name: the flock releases when the fd
    # closes, so letting this be collected would silently unlock.
    _heavy_lock = take_heavy_queue("build_exit_head_dataset")  # noqa: F841

    candle_map: Dict[str, Path] = {}
    for spec in a.candles:
        sym, _, path = spec.partition("=")
        if not path:
            print(f"bad --candles spec: {spec}", file=sys.stderr)
            return 2
        candle_map[sym] = Path(path)

    harness_report: dict = {}
    trades = load_harness_trades([Path(t) for t in a.trades], harness_report)
    live_report: dict = {}
    if a.db:
        live = load_live_trades(
            Path(a.db), Path(a.instruments), live_report,
            legs=[x.strip() for x in (a.legs or "").split(",") if x.strip()])
        trades += live
        # ALWAYS state the live-source population. The E1->E2 gate needs the
        # live set to agree in sign, so a silent 0 disables a gate arm.
        print(f"live source {live_report.get('db')}: "
              f"{live_report.get('trades_in_table')} rows in `trades`, "
              f"{live_report.get('rows_matching_filters')} matched filters, "
              f"{len(live)} usable", file=sys.stderr)
        if not live_report.get("trades_in_table"):
            print("    ^ that DB's `trades` table is EMPTY — this is a "
                  "WRONG-DB symptom, not evidence that no live trades exist. "
                  "Check for a second journal copy (e.g. <repo>/data/).",
                  file=sys.stderr)
    if not trades:
        # State the population and the reason. "no trades loaded" over a
        # 1170-row input is a different failure from the same message over an
        # empty one, and the old message could not tell them apart.
        print(f"no trades loaded — read {harness_report.get('rows_seen', 0)} "
              f"harness row(s), loaded {harness_report.get('rows_loaded', 0)}",
              file=sys.stderr)
        for reason, n in sorted((harness_report.get("skipped") or {}).items(),
                                key=lambda kv: -kv[1]):
            print(f"    dropped {n}: {reason}", file=sys.stderr)
        if harness_report.get("rows_seen"):
            print("    ^ the harness DID emit rows; they were rejected on "
                  "shape, not absent. Fix the emit schema, not the data.",
                  file=sys.stderr)
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

    # THE TWO DROP STAGES ARE REPORTED SEPARATELY AND BOTH ALWAYS.
    #
    # `skipped` above counts only the CANDLE stage (a row that reached here and
    # had no candles / produced no rows). The LOAD stage — a row rejected on
    # shape, before it was ever a trade — was counted into `harness_report` and
    # then printed ONLY inside the `if not trades:` total-failure branch. So a
    # PARTIAL drop was invisible, which is precisely the case the counters
    # exist for: measured 2026-08-13, the 1d round dropped all 371 trend rows
    # at the load stage (`missing:exit_time`/`entry`/`sl`) while 578 pullback
    # rows loaded fine, and `build_report.json` said only
    # `{"no_candles": 697, "unresolvable": 63}` — no trace of the 371. Reading
    # that report, the trend family simply did not exist, and `trades_in: 1332`
    # counted the survivors, so even the denominator gave nothing away.
    #
    # The instrumentation added after the 2026-08-12 scalp incident covered the
    # failure that had already happened (a TOTAL drop) and not the one that had
    # not (a partial one). Surfacing it unconditionally is the fix.
    #
    # They are NOT merged into one `skipped` dict: "rejected on shape" and
    # "had no candles" are different failures with different fixes, and
    # collapsing them would re-create the ambiguity this is correcting.
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tf": a.tf, "trades_in": len(trades), "skipped": skipped,
        "skipped_at_load": dict(harness_report.get("skipped") or {}),
        "rows_seen_at_load": harness_report.get("rows_seen"),
        "rows_loaded_at_load": harness_report.get("rows_loaded"),
        # Per-family load split + the starved set (2026-08-13). Surfacing the
        # AGGREGATE drop counts was necessary and NOT sufficient: the number was
        # computable all along and nobody looked, because nothing said which
        # family it belonged to. These two make the asymmetric case — one family
        # at 0 beside a healthy sibling — a value rather than an inference.
        "per_family_load": harness_report.get("per_family_load") or {},
        "families_starved": harness_report.get("families_starved") or [],
        "holding_pays_threshold_r": HOLDING_PAYS_R, "families": {},
    }
    # And say it on stderr too, where the round driver's log will carry it —
    # a report nobody opens is not an alert.
    if report["skipped_at_load"]:
        seen, loaded = report["rows_seen_at_load"], report["rows_loaded_at_load"]
        print(f"LOAD-STAGE DROPS: {seen} harness row(s) read, {loaded} loaded",
              file=sys.stderr)
        for reason, n in sorted(report["skipped_at_load"].items(),
                                key=lambda kv: -kv[1]):
            print(f"    dropped {n}: {reason}", file=sys.stderr)

    # ── THE PER-FAMILY LINE, PRINTED WHETHER OR NOT ANYTHING WAS DROPPED ─────
    #
    # Unconditional on purpose. `skipped_at_load` being empty is exactly how a
    # clean-looking report hid a starved family before: a reader saw no drops
    # and stopped. Printing `loaded N of M` for every family means the healthy
    # case states its own denominator, so a 0 is visible by CONTRAST rather than
    # by knowing to go looking for it.
    pfl = report["per_family_load"]
    if pfl:
        print("LOAD BY FAMILY:", file=sys.stderr)
        for fam, c in sorted(pfl.items()):
            mark = "  <-- STARVED" if c["seen"] > 0 and c["loaded"] == 0 else ""
            print(f"    {fam}: loaded {c['loaded']} of {c['seen']}{mark}",
                  file=sys.stderr)

    # ── THE LOUD SIGNAL: a family at zero BESIDE a healthy sibling ───────────
    #
    # This is the specific shape the row was filed for and the one a reader is
    # least likely to catch by eye, because the report looks healthy: families
    # are present, rows are non-zero, nothing is obviously wrong. It cost two
    # misdiagnoses on 2026-08-13 before a key-set diff found the cause.
    #
    # NOT a non-zero exit, deliberately. A round can legitimately be invoked on
    # inputs where one family has no emitted rows at all, and failing the build
    # would make the round driver's own error path the thing that needs
    # explaining. The banner is unmissable and the `families_starved` field is
    # machine-readable — which is what the row's resolution criteria asked for.
    starved = report["families_starved"]
    healthy = [f for f, c in pfl.items() if c["loaded"] > 0]
    if starved and healthy:
        print("", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print("!! FAMILY STARVED AT LOAD — 100% of its rows were rejected on SHAPE,",
              file=sys.stderr)
        print("!! while a sibling family in the SAME round loaded fine.",
              file=sys.stderr)
        for fam in starved:
            print(f"!!   {fam}: loaded 0 of {pfl[fam]['seen']} emitted row(s)",
                  file=sys.stderr)
        print(f"!! healthy in the same round: {', '.join(sorted(healthy))}",
              file=sys.stderr)
        print("!! Every verdict this round produces EXCLUDES the starved family.",
              file=sys.stderr)
        print("!! Check `skipped_at_load` above for the missing key, then fix the",
              file=sys.stderr)
        print("!! harness emit — this is BL-20260813-TREND-SQUEEZE-FVG-HARNESSES-EMIT-NO-SYMBOL.",
              file=sys.stderr)
        print("=" * 72, file=sys.stderr)
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
