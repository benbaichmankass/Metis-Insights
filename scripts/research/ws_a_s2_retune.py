#!/usr/bin/env python3
"""WS-A S2 — per-symbol re-tune of the S1 diversifier leads.

Coarse, OVERFITTING-AWARE grid search. For each (symbol, strategy) lead
from the S1 matrix, sweeps a small param grid and keeps only configs that
are net-positive in BOTH the in-sample (<=2022) AND out-of-sample (2023+)
windows with OOS n>=20 — then ranks by OOS expectancy. Robustness across
the IS/OOS boundary is the selection criterion, NOT peak net-R (which
overfits). The default-param baseline is reported alongside each lead so
the re-tune's marginal value is visible.

Reuses the S1-downloaded CSVs under ~/ws_a_sweep_out/<date>/data/ when
present; else re-fetches via yfinance. Runs harness subprocesses
concurrently (threads — they're subprocess-bound).

Output: ~/ws_a_s2_out/<UTC-date>/{results.json,SUMMARY.md}; SUMMARY.md is
printed to stdout for the trainer-vm-diag relay.

NOTE: still a research probe. Daily continuous-contract (=F) data — the
roll-artifact caveat from S1 stands. A surviving config is a *candidate*
for a Tier-3 proposal, not a proposal; real-venue commissions + roll-
adjusted data verification come first.
"""
from __future__ import annotations

import datetime as dt
import glob
import itertools
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FEE_BPS = "2.0"
IS_END = "2022-12-31"
OOS_START = "2023-01-01"
WORKERS = 4

SCRIPT = {
    "trend": "scripts/backtest_trend.py",
    "pullback": "scripts/backtest_pullback.py",
}

# S1 diversifier leads to re-tune (cleanest OOS-holding, BTC-uncorrelated).
TARGETS = [
    ("ES=F", "S&P 500", "trend"),
    ("NQ=F", "Nasdaq 100", "trend"),
    ("GC=F", "Gold", "trend"),
    ("HG=F", "Copper", "pullback"),
    ("GC=F", "Gold", "pullback"),
]

# Coarse grids (kept small on purpose — wider grids overfit).
TREND_GRID = {
    "--donchian": [20, 30, 40, 55],
    "--atr-stop-mult": [2.0, 2.5, 3.0],
    "--trail-mult": [3.0, 4.0, 5.0],
}
PULLBACK_GRID = {
    "--pullback-lookback": [10, 15, 20],
    "--pullback-frac": [0.5, 0.618],
    "--atr-stop-mult": [2.0, 2.5, 3.0],
    "--trail-mult": [4.0, 5.0],
}
GRID = {"trend": TREND_GRID, "pullback": PULLBACK_GRID}


def find_csv(ticker: str) -> Path | None:
    safe = ticker.replace("=", "_")
    hits = sorted(glob.glob(str(Path.home() / "ws_a_sweep_out" / "*" / "data" / f"{safe}.csv")))
    return Path(hits[-1]) if hits else None


