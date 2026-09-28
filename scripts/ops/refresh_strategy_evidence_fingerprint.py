#!/usr/bin/env python3
# wiring: manual-only — run by hand, once, when a session has already PROVEN
# (with a checked test, not an assumption) that a config edit changed a
# leg's raw YAML bytes but not its resolved parameters; never on a cron or
# from CI, since deciding "provably inert" is a judgment call this script
# does not and must not make for you.
"""Refresh a committed strategy-evidence record's `config_fingerprint` when a
config edit provably changed no resolved parameter.

`config_fingerprint` (scripts/ops/build_strategy_evidence.py /
scripts/ci/check_roster_promotion_evidence.py, byte-identical by contract)
digests a leg's raw YAML params so a promotion record can be tied to the
EXACT config it was measured against — "the fingerprint catches a config
that moved" (build_strategy_evidence.py's own docstring). A record whose
fingerprint no longer matches the live config is graded STALE.

That is correct behaviour for a config change that could change what the
harness measures. It is also unconditional: it fires on ANY byte diff in
the leg's block, including a genuinely inert one — e.g. removing a field
that a merge-with-defaults resolver (`_resolve_params` in
src/units/strategies/ict_scalp.py) fills back in identically either way,
which JC-CA-02 (docs/audits/code-audit-2026-09-27.md §6, CA-A05-001) proved
for `htf_trend_filter_enabled` on 7 ict_scalp legs — the resolved params
`order_package` actually runs on are byte-for-byte identical whether the
key is explicit or absent, so the harness run underlying the existing
evidence record measured the SAME leg either way.

This tool does NOT re-run any harness and does NOT touch any measured
field (net_r_oos, expectancy_r_oos, fold_detail, ...) — it only rewrites
`config_fingerprint` to match the current config, and only for legs named
on the command line. Never run it to launder an ACTUAL parameter change;
that is what re-running build_strategy_evidence.py is for. Use this only
when you can point to a specific, checked reason the resolved params are
unchanged (a test proving it, as here) — never on the strength of "should
be fine".

Usage:
    python3 scripts/ops/refresh_strategy_evidence_fingerprint.py \\
        --strategy ict_scalp_sol_5m --strategy ict_scalp_xrp_5m ... \\
        --reason "CA-A05-001: htf_trend_filter_enabled removed, proven inert"

Prints old -> new fingerprint per leg. Exits 1 if a named leg has no
config block or no committed record.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "comms" / "strategy_evidence"

sys.path.insert(0, str(REPO / "scripts" / "ci"))
import check_roster_promotion_evidence as guard  # noqa: E402


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strategy", action="append", required=True,
                     help="leg name; repeat for multiple")
    ap.add_argument("--reason", required=True,
                     help="why the resolved params are unchanged (for the printed audit line)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    import yaml  # noqa: PLC0415
    strategies = yaml.safe_load(
        (REPO / "config" / "strategies.yaml").read_text(encoding="utf-8")
    )["strategies"]

    rc = 0
    for leg in args.strategy:
        cfg = strategies.get(leg)
        if not isinstance(cfg, dict):
            print(f"ERROR: {leg!r} has no block in config/strategies.yaml", file=sys.stderr)
            rc = 1
            continue
        path = EVIDENCE_DIR / f"{leg}.json"
        if not path.is_file():
            print(f"ERROR: no committed record at {path}", file=sys.stderr)
            rc = 1
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        old = record.get("config_fingerprint")
        new = guard.config_fingerprint(cfg)
        if old == new:
            print(f"{leg}: already current ({new})")
            continue
        print(f"{leg}: {old} -> {new}  ({args.reason})")
        if not args.dry_run:
            record["config_fingerprint"] = new
            path.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n",
                             encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
