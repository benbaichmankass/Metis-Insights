"""S-015 T3 — strategy-agnostic analysis on existing repo fixtures.

Runs the T1 harness end-to-end against the small OHLCV fixtures
already committed to ``data/`` (no network, no Bybit). The output is
a *harness validation* report — it confirms the data → strategy →
metrics pipeline works, and gives Session B a reference shape to
compare against once it loads real 5-year data.

**This is not a P&L claim.** The fixtures here are 7 days (March 2026
BTC 1m) and 3.5 days (July 2022 BTC 1m) — orders of magnitude too
small to draw conclusions about strategy quality. The numbers below
are *only* meaningful as a "the harness ran and produced
sensible-shape output" smoke test.

Usage:
    PYTHONPATH=. python scripts/sprint015/analyze_fixtures.py \\
        > docs/backtests/sprint-015/harness-validation.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.sprint015 import run_backtest as rb  # noqa: E402

FIXTURES = [
    ("btc_2026_03", REPO_ROOT / "data" / "btc_1m_sample.csv"),
    ("btc_2022_07", REPO_ROOT / "data" / "backtest_candles.csv"),
]


def _load_fixture(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Down-sample 1m bars to *rule* (e.g. ``15min``) for VWAP signals.
    Strategies in this repo run on 15m by default; resampling here
    keeps the harness honest about the bar grid the strategy expects."""
    out = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return out


def _vwap_strategy(frame: pd.DataFrame, params: Dict[str, Any]):
    """Wrap the production VWAP signal builder behind the harness contract.

    Walks the frame bar by bar, computing the rolling-window signal at
    each step using the same ``build_vwap_signal`` the live trader
    calls. Threshold is a strategy parameter so T4 can sweep it.
    """
    from src.units.strategies.vwap import build_vwap_signal
    import src.units.strategies.vwap as vwap_module

    threshold = float(params.get("entry_std_threshold", vwap_module.ENTRY_STD_THRESHOLD))
    qty = float(params.get("qty", 1.0))
    lookback = int(params.get("lookback", 50))
    symbol = str(params.get("symbol", "BTCUSDT"))

    saved = vwap_module.ENTRY_STD_THRESHOLD
    vwap_module.ENTRY_STD_THRESHOLD = threshold
    try:
        sigs = []
        for i in range(lookback, len(frame)):
            window = frame.iloc[i - lookback : i + 1]
            sig = build_vwap_signal(window, symbol=symbol, qty=qty)
            if sig.get("side") in ("buy", "sell"):
                sigs.append({"ts": window.index[-1], "side": sig["side"], "qty": sig["qty"]})
        return sigs
    finally:
        vwap_module.ENTRY_STD_THRESHOLD = saved


def _slippage_sweep(frame: pd.DataFrame, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for slip in [0.0, 2.0, 5.0, 10.0, 20.0]:
        result = rb.run_backtest(
            "vwap", _vwap_strategy, params, [frame], slippage_bps=slip,
        )
        fm = result.folds[0]
        rows.append({
            "slippage_bps": slip,
            "realised_pnl": round(fm.realised_pnl, 2),
            "n_trades": fm.n_trades,
            "win_rate": round(fm.win_rate, 3),
            "max_drawdown": round(fm.max_drawdown, 2),
        })
    return rows


def _signal_density(frame: pd.DataFrame, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for thr in [0.5, 1.0, 1.5, 2.0]:
        local_params = {**params, "entry_std_threshold": thr}
        sigs = list(_vwap_strategy(frame, local_params))
        rows.append({"threshold_std": thr, "n_signals": len(sigs)})
    return rows


def _hour_attribution(frame: pd.DataFrame, params: Dict[str, Any]) -> Dict[int, int]:
    sigs = list(_vwap_strategy(frame, params))
    counts: Dict[int, int] = {}
    for s in sigs:
        h = int(s["ts"].hour)
        counts[h] = counts.get(h, 0) + 1
    return dict(sorted(counts.items()))


def _build_report(name: str, df_15m: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
    params = {"qty": 1.0, "lookback": 50, "symbol": "BTCUSDT"}
    slip = _slippage_sweep(df_15m, params)
    density = _signal_density(df_15m, params)
    hours = _hour_attribution(df_15m, params)

    out = [f"### Fixture: `{name}`\n"]
    out.append(
        f"- bars (15m): **{len(df_15m)}** "
        f"covering `{df_15m.index[0]}` → `{df_15m.index[-1]}`\n"
    )
    out.append("\n#### Slippage sensitivity (VWAP, threshold=1.0σ, 1 fold)\n\n")
    out.append("| slippage_bps | realised_pnl | n_trades | win_rate | max_dd |\n")
    out.append("|---:|---:|---:|---:|---:|\n")
    for r in slip:
        out.append(
            f"| {r['slippage_bps']:.1f} | {r['realised_pnl']:.2f} | "
            f"{r['n_trades']} | {r['win_rate']:.3f} | {r['max_drawdown']:.2f} |\n"
        )
    out.append("\n#### Signal density vs entry threshold\n\n")
    out.append("| threshold_std | n_signals |\n|---:|---:|\n")
    for r in density:
        out.append(f"| {r['threshold_std']:.1f} | {r['n_signals']} |\n")
    out.append("\n#### Hour-of-day signal attribution (UTC, threshold=1.0σ)\n\n")
    if hours:
        out.append("| hour_utc | n_signals |\n|---:|---:|\n")
        for h, c in hours.items():
            out.append(f"| {h:02d} | {c} |\n")
    else:
        out.append("_(no signals fired in this fixture window — expected for short windows.)_\n")
    out.append("\n")

    summary = {
        "fixture": name,
        "bars_15m": int(len(df_15m)),
        "slippage_sweep": slip,
        "signal_density": density,
        "hour_attribution": hours,
    }
    return "".join(out), summary


def main() -> int:
    print("# S-015 T3 — Harness validation on repo fixtures\n")
    print(
        "_Generated by `scripts/sprint015/analyze_fixtures.py`. "
        "**This is harness validation, not strategy quality.** "
        "The fixtures committed to `data/` cover only a few days each — "
        "orders of magnitude too small for real backtest claims. The "
        "5-year analysis lands in Session B once the data fetcher has "
        "outbound network access._\n",
    )
    print("\n## Goal\n")
    print(
        "Demonstrate the T1 harness running end-to-end against existing "
        "repo OHLCV fixtures. Confirm:\n\n"
        "1. The data pipeline (CSV → DataFrame → resample → strategy) is "
        "wired and produces sensible-shape output.\n"
        "2. The slippage sensitivity dial works in both directions.\n"
        "3. The signal density responds monotonically to the threshold "
        "parameter (sanity check on the strategy adapter).\n"
        "4. Hour-of-day attribution buckets are produced for the eventual "
        "killzone overlay analysis.\n",
    )
    print("\n## Results per fixture\n")

    summaries: List[Dict[str, Any]] = []
    for name, path in FIXTURES:
        if not path.exists():
            print(f"\n### Fixture: `{name}`\n\n_skipped — file not found at `{path}`_\n")
            continue
        df_1m = _load_fixture(path)
        df_15m = _resample(df_1m, "15min")
        report, summary = _build_report(name, df_15m)
        print(report)
        summaries.append(summary)

    print("\n## Machine-readable summary\n\n```json")
    print(json.dumps(summaries, indent=2, default=str))
    print("```")
    return 0


if __name__ == "__main__":
    sys.exit(main())
