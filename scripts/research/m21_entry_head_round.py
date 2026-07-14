#!/usr/bin/env python3
"""M21 E-3 — fleet P_win entry-head round driver.

One reproducible command per round: for every runnable leg of the selected
family (config-exact from config/strategies.yaml, same resolvers as the
M20/M21 fleet sweeps), it

  1. re-runs the harness with ``--emit-trades`` (the E-3 emits must carry
     the new ``confidence`` field — pre-E-3 emits lack it),
  2. pools the emits per ``(family, timeframe)`` group and builds the
     E0 dataset (``build_exit_head_dataset.py`` — post-E-3 rows carry
     first_touch_1r / reaches_2r / entry_confidence),
  3. trains + replays the entry head (``train_entry_head.py``).

Emit rows are re-stamped with the LEG name as ``strategy`` so two legs on
the same symbol/timeframe can never collide on ``trade_key``.

Tier-1 research tooling. Run on the trainer, detached:
  nohup .venv/bin/python3 scripts/research/m21_entry_head_round.py \
      --data-dir ~/ict-trading-bot/data --family donchian \
      --out ~/ict-trading-bot/runtime_logs/m21_entry_head >/tmp/e3.log 2>&1 &
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


def run(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)[-2000:]


# family_of (builder-side) pools rows by the emit's strategy name, so the
# restamped per-leg name must STILL classify into the harness family — a
# leg name like ``mgc_trend_1h`` or ``iwm_trend_long_1d`` doesn't (it
# neither contains "donchian" nor starts with "trend_"), which stranded
# those legs in per-leg family dirs on the 2026-07-14 first run (the 1d
# group's train step 404'd on the pooled rows.jsonl).
FAM_PREFIX = {"donchian": "trend_", "pullback": "pullback_",
              "squeeze": "squeeze_", "fade": "fade_"}


def _family_of(strategy: str) -> str:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_exit_head_dataset", REPO / "scripts/ml/build_exit_head_dataset.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.family_of(strategy)


def restamp(emit: Path, leg: str, fam: str) -> int:
    """Rewrite the emit's ``strategy`` to a unique per-leg name that still
    pools into ``fam`` under the builder's family_of (trade_key safety +
    correct family pooling)."""
    name = leg if _family_of(leg) == fam else f"{FAM_PREFIX[fam]}{leg}"
    rows = []
    for line in emit.open():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        r["strategy"] = name
        rows.append(r)
    emit.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return len(rows)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m21_entry_head"))
    ap.add_argument("--family", default="donchian",
                    help="harness family to run (donchian round first)")
    ap.add_argument("--only", default=None, help="CSV of leg names (debug)")
    ap.add_argument("--tf", default=None,
                    help="CSV of timeframes to restrict to (rerun one group)")
    ap.add_argument("--priority", default="trend_donchian_eth",
                    help="CSV of legs run first within each group")
    ap.add_argument("--db", default=None,
                    help="optional trade_journal.db for live validation")
    ap.add_argument("--min-fold-trades", type=int, default=50)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    only = set(a.only.split(",")) if a.only else None
    prio = [s for s in (a.priority or "").split(",") if s]
    data_dir = Path(a.data_dir)
    run_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")

    groups: dict[tuple[str, str], list[dict]] = {}
    skipped = []
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or (only and name not in only):
            continue
        fam = classify(name)
        if fam != a.family or fam not in FAMILY_HARNESS:
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        if a.tf and tf not in a.tf.split(","):
            continue
        data, proxy, resample = resolve_data(str(sym), tf, data_dir)
        if data is None:
            skipped.append({"leg": name, "reason": f"data_missing:{sym}"})
            continue
        groups.setdefault((fam, tf), []).append(
            {"leg": name, "symbol": sym, "tf": tf, "proxy": proxy,
             "data": data, "resample": resample,
             "args": base_args(name, cfg, fam, data, resample)})
    for legs in groups.values():
        legs.sort(key=lambda p: (p["leg"] not in prio, p["leg"]))

    print(f"plan: {sum(len(v) for v in groups.values())} legs in "
          f"{len(groups)} (family, tf) groups; {len(skipped)} skipped")
    if a.list:
        for (fam, tf), legs in sorted(groups.items()):
            print(f"  {fam}@{tf}: {[p['leg'] for p in legs]}")
        return 0

    harness = str(REPO / FAMILY_HARNESS[a.family])
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "family": a.family, "skipped": skipped, "groups": {}}
    for (fam, tf), legs in sorted(groups.items()):
        gtag = f"{fam}_{tf}"
        emits_dir = run_dir / "emits" / gtag
        emits_dir.mkdir(parents=True, exist_ok=True)
        trades_args: list[str] = []
        candle_args: list[str] = []
        seen_syms: set[str] = set()
        g = {"legs": {}, "dataset": None, "report": None}
        for p in legs:
            emit = emits_dir / f"{p['leg']}.jsonl"
            rc, tail = run([sys.executable, harness, *p["args"],
                            "--emit-trades", str(emit),
                            "--json", str(emits_dir / f"{p['leg']}.json")])
            if rc != 0 or not emit.exists():
                g["legs"][p["leg"]] = {"error": tail[-300:]}
                print(f"  EMIT FAIL {p['leg']}: {tail[-120:]}", flush=True)
                continue
            n = restamp(emit, p["leg"], fam)
            g["legs"][p["leg"]] = {"trades": n, "proxy": p["proxy"]}
            print(f"  emit {p['leg']}: {n} trades", flush=True)
            trades_args += ["--trades", str(emit)]
            if p["symbol"] not in seen_syms:
                seen_syms.add(p["symbol"])
                candle_args += ["--candles", f"{p['symbol']}={p['data']}"]
        if not trades_args:
            summary["groups"][gtag] = g
            continue
        ds_dir = run_dir / "ds" / gtag
        cmd = [sys.executable, str(REPO / "scripts/ml/build_exit_head_dataset.py"),
               *trades_args, *candle_args, "--tf", tf, "--out", str(ds_dir)]
        if a.db:
            cmd += ["--db", a.db]
        rc, tail = run(cmd, timeout=3600)
        if rc != 0:
            g["dataset"] = {"error": tail[-300:]}
            summary["groups"][gtag] = g
            print(f"  BUILD FAIL {gtag}: {tail[-120:]}", flush=True)
            continue
        fam_dir = ds_dir / fam
        g["dataset"] = str(fam_dir)
        rc, tail = run([sys.executable, str(REPO / "scripts/ml/train_entry_head.py"),
                        "--family-dir", str(fam_dir),
                        "--min-fold-trades", str(a.min_fold_trades)],
                       timeout=3600)
        print(tail, flush=True)
        g["report"] = (str(fam_dir / "entry_head_report.json")
                       if rc == 0 else {"error": tail[-300:]})
        summary["groups"][gtag] = g

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "round_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"done -> {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
