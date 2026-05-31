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


def _account_from_args(args) -> "object | None":
    """Build an AccountConfig when any account CLI arg was supplied, else None.

    Passing ``--initial-balance``, ``--risk-pct``, or ``--daily-loss-pct`` enables
    the Phase-5 $ account layer; unspecified knobs fall back to AccountConfig
    defaults. With none supplied the layer stays OFF (R-only, back-compatible).
    """
    from sim.account import AccountConfig

    ib = getattr(args, "initial_balance", None)
    rp = getattr(args, "risk_pct", None)
    dl = getattr(args, "daily_loss_pct", None)
    if ib is None and rp is None and dl is None:
        return None
    d = AccountConfig()
    return AccountConfig(
        initial_balance=ib if ib is not None else d.initial_balance,
        risk_pct=rp if rp is not None else d.risk_pct,
        daily_loss_pct=dl if dl is not None else d.daily_loss_pct,
    )


def _resolve_flip_policy(cli_value: "str | None") -> "str | None":
    """Resolve the flip policy: explicit CLI value wins, else the LIVE default.

    Reads ``src.runtime.intents.resolve_flip_policy`` (the source of truth — the
    same function the live order path uses), never a hardcoded literal. Returns
    None only if the live resolver is unavailable, in which case the engine keeps
    its Phase-1 at-most-one-open behavior.
    """
    if cli_value:
        return cli_value
    try:
        from src.runtime.intents import resolve_flip_policy
        return resolve_flip_policy()
    except Exception:  # noqa: BLE001 — never let a resolver import strand a run
        return None


