#!/usr/bin/env python3
"""M36 Track C · C4 — the conditioned-lifecycle backtest runner (observe-only).

Scores the **conditioned macro/value thesis lifecycle** (C2 progress-exit +
optional C3 crowding conditioner, over the realized price path) against the
**value-only hold-to-horizon baseline** — net-of-cost, out-of-sample, on the
committed point-in-time history. This is the decisive C4 gate of
``docs/research/M36-macro-intelligence-and-crowding-DESIGN.md``:

    valuation snapshots (point-in-time JSONL) + leakage-safe daily-close panels
        → as-of thesis former (identical to the P4 baseline replay)
        → for each thesis: baseline exit = close at horizon;
          conditioned exit = thesis_conditioned.conditioned_exit_on_path
              (drives the shipped C2 thesis_progress + C3 crowding_read)
        → score both arms (net-of-cost + calibration + equity/maxDD)
        → a grid over expected_move_pct × {crowding on/off} — the FULL grid is
          reported (never a tuned cell) so the read is the shape, not a pick.

**The honest question C4 answers:** does moving the exit up when the move is
priced-in early (± the crowding tighten) beat holding to horizon on
**mean net return, calibration, AND max-drawdown**? Track C's reductive thesis
is that it should at least *reduce drawdown* without hurting return; the baseline
value sleeve already ran OOS-NULL (`M28-P4-value-gate-run-2026-07-27.md`), so a
positive net edge is not expected — the maxDD axis is where a reductive win, if
any, shows up.

**Point-in-time discipline (inherited, unchanged):** value reads use strict
past-only ``as_of_snapshot_rows``; prices are as-of-or-prior (never a future
bar). The conditioned exit only ever fires *earlier* than the baseline (it never
extends a hold), so it cannot manufacture look-ahead. Observe-only: reads
logs + CSVs, writes a scorecard. No order path, no DB write.

Usage:
    python scripts/macro/thesis_c4_run.py \
        --snapshots comms/macro/valuation_snapshots_backfill.jsonl \
        --candles-dir data/macro_candles \
        --rebalance-every 30 --horizon-days 30 --fee-frac 0.001 \
        --json comms/macro/thesis_c4_scorecard.json
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.macro.thesis_backtest_run import (  # noqa: E402
    _norm_date,
    derive_rebalance_dates,
    load_close_panels,
    make_price_at,
)
from src.units.strategies.macro_thesis.thesis_backtest import (  # noqa: E402
    equity_and_maxdd,
    score_backtest,
    thesis_outcome,
)
from src.units.strategies.macro_thesis.thesis_conditioned import conditioned_exit_on_path  # noqa: E402
from src.units.strategies.macro_thesis.thesis_replay import (  # noqa: E402
    add_days_iso,
    as_of_snapshot_rows,
)
from src.units.strategies.macro_thesis.thesis_tick import form_tick_theses, load_sleeve_config  # noqa: E402
from src.units.strategies.macro_thesis.valuation_store import read_snapshot_records  # noqa: E402

# The honest grid — the whole grid is reported; no cell is selected in-sample.
DEFAULT_MOVE_PCTS = [0.01, 0.02, 0.03, 0.05]


def _path_between(panel: list[tuple], as_of: str, exit_at: str) -> list[tuple]:
    """Ascending ``[(date, close)]`` slice with ``as_of < date <= exit_at`` — the
    realized daily path the conditioned exit walks (entry day excluded, horizon
    day included). ``panel`` is the symbol's full sorted ``[(date, close)]``."""
    dates = [d for d, _ in panel]
    lo = bisect.bisect_right(dates, _norm_date(as_of))      # first day strictly after as_of
    hi = bisect.bisect_right(dates, _norm_date(exit_at))    # last day <= exit_at
    return list(panel[lo:hi])


