#!/usr/bin/env python3
"""Research-sweep orchestrator — study-spec-driven variation testing + signal
isolation (RESEARCH-ONLY, Tier-1).

Generalizes ``scripts/ops/recombination_sweep.py`` from "swap whole primitives"
to "**run an arbitrary matrix of config variations AND ablate individual
components**", so a single study answers BOTH:

  * which variation tiers best (the leaderboard), and
  * which *component* creates the edge (the attribution table) — by running the
    base config with one component disabled and reporting the marginal
    contribution ``Δ = base − ablated``.

It is pure glue over proven parts — the standalone backtest harnesses
(``--emit-trades``), the k-fold gate (``scripts/ops/m15_ws_b_fold_report.py``),
and the readiness tier (``scripts/ops/classify_strategy_tier.py``). No new
statistics, no new evaluation rubric.

**Tier-1 research tooling. Runs on the TRAINER VM (autonomous); writes NOTHING
to live** — never touches the order path, ``config/strategies.yaml``,
``config/accounts.yaml``, or any unit the live VM consumes. Survivors are
*proposed* via the normal Tier-3 PR.

Design: docs/research/research-framework-DESIGN.md

Study spec (``config/research/studies/<name>.yaml``)::

    schema_version: 1
    name: eth_pullback_adx
    harness: backtest_pullback.py
    base:                       # the reference cell
      data: data/ETHUSDT_15m.csv
      resample: 2h
      symbol: ETHUSDT
      args: {trend-lookback: 40, pullback-lookback: 10, pullback-frac: 0.5,
             atr-stop-mult: 2.5, trail-mult: 5.0, adx-min: 25, min-confidence: 0.0}
    variants:                   # sweep cells — each a delta vs base.args
      - {name: adx_none, drop: [adx-min]}
      - {name: adx20,    set: {adx-min: 20}}
      - {name: trail3,   set: {trail-mult: 3.0}}
    ablations:                  # component-off — Δ-vs-base attribution
      - {name: regime_gate, drop: [adx-min, adx-max]}
      - {name: confidence_floor, set: {min-confidence: 0.0}}
    kfold: {folds: 5, train_frac: 0.4, wf_end: "2026-06-11"}
    fees:  {base_bps: 7.5, double_bps: 15.0}

Usage::

    python3 scripts/ops/research_sweep.py --study config/research/studies/eth_pullback_adx.yaml
    python3 scripts/ops/research_sweep.py --study <spec> --dry-run     # enumerate runs only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"

sys.path.insert(0, str(_SCRIPTS / "ops"))
try:
    from classify_strategy_tier import classify_tier as _classify_tier
except Exception:  # pragma: no cover - repo-local, should import
    _classify_tier = None


@dataclass
class Run:
    """One enumerated run: a base config overlaid with a delta."""
    name: str
    kind: str                       # "base" | "variant" | "ablation"
    data: str
    resample: str
    symbol: str
    args: Dict[str, Any] = field(default_factory=dict)


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return str(v)
    f = float(v) if isinstance(v, (int, float)) else None
    if f is not None:
        return str(int(f)) if f == int(f) else str(f)
    return str(v)


def _apply_delta(base_args: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    """Return base_args with delta applied: ``drop:`` removes keys, ``set:`` overrides."""
    out = dict(base_args)
    for key in delta.get("drop", []) or []:
        out.pop(key, None)
    for key, val in (delta.get("set", {}) or {}).items():
        out[key] = val
    return out


def build_runs(study: Dict[str, Any]) -> List[Run]:
    """Enumerate the base + variant + ablation runs (pure, unit-testable)."""
    base = study["base"]
    base_args = dict(base.get("args", {}) or {})
    common = dict(data=base["data"], resample=str(base["resample"]), symbol=base["symbol"])
    runs: List[Run] = [Run(name="base", kind="base", args=base_args, **common)]
    for v in study.get("variants", []) or []:
        runs.append(Run(name=v["name"], kind="variant",
                        args=_apply_delta(base_args, v), **common))
    for ab in study.get("ablations", []) or []:
        runs.append(Run(name=ab["name"], kind="ablation",
                        args=_apply_delta(base_args, ab), **common))
    return runs


def _args_to_flags(args: Dict[str, Any]) -> List[str]:
    """Convert an args dict ({'adx-min': 25}) to CLI flags (['--adx-min','25'])."""
    out: List[str] = []
    for key, val in args.items():
        if val is None:
            continue
        flag = f"--{key}"
        if isinstance(val, bool):           # store_true style flags
            if val:
                out.append(flag)
        else:
            out += [flag, _fmt(val)]
    return out


def _harness_cmd(study: Dict[str, Any], run: Run, fee_bps: float,
                 emit_path: Path, json_path: Path) -> List[str]:
    data = run.data if Path(run.data).is_absolute() else str(_REPO_ROOT / run.data)
    return [
        sys.executable, str(_SCRIPTS / study["harness"]),
        "--data", data, "--resample", run.resample, "--symbol", run.symbol,
        "--fee-bps-roundtrip", _fmt(fee_bps),
        "--emit-trades", str(emit_path), "--json", str(json_path),
    ] + _args_to_flags(run.args)


def _data_start(csv_path: Path) -> str:
    with open(csv_path, encoding="utf-8") as fh:
        fh.readline()
        first = fh.readline().strip()
    return first.split(",", 1)[0].split("T")[0].split(" ")[0]


def _fold_report_cmd(study: Dict[str, Any], run: Run, emit_base: Path,
                     emit_double: Path, fold_json: Path) -> List[str]:
    kf = study.get("kfold", {}) or {}
    fees = study.get("fees", {}) or {}
    data = Path(run.data if Path(run.data).is_absolute() else _REPO_ROOT / run.data)
    return [
        sys.executable, str(_SCRIPTS / "ops" / "m15_ws_b_fold_report.py"),
        "--mode", "net", "--emit", str(emit_base), "--emit-2x", str(emit_double),
        "--fee-bps", _fmt(fees.get("base_bps", 7.5)),
        "--wf-start", _data_start(data),
        "--wf-end", str(kf.get("wf_end", "2026-06-11")),
        "--folds", str(kf.get("folds", 5)),
        "--train-frac", str(kf.get("train_frac", 0.4)),
        "--label", run.name, "--json", str(fold_json),
    ]


def _tier(report: Dict[str, Any]) -> Optional[str]:
    if report.get("tier"):
        return report["tier"]
    if _classify_tier is None:
        return None
    try:
        return _classify_tier(report)["tier"]
    except Exception:
        return None


def run_one(study: Dict[str, Any], run: Run, out_dir: Path) -> Optional[Dict[str, Any]]:
    """Execute one run end-to-end (harness ×2 fees → fold-report → tier)."""
    fees = study.get("fees", {}) or {}
    base_bps, double_bps = fees.get("base_bps", 7.5), fees.get("double_bps", 15.0)
    tdir = out_dir / run.name
    tdir.mkdir(parents=True, exist_ok=True)
    emit_base, emit_double = tdir / "trades_base.jsonl", tdir / "trades_double.jsonl"
    for fee, emit in ((base_bps, emit_base), (double_bps, emit_double)):
        cmd = _harness_cmd(study, run, fee, emit, tdir / f"summary_{_fmt(fee)}.json")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not emit.exists():
            print(f"RUN_FAILED {run.name} fee={_fmt(fee)}: rc={proc.returncode} "
                  f"{proc.stderr.strip()[:200]}", file=sys.stderr)
            return None
    fold_json = tdir / "fold.json"
    proc = subprocess.run(_fold_report_cmd(study, run, emit_base, emit_double, fold_json),
                          capture_output=True, text=True)
    if proc.returncode != 0 or not fold_json.exists():
        print(f"RUN_FAILED {run.name} fold-report: rc={proc.returncode} "
              f"{proc.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        report = json.loads(fold_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"RUN_FAILED {run.name}: bad fold JSON ({exc})", file=sys.stderr)
        return None
    return {
        "name": run.name, "kind": run.kind, "symbol": run.symbol,
        "tier": _tier(report),
        "net_r_base": report.get("total_oos_net_r_base"),
        "net_r_double": report.get("total_oos_net_r_double"),
        "all_folds_positive": report.get("gate_all_folds_positive"),
        "args": run.args,
    }


def _attribution(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """For each ablation row, Δ vs base = how much the dropped component adds."""
    base = next((r for r in rows if r["kind"] == "base"), None)
    if base is None or not isinstance(base.get("net_r_base"), (int, float)):
        return []
    out = []
    for r in rows:
        if r["kind"] != "ablation" or not isinstance(r.get("net_r_base"), (int, float)):
            continue
        out.append({
            "component": r["name"],
            "delta_net_r": round(base["net_r_base"] - r["net_r_base"], 2),
            "base_net_r": base["net_r_base"], "ablated_net_r": r["net_r_base"],
            "base_tier": base["tier"], "ablated_tier": r["tier"],
        })
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study", required=True, help="path to a study-spec YAML")
    ap.add_argument("--out", default=None, help="output dir (default results/studies/<name>)")
    ap.add_argument("--dry-run", action="store_true", help="enumerate runs; execute nothing")
    args = ap.parse_args(argv)

    study_path = Path(args.study)
    if not study_path.is_absolute():
        study_path = _REPO_ROOT / study_path
    study = yaml.safe_load(study_path.read_text(encoding="utf-8"))
    runs = build_runs(study)

    if args.dry_run:
        print(f"# study '{study.get('name')}' — {len(runs)} run(s), harness={study['harness']}")
        for r in runs:
            print(f"  [{r.kind:<9}] {r.name:<22} {r.symbol} {r.resample}  args={r.args}")
        return 0

    out_dir = Path(args.out) if args.out else _REPO_ROOT / "results" / "studies" / str(study.get("name", "study"))
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for i, run in enumerate(runs, 1):
        print(f"=== [{i}/{len(runs)}] {run.kind}:{run.name} ===", file=sys.stderr)
        row = run_one(study, run, out_dir)
        if row is not None:
            rows.append(row)

    attribution = _attribution(rows)
    out = {"study": study.get("name"), "harness": study["harness"],
           "runs": rows, "attribution": attribution}
    (out_dir / "summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    # leaderboard (tier then net_r)
    order = {"live_ready": 3, "paper_ready": 2, "reject": 1, "backtest_only": 0, None: -1}
    print(f"\n# study '{study.get('name')}' — {len(rows)}/{len(runs)} runs -> {out_dir/'summary.json'}")
    print(f"{'kind':<10} {'name':<22} {'tier':<12} {'net':>9} {'2x':>9} {'folds+':>7}")
    for r in sorted(rows, key=lambda x: (-order.get(x["tier"], -1),
                                         -(x["net_r_base"] if isinstance(x["net_r_base"], (int, float)) else -1e9))):
        net = r["net_r_base"]
        dbl = r["net_r_double"]
        ns = f"{net:.2f}" if isinstance(net, (int, float)) else "—"
        ds = f"{dbl:.2f}" if isinstance(dbl, (int, float)) else "—"
        print(f"{r['kind']:<10} {r['name']:<22} {str(r['tier']):<12} {ns:>9} {ds:>9} {str(r['all_folds_positive']):>7}")

    if attribution:
        print("\n# ATTRIBUTION (Δnet_r = base − ablated; +ve = component helps):")
        for a in sorted(attribution, key=lambda x: -x["delta_net_r"]):
            print(f"  {a['component']:<22} Δ={a['delta_net_r']:+8.2f}R  "
                  f"(base {a['base_net_r']} [{a['base_tier']}] → ablated {a['ablated_net_r']} [{a['ablated_tier']}])")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
