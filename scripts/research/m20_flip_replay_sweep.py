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
    FAMILY_HARNESS, base_args, classify, resolve_data)

POLICY_KEY = {"donchian": "trend_donchian", "pullback": "htf_pullback_trend_2h"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m20_flip_replay"))
    ap.add_argument("--only", default=None)
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    only = set(a.only.split(",")) if a.only else None
    data_dir = Path(a.data_dir)
    out_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    verdicts: dict = {}

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
               *base_args(name, cfg, fam, data, resample),
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
        verdicts[name] = {
            "proxy": proxy, "trades": r["trades"],
            "flip_pct": r["flip_pct"], "walkforward": r["walkforward"],
            "verdict": r["verdict"],
            "actual_net_r": r["overall_actual"]["net_total_r"],
            "flip_net_r": r["overall_flip"]["net_total_r"],
        }
        print(f"   {r['verdict']} wf={r['walkforward']} "
              f"flip%={r['flip_pct']} net {r['overall_actual']['net_total_r']}"
              f" -> {r['overall_flip']['net_total_r']}", flush=True)

    (out_dir / "verdicts.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "verdicts": verdicts}, indent=1))
    print(f"done -> {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
