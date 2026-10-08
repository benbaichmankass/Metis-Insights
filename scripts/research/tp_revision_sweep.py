#!/usr/bin/env python3
"""TP doctrine B1 — grade the trend_donchian measured-move TP revision offline.

WHAT QUESTION THIS ANSWERS
--------------------------
`src/runtime/tp_revision.py::donchian_measured_move` moves a trend_donchian
leg's take-profit, after every closed bar, to the Donchian measured move (the
channel height carried past the channel edge, extend-only). Before any leg
DECLARES it (`tp_revision:` in config/strategies.yaml — Tier-3), does it beat
the leg's book as it trades today, net of the full cost stack, out of sample?

Unit: `research/queue/RQ-20261007-080.yaml` (rule registered before the run).

METHOD — the be_floor_sweep machinery, one lever swapped
--------------------------------------------------------
This reuses `scripts/research/be_floor_sweep.py` verbatim for everything except
the lever: the same leg resolver (`pipeline.monitor_unit_for`, filtered to the
`trend_donchian` unit), the same harness (`scripts/backtest_trend.py`, loaded by
path), the same config->kwarg map and harness defaults, the same calendar-fold
panel {3, 4, 5}, the same inert-fold rule and the same pooled gate. Every arm
differs from the control in exactly one lever.

  * control   `tp_revision=""` — the leg's declared `tp_r`, placed at the venue
              cap as live does (`tp_cap_pct = TP_VENUE_CAP_PCT` on EVERY arm,
              so the control is the live bracket, not a TP-less book)
  * arms      `tp_revision="donchian_measured_move"`, `width_mult` in
              {0.5, 1.0, 1.5, 2.0}

⚠️ IT PROPOSES; IT ARMS NOTHING. It writes no config.

Run:
    python3 scripts/research/tp_revision_sweep.py --data-dir <dir of SYMBOL_1h.csv> \\
        [--fetch DAYS] [--leg trend_donchian_eth_4h] [--out DIR]
`--out DIR` writes DIR/sweep.json and DIR/verdict.json (graded by `grade()`
against RULE-RQ1007-080, the research-script-run E5 contract).
"""
from __future__ import annotations

import functools
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))


def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_be_floor_sweep",
                                                  ROOT / "scripts/research/be_floor_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_be_floor_sweep"] = mod
    spec.loader.exec_module(mod)
    return mod


bfs = _load()

from src.runtime.tp_revision import RULE_DONCHIAN_MEASURED_MOVE  # noqa: E402
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402

#: 0.0 is the CONTROL (no revision); the rest are `width_mult` arms.
ARMS: tuple = (0.0, 0.5, 1.0, 1.5, 2.0)
UNIT = "trend_donchian"

_orig_kwargs_for = bfs._kwargs_for
_orig_ratchet_legs = bfs.ratchet_legs


def _kwargs_for(mod, leg: Dict[str, Any], symbol: str, timeframe: str,
                arm: float, emit_path: str) -> tuple:
    kw, dropped = _orig_kwargs_for(mod, leg, symbol, timeframe, 0.0, emit_path)
    kw["tp_cap_pct"] = TP_VENUE_CAP_PCT
    if arm > 0.0:
        kw["tp_revision"] = RULE_DONCHIAN_MEASURED_MOVE
        kw["tp_revision_width_mult"] = arm
    return kw, dropped


def donchian_legs() -> Dict[str, Dict[str, Any]]:
    return {k: v for k, v in _orig_ratchet_legs().items() if v.get("_unit") == UNIT}


def _print_pooled(a: Dict[str, Any]) -> None:
    print("\n=== POOLED (graded legs only) ===")
    print(f"graded legs: {a['graded_legs']}   excluded: {a['excluded']}")
    print(f"control (no revision): net {a['control_net_total_r']:+.2f}R over {a['control_trades']} trades")
    for arm, v in a["by_arm"].items():
        pan = " ".join(f"k={k}:{p['wins']}W/{p['losses']}L/{p['inert']}inert"
                       for k, p in v["panel"].items())
        print(f"  width_mult={arm:<4} net {v['net_total_r']:+9.2f}R  d {v['d_net_r_vs_control']:+8.2f}R  "
              f"legs +{v['legs_better']}/-{v['legs_worse']}/={v['legs_inert']}  {pan}  "
              f"GATE={'PASS' if v['clears_gate'] else 'FAIL'}")


_calls: List[Dict[str, Any]] = []
_harness = bfs.HARNESS[UNIT]
_orig_run_backtest = _harness.run_backtest
_orig_run_leg = bfs.run_leg


@functools.wraps(_orig_run_backtest)    # be_floor_sweep reads the real signature
def _run_backtest(df, **kw):
    out = _orig_run_backtest(df, **kw)
    _calls.append(dict(out.get("params") or {}))
    return out


def _run_leg(name, leg, data_dir):
    """be_floor_sweep.run_leg plus, per arm, how many revisions were applied
    and how many the venue cap bound (a cap-bound arm is not the measured move)."""
    _calls.clear()
    rec = _orig_run_leg(name, leg, data_dir)
    if len(_calls) == len(ARMS):
        rec["tp_revision_counts"] = {
            str(arm): {"applied": c.get("tp_revisions_applied", 0),
                       "clamped": c.get("tp_revisions_clamped", 0)}
            for arm, c in zip(ARMS, _calls)}
    return rec


# Swap the lever into the shared machinery (module globals read at call time).
_harness.run_backtest = _run_backtest
bfs.run_leg = _run_leg
bfs.ARMS = ARMS
bfs._kwargs_for = _kwargs_for
bfs.ratchet_legs = donchian_legs
bfs._print_pooled = _print_pooled


