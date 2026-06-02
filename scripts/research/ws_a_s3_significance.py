#!/usr/bin/env python3
"""WS-A S3 — significance + robustness test of the S2 survivors.

S1 found edges, S2 tuned them, but every OOS verdict rested on ~20-32
trades. S3 asks the only question that matters before any further
investment: **is the edge statistically distinguishable from luck, and is
it consistent across time — or carried by one window?**

For each tuned survivor (Gold/trend, Gold/pullback, Copper/pullback) it
runs the harness over FULL history with --emit-trades, then on the
per-trade net-R series computes:

  1. Walk-forward-by-year consistency: net-R + trade count per calendar
     year. An edge carried by a single year is fragile regardless of total.
  2. Block bootstrap (preserves autocorrelation): resample the trade
     sequence in blocks B times; report the expectancy distribution, the
     fraction of resamples with total net-R > 0 (a bootstrap significance
     proxy), and the 5th/50th/95th percentile expectancy.

A survivor that is (a) net-positive in a majority of years AND (b) has a
bootstrap 5th-percentile expectancy > 0 is a genuine candidate worth the
data-plumbing / demo-ladder investment. Otherwise it isn't.

CAVEAT (unchanged): daily continuous-contract (=F) data, 2.0bps
placeholder fees. Significance here is *internal* (vs resampled luck), not
a live guarantee. Still not a Tier-3 basis.

Output: ~/ws_a_s3_out/<UTC-date>/{results.json,SUMMARY.md}; printed for
the trainer-vm-diag relay.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
FEE_BPS = "2.0"
B = 10000          # bootstrap resamples
BLOCK = 8          # block length (preserves short-run autocorrelation)
RNG = np.random.default_rng(20260602)

SCRIPT = {"trend": "scripts/backtest_trend.py", "pullback": "scripts/backtest_pullback.py"}

# Tuned survivors from S2 (best robust config per lead).
SURVIVORS = [
    ("GC=F", "Gold", "trend",
     {"--donchian": 30, "--atr-stop-mult": 2.0, "--trail-mult": 4.0}),
    ("GC=F", "Gold", "pullback",
     {"--pullback-lookback": 15, "--pullback-frac": 0.618, "--atr-stop-mult": 2.0, "--trail-mult": 4.0}),
    ("HG=F", "Copper", "pullback",
     {"--pullback-lookback": 15, "--pullback-frac": 0.5, "--atr-stop-mult": 2.0, "--trail-mult": 4.0}),
]


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


def emit_trades(strategy: str, csv: Path, params: dict) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
        tpath = Path(tf.name)
    cmd = [sys.executable, str(REPO / SCRIPT[strategy]), "--data", str(csv),
           "--fee-bps-roundtrip", FEE_BPS, "--emit-trades", str(tpath)]
    for k, v in params.items():
        cmd += [k, str(v)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        rows = [json.loads(ln) for ln in tpath.read_text().splitlines() if ln.strip()]
        return rows
    finally:
        tpath.unlink(missing_ok=True)


def block_bootstrap(net: np.ndarray) -> dict:
    n = len(net)
    if n < BLOCK + 1:
        return {"error": f"too few trades ({n})"}
    n_blocks = int(np.ceil(n / BLOCK))
    starts_max = n - BLOCK
    totals = np.empty(B)
    exps = np.empty(B)
    for b in range(B):
        starts = RNG.integers(0, starts_max + 1, size=n_blocks)
        sample = np.concatenate([net[s:s + BLOCK] for s in starts])[:n]
        totals[b] = sample.sum()
        exps[b] = sample.mean()
    return {
        "n_trades": int(n),
        "observed_total_r": round(float(net.sum()), 2),
        "observed_exp_r": round(float(net.mean()), 4),
        "frac_total_positive": round(float((totals > 0).mean()), 4),
        "exp_p05": round(float(np.percentile(exps, 5)), 4),
        "exp_p50": round(float(np.percentile(exps, 50)), 4),
        "exp_p95": round(float(np.percentile(exps, 95)), 4),
    }


def by_year(rows: list[dict]) -> list[tuple[str, int, float]]:
    acc = defaultdict(lambda: [0, 0.0])
    for r in rows:
        yr = str(r.get("entry_time", ""))[:4]
        if len(yr) == 4 and yr.isdigit():
            acc[yr][0] += 1
            acc[yr][1] += float(r.get("net_r", 0.0))
    return [(y, acc[y][0], round(acc[y][1], 2)) for y in sorted(acc)]


def pstr(params: dict) -> str:
    return " ".join(f"{k.lstrip('-')}={v}" for k, v in params.items())


def main() -> int:
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path.home() / "ws_a_s3_out" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(exist_ok=True)

    results = {}
    lines = [
        f"# WS-A S3 — Significance & Robustness ({date})",
        "",
        f"Block bootstrap (B={B}, block={BLOCK}) + walk-forward-by-year on the "
        "S2-tuned survivors, full history, net-of-fee @ "
        f"{FEE_BPS}bps. **Candidate test = (positive in a majority of years) "
        "AND (bootstrap 5th-pct expectancy > 0).** Internal significance vs "
        "resampled luck — not a live guarantee; =F daily data caveat stands.",
        "",
    ]
    for ticker, label, strat, params in SURVIVORS:
        csv = find_csv(ticker)
        if csv is None:
            csv = out_dir / "data" / f"{ticker.replace('=', '_')}.csv"
            if fetch_daily(ticker, csv) < 250:
                lines += [f"## {label} / {strat}", "- skipped (no data)", ""]
                continue
        rows = emit_trades(strat, csv, params)
        net = np.array([float(r.get("net_r", 0.0)) for r in rows], dtype=float)
        years = by_year(rows)
        boot = block_bootstrap(net)
        pos_years = sum(1 for _, _, r in years if r > 0)
        verdict_year = pos_years >= (len(years) + 1) // 2 if years else False
        verdict_boot = isinstance(boot.get("exp_p05"), float) and boot["exp_p05"] > 0
        passes = verdict_year and verdict_boot
        results[f"{label}/{strat}"] = {"params": params, "by_year": years,
                                       "bootstrap": boot, "passes": passes}

        lines.append(f"## {label} / {strat} — `{pstr(params)}`")
        lines.append("")
        if "error" not in boot:
            lines.append(f"- **n={boot['n_trades']}**, total {boot['observed_total_r']:+.1f}R, "
                         f"observed exp {boot['observed_exp_r']:+.3f}R")
            lines.append(f"- bootstrap expectancy: p05 **{boot['exp_p05']:+.3f}** / "
                         f"p50 {boot['exp_p50']:+.3f} / p95 {boot['exp_p95']:+.3f}; "
                         f"P(total>0)={boot['frac_total_positive']:.1%}")
        else:
            lines.append(f"- bootstrap: {boot['error']}")
        pos = sum(1 for _, _, r in years if r > 0)
        lines.append(f"- by-year ({pos}/{len(years)} positive): "
                     + ", ".join(f"{y}:{r:+.1f}(n{n})" for y, n, r in years))
        lines.append(f"- **VERDICT: {'PASS — genuine candidate' if passes else 'FAIL — do not advance on this data'}** "
                     f"(years {'ok' if verdict_year else 'no'}, bootstrap {'ok' if verdict_boot else 'no'})")
        lines.append("")

    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
    summary = "\n".join(lines) + "\n"
    (out_dir / "SUMMARY.md").write_text(summary)
    print("\n" + "=" * 72 + "\n" + summary + "=" * 72)
    print(f"[ws-a-s3] wrote {out_dir}/results.json + SUMMARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
