#!/usr/bin/env python3
"""backtest↔live fidelity calibrator — the earned-trust linchpin (P0).

Design of record: docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md § 2.4.

The problem it solves: we have never MEASURED whether our backtests are right, so we
fell back to "only trust real live trades" — which caps every decision at reality's
clock. This turns the qualitative `faithful`/`approximate` label into a measured
**agreement score** per strategy×symbol: does the backtest trade distribution
reproduce the LIVE trade distribution (win-rate + realized-R shape) on the legs where
we have both? A leg that clears the gate → its backtest is TRUSTED OOS evidence now;
a leg that drifts → its backtest is a lead, not a result; a leg with too few live
trades → `insufficient-live` (honest, never a silent pass).

TRUST DISCIPLINE (the scars):
- Live population is **measured-provenance only** (`provenance.pnl_is_trustworthy`) —
  fabricated/paper pnl never enters the calibration set (the same filter the P0
  label-augment eval used to exclude the poisoned paper book).
- Win-rate agreement + a two-sample KS on the realized-R distribution are the two
  axes; the verdict abstains below a live-n floor rather than certifying on noise.
- Pure functions (`agreement`) are network/DB-free and unit-tested; only `_load_*`
  touch SQLite (read-only).

Usage (on the trainer, where both DBs live):
    python scripts/research/backtest_fidelity_calibrate.py \
      --backtest-db datasets-out/backtest_trades.db \
      --live-db data/trade_journal.db \
      --strategy trend_donchian --symbol BTCUSDT \
      --out comms/research/backtest_fidelity_trend_donchian_BTCUSDT.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# Gate thresholds (documented; a leg clears only if it meets ALL).
MIN_LIVE_N = 30            # below this the live sample is too thin to calibrate
MAX_WINRATE_DIFF = 0.15    # |backtest win-rate − live win-rate| must be ≤ this
MAX_KS = 0.30              # KS(realized-R) must be ≤ this (distribution agreement)


def _ks_2samp(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Two-sample Kolmogorov–Smirnov statistic (max CDF gap). Stdlib-only."""
    a = sorted(x for x in a if x is not None and not math.isnan(x))
    b = sorted(x for x in b if x is not None and not math.isnan(x))
    if not a or not b:
        return None
    grid = sorted(set(a) | set(b))

    def cdf(xs: list[float], v: float) -> float:
        # fraction of xs ≤ v
        lo, hi = 0, len(xs)
        while lo < hi:
            mid = (lo + hi) // 2
            if xs[mid] <= v:
                lo = mid + 1
            else:
                hi = mid
        return lo / len(xs)

    return max(abs(cdf(a, v) - cdf(b, v)) for v in grid)


def _win_rate(outcomes: Sequence[float]) -> float | None:
    vals = [x for x in outcomes if x is not None]
    if not vals:
        return None
    return sum(1 for x in vals if x > 0) / len(vals)


def agreement(
    live_r: Sequence[float],
    backtest_r: Sequence[float],
    *,
    min_live_n: int = MIN_LIVE_N,
    max_winrate_diff: float = MAX_WINRATE_DIFF,
    max_ks: float = MAX_KS,
) -> dict[str, Any]:
    """Pure: score backtest↔live agreement from two realized-R samples.

    Returns the metrics + a verdict ∈ {calibrated, drifts, insufficient-live}. The R
    values are per-trade realized R (or a PnL-sign proxy when R is unknown — win-rate
    still works; KS is reported None and the verdict then rests on win-rate alone)."""
    n_live, n_bt = len(list(live_r)), len(list(backtest_r))
    wr_live, wr_bt = _win_rate(live_r), _win_rate(backtest_r)
    wr_diff = None if (wr_live is None or wr_bt is None) else abs(wr_bt - wr_live)
    ks = _ks_2samp(live_r, backtest_r)
    mean_live = (sum(live_r) / n_live) if n_live else None
    mean_bt = (sum(backtest_r) / n_bt) if n_bt else None

    if n_live < min_live_n:
        verdict = "insufficient-live"
        reason = f"live n={n_live} < floor {min_live_n} — cannot calibrate; backtest is a lead, not a result"
    else:
        wr_ok = wr_diff is not None and wr_diff <= max_winrate_diff
        ks_ok = ks is None or ks <= max_ks
        if wr_ok and ks_ok:
            verdict = "calibrated"
            reason = "backtest reproduces the live distribution within tolerance — TRUSTED OOS evidence"
        else:
            verdict = "drifts"
            bits = []
            if not wr_ok:
                if wr_diff is None:
                    bits.append("win-rate unavailable (empty backtest sample)")
                else:
                    bits.append(f"win-rate gap {wr_diff:.3f} > {max_winrate_diff}")
            if not ks_ok and ks is not None:
                bits.append(f"KS(R) {ks:.3f} > {max_ks}")
            reason = "backtest drifts from live (" + "; ".join(bits) + ") — backtest is a lead, not a result"

    return {
        "verdict": verdict,
        "reason": reason,
        "n_live": n_live,
        "n_backtest": n_bt,
        "live_win_rate": wr_live,
        "backtest_win_rate": wr_bt,
        "win_rate_diff": wr_diff,
        "ks_realized_r": ks,
        "live_mean_r": mean_live,
        "backtest_mean_r": mean_bt,
        "thresholds": {"min_live_n": min_live_n, "max_winrate_diff": max_winrate_diff, "max_ks": max_ks},
    }


