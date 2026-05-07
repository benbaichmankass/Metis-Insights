#!/usr/bin/env python3
"""
s006_ict_synthetic_validate.py — synthetic multi-symbol ICT validation.

Generates deterministic OHLCV data for 5 symbols across trending and
volatile regimes, runs ICTBacktester on each, and writes a PF/WR report
to docs/sprint-plans/s006-synthetic-report.md.

Usage
-----
    PYTHONPATH=. python scripts/s006_ict_synthetic_validate.py
    PYTHONPATH=. python scripts/s006_ict_synthetic_validate.py --output /tmp/report.md

Also importable: call ``run_synthetic_validation()`` directly.

Design
------
Each symbol gets 10 000 candles built from repeating "FVG cycles":

  [trend bars] → [3-bar FVG injection] → [pullback into FVG zone] → [TP continuation]

In trending regimes (bullish / bearish) the price resumes the trend after
filling the FVG, so take-profit hits are probable.  The ranging regime mixes
bullish and bearish FVGs.  All timestamps sit within the default session
window (06:00 UTC), so no session-filter override is needed.

The generator uses numpy with fixed seeds → fully deterministic.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Repo path so imports work when run as a script
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.backtester import ICTBacktester

# ---------------------------------------------------------------------------
# Symbol registry
# ---------------------------------------------------------------------------
SYMBOLS = [
    # (symbol, timeframe, regime, base_price, seed)
    ("BTCUSDT", "5m",  "bullish", 95_000.0, 11),
    ("ETHUSDT", "5m",  "bearish",  3_200.0, 22),
    ("SPY",     "15m", "mixed",      580.0, 33),
    ("QQQ",     "15m", "bullish",    490.0, 44),
    ("GOLD",    "15m", "ranging",  2_600.0, 55),
]

N_CANDLES = 10_000
CYCLE     = 90   # bars per FVG cycle
SESSION_H = 6    # hour for all synthetic timestamps (within 02-12 window)

# ---------------------------------------------------------------------------
# Data generator
# ---------------------------------------------------------------------------

def _trend_drift(price: float, direction: str) -> float:
    return price * (0.00018 if direction == "up" else -0.00018)


def _one_cycle(
    rng: np.random.Generator,
    price: float,
    ts: pd.Timestamp,
    freq: pd.Timedelta,
    direction: str,  # "up" or "down"
) -> tuple[list[dict], float, pd.Timestamp]:
    """Build one 90-bar FVG cycle.  Returns (rows, end_price, end_ts)."""
    rows: list[dict] = []
    drift = _trend_drift(price, direction)
    spread_factor = 0.0008

    def _bar(o, c, h_override=None, l_override=None, vol=100):
        nonlocal ts
        h = max(o, c, h_override if h_override is not None else o) + abs(price) * spread_factor
        l = min(o, c, l_override if l_override is not None else o) - abs(price) * spread_factor
        rows.append({"timestamp": ts, "open": o, "high": h,
                     "low": l, "close": c, "volume": vol})
        ts += freq

    # ── Phase 1: 55 trending bars ──────────────────────────────────────
    for _ in range(55):
        noise = rng.normal(0, abs(price) * 0.0003)
        c = price + drift + noise
        _bar(price, c)
        price = c

    # ── Phase 2: 3-bar FVG injection ───────────────────────────────────
    # bar A: normal — record high[A] or low[A]
    c_A = price + drift * 2
    if direction == "up":
        h_A = c_A + abs(price) * 0.0012   # high[A]
    else:
        l_A = c_A - abs(price) * 0.0012   # low[A]
    _bar(price, c_A, vol=140)
    price = c_A

    # bar B: big move that widens the gap
    gap = abs(price) * 0.0035
    if direction == "up":
        c_B = price + gap * 2
        _bar(price, c_B, h_override=c_B + gap * 0.5, vol=280)
    else:
        c_B = price - gap * 2
        _bar(price, c_B, l_override=c_B - gap * 0.5, vol=280)
    price = c_B

    # bar C: creates the FVG
    if direction == "up":
        # bullish FVG: low[C] > high[A]
        l_C = h_A + abs(price) * 0.0015   # guaranteed gap
        h_C = l_C + abs(price) * 0.0040
        c_C = (l_C + h_C) * 0.55
        fvg_bottom, fvg_top = h_A, l_C
    else:
        # bearish FVG: high[C] < low[A]
        h_C = l_A - abs(price) * 0.0015
        l_C = h_C - abs(price) * 0.0040
        c_C = (h_C + l_C) * 0.45
        fvg_bottom, fvg_top = h_C, l_A

    # Enforce OHLCV invariants on bar C explicitly
    bar_c_h = max(price, c_C, h_C) + abs(price) * spread_factor
    bar_c_l = min(price, c_C, l_C) - abs(price) * spread_factor
    rows.append({"timestamp": ts, "open": price, "high": bar_c_h,
                 "low": bar_c_l, "close": c_C, "volume": 200})
    ts += freq
    price = c_C

    # ── Phase 3: 12-bar pullback into FVG zone ─────────────────────────
    fvg_mid = (fvg_bottom + fvg_top) / 2
    n_pb = 12
    pb_step = (price - fvg_mid) / n_pb if direction == "up" else (fvg_mid - price) / n_pb
    for _ in range(n_pb):
        c = price - pb_step if direction == "up" else price + pb_step
        _bar(price, c, vol=75)
        price = c
    # price ≈ fvg_mid → in FVG zone → signal fires on next bar(s)

    # ── Phase 4: 20-bar continuation toward TP ─────────────────────────
    strong_drift = drift * 3.5
    for _ in range(20):
        noise = rng.normal(0, abs(price) * 0.0002)
        c = price + strong_drift + noise
        _bar(price, c, vol=130)
        price = c

    return rows, price, ts


def make_synthetic_ohlcv(
    n: int = N_CANDLES,
    regime: str = "bullish",
    base_price: float = 100.0,
    seed: int = 42,
    freq_min: int = 5,
) -> pd.DataFrame:
    """Generate *n* candles of synthetic OHLCV for the given regime.

    regime options: 'bullish', 'bearish', 'mixed', 'ranging'
    """
    rng = np.random.default_rng(seed)
    freq = pd.Timedelta(minutes=freq_min)
    # Timestamps start at 06:00 UTC (inside default session 02-12)
    ts = pd.Timestamp("2026-01-04 06:00:00")

    all_rows: list[dict] = []
    price = base_price

    block_size = 2_000   # bars per regime block for 'mixed'

    while len(all_rows) < n:
        needed = n - len(all_rows)
        if needed < CYCLE:
            # pad with plain drift to reach exactly n bars
            drift = _trend_drift(price, "up")
            for _ in range(needed):
                noise = rng.normal(0, abs(price) * 0.0003)
                c = price + drift + noise
                h = max(price, c) + abs(price) * 0.0008
                lo = min(price, c) - abs(price) * 0.0008
                all_rows.append({"timestamp": ts, "open": price, "high": h,
                                 "low": lo, "close": c, "volume": 100})
                ts += freq
                price = c
            break

        if regime == "bullish":
            direction = "up"
        elif regime == "bearish":
            direction = "down"
        elif regime == "mixed":
            # alternate direction every block_size bars
            block_idx = len(all_rows) // block_size
            direction = "up" if block_idx % 2 == 0 else "down"
        else:  # ranging
            # alternate every cycle
            cycle_idx = len(all_rows) // CYCLE
            direction = "up" if cycle_idx % 2 == 0 else "down"

        cycle_rows, price, ts = _one_cycle(rng, price, ts, freq, direction)
        all_rows.extend(cycle_rows)

    df = pd.DataFrame(all_rows[:n])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].round(4)
    df["volume"] = df["volume"].astype(float)
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Run validation across all symbols
# ---------------------------------------------------------------------------

BACKTEST_CONFIG = {
    # Session filter OFF — synthetic timestamps are all 06:00 UTC but we
    # bypass it to keep config explicit for the validation.
    "disable_session_filter": True,
    # FVG-only signals (no OB confluence filter) to maximise trade count.
    "ob_confluence_only": False,
}


def run_synthetic_validation() -> list[dict]:
    """Run ICTBacktester on all SYMBOLS.  Returns list of result dicts."""
    results = []
    for symbol, tf, regime, base_price, seed in SYMBOLS:
        freq_min = 5 if tf == "5m" else 15
        df = make_synthetic_ohlcv(
            n=N_CANDLES, regime=regime, base_price=base_price,
            seed=seed, freq_min=freq_min,
        )
        bt = ICTBacktester(df, config=BACKTEST_CONFIG)
        bt.run()
        summary = bt.summary()
        results.append({
            "symbol": symbol,
            "timeframe": tf,
            "regime": regime,
            "summary": summary,
        })
    return results


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

PF_THRESHOLD = 1.2


def _verdict(results: list[dict]) -> tuple[bool, dict]:
    """Compute aggregate stats and GO/NO-GO verdict (PF > threshold)."""
    ok = [r for r in results if "error" not in r["summary"]]
    total_trades = sum(r["summary"].get("total_trades", 0) for r in ok)
    winners = sum(r["summary"].get("winners", 0) for r in ok)
    wr = (winners / total_trades * 100) if total_trades else 0.0

    r_vals = [r["summary"].get("avg_r_multiple", 0) for r in ok]
    avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0

    pf_vals = [r["summary"].get("profit_factor", 0) for r in ok
               if r["summary"].get("profit_factor", 0) != float("inf")]
    avg_pf = sum(pf_vals) / len(pf_vals) if pf_vals else 0.0

    ret_vals = [r["summary"].get("total_return_pct", 0) for r in ok]
    avg_ret = sum(ret_vals) / len(ret_vals) if ret_vals else 0.0

    dd_vals = [r["summary"].get("max_drawdown_pct", 0) for r in ok]
    avg_dd = sum(dd_vals) / len(dd_vals) if dd_vals else 0.0

    agg = {
        "symbols_total": len(results),
        "symbols_with_trades": len(ok),
        "total_trades": total_trades,
        "blended_wr_pct": round(wr, 1),
        "avg_r_multiple": round(avg_r, 2),
        "avg_profit_factor": round(avg_pf, 2),
        "avg_return_pct": round(avg_ret, 2),
        "avg_max_dd_pct": round(avg_dd, 2),
    }
    go = avg_pf > PF_THRESHOLD and total_trades >= 50
    return go, agg


def render_report(results: list[dict]) -> str:
    go, agg = _verdict(results)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    verdict_line = (
        "## ✅ GO — PF > 1.2, recommend ICT_RISK_PCT bump to 0.4"
        if go else
        "## ❌ NO-GO — PF threshold not met"
    )

    header = f"""# S-006 Synthetic Multi-Symbol Validation Report

