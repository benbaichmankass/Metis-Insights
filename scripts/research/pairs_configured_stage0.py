#!/usr/bin/env python3
"""Stage-0 read of ONE configured M22 pair at its CONFIGURED parameters, net of
the full cost stack, landing a sim-compatible trade ledger.

WHY THIS EXISTS (PAIRS-STOCKTAKE, 2026-10-07). The operator asked where the
pairs strategies stand and whether any is live-ready. The honest answer was
that NO configured pair has a Stage-0 record a guard can read (checklist E37),
and the one dispatchable route -- `research-harness-dispatch.yml harness=pairs`
-- runs `scripts/backtest_pairs.py` at the HARNESS defaults (lookback 20,
hedge_beta=one), not at the parameters `config/pairs.yaml` actually trades
(lookback 15, hedge_beta=rolling). A number about a different leg is evidence
about a different leg. The dispatch workflow takes no parameter inputs, so the
configured arm needs its own, allowlisted, `research-script-run.yml` command.

WHAT IT DOES. For `--pair <name>` it reads that entry from `config/pairs.yaml`
(symbols, timeframe, lookback, entry/exit/stop z, max_hold_bars, hedge_beta),
optionally fetches both legs' candles through the ONE corpus owner
(`scripts/ops/fetch_backtest_corpus.py`, Binance-vision proxy for Bybit, which
is geoblocked from GitHub runners), aligns them exactly as the harness CLI does,
resolves the cost stack PER LEG through `execution_costs.resolve_cost_policy`
(fee + slippage + perp funding -- the same resolution the harness's own `main()`
performs), runs `backtest_pairs.run_backtest` (the parity-verified engine; never
re-implemented here), and writes into `--out`:

  * `result.json`     -- the harness's own summary (net_total_r, fee-only arm,
                         cost bps, params, window)
  * `trades.jsonl`    -- one row per PAIR trade with `entry_time`, `exit_time`,
                         `gross_r`, `net_r` -- the keys `prop_ev_sim.py` reads,
                         so a committed copy of this file is the ledger a later
                         prop-fit unit can price without re-running anything
  * `verdict.json`    -- the `script_run.py` contract, graded by the rule below

THE RULE IS FIXED HERE, BEFORE ANY RUN, and the unit that dispatches this
command registers the same clauses as its `decision_rule`:
    PASS          iff net_total_r > 0 AND n >= --min-n
    FAIL          iff net_total_r <= 0 AND n >= --min-n
    indeterminate iff n < --min-n
`net_total_r` is NET OF THE FULL COST STACK. The fee-only arm is REPORTED
beside it and never graded on -- selecting on the pre-cost figure is the exact
failure the 2026-07-16 G2 readiness work and RQ-20260922-011 both name.

WHAT IT DOES NOT DO. It does not touch `config/pairs.yaml`, any roster, or any
order path. A PASS here is Stage-0 evidence only; it is not a promotion and it
is not a prop-fit verdict (that needs a 2-leg-aware simulator, which
`prop_ev_sim.py` is not -- it skips legs independently).

Self-test (synthetic cointegrated legs, no network)::

    python3 scripts/research/pairs_configured_stage0.py --self-test
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.backtest_pairs as bp  # noqa: E402  (the ONE pairs engine)
from src.runtime import execution_costs  # noqa: E402

CORPUS_FETCHER = _REPO_ROOT / "scripts" / "ops" / "fetch_backtest_corpus.py"
PAIRS_CONFIG = _REPO_ROOT / "config" / "pairs.yaml"
DEFAULT_MIN_N = 39   # the repo's standing E35 floor (d=0.45, alpha 0.05, power 0.8)


def load_pair(name: str, config_path: Path = PAIRS_CONFIG) -> Dict[str, Any]:
    """The configured entry for `name`, or a KeyError naming what exists."""
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    entries = data.get("pairs") or []
    for item in entries:
        if isinstance(item, dict) and str(item.get("name")) == name:
            return dict(item)
    raise KeyError(f"no pair named {name!r} in {config_path} "
                   f"(have: {[e.get('name') for e in entries if isinstance(e, dict)]})")


def fetch_leg(symbol: str, timeframe: str) -> None:
    """Fetch one leg through the corpus owner. A non-zero exit raises -- a
    missing corpus must land `producer_failed`, never a fabricated pass."""
    cmd = [sys.executable, str(CORPUS_FETCHER), "--symbol", symbol, "--timeframe", timeframe]
    rc = subprocess.run(cmd, cwd=str(_REPO_ROOT), check=False).returncode
    if rc != 0:
        raise RuntimeError(f"corpus fetch failed for {symbol} {timeframe} (exit {rc})")


def grade(net_total_r: float, n: int, min_n: int) -> str:
    if n < min_n:
        return "indeterminate"
    return "pass" if net_total_r > 0 else "fail"


def run_pair(pair: Dict[str, Any], *, data_a: Path, data_b: Path, out: Path,
             min_n: int, start: Optional[str] = None, end: Optional[str] = None,
             pair_name: Optional[str] = None) -> Dict[str, Any]:
    """Run the configured pair; write result.json / trades.jsonl / verdict.json."""
    import pandas as pd

    out.mkdir(parents=True, exist_ok=True)
    sym_a, sym_b = str(pair["symbol_a"]), str(pair["symbol_b"])
    timeframe = str(pair.get("timeframe", "1h"))
    name = pair_name or str(pair.get("name") or f"{sym_a}/{sym_b}")

    # Cost stack, resolved PER LEG exactly as `backtest_pairs.main()` does.
    # Module globals are what `_fee_r` reads, so they are set the same way.
    bp.FEE_BPS_ROUNDTRIP = execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
    bp.SLIPPAGE_BPS_ROUNDTRIP, bp.FUNDING_BPS_PER_WINDOW_A = execution_costs.resolve_cost_policy(
        sym_a, slippage_bps_roundtrip=None, funding_bps_per_window=None)
    _, bp.FUNDING_BPS_PER_WINDOW_B = execution_costs.resolve_cost_policy(
        sym_b, slippage_bps_roundtrip=None, funding_bps_per_window=None)

    a, b = bp._load_candles(str(data_a)), bp._load_candles(str(data_b))
    m = bp._align(a, b)
    if start:
        m = m[m["timestamp"] >= pd.to_datetime(start, utc=True)].reset_index(drop=True)
    if end:
        m = m[m["timestamp"] < pd.to_datetime(end, utc=True)].reset_index(drop=True)
    lookback = int(pair.get("lookback", 15))
    if len(m) <= lookback + 2:
        raise RuntimeError(f"only {len(m)} aligned bars (need > lookback={lookback})")

    rows: List[Dict[str, Any]] = []
    summary = bp.run_backtest(
        m, lookback=lookback,
        entry_z=float(pair.get("entry_z", 2.0)), exit_z=float(pair.get("exit_z", 0.5)),
        stop_z=float(pair.get("stop_z", 2.0)), max_hold_bars=int(pair.get("max_hold_bars", 20)),
        cooldown_bars=int(pair.get("cooldown_bars", 1)),
        hedge_beta=str(pair.get("hedge_beta", "rolling")),
        timeframe=timeframe, pair=f"{sym_a}/{sym_b}", collect_rows=rows)

    # Sim-compatible ledger: entry_time / exit_time / gross_r / net_r per PAIR
    # trade, costed by the engine's own `_fee_r` over a Trade built from the
    # collected decision facts (risk = risk_spread; the spread levels are not
    # needed by the cost term and are zeroed).
    ledger: List[Dict[str, Any]] = []
    for r in rows:
        t = bp.Trade(entry_time=r["entry_time"], direction=r["direction"], entry_spread=0.0,
                     exit_time=r["exit_time"], exit_spread=0.0, risk=float(r["risk_spread"]),
                     outcome=r["outcome"], gross_r=float(r["gross_r"]), z_at_entry=0.0)
        cost = bp._fee_r(t)
        ledger.append({
            "strategy": name, "pair": f"{sym_a}/{sym_b}", "symbol": f"{sym_a}/{sym_b}",
            "entry_time": str(r["entry_time"]), "exit_time": str(r["exit_time"]),
            "direction": r["direction"], "outcome": r["outcome"],
            "gross_r": float(r["gross_r"]), "cost_r": round(float(cost), 6),
            "net_r": round(float(r["gross_r"]) - float(cost), 4),
            "beta": r["beta"], "risk_spread": r["risk_spread"],
        })
    with (out / "trades.jsonl").open("w", encoding="utf-8") as fh:
        for row in ledger:
            fh.write(json.dumps(row, default=str) + "\n")

    n = int(summary.get("total_trades") or 0)
    net = float(summary.get("net_total_r") or 0.0)
    verdict = grade(net, n, min_n)
    data_start = str(m["timestamp"].iloc[0]) if len(m) else None
    data_end = str(m["timestamp"].iloc[-1]) if len(m) else None
    measurement = {
        "pair": name, "symbol_a": sym_a, "symbol_b": sym_b, "timeframe": timeframe,
        "params": summary.get("params"),
        "n_trades": n, "net_total_r": net,
        "net_total_r_fee_only": summary.get("net_total_r_fee_only"),
        "net_expectancy_r": summary.get("net_expectancy_r"),
        "win_rate_pct": summary.get("win_rate_pct"),
        "max_drawdown_r": summary.get("max_drawdown_r"),
        "cost_stack_bps": {
            "fee_roundtrip_per_leg": bp.FEE_BPS_ROUNDTRIP,
            "slippage_roundtrip_per_leg": bp.SLIPPAGE_BPS_ROUNDTRIP,
            "funding_per_window_a": bp.FUNDING_BPS_PER_WINDOW_A,
            "funding_per_window_b": bp.FUNDING_BPS_PER_WINDOW_B,
        },
        "data_start": data_start, "data_end": data_end, "aligned_bars": int(len(m)),
        "min_n": min_n, "rule": "PASS iff net_total_r > 0 AND n >= min_n; FAIL iff "
                                "net_total_r <= 0 AND n >= min_n; indeterminate iff n < min_n",
    }
    (out / "result.json").write_text(json.dumps(summary, indent=2, default=str) + "\n",
                                     encoding="utf-8")
    payload = {
        "verdict": verdict,
        "read_state": "measured",
        "population": (f"{name}: {n} pair trades, {sym_a}/{sym_b} {timeframe} at the configured "
                       f"params {measurement['params']}, {data_start} -> {data_end}, net of "
                       f"fee+slippage+funding per leg"),
        "n": n,
        "measurement": measurement,
        "note": (f"{verdict.upper()}: net_total_r {net:+.4f} R over n={n} (floor {min_n}); "
                 f"fee-only arm {measurement['net_total_r_fee_only']} R reported, not graded. "
                 f"Ledger: trades.jsonl beside this file (entry_time/exit_time/net_r per pair trade)."),
        "records": [{"leg": name, "verdict": verdict, "n": n, "net_total_r": net,
                     "population": measurement["params"]}],
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str) + "\n",
                                      encoding="utf-8")
    print(f"pairs_configured_stage0: {name} verdict={verdict} n={n} net_total_r={net:+.4f}")
    return payload


def _self_test() -> int:
    import tempfile

    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(7)
    n = 2400
    ts = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    common = np.cumsum(rng.normal(0, 0.004, n))
    # an AR(1) stationary spread so the engine finds reverting trades
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = 0.9 * spread[i - 1] + rng.normal(0, 0.01)
    pa = 100.0 * np.exp(common + spread)
    pb = 50.0 * np.exp(common)

    def frame(px: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame({"timestamp": ts, "open": px, "high": px * 1.001,
                             "low": px * 0.999, "close": px})

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        frame(pa).to_csv(d / "A_1h.csv", index=False)
        frame(pb).to_csv(d / "B_1h.csv", index=False)
        pair = {"name": "pairs_test", "symbol_a": "AUSDT", "symbol_b": "BUSDT",
                "timeframe": "1h", "lookback": 15, "entry_z": 2.0, "exit_z": 0.5,
                "stop_z": 2.0, "max_hold_bars": 20, "hedge_beta": "rolling"}
        out = d / "out"
        v = run_pair(pair, data_a=d / "A_1h.csv", data_b=d / "B_1h.csv", out=out, min_n=5)
        assert (out / "verdict.json").exists() and (out / "trades.jsonl").exists()
        assert v["verdict"] in ("pass", "fail", "indeterminate"), v
        assert v["n"] >= 5 and v["verdict"] != "indeterminate", v
        rows = [json.loads(line) for line in (out / "trades.jsonl").read_text().splitlines()]
        assert len(rows) == v["n"]
        assert all("entry_time" in r and "exit_time" in r and "net_r" in r for r in rows)
        # the full-cost arm can never read higher than the fee-only arm
        assert v["measurement"]["net_total_r"] <= v["measurement"]["net_total_r_fee_only"] + 1e-9
        # grading branches
        assert grade(1.0, 10, 39) == "indeterminate"
        assert grade(1.0, 40, 39) == "pass" and grade(0.0, 40, 39) == "fail"
        # a config lookup that cannot find the pair names what exists
        try:
            load_pair("no_such_pair")
        except KeyError as exc:
            assert "pairs_sol_eth" in str(exc)
        else:
            raise AssertionError("load_pair must refuse an unknown pair")
    print("pairs_configured_stage0 self-test OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--pair", help="entry name in config/pairs.yaml, e.g. pairs_sol_eth")
    ap.add_argument("--config", default=str(PAIRS_CONFIG))
    ap.add_argument("--data-dir", default="data", help="where <SYMBOL>_<tf>.csv live")
    ap.add_argument("--fetch", action="store_true",
                    help="fetch both legs through scripts/ops/fetch_backtest_corpus.py first")
    ap.add_argument("--out", required=False, help="output directory ({out_dir} on the runner)")
    ap.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    ap.add_argument("--start", default=None, help="ISO date; drop aligned bars before it")
    ap.add_argument("--end", default=None, help="ISO date; drop aligned bars on/after it")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.pair or not args.out:
        ap.error("--pair and --out are required (or --self-test)")
    pair = load_pair(args.pair, Path(args.config))
    tf = str(pair.get("timeframe", "1h"))
    sym_a, sym_b = str(pair["symbol_a"]), str(pair["symbol_b"])
    if args.fetch:
        fetch_leg(sym_a, tf)
        fetch_leg(sym_b, tf)
    data_dir = Path(args.data_dir)
    run_pair(pair, data_a=data_dir / f"{sym_a}_{tf}.csv", data_b=data_dir / f"{sym_b}_{tf}.csv",
             out=Path(args.out), min_n=args.min_n, start=args.start, end=args.end,
             pair_name=args.pair)
    return 0


if __name__ == "__main__":
    sys.exit(main())