def build_thesis_records(records, panels, *, rebalance_dates, cfg, horizon_days):
    """Per-thesis replay records carrying the entry, the horizon exit, and the
    realized path — the shared substrate both arms score off (built once)."""
    price_at = make_price_at(panels)
    out = []
    for as_of in rebalance_dates or []:
        rows = as_of_snapshot_rows(records, as_of)
        id_prefix = str(as_of).replace("-", "").replace(":", "")[:12]
        theses = form_tick_theses(rows, cfg=cfg, now_iso=str(as_of), id_prefix=id_prefix)
        exit_at = add_days_iso(as_of, horizon_days)
        for t in theses:
            symbol = (t.instrument or {}).get("symbol")
            if not symbol:
                continue
            panel = panels.get(str(symbol).upper())
            if not panel:
                continue
            entry_price = price_at(symbol, str(as_of))
            baseline_exit = price_at(symbol, exit_at)
            path = _path_between(panel, str(as_of), exit_at)
            if entry_price is None or baseline_exit is None or not path:
                continue
            out.append({
                "thesis_id": t.thesis_id, "symbol": symbol,
                "conviction": t.thesis_conviction, "direction": t.direction,
                "entry_price": entry_price, "baseline_exit_price": baseline_exit,
                "as_of": str(as_of), "exit_at": exit_at, "path": path,
            })
    return out


def _score_arm(outcomes_ordered):
    """score_backtest + equity/maxDD for one arm (outcomes pre-sorted by exit)."""
    card = score_backtest(outcomes_ordered)
    card.update({"risk": equity_and_maxdd(outcomes_ordered)})
    return card


