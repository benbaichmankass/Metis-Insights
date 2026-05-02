"""Orchestrator for autonomous training runs (GitHub Actions entry point).

Loads experiments/<run-id>/hypotheses.py, which must define:
  HYPOTHESES = [(id, fn), ...]   # ordered list of hypothesis callables
  setup(ctx)  (optional)         # populates ctx with shared state (candles, etc.)

Each hypothesis fn(ctx) returns a dict like
  {'metrics': {...}, 'baseline_metrics': {...}, 'summary_md': str}.

Writes per-hypothesis metrics.json + summary.md, aggregates SUMMARY.md.
The .github/workflows/training-run.yml step takes over for git commit + PR open.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import time
import traceback


def _safe_run(hid, fn, ctx, results_dir, results, failures, t0, max_hours):
    if (time.time() - t0) / 3600 > max_hours:
        failures[hid] = "wall-clock budget exhausted"
        print(f"[{hid}] SKIPPED — wall-clock exhausted")
        return
    print(f"[{hid}] running ({round(max_hours - (time.time() - t0) / 3600, 2)}h left)")
    d = results_dir / hid
    d.mkdir(parents=True, exist_ok=True)
    try:
        out = fn(ctx)
        results[hid] = out
        (d / "metrics.json").write_text(json.dumps(out.get("metrics", {}), indent=2))
        (d / "summary.md").write_text(out.get("summary_md", f"# {hid}"))
    except Exception:
        failures[hid] = traceback.format_exc()
        (d / "FAILURE.md").write_text("```\n" + failures[hid] + "\n```")
        print(f"[{hid}] FAILED:\n{failures[hid]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-hours", type=float, default=5.5)
    args = parser.parse_args()

    run_dir = pathlib.Path(f"experiments/{args.run_id}")
    hyp_file = run_dir / "hypotheses.py"
    if not hyp_file.exists():
        raise SystemExit(f"Missing {hyp_file}")

    sys.path.insert(0, str(pathlib.Path.cwd()))
    spec = importlib.util.spec_from_file_location(f"hyp_{args.run_id}", hyp_file)
    hyp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hyp)

    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = results_dir / "_cache"
    cache_dir.mkdir(exist_ok=True)

    ctx: dict = {"cache_dir": cache_dir, "run_id": args.run_id}
    if hasattr(hyp, "setup"):
        hyp.setup(ctx)

    t0 = time.time()
    results: dict = {}
    failures: dict = {}
    for hid, fn in hyp.HYPOTHESES:
        _safe_run(hid, fn, ctx, results_dir, results, failures, t0, args.max_hours)

    lines = [
        f"# Training run {args.run_id} — summary", "",
        f"Wall-clock: {round((time.time() - t0) / 3600, 2)} h.", "",
        "| Hypothesis | Status | Key metric (variant vs baseline) |",
        "|---|---|---|",
    ]
    for hid, r in results.items():
        m = r.get("metrics", {})
        b = r.get("baseline_metrics", {})
        if "sharpe" in m:
            delta = f' (Δ sharpe {m["sharpe"] - b.get("sharpe", 0):+.2f})'
            lines.append(f'| {hid} | OK | sharpe={m["sharpe"]:.2f}{delta} |')
        elif "expectancy_r" in m:
            delta = f' (Δ E[R] {m["expectancy_r"] - b.get("expectancy_r", 0):+.3f})'
            lines.append(f'| {hid} | OK | E[R]={m["expectancy_r"]:.3f}{delta} |')
        else:
            lines.append(f"| {hid} | OK | see results/{hid}/metrics.json |")
    for hid in failures:
        lines.append(f"| {hid} | FAILED | see results/{hid}/FAILURE.md |")
    (results_dir / "SUMMARY.md").write_text("\n".join(lines))
    print("\n".join(lines))

    if failures:
        (results_dir / "_FAILURES").write_text("\n\n".join(f"=== {k} ===\n{v}" for k, v in failures.items()))


if __name__ == "__main__":
    main()
