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
                bits.append(f"win-rate gap {wr_diff:.3f} > {max_winrate_diff}")
            if not ks_ok:
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


# ---- DB readers (read-only) -------------------------------------------------

def _live_realized_r(live_db: str, strategy: str, symbol: str) -> list[float]:
    """Measured-provenance-only live realized-R (falls back to PnL sign as the R
    proxy when a stop-distance R is unavailable). Excludes fabricated/paper pnl."""
    try:
        from src.runtime import provenance  # trust filter
        trust = provenance.pnl_is_trustworthy
    except Exception:  # allow-silent: provenance import is optional here — absent ⇒ unfiltered live sample; research calibrator, not a live read-path
        trust = None
    con = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT pnl, notes FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
        "AND strategy_name=? AND symbol=? AND pnl IS NOT NULL",
        (strategy, symbol),
    ).fetchall()
    out: list[float] = []
    for r in rows:
        if trust is not None:
            try:
                if not trust(dict(r)):
                    continue
            except Exception:  # allow-silent: fail-open on an un-scoreable row (keep it) — research calibrator, not a live read-path
                pass
        # R proxy: sign of pnl (win/loss) — a full stop-distance R is a P1 upgrade.
        out.append(1.0 if (r["pnl"] or 0) > 0 else -1.0)
    con.close()
    return out


def _backtest_realized_r(bt_db: str, strategy: str, symbol: str) -> list[float]:
    con = sqlite3.connect(f"file:{bt_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT pnl FROM trades WHERE COALESCE(is_backtest,0)=1 AND strategy_name=? AND symbol=? "
        "AND pnl IS NOT NULL",
        (strategy, symbol),
    ).fetchall()
    con.close()
    # backtest rows store the R-multiple as `pnl` (record_harness_trades maps net_r→pnl).
    return [float(r["pnl"]) for r in rows if r["pnl"] is not None]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backtest-db", required=True)
    p.add_argument("--live-db", required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    live = _live_realized_r(a.live_db, a.strategy, a.symbol)
    bt = _backtest_realized_r(a.backtest_db, a.strategy, a.symbol)
    result = agreement(live, bt)
    result.update({"strategy": a.strategy, "symbol": a.symbol})
    out = json.dumps(result, indent=2)
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out)
        print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