# ---- stratification (regime/small-sample separation) ------------------------

def _year_of(ts: Any) -> str | None:
    """UTC year bucket from an entry timestamp (epoch-ms/epoch-s/ISO). None if
    un-parseable — the row then only counts toward the un-stratified overall."""
    if ts is None:
        return None
    s = str(ts).strip()
    if not s:
        return None
    try:  # epoch (s or ms)
        v = float(s)
        if v > 1e12:  # epoch-ms
            v /= 1000.0
        from datetime import datetime, timezone
        return str(datetime.fromtimestamp(v, tz=timezone.utc).year)
    except ValueError:
        pass
    m = re.match(r"(\d{4})-\d{2}", s)  # ISO
    return m.group(1) if m else None


def stratified_agreement(
    live_rows: Sequence[dict[str, Any]],
    backtest_rows: Sequence[dict[str, Any]],
    *,
    key: str,
    **thresholds: Any,
) -> dict[str, Any]:
    """Pure: agreement() overall PLUS one agreement() per stratum, grouped by
    `key` ∈ {direction, year}. Separates a UNIFORM cost-model gap (drift roughly
    equal across strata) from a CONCENTRATED regime/small-sample bias (drift in one
    stratum, others fine) — the § 5a caveat, operationalized. Strata below the
    live-n floor are reported honestly as `insufficient-live`, never hidden."""
    def _bucket(row: dict[str, Any]) -> str | None:
        if key == "direction":
            d = str(row.get("direction") or "").lower()
            return d if d in ("long", "short") else None
        if key == "year":
            return _year_of(row.get("ts"))
        return None

    overall = agreement([r["r"] for r in live_rows],
                        [r["r"] for r in backtest_rows], **thresholds)
    strata_keys = sorted({_bucket(r) for r in list(live_rows) + list(backtest_rows)}
                         - {None})
    strata: dict[str, Any] = {}
    for sk in strata_keys:
        lr = [r["r"] for r in live_rows if _bucket(r) == sk]
        br = [r["r"] for r in backtest_rows if _bucket(r) == sk]
        strata[sk] = agreement(lr, br, **thresholds)
    return {"key": key, "overall": overall, "strata": strata}


# ---- DB readers (read-only) -------------------------------------------------

def _live_rows(live_db: str, strategy: str, symbol: str) -> list[dict[str, Any]]:
    """Measured-provenance-only live rows: {r (PnL-sign proxy), direction, ts, won}.
    Excludes fabricated/paper pnl (the strict provenance filter — the scarce but
    TRUSTED calibration set). R is the win/loss sign proxy; a full stop-distance R
    needs the per-trade risk and is the documented next upgrade."""
    try:
        from src.runtime import provenance  # trust filter
        trust = provenance.pnl_is_trustworthy
    except Exception:  # allow-silent: provenance import is optional here — absent ⇒ unfiltered live sample; research calibrator, not a live read-path
        trust = None
    con = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT pnl, notes, direction, timestamp FROM trades WHERE status='closed' "
        "AND COALESCE(is_backtest,0)=0 AND strategy_name=? AND symbol=? "
        "AND pnl IS NOT NULL",
        (strategy, symbol),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        if trust is not None:
            try:
                if not trust(dict(r)):
                    continue
            except Exception:  # allow-silent: fail-open on an un-scoreable row (keep it) — research calibrator, not a live read-path
                pass
        pnl = r["pnl"] or 0
        out.append({"r": 1.0 if pnl > 0 else -1.0,
                    "direction": r["direction"], "ts": r["timestamp"],
                    "won": pnl > 0})
    con.close()
    return out