def fetch_daily(ticker: str, out_csv: Path) -> int:
    import yfinance as yf

    df = yf.download(ticker, period="max", interval="1d",
                     auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        return 0
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    ts = "Datetime" if "Datetime" in df.columns else "Date"
    out = df[[ts, "Open", "High", "Low", "Close"]].copy()
    out.columns = ["timestamp", "open", "high", "low", "close"]
    out.dropna().to_csv(out_csv, index=False)
    return len(out)


def run(strategy: str, csv: Path, params: dict, start: str | None, end: str | None) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as jf:
        jpath = Path(jf.name)
    cmd = [sys.executable, str(REPO / SCRIPT[strategy]), "--data", str(csv),
           "--json", str(jpath), "--fee-bps-roundtrip", FEE_BPS]
    for k, v in params.items():
        cmd += [k, str(v)]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        d = json.loads(jpath.read_text())
        return {"net_r": d.get("net_total_r"), "exp": d.get("net_expectancy_r"),
                "trades": d.get("total_trades"), "win": d.get("win_rate_pct"),
                "dd": d.get("max_drawdown_r")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[-200:]}
    finally:
        jpath.unlink(missing_ok=True)


def grid_configs(strategy: str):
    g = GRID[strategy]
    keys = list(g)
    for combo in itertools.product(*(g[k] for k in keys)):
        yield dict(zip(keys, combo))


def evaluate(strategy: str, csv: Path):
    """Return (baseline, ranked_robust_configs)."""
    configs = list(grid_configs(strategy))

    def both_windows(params):
        is_ = run(strategy, csv, params, None, IS_END)
        oos = run(strategy, csv, params, OOS_START, None)
        return params, is_, oos

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for params, is_, oos in ex.map(both_windows, configs):
            if "error" in is_ or "error" in oos:
                continue
            rows.append((params, is_, oos))

    baseline = next((r for r in rows if (
        (strategy == "trend" and r[0] == {"--donchian": 20, "--atr-stop-mult": 2.5, "--trail-mult": 3.0})
        or (strategy == "pullback" and r[0] == {"--pullback-lookback": 10, "--pullback-frac": 0.5,
                                                "--atr-stop-mult": 2.5, "--trail-mult": 5.0})
    )), None)

    robust = [r for r in rows
              if isinstance(r[1].get("net_r"), (int, float)) and r[1]["net_r"] > 0
              and isinstance(r[2].get("net_r"), (int, float)) and r[2]["net_r"] > 0
              and (r[2].get("trades") or 0) >= 20]
    robust.sort(key=lambda r: (r[2].get("exp") or -9, min(r[1]["net_r"], r[2]["net_r"])), reverse=True)
    return baseline, robust, len(rows)


def f(v, nd=1):
    return f"{v:+.{nd}f}" if isinstance(v, (int, float)) else "—"


def pstr(params: dict) -> str:
    return " ".join(f"{k.lstrip('-')}={v}" for k, v in params.items())


def main() -> int:
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path.home() / "ws_a_s2_out" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(exist_ok=True)

    results = {}
    lines = [
        f"# WS-A S2 — Diversifier Re-tune ({date})",
        "",
        "Coarse grid, **robustness-selected**: kept only configs net-positive "
        "in BOTH IS(<=2022) AND OOS(2023+), OOS n>=20, ranked by OOS "
        f"expectancy. Net-of-fee @ {FEE_BPS}bps. Default-param baseline shown "
        "for comparison. Daily =F data — roll-artifact caveat from S1 stands; "
        "a surviving config is a *candidate*, not a Tier-3 proposal.",
        "",
    ]
    for ticker, label, strat in TARGETS:
        csv = find_csv(ticker)
        if csv is None:
            csv = out_dir / "data" / f"{ticker.replace('=', '_')}.csv"
            if fetch_daily(ticker, csv) < 250:
                lines += [f"## {label} / {strat}", "", "- skipped (no data)", ""]
                continue
        baseline, robust, n_ok = evaluate(strat, csv)
        results[f"{label}/{strat}"] = {
            "baseline": ({"params": baseline[0], "is": baseline[1], "oos": baseline[2]}
                         if baseline else None),
            "top": [{"params": p, "is": i, "oos": o} for p, i, o in robust[:8]],
            "configs_evaluated": n_ok,
        }
        lines.append(f"## {label} / {strat}")
        lines.append("")
        if baseline:
            b_is, b_oos = baseline[1], baseline[2]
            lines.append(f"- **baseline** (`{pstr(baseline[0])}`): "
                         f"IS {f(b_is.get('net_r'))}R / OOS {f(b_oos.get('net_r'))}R, "
                         f"OOS exp {f(b_oos.get('exp'),3)}, OOS n={b_oos.get('trades')}, "
                         f"OOS maxDD {f(b_oos.get('dd'))}R")
        if not robust:
            lines += ["- no config passed both-windows-positive + OOS n>=20", ""]
            continue
        lines.append(f"- **top robust configs** ({len(robust)}/{n_ok} passed the both-positive filter):")
        for p, i, o in robust[:5]:
            lines.append(f"    - `{pstr(p)}` — IS {f(i['net_r'])}R / **OOS {f(o['net_r'])}R**, "
                         f"OOS exp {f(o.get('exp'),3)}, OOS n={o.get('trades')}, "
                         f"win {f(o.get('win'),0)}%, OOS maxDD {f(o.get('dd'))}R")
        lines.append("")

    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
    summary = "\n".join(lines) + "\n"
    (out_dir / "SUMMARY.md").write_text(summary)
    print("\n" + "=" * 72 + "\n" + summary + "=" * 72)
    print(f"[ws-a-s2] wrote {out_dir}/results.json + SUMMARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
