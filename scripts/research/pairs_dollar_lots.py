"""Pairs sleeve **$-and-lots** backtest (M22 real-money readiness, gap G2).

``scripts/backtest_pairs.py`` proves the *edge* in R-space (spread P&L / risk),
net-of-fee — but R-space is **balance-blind and lot-blind**. It answers "does the
spread revert?", NOT "does the sleeve make money on a **small real balance** once
each leg is floored to the exchange's minimum contract?". Those are different
questions, and the difference is exactly what bit the live paper go-live: on the
~$166k *inflated* demo balance most legs cleared the venue minimum, but on a real
account (bybit_2 is low-hundreds-of-$) `risk_pct × balance` is tiny and:

  1. **most pairs can't place at all** — a leg floors below the exchange minimum
     lot, so the pre-placement both-legs-or-nothing gate (#6591) *skips* the
     trade (`skip_size`). The **skip fraction** at a given balance is the primary
     go/no-go number.
  2. the pairs that *do* place suffer **min-qty rounding drag** the R-space model
     never saw: flooring `qty_a` and `qty_b` *independently* perturbs the β-hedge,
     leaving a small directional residual — real money the R-space P&L ignores.

This driver closes G2. It **reuses the validated engine** — it calls
``backtest_pairs.run_backtest(..., collect_rows=…)`` to get the exact entry/exit
decisions (never re-implementing the spread/z logic), then for each trade:

  * sizes the two legs off the **canonical account basis**
    ``balance × risk_pct × pairs_risk_fraction`` via the **live**
    ``pairs_sizing.pair_notionals`` (the same function the executor calls),
  * **floors each leg to its venue lot** (``qty_legalize.instrument_lot`` — the
    authoritative ``config/instruments.yaml`` source; a leg below its min ⇒ the
    pair is *skipped*, mirroring the live pre-placement gate),
  * computes the **true two-leg dollar P&L** from the *actually-floored* (hedge-
    imperfect) quantities and real per-leg entry/exit prices, minus real fees.

It then **sweeps a range of balances** so the output is the balance→viability
curve: at each balance, the `skip_size` fraction, net $, $-expectancy, and win%.
That is the "will it make money on $X real?" answer, per pair.

Research only (Tier-1). It writes JSON/markdown, never touches the live path; any
route-to-real-account decision is a separate Tier-3 ``config/pairs.yaml`` /
``config/accounts.yaml`` proposal.

Run (on the trainer, where the candle CSVs live)::

    python scripts/research/pairs_dollar_lots.py \
        --data-a runtime_state/candles/ETHUSDT_1h.csv \
        --data-b runtime_state/candles/BTCUSDT_1h.csv \
        --symbol-a ETHUSDT --symbol-b BTCUSDT --resample 1h \
        --risk-pct 0.015 --balances 200,500,1000,5000,166000 \
        --json /tmp/pairs_dollars.json --md /tmp/pairs_dollars.md

Self-test (synthetic OU pair; no data files)::

    python scripts/research/pairs_dollar_lots.py --self-test
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_REPO_ROOT, _SCRIPTS, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_pairs as bp                                   # noqa: E402
from src.units.strategies import pairs_sizing as psz          # noqa: E402

# Static fallback lot table (Bybit linear-perp minimums) for a symbol with no
# config/instruments.yaml profile — keeps the sim usable off-repo. The live
# resolver (qty_legalize.instrument_lot) is preferred and authoritative.
_STATIC_LOTS: Dict[str, Tuple[float, float]] = {
    "BTCUSDT": (0.001, 0.001), "ETHUSDT": (0.01, 0.01), "SOLUSDT": (0.1, 0.1),
    "BNBUSDT": (0.01, 0.01), "XRPUSDT": (1.0, 1.0), "ADAUSDT": (1.0, 1.0),
}


def resolve_lot(symbol: str, *, exchange: str = "bybit") -> Optional[Tuple[float, float]]:
    """(qty_step, min_qty) for *symbol* — the live instruments.yaml resolver first,
    the static Bybit map as a fallback, ``None`` if neither knows it."""
    try:
        from src.units.accounts.qty_legalize import instrument_lot
        lot = instrument_lot(symbol, exchange=exchange)
        if lot is not None:
            return (float(lot[0]), float(lot[1]))
    except Exception:  # noqa: BLE001 — offline fallback below
        pass
    return _STATIC_LOTS.get(symbol.upper())


def _floor_to_lot(qty: float, step: float, min_qty: float) -> Tuple[float, bool]:
    """Floor *qty* DOWN to *step* and report whether it clears *min_qty*.

    Mirrors ``qty_legalize.legalize_qty`` exactly (floor, never round up — realised
    risk must not exceed the sized cap; refuse when the floored value is below the
    venue minimum). Returns ``(floored_qty, ok)``; ``ok=False`` ⇒ the live
    pre-placement gate would skip this leg (``below_venue_min_qty``)."""
    if step <= 0:
        return (float(qty), qty >= min_qty)
    floored = math.floor(float(qty) / step + 1e-12) * step
    # kill FP dust so 0.30000000000000004 -> 0.3
    floored = round(floored, 12)
    return (floored, floored > 0 and floored >= min_qty - 1e-12)


def _leg_signs(direction: str) -> Tuple[float, float]:
    """(sign_a, sign_b): long_spread = long A / short B; short_spread = the mirror."""
    return (1.0, -1.0) if direction == "long_spread" else (-1.0, 1.0)


def simulate_dollar_lots(
    rows: List[Dict[str, Any]],
    *,
    balance: float,
    risk_pct: float,
    pairs_risk_fraction: float,
    lot_a: Optional[Tuple[float, float]],
    lot_b: Optional[Tuple[float, float]],
    fee_bps_roundtrip: float = bp.FEE_BPS_ROUNDTRIP,
) -> Dict[str, Any]:
    """Translate R-space trade rows into **dollar** outcomes at one balance.

    ``rows`` are the ``collect_rows`` dicts from ``run_backtest``. Each trade is
    sized off ``balance × risk_pct × pairs_risk_fraction`` (the canonical account
    basis) via the live ``pairs_sizing.pair_notionals``, each leg floored to its
    venue lot; a leg below its min skips the whole pair (both-legs-or-nothing).
    Placed trades get the true two-leg $ P&L on the floored quantities minus fees.

    A ``None`` lot means "rule unknown" ⇒ that leg is never floored (passthrough,
    like the live legalizer) — used by the synthetic self-test.
    """
    budget = float(balance) * float(risk_pct) * float(pairs_risk_fraction)
    step_a, min_a = (lot_a if lot_a is not None else (0.0, 0.0))
    step_b, min_b = (lot_b if lot_b is not None else (0.0, 0.0))
    fee_frac = float(fee_bps_roundtrip) / 10_000.0

    placed: List[float] = []          # net $ per placed trade
    gross_total = 0.0
    fees_total = 0.0
    n_skipped = 0
    skip_reasons: Dict[str, int] = {}
    hedge_residuals: List[float] = []

    for r in rows:
        entry_a = float(r["entry_price_a"])
        entry_b = float(r["entry_price_b"])
        exit_a = float(r["exit_price_a"])
        exit_b = float(r["exit_price_b"])
        beta = float(r["beta"])
        risk_spread = float(r["risk_spread"])
        direction = str(r["direction"])

        notionals = psz.pair_notionals(budget, risk_spread, beta, entry_a, entry_b)
        qty_a_ideal = notionals["qty_a"]
        qty_b_ideal = notionals["qty_b"]
        if not (qty_a_ideal > 0 and qty_b_ideal > 0):
            n_skipped += 1
            skip_reasons["degenerate_size"] = skip_reasons.get("degenerate_size", 0) + 1
            continue

        qty_a, ok_a = _floor_to_lot(qty_a_ideal, step_a, min_a)
        qty_b, ok_b = _floor_to_lot(qty_b_ideal, step_b, min_b)
        if not (ok_a and ok_b):
            n_skipped += 1
            which = "leg_a" if not ok_a else ""
            which = which + ("+leg_b" if not ok_b else "") if which else "leg_b"
            skip_reasons[f"below_min_{which}"] = skip_reasons.get(f"below_min_{which}", 0) + 1
            continue

        sign_a, sign_b = _leg_signs(direction)
        pnl = qty_a * (exit_a - entry_a) * sign_a + qty_b * (exit_b - entry_b) * sign_b
        # fee: per-leg round-trip taker on entry notional (matches the R-space
        # FEE_BPS_ROUNDTRIP=7.5 "per leg, round-trip" semantics).
        fee = fee_frac * (qty_a * entry_a + qty_b * entry_b)
        net = pnl - fee
        placed.append(net)
        gross_total += pnl
        fees_total += fee
        # hedge residual: how far the floored short-leg notional is from the
        # β-hedge target |β|·N_A (0 = perfectly hedged; larger = more rounding drag).
        n_a_usd = qty_a * entry_a
        n_b_target = abs(beta) * n_a_usd
        n_b_actual = qty_b * entry_b
        if n_a_usd > 0:
            hedge_residuals.append(abs(n_b_actual - n_b_target) / n_a_usd)

    n_signaled = len(rows)
    n_placed = len(placed)
    net_total = float(sum(placed))
    wins = [p for p in placed if p > 0]
    return {
        "balance_usd": round(float(balance), 2),
        "budget_usd": round(budget, 4),
        "risk_pct": float(risk_pct),
        "pairs_risk_fraction": float(pairs_risk_fraction),
        "fee_bps_roundtrip_per_leg": float(fee_bps_roundtrip),
        "n_signaled": n_signaled,
        "n_placed": n_placed,
        "n_skipped": n_skipped,
        "skip_pct": round(100.0 * n_skipped / n_signaled, 2) if n_signaled else 0.0,
        "skip_reasons": skip_reasons,
        # net over PLACED trades (the sleeve's realised $ at this balance — a skip
        # is simply "no trade", contributing $0).
        "net_usd": round(net_total, 4),
        "gross_usd": round(gross_total, 4),
        "fees_usd": round(fees_total, 4),
        "expectancy_usd": round(net_total / n_placed, 4) if n_placed else None,
        "win_pct": round(100.0 * len(wins) / n_placed, 2) if n_placed else None,
        "mean_hedge_residual_pct": round(100.0 * float(np.mean(hedge_residuals)), 3)
        if hedge_residuals else None,
    }


def run_pair(m: pd.DataFrame, args, *, symbol_a: str, symbol_b: str) -> Dict[str, Any]:
    """Run the engine ONCE (collecting leg rows) then simulate every balance."""
    rows: List[Dict[str, Any]] = []
    summary = bp.run_backtest(
        m, lookback=args.lookback, entry_z=args.entry_z, exit_z=args.exit_z,
        stop_z=args.stop_z, max_hold_bars=args.max_hold_bars,
        cooldown_bars=args.cooldown_bars, hedge_beta=args.hedge_beta,
        timeframe=args.resample, pair=f"{symbol_a}/{symbol_b}", collect_rows=rows)
    # faithfulness cross-check: the collected rows must match the summarized count.
    n_summary = int((summary.get("trades_long") or 0) + (summary.get("trades_short") or 0))
    lot_a = resolve_lot(symbol_a)
    lot_b = resolve_lot(symbol_b)
    sweep = [
        simulate_dollar_lots(
            rows, balance=bal, risk_pct=args.risk_pct,
            pairs_risk_fraction=args.pairs_risk_fraction,
            lot_a=lot_a, lot_b=lot_b, fee_bps_roundtrip=args.fee_bps_roundtrip)
        for bal in args.balances
    ]
    out = {
        "pair": f"{symbol_a}/{symbol_b}",
        "symbol_a": symbol_a, "symbol_b": symbol_b,
        "lot_a": lot_a, "lot_b": lot_b,
        "n_trades": len(rows),
        "rows_match_summary": len(rows) == n_summary,
        "r_space": {k: summary.get(k) for k in
                    ("net_total_r", "net_expectancy_r", "win_rate_pct",
                     "net_r_per_pos_day", "data_start", "data_end")},
        "balance_sweep": sweep,
    }
    if getattr(args, "ideal_no_floor", False):
        # DIAGNOSTIC: lots→passthrough (never floor, never skip) at a high balance.
        # Isolates the PURE fixed-entry-β hold $ economics (real per-leg prices +
        # fees, hedge intact) from the min-qty lot effect — so a net-negative here
        # means the R-space edge doesn't survive real fixed-β execution EVEN with
        # no lot constraint (a strategy/backtest gap, not a sizing gap).
        ideal_bal = max(args.balances) if args.balances else 100000.0
        out["ideal_no_floor"] = simulate_dollar_lots(
            rows, balance=ideal_bal, risk_pct=args.risk_pct,
            pairs_risk_fraction=args.pairs_risk_fraction,
            lot_a=None, lot_b=None, fee_bps_roundtrip=args.fee_bps_roundtrip)
        out["ideal_no_floor_feefree"] = simulate_dollar_lots(
            rows, balance=ideal_bal, risk_pct=args.risk_pct,
            pairs_risk_fraction=args.pairs_risk_fraction,
            lot_a=None, lot_b=None, fee_bps_roundtrip=0.0)
    return out


def _to_md(res: Dict[str, Any]) -> str:
    lines = [f"# Pairs $-and-lots — {res['pair']}", "",
             f"- lots: A {res['lot_a']} · B {res['lot_b']}  ·  trades: {res['n_trades']}",
             f"- R-space (balance-blind): net_R={res['r_space'].get('net_total_r')} "
             f"exp_R={res['r_space'].get('net_expectancy_r')} "
             f"win={res['r_space'].get('win_rate_pct')}%", "",
             "| balance $ | budget $ | skip % | placed | net $ | exp $ | win % | hedge resid % |",
             "|---|---|---|---|---|---|---|---|"]
    for s in res["balance_sweep"]:
        lines.append(
            "| {bal} | {bud} | {skip} | {pl}/{sig} | {net} | {exp} | {win} | {hr} |".format(
                bal=s["balance_usd"], bud=s["budget_usd"], skip=s["skip_pct"],
                pl=s["n_placed"], sig=s["n_signaled"], net=s["net_usd"],
                exp=s["expectancy_usd"], win=s["win_pct"], hr=s["mean_hedge_residual_pct"]))
    ideal = res.get("ideal_no_floor")
    if ideal is not None:
        ff = res.get("ideal_no_floor_feefree", {})
        lines += ["",
                  "**Ideal (no lot floor — pure fixed-β-hold economics):** "
                  f"net ${ideal.get('net_usd')} (exp ${ideal.get('expectancy_usd')}, "
                  f"win {ideal.get('win_pct')}%, placed {ideal.get('n_placed')}); "
                  f"**fee-free** net ${ff.get('net_usd')} — "
                  "net-negative here ⇒ the R-space edge does NOT survive real "
                  "fixed-β execution independent of lots."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Self-test: a synthetic OU pair. At a HUGE balance with fine lots nothing
# skips and the sleeve's $ direction matches its (positive) R edge; at a TINY
# balance with coarse lots everything skips (skip_pct == 100). No data files.
# --------------------------------------------------------------------------

def _self_test() -> int:
    rng = np.random.default_rng(7)
    n = 6000
    lb = np.cumsum(rng.normal(0, 0.01, n)) + np.log(100.0)
    s = np.zeros(n)
    for k in range(1, n):
        s[k] = 0.92 * s[k - 1] + rng.normal(0, 0.02)   # OU, strong reversion
    la = lb + s
    ts = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    m = pd.DataFrame({"timestamp": ts, "close_a": np.exp(la), "close_b": np.exp(lb)})

    rows: List[Dict[str, Any]] = []
    summary = bp.run_backtest(m, lookback=24, entry_z=2.0, exit_z=0.3, stop_z=3.0,
                              max_hold_bars=48, cooldown_bars=0, hedge_beta="one",
                              timeframe="1h", pair="SYN_A/SYN_B", collect_rows=rows)
    n_summary = int((summary.get("trades_long") or 0) + (summary.get("trades_short") or 0))
    assert len(rows) == n_summary, ("row/summary parity", len(rows), n_summary)
    assert summary["net_total_r"] > 0, "OU edge must be positive in R-space"

    fine = (1e-9, 1e-9)   # essentially unbounded lots -> nothing floors/skips
    big = simulate_dollar_lots(rows, balance=1e7, risk_pct=0.015,
                               pairs_risk_fraction=1.0, lot_a=fine, lot_b=fine,
                               fee_bps_roundtrip=0.0)
    print("BIG balance, fine lots:", {k: big[k] for k in
          ("n_placed", "n_skipped", "skip_pct", "net_usd", "win_pct")})
    assert big["n_skipped"] == 0, "fine lots should never skip"
    assert big["net_usd"] > 0, "positive R edge -> positive $ (fee-free, hedged)"

    coarse = (1e9, 1e9)   # min lot larger than any sized qty -> always skip
    tiny = simulate_dollar_lots(rows, balance=100.0, risk_pct=0.015,
                                pairs_risk_fraction=1.0, lot_a=coarse, lot_b=coarse)
    print("TINY balance, coarse lots:", {k: tiny[k] for k in
          ("n_placed", "n_skipped", "skip_pct")})
    assert tiny["skip_pct"] == 100.0 and tiny["n_placed"] == 0, "coarse lots must skip all"

    # a REAL Bybit BTC-leg squeeze: BTC min 0.001 at ~$100k = ~$100 notional. A
    # small β on the B(=BTC) leg makes it sub-min even at a healthy budget.
    real = simulate_dollar_lots(rows, balance=500.0, risk_pct=0.015,
                                pairs_risk_fraction=1.0,
                                lot_a=(0.01, 0.01), lot_b=(0.001, 0.001))
    print("REAL-ish lots @ $500:", {k: real[k] for k in
          ("n_placed", "n_skipped", "skip_pct", "net_usd")})
    assert real["n_signaled"] == len(rows)

    print("SELF-TEST PASS: engine-reuse parity + $ sizing + lot flooring + skip logic verified.")
    return 0


def _parse(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pairs $-and-lots backtest (real balance + venue lots + fees).")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--data-a", help="leg A candle CSV/parquet")
    p.add_argument("--data-b", help="leg B candle CSV/parquet")
    p.add_argument("--symbol-a", default="A")
    p.add_argument("--symbol-b", default="B")
    p.add_argument("--resample", default="1h", help="resample BOTH legs to this rule first")
    p.add_argument("--lookback", type=int, default=15)
    p.add_argument("--entry-z", type=float, default=2.0)
    p.add_argument("--exit-z", type=float, default=0.5)
    p.add_argument("--stop-z", type=float, default=2.0)
    p.add_argument("--max-hold-bars", type=int, default=20)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--hedge-beta", choices=["one", "rolling"], default="rolling")
    p.add_argument("--risk-pct", type=float, default=0.015, help="account risk_pct (bybit_2 = 0.015)")
    p.add_argument("--pairs-risk-fraction", type=float, default=1.0)
    p.add_argument("--fee-bps-roundtrip", type=float, default=bp.FEE_BPS_ROUNDTRIP,
                   help="per-leg round-trip taker cost in bps (two legs charged)")
    p.add_argument("--balances", default="200,500,1000,5000,166000",
                   help="CSV of balances $ to sweep")
    p.add_argument("--ideal-no-floor", action="store_true",
                   help="also report the no-lot-floor 'ideal' fixed-β-hold $ edge "
                        "(isolates strategy economics from the min-qty lot effect)")
    p.add_argument("--start", default=None, help="ISO date; drop aligned bars before it")
    p.add_argument("--end", default=None, help="ISO date; drop aligned bars on/after it")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--md", dest="md_out", default=None)
    args = p.parse_args(argv)
    args.balances = [float(b.strip()) for b in str(args.balances).split(",") if b.strip()]
    return args


def main(argv: List[str]) -> int:
    args = _parse(argv)
    if args.self_test:
        return _self_test()
    if not args.data_a or not args.data_b:
        print("error: --data-a and --data-b required (or --self-test)", file=sys.stderr)
        return 2
    try:
        a = bp._resample(bp._load_candles(args.data_a), args.resample)
        b = bp._resample(bp._load_candles(args.data_b), args.resample)
        m = bp._align(a, b)
        if args.start:
            m = m[m["timestamp"] >= pd.to_datetime(args.start, utc=True)].reset_index(drop=True)
        if args.end:
            m = m[m["timestamp"] < pd.to_datetime(args.end, utc=True)].reset_index(drop=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    if len(m) <= args.lookback + 2:
        print(f"ERROR: only {len(m)} aligned bars (need > lookback={args.lookback})", file=sys.stderr)
        return 1

    res = run_pair(m, args, symbol_a=args.symbol_a.upper(), symbol_b=args.symbol_b.upper())
    md = _to_md(res)
    print(md)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, default=str)
        print(f"JSON -> {args.json_out}", file=sys.stderr)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"MD -> {args.md_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
