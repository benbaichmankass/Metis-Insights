#!/usr/bin/env python3
"""M20 P4.5 — fleet regime-flip-exit replay driver.

For every runnable donchian/pullback leg (config-exact from
config/strategies.yaml, same resolvers as the fleet sweep): run the leg's
harness with --emit-trades, then replay the frozen-label regime-flip exit
(m20_regime_flip_replay) against the actual exits. Policy key = the family
base the roster matrix was measured at (donchian -> trend_donchian,
pullback -> htf_pullback_trend_2h).

Tier-1 research tooling. Run on the trainer, detached:
  nohup .venv/bin/python3 scripts/research/m20_flip_replay_sweep.py \
      --data-dir ~/ict-trading-bot/data \
      --out ~/ict-trading-bot/runtime_logs/m20_flip_replay >/tmp/flip.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS, base_args, classify, resolve_data, tp_geometry_for)

POLICY_KEY = {"donchian": "trend_donchian", "pullback": "htf_pullback_trend_2h"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m20_flip_replay"))
    ap.add_argument("--only", default=None)
    ap.add_argument("--tp-cap-pct", type=float, default=0.099,
                    help="take-profit cap as a fraction of entry, forwarded to "
                         "base_args (LIVE PARITY IS THE DEFAULT). The live "
                         "donchian/pullback units clamp the TP to 9.9%% from "
                         "entry via _TP_SENTINEL_CAP_PCT; this driver called "
                         "base_args POSITIONALLY, so tp_cap_pct defaulted to "
                         "0.0 and neither --tp-cap-pct nor --tp-r was ever "
                         "appended. EVERY regime_flip_exit cell in the matrix "
                         "was therefore replayed against a book with NO "
                         "take-profit — 42 of 43 negatives are capped-family "
                         "legs — which is why the coverage roll-up pins this "
                         "lever's geometry cutover at the NEVER sentinel "
                         "rather than a date "
                         "(BL-20260814-THREE-SIBLING-SWEEPS-STILL-BUILD-NO-TAKE-PROFIT-BOOKS-AND-STAMP-NOTHING). "
                         "Pass 0 to reproduce the old no-take-profit books.")
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    only = set(a.only.split(",")) if a.only else None
    data_dir = Path(a.data_dir)
    out_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    verdicts: dict = {}
    # Families that actually EMITTED, so the run-level stamp describes what ran
    # rather than what was requested — a leg that failed its harness must not
    # colour the geometry label.
    fams_seen: set[str] = set()

    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or (only and name not in only):
            continue
        fam = classify(name)
        if fam not in POLICY_KEY:
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        data, proxy, resample = resolve_data(str(sym), tf, data_dir)
        if data is None:
            verdicts[name] = {"status": "data_missing"}
            continue
        emit = out_dir / f"{name}_trades.jsonl"
        cmd = [sys.executable, str(REPO / FAMILY_HARNESS[fam]),
               *base_args(name, cfg, fam, data, resample, a.tp_cap_pct),
               "--emit-trades", str(emit), "--json", "/tmp/flip_base.json"]
        print(f"== {name} ({sym} {tf}) ==", flush=True)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            verdicts[name] = {"status": "harness_timeout"}
            continue
        if p.returncode != 0 or not emit.exists():
            verdicts[name] = {"status": "harness_error",
                              "error": (p.stderr or p.stdout)[-200:]}
            continue
        rep = out_dir / f"{name}_flip.json"
        cmd2 = [sys.executable,
                str(REPO / "scripts/research/m20_regime_flip_replay.py"),
                "--data", data, "--symbol", str(sym), "--timeframe", tf,
                "--policy-key", POLICY_KEY[fam],
                "--trades", str(emit), "--json", str(rep)]
        try:
            p2 = subprocess.run(cmd2, capture_output=True, text=True,
                                timeout=900)
        except subprocess.TimeoutExpired:
            verdicts[name] = {"status": "replay_timeout"}
            continue
        if p2.returncode != 0 or not rep.exists():
            verdicts[name] = {"status": "replay_error",
                              "error": (p2.stderr or p2.stdout)[-200:]}
            continue
        r = json.loads(rep.read_text())
        fams_seen.add(fam)
        verdicts[name] = {
            # PER LEG, from the family that ran, because the run-level flag
            # does not describe a leg: base_args withholds the cap from an
            # uncapped family, and a leg stamped off the flag alone would
            # self-report a geometry its harness never received.
            "tp_geometry": tp_geometry_for({fam}, a.tp_cap_pct),
            "tp_cap_pct": a.tp_cap_pct,
            "proxy": proxy, "trades": r["trades"],
            "flip_pct": r["flip_pct"], "walkforward": r["walkforward"],
            "verdict": r["verdict"],
            "actual_net_r": r["overall_actual"]["net_total_r"],
            "flip_net_r": r["overall_flip"]["net_total_r"],
        }
        print(f"   {r['verdict']} wf={r['walkforward']} "
              f"flip%={r['flip_pct']} net {r['overall_actual']['net_total_r']}"
              f" -> {r['overall_flip']['net_total_r']}", flush=True)

    geometry = tp_geometry_for(fams_seen, a.tp_cap_pct)
    (out_dir / "verdicts.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         # ALWAYS stamped, including the 0 case. An absent key is what the
         # roll-up's `geometry_undeclared` bucket exists to catch, and this
         # driver previously stamped nothing at all — "there is not even a
         # field to check", which is why its cutover is a sentinel and not a
         # date. Removing the `regime_flip_exit` entry from
         # `m20_coverage_rollup.LEVER_GEOMETRY_CUTOVER` is what marks this
         # fixed, and that must wait for a real re-sweep to land: the harness
         # can now produce a live-parity book, and NO committed cell was
         # measured on one yet.
         "tp_cap_pct": a.tp_cap_pct,
         "tp_geometry": geometry,
         "families": sorted(fams_seen),
         "verdicts": verdicts}, indent=1))
    if a.tp_cap_pct <= 0.0:
        print("WARNING: tp_cap_pct=0 — the donchian/pullback books this "
              "replayed model NO TAKE-PROFIT, which is not the geometry the "
              "live units place. Verdicts are conditioned on that.", flush=True)
    print(f"done -> {out_dir} | tp_geometry: {geometry}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
