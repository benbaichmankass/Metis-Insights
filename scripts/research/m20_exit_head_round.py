#!/usr/bin/env python3
"""M20 exit-head ROUND driver — one command per (family, tf) exit-head round.

Codifies the E0→E1 round the donchian-1h head went through (program doc
docs/research/M20-exit-head-PROGRAM.md; skill .claude/skills/exit-refinement)
so the remaining matrix rounds (4h donchians, 2h alt pullbacks, equities) are
one invocation each instead of hand-run stages:

  1. For each leg: resolve its family/harness/data/params CONFIG-EXACT from
     config/strategies.yaml (reusing m20_fleet_exit_sweep's resolvers) and run
     the harness with --emit-trades (the E0 volume source).
  2. One E0 build over all emitted trades at --tf
     (scripts/ml/build_exit_head_dataset.py; per-symbol candle CSVs threaded).
  3. One E1 train+τ-replay per produced family dir
     (scripts/ml/train_exit_head.py) — prints the gate verdict.

Advisory research tooling (Tier-1): never touches config or the registry;
E2/E3 graduation stays operator-gated. Run on the trainer, detached:
  nohup .venv/bin/python3 scripts/research/m20_exit_head_round.py \
      --legs trend_donchian_eth_4h,trend_donchian_sol_4h --tf 4h \
      --out runtime_logs/m20_exit_head/4h >/tmp/eh_round.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS, base_args, classify, resolve_data)


def sh(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, timeout=timeout)


_ACCEPTS_STRATEGY_NAME: dict[str, bool] = {}


def accepts_strategy_name(harness: str) -> bool:
    """Does this harness take `--strategy-name`? ASKED, not declared.

    This used to be the literal `fam == "scalp"`, correct on the day it was
    written and silently wrong the moment the trend and pullback harnesses
    gained the flag (2026-08-13) — a hardcoded capability list drifts exactly
    when someone adds the capability, which is the moment it matters. Probing
    `--help` costs one subprocess per harness per round and cannot go stale.

    A probe that FAILS is not a "no": a harness whose --help errors is a
    condition worth seeing, not a reason to quietly emit unattributable rows.
    It returns False (the old behaviour) and says so on stdout.
    """
    if harness in _ACCEPTS_STRATEGY_NAME:
        return _ACCEPTS_STRATEGY_NAME[harness]
    ok = False
    try:
        p = subprocess.run([sys.executable, str(REPO / harness), "--help"],
                           capture_output=True, text=True, timeout=120)
        if p.returncode == 0:
            ok = "--strategy-name" in (p.stdout or "")
        else:
            print(f"    !! {harness} --help exited {p.returncode}; assuming NO "
                  f"--strategy-name. Emitted rows will carry the harness's own "
                  f"family literal and per-leg attribution will be lost for it.",
                  flush=True)
    except Exception as exc:                     # noqa: BLE001 - probe only
        print(f"    !! {harness} --help probe failed ({type(exc).__name__}); "
              f"assuming NO --strategy-name.", flush=True)
    _ACCEPTS_STRATEGY_NAME[harness] = ok
    return ok


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legs", required=True, help="CSV of strategy leg names")
    ap.add_argument("--tf", required=True,
                    choices=["5m", "15m", "1h", "2h", "4h", "1d"])
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=None,
                    help="optional trade_journal.db for the live-source split")
    ap.add_argument("--target", default=None,
                    choices=["holding_pays", "peak_is_in"],
                    help="pass through to train_exit_head (P4.2)")
    ap.add_argument("--features", default=None, choices=["base", "extended"],
                    help="pass through to train_exit_head (P4.3)")
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    out = Path(a.out)
    (out / "emit").mkdir(parents=True, exist_ok=True)
    data_dir = Path(a.data_dir)

    emits: list[str] = []
    candles: dict[str, str] = {}
    for leg in a.legs.split(","):
        cfg = strategies.get(leg)
        if not isinstance(cfg, dict):
            print(f"SKIP {leg}: not in strategies.yaml", flush=True)
            continue
        fam = classify(leg)
        if fam is None or fam not in FAMILY_HARNESS:
            print(f"SKIP {leg}: no harness family", flush=True)
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        if tf != a.tf:
            print(f"SKIP {leg}: leg tf {tf} != round tf {a.tf}", flush=True)
            continue
        data, proxy, resample = resolve_data(str(sym), tf, data_dir)
        if data is None:
            print(f"SKIP {leg}: data_missing:{sym}", flush=True)
            continue
        if proxy:
            # Head training needs native data (matrix rule: proxy OK for
            # levers only) — refuse rather than silently train on a proxy.
            print(f"SKIP {leg}: proxy data ({sym}) — native history required "
                  "for head training", flush=True)
            continue
        emit = out / "emit" / f"{leg}.jsonl"
        args = base_args(leg, cfg, fam, data, resample)
        # Every harness stamped a HARDCODED family literal on each emitted row
        # -- `ict_scalp_5m`, `trend_donchian`, `htf_pullback_trend_2h` -- so the
        # E0 dataset, which buckets by that field, could not tell a 15m ETH
        # trade from a 5m XRP one, or `gld_pullback_1d` from `tlt_pullback_1h`.
        # Every per-leg verdict would have been attributed to one arbitrary leg.
        # The scalp harness was fixed first; trend and pullback followed
        # (2026-08-13), which is what makes the 26 non-scalp `exit_head_ml`
        # cells runnable at all.
        #
        # ASKED, not assumed -- see `accepts_strategy_name`. The old `fam ==
        # "scalp"` test was correct when written and would have silently kept
        # excluding trend/pullback after they gained the flag.
        if accepts_strategy_name(FAMILY_HARNESS[fam]):
            args = [*args, "--strategy-name", leg]
        else:
            print(f"    NOTE {leg}: {FAMILY_HARNESS[fam]} has no "
                  f"--strategy-name; its rows will carry the family literal and "
                  f"this leg's verdict will NOT be separately attributable.",
                  flush=True)
        p = sh([sys.executable, REPO / FAMILY_HARNESS[fam], *args,
                "--emit-trades", emit, "--json", "/tmp/eh_round_cell.json"])
        if p.returncode != 0:
            print(f"HARNESS FAIL {leg}: {(p.stderr or p.stdout)[-300:]}",
                  flush=True)
            continue
        n = sum(1 for _ in emit.open()) if emit.exists() else 0
        print(f"emitted {leg}: {n} trades", flush=True)
        if n:
            emits.append(str(emit))
            candles[str(sym)] = data

    if not emits:
        print("no emitted trades — nothing to build")
        return 1

    build_cmd = [sys.executable, REPO / "scripts/ml/build_exit_head_dataset.py",
                 "--tf", a.tf, "--out", out,
                 "--instruments", REPO / "config/instruments.yaml"]
    for e in emits:
        build_cmd += ["--trades", e]
    for sym, path in candles.items():
        build_cmd += ["--candles", f"{sym}={path}"]
    if a.db:
        build_cmd += ["--db", a.db]
    p = sh(build_cmd, timeout=21600)
    print(p.stdout[-2000:], p.stderr[-500:], flush=True)
    if p.returncode != 0:
        return 1

    report = {}
    for fam_dir in sorted(d for d in out.iterdir()
                          if d.is_dir() and (d / "rows.jsonl").exists()):
        train_cmd = [sys.executable, REPO / "scripts/ml/train_exit_head.py",
                     "--family-dir", fam_dir, "--tf", a.tf]
        if a.target:
            train_cmd += ["--target", a.target]
        if a.features:
            train_cmd += ["--features", a.features]
        p = sh(train_cmd, timeout=21600)
        print(p.stdout[-3000:], p.stderr[-500:], flush=True)
        e1 = fam_dir / "e1_report.json"
        if e1.exists():
            try:
                report[fam_dir.name] = json.loads(e1.read_text())
            except json.JSONDecodeError:
                pass
    (out / "round_report.json").write_text(json.dumps(
        {k: v for k, v in report.items()}, indent=1, default=str))
    print("round done ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
