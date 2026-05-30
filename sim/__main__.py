"""SIM CLI — ``python -m sim run ...`` (Phase 1).

Loads historical candles, runs the integrated pipeline replay, and writes the
ledger + summary under ``runtime_logs/sim/<run_id>/``.

Candle input: a JSONL or CSV of ascending OHLCV rows with at least
``ts,open,high,low,close``. The trainer-VM ``market_raw`` shards
(``datasets-out/market_raw/<symbol>/<tf>/<ver>/data.jsonl``) are the intended
source; a CSV (e.g. ``data/backtest_candles.csv``) also works for local checks.

Read-only against history. Writes only under ``runtime_logs/sim/``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_candles(path: Path) -> list[dict]:
    rows: list[dict] = []
    if path.suffix == ".jsonl":
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    elif path.suffix == ".csv":
        import csv
        with path.open() as fh:
            for r in csv.DictReader(fh):
                rows.append(r)
    else:
        raise ValueError(f"unsupported candle file {path} (want .jsonl or .csv)")

    norm: list[dict] = []
    for r in rows:
        ts = r.get("ts") or r.get("timestamp") or r.get("time")
        try:
            norm.append({
                "ts": ts,
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r.get("volume", 0) or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return norm


def _cmd_run(args: argparse.Namespace) -> int:
    from sim.engine import run_replay

    candles = _load_candles(Path(args.candles))
    if len(candles) <= args.warmup:
        print(f"ERROR: only {len(candles)} candles, need > warmup ({args.warmup})", file=sys.stderr)
        return 2

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]

    # Phase 2: optional models-in-the-loop.
    model_scorer = None
    model_ids = [m.strip() for m in (args.models or "").split(",") if m.strip()]
    if model_ids:
        from sim.models import ModelScorer
        quorum: object = args.quorum
        if str(args.quorum).isdigit():
            quorum = int(args.quorum)
        policy_cfg = {"advisory_policy": {
            "mode": "downsize",
            "bearish_threshold": args.bearish_threshold,
            "size_floor": args.size_floor,
            "quorum": quorum,
        }}
        model_scorer = ModelScorer(
            model_ids=model_ids, policy_cfg=policy_cfg,
            registry_root=args.registry_root or None,
        )

    ledger = run_replay(
        candles=candles,
        strategies=strategies,
        symbol=args.symbol,
        warmup_bars=args.warmup,
        fee_bps_roundtrip=args.fee_bps,
        timeout_bars=args.timeout_bars,
        model_scorer=model_scorer,
    )
    summary = ledger.summary()
    summary["run"] = {
        "candles": len(candles),
        "span": [candles[0]["ts"], candles[-1]["ts"]],
        "strategies": strategies,
        "symbol": args.symbol,
        "fee_bps_roundtrip": args.fee_bps,
        "timeout_bars": args.timeout_bars,
        "warmup_bars": args.warmup,
        "models": model_ids,
    }

    # Phase 3: decision-attrition (per model: live-funnel decision volume vs
    # holdout n_eval, + flagged-trade quality + promotion-readiness verdict).
    if model_ids:
        from sim.attrition import compute_attrition, eval_n_from_registry
        eval_n = eval_n_from_registry(model_ids, registry_root=args.registry_root or None)
        summary["decision_attrition"] = compute_attrition(
            ledger.trades,
            bearish_threshold=args.bearish_threshold,
            eval_n_by_model=eval_n,
        )

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with (out_dir / "ledger.jsonl").open("w") as fh:
        for t in ledger.trades:
            fh.write(json.dumps(t.to_dict()) + "\n")

    # Console headline: portfolio + funnel.
    p = summary["portfolio"]
    print(f"SIM replay {run_id} — {args.symbol} {len(candles)} bars "
          f"{candles[0]['ts']}..{candles[-1]['ts']}")
    print(f"  portfolio: closed={p['closed_trades']} win_rate={p['win_rate']} "
          f"net_r={p['net_r']} exp_r={p['expectancy_r']} maxdd_r={p['max_drawdown_r']}")
    print("  funnel (emitted -> survived_mux -> passed_risk -> filled):")
    for strat, f in sorted(summary["funnel"].items()):
        print(f"    {strat:22s} {f['emitted']:5d} -> {f['survived_mux']:5d} "
              f"-> {f['passed_risk']:5d} -> {f['filled']:5d}")
    if "models_in_loop" in summary:
        m = summary["models_in_loop"]
        print(f"  models-in-loop ({','.join(model_ids)}): "
              f"without={m['net_r_without_model']} with={m['net_r_with_model']} "
              f"delta={m['delta_r']} | downsized={m['downsized_trades']} "
              f"(cut_losers={m['downsize_cut_losers']} cut_winners={m['downsize_cut_winners']})")
    if summary.get("decision_attrition"):
        print("  decision-attrition (per model):")
        for mid, a in sorted(summary["decision_attrition"].items()):
            ratio = a["attrition_ratio"]
            ratio_s = f"{ratio}" if ratio is not None else "n/a"
            print(f"    {mid:26s} scored={a['funnel_scored']} eval_n={a['eval_n']} "
                  f"attrition={ratio_s} influenced={a['influenced']}")
            print(f"      -> {a['readiness']}")
    print(f"  -> {out_dir}/summary.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim", description="Integrated strategy+ML simulation harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="integrated pipeline replay (Phase 1)")
    run.add_argument("--candles", required=True, help="OHLCV .jsonl or .csv (ascending)")
    run.add_argument("--strategies", required=True, help="comma-separated strategy names")
    run.add_argument("--symbol", default="BTCUSDT")
    run.add_argument("--warmup", type=int, default=200, help="history bars per decision (live builders fetch 200)")
    run.add_argument("--fee-bps", type=float, default=7.5, help="round-trip fee in bps")
    run.add_argument("--timeout-bars", type=int, default=0, help="0 = no timeout")
    run.add_argument("--out-root", default="runtime_logs/sim")
    run.add_argument("--run-id", default="")
    # Phase 2 — models-in-the-loop (optional).
    run.add_argument("--models", default="", help="comma-separated model_ids to score as advisory (any stage)")
    run.add_argument("--bearish-threshold", type=float, default=0.35, help="score below this = bearish vote")
    run.add_argument("--size-floor", type=float, default=0.5, help="downsize floor (0=veto, 1=no effect)")
    run.add_argument("--quorum", default="majority", help="bearish models needed: int or 'majority'")
    run.add_argument("--registry-root", default="", help="override registry-store path")
    run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