def run_c4(thesis_records, *, fee_frac, carry_frac_per_day, move_pcts, horizon_days, n_bins=4):
    """Score the baseline arm + every grid cell. Returns the full scorecard dict."""
    # `--n-bins` is advertised on the CLI and this scorer does not bin anything:
    # every arm is scored whole. Refuse a non-default rather than accept a knob
    # that silently does nothing.
    if n_bins != 4:
        raise NotImplementedError(
            f"run_c4 does not bin; --n-bins is inert (got {n_bins}). "
            f"Remove the flag or implement binning before passing it."
        )
    def _mk_outcomes(exit_of, hold_of):
        """Build ordered scored outcomes; ``exit_of``/``hold_of`` map a record to
        its (exit_price, hold_days) for this arm. Ordered by exit date for maxDD."""
        rows = []
        for r in thesis_records:
            xp = exit_of(r)
            hd = hold_of(r)
            if xp is None:
                continue
            carry = abs(carry_frac_per_day) * float(hd or 0.0)
            o = thesis_outcome(r["conviction"], r["direction"], r["entry_price"], xp,
                               fee_frac=fee_frac, carry_frac=carry, thesis_id=r["thesis_id"])
            if o is not None:
                # exit calendar date for ordering (as_of + hold_days, approx via exit_at)
                o["_exit_key"] = (r["as_of"], r["thesis_id"])
                rows.append(o)
        rows.sort(key=lambda x: x["_exit_key"])
        return rows

    baseline = _mk_outcomes(lambda r: r["baseline_exit_price"], lambda r: horizon_days)
    baseline_card = _score_arm(baseline)
    base_mean = baseline_card.get("mean_net_return")
    base_dd = (baseline_card.get("risk") or {}).get("max_drawdown")

    cells = []
    for pct in move_pcts:
        for use_crowding in (False, True):
            def _cond(r, _pct=pct, _uc=use_crowding):
                res = conditioned_exit_on_path(
                    thesis_id=r["thesis_id"], direction=r["direction"],
                    entry_price=r["entry_price"], as_of=r["as_of"], path=r["path"],
                    horizon_days=horizon_days, expected_move_pct=_pct, use_crowding=_uc,
                )
                return res
            # precompute conditioned exits once per cell
            cond_by_id = {r["thesis_id"]: _cond(r) for r in thesis_records}
            outs = _mk_outcomes(
                lambda r: (cond_by_id[r["thesis_id"]] or {}).get("exit_price"),
                lambda r: (cond_by_id[r["thesis_id"]] or {}).get("hold_days", horizon_days),
            )
            card = _score_arm(outs)
            mean_net = card.get("mean_net_return")
            dd = (card.get("risk") or {}).get("max_drawdown")
            # mean hold across the arm (how much earlier the conditioned arm exits)
            holds = [(cond_by_id[r["thesis_id"]] or {}).get("hold_days")
                     for r in thesis_records if cond_by_id.get(r["thesis_id"])]
            holds = [h for h in holds if isinstance(h, (int, float))]
            mean_hold = (sum(holds) / len(holds)) if holds else None
            cells.append({
                "expected_move_pct": pct, "crowding": use_crowding,
                "n": card.get("n"), "win_rate": card.get("win_rate"),
                "mean_net_return": mean_net, "calibration_rank": card.get("calibration_rank"),
                "max_drawdown": dd, "mean_hold_days": mean_hold,
                "edge_vs_value_baseline": (mean_net - base_mean)
                if (mean_net is not None and base_mean is not None) else None,
                "maxdd_delta_vs_baseline": (dd - base_dd)
                if (dd is not None and base_dd is not None) else None,
            })
    return {"baseline_value_holdtohorizon": baseline_card, "grid": cells,
            "baseline_mean_net_return": base_mean, "baseline_max_drawdown": base_dd}


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def render(result, meta) -> str:
    b = result["baseline_value_holdtohorizon"]
    br = b.get("risk") or {}
    lines = [
        "M36 Track C · C4 — conditioned-lifecycle backtest (observe-only)",
        "=" * 62,
        f"rebalances={meta['rebalances']}  horizon_days={meta['horizon_days']}  "
        f"fee_frac={meta['fee_frac']}  theses={meta['thesis_records']}",
        "",
        "BASELINE  value thesis, hold to horizon:",
        f"  n={_fmt(b.get('n'))}  win_rate={_fmt(b.get('win_rate'))}  "
        f"mean_net={_fmt(b.get('mean_net_return'))}  "
        f"calib_rank={_fmt(b.get('calibration_rank'))}  maxDD={_fmt(br.get('max_drawdown'))}",
        "",
        "CONDITIONED grid (exit moved up on progress ± crowding):",
        "  move%  crowd |    n  win_rate  mean_net  calib_rank   maxDD  hold_d | Δnet   ΔmaxDD",
    ]
    for c in result["grid"]:
        lines.append(
            f"  {c['expected_move_pct']:.2f}  {str(c['crowding'])[0]:>5} | "
            f"{_fmt(c['n']):>4}  {_fmt(c['win_rate']):>7}  {_fmt(c['mean_net_return']):>8}  "
            f"{_fmt(c['calibration_rank']):>9}  {_fmt(c['max_drawdown']):>6}  {_fmt(c['mean_hold_days']):>5} | "
            f"{_fmt(c['edge_vs_value_baseline']):>6} {_fmt(c['maxdd_delta_vs_baseline']):>6}"
        )
    lines += [
        "",
        "READ: Δnet > 0 = the conditioned exit beats hold-to-horizon on net return;",
        "ΔmaxDD < 0 = it reduces drawdown (Track C's reductive win). The whole grid is",
        "shown — no cell is selected in-sample. A grid with no Δnet>0 AND no ΔmaxDD<0",
        "is a clean NULL for the conditioned lifecycle on this substrate.",
    ]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M36 Track C · C4 conditioned-lifecycle backtest")
    ap.add_argument("--snapshots", default=None, help="valuation snapshots JSONL")
    ap.add_argument("--candles-dir", required=True, help="per-symbol daily-close CSV dir")
    ap.add_argument("--config", default=None, help="config/macro_theses.yaml override")
    ap.add_argument("--rebalance-every", type=int, default=30)
    ap.add_argument("--horizon-days", type=float, default=30.0)
    ap.add_argument("--fee-frac", type=float, default=0.001)
    ap.add_argument("--carry-frac-per-day", type=float, default=0.0)
    ap.add_argument("--move-pcts", default=None,
                    help="comma-separated expected_move_pct grid (default 0.01,0.02,0.03,0.05)")
    ap.add_argument("--n-bins", type=int, default=4)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    records = read_snapshot_records(path=args.snapshots)
    cfg = load_sleeve_config(args.config)
    panels = load_close_panels(args.candles_dir)
    rebalance_dates = derive_rebalance_dates(records, args.rebalance_every)
    move_pcts = ([float(x) for x in args.move_pcts.split(",")] if args.move_pcts
                 else list(DEFAULT_MOVE_PCTS))

    thesis_records = build_thesis_records(
        records, panels, rebalance_dates=rebalance_dates, cfg=cfg, horizon_days=args.horizon_days)
    result = run_c4(
        thesis_records, fee_frac=args.fee_frac, carry_frac_per_day=args.carry_frac_per_day,
        move_pcts=move_pcts, horizon_days=args.horizon_days, n_bins=args.n_bins)
    meta = {
        "rebalances": len(rebalance_dates), "horizon_days": args.horizon_days,
        "fee_frac": args.fee_frac, "carry_frac_per_day": args.carry_frac_per_day,
        "snapshot_records": len(records), "thesis_records": len(thesis_records),
        "symbols_with_candles": sorted(panels.keys()), "move_pcts": move_pcts,
    }
    print(render(result, meta))
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"result": result, "meta": meta}, indent=2, default=str),
                     encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
