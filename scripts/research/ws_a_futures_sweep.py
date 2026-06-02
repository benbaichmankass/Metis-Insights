#!/usr/bin/env python3
"""WS-A S1 — futures generalization sweep (Meantime Expansion Program).

Runs the higher-timeframe roster harnesses (trend / fade / squeeze /
pullback) across the NinjaTrader-tradeable futures universe on DAILY
bars (yfinance continuous front-month '=F' series), net-of-fee, with a
full-period + OOS(2023+) walk-forward split. Produces a
symbol x strategy generalization matrix.

WHY DAILY: yfinance only serves ~60d of 5m/15m and ~730d of 1h, but
multi-year daily — so daily is the only resolution that supports a
robust multi-year cross-asset probe here. The intraday harnesses
(ict_scalp 5m, fvg_range 15m) need IBKR intraday history and are a
separate follow-up (the MES IBKR pull path generalized per symbol).

WHY DEFAULT PARAMS: this is the WHERE-does-an-edge-exist probe. Cells
that show a net-positive, OOS-holding edge graduate to a per-symbol
re-tune (crypto params don't transfer) before any Tier-3 proposal.

FEES: futures are commission-based, not bps. 2.0 bps round-trip is a
deliberately conservative stand-in for liquid-future micro notional;
the exact NinjaTrader per-contract commission is verified before live
(see docs/research/tradeable-universe-2026-06-02.md).

Output: ~/ws_a_sweep_out/<UTC-date>/{all_metrics.json,SUMMARY.md};
SUMMARY.md is also printed to stdout so the trainer-vm-diag relay posts
it straight back.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FEE_BPS = "2.0"
OOS_START = "2023-01-01"

# yfinance ticker -> (display label, asset class, NinjaTrader micro/standard)
UNIVERSE = [
    ("ES=F", "S&P 500", "index", "MES/ES"),
    ("NQ=F", "Nasdaq 100", "index", "MNQ/NQ"),
    ("YM=F", "Dow", "index", "MYM/YM"),
    ("RTY=F", "Russell 2000", "index", "M2K/RTY"),
    ("GC=F", "Gold", "metals", "MGC/GC"),
    ("SI=F", "Silver", "metals", "SIL/SI"),
    ("HG=F", "Copper", "metals", "MHG/HG"),
    ("CL=F", "Crude Oil", "energy", "MCL/CL"),
    ("NG=F", "Nat Gas", "energy", "QG/NG"),
    ("ZN=F", "10Y Note", "rates", "ZN"),
    ("ZB=F", "T-Bond", "rates", "ZB"),
    ("ZC=F", "Corn", "grains", "ZC"),
    ("ZS=F", "Soybeans", "grains", "ZS"),
    ("ZW=F", "Wheat", "grains", "ZW"),
    ("6E=F", "Euro FX", "fx", "6E/M6E"),
    ("6J=F", "Yen FX", "fx", "6J"),
    ("BTC=F", "CME Bitcoin", "crypto", "MBT"),
    ("ETH=F", "CME Ether", "crypto", "MET"),
]

STRATEGIES = {
    "trend": "scripts/backtest_trend.py",
    "fade": "scripts/backtest_fade.py",
    "squeeze": "scripts/backtest_squeeze.py",
    "pullback": "scripts/backtest_pullback.py",
}


def fetch_daily(ticker: str, out_csv: Path) -> int:
    """Download max daily history for one ticker -> harness CSV. Returns row count."""
    import yfinance as yf

    df = yf.download(
        ticker, period="max", interval="1d",
        auto_adjust=False, progress=False, threads=False,
    )
    if df is None or df.empty:
        return 0
    # Single-ticker download can still return a MultiIndex column frame.
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    ts_col = "Datetime" if "Datetime" in df.columns else "Date"
    out = df[[ts_col, "Open", "High", "Low", "Close"]].copy()
    out.columns = ["timestamp", "open", "high", "low", "close"]
    out = out.dropna()
    out.to_csv(out_csv, index=False)
    return len(out)


def run_harness(script: str, csv: Path, start: str | None, end: str | None) -> dict:
    """Run one harness, return its JSON summary (or an {error} dict)."""
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as jf:
        jpath = Path(jf.name)
    cmd = [
        sys.executable, str(REPO / script),
        "--data", str(csv), "--json", str(jpath),
        "--fee-bps-roundtrip", FEE_BPS,
    ]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        return json.loads(jpath.read_text())
    except subprocess.CalledProcessError as e:
        return {"error": (e.stderr or e.stdout or "nonzero exit")[-300:]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[-300:]}
    finally:
        jpath.unlink(missing_ok=True)


def pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def cell(summary: dict) -> dict:
    """Normalize a harness summary into the comparable metric cell."""
    if "error" in summary:
        return {"error": summary["error"]}
    return {
        "net_total_r": pick(summary, "net_total_r", default=None),
        "net_r_long": pick(summary, "net_total_r_long", default=None),
        "net_r_short": pick(summary, "net_total_r_short", default=None),
        "net_exp_r": pick(summary, "net_expectancy_r", default=None),
        "win_rate_pct": pick(summary, "win_rate_pct", default=None),
        "trades": pick(summary, "total_trades", "trades", default=None),
        "max_dd_r": pick(summary, "max_drawdown_r", "max_dd_r", default=None),
        "data_start": pick(summary, "data_start", default=None),
        "data_end": pick(summary, "data_end", default=None),
    }


def fmt(v, nd=1):
    return f"{v:+.{nd}f}" if isinstance(v, (int, float)) else "—"


def main() -> int:
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path.home() / "ws_a_sweep_out" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(exist_ok=True)

    results: dict = {}
    skipped: list[str] = []
    for ticker, label, klass, contract in UNIVERSE:
        csv = data_dir / f"{ticker.replace('=', '_')}.csv"
        n = fetch_daily(ticker, csv)
        if n < 250:  # need ~1y of daily bars for anything meaningful
            skipped.append(f"{ticker} ({label}): {n} rows")
            continue
        results[ticker] = {"label": label, "class": klass, "contract": contract,
                           "rows": n, "strategies": {}}
        for strat, script in STRATEGIES.items():
            full = cell(run_harness(script, csv, None, None))
            oos = cell(run_harness(script, csv, OOS_START, None))
            results[ticker]["strategies"][strat] = {"full": full, "oos": oos}
        print(f"  done {ticker} ({label}) — {n} daily rows", flush=True)

    (out_dir / "all_metrics.json").write_text(json.dumps(results, indent=2))

    # ---- SUMMARY.md: full-period net-R matrix + OOS hold flags ----
    lines = [
        f"# WS-A S1 — Futures Generalization Sweep ({date})",
        "",
        "Daily bars (yfinance continuous '=F'), default params, "
        f"net-of-fee @ {FEE_BPS}bps round-trip. Each cell: **full-period "
        "net-R** (OOS-2023+ net-R). `n` = full-period trades. Re-tune the "
        "net-positive + OOS-holding cells before any Tier-3 proposal.",
        "",
        "| Symbol (contract) | Class | trend | fade | squeeze | pullback |",
        "|---|---|---|---|---|---|",
    ]
    for ticker, blob in results.items():
        row = [f"{blob['label']} ({blob['contract']})", blob["class"]]
        for strat in STRATEGIES:
            sc = blob["strategies"][strat]
            f, o = sc["full"], sc["oos"]
            if "error" in f:
                row.append("err")
            else:
                n = f.get("trades") or 0
                row.append(f"{fmt(f.get('net_total_r'))} ({fmt(o.get('net_total_r'))}) n={n}")
        lines.append("| " + " | ".join(row) + " |")

    # Winners: net-positive full AND OOS, with a usable sample.
    lines += ["", "## Net-positive + OOS-holding (re-tune candidates)", ""]
    winners = []
    for ticker, blob in results.items():
        for strat in STRATEGIES:
            sc = blob["strategies"][strat]
            f, o = sc["full"], sc["oos"]
            if "error" in f or "error" in o:
                continue
            ft, ot = f.get("net_total_r"), o.get("net_total_r")
            n = f.get("trades") or 0
            if isinstance(ft, (int, float)) and isinstance(ot, (int, float)) \
                    and ft > 0 and ot > 0 and n >= 20:
                winners.append((ot, f"- **{blob['label']} / {strat}** — full {fmt(ft)}R, "
                                    f"OOS {fmt(ot)}R, n={n}, win {fmt(f.get('win_rate_pct'),0)}%, "
                                    f"maxDD {fmt(f.get('max_dd_r'))}R"))
    for _, ln in sorted(winners, reverse=True):
        lines.append(ln)
    if not winners:
        lines.append("- (none cleared net-positive + OOS-holding + n>=20)")

    if skipped:
        lines += ["", "## Skipped (insufficient history)", ""]
        lines += [f"- {s}" for s in skipped]

    summary = "\n".join(lines) + "\n"
    (out_dir / "SUMMARY.md").write_text(summary)
    print("\n" + "=" * 72)
    print(summary)
    print("=" * 72)
    print(f"[ws-a-sweep] wrote {out_dir}/all_metrics.json + SUMMARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
