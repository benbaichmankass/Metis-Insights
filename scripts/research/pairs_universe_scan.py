"""Cointegration pairs UNIVERSE-SCAN (M22 wave-2 D5).

The D2 finding shipped 4 hand-picked market-neutral cointegration pairs
(SOL/BTC, BNB/BTC, ETH/BTC, SOL/ETH) live on paper. The edge is statistical, not
symbol-specific — so it almost certainly exists across the broader liquid-perp
universe. This driver **scans every candidate pair** from a directory of candle
CSVs, screens each for cointegration stability, backtests it net-of-fee both
full-sample and out-of-sample, ranks by the operator's capital-efficiency metric
(``net_r_per_pos_day``), and emits a **shortlist** of robust, low-leg-overlap
pairs to consider adding to the sleeve.

Pure REUSE — it composes the already-merged, already-validated pieces:
  * ``scripts/backtest_pairs.py::run_backtest`` (the parity-verified engine),
  * ``scripts/research/cointegration_stability.py::analyze`` (half-life /
    valid-fraction / beta-drift screen).
It never touches the live path; it writes a ranked JSON + markdown only. Any
add-to-sleeve decision is a separate Tier-3 ``config/pairs.yaml`` proposal.

Run (on the trainer, where the candle CSVs live)::

    python scripts/research/pairs_universe_scan.py \
        --data-dir runtime_state/candles --resample 1h --oos-start 2025-01-01 \
        --json /tmp/pairs_scan.json --md /tmp/pairs_scan.md

Self-test (synthetic cointegrated triple; no data files needed)::

    python scripts/research/pairs_universe_scan.py --self-test
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_pairs as bp          # noqa: E402
import cointegration_stability as cs  # noqa: E402

# The 4 pairs already live on bybit_1 (canonical A/B ordering, symbols upper).
LIVE_PAIRS = {("SOLUSDT", "BTCUSDT"), ("BNBUSDT", "BTCUSDT"),
              ("ETHUSDT", "BTCUSDT"), ("SOLUSDT", "ETHUSDT")}


def _sym_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0].upper()


def _discover_symbols(data_dir: str) -> Dict[str, str]:
    """{SYMBOL: path} for every .csv/.parquet in data_dir."""
    out: Dict[str, str] = {}
    for fn in sorted(os.listdir(data_dir)):
        if fn.endswith((".csv", ".parquet")):
            out[_sym_from_path(fn)] = os.path.join(data_dir, fn)
    return out


def _aligned(path_a: str, path_b: str, resample: str) -> pd.DataFrame:
    a = bp._resample(bp._load_candles(path_a), resample)
    b = bp._resample(bp._load_candles(path_b), resample)
    return bp._align(a, b)


def _oos_slice(m: pd.DataFrame, oos_start: Optional[str]) -> pd.DataFrame:
    if not oos_start or "timestamp" not in m:
        return m
    ts = pd.to_datetime(oos_start, utc=True)
    return m[m["timestamp"] >= ts].reset_index(drop=True)


def _n_trades(metrics: Dict[str, Any]) -> int:
    """run_backtest reports trades_long/trades_short (no aggregate 'trades' key)."""
    return int((metrics.get("trades_long") or 0) + (metrics.get("trades_short") or 0))


def _adf_tstat(spread: np.ndarray) -> Optional[float]:
    """Augmented Dickey-Fuller t-statistic (lag-0) on the spread — the STATIONARITY
    test that separates a genuinely cointegrated pair from two independent random
    walks whose spread only *looks* mean-reverting by chance. Regress
    Δs_t = α + β·s_{t-1} + ε; the t-stat of β is the DF statistic. Cointegrated
    (stationary) ⇒ β significantly < 0 ⇒ very negative t (below ≈ −2.86 at 5%).
    A unit-root spread ⇒ β ≈ 0 ⇒ t near 0. Returns None on degenerate input."""
    s = np.asarray(spread, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < 40:
        return None
    y = np.diff(s)                       # Δs_t
    x = s[:-1]                           # s_{t-1}
    X = np.column_stack([np.ones_like(x), x])
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        dof = len(y) - 2
        if dof <= 0:
            return None
        s2 = float(resid @ resid) / dof
        xtx_inv = np.linalg.inv(X.T @ X)
        se_slope = float(np.sqrt(s2 * xtx_inv[1, 1]))
        if se_slope <= 0:
            return None
        return float(beta[1] / se_slope)
    except (np.linalg.LinAlgError, ValueError):
        return None


def _bt(m: pd.DataFrame, args) -> Dict[str, Any]:
    return bp.run_backtest(
        m, lookback=args.lookback, entry_z=args.entry_z, exit_z=args.exit_z,
        stop_z=args.stop_z, max_hold_bars=args.max_hold_bars,
        cooldown_bars=args.cooldown_bars, hedge_beta=args.hedge_beta,
        timeframe=args.resample, pair="scan")


def _score_pair(sym_a: str, sym_b: str, path_a: str, path_b: str, args) -> Dict[str, Any]:
    """Screen + full + OOS backtest for one candidate pair. Never raises."""
    rec: Dict[str, Any] = {"pair": f"{sym_a}/{sym_b}", "symbol_a": sym_a, "symbol_b": sym_b,
                           "is_live": (sym_a, sym_b) in LIVE_PAIRS or (sym_b, sym_a) in LIVE_PAIRS}
    try:
        m = _aligned(path_a, path_b, args.resample)
        rec["n_bars"] = int(len(m))
        if len(m) < args.min_bars:
            rec["skipped"] = f"insufficient overlap ({len(m)} < {args.min_bars})"
            return rec
        stab = cs.analyze(path_a, path_b, resample=args.resample, lookback=args.lookback,
                          window=args.stability_window, z_cap=4.0, hl_cap_bars=200.0)
        rec["half_life_hours"] = stab.get("global_half_life_hours")
        rec["rolling_hl_valid_pct"] = stab.get("rolling_hl_valid_pct")
        rec["hedge_beta_drift"] = stab.get("hedge_beta_drift")
        # Engle-Granger cointegration test: step 1 fits ONE full-sample
        # cointegrating vector (OLS of logA on logB), step 2 runs ADF on the
        # residual. The gate MUST use this FIXED beta, not the engine's rolling
        # beta — a rolling regression re-fits every bar and makes ANY pair's
        # residual spuriously stationary, so it can't discriminate cointegration.
        la = np.log(m["close_a"].to_numpy())
        lb = np.log(m["close_b"].to_numpy())
        Xb = np.column_stack([np.ones_like(lb), lb])
        coef, *_ = np.linalg.lstsq(Xb, la, rcond=None)   # la ≈ alpha + beta*lb
        rec["eg_beta"] = round(float(coef[1]), 4)
        rec["adf_tstat"] = _adf_tstat(la - Xb @ coef)     # ADF on the EG residual
        full = _bt(m, args)
        rec["full_net_r"] = full.get("net_total_r")
        rec["full_expectancy_r"] = full.get("net_expectancy_r")
        rec["full_win_pct"] = full.get("win_rate_pct")
        rec["full_max_dd_r"] = full.get("max_drawdown_r")
        rec["full_trades"] = _n_trades(full)
        rec["full_net_r_per_pos_day"] = full.get("net_r_per_pos_day")
        oosm = _oos_slice(m, args.oos_start)
        if len(oosm) >= args.lookback + 5:
            oos = _bt(oosm, args)
            rec["oos_net_r"] = oos.get("net_total_r")
            rec["oos_expectancy_r"] = oos.get("net_expectancy_r")
            rec["oos_win_pct"] = oos.get("win_rate_pct")
            rec["oos_max_dd_r"] = oos.get("max_drawdown_r")
            rec["oos_trades"] = _n_trades(oos)
            rec["oos_net_r_per_pos_day"] = oos.get("net_r_per_pos_day")
            # COINTEGRATION PERSISTENCE: apply the FULL-sample cointegrating
            # vector to the OOS slice and ADF that residual. A genuine pair stays
            # stationary under the same vector OOS; a spurious in-sample fit
            # breaks (ADF rises toward 0). This is the key false-positive filter.
            la_o = np.log(oosm["close_a"].to_numpy())
            lb_o = np.log(oosm["close_b"].to_numpy())
            rec["oos_adf_tstat"] = _adf_tstat(
                la_o - np.column_stack([np.ones_like(lb_o), lb_o]) @ coef)
        # Robustness gate (mirrors the D2 validation): OOS net-positive with real
        # sample, cointegration stable (valid-fraction high, half-life sane band),
        # positive full-sample expectancy. Half-life band 2h..72h @ the resample.
        hl = rec.get("half_life_hours")
        adf = rec.get("adf_tstat")
        oos_adf = rec.get("oos_adf_tstat")
        rec["oos_robust"] = bool(
            adf is not None and adf <= args.adf_max_tstat        # cointegrated in-sample
            and oos_adf is not None and oos_adf <= args.adf_max_tstat  # AND persists OOS
            and (rec.get("oos_expectancy_r") or -1) > 0
            and (rec.get("oos_trades") or 0) >= args.min_trades
            and (rec.get("full_expectancy_r") or -1) > 0
            and (rec.get("rolling_hl_valid_pct") or 0) >= args.min_valid_pct
            and hl is not None and args.hl_min_hours <= hl <= args.hl_max_hours)
    except Exception as exc:  # noqa: BLE001 — one bad pair never kills the scan
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def _shortlist(ranked: List[Dict[str, Any]], max_leg_uses: int) -> List[str]:
    """Greedy low-leg-overlap pick from the OOS-robust, not-already-live pairs
    (best capital-efficiency first), capping how many times any one symbol is a
    leg so the sleeve stays diversified rather than all-BTC."""
    uses: Dict[str, int] = {}
    picks: List[str] = []
    for r in ranked:
        if r.get("is_live") or not r.get("oos_robust"):
            continue
        a, b = r["symbol_a"], r["symbol_b"]
        if uses.get(a, 0) >= max_leg_uses or uses.get(b, 0) >= max_leg_uses:
            continue
        picks.append(r["pair"])
        uses[a] = uses.get(a, 0) + 1
        uses[b] = uses.get(b, 0) + 1
    return picks


def scan(symbols: Dict[str, str], args) -> Dict[str, Any]:
    scored: List[Dict[str, Any]] = []
    for sym_a, sym_b in itertools.combinations(sorted(symbols), 2):
        scored.append(_score_pair(sym_a, sym_b, symbols[sym_a], symbols[sym_b], args))
    # rank: OOS-robust first, then by OOS capital-efficiency (net_r_per_pos_day),
    # falling back to full-sample when OOS is absent.
    def _key(r: Dict[str, Any]) -> Tuple:
        eff = r.get("oos_net_r_per_pos_day")
        if eff is None:
            eff = r.get("full_net_r_per_pos_day")
        return (1 if r.get("oos_robust") else 0, eff if eff is not None else -1e9)
    ranked = sorted(scored, key=_key, reverse=True)
    return {
        "params": {k: getattr(args, k) for k in
                   ("resample", "lookback", "entry_z", "exit_z", "stop_z",
                    "max_hold_bars", "hedge_beta", "oos_start", "min_bars",
                    "min_trades", "min_valid_pct", "adf_max_tstat",
                    "hl_min_hours", "hl_max_hours")},
        "symbols": sorted(symbols),
        "n_candidates": len(scored),
        "n_oos_robust": sum(1 for r in scored if r.get("oos_robust")),
        "ranked": ranked,
        "recommend_add": _shortlist(ranked, args.max_leg_uses),
    }


def _to_md(res: Dict[str, Any]) -> str:
    rows = ["# Pairs universe-scan", "",
            f"- symbols: {', '.join(res['symbols'])}",
            f"- candidates: {res['n_candidates']} · OOS-robust: {res['n_oos_robust']}",
            f"- **recommend add (low leg-overlap):** {', '.join(res['recommend_add']) or '(none cleared the gate)'}",
            "", "| pair | live | ADF t | OOS ADF | HL h | OOS net_R | OOS exp | OOS win% | OOS eff | robust |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["ranked"]:
        if r.get("skipped") or r.get("error"):
            continue
        rows.append("| {pair} | {live} | {adf} | {oadf} | {hl} | {onr} | {oe} | {ow} | {eff} | {rob} |".format(
            pair=r["pair"], live="✓" if r.get("is_live") else "",
            adf=r.get("adf_tstat"), oadf=r.get("oos_adf_tstat"), hl=r.get("half_life_hours"),
            onr=r.get("oos_net_r"), oe=r.get("oos_expectancy_r"),
            ow=r.get("oos_win_pct"), eff=r.get("oos_net_r_per_pos_day"),
            rob="✅" if r.get("oos_robust") else ""))
    return "\n".join(rows) + "\n"


# --------------------------------------------------------------------------
# Self-test: a synthetic cointegrated TRIPLE. B is cointegrated with A (their
# log-spread is a mean-reverting OU process); C is an independent random walk.
# The scan must rank A/B (the only genuinely cointegrated, tradeable pair) as
# OOS-robust and above the A/C and B/C pairs.
# --------------------------------------------------------------------------

def _synth_csv(tmp: str, name: str, close: np.ndarray, start="2022-01-01") -> str:
    ts = pd.date_range(start, periods=len(close), freq="1h", tz="UTC")
    df = pd.DataFrame({"timestamp": ts, "open": close, "high": close * 1.001,
                       "low": close * 0.999, "close": close})
    path = os.path.join(tmp, f"{name}.csv")
    df.to_csv(path, index=False)
    return path


def _self_test() -> int:
    import tempfile
    rng = np.random.default_rng(7)
    n = 6000
    # A: a LARGE-amplitude shared random walk. B = A + a SMALL stationary OU
    # spread (so B is genuinely cointegrated with A, and the tiny stationary
    # component doesn't leak into B-vs-independent pairings — the mistake in an
    # earlier synthetic where the OU amplitude rivalled the RW's). C: independent.
    la = np.cumsum(rng.normal(0, 0.02, n)) + np.log(100.0)      # A: random walk
    ou = np.zeros(n)                                            # small OU spread A->B
    for i in range(1, n):
        ou[i] = 0.7 * ou[i - 1] + rng.normal(0, 0.003)         # mean-reverting, small
    lb = la + ou                                               # B cointegrated w/ A
    lc = np.cumsum(rng.normal(0, 0.02, n)) + np.log(50.0)      # C: independent RW
    with tempfile.TemporaryDirectory() as tmp:
        syms = {
            "AAA": _synth_csv(tmp, "AAA", np.exp(la)),
            "BBB": _synth_csv(tmp, "BBB", np.exp(lb)),
            "CCC": _synth_csv(tmp, "CCC", np.exp(lc)),
        }
        args = _parse([])
        args.resample = "1h"
        args.min_bars = 1000
        args.min_trades = 10
        args.oos_start = "2022-06-01"
        args.stability_window = 720
        res = scan(syms, args)
        by = {r["pair"]: r for r in res["ranked"]}
        ab = by.get("AAA/BBB", {})
        print("AAA/BBB:", {k: ab.get(k) for k in
              ("adf_tstat", "oos_adf_tstat", "half_life_hours", "oos_expectancy_r",
               "oos_trades", "oos_net_r_per_pos_day", "oos_robust")})
        for pr in ("AAA/CCC", "BBB/CCC"):
            rr = by.get(pr, {})
            print(f"{pr}: adf {rr.get('adf_tstat')} oos_adf {rr.get('oos_adf_tstat')} robust {rr.get('oos_robust')}")
        assert ab.get("oos_robust") is True, "cointegrated A/B should be OOS-robust"
        assert res["ranked"][0]["pair"] == "AAA/BBB", "A/B should rank first"
        # The independent pairs should NOT be robust.
        assert not by.get("AAA/CCC", {}).get("oos_robust"), "A/C is not cointegrated"
        assert not by.get("BBB/CCC", {}).get("oos_robust"), "B/C is not cointegrated"
        assert "AAA/BBB" in res["recommend_add"], "A/B should make the shortlist"
    print("SELF-TEST PASS: cointegrated pair identified + ranked #1; noise pairs rejected.")
    return 0


def _parse(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cointegration pairs universe-scan (net-of-fee, OOS-gated).")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--data-dir", help="directory of <SYMBOL>.csv|.parquet candle files")
    p.add_argument("--symbols", default=None, help="CSV subset (default: all files in --data-dir)")
    p.add_argument("--resample", default="1h")
    p.add_argument("--lookback", type=int, default=15)
    p.add_argument("--entry-z", type=float, default=2.0)
    p.add_argument("--exit-z", type=float, default=0.5)
    p.add_argument("--stop-z", type=float, default=2.0)
    p.add_argument("--max-hold-bars", type=int, default=20)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--hedge-beta", choices=["one", "rolling"], default="rolling")
    p.add_argument("--oos-start", default="2025-01-01", help="ISO date; OOS split point")
    p.add_argument("--stability-window", type=int, default=720, help="rolling-HL window bars (720=30d @1h)")
    p.add_argument("--min-bars", type=int, default=3000, help="min aligned overlap bars to score a pair")
    p.add_argument("--min-trades", type=int, default=20, help="min OOS trades for the robust gate")
    p.add_argument("--min-valid-pct", type=float, default=70.0, help="min rolling-HL valid %% for robust")
    p.add_argument("--adf-max-tstat", type=float, default=-2.86,
                   help="max (most-positive) DF t-stat for the cointegration gate (5%% crit ≈ -2.86)")
    p.add_argument("--hl-min-hours", type=float, default=2.0)
    p.add_argument("--hl-max-hours", type=float, default=72.0)
    p.add_argument("--max-leg-uses", type=int, default=3, help="cap per-symbol appearances in the shortlist")
    p.add_argument("--top", type=int, default=60)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--md", dest="md_out", default=None)
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse(argv)
    if args.self_test:
        return _self_test()
    if not args.data_dir:
        print("error: --data-dir required (or --self-test)", file=sys.stderr)
        return 2
    symbols = _discover_symbols(args.data_dir)
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
        symbols = {k: v for k, v in symbols.items() if k in want}
    if len(symbols) < 2:
        print(f"error: need >=2 symbols, found {sorted(symbols)}", file=sys.stderr)
        return 2
    res = scan(symbols, args)
    res["ranked"] = res["ranked"][: args.top]
    out = json.dumps(res, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(out)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(_to_md(res))
    print(out if not args.json_out else _to_md(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
