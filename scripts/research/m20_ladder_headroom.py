#!/usr/bin/env python3
"""M20 — is there ROOM for a partial-TP ladder under the live take-profit cap?

The question this answers, and why it comes BEFORE any ladder producer
=====================================================================
A partial-TP ladder banks part of the position at a rung and runs the remainder
to a final target. Its whole thesis needs somewhere to run. On this fleet the
live take-profit is::

    tp = min(entry * (1 + 0.099), entry + tp_r * risk)      # trend_donchian.py:388

and **27 of the 40 capped live legs declare ``tp_r: 50.0``** — a "let it run"
sentinel that, at any realistic risk (1-7% of price), the 9.9% venue cap ALWAYS
wins. So the remainder's ceiling is the same 9.9% whether a rung banked or not.

That makes the honest first question not *"which ladder wins?"* but
**"how often does a trade even reach the ceiling?"** — cap utilisation:

* If winners rarely reach the cap, a rung BELOW it mostly banks trades that were
  going to win anyway. That is a cost, not an improvement, and no amount of grid
  search over rung placement fixes it. We would stop here.
* If winners routinely blow through the cap, the binding problem is the CAP
  (a fixed-TP-vs-trailing-final question), not the absence of a rung.
* Only the middle case — a meaningful mass of winners running well past a
  candidate rung but stalling before the cap — is a ladder opportunity.

Building a producer first and discovering this afterwards would manufacture ~46
more cells whose verdict says "the ladder did not work" when it actually means
"there was never room for one" — the vacuity class this milestone has spent its
time removing.

What it measures
----------------
One harness run per leg at LIVE PARITY (``--tp-cap-pct 0.099``), then per trade:

    risk   = |entry - sl|
    cap_r  = 0.099 * entry / risk        # the cap expressed in THIS trade's R
    mfe_r  = exit_capture.mfe_r_of(row)  # the ONE canonical accessor

``cap_r`` is per-trade, not per-leg: the cap is a fraction of PRICE and R is a
fraction of RISK, so the same 9.9% is a different R multiple on every trade.
Reporting a single leg-level "cap in R" would be a fiction; the distribution is
the answer.

Honesty rules this script keeps
-------------------------------
* **MFE-missing is counted and reported separately**, never folded into "did not
  go favourable". ``exit_capture.mfe_r_of`` exists precisely because a consumer
  that re-derived the read saw ``0/1102`` on the scalp legs and could not tell a
  missing reading from a flat trade.
* **No percentage over a thin or zero denominator.** Below ``--min-trades`` the
  leg reports ``verdict: "insufficient_trades"`` with its count, and no share.
* **Three outcomes, never collapsed**: ``no_room`` (winners do not reach a
  candidate rung) · ``cap_binds`` (winners routinely reach the ceiling) ·
  ``ladder_opportunity``. A leg that cannot be graded says so.

Tier-1 research tooling. Reads harness output; writes JSON. Touches no config,
no order path, no live VM.

Usage::

    python3 scripts/research/m20_ladder_headroom.py \
        --data-dir ~/ict-trading-bot/data \
        --out ~/ict-trading-bot/runtime_logs/m20_ladder_headroom
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts"))

from exit_capture import mfe_r_of  # noqa: E402
from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS, LIVE_TP_CAPPED_FAMILIES, base_args, classify, resolve_data,
    tp_geometry_for)

# The live cap, from src/units/strategies/trend_donchian.py::_TP_SENTINEL_CAP_PCT.
# Imported as a literal rather than from the strategy module because this script
# must run on a bare research checkout; the value is asserted against the
# strategy source by tests/test_m20_ladder_headroom.py so it cannot drift.
# The venue TP clamp -- ONE owner: src/runtime/tp_venue_cap.py. IMPORTED, not
# mirrored. This file used to carry its own `LIVE_TP_CAP_PCT = 0.099`, one of
# thirteen such literals with nothing binding them -- and this repo's own note
# on that was right: "if the live constant moves, this silently keeps measuring
# the OLD book, and the sweep will look correct while doing it". The owner
# imports only `typing`, and src/__init__.py + src/runtime/__init__.py are
# empty, so this adds no heavy dependency.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.runtime.tp_venue_cap import (  # noqa: E402
    TP_VENUE_CAP_PCT as LIVE_TP_CAP_PCT)

# Candidate rung placements, in R, evaluated against each trade's own MFE.
# Deliberately COARSE: this script decides whether a ladder is worth pursuing at
# all, not which parameterization wins. Picking the rung is the producer's job,
# and it should read each leg's own MFE quantiles (the trail_decay P4.4
# precedent), not this grid.
CANDIDATE_RUNGS_R = (1.0, 1.5, 2.0)


def _pct(n: int, d: int) -> Optional[float]:
    """Share, or None when the denominator cannot carry one."""
    return round(100.0 * n / d, 1) if d > 0 else None


def _quantile(sorted_vals: List[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return round(sorted_vals[idx], 4)


def analyse_trades(rows: List[dict], cap_pct: float,
                   rungs_r: tuple = CANDIDATE_RUNGS_R) -> Dict[str, Any]:
    """Cap utilisation + rung reachability for one leg's emitted trades.

    Pure — no I/O, so the tests exercise the arithmetic directly.
    """
    n_total = len(rows)
    mfe_missing = 0
    unusable = 0
    measured: List[float] = []          # mfe_r for every measurable trade
    cap_r_vals: List[float] = []
    reached_cap = 0
    reach_rung = {r: 0 for r in rungs_r}
    winners = 0
    winner_mfe: List[float] = []

    for tr in rows:
        try:
            entry = float(tr["entry"])
            sl = float(tr["sl"])
        except (KeyError, TypeError, ValueError):
            unusable += 1
            continue
        risk = abs(entry - sl)
        if risk <= 0 or entry <= 0:
            unusable += 1
            continue

        mfe = mfe_r_of(tr)
        if mfe is None:
            mfe_missing += 1
            continue

        # The cap expressed in THIS trade's R. Per-trade by necessity: the cap is
        # a share of price, R is a share of risk.
        cap_r = cap_pct * entry / risk
        cap_r_vals.append(cap_r)
        measured.append(float(mfe))

        if float(mfe) >= cap_r:
            reached_cap += 1
        for r in rungs_r:
            if float(mfe) >= r:
                reach_rung[r] += 1

        net = tr.get("net_r")
        if net is None:
            net = tr.get("gross_r")
        try:
            if net is not None and float(net) > 0:
                winners += 1
                winner_mfe.append(float(mfe))
        except (TypeError, ValueError):
            pass

    measured.sort()
    winner_mfe.sort()
    cap_r_vals.sort()
    n_meas = len(measured)

    return {
        "trades_emitted": n_total,
        "trades_measured": n_meas,
        "mfe_missing": mfe_missing,
        "unusable_rows": unusable,
        # MEASUREMENT COVERAGE — read every share below against this, not against
        # trades_emitted. A leg with mfe_missing == trades_emitted measured NOTHING.
        "mfe_coverage_pct": _pct(n_meas, n_total),
        "winners": winners,
        "cap_r_median": _quantile(cap_r_vals, 0.5),
        "cap_r_p10": _quantile(cap_r_vals, 0.10),
        "cap_r_p90": _quantile(cap_r_vals, 0.90),
        "reached_cap": reached_cap,
        "cap_utilisation_pct": _pct(reached_cap, n_meas),
        "mfe_p50": _quantile(measured, 0.50),
        "mfe_p80": _quantile(measured, 0.80),
        "mfe_p95": _quantile(measured, 0.95),
        "winner_mfe_p50": _quantile(winner_mfe, 0.50),
        "winner_mfe_p80": _quantile(winner_mfe, 0.80),
        "rung_reach_pct": {f"{r:g}R": _pct(reach_rung[r], n_meas) for r in rungs_r},
    }


def verdict_for(stats: Dict[str, Any], min_trades: int,
                cap_binds_pct: float, rung_floor_pct: float) -> Dict[str, Any]:
    """Grade a leg. Three real outcomes plus an explicit ungradeable state."""
    n = stats["trades_measured"]
    if n < min_trades:
        return {"verdict": "insufficient_trades",
                "why": (f"{n} measurable trade(s) < --min-trades {min_trades}"
                        f" (emitted {stats['trades_emitted']}, "
                        f"mfe_missing {stats['mfe_missing']}) — no share is "
                        f"reported over a denominator this thin")}

    cap_util = stats["cap_utilisation_pct"]
    # The widest candidate rung's reach is what bounds a ladder's opportunity:
    # if even the CHEAPEST rung is rarely reached, no placement helps.
    best_rung_pct = max((v for v in stats["rung_reach_pct"].values()
                         if v is not None), default=None)

    if cap_util is not None and cap_util >= cap_binds_pct:
        return {"verdict": "cap_binds",
                "why": (f"{cap_util}% of measured trades reach the {LIVE_TP_CAP_PCT:.1%} "
                        f"cap — the binding constraint is the CEILING, not the "
                        f"absence of a rung. The question for this leg is "
                        f"fixed-TP vs a trailing final, not a partial ladder.")}
    if best_rung_pct is None or best_rung_pct < rung_floor_pct:
        return {"verdict": "no_room",
                "why": (f"the most-reached candidate rung is hit by "
                        f"{best_rung_pct}% of measured trades (floor "
                        f"{rung_floor_pct}%) — a rung here mostly banks trades "
                        f"that were going to win anyway. Not a ladder problem.")}
    return {"verdict": "ladder_opportunity",
            "why": (f"rungs are reachable ({best_rung_pct}% hit the best "
                    f"candidate) while only {cap_util}% run to the cap — mass "
                    f"between a rung and the ceiling is exactly what a partial "
                    f"ladder monetises. Worth a producer.")}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m20_ladder_headroom"))
    ap.add_argument("--only", default=None, help="CSV of leg names")
    ap.add_argument("--tp-cap-pct", type=float, default=LIVE_TP_CAP_PCT,
                    help="Live-parity TP cap (default 0.099 — the production "
                         "value). Pass 0.0 only to reproduce a legacy run.")
    ap.add_argument("--min-trades", type=int, default=30,
                    help="Below this many MEASURABLE trades a leg is graded "
                         "insufficient_trades and reports no share.")
    ap.add_argument("--cap-binds-pct", type=float, default=25.0)
    ap.add_argument("--rung-floor-pct", type=float, default=15.0)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
                  or {}).get("strategies") or {}
    only = set(a.only.split(",")) if a.only else None
    data_dir = Path(a.data_dir)
    out_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")

    plan = []
    skipped = []
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or (only and name not in only):
            continue
        if cfg.get("execution") == "shadow":
            skipped.append({"leg": name, "reason": "shadow"})
            continue
        fam = classify(name)
        if fam not in LIVE_TP_CAPPED_FAMILIES:
            skipped.append({"leg": name, "reason": f"family_not_capped:{fam}"})
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        data, proxy, resample = resolve_data(str(sym), tf, data_dir)
        if data is None:
            skipped.append({"leg": name, "reason": f"data_missing:{sym}"})
            continue
        plan.append({"leg": name, "cfg": cfg, "fam": fam, "symbol": sym,
                     "tf": tf, "data": data, "proxy": proxy, "resample": resample})

    print(f"plan: {len(plan)} live capped leg(s), {len(skipped)} skipped", flush=True)
    if a.list:
        for p in plan:
            print(f"  RUN {p['leg']:32s} {p['symbol']} {p['tf']} "
                  f"{'(proxy)' if p['proxy'] else ''}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    results: Dict[str, Any] = {}

    for p in plan:
        name = p["leg"]
        emit = out_dir / f"{name}_trades.jsonl"
        cmd = [sys.executable, str(REPO / FAMILY_HARNESS[p["fam"]]),
               *base_args(name, p["cfg"], p["fam"], p["data"], p["resample"],
                          a.tp_cap_pct),
               "--emit-trades", str(emit), "--json", "/tmp/m20_headroom_base.json"]
        print(f"== {name} ({p['symbol']} {p['tf']}) ==", flush=True)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=a.timeout)
        except subprocess.TimeoutExpired:
            results[name] = {"status": "harness_timeout"}
            print("   harness_timeout", flush=True)
            continue
        if proc.returncode != 0 or not emit.exists():
            results[name] = {"status": "harness_error",
                             "error": (proc.stderr or proc.stdout)[-300:]}
            print(f"   harness_error: {(proc.stderr or proc.stdout)[-160:]}", flush=True)
            continue

        rows = []
        for line in emit.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        stats = analyse_trades(rows, a.tp_cap_pct)
        v = verdict_for(stats, a.min_trades, a.cap_binds_pct, a.rung_floor_pct)
        results[name] = {"status": "ok", "symbol": p["symbol"], "tf": p["tf"],
                         "proxy": p["proxy"], "family": p["fam"],
                         "tp_geometry": tp_geometry_for({p["fam"]}, a.tp_cap_pct),
                         "tp_cap_pct": a.tp_cap_pct,
                         "declared_tp_r": p["cfg"].get("tp_r"),
                         **stats, **v}
        print(f"   {v['verdict']}: cap_util={stats['cap_utilisation_pct']}% "
              f"rungs={stats['rung_reach_pct']} "
              f"(measured {stats['trades_measured']}/{stats['trades_emitted']}, "
              f"mfe_missing {stats['mfe_missing']})", flush=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tp_cap_pct": a.tp_cap_pct,
        "candidate_rungs_r": list(CANDIDATE_RUNGS_R),
        "thresholds": {"min_trades": a.min_trades,
                       "cap_binds_pct": a.cap_binds_pct,
                       "rung_floor_pct": a.rung_floor_pct},
        "skipped": skipped,
        "legs": results,
    }
    (out_dir / "headroom.json").write_text(json.dumps(payload, indent=1))

    graded = [r for r in results.values() if r.get("status") == "ok"]
    tally: Dict[str, int] = {}
    for r in graded:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\n=== VERDICT TALLY ===", flush=True)
    for k, n in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {k:24s} {n}", flush=True)
    print(f"  (graded {len(graded)} of {len(plan)} planned; "
          f"{len(results) - len(graded)} failed to run)", flush=True)
    print(f"done -> {out_dir}/headroom.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