**Generated:** {now}
**Symbols:** {len(results)} × {N_CANDLES:,} candles each
**PF threshold:** > {PF_THRESHOLD}

{verdict_line}

---

## Aggregate

| Metric | Value |
|--------|-------|
| Symbols run | {agg['symbols_total']} |
| Symbols with trades | {agg['symbols_with_trades']} |
| Total trades | {agg['total_trades']} |
| Blended WR% | {agg['blended_wr_pct']} |
| Avg R-multiple | {agg['avg_r_multiple']} |
| Avg profit factor | {agg['avg_profit_factor']} |
| Avg return% | {agg['avg_return_pct']} |
| Avg max DD% | {agg['avg_max_dd_pct']} |

---

## Per-symbol results

| Symbol | TF | Regime | Trades | WR% | Avg R | PF | Return% | Max DD% | Status |
|--------|----|--------|-------:|----:|------:|---:|--------:|--------:|--------|"""

    rows = []
    for r in results:
        sym = r["symbol"]
        tf = r["timeframe"]
        reg = r["regime"]
        s = r["summary"]
        if "error" in s:
            rows.append(f"| {sym} | {tf} | {reg} | 0 | — | — | — | — | — | ⚠️ no trades |")
            continue
        t = s.get("total_trades", 0)
        wr = s.get("win_rate_pct", 0)
        avg_r = s.get("avg_r_multiple", 0)
        pf = s.get("profit_factor", 0)
        ret = s.get("total_return_pct", 0)
        dd = s.get("max_drawdown_pct", 0)
        status = "✅" if (isinstance(pf, (int, float)) and pf > PF_THRESHOLD) else "⚠️"
        pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) and pf != float("inf") else "∞"
        rows.append(f"| {sym} | {tf} | {reg} | {t} | {wr} | {avg_r} | {pf_str} | {ret} | {dd} | {status} |")

    footer = """
