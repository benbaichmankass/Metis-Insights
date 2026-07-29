#!/usr/bin/env python3
"""Run the per-(trend-regime, direction) net-R matrix for the regime-coverage
debt roster — the rec #5 follow-up for the strategies the sandbox can't reach.

For each `coverage_debt` strategy (config/regime_coverage_exemptions.yaml) it:
  1. classifies the harness (Donchian trend -> backtest_trend.py; pullback ->
     backtest_pullback.py) and extracts the EXACT live params from
     config/strategies.yaml,
  2. resolves the candle feed for the symbol — Binance-vision for `*USDT` crypto,
     Yahoo (yfinance) for equities/ETFs, and Yahoo continuous futures for
     MES/MGC/MHG (ES=F/GC=F/HG=F, mirroring the dashboard `_yf_ticker`),
  3. runs the harness `--emit-trades` then `regime_tag_emitted.py` and collects
     the matrix JSON, tagged with a **fidelity** flag.

**Fidelity.** A strategy is `faithful` when the base harness models every lever
its config declares. The pullback harness exposes the vol-skip / stale-exit /
trail-vol lever flags, so those variants run faithfully. The trend harness does
NOT, so a Donchian variant carrying stale-exit / exit-head levers is
`approximate` (base geometry only, levers omitted — labelled, never hidden). An
`exit_head_model` lever is never replayable offline -> always `approximate`.

Yahoo needs network the sandbox firewalls, so this is built to run on a free
GitHub-hosted runner (see .github/workflows/regime-debt-matrix.yml). The crypto
path is exercisable in-sandbox for testing.

Usage:
  python scripts/research/regime_debt_matrix.py --only trend_donchian_eth_4h --json
  python scripts/research/regime_debt_matrix.py --crypto-only --workdir /tmp/rdm --json
  python scripts/research/regime_debt_matrix.py --json > matrix.json   # full roster
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- feed resolution -------------------------------------------------------
# Yahoo tickers for the symbols that need translating (mirror dashboard _yf_ticker).
_YF_TICKER = {"MES": "ES=F", "MGC": "GC=F", "MHG": "HG=F", "XAUUSD": "GC=F"}
# Bybit interval code fetch_backtest_candles speaks, per timeframe.
_TF_TO_BYBIT_INT = {"1h": "60", "2h": "120", "4h": "240", "1d": "D"}
# Yahoo base interval to fetch per timeframe (Yahoo has no 2h/4h -> fetch 60m + resample).
_TF_TO_YF_INT = {"1h": "60m", "2h": "60m", "4h": "60m", "1d": "1d"}

# Plain trend param keys the base harness fully models; anything else on a
# Donchian strategy is an unmodelled lever -> approximate.
_TREND_PLAIN = {"model", "signal_prefixes", "enabled", "execution", "timeframe",
                "symbols", "donchian", "atr_period", "atr_stop_mult", "trail_mult",
                "tp_r", "min_confidence", "long_only", "adx_min", "adx_max",
                "adx_period", "shadow_model_ids", "description"}
# Pullback lever config-key -> harness flag (these the pullback harness DOES model).
_PB_LEVER_FLAG = {
    "stale_exit_bars": "--stale-exit-bars", "stale_exit_below_r": "--stale-exit-below-r",
    "vol_skip_below_pctl": "--vol-skip-below-pctl", "vol_skip_above_pctl": "--vol-skip-above-pctl",
    "trail_vol_below_pctl": "--trail-vol-below-pctl", "trail_vol_above_pctl": "--trail-vol-above-pctl",
    "trail_vol_tight_mult": "--trail-vol-tight-mult", "vol_pctl_window": "--vol-pctl-window",
}
_PB_PLAIN = {"model", "signal_prefixes", "enabled", "execution", "timeframe", "symbols",
             "trend_lookback", "pullback_lookback", "pullback_frac", "atr_period",
             "atr_stop_mult", "trail_mult", "tp_r", "min_confidence", "adx_min",
             "adx_max", "adx_period", "shadow_model_ids", "description"}
# Levers no offline harness can replay.
_UNREPLAYABLE = {"exit_head_model", "exit_head_threshold", "exit_head_action"}


def classify(cfg: dict) -> str | None:
    if "donchian" in cfg:
        return "trend"
    if "trend_lookback" in cfg or "pullback_frac" in cfg:
        return "pullback"
    return None


def resolve_feed(symbol: str, timeframe: str) -> dict:
    if symbol.upper().endswith("USDT"):
        return {"source": "binance", "ticker": symbol,
                "interval": _TF_TO_BYBIT_INT.get(timeframe, "D"), "resample": timeframe}
    return {"source": "yahoo", "ticker": _YF_TICKER.get(symbol.upper(), symbol),
            "interval": _TF_TO_YF_INT.get(timeframe, "1d"), "resample": timeframe}


def _fetch_csv(feed: dict, days: int, out: str) -> None:
    """Populate `out` with a timestamp,open,high,low,close,volume CSV."""
    if feed["source"] == "binance":
        subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts/ops/fetch_backtest_candles.py"),
             "--symbol", feed["ticker"], "--interval", feed["interval"],
             "--days", str(days), "--output", out],
            check=True, cwd=REPO)
        return
    # Yahoo via yfinance -> write the same CSV shape the harnesses read.
    import pandas as pd
    import yfinance as yf
    period = f"{min(days, 720)}d" if feed["interval"].endswith("m") else f"{days}d"
    df = yf.download(feed["ticker"], period=period, interval=feed["interval"],
                     auto_adjust=False, progress=False, threads=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned no rows for {feed['ticker']}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower).reset_index()
    # first column is the datetime index (Datetime/Date) -> canonical `timestamp`
    df = df.rename(columns={df.columns[0]: "timestamp"})
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    df[cols].to_csv(out, index=False)


def build_harness_cmd(name: str, cfg: dict, harness: str, csv: str, resample: str,
                      emit: str, jout: str) -> tuple[list[str], bool, list[str]]:
    """Return (argv, faithful, omitted_levers)."""
    py = sys.executable
    common = ["--data", csv, "--symbol", cfg["symbols"][0], "--resample", resample,
              "--atr-period", str(cfg.get("atr_period", 14)),
              "--atr-stop-mult", str(cfg.get("atr_stop_mult", 2.5)),
              "--trail-mult", str(cfg.get("trail_mult", 5.0)),
              "--min-confidence", str(cfg.get("min_confidence", 0.0)),
              "--fee-bps-roundtrip", "7.5", "--emit-trades", emit, "--json", jout]
    if cfg.get("adx_min") is not None:
        common += ["--adx-min", str(cfg["adx_min"])]
    if cfg.get("adx_max") is not None:
        common += ["--adx-max", str(cfg["adx_max"])]
    omitted: list[str] = []
    if harness == "trend":
        argv = [py, os.path.join(REPO, "scripts/backtest_trend.py"),
                "--donchian", str(cfg.get("donchian", 20))] + common
        if cfg.get("long_only"):
            argv.append("--long-only")
        omitted = sorted(k for k in cfg if k not in _TREND_PLAIN)
        faithful = not omitted
    else:
        argv = [py, os.path.join(REPO, "scripts/backtest_pullback.py"),
                "--trend-lookback", str(cfg.get("trend_lookback", 40)),
                "--pullback-lookback", str(cfg.get("pullback_lookback", 10)),
                "--pullback-frac", str(cfg.get("pullback_frac", 0.5))] + common
        # pass every lever the pullback harness can model
        for k, flag in _PB_LEVER_FLAG.items():
            if cfg.get(k) is not None:
                argv += [flag, str(cfg[k])]
        omitted = sorted(k for k in cfg
                         if k not in _PB_PLAIN and k not in _PB_LEVER_FLAG)
        faithful = not omitted
    return argv, faithful, omitted


def run_one(name: str, cfg: dict, workdir: str, days: int) -> dict:
    harness = classify(cfg)
    sym = (cfg.get("symbols") or [None])[0]
    tf = cfg.get("timeframe")
    row: dict = {"strategy": name, "symbol": sym, "timeframe": tf, "harness": harness}
    if harness is None or not sym or not tf:
        row["error"] = "unclassifiable (no donchian/pullback params or no symbol/timeframe)"
        return row
    unreplayable = sorted(k for k in cfg if k in _UNREPLAYABLE)
    feed = resolve_feed(sym, tf)
    row["feed"] = feed
    csv = os.path.join(workdir, f"{name}__data.csv")
    emit = os.path.join(workdir, f"{name}__trades.jsonl")
    jout = os.path.join(workdir, f"{name}__bt.json")
    try:
        _fetch_csv(feed, days, csv)
    except Exception as e:  # noqa: BLE001
        row["error"] = f"fetch failed: {type(e).__name__}: {e}"
        return row
    argv, faithful, omitted = build_harness_cmd(name, cfg, harness, csv,
                                                feed["resample"], emit, jout)
    if unreplayable:
        faithful = False
        omitted = sorted(set(omitted) | set(unreplayable))
    row["fidelity"] = "faithful" if faithful else "approximate"
    row["omitted_levers"] = omitted
    try:
        subprocess.run(argv, check=True, cwd=REPO,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        row["error"] = f"harness failed: {(e.stderr or b'').decode()[-300:]}"
        return row
    try:
        out = subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts/research/regime_tag_emitted.py"),
             "--trades", emit, "--data", csv, "--resample", feed["resample"],
             "--label", name, "--json"],
            check=True, cwd=REPO, capture_output=True)
        row["matrix"] = json.loads(out.stdout.decode())
    except Exception as e:  # noqa: BLE001
        row["error"] = f"regime-tag failed: {type(e).__name__}: {e}"
    return row


def load_roster() -> dict:
    ex = yaml.safe_load(open(os.path.join(REPO, "config/regime_coverage_exemptions.yaml")))
    strat = yaml.safe_load(open(os.path.join(REPO, "config/strategies.yaml"))).get("strategies", {})
    return {n: strat.get(n, {}) for n in (ex.get("coverage_debt") or {})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="run only these debt strategies")
    ap.add_argument("--crypto-only", action="store_true", help="skip Yahoo feeds (sandbox-testable)")
    ap.add_argument("--workdir", default="/tmp/regime_debt_matrix")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.workdir, exist_ok=True)
    roster = load_roster()
    names = args.only or sorted(roster)
    results = []
    for n in names:
        cfg = roster.get(n)
        if cfg is None:
            results.append({"strategy": n, "error": "not in coverage_debt"})
            continue
        sym = (cfg.get("symbols") or [None])[0]
        if args.crypto_only and not (sym or "").upper().endswith("USDT"):
            results.append({"strategy": n, "symbol": sym, "skipped": "non-crypto (crypto-only)"})
            continue
        results.append(run_one(n, cfg, args.workdir, args.days))
    payload = {"count": len(results), "results": results}
    if args.json:
        print(json.dumps(payload))
    else:
        for r in results:
            m = (r.get("matrix") or {}).get("by_regime", {})
            tot = (r.get("matrix") or {}).get("totals", {})
            print(f"{r['strategy']:26s} {r.get('fidelity','-'):11s} "
                  f"{r.get('error') or r.get('skipped') or ''}")
            for reg, s in m.items():
                print(f"    {reg:13s} net_r={s['net_r']:>8} long={s['long_r']:>8}(n{s['long_n']}) "
                      f"short={s['short_r']:>8}(n{s['short_n']})")
            if tot:
                print(f"    TOTAL net_r={tot.get('net_r')} long={tot.get('long_r')} short={tot.get('short_r')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
