#!/usr/bin/env python3
"""Count the harness trades a set of legs emit per calendar year — NOTHING ELSE.

WHY THIS EXISTS (RQ-20260928-005 follow-up, PI-20260929-FCJRWVAK-0001)
-----------------------------------------------------------------------
A per-year walk-forward's FINAL fold is graded on n_oos >= 64. Whether a leg can
reach that depends on how many trades the harness emits in the latest calendar
year — a number that must be MEASURED on the candle file a round will actually
read, BEFORE the unit is registered, and WITHOUT running the statistic. A full
`m20_exit_head_round.py` round trains the head and replays the tau policy, i.e.
it produces the registered statistic; running it to "see the counts" would be a
run before registration. This script stops after the harness emit step.

It reuses the round's own config-exact resolution (`resolve_data`, `base_args`,
the family harness, `--strategy-name`, TP cap 0.099 = live parity) so the counts
are the counts the round would see. It trains nothing and writes no registry.

Usage (on the trainer, `.venv` active)::

    python scripts/research/exit_head_leg_trade_census.py \\
        --legs ict_scalp_sol_15m,ict_scalp_xrp_15m,ict_scalp_eth_15m --tf 15m \\
        --data-dir /home/ubuntu/refresh_15m_20260929 --out /tmp/eh_census
"""
# wiring: manual-only - a pre-registration feasibility census, run by hand via a
# trainer-vm-diag issue; named in PI-20260929-FCJRWVAK-0001's `origin.rerun`.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))
from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS, base_args, classify, resolve_data)


def count_by_year(emit_path: Path) -> dict:
    """Trades per calendar year of `entry_time`; unreadable rows are COUNTED, not dropped."""
    years: Counter = Counter()
    unreadable = 0
    for line in emit_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line).get("entry_time")
            y = (datetime.fromisoformat(str(t).replace("Z", "+00:00")).year
                 if isinstance(t, str) else datetime.fromtimestamp(float(t), timezone.utc).year)
            years[y] += 1
        except (ValueError, TypeError, OverflowError):
            unreadable += 1
    return {"by_year": dict(sorted(years.items())), "total": sum(years.values()),
            "unreadable_rows": unreadable}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--legs", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tp-cap-pct", type=float, default=0.099,
                    help="live-parity TP cap, the round's own default")
    a = ap.parse_args(argv[1:])
    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
                  or {}).get("strategies") or {}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {}
    rc = 0
    for leg in a.legs.split(","):
        cfg = strategies.get(leg)
        fam = classify(leg) if isinstance(cfg, dict) else None
        if fam is None or fam not in FAMILY_HARNESS:
            result[leg] = {"state": "skipped", "why": "not in strategies.yaml / no harness family"}
            rc = 1
            continue
        sym = (cfg.get("symbols") or [None])[0]
        data, proxy, resample = resolve_data(str(sym), a.tf, Path(a.data_dir), prefer_native=True)
        if data is None or proxy:
            result[leg] = {"state": "skipped", "why": f"data_missing_or_proxy:{sym}"}
            rc = 1
            continue
        emit = out / f"{leg}.jsonl"
        args = base_args(leg, cfg, fam, data, resample, a.tp_cap_pct)
        p = subprocess.run([sys.executable, str(REPO / FAMILY_HARNESS[fam]), *args,
                            "--strategy-name", leg, "--emit-trades", str(emit),
                            "--json", str(out / f"{leg}.summary.json")],
                           capture_output=True, text=True, timeout=3600)
        if p.returncode != 0 or not emit.exists():
            result[leg] = {"state": "harness_failed", "tail": (p.stderr or p.stdout)[-300:]}
            rc = 1
            continue
        result[leg] = {"state": "counted", "data": data, **count_by_year(emit)}
    print(json.dumps(result, indent=1, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
