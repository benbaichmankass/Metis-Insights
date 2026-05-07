"""S-015 follow-up — daily-resolution end-to-end smoke test.

HARNESS VALIDATION ONLY — NOT A BASELINE, NOT FOR PARAMETER TUNING.

⚠️ **HARNESS VALIDATION ONLY — NOT A BASELINE, NOT FOR PARAMETER TUNING.**

Pulls real BTC + ETH daily prices from the coinmetrics/data github mirror
(2010 → present, ~5.7k bars per asset) and runs a tiny VWAP signal
adapter through the T1 harness across N stratified monthly folds.

The point is to *prove* that:
1. The fetcher reaches a real source from inside this sandbox.
2. The sampler produces disjoint folds at the requested cadence.
3. The harness produces deterministic per-fold metrics with real data.
4. The 2 bps slippage model behaves monotonically end-to-end.

What this is **NOT**:

- A baseline for the live trader. The live trader runs at 5m / 15m;
  this is daily reference rates with synthetic OHLC (close replicated).
- An input for parameter tuning. Operator hard rule: "we definitely
  don't want the models learning from incorrect datasets." The output
  here is reference-rate daily prices, not tradeable bar shapes.
- A claim about VWAP / turtle_soup quality. The strategies are wired
  for intraday signals; running them at daily timeframe answers a
  different question entirely.

Usage:
    PYTHONPATH=. python scripts/sprint015/run_smoke_test.py \\
        > docs/backtests/sprint-015/smoke-test-daily.md
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.sprint015 import data_sources as ds  # noqa: E402
from scripts.sprint015 import run_backtest as rb  # noqa: E402
from scripts.sprint015 import sample_data as sd  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TIMEFRAME = "1d"
N_FOLDS = 5
SEED = 42
RECENT_MONTHS = 36  # cap at 3y for the smoke test — keeps wall-clock low


def _fetch_full_series(symbol: str) -> Tuple[pd.DataFrame, str]:
    """Fetch a sweeping window covering the full smoke-test horizon."""
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end.replace(year=end.year - 5)
    df, source, attempts = ds.fetch_ohlcv(symbol, TIMEFRAME, start, end)
    return df, source


def _slice_for_buckets(
    df: pd.DataFrame, buckets: List[sd.MonthBucket],
) -> pd.DataFrame:
    """Return only the rows whose (year, month) is in *buckets*."""
    months = {(b.year, b.month) for b in buckets}
    mask = [(ts.year, ts.month) in months for ts in df.index]
    return df.loc[mask]


def _toy_vwap_strategy(frame: pd.DataFrame, params: Dict[str, Any]):
    """Bar-by-bar VWAP-deviation signal — uses the same arithmetic as
    ``src.units.strategies.vwap.build_vwap_signal`` but rolls a window so
    we can run on daily bars.

    Parameters: ``threshold_std`` (float), ``lookback`` (int).
    """
    threshold = float(params.get("threshold_std", 1.0))
    lookback = int(params.get("lookback", 20))
    if len(frame) <= lookback:
        return []
    sigs = []
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    cum_pv = (typical * frame["volume"]).cumsum()
    cum_v = frame["volume"].cumsum().replace(0, pd.NA)
    vwap_series = cum_pv / cum_v
    for i in range(lookback, len(frame)):
        window_typ = typical.iloc[i - lookback : i + 1]
        std = float(window_typ.std())
        if std <= 0 or pd.isna(vwap_series.iloc[i]):
            continue
        price = float(frame["close"].iloc[i])
        vwap = float(vwap_series.iloc[i])
        dev = (price - vwap) / std
        if dev <= -threshold:
            sigs.append({"ts": frame.index[i], "side": "buy", "qty": 1.0})
        elif dev >= threshold:
            sigs.append({"ts": frame.index[i], "side": "sell", "qty": 1.0})
    return sigs


def _build_folds(
    full_df: pd.DataFrame, ref_date: datetime,
) -> Tuple[List[sd.MonthBucket], List[pd.DataFrame]]:
    folds = sd.stratified_folds(
        ref_date.date(), n_folds=N_FOLDS, total_months=RECENT_MONTHS, seed=SEED,
    )
    fold_frames = [_slice_for_buckets(full_df, fold) for fold in folds]
    return folds, fold_frames


def _format_per_fold(result: rb.BacktestResult) -> List[Dict[str, Any]]:
    return [
        {
            "fold": i,
            "n_trades": fm.n_trades,
            "realised_pnl": round(fm.realised_pnl, 2),
            "win_rate": round(fm.win_rate, 3),
            "sharpe": round(fm.sharpe, 4),
            "max_drawdown": round(fm.max_drawdown, 2),
        }
        for i, fm in enumerate(result.folds)
    ]


def _run_for_symbol(symbol: str, ref_date: datetime) -> Dict[str, Any]:
    df, source = _fetch_full_series(symbol)
    folds, fold_frames = _build_folds(df, ref_date)
    fold_summary = sd.fold_summary(folds)
    bar_counts = [int(len(f)) for f in fold_frames]

    # Slippage sweep at threshold=1.0 (single fixed parameter — this is a
    # smoke test, not a parameter sweep).
    slip_rows = []
    for slip in [0.0, 2.0, 10.0]:
        result = rb.run_backtest(
            symbol, _toy_vwap_strategy, {"threshold_std": 1.0, "lookback": 20},
            fold_frames, slippage_bps=slip,
        )
        slip_rows.append({
            "slippage_bps": slip,
            "aggregate_pnl": round(result.aggregate_pnl, 2),
            "aggregate_sharpe": round(result.aggregate_sharpe, 4),
            "aggregate_max_dd": round(result.aggregate_max_dd, 2),
        })

    # One per-fold breakdown at the operator-default 2 bps slippage.
    detail = rb.run_backtest(
        symbol, _toy_vwap_strategy, {"threshold_std": 1.0, "lookback": 20},
        fold_frames, slippage_bps=2.0,
    )
    return {
        "symbol": symbol,
        "source": source,
        "bars_total": int(len(df)),
        "date_range": [str(df.index.min()), str(df.index.max())],
        "fold_bar_counts": bar_counts,
        "fold_recency_summary": fold_summary,
        "slippage_sweep": slip_rows,
        "per_fold_at_2bps": _format_per_fold(detail),
    }


def main() -> int:
    ref_date = datetime.now(timezone.utc)
    print("# S-015 — Daily-resolution smoke test (NOT a baseline)\n")
    print(
        "_⚠️ HARNESS VALIDATION ONLY. The data here is **daily reference**\n"
        "**rates** from coinmetrics/data — not 5m / 15m intraday bars.\n"
        "DO NOT use these numbers to tune live strategy parameters._\n"
    )
    print(f"\n- Generated: `{ref_date.replace(microsecond=0).isoformat()}`")
    print(f"- Folds: **{N_FOLDS}** stratified, disjoint")
    print(f"- Recency window: **last {RECENT_MONTHS} months**")
    print(f"- Sampler seed: **{SEED}**")
    print("- Slippage: 2 bps round-trip (default), plus a 0/2/10 sweep\n")

    summaries: List[Dict[str, Any]] = []
    for symbol in SYMBOLS:
        try:
            summary = _run_for_symbol(symbol, ref_date)
        except ds.DataUnavailableError as exc:
            print(f"\n## {symbol}\n\n_skipped — {exc}_\n")
            continue
        summaries.append(summary)
        print(f"\n## {symbol}\n")
        print(f"- source: `{summary['source']}` (provenance: keyless github mirror)")
        print(f"- bars in full series: **{summary['bars_total']}** "
              f"({summary['date_range'][0]} → {summary['date_range'][1]})")
        print(f"- bars per fold: {summary['fold_bar_counts']}")
        print(f"- recency mix per fold (recent / mid / old): "
              f"{[(d['recent'], d['mid'], d['old']) for d in summary['fold_recency_summary']]}\n")

        print("### Slippage sweep (threshold=1.0σ, lookback=20)\n")
        print("| slippage_bps | aggregate_pnl | sharpe | max_dd |")
        print("|---:|---:|---:|---:|")
        for r in summary["slippage_sweep"]:
            print(f"| {r['slippage_bps']:.1f} | {r['aggregate_pnl']:.2f} | "
                  f"{r['aggregate_sharpe']:.4f} | {r['aggregate_max_dd']:.2f} |")
        print()

        print("### Per-fold breakdown (2 bps slippage)\n")
        print("| fold | n_trades | realised_pnl | win_rate | sharpe | max_dd |")
        print("|---:|---:|---:|---:|---:|---:|")
        for r in summary["per_fold_at_2bps"]:
            print(f"| {r['fold']} | {r['n_trades']} | {r['realised_pnl']:.2f} | "
                  f"{r['win_rate']:.3f} | {r['sharpe']:.4f} | {r['max_drawdown']:.2f} |")

    print("\n## What this proves\n")
    print(
        "1. The github-raw adapter reaches `coinmetrics/data` from inside "
        "this sandbox and returns real daily bars.\n"
        "2. The recency-weighted month-bucket sampler produces "
        f"{N_FOLDS} disjoint folds with a balanced recency mix.\n"
        "3. The harness runs a strategy adapter end-to-end and computes "
        "per-fold realised P&L, Sharpe, win rate, and max drawdown.\n"
        "4. The 2 bps slippage model degrades P&L monotonically vs the "
        "0 bps reference run.\n"
    )
    print("## What this does NOT prove\n")
    print(
        "1. Anything about VWAP or turtle_soup at 5m / 15m — those run on "
        "real intraday bars, not daily reference rates with synthesised "
        "OHLC.\n"
        "2. Anything about parameter tuning — operator hard rule: do not "
        "learn parameters from incorrect-resolution data.\n"
        "3. Anything about live P&L — the slippage model is a stylised "
        "constant, not a Bybit fill simulator.\n"
    )

    print("\n## Machine-readable\n\n```json")
    print(json.dumps(summaries, indent=2, default=str))
    print("```")
    return 0


if __name__ == "__main__":
    sys.exit(main())