def fetch(data_dir: Path, symbols: List[str], days: int) -> None:
    """Public 1h klines from data.binance.vision (USD-M perps), written as
    <SYMBOL>_1h.csv -- the layout be_floor_sweep reads. An empty fetch writes
    nothing, so the leg reads `no_data` ("could not look"), never a result."""
    import time

    import pandas as pd
    fbc = _load_path("_fetch_candles", ROOT / "scripts/ops/fetch_backtest_candles.py")
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    data_dir.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        rows = fbc.fetch_klines_binance_vision(sym, "60", start_ms, end_ms)
        if not rows:
            print(f"fetch: {sym}: no rows", file=sys.stderr)
            continue
        df = pd.DataFrame([{k: r[k] for k in ("timestamp", "open", "high", "low", "close", "volume")}
                           for r in rows])
        df.to_csv(data_dir / f"{sym}_1h.csv", index=False)
        print(f"fetch: {sym}: {len(df)} bars {df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]}")


def _load_path(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: List[str]) -> int:
    """Adds `--fetch DAYS` (download the legs' 1h klines into --data-dir
    first) to be_floor_sweep's CLI; every other flag is passed through."""
    args = list(argv[1:])
    # `--leg` may repeat here (be_floor_sweep takes one): the named legs ARE the
    # population, and an unknown name is refused rather than silently dropped.
    only: List[str] = []
    while "--leg" in args:
        i = args.index("--leg")
        only.append(args[i + 1])
        del args[i:i + 2]
    legs = donchian_legs()
    if only:
        unknown = sorted(set(only) - set(legs))
        if unknown:
            print(f"not an enabled+live trend_donchian-unit leg: {unknown}", file=sys.stderr)
            return 2
        legs = {k: v for k, v in legs.items() if k in only}
    bfs.ratchet_legs = lambda: legs
    if "--fetch" in args:
        i = args.index("--fetch")
        days = int(args[i + 1])
        del args[i:i + 2]
        data_dir = Path(args[args.index("--data-dir") + 1])
        fetch(data_dir, sorted({(v.get("symbols") or [None])[0] for v in legs.values()} - {None}), days)
    out_dir = None
    if "--out" in args:
        i = args.index("--out")
        out_dir = Path(args[i + 1])
        del args[i:i + 2]
        out_dir.mkdir(parents=True, exist_ok=True)
        args += ["--json", str(out_dir / "sweep.json")]
    rc = bfs.main([argv[0], *args])
    if out_dir is not None and rc == 0:
        verdict = grade(json.loads((out_dir / "sweep.json").read_text()))
        (out_dir / "verdict.json").write_text(json.dumps(verdict, indent=2))
        print(f"verdict: {verdict['verdict']} -- {verdict['note']}")
    return rc


#: An arm whose applied revisions the venue cap bound this often measures a
#: re-anchored cap, not the measured move, and cannot PASS as the rule.
MAX_CLAMPED_SHARE = 0.5


def grade(sweep: Dict[str, Any]) -> Dict[str, Any]:
    """RULE-RQ1007-080 (research/queue/RQ-20261007-080.yaml), mechanically."""
    pooled = sweep.get("pooled") or {}
    legs = sweep.get("legs") or []
    graded = [r for r in legs if r.get("state") == "graded"]
    pop = ("trend_donchian-unit legs enabled+live in config/strategies.yaml, "
           "scripts/backtest_trend.py at each leg's declared params, tp_cap_pct=TP_VENUE_CAP_PCT on every arm")
    n = sum(r.get("control_trades", 0) for r in graded)
    if not graded:
        return {"verdict": "indeterminate", "read_state": "measured", "population": pop, "n": 0,
                "note": "no leg reached MIN_TRADES control trades: unresolvable at current n"}

    def clamped_share(arm: str) -> float:
        app = sum((r.get("tp_revision_counts") or {}).get(arm, {}).get("applied", 0) for r in graded)
        cl = sum((r.get("tp_revision_counts") or {}).get(arm, {}).get("clamped", 0) for r in graded)
        return cl / app if app else 1.0

    passing = []
    for arm, v in (pooled.get("by_arm") or {}).items():
        share = clamped_share(arm)
        if v.get("clears_gate") and share <= MAX_CLAMPED_SHARE:
            passing.append((v["d_net_r_vs_control"], arm, share))
    records = [{"leg": r["leg"], "state": r.get("state"), "control_trades": r.get("control_trades"),
                "arms": r.get("arms"), "tp_revision_counts": r.get("tp_revision_counts")} for r in legs]
    if passing:
        d, arm, share = max(passing)
        return {"verdict": "pass", "read_state": "measured", "population": pop, "n": n,
                "measurement": {"best_width_mult": float(arm), "d_net_r_vs_control": d,
                                "clamped_share": round(share, 4)},
                "note": f"width_mult={arm} clears the pooled gate (net up, majority of exercised "
                        f"folds won at k=3,4,5) with {share:.0%} of revisions cap-bound",
                "records": records}
    return {"verdict": "fail", "read_state": "measured", "population": pop, "n": n,
            "measurement": {a: {"d_net_r_vs_control": v.get("d_net_r_vs_control"),
                                "clears_gate": v.get("clears_gate"),
                                "clamped_share": round(clamped_share(a), 4)}
                            for a, v in (pooled.get("by_arm") or {}).items()},
            "note": "no width_mult arm clears the pooled gate with <= 50% cap-bound revisions",
            "records": records}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
