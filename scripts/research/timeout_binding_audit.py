#!/usr/bin/env python3
"""timeout-binding audit — does the harness's bar-count force-close BIND?

BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES

THE CLASS. `scripts/backtest_trend.py` and `scripts/backtest_pullback.py` force-close
every trade at `min(entry_i + timeout_bars, n - 1)` with a default of 200
(`backtest_trend.py:982`, `backtest_pullback.py:961`). **No live unit implements a
bar-count exit for either family** — only `fvg_range_15m.py` and `fade_breakout_4h.py`
read `timeout_bars` at all, each from its own `_DEFAULTS`, and there is no generic
reader (verified by grep over `src/`, 2026-08-29). So live's effective timeout is
INFINITE and every trend/pullback verdict was measured under an exit production
does not have.

WHAT THIS SCRIPT ANSWERS, and why it is not the same question. "The harness models
an exit live lacks" is a statement about CODE. Whether that exit CHANGED ANY VERDICT
is a statement about DATA, and only the second one sizes the problem. The
`e35_bracket_geometry_sweep` grid happens to contain the control: it sweeps
`timeout_bars` over (24, 48, 96, 400) beside a base arm that runs at the harness
default. Comparing the base arm against `to400` at otherwise-identical geometry is a
direct test of whether the default ever bound.

THE TEST.
  base (timeout = harness default) vs to400, same (leg, tp_r, stop_mult, tp_cap):
    net_total_r differs  -> the default BOUND: at least one trade was force-closed
                            at the default that would still have been open at 400.
                            The leg's verdicts are CONTAMINATED.
    net_total_r identical -> no trade in that configuration ever reached the
                            default. The default is inert, so base == live's
                            infinity on this dimension, and the arm IS live-parity.

⚠️ A NEGATIVE NEEDS A DENOMINATOR, so the identical case is not trusted on its own:
`longest_moving_grid_point` reports the largest grid timeout that DOES move the leg,
which bounds max trade duration from below and proves the probe can find a positive
on that leg. A leg where NO grid point moves is reported `no_power`, never `clean` —
that would be "we did not look" wearing the label of "we looked and found nothing".

⚠️ `inert_equals_base` ROWS CARRY `net_total_r: null` AND ARE EXCLUDED, NOT ZEROED.
The sweep correctly declines to run a grid point equal to the base's own value and
reports null rather than a fabricated zero delta. Coercing that null to 0.0 compares
a real number against a manufactured one and invents a difference: the first run of
this audit did exactly that and produced 8 spurious single-cell findings, one on each
leg that has such a row. `skipped_inert` is reported so the exclusion is visible.

# wiring: manual-only - an on-demand measurement over a COMMITTED, static corpus
# (docs/research/e35-bracket-corpus.jsonl), not a gate and not a cadence. Re-run it
# when that corpus GAINS ROWS -- i.e. after an e35-bracket-sweep run lands new legs
# or a new timeout grid point -- because a leg absent from the corpus is ungraded on
# this axis, not clean. Deliberately NOT wired to CI: re-deriving a fixed answer from
# a fixed file on every push is waste, and inventing a schedule for it here would
# create a cadence with no owner. Its verdicts are carried durably in the coverage
# matrix's per-cell `timeout_binding` notes, so a reader never depends on a re-run.

Usage:
    python3 scripts/research/timeout_binding_audit.py            # table + summary
    python3 scripts/research/timeout_binding_audit.py --json     # machine-readable
    python3 scripts/research/timeout_binding_audit.py --self-test
Exit 0 always — this is a measurement, not a gate.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "docs" / "research" / "e35-bracket-corpus.jsonl"

# The sweep's own grid. `None` is the base arm = the harness default
# (200 for trend/pullback, 48 for squeeze — HARNESS_TIMEOUT_DEFAULT in
# e35_bracket_geometry_sweep.py). 400 is the reference arm.
GRID: tuple[Any, ...] = (24, 48, 96, None)
REFERENCE = 400
EPS = 1e-9


def _geometry_key(row: dict) -> tuple:
    """Everything about a cell EXCEPT its timeout, so the two arms pair up."""
    return (row["leg"], row.get("tp_r"), row.get("stop_mult"), row.get("tp_cap_pct"))


def load_rows(path: Path = CORPUS) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def audit(rows: list[dict]) -> dict[str, dict]:
    """Per leg: does the harness default bind, and does the probe have power?"""
    paired: dict[tuple, dict] = collections.defaultdict(dict)
    for row in rows:
        paired[_geometry_key(row)][row.get("timeout")] = row

    per_leg: dict[str, dict] = {}
    for key, arms in paired.items():
        leg = key[0]
        st = per_leg.setdefault(leg, {
            "leg": leg,
            "family": arms[next(iter(arms))]["family"],
            "timeframe": arms[next(iter(arms))]["timeframe"],
            "graded_pairs": 0, "identical": 0, "binding": 0,
            "skipped_inert": 0, "unpaired": 0,
            "max_abs_d_net_r": 0.0,
            "moves_at": {},
        })
        ref = arms.get(REFERENCE)
        if ref is None:
            st["unpaired"] += 1
            continue
        if ref.get("net_total_r") is None:
            st["skipped_inert"] += 1
            continue
        for point in GRID:
            other = arms.get(point)
            if other is None:
                continue
            if other.get("net_total_r") is None:
                # inert_equals_base — NOT a measurement. Never coerced to 0.0.
                if point is None:
                    st["skipped_inert"] += 1
                continue
            delta = other["net_total_r"] - ref["net_total_r"]
            moved = abs(delta) > EPS
            tally = st["moves_at"].setdefault(str(point), [0, 0])
            tally[0] += 1
            tally[1] += 1 if moved else 0
            if point is None:                     # the base arm — the finding
                st["graded_pairs"] += 1
                if moved:
                    st["binding"] += 1
                    st["max_abs_d_net_r"] = max(st["max_abs_d_net_r"], abs(delta))
                else:
                    st["identical"] += 1

    for st in per_leg.values():
        moving = [p for p, (_, mv) in st["moves_at"].items() if mv > 0]
        st["longest_moving_grid_point"] = (
            max((p for p in moving if p != "None"), key=lambda p: int(p), default=None)
        )
        if st["moves_at"].get("None", [0, 0])[1] > 0:
            st["verdict"] = "contaminated"
            st["max_trade_duration"] = "> harness default"
        elif st["longest_moving_grid_point"] is None:
            # No grid point moves at all: we cannot show the probe finds a positive.
            st["verdict"] = "no_power"
            st["max_trade_duration"] = "unknown"
        else:
            nxt = {"24": "48", "48": "96", "96": "harness default"}[
                st["longest_moving_grid_point"]]
            st["verdict"] = "clean"
            st["max_trade_duration"] = f"in ({st['longest_moving_grid_point']}, {nxt}] bars"
    return per_leg


def render(per_leg: dict[str, dict]) -> str:
    lines = [
        f"timeout-binding audit — {CORPUS.relative_to(REPO)}",
        "",
        f"{'leg':28s} {'fam':9s} {'tf':3s} {'pairs':>6s} {'bind':>5s} "
        f"{'longest-moving':>15s} {'max|d net_r|':>13s}  verdict",
    ]
    for leg in sorted(per_leg, key=lambda k: (per_leg[k]["family"],
                                              per_leg[k]["timeframe"], k)):
        s = per_leg[leg]
        lines.append(
            f"{leg:28s} {s['family']:9s} {s['timeframe']:3s} {s['graded_pairs']:6d} "
            f"{s['binding']:5d} {str(s['longest_moving_grid_point']):>15s} "
            f"{s['max_abs_d_net_r']:13.4f}  {s['verdict']}")
    counts = collections.Counter(s["verdict"] for s in per_leg.values())
    pairs = sum(s["graded_pairs"] for s in per_leg.values())
    bind = sum(s["binding"] for s in per_leg.values())
    inert = sum(s["skipped_inert"] for s in per_leg.values())
    lines += [
        "",
        f"POPULATION: {len(per_leg)} legs, {pairs} graded base-vs-to400 geometry pairs "
        f"({inert} excluded as inert_equals_base, never zeroed).",
        f"BINDING: {bind}/{pairs} pairs ({100 * bind / pairs:.1f}%) move when the "
        f"harness default is relaxed to {REFERENCE}.",
        "LEGS: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())),
    ]
    return "\n".join(lines)


def _self_test() -> None:
    """Assertions INSIDE the transform — the only self-verification that has
    historically worked in this repo (CLAUDE-RULES-CANONICAL § RULE ONE)."""
    # A null on either arm must be skipped, NOT coerced to 0.0.
    rows = [
        {"leg": "L", "family": "f", "timeframe": "1d", "tp_r": None, "stop_mult": 2.0,
         "tp_cap_pct": 0.099, "timeout": None, "net_total_r": None},
        {"leg": "L", "family": "f", "timeframe": "1d", "tp_r": None, "stop_mult": 2.0,
         "tp_cap_pct": 0.099, "timeout": 400, "net_total_r": 5.0},
    ]
    got = audit(rows)["L"]
    assert got["graded_pairs"] == 0, got
    assert got["binding"] == 0, got
    assert got["skipped_inert"] == 1, got
    assert got["verdict"] == "no_power", got     # never "clean"

    # A leg whose base differs from to400 is contaminated.
    rows = [
        {"leg": "M", "family": "f", "timeframe": "1h", "tp_r": None, "stop_mult": 2.0,
         "tp_cap_pct": 0.099, "timeout": t, "net_total_r": v}
        for t, v in ((None, 1.0), (400, 3.0), (96, 2.0))
    ]
    got = audit(rows)["M"]
    assert got["verdict"] == "contaminated", got
    assert got["binding"] == 1 and got["graded_pairs"] == 1, got
    assert abs(got["max_abs_d_net_r"] - 2.0) < 1e-9, got

    # Identical base/to400 with a moving shorter point is CLEAN with power.
    rows = [
        {"leg": "N", "family": "f", "timeframe": "1d", "tp_r": None, "stop_mult": 2.0,
         "tp_cap_pct": 0.099, "timeout": t, "net_total_r": v}
        for t, v in ((None, 4.0), (400, 4.0), (48, 1.0), (96, 4.0))
    ]
    got = audit(rows)["N"]
    assert got["verdict"] == "clean", got
    assert got["longest_moving_grid_point"] == "48", got

    # Identical everywhere = no power, NOT clean.
    rows = [
        {"leg": "P", "family": "f", "timeframe": "1d", "tp_r": None, "stop_mult": 2.0,
         "tp_cap_pct": 0.099, "timeout": t, "net_total_r": 4.0}
        for t in (None, 400, 96, 48, 24)
    ]
    assert audit(rows)["P"]["verdict"] == "no_power", audit(rows)["P"]
    print("timeout_binding_audit self-test: OK (4 cases)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--corpus", default=str(CORPUS))
    args = ap.parse_args()
    if args.self_test:
        _self_test()
        return 0
    per_leg = audit(load_rows(Path(args.corpus)))
    print(json.dumps(per_leg, indent=1, sort_keys=True) if args.json else render(per_leg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