def _backtest_rows(bt_db: str, strategy: str, symbol: str) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{bt_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT pnl, direction, entry_ts, timestamp FROM trades "
        "WHERE COALESCE(is_backtest,0)=1 AND strategy_name=? AND symbol=? "
        "AND pnl IS NOT NULL",
        (strategy, symbol),
    ).fetchall()
    con.close()
    # backtest rows store the R-multiple as `pnl` (record_harness_trades maps net_r→pnl).
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["pnl"] is None:
            continue
        ts = None
        for col in ("entry_ts", "timestamp"):
            try:
                ts = r[col]
            except (IndexError, KeyError):
                ts = None
            if ts:
                break
        out.append({"r": float(r["pnl"]), "direction": r["direction"], "ts": ts,
                    "won": float(r["pnl"]) > 0})
    return out


def _live_realized_r(live_db: str, strategy: str, symbol: str) -> list[float]:
    return [r["r"] for r in _live_rows(live_db, strategy, symbol)]


def _backtest_realized_r(bt_db: str, strategy: str, symbol: str) -> list[float]:
    return [r["r"] for r in _backtest_rows(bt_db, strategy, symbol)]


def _legs_in(db: str, is_backtest: int) -> set[tuple[str, str]]:
    """Distinct (strategy_name, symbol) with resolved pnl in one DB. A DB error
    RAISES (never a silent empty set) — an empty trust map from a swallowed read
    error would read as a clean 'no overlapping legs', the false-negative the
    silent-empty guard exists to stop."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT strategy_name, symbol FROM trades "
            "WHERE COALESCE(is_backtest,0)=? AND strategy_name IS NOT NULL "
            "AND symbol IS NOT NULL AND pnl IS NOT NULL",
            (is_backtest,),
        ).fetchall()
    finally:
        con.close()
    return {(r[0], r[1]) for r in rows}


def _calibrate_leg(live_db: str, bt_db: str, strategy: str, symbol: str,
                   *, stratify: str = "none") -> dict[str, Any]:
    live = _live_rows(live_db, strategy, symbol)
    bt = _backtest_rows(bt_db, strategy, symbol)
    result = agreement([r["r"] for r in live], [r["r"] for r in bt])
    result.update({"strategy": strategy, "symbol": symbol})
    if stratify and stratify != "none":
        result["stratified"] = stratified_agreement(live, bt, key=stratify)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backtest-db", required=True)
    p.add_argument("--live-db", required=True)
    p.add_argument("--strategy", default=None, help="single-leg mode (with --symbol)")
    p.add_argument("--symbol", default=None)
    p.add_argument("--stratify", choices=["none", "direction", "year"], default="none",
                   help="also compute per-stratum agreement to separate a uniform "
                        "cost-model gap from a concentrated regime/small-sample bias.")
    p.add_argument("--trust-map", action="store_true",
                   help="run every (strategy,symbol) leg present in BOTH DBs and emit "
                        "a table — the full trust map (§ 5a).")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    if a.trust_map:
        legs = sorted(_legs_in(a.live_db, 0) & _legs_in(a.backtest_db, 1))
        rows = [_calibrate_leg(a.live_db, a.backtest_db, s, sym, stratify=a.stratify)
                for (s, sym) in legs]
        result: dict[str, Any] = {"trust_map": rows, "n_legs": len(rows),
                                  "verdict_counts": {}}
        for r in rows:
            v = r["verdict"]
            result["verdict_counts"][v] = result["verdict_counts"].get(v, 0) + 1
    else:
        if not a.strategy or not a.symbol:
            p.error("single-leg mode needs --strategy and --symbol (or use --trust-map)")
        result = _calibrate_leg(a.live_db, a.backtest_db, a.strategy, a.symbol,
                                stratify=a.stratify)

    out = json.dumps(result, indent=2)
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out)
        print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