def _add_account_cli(sp) -> None:
    """Phase-5 CLI knobs shared by ``run`` and ``sweep`` (percent units)."""
    sp.add_argument("--initial-balance", type=float, default=None,
                    help="enable the $ account layer with this starting balance")
    sp.add_argument("--risk-pct", type=float, default=None,
                    help="percent of balance risked per trade (1.0 = 1%%)")
    sp.add_argument("--daily-loss-pct", type=float, default=None,
                    help="percent daily-loss cap that halts new opens (0 = off)")
    sp.add_argument("--flip-policy", default=None, choices=["reverse", "hold", "flat"],
                    help="conflict policy on opposite-side intent "
                         "(default: live resolve_flip_policy())")


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

    # Phase 5: optional $ account layer (enabled when any account arg is passed)
    # + flip-policy. resolve_flip_policy() is the LIVE source of truth for the
    # default — never hardcoded here.
    account = _account_from_args(args)
    flip_policy = _resolve_flip_policy(getattr(args, "flip_policy", None))

    ledger = run_replay(
        candles=candles,
        strategies=strategies,
        symbol=args.symbol,
        warmup_bars=args.warmup,
        fee_bps_roundtrip=args.fee_bps,
        timeout_bars=args.timeout_bars,
        model_scorer=model_scorer,
        timeframe=args.timeframe,
        account=account,
        flip_policy=flip_policy,
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
    if "account" in summary:
        ac = summary["account"]
        print(f"  account: bal {ac['initial_balance']:.0f} -> {ac['final_balance']:.0f}  "
              f"net=${ac['net_usd']:.0f} ({ac['return_pct']}%)  "
              f"maxDD=${ac['max_drawdown_usd']:.0f} ({ac['max_drawdown_pct']}%)  "
              f"ret/DD={ac['return_over_dd']}  capital_util={ac['capital_utilization_pct']}%")
        if ac["halted_days"]:
            print(f"    daily-loss halted days: {ac['halted_days']}")
    print(f"  -> {out_dir}/summary.json")
    return 0


def _load_spec(path: Path) -> list[dict]:
    """Load a variants spec (.json or .yaml) -> list of variant dicts."""
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        import yaml
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    variants = doc.get("variants") if isinstance(doc, dict) else doc
    if not isinstance(variants, list) or not variants:
        raise ValueError(f"spec {path} must contain a non-empty 'variants' list")
    return variants


def _cmd_sweep(args: argparse.Namespace) -> int:
    from sim.sweep import run_sweep, write_sweep

    candles = _load_candles(Path(args.candles))
    if len(candles) <= args.warmup:
        print(f"ERROR: only {len(candles)} candles, need > warmup ({args.warmup})", file=sys.stderr)
        return 2
    variants = _load_spec(Path(args.spec))

    # Phase 5: thread the optional $ account layer + flip-policy through to every
    # variant. Forward defensively (only kwargs run_sweep declares) so the CLI
    # stays robust regardless of run_sweep's exact signature.
    import inspect

    sweep_kwargs = dict(
        variants=variants, candles=candles, symbol=args.symbol,
        warmup_bars=args.warmup, fee_bps_roundtrip=args.fee_bps,
        timeout_bars=args.timeout_bars, registry_root=args.registry_root or None,
        timeframe=args.timeframe,
    )
    _accepted = inspect.signature(run_sweep).parameters
    if "account" in _accepted:
        sweep_kwargs["account"] = _account_from_args(args)
    if "flip_policy" in _accepted:
        sweep_kwargs["flip_policy"] = _resolve_flip_policy(getattr(args, "flip_policy", None))
    results = run_sweep(**sweep_kwargs)
    span = [candles[0]["ts"], candles[-1]["ts"]]
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_root) / run_id
    write_sweep(results, out_dir=out_dir, span=span, symbol=args.symbol)

    if args.publish:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        mirror = Path("runtime_logs/trainer_mirror/backtests") / date
        write_sweep(results, out_dir=mirror, span=span, symbol=args.symbol)

    print(f"SIM sweep {run_id} — {args.symbol} {len(candles)} bars, {len(results)} variants")
    print("  rank  variant                net_R    trades  win%   maxDD_R")
    for i, r in enumerate(results, 1):
        h = r["headline"]
        print(f"  {i:<4d}  {r['name']:22s} {h['net_r']:8.2f}  {h['closed_trades']:6d}  "
              f"{h['win_rate']*100:5.1f}  {h['max_drawdown_r']:7.2f}")
    print(f"  -> {out_dir}/SUMMARY.md" + ("  (+published to mirror)" if args.publish else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim", description="Integrated strategy+ML simulation harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="integrated pipeline replay (Phase 1)")
    run.add_argument("--candles", required=True, help="OHLCV .jsonl or .csv (ascending)")
    run.add_argument("--strategies", required=True, help="comma-separated strategy names")
    run.add_argument("--symbol", default="BTCUSDT")
    run.add_argument("--timeframe", default="", help="candle timeframe (e.g. 5m/2h/4h) — lets regime models compute vol_bucket")
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
    # Phase 5 — optional $ account layer + flip-policy.
    _add_account_cli(run)
    run.set_defaults(func=_cmd_run)

    sweep = sub.add_parser("sweep", help="multi-variation sweep (Phase 4)")
    sweep.add_argument("--candles", required=True, help="OHLCV .jsonl or .csv (ascending)")
    sweep.add_argument("--spec", required=True, help="variants spec .json or .yaml ({variants:[...]})")
    sweep.add_argument("--symbol", default="BTCUSDT")
    sweep.add_argument("--timeframe", default="", help="candle timeframe (e.g. 5m/2h/4h) — lets regime models compute vol_bucket")
    sweep.add_argument("--warmup", type=int, default=200)
    sweep.add_argument("--fee-bps", type=float, default=7.5)
    sweep.add_argument("--timeout-bars", type=int, default=0)
    sweep.add_argument("--registry-root", default="")
    sweep.add_argument("--out-root", default="runtime_logs/sim/sweeps")
    sweep.add_argument("--run-id", default="")
    sweep.add_argument("--publish", action="store_true",
                       help="also write to runtime_logs/trainer_mirror/backtests/<date>/ "
                            "(dashboard sweep surface). Off by default so a SIM run never "
                            "clobbers a real backtest sweep.")
    # Phase 5 — optional $ account layer + flip-policy (applied to every variant).
    _add_account_cli(sweep)
    sweep.set_defaults(func=_cmd_sweep)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
