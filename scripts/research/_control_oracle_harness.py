#!/usr/bin/env python3
"""CONTROL-ONLY harness wrapper — plants a KNOWN-TRUE effect so the M20 exit
sweep's detector can be tested for its ability to see one.

⚠️ THIS IS NOT A LEVER AND MUST NEVER BE SHIPPED, SWEPT AS A CANDIDATE, OR
ENTERED IN THE COVERAGE MATRIX. It is a measurement instrument's calibration
weight. The `loss_free` transform is physically impossible (it retroactively
deletes every losing trade); its only purpose is that its sign is known BEFORE
the run, which is what makes it a control.

WHY IT EXISTS (MI-145, object WO-20260906-POSITIVE-CONTROL-ON-THE-EXIT-SWEEP-320).
`docs/research/exit-refinement-coverage.json` carries 320 `honest_negative`
cells and nobody had shown that the sweep's gate can emit a positive at all.
This repo's own rule — "a negative needs a denominator; prove the probe can
find a positive before trusting that it is quiet" (docs/CLAUDE-RULES-CANONICAL.md
§ RULE ONE) — applied to the research harness rather than to a grep.

HOW IT PRESERVES THE THING UNDER TEST. It changes NO production file. It runs
the real harness as a subprocess with the real argv, reads the real per-trade
book out of `--emit-trades`, applies one declared transform to each trade's
realised net R, and re-derives `net_total_r` / `max_drawdown_r` with the SAME
arithmetic `_summarize` uses (cumulative sum in trade order; drawdown from the
running peak). Everything downstream — `run_cell`, `beats`, `walkforward`, the
verdict — is the sweep's own code, unmodified.

TRANSFORMS (declared a priori, never tuned):
  identity   net_r' = net_r
             A NULL control. Must reproduce the wrapped harness exactly, so a
             non-zero delta indicts the wrapper (or `run_cell`), not the lever.
  loss_free  net_r' = max(net_r, 0.0)
             THE POSITIVE control. Provably dominant, with no appeal to market
             behaviour: (a) every trade's R weakly improves, so `net_total_r`
             is >= base and is STRICTLY greater whenever the base book contains
             one losing trade; (b) every R is >= 0, so the equity curve is
             monotone non-decreasing and `max_drawdown_r` is exactly 0.0, which
             is <= any base drawdown. `beats()` requires
             `cn >= bn and cd <= bd and (cn > bn or cd < bd)` — all three hold
             by construction on any base book with a loser. A sweep that grades
             this cell `is_oos_fail` is broken, and that is the whole point.

⚠️ `loss_free` is deliberately NOT an "exit at MFE" oracle. Exiting at MFE is
the intuitive control and it is NOT provably dominant on max-drawdown: the
drawdown term is path-dependent, so a book whose trades all improve can still
carry a larger drawdown, and the control would then fail for a reason that says
nothing about the detector. `loss_free` pins both terms of the gate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

TRANSFORMS = {
    "identity": lambda r: r,
    "loss_free": lambda r: max(r, 0.0),
}


def _take_flag(argv: list[str], flag: str) -> tuple[list[str], str | None]:
    """Remove `--flag VALUE` from argv and return (remaining, value)."""
    out: list[str] = []
    val: str | None = None
    i = 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            val = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out, val


def main(argv: list[str]) -> int:
    argv, transform = _take_flag(argv, "--control-transform")
    argv, harness = _take_flag(argv, "--control-harness")
    if transform not in TRANSFORMS:
        print(f"--control-transform must be one of {sorted(TRANSFORMS)}", file=sys.stderr)
        return 2
    if not harness:
        print("--control-harness <path> is required", file=sys.stderr)
        return 2
    argv, json_out = _take_flag(argv, "--json")
    if not json_out:
        print("--json <path> is required (this wrapper rewrites the summary)", file=sys.stderr)
        return 2
    # Strip any caller-supplied --emit-trades: this wrapper owns that channel.
    argv, _ = _take_flag(argv, "--emit-trades")

    fd, summ = tempfile.mkstemp(prefix="ctl_summary_", suffix=".json")
    os.close(fd)
    fd, trades = tempfile.mkstemp(prefix="ctl_trades_", suffix=".jsonl")
    os.close(fd)
    try:
        cmd = [sys.executable, str(REPO / harness), *argv,
               "--json", summ, "--emit-trades", trades]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            sys.stderr.write((p.stderr or p.stdout)[-2000:])
            return p.returncode
        out = json.loads(Path(summ).read_text())
        rows = [json.loads(x) for x in Path(trades).read_text().splitlines() if x.strip()]

        fn = TRANSFORMS[transform]
        net = [fn(float(r["net_r"])) for r in rows]
        cum = peak = mdd = 0.0
        for r in net:
            cum += r
            peak = max(peak, cum)
            mdd = max(mdd, peak - cum)
        out["net_total_r"] = round(sum(net), 4)
        out["max_drawdown_r"] = round(mdd, 4)
        out["total_trades"] = len(net)
        # Loud, un-missable provenance so no consumer can mistake a control run
        # for a measurement of a real lever.
        out["CONTROL_ORACLE"] = True
        out["control_transform"] = transform
        out.setdefault("params", {})["CONTROL_ORACLE_TRANSFORM"] = transform
        Path(json_out).write_text(json.dumps(out))
        return 0
    finally:
        for pth in (summ, trades):
            try:
                os.unlink(pth)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
