#!/usr/bin/env python3
"""M20 U5 — favourable:adverse excursion in ATR UNITS, per leg per week.

# wiring: manual-only — a research session runs this; a cadence is PROPOSED in
# the memo, not asserted here. See `--validate` before trusting any reading.

WHY IN ATR UNITS, WHICH IS THE WHOLE POINT.
`BL-20260911-MFE-OVER-MAE-IN-ATR-UNITS-IS-A-LEADING-INDICATOR-NOBODY-COMPUTES`:
every surface that could have caught the 2026-08-30 regime break was PnL-based,
"which is why the repo spent two sessions arguing attribution from a
provenance-contaminated PnL while a clean, provenance-free signal sat in the
candle data."

MI-278 U2b sharpened that argument after the row was filed, and it is now
stronger than "PnL is contaminated": **R is contaminated too.** The break-even
ratchet amends `order_packages.sl` on winners only, so a denominator of
`|entry - sl|` is inflated on exactly the population under study. ATR units are
immune to BOTH — they need no exit price, no provenance, no stop, and no
knowledge of which lever closed the trade.

THE TRUNCATION CONFOUND, AND WHY THE WINDOW IS FIXED. Measuring over each
trade's own LIFETIME manufactures the finding: a tighter stop closes a trade
sooner, and a shorter observation window mechanically lowers MFE and caps MAE.
So excursion is measured over a FIXED NUMBER OF HOURS FROM ENTRY -- a window
that depends on neither the geometry, nor when the trade closed, nor which
lever closed it. MI-275 §8 established this and reported the lifetime figures
only beside their control; this module implements the control and does not
offer the lifetime variant at all.

WHAT IT IS NOT. It is NOT `scripts/research/m31_mfe_parity.py`, which asks a
different question in different units: whether the HARNESS's `mfe_r` matches
LIVE `peak_r` for the same leg (a parity check, in R). This asks whether the
MARKET's favourable:adverse travel has moved (a regime read, in ATR). Checked
before building, per RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED.

AND IT IS A COMPLEMENT TO THE STREAK DETECTOR, NOT A DUPLICATE. MI-276 shipped
`src/runtime/losing_streak_alert.py`. That says THAT the book is bleeding; this
says WHY, and needs no provenance-clean PnL -- which is what made the streak
question hard. The backlog row warns that "two cadences landing in the same
window is how they come to disagree", so this module deliberately ships with NO
cadence and NO alarm: it computes and reports, and the carrier is a decision
recorded in the memo.

⚠️ A QUIET READING IS NOT EVIDENCE OF HEALTH until the ratio has been seen
MOVING on a known event. `--validate` runs it against 2026-08-30, the event it
was derived from, and REFUSES to report a clean bill if the move is not
reproduced.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: fixed hours-from-entry windows (MI-275 §8's control)
WINDOWS_H: Tuple[int, ...] = (4, 12, 24, 48)
#: the known event this instrument was derived from, used by --validate
KNOWN_EVENT = "2026-08-30T08:53:19"


def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    m = row.get("meta")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except Exception:                       # noqa: BLE001  # allow-silent: an unparseable meta yields {}, which drops the row as ungradeable — it can never become a reading
            return {}
    return m if isinstance(m, dict) else {}


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts).replace("Z", "+00:00").replace(" ", "T")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def excursion(candles: List[Dict[str, Any]], entry: float, direction: str,
              atr: float, t0: datetime, hours: int
              ) -> Optional[Tuple[float, float, int]]:
    """(MAE, MFE) in ATR units over [t0, t0+hours], plus the bar count.

    Returns None when the window is not FULLY covered by the candle data —
    a partly-covered window understates both excursions, and reporting it
    would be the truncation confound this module exists to remove.
    """
    if atr <= 0:
        return None
    t1 = t0 + timedelta(hours=hours)
    lo = hi = None
    n = 0
    for c in candles:
        ct = c.get("_dt")
        if ct is None or ct < t0 or ct > t1:
            continue
        n += 1
        c_hi, c_lo = c["high"], c["low"]
        hi = c_hi if hi is None else max(hi, c_hi)
        lo = c_lo if lo is None else min(lo, c_lo)
    if n == 0 or hi is None:
        return None
    last = max((c["_dt"] for c in candles), default=None)
    if last is None or last < t1:
        return None                     # window runs past the data end
    if direction == "long":
        mae, mfe = (entry - lo) / atr, (hi - entry) / atr
    else:
        mae, mfe = (hi - entry) / atr, (entry - lo) / atr
    return max(mae, 0.0), max(mfe, 0.0), n


def load_candles(symbol: str, interval: str, start: datetime, end: datetime
                 ) -> List[Dict[str, Any]]:
    """Binance Vision — globally reachable, unlike api.bybit.com from a runner."""
    sys.path.insert(0, str(REPO / "scripts" / "ops"))
    from fetch_backtest_candles import fetch_klines_binance_vision  # noqa: E402
    rows = fetch_klines_binance_vision(
        symbol, interval, int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    out = []
    for r in rows:
        ts = r.get("timestamp") or r.get("open_time") or r.get("t")
        # the fetcher already hands back a tz-aware datetime; an epoch-ms int is
        # accepted too so a second candle source cannot silently mis-parse.
        if isinstance(ts, datetime):
            when = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        elif isinstance(ts, (int, float)):
            when = datetime.fromtimestamp(float(ts) / 1000, tz=timezone.utc)
        else:
            continue
        try:
            out.append({"_dt": when, "high": float(r["high"]), "low": float(r["low"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def decision_population_pkg_ids(trades: List[Dict[str, Any]]) -> set:
    """Package ids of MI-271's population(), IMPORTED IN SHAPE not restated:
    closed AND NOT is_backtest AND pnl IS NOT NULL AND not the pairs sleeve.

    ⚠️ THIS IS THE POPULATION EVERY PREDECESSOR MEMO USES, and grading all
    packages instead is not a wider read — it is a DIFFERENT one. Rejected and
    never-opened packages have no trade behind them, and including them
    silently changed my own first reading of this instrument from MI-275's
    F:A 1.037 (n=18) to 0.385 (n=49). Population before instrument.
    """
    out = set()
    for t in trades:
        if (t.get("status") == "closed" and not t.get("is_backtest")
                and t.get("pnl") is not None
                and not str(t.get("strategy_name") or "").startswith("pairs_")):
            pid = t.get("order_package_id")
            if pid:
                out.add(str(pid))
    return out


def grade(packages: List[Dict[str, Any]], candle_interval: str,
          legs: Optional[set] = None, verbose: bool = True,
          pkg_id_filter: Optional[set] = None
          ) -> List[Dict[str, Any]]:
    """One row per (package, window). States are never collapsed."""
    usable, skipped = [], collections.Counter()
    for p in packages:
        s = str(p.get("strategy_name") or "")
        if legs is not None and s not in legs:
            continue
        if s.startswith("pairs_"):
            skipped["pairs_sleeve_excluded"] += 1
            continue
        if pkg_id_filter is not None and str(p.get("order_package_id")) not in pkg_id_filter:
            skipped["outside_decision_population"] += 1
            continue
        m = _meta(p)
        atr = m.get("atr")
        t0 = _parse(m.get("entry_time") or p.get("created_at"))
        e, d = p.get("entry"), str(p.get("direction") or "").lower()
        if not atr or atr <= 0:
            skipped["no_atr_on_package"] += 1
            continue
        if t0 is None or not e or d not in ("long", "short"):
            skipped["ungradeable_entry"] += 1
            continue
        usable.append((p, s, float(atr), t0, float(e), d))
    if verbose:
        print(f"  packages usable: {len(usable)}; skipped: {dict(skipped)}")
    if not usable:
        return []

    by_symbol: Dict[str, List[Any]] = collections.defaultdict(list)
    for rec in usable:
        by_symbol[str(rec[0].get("symbol"))].append(rec)

    rows: List[Dict[str, Any]] = []
    for sym, recs in sorted(by_symbol.items()):
        t_lo = min(r[3] for r in recs) - timedelta(hours=2)
        t_hi = max(r[3] for r in recs) + timedelta(hours=max(WINDOWS_H) + 4)
        try:
            candles = load_candles(sym, candle_interval, t_lo, t_hi)
        except Exception as exc:            # noqa: BLE001  # allow-silent: a fetch failure is COUNTED as could_not_fetch, never graded as a quiet reading
            if verbose:
                print(f"  {sym}: could_not_fetch — {type(exc).__name__}: {exc}")
            continue
        if verbose:
            print(f"  {sym}: {len(candles)} candles, {len(recs)} packages")
        for p, s, atr, t0, e, d in recs:
            for w in WINDOWS_H:
                ex = excursion(candles, e, d, atr, t0, w)
                if ex is None:
                    continue
                mae, mfe, nbars = ex
                iso = t0.isocalendar()
                rows.append({
                    "order_package_id": p.get("order_package_id"),
                    "strategy": s, "symbol": sym, "direction": d,
                    "entry_at": t0.isoformat(), "window_h": w,
                    "iso_week": f"{iso[0]}-W{iso[1]:02d}",
                    "mae_atr": round(mae, 4), "mfe_atr": round(mfe, 4),
                    "fa_ratio": round(mfe / mae, 4) if mae > 0 else None,
                    "bars": nbars,
                })
    return rows


def summarise(rows: List[Dict[str, Any]], key) -> List[Dict[str, Any]]:
    g: Dict[Any, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    out = []
    for k, v in sorted(g.items(), key=lambda kv: str(kv[0])):
        mae = statistics.median([x["mae_atr"] for x in v])
        mfe = statistics.median([x["mfe_atr"] for x in v])
        out.append({"key": k, "n": len(v), "mae_atr": round(mae, 3),
                    "mfe_atr": round(mfe, 3),
                    # the ratio OF THE MEDIANS, matching MI-275 §8 — not the
                    # median of the per-row ratios, which is a different number
                    "fa_ratio": round(mfe / mae, 3) if mae > 0 else None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packages", required=True)
    ap.add_argument("--trades", help=(
        "journal trades JSON. When given, grading is restricted to MI-271's DECISION "
        "POPULATION (closed, non-backtest, pnl NOT NULL, non-pairs) -- which is what every "
        "predecessor memo measures. Omit at your peril: the all-packages read is a DIFFERENT "
        "population, not a wider one."))
    ap.add_argument("--interval", default="15", help="candle interval (bybit-style: 5,15,60)")
    ap.add_argument("--legs", default="", help="CSV of legs; empty = all non-pairs")
    ap.add_argument("--validate", action="store_true",
                    help="grade the 2026-08-30 event and REFUSE a clean bill if it is not reproduced")
    ap.add_argument("--out")
    args = ap.parse_args()

    pkgs = json.loads(Path(args.packages).read_text())
    legs = set(x.strip() for x in args.legs.split(",") if x.strip()) or None
    pkg_filter = None
    if args.trades:
        pkg_filter = decision_population_pkg_ids(json.loads(Path(args.trades).read_text()))
        print(f"decision population: {len(pkg_filter)} package ids from {args.trades}")
    else:
        print("⚠️ NO --trades: grading ALL packages, which is a DIFFERENT population from "
              "every predecessor memo (it includes rejected and never-opened packages).")
    print(f"grading {len(pkgs)} packages at interval {args.interval}"
          + (f", legs={sorted(legs)}" if legs else ", all non-pairs legs"))
    rows = grade(pkgs, args.interval, legs, pkg_id_filter=pkg_filter)
    if not rows:
        print("::error::no gradeable (package, window) rows — this is NOT a clean reading.",
              file=sys.stderr)
        return 2
    print(f"\ngraded {len(rows)} (package, window) rows\n")

    print("=== per WINDOW, all graded legs ===")
    print(f"{'window':>8s} {'n':>5s} {'MAE atr':>9s} {'MFE atr':>9s} {'F:A':>7s}")
    for r in summarise(rows, lambda x: x["window_h"]):
        print(f"{str(r['key'])+'h':>8s} {r['n']:5d} {r['mae_atr']:9.3f} {r['mfe_atr']:9.3f}"
              f" {str(r['fa_ratio']):>7s}")

    if args.validate:
        print(f"\n=== VALIDATION — the known event, {KNOWN_EVENT} ===")
        print("  a quiet reading is not evidence of health until the ratio is seen MOVING")
        moved = []
        for w in WINDOWS_H:
            sub = [r for r in rows if r["window_h"] == w]
            pre = [r for r in sub if r["entry_at"][:19] < KNOWN_EVENT]
            post = [r for r in sub if r["entry_at"][:19] >= KNOWN_EVENT]
            if len(pre) < 3 or len(post) < 3:
                print(f"  {w:3d}h: n too thin (pre {len(pre)} / post {len(post)}) — NOT GRADED")
                continue
            a = summarise(pre, lambda x: 0)[0]
            b = summarise(post, lambda x: 0)[0]
            ra, rb = a["fa_ratio"], b["fa_ratio"]
            ok = ra is not None and rb is not None and rb < ra
            moved.append(ok)
            print(f"  {w:3d}h: F:A {ra} (n={a['n']}) -> {rb} (n={b['n']})"
                  f"   MAE {a['mae_atr']}->{b['mae_atr']}  MFE {a['mfe_atr']}->{b['mfe_atr']}"
                  f"   {'MOVED DOWN ✓' if ok else 'did NOT move down'}")
        if not moved:
            print("::error::validation could not run at any window — the instrument is UNVALIDATED "
                  "and its readings must not be used.", file=sys.stderr)
            return 2
        if not any(moved):
            print("::error::the instrument did NOT reproduce the known 2026-08-30 move at any "
                  "window. Do not read a quiet result from it as health.", file=sys.stderr)
            return 1
        print(f"  => reproduced at {sum(moved)} of {len(moved)} graded windows.")

    print("\n=== per LEG per ISO WEEK (the proposed durable surface), 24h window ===")
    wk = [r for r in rows if r["window_h"] == 24]
    for r in summarise(wk, lambda x: (x["strategy"], x["iso_week"])):
        if r["n"] < 3:
            continue
        print(f"  {r['key'][0]:24s} {r['key'][1]:10s} n={r['n']:3d}  "
              f"MAE {r['mae_atr']:6.3f}  MFE {r['mfe_atr']:6.3f}  F:A {r['fa_ratio']}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"windows_h": list(WINDOWS_H), "rows": rows,
             "by_window": summarise(rows, lambda x: x["window_h"]),
             "by_leg_week_24h": summarise(wk, lambda x: (x["strategy"], x["iso_week"]))},
            indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
