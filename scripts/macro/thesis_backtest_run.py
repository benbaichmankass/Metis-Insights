#!/usr/bin/env python3
"""M28 P4 — point-in-time value-thesis backtest runner (observe-only).

Ties the tested-but-unwired P4 primitives into a runnable scorecard:

    valuation snapshots (point-in-time JSONL, valuation_store)
        + a leakage-safe historical daily-close reader (per-symbol CSVs)
        → build_replay_entries  (as-of value reads → S1 former → forward price)
        → run_thesis_backtest    (net-of-cost + calibration + beat-baseline)
        → the P4 scorecard (text + optional JSON).

**Why a script, not a package module:** the ``macro_thesis`` package is a pure
Signals+Strategy island locked against Execution in ``.importlinter``. The price
source is therefore INJECTED here (a ``price_at`` closure over external CSVs) —
the runner lives outside the pure package so the package never grows a data/IO
dependency. Same dependency-injection seam ``build_replay_entries`` already
exposes via its ``price_at`` parameter.

**Point-in-time discipline (the M28 correctness invariant):** two guards, both
upstream-owned and re-stated here so a future edit can't silently break them:
  1. Value reads use ``as_of_snapshot_rows`` (``observed_at <= as_of`` strict
     past-only) — a revised macro value is a NEW snapshot line, never an
     overwrite, so the replay reconstructs exactly what was known as-of the
     rebalance instant (no revised-data lookahead).
  2. The price reader is **as-of-or-prior**: the close ON the date, else the
     last close STRICTLY BEFORE it — never a future bar. A date with no bar
     at/before it (pre-history) resolves ``None`` and drops that thesis (a
     missing price is never a fabricated fill).

Observe-only: reads logs + CSVs, writes a scorecard. No order path, no DB write.

Usage:
    python scripts/macro/thesis_backtest_run.py \
        --candles-dir data/macro_candles \
        --rebalance-every 30 --horizon-days 30 \
        --fee-frac 0.001 --carry-frac-per-day 0.0 \
        --json out/thesis_p4_scorecard.json

    # explicit rebalance dates + an explicit snapshots file (a frozen fixture):
    python scripts/macro/thesis_backtest_run.py \
        --snapshots runtime_logs/valuation_snapshots.jsonl \
        --candles-dir data/macro_candles \
        --rebalance 2026-01-01 --rebalance 2026-02-01 --horizon-days 45
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

# Repo root on path so ``python scripts/...`` works without install.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest  # noqa: E402
from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries  # noqa: E402
from src.units.strategies.macro_thesis.thesis_tick import load_sleeve_config  # noqa: E402
from src.units.strategies.macro_thesis.valuation_store import read_snapshot_records  # noqa: E402


# ---------------------------------------------------------------------------
# Leakage-safe historical daily-close reader (injected price source).
# ---------------------------------------------------------------------------
def _norm_date(s: str) -> str:
    """A date/timestamp string → ``YYYY-MM-DD`` (ISO dates sort == chronological)."""
    return str(s).strip()[:10]


def load_close_panels(candles_dir: str) -> dict[str, list[tuple[str, float]]]:
    """Load one daily-close series per symbol from ``<candles-dir>/<SYMBOL>.csv``.

    Each CSV is a daily OHLCV file with a ``date`` (or ``time``/``timestamp``)
    column and a ``close`` column (case-insensitive; extra columns ignored) —
    the same per-symbol-CSV convention as ``scripts/backtest_xsec_momentum.py``.
    Returns ``{SYMBOL: [(YYYY-MM-DD, close), ...]}`` sorted ascending by date.
    A row with an unparseable close is skipped (never a fabricated price).
    """
    panels: dict[str, list[tuple[str, float]]] = {}
    d = Path(candles_dir)
    for fp in sorted(d.glob("*.csv")):
        symbol = fp.stem.upper()
        rows: list[tuple[str, float]] = []
        try:
            with fp.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                lower = {c.lower(): c for c in (reader.fieldnames or [])}
                date_col = lower.get("date") or lower.get("time") or lower.get("timestamp")
                close_col = lower.get("close") or lower.get("adj close") or lower.get("close_usd")
                if date_col is None or close_col is None:
                    continue
                for r in reader:
                    try:
                        close = float(r[close_col])
                    except (TypeError, ValueError, KeyError):
                        continue
                    day = _norm_date(r.get(date_col, ""))
                    if len(day) == 10:
                        rows.append((day, close))
        except OSError:
            continue
        rows.sort(key=lambda x: x[0])
        if rows:
            panels[symbol] = rows
    return panels


def make_price_at(panels: dict[str, list[tuple[str, float]]]) -> Callable[[str, str], Optional[float]]:
    """Build the as-of-or-prior ``price_at(symbol, date_iso)`` closure.

    Returns the close ON ``date_iso`` if the symbol traded that day, else the
    last close STRICTLY BEFORE it (never a future bar). ``None`` when the symbol
    is unknown or the date precedes its history."""
    # Pre-split each panel into parallel (dates, closes) for bisect.
    idx: dict[str, tuple[list[str], list[float]]] = {
        sym: ([d for d, _ in rows], [c for _, c in rows]) for sym, rows in panels.items()
    }

    def price_at(symbol: str, date_iso: str) -> Optional[float]:
        entry = idx.get(str(symbol).upper())
        if entry is None:
            return None
        dates, closes = entry
        target = _norm_date(date_iso)
        # rightmost position where dates[pos-1] <= target → as-of-or-prior
        pos = bisect.bisect_right(dates, target)
        if pos == 0:
            return None  # target precedes all history — no leak-free price
        return closes[pos - 1]

    return price_at


# ---------------------------------------------------------------------------
# Rebalance-date generation.
# ---------------------------------------------------------------------------
def derive_rebalance_dates(records: list[dict], every_days: int) -> list[str]:
    """Evenly-spaced rebalance dates across the snapshot history span.

    First rebalance = the earliest ``observed_at`` date; then every
    ``every_days`` up to the latest. Empty when there is no dated history."""
    obs = [_norm_date(r.get("observed_at", "")) for r in records if r.get("observed_at")]
    obs = [o for o in obs if len(o) == 10]
    if not obs:
        return []
    start, end = min(obs), max(obs)
    out: list[str] = []
    cur = _dt.date.fromisoformat(start)
    end_d = _dt.date.fromisoformat(end)
    step = max(1, int(every_days))
    while cur <= end_d:
        out.append(cur.isoformat())
        cur = cur + _dt.timedelta(days=step)
    return out


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def render_scorecard(card: dict, *, meta: dict) -> str:
    lines = [
        "M28 P4 — value-thesis backtest scorecard (observe-only)",
        "=" * 56,
        f"rebalances={meta['rebalances']}  horizon_days={meta['horizon_days']}  "
        f"fee_frac={meta['fee_frac']}  carry/day={meta['carry_frac_per_day']}",
        f"entries scored (n) : {_fmt(card.get('n'))}",
        f"win_rate           : {_fmt(card.get('win_rate'))}",
        f"mean_net_return    : {_fmt(card.get('mean_net_return'))}",
        f"expectancy         : {_fmt(card.get('expectancy'))}",
        f"avg_win / avg_loss : {_fmt(card.get('avg_win'))} / {_fmt(card.get('avg_loss'))}",
        f"calibration_rank   : {_fmt(card.get('calibration_rank'))}  "
        "(Spearman conviction→net-return; the P4 'does conviction predict?' gate)",
        f"baseline_mean_net  : {_fmt(card.get('baseline_mean_net_return'))}  (naive all-long arm)",
        f"edge_vs_baseline   : {_fmt(card.get('edge_vs_baseline'))}  (must be > 0 net of cost)",
        "",
        "calibration bins (conviction quantile → realized net-return):",
    ]
    for i, b in enumerate(card.get("calibration_bins") or []):
        lines.append(
            f"  bin {i}: n={_fmt(b.get('n'))}  "
            f"conv=[{_fmt(b.get('lo'))},{_fmt(b.get('hi'))}]  "
            f"mean_net={_fmt(b.get('mean_net_return'))}  hit_rate={_fmt(b.get('hit_rate'))}"
        )
    if not card.get("n"):
        lines += [
            "",
            "NOTE: n=0 — no scored entries. Either no valuation-snapshot history has",
            "accrued yet (the FRED value soak feeds valuation_snapshots.jsonl), or no",
            "candle CSV covered the rebalance/exit dates. This is the honest empty state,",
            "not a pass. P4 becomes decision-grade once real snapshot history exists.",
        ]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M28 P4 value-thesis backtest runner (observe-only)")
    ap.add_argument("--snapshots", default=None,
                    help="valuation snapshots JSONL (default: runtime_logs/valuation_snapshots.jsonl)")
    ap.add_argument("--candles-dir", required=True,
                    help="directory of per-symbol daily-close CSVs (<SYMBOL>.csv)")
    ap.add_argument("--config", default=None, help="config/macro_theses.yaml override")
    ap.add_argument("--rebalance", action="append", default=None,
                    help="explicit rebalance date (repeatable); overrides --rebalance-every")
    ap.add_argument("--rebalance-every", type=int, default=30,
                    help="rebalance cadence in days across the snapshot span (default 30)")
    ap.add_argument("--horizon-days", type=float, default=30.0,
                    help="thesis hold horizon in days (default 30 — weeks-horizon)")
    ap.add_argument("--fee-frac", type=float, default=0.0,
                    help="round-trip fee as a fraction of notional (e.g. 0.001)")
    ap.add_argument("--carry-frac-per-day", type=float, default=0.0,
                    help="per-day carry/roll drag as a fraction of notional")
    ap.add_argument("--n-bins", type=int, default=4, help="calibration bins (default 4)")
    ap.add_argument("--json", default=None, help="write the scorecard JSON to this path")
    args = ap.parse_args(argv)

    records = read_snapshot_records(path=args.snapshots)
    cfg = load_sleeve_config(args.config)
    panels = load_close_panels(args.candles_dir)
    price_at = make_price_at(panels)

    rebalance_dates = (
        [_norm_date(d) for d in args.rebalance]
        if args.rebalance
        else derive_rebalance_dates(records, args.rebalance_every)
    )

    entries = build_replay_entries(
        records, price_at,
        rebalance_dates=rebalance_dates, cfg=cfg, horizon_days=args.horizon_days,
    )
    card = run_thesis_backtest(
        entries, fee_frac=args.fee_frac,
        carry_frac_per_day=args.carry_frac_per_day, n_bins=args.n_bins,
    )
    meta = {
        "rebalances": len(rebalance_dates),
        "horizon_days": args.horizon_days,
        "fee_frac": args.fee_frac,
        "carry_frac_per_day": args.carry_frac_per_day,
        "snapshot_records": len(records),
        "symbols_with_candles": sorted(panels.keys()),
        "entries_scored": card.get("n"),
    }
    print(render_scorecard(card, meta=meta))

    if args.json:
        out = {"scorecard": card, "meta": meta, "rebalance_dates": rebalance_dates}
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