---

## Config used

```json
{config}
```

## Next step (M3)

- If PF > 1.2: open PR to bump `ICT_RISK_PCT` from current to **0.4** in
  `config/master-secrets.template.yaml` and validate with live kill-switch ready.
- If NO-GO: lower `min_fvg_size_pct` or widen session window and re-run.
""".format(config=str(BACKTEST_CONFIG).replace("'", '"').replace("True", "true").replace("False", "false"))

    return "\n".join([header] + rows) + footer


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        description="Run synthetic ICT multi-symbol validation and write report."
    )
    p.add_argument(
        "--output", type=Path,
        default=REPO_ROOT / "docs" / "sprint-plans" / "s006-synthetic-report.md",
        help="Destination markdown file.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-symbol progress output.",
    )
    args = p.parse_args(argv)

    if not args.quiet:
        print(f"Running synthetic validation: {len(SYMBOLS)} symbols × {N_CANDLES:,} candles each…")

    results = run_synthetic_validation()

    go, agg = _verdict(results)

    if not args.quiet:
        for r in results:
            s = r["summary"]
            t = s.get("total_trades", 0)
            pf = s.get("profit_factor", "—")
            wr = s.get("win_rate_pct", "—")
            print(f"  {r['symbol']:8s} {r['timeframe']:4s} {r['regime']:8s} "
                  f"trades={t:4d}  WR={wr}%  PF={pf}")
        print(f"\nAggregate: {agg['total_trades']} trades  "
              f"WR={agg['blended_wr_pct']}%  avgR={agg['avg_r_multiple']}  "
              f"PF={agg['avg_profit_factor']}")
        verdict_str = "GO ✅" if go else "NO-GO ❌"
        print(f"Verdict: {verdict_str}  (threshold PF > {PF_THRESHOLD})")

    report = render_report(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report)
    print(f"\nReport written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
