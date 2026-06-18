#!/usr/bin/env python3
"""Strategy-primitives recombination sweep orchestrator (RESEARCH-ONLY, Tier-1).

Reads the primitive pool (``config/research/recombination_pool.yaml``),
enumerates the *coherent* Cartesian product of the swept axes per entry family,
and — for each tuple — runs the standalone backtest harness at both the base
and double fee with ``--emit-trades``, then pipes the two emit logs through the
existing k-fold gate (``scripts/ops/m15_ws_b_fold_report.py`` → which stamps
``tier`` via ``classify_strategy_tier.py``). It collects one row per tuple and
writes ``summary.json`` (sorted by tier then net R) plus a tier table. The
``paper_ready`` / ``live_ready`` survivors are the actionable output — each one
opens an ``SRQ-…`` row and (on paper_ready) proposes a Tier-3 demo wire, the
PR #3941 pattern. It is pure glue over proven parts: no new statistics, no new
evaluation rubric.

**This is Tier-1 research tooling. It runs on the TRAINER VM (autonomous), and
writes NOTHING to live** — it never touches the live order path,
``config/strategies.yaml``, ``config/accounts.yaml``, or any unit the live VM
consumes. Survivors are *proposed* through the normal Tier-3 PR; this script
only produces evidence.

**v1 sweeps only the harness-exposed axes** (entry-trigger = which harness,
symbol, timeframe, ADX regime band, trail-distance exit, min-confidence
selectivity). Cross-family entry×exit recombination — one family's entry
geometry with another family's exit manager — is **Phase-3**: it needs the
harness refactor that exposes the exit manager as an injectable primitive (see
the pool YAML's ``_deferred`` block).

Design: docs/research/strategy-primitives-recombination-DESIGN.md (§3, §4, §7).

Usage:
    python3 scripts/ops/recombination_sweep.py --dry-run
    python3 scripts/ops/recombination_sweep.py --limit 2 \
        --pool config/research/recombination_pool.yaml --out results/recombination
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"

# Reuse the canonical readiness classifier (do NOT reimplement the rubric).
sys.path.insert(0, str(_SCRIPTS / "ops"))
try:
    from classify_strategy_tier import classify_tier as _classify_tier
except Exception:  # pragma: no cover - classifier is repo-local, should import
    _classify_tier = None


def _tier_from_report(report: Dict[str, Any]) -> Optional[str]:
    """Resolve the readiness tier for a fold-report JSON.

    Prefers the ``tier`` ``m15_ws_b_fold_report.py`` stamps itself, else
    classifies via the canonical ``classify_strategy_tier.classify_tier`` (which
    reads the per-fold ``folds_base_fee`` list and is robust to the report's
    scalar ``folds`` count since the 2026-06-18 fix). Never lets a tiering
    failure break the sweep.
    """
    if report.get("tier"):
        return report["tier"]
    if _classify_tier is None:
        return None
    try:
        return _classify_tier(report)["tier"]
    except Exception:  # never let tiering break the sweep
        return None


@dataclass
class Tuple_:
    """One enumerated recombination cell."""
    label: str
    entry: str          # pool entries key (e.g. "trend_donchian")
    harness: str        # harness filename (e.g. "backtest_trend.py")
    family: str         # "trend" / "pullback"
    symbol: str
    timeframe: str
    regime_filter: Dict[str, Any] = field(default_factory=dict)
    exit_trail: Dict[str, Any] = field(default_factory=dict)
    selectivity: Dict[str, Any] = field(default_factory=dict)


def _fmt_num(v: float) -> str:
    """Stable compact number for a label (20 -> '20', 5.0 -> '5.0')."""
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


def enumerate_tuples(pool: Dict[str, Any]) -> List[Tuple_]:
    """Enumerate the coherent Cartesian product of axes per entry family.

    Pure (no I/O) so it is unit-testable. Applies the pool's coherence mask:
    ``coherence.selectivity_by_family`` restricts which selectivity variants
    each family admits (pullback → base only); a family absent from the map
    admits all selectivity variants.
    """
    entries: Dict[str, Any] = pool["entries"]
    axes: Dict[str, Any] = pool["axes"]
    symbols: List[str] = list(axes["symbol"])
    tf_by_family: Dict[str, List[str]] = axes["timeframe"]
    regime_variants: List[Dict[str, Any]] = list(axes["regime_filter"])
    exit_variants: List[Dict[str, Any]] = list(axes["exit_trail"])
    selectivity_variants: List[Dict[str, Any]] = list(axes["selectivity"])

    coherence = pool.get("coherence", {}) or {}
    sel_by_family: Dict[str, List[str]] = coherence.get("selectivity_by_family", {}) or {}

    out: List[Tuple_] = []
    for entry_key, ent in entries.items():
        family = ent["family"]
        harness = ent["harness"]
        timeframes = tf_by_family.get(family, [])
        # Coherence mask: restrict selectivity variants for this family.
        allowed_sel_names = sel_by_family.get(family)
        if allowed_sel_names is None:
            sel_choices = selectivity_variants
        else:
            sel_choices = [s for s in selectivity_variants if s["name"] in allowed_sel_names]
        for symbol, tf, regime, exit_, sel in product(
            symbols, timeframes, regime_variants, exit_variants, sel_choices
        ):
            label = _build_label(entry_key, family, symbol, tf, regime, exit_, sel)
            out.append(Tuple_(
                label=label, entry=entry_key, harness=harness, family=family,
                symbol=symbol, timeframe=tf, regime_filter=regime,
                exit_trail=exit_, selectivity=sel,
            ))
    return out


def _build_label(entry_key: str, family: str, symbol: str, tf: str,
                 regime: Dict[str, Any], exit_: Dict[str, Any],
                 sel: Dict[str, Any]) -> str:
    """Stable label like ``trend_ETHUSDT_4h_adxmin20_trail5.0_conf0.0``.

    The ``family`` short-name leads (trend/pullback), then symbol, timeframe,
    the regime band (``none`` → ``adxnone``; an adx_min → ``adxmin<v>``), the
    trail multiple, and the confidence floor. Deterministic given the tuple.
    """
    regime_tok = "adxnone"
    if regime.get("adx_min") is not None and regime.get("adx_max") is not None:
        regime_tok = f"adx{_fmt_num(regime['adx_min'])}-{_fmt_num(regime['adx_max'])}"
    elif regime.get("adx_min") is not None:
        regime_tok = f"adxmin{_fmt_num(regime['adx_min'])}"
    elif regime.get("adx_max") is not None:
        regime_tok = f"adxmax{_fmt_num(regime['adx_max'])}"
    trail_tok = f"trail{_fmt_num(exit_.get('trail_mult', 0))}"
    conf_tok = f"conf{_fmt_num(sel.get('min_confidence', 0.0))}"
    return f"{family}_{symbol}_{tf}_{regime_tok}_{trail_tok}_{conf_tok}"


def _data_path(data_dir: Path, symbol: str, family: str) -> Optional[Path]:
    """Resolve the per-symbol candle CSV for a tuple.

    The WS-C alt panel uses ``data/<SYM>_15m.csv`` resampled to the family
    cadence (trend 4h / pullback 2h). BTCUSDT falls back to the base
    ``backtest_candles.csv`` so an in-sandbox smoke run works against the one
    file this environment ships. Returns None when no candle file exists (the
    alt CSVs live on the trainer VM).
    """
    candidates = [data_dir / f"{symbol}_15m.csv", data_dir / f"{symbol}_5m.csv"]
    if symbol == "BTCUSDT":
        candidates.append(data_dir / "backtest_candles.csv")
    for c in candidates:
        if c.exists():
            return c
    return None


def _resample_rule(timeframe: str) -> str:
    """Harness --resample rule for a timeframe token (e.g. '4h' -> '4h')."""
    return timeframe


def _harness_cmd(t: Tuple_, data: Path, fee_bps: float, emit_path: Path,
                 json_path: Path) -> List[str]:
    """Build the harness subprocess command for one tuple at one fee."""
    cmd = [
        sys.executable, str(_SCRIPTS / t.harness),
        "--data", str(data),
        "--resample", _resample_rule(t.timeframe),
        "--timeframe", t.timeframe,
        "--symbol", t.symbol,
        "--fee-bps-roundtrip", _fmt_num(fee_bps),
        "--emit-trades", str(emit_path),
        "--json", str(json_path),
    ]
    # Regime filter → --adx-min / --adx-max.
    if t.regime_filter.get("adx_min") is not None:
        cmd += ["--adx-min", _fmt_num(t.regime_filter["adx_min"])]
    if t.regime_filter.get("adx_max") is not None:
        cmd += ["--adx-max", _fmt_num(t.regime_filter["adx_max"])]
    if t.regime_filter.get("adx_period") is not None:
        cmd += ["--adx-period", _fmt_num(t.regime_filter["adx_period"])]
    # Exit-trail → --trail-mult.
    if t.exit_trail.get("trail_mult") is not None:
        cmd += ["--trail-mult", _fmt_num(t.exit_trail["trail_mult"])]
    # Selectivity → --min-confidence.
    if t.selectivity.get("min_confidence") is not None:
        cmd += ["--min-confidence", _fmt_num(t.selectivity["min_confidence"])]
    return cmd


def _fold_report_cmd(t: Tuple_, emit_base: Path, emit_double: Path,
                     fold_json: Path, pool: Dict[str, Any], wf_start: str) -> List[str]:
    """Build the m15_ws_b_fold_report.py command (net mode, base + 2x emit)."""
    kf = pool.get("kfold", {}) or {}
    fees = pool.get("fees", {}) or {}
    return [
        sys.executable, str(_SCRIPTS / "ops" / "m15_ws_b_fold_report.py"),
        "--mode", "net",
        "--emit", str(emit_base),
        "--emit-2x", str(emit_double),
        "--fee-bps", _fmt_num(fees.get("base_bps", 7.5)),
        "--wf-start", wf_start,
        "--wf-end", str(kf.get("wf_end", "2026-06-11")),
        "--folds", str(kf.get("folds", 5)),
        "--train-frac", str(kf.get("train_frac", 0.4)),
        "--label", t.label,
        "--json", str(fold_json),
    ]


def _data_start(csv_path: Path) -> str:
    """First-row UTC date of a candle CSV (wf-start, mirrors the shell driver)."""
    with open(csv_path, encoding="utf-8") as fh:
        fh.readline()  # header
        first = fh.readline().strip()
    ts = first.split(",", 1)[0]
    return ts.split("T")[0].split(" ")[0]


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def run_tuple(t: Tuple_, pool: Dict[str, Any], data_dir: Path,
              out_dir: Path) -> Optional[Dict[str, Any]]:
    """Run one tuple end-to-end (harness×2 → fold-report). Returns a row dict.

    Returns None and logs ``RUN_FAILED``/``SKIP`` on any failure so a single
    bad tuple never aborts the sweep.
    """
    data = _data_path(data_dir, t.symbol, t.family)
    if data is None:
        print(f"SKIP {t.label}: no candle CSV for {t.symbol} under {data_dir} "
              f"(alt CSVs live on the trainer VM)", file=sys.stderr)
        return None

    fees = pool.get("fees", {}) or {}
    base_bps = fees.get("base_bps", 7.5)
    double_bps = fees.get("double_bps", 15.0)
    tdir = out_dir / t.label
    tdir.mkdir(parents=True, exist_ok=True)
    emit_base = tdir / "trades_base.jsonl"
    emit_double = tdir / "trades_double.jsonl"

    for fee, emit in ((base_bps, emit_base), (double_bps, emit_double)):
        cmd = _harness_cmd(t, data, fee, emit, tdir / f"summary_fee{_fmt_num(fee)}.json")
        proc = _run(cmd)
        if proc.returncode != 0 or not emit.exists():
            print(f"RUN_FAILED {t.label} (fee={_fmt_num(fee)}): "
                  f"rc={proc.returncode} {proc.stderr.strip()[:200]}", file=sys.stderr)
            return None

    wf_start = _data_start(data)
    fold_json = tdir / "fold.json"
    fr_cmd = _fold_report_cmd(t, emit_base, emit_double, fold_json, pool, wf_start)
    proc = _run(fr_cmd)
    if proc.returncode != 0 or not fold_json.exists():
        print(f"RUN_FAILED {t.label} (fold-report): "
              f"rc={proc.returncode} {proc.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        report = json.loads(fold_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"RUN_FAILED {t.label}: bad fold JSON ({exc})", file=sys.stderr)
        return None

    return {
        "label": t.label,
        "entry": t.entry,
        "family": t.family,
        "symbol": t.symbol,
        "timeframe": t.timeframe,
        "regime_filter": t.regime_filter.get("name"),
        "exit_trail": t.exit_trail.get("name"),
        "selectivity": t.selectivity.get("name"),
        "tier": _tier_from_report(report),
        "verdict": report.get("verdict"),
        "total_oos_net_r_base": report.get("total_oos_net_r_base"),
        "total_oos_net_r_double": report.get("total_oos_net_r_double"),
        "data": str(data.relative_to(_REPO_ROOT)) if _REPO_ROOT in data.parents else str(data),
    }


_TIER_ORDER = {"live_ready": 3, "paper_ready": 2, "reject": 1, "backtest_only": 0, None: -1}


def _sort_key(row: Dict[str, Any]):
    tier_rank = _TIER_ORDER.get(row.get("tier"), -1)
    net = row.get("total_oos_net_r_base")
    net = net if isinstance(net, (int, float)) else float("-inf")
    return (-tier_rank, -net)


def _print_table(rows: List[Dict[str, Any]]) -> None:
    """Tier table mirroring classify_strategy_tier.py's style."""
    print(f"{'tier':<13} {'label':<44} {'net':>9} {'2x':>9} {'verdict':>8}")
    for row in sorted(rows, key=_sort_key):
        net = row.get("total_oos_net_r_base")
        dbl = row.get("total_oos_net_r_double")
        net_s = f"{net:.2f}" if isinstance(net, (int, float)) else "—"
        dbl_s = f"{dbl:.2f}" if isinstance(dbl, (int, float)) else "—"
        print(f"{str(row.get('tier')):<13} {row['label']:<44} "
              f"{net_s:>9} {dbl_s:>9} {str(row.get('verdict')):>8}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default="config/research/recombination_pool.yaml")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="results/recombination")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap the number of tuples (smoke run).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Enumerate + print the coherent tuples; run nothing.")
    args = ap.parse_args(argv)

    pool_path = Path(args.pool)
    if not pool_path.is_absolute():
        pool_path = _REPO_ROOT / pool_path
    pool = yaml.safe_load(pool_path.read_text(encoding="utf-8"))

    tuples = enumerate_tuples(pool)
    if args.limit is not None:
        tuples = tuples[: args.limit]

    if args.dry_run:
        print(f"# {len(tuples)} coherent tuple(s) from {args.pool}")
        for t in tuples:
            print(f"  {t.label}  [entry={t.entry} harness={t.harness} "
                  f"regime={t.regime_filter.get('name')} exit={t.exit_trail.get('name')} "
                  f"sel={t.selectivity.get('name')}]")
        return 0

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = _REPO_ROOT / data_dir
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = _REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    for i, t in enumerate(tuples, 1):
        print(f"=== [{i}/{len(tuples)}] {t.label} ===", file=sys.stderr)
        row = run_tuple(t, pool, data_dir, out_dir)
        if row is not None:
            rows.append(row)

    rows.sort(key=_sort_key)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"\n# {len(rows)}/{len(tuples)} tuple(s) completed -> {summary_path}")
    _print_table(rows)
    survivors = [r for r in rows if r.get("tier") in ("paper_ready", "live_ready")]
    print(f"\n# survivors (paper_ready/live_ready): {len(survivors)}")
    for r in survivors:
        print(f"  {r['tier']:<11} {r['label']}  net={r.get('total_oos_net_r_base')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
