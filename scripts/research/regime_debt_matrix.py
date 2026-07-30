#!/usr/bin/env python3
"""Run the per-(trend-regime, direction) net-R matrix for the regime-coverage
debt roster — the rec #5 follow-up for the strategies the sandbox can't reach.

For each `coverage_debt` strategy (config/regime_coverage_exemptions.yaml) it:
  1. classifies the harness (Donchian trend -> backtest_trend.py; pullback ->
     backtest_pullback.py; TTM-style BB-inside-KC squeeze -> backtest_squeeze.py)
     and extracts the EXACT live params from config/strategies.yaml,
  2. resolves the candle feed for the symbol — Binance-vision for `*USDT` crypto,
     Yahoo (yfinance) for equities/ETFs, and Yahoo continuous futures for
     MES/MGC/MHG (ES=F/GC=F/HG=F, mirroring the dashboard `_yf_ticker`),
  3. runs the harness `--emit-trades` then `regime_tag_emitted.py` and collects
     the matrix JSON, tagged with a **fidelity** flag.

**Fidelity.** A strategy is `faithful` when the base harness models every lever
its config declares. The pullback harness exposes the vol-skip / stale-exit /
trail-vol lever flags, so those variants run faithfully. The trend harness models
the **stale-exit** lever (`--stale-exit-bars`/`--stale-exit-below-r`, ported for
rec #5 so the debt matrix can re-measure a Donchian variant with its declared exit
lever ON) — so a Donchian variant carrying ONLY stale-exit runs faithfully now. A
variant still carrying trail-decay / vol-skip / giveback levers the trend harness
doesn't yet expose stays `approximate` (base geometry only, those levers omitted —
labelled, never hidden). An `exit_head_model` lever is never replayable offline ->
always `approximate`.

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

from typing import Optional

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
# Trend lever config-key -> harness flag (levers the trend harness DOES model,
# so a trend strategy carrying ONLY these is faithful, not approximate). The
# stale-exit lever was ported into scripts/backtest_trend.py as the rec #5
# follow-up so trend_donchian_sol's chop-long cell can be re-measured with its
# declared exit lever ON (BL-20260717-REGIME-COVERAGE-DEBT). exit_head_* stay in
# _UNREPLAYABLE — an ML exit head can never be replayed offline.
_TREND_LEVER_FLAG = {
    "stale_exit_bars": "--stale-exit-bars", "stale_exit_below_r": "--stale-exit-below-r",
}
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
# Squeeze (TTM-style BB-inside-KC) lever config-key -> harness flag. The squeeze
# harness (scripts/backtest_squeeze.py) is the SAME harness that validated the
# strategy in docs/audits/squeeze-breakout-complement-2026-05-24.md — it was simply
# never wired into classify(), so squeeze_breakout_4h fell through to
# "unclassifiable" and its FOUR live regime cells could not be re-audited at all
# (BL-20260730-SQUEEZE-NO-HARNESS, found by the 2026-07-30 authored-cell re-audit).
_SQZ_LEVER_FLAG = {
    "stale_exit_bars": "--stale-exit-bars", "stale_exit_below_r": "--stale-exit-below-r",
    "giveback_min_mfe_r": "--giveback-min-mfe-r", "giveback_r": "--giveback-r",
    "timeout_bars": "--timeout-bars", "cooldown_bars": "--cooldown-bars",
}
_SQZ_PLAIN = {"model", "signal_prefixes", "enabled", "execution", "timeframe", "symbols",
              "bb_period", "bb_std", "kc_mult", "atr_period", "atr_stop_mult",
              "trail_mult", "min_confidence", "shadow_model_ids", "description"}
# `tp_r` is DELIBERATELY not in _SQZ_PLAIN. scripts/backtest_squeeze.py has no
# --tp-r flag: it models the Chandelier trail as the sole profit-exit, which is
# what src/units/strategies/squeeze_breakout_4h.py itself documents ("No fixed
# profit target — the trail is the sole profit-exit; ``tp`` is [a formality]").
# So the omission is harmless ONLY while tp_r is far enough out that it cannot
# bind before the trail fires. That is a CHECKED bound, not an assumption: below
# the threshold `tp_r` is reported as an omitted lever and the row degrades to
# `approximate`, which correctly blocks cell authoring.
#
# 20R is chosen as comfortably beyond any plausible 3.5-ATR-trail exit while
# still failing loudly if someone sets a real target (e.g. tp_r: 3). The live
# config is tp_r: 50.0. The STRONGER form of this check is empirical — verify no
# emitted trade's MFE ever reached tp_r on the sample — which needs a post-run
# fidelity adjustment in both callers: BL-20260730-SQZ-TPR-EMPIRICAL-CHECK.
_SQZ_TP_R_NONBINDING = 20.0
# Levers no offline harness can replay.
_UNREPLAYABLE = {"exit_head_model", "exit_head_threshold", "exit_head_action"}


def classify(cfg: dict) -> str | None:
    if "donchian" in cfg:
        return "trend"
    if "trend_lookback" in cfg or "pullback_frac" in cfg:
        return "pullback"
    # TTM-style squeeze: Bollinger Bands contracting inside the Keltner Channels.
    # Checked AFTER trend/pullback so a strategy carrying both shapes keeps its
    # existing harness (no silent re-routing of an already-measured strategy).
    if "kc_mult" in cfg and "bb_period" in cfg:
        return "squeeze"
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
        # Force Binance-vision: fetch_backtest_candles defaults to source=auto,
        # which tries Bybit first — Bybit geoblocks US GH-runners (403), wasting
        # ~14s of retries per symbol before it falls back. Skip straight to the
        # feed that actually works off-VM.
        subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts/ops/fetch_backtest_candles.py"),
             "--symbol", feed["ticker"], "--interval", feed["interval"],
             "--days", str(days), "--output", out, "--source", "binance_vision"],
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


def roundtrip_fee_bps(symbol: str) -> float:
    """Venue-appropriate round-trip fee in bps for *symbol* — the research-harness
    counterpart of the live close path's resolver.

    Delegates to ``core.profile_loader.roundtrip_fee_bps_for``, which returns
    ``0.0`` for a commission-free venue (US equity/ETF on Alpaca) and ``None``
    meaning "no venue-specific rate — use the estimator default" for
    crypto/futures/fx. ``None`` (and any resolver failure) falls back to
    ``trade_costs.DEFAULT_FEE_BPS_ROUNDTRIP`` so the default lives in exactly one
    place and is never duplicated here.

    Why this exists (BL-20260730-RESEARCH-VENUE-FEE): this function used to be the
    literal constant ``7.5`` for EVERY symbol, so all 14 commission-free
    ``(alpaca, spot)`` instruments (SPY QQQ TQQQ QLD GLD IWM TLT IEF SLV USO GDX
    SPLG IAUM SCHA) were charged a crypto-perp fee — a ~25x over-charge worth
    ~0.04-0.12 R/trade. Over-charging can only make a strategy look WORSE, so the
    bug's signature is **false OFF cells** (gating a leg that is actually fine),
    never a fabricated edge. #7930 fixed the identical bug in the live close-path
    writer (``database._record_trade_cost_estimate``); the research harness that
    sources Tier-3 regime cells was missed, which is why the equity/ETF matrix
    (#7918) and its walk-forward verdicts (#7920-#7924) need re-running.
    """
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        from src.core.profile_loader import roundtrip_fee_bps_for
        from src.runtime.trade_costs import DEFAULT_FEE_BPS_ROUNDTRIP
    except Exception as exc:  # noqa: BLE001
        # Loud, not silent: a resolver miss would quietly restore the 25x
        # over-charge this function exists to remove. Fall back to the documented
        # default and SAY SO, so a degraded run is visible in the log.
        print(f"[fee] resolver unavailable ({exc}); falling back to 7.5 bps for {symbol}",
              file=sys.stderr)
        return 7.5
    resolved = roundtrip_fee_bps_for(symbol)
    return float(DEFAULT_FEE_BPS_ROUNDTRIP if resolved is None else resolved)


def build_harness_cmd(name: str, cfg: dict, harness: str, csv: str, resample: str,
                      emit: str, jout: str) -> tuple[list[str], bool, list[str]]:
    """Return (argv, faithful, omitted_levers)."""
    py = sys.executable
    symbol = cfg["symbols"][0]
    common = ["--data", csv, "--symbol", symbol, "--resample", resample,
              "--atr-period", str(cfg.get("atr_period", 14)),
              "--atr-stop-mult", str(cfg.get("atr_stop_mult", 2.5)),
              "--trail-mult", str(cfg.get("trail_mult", 5.0)),
              "--min-confidence", str(cfg.get("min_confidence", 0.0)),
              "--fee-bps-roundtrip", str(roundtrip_fee_bps(symbol)),
              "--emit-trades", emit, "--json", jout]
    # ADX gating is supported by the trend + pullback harnesses but NOT by
    # scripts/backtest_squeeze.py (it has no --adx-* flags). Kept OUT of `common`
    # so an adx-carrying squeeze strategy degrades honestly to `approximate`
    # instead of crashing the subprocess with "unrecognized arguments" — a
    # harness failure reads as a fetch/harness error, which would misattribute a
    # missing capability as a broken run.
    adx_flags: list[str] = []
    if cfg.get("adx_min") is not None:
        adx_flags += ["--adx-min", str(cfg["adx_min"])]
    if cfg.get("adx_max") is not None:
        adx_flags += ["--adx-max", str(cfg["adx_max"])]
    if harness != "squeeze":
        common += adx_flags
    omitted: list[str] = []
    if harness == "trend":
        argv = [py, os.path.join(REPO, "scripts/backtest_trend.py"),
                "--donchian", str(cfg.get("donchian", 20))] + common
        if cfg.get("long_only"):
            argv.append("--long-only")
        # pass every trend lever the harness can now model (stale-exit)
        for k, flag in _TREND_LEVER_FLAG.items():
            if cfg.get(k) is not None:
                argv += [flag, str(cfg[k])]
        omitted = sorted(k for k in cfg
                         if k not in _TREND_PLAIN and k not in _TREND_LEVER_FLAG)
        faithful = not omitted
    elif harness == "squeeze":
        argv = [py, os.path.join(REPO, "scripts/backtest_squeeze.py"),
                "--bb-period", str(cfg.get("bb_period", 20)),
                "--bb-std", str(cfg.get("bb_std", 2.0)),
                "--kc-mult", str(cfg.get("kc_mult", 1.0))] + common
        for k, flag in _SQZ_LEVER_FLAG.items():
            if cfg.get(k) is not None:
                argv += [flag, str(cfg[k])]
        omitted = sorted(k for k in cfg
                         if k not in _SQZ_PLAIN and k not in _SQZ_LEVER_FLAG
                         and k != "tp_r")
        # tp_r counts as omitted only when it is near enough to actually bind —
        # see _SQZ_TP_R_NONBINDING. A missing tp_r is the harness's own default
        # (trail-only), so it is not an omission either.
        tp_r = cfg.get("tp_r")
        if tp_r is not None and float(tp_r) < _SQZ_TP_R_NONBINDING:
            omitted = sorted(set(omitted) | {"tp_r"})
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
        row["error"] = "unclassifiable (no donchian/pullback/squeeze params or no symbol/timeframe)"
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
    # Record WHICH fee graded this row, so a reader can never again have to guess
    # whether a verdict was produced under the venue-blind 7.5-bps default
    # (BL-20260730-RESEARCH-VENUE-FEE). 0.0 = commission-free venue.
    row["fee_bps_roundtrip"] = roundtrip_fee_bps(cfg["symbols"][0])
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


def resolve_strategy(name: str) -> Optional[dict]:
    """Config for ANY declared strategy, whether or not it is in `coverage_debt`.

    Why this exists (BL-20260730-REGIME-CELL-UNAUDITABLE): the roster above is the
    **debt list**, and authoring a cell PAYS THE STRATEGY DOWN OUT of `coverage_debt`
    — so the moment a Tier-3 cell is authored, both re-grade tools stop being able to
    measure that strategy at all (`regime_cell_walkforward.run_cell` returned the
    literal error "not in coverage_debt roster"). The tooling could grade candidates
    but never RE-AUDIT a decision it had already made.

    That bit immediately: the 2026-07-30 corrected-cost re-run could not re-measure
    `gld_pullback_1h` — the one live Tier-3 cell whose evidence the fee fix most
    called into question — because authoring that cell had removed it from the
    roster. A blind spot exactly where the live gate is.

    So an explicitly-named strategy (`--only`, or a walk-forward request) resolves
    against `config/strategies.yaml` directly. The DEFAULT roster is unchanged: still
    the debt list, so a bare full-roster run means the same thing it always did.
    """
    strat = yaml.safe_load(
        open(os.path.join(REPO, "config/strategies.yaml"))).get("strategies", {}) or {}
    cfg = strat.get(name)
    return cfg if isinstance(cfg, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="run only these strategies (may be outside coverage_debt — an already-celled strategy stays auditable)")
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
        # An explicitly-named strategy may live outside `coverage_debt` (an
        # already-celled one, e.g. gld_pullback_1h) — resolve it so an authored
        # cell stays auditable. BL-20260730-REGIME-CELL-UNAUDITABLE.
        cfg = roster.get(n) or resolve_strategy(n)
        if cfg is None:
            results.append({"strategy": n, "error": "not declared in strategies.yaml"})
            continue
        sym = (cfg.get("symbols") or [None])[0]
        if args.crypto_only and not (sym or "").upper().endswith("USDT"):
            results.append({"strategy": n, "symbol": sym, "skipped": "non-crypto (crypto-only)"})
            continue
        results.append(run_one(n, cfg, args.workdir, args.days))

    # COVERAGE DECLARATION (BL-20260730-REGIME-CELL-UNAUDITABLE, and the binding
    # "Green is not evidence" rule §3). This run's roster is a WORK QUEUE — the
    # coverage_debt list — not the population of live strategies. Authoring a cell
    # PAYS THE STRATEGY DOWN OUT of that queue, so the queue systematically excludes
    # exactly the decisions most in need of re-checking: the 2026-07-30 corrected-cost
    # re-grade reported "34 rows, 0 errored, 0 skipped" while silently omitting
    # gld_pullback_1h, the one live Tier-3 cell it existed to re-check. Emitting the
    # population + the excluded set means "34 rows" can never again be read as "the
    # whole audit".
    try:
        strat_all = yaml.safe_load(
            open(os.path.join(REPO, "config/strategies.yaml"))).get("strategies", {}) or {}
        live = {k for k, v in strat_all.items()
                if isinstance(v, dict) and v.get("execution", "live") != "shadow"}
        covered = {r.get("strategy") for r in results}
        coverage = {
            "roster_kind": "coverage_debt (a WORK QUEUE, not the live population)",
            "declared_live_strategies": len(live),
            "covered": len(covered),
            "not_covered": sorted(live - covered),
            "warning": ("Strategies absent here are NOT cleared — they were never "
                        "measured by this run. An ALREADY-CELLED strategy is absent "
                        "precisely because a cell was authored for it; re-audit those "
                        "explicitly with --only <name>."),
        }
    except Exception as exc:  # noqa: BLE001
        coverage = {"error": f"could not compute coverage: {type(exc).__name__}: {exc}"}

    payload = {"count": len(results), "coverage": coverage, "results": results}
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
