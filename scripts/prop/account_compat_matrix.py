#!/usr/bin/env python3
"""Per-account compatibility matrix — run ONE strategy against EVERY account's ruleset.

The **mandatory** evidence step in the strategy flow (see the `backtesting` and
`new-strategy` skills + `docs/integrations/prop-accounts-architecture-DESIGN.md`):
it produces the top-down "which strategy belongs on which account" answer, so a
strategy is never routed to an account it wasn't evaluated against under that
account's rules.

For each account (resolved via `src.prop.account_rulesets.all_account_units`):
  - **prop** account  → cost-aware EV + survival (`run_ev_montecarlo`) under its
    prop ruleset (breach rules + economics) at the account's risk_pct.
  - **standard** account → net-of-fee performance (`run_montecarlo` end-return +
    P(breach) + horizon survival against the account's own soft limits) at its
    risk_pct. The ROUTE gate is the equity/real-money gate: positive mean
    end-return AND survival ≥ `--min-survival` AND P(breach) ≤ `--max-p-breach`
    (so a positive-but-fragile cell can't route onto live capital). For an Alpaca
    real-money promotion run this with `--symbol`/`--fee-bps-roundtrip` so the
    asset_class + fee are recorded on the matrix.

One engine run per (strategy, data); every account eval reuses that single
sizing-independent ledger (the same pattern as `montecarlo_prop`). Multi-account
by construction — a new/added account is picked up automatically.

Output: `runtime_logs/prop_eval/<date>/compat_<strategy>.{json,md}`.
Tier-1 research tooling — no live order path.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.backtest_system as bt  # noqa: E402
from src.prop.account_rulesets import all_account_units  # noqa: E402
from src.prop.montecarlo import run_ev_montecarlo, run_montecarlo  # noqa: E402


def _asset_class_for(symbol: str | None) -> str:
    """Resolve a symbol → coarse asset class via the reporting classifier.

    Reuses ``src.web.api._asset_class`` (the same instruments.yaml-backed map the
    ``/performance`` breakdown uses) so the compat matrix and the dashboard agree
    on what an ETF *is*. Fails soft to ``"unknown"`` so a missing classifier (or a
    symbol absent from instruments.yaml) never breaks the eval — the standard gate
    still runs on the net-of-fee numbers, just without the class tag.
    """
    if not symbol:
        return "unknown"
    try:
        from src.web.api._asset_class import asset_class_for_symbol

        return asset_class_for_symbol(symbol)
    except Exception:  # noqa: BLE001 — reporting-only tag; never fatal to the eval
        return "unknown"


def synth_ledger_from_emit(
    rows: List[Dict[str, Any]],
    *,
    base_account_size: float,
    base_risk_pct: float,
) -> List[Dict[str, Any]]:
    """Synthesize a closed-trade ledger from a harness ``--emit-trades`` JSONL.

    The ETF / alt research cells live in the standalone harnesses (not the BTC
    system engine `scripts.backtest_system.ROSTER`), so they can't be scored by
    the engine path below. Each emit row carries a per-trade ``net_r`` (R already
    net of fees) + an entry/exit timestamp. We replay the SAME compounding
    balance walk that :func:`src.prop.montecarlo.ledger_to_r_sequence` reads back
    (``R_k = pnl_k / (balance_before_k * base_risk_pct/100)``) so the round-trip
    is exact: we set ``pnl_k = net_r_k * balance_before_k * risk_frac`` and let
    the same accumulator recover ``net_r_k``.

    Keys populated match exactly what ``ledger_to_r_sequence`` reads via its
    ``_get``/``_exit_key`` accessors: ``pnl`` and ``exit_ts`` (+ ``entry_ts``
    for completeness). The extra mirror keys are harmless metadata.
    """
    balance = float(base_account_size)
    risk_frac = float(base_risk_pct) / 100.0
    ledger: List[Dict[str, Any]] = []
    for r in rows:
        nr = float(r.get("net_r", 0.0) or 0.0)
        pnl = nr * balance * risk_frac  # reproduces the exact compounding walk
        et = r.get("entry_time") or r.get("exit_time") or r.get("entry_ts") or r.get("exit_ts")
        ledger.append({
            "pnl": pnl,
            "exit_ts": et,      # the key ledger_to_r_sequence._exit_key reads
            "entry_ts": et,     # the key ledger_to_r_sequence reads for entry
            "r_multiple": nr,   # mirror of the input net_r (metadata)
        })
        balance += pnl
    return ledger


def _evaluate_account(unit, ledger, args, horizon: float) -> Dict[str, Any]:
    """Evaluate the strategy ledger against one account's ruleset → a matrix row."""
    common = dict(
        risk_pct=unit.risk_pct, base_risk_pct=args.base_risk_pct,
        account_size=unit.account_size_usd, n_paths=args.n_paths,
        block_len=args.block_len, horizons_months=(horizon,), seed=args.seed,
    )
    if unit.kind == "prop":
        ev = run_ev_montecarlo(ledger, unit.ruleset, **common)
        h = ev.get("horizons", {}).get(str(float(horizon)), {})
        mean_net = h.get("mean_net_usd")
        p_prof = h.get("p_profitable")
        route = bool(mean_net is not None and mean_net > 0 and (p_prof or 0) >= args.min_p_profitable)
        return {
            "account": unit.account_id, "kind": "prop", "class": unit.account_class,
            "risk_pct": unit.risk_pct, "account_size_usd": unit.account_size_usd,
            "metric": "ev_net_usd", "value": mean_net, "p_profitable": p_prof,
            "mean_accounts": h.get("mean_accounts"), "roi_on_fees": h.get("roi_on_fees"),
            "verdict": "ROUTE" if route else "skip",
        }
    # standard account → performance + soft-breach + survival view.
    # The ROUTE gate is TIGHTER than "positive mean end-return": a real-money
    # equity account must ALSO survive the horizon (>= --min-survival) and not
    # breach too often (<= --max-p-breach) under its own soft limits, so a
    # positive-but-fragile cell can't route onto live capital.
    mc = run_montecarlo(ledger, unit.ruleset, **common)
    er = (mc.get("end_return") or {}).get("mean")
    p_breach = mc.get("p_breach")
    survival = (mc.get("survival") or {}).get(str(float(horizon)))
    positive = bool(er is not None and er > 0)
    survives = bool(survival is not None and survival >= args.min_survival)
    low_breach = bool(p_breach is not None and p_breach <= args.max_p_breach)
    route = positive and survives and low_breach
    return {
        "account": unit.account_id, "kind": "standard", "class": unit.account_class,
        "risk_pct": unit.risk_pct, "account_size_usd": unit.account_size_usd,
        "metric": "end_return_mean", "value": er, "p_breach": p_breach,
        "survival": survival, "asset_class": args.asset_class,
        "net_of_fee_bps": args.fee_bps_roundtrip,
        "verdict": "ROUTE" if route else "skip",
    }


def run(args: argparse.Namespace) -> int:
    units = all_account_units()
    if args.accounts:
        keep = {a.strip() for a in args.accounts.split(",") if a.strip()}
        units = {k: v for k, v in units.items() if k in keep}
    if not units:
        print("ERROR: no accounts resolved", file=sys.stderr)
        return 2

    # Output label: the strategy name when scoring a ROSTER cell via the engine,
    # else the ledger filename stem when scoring a harness emit directly.
    label = args.strategy or (Path(args.ledger).stem if args.ledger else None)

    # Resolve the asset class once from --symbol (reporting tag + stamped onto
    # every standard row by _evaluate_account).
    args.asset_class = _asset_class_for(args.symbol)

    if args.ledger:
        # --- harness-emit path (aliased ETF / alt cells, outside bt.ROSTER) ---
        # SKIP both the ROSTER check AND the engine run; synthesize the ledger
        # straight from the emit so it round-trips exactly through
        # montecarlo.ledger_to_r_sequence.
        ledger_path = Path(args.ledger)
        rows = [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
        print(f"[compat] ledger run: {label} ({len(rows)} emit rows, base risk {args.base_risk_pct})",
              file=sys.stderr)
        ledger = synth_ledger_from_emit(
            rows, base_account_size=args.base_account_size, base_risk_pct=args.base_risk_pct,
        )
        data_src = str(ledger_path)
    else:
        # --- engine path (BTC system ROSTER cell) — UNCHANGED ---
        if args.strategy not in bt.ROSTER:
            print(f"ERROR: strategy {args.strategy!r} not in backtest ROSTER {list(bt.ROSTER)}", file=sys.stderr)
            return 2
        base5m = bt._load_candles(args.data)
        print(f"[compat] engine run: {args.strategy} (base risk {args.base_risk_pct})", file=sys.stderr)
        summary = bt.run_system_backtest(
            base5m, roster=[args.strategy], start=args.start, end=args.end,
            initial_balance=args.base_account_size, risk_pct=args.base_risk_pct,
            daily_loss_pct=3.0, signal_ttl_bars=1, overrides={}, refresh=args.refresh_signals,
            clock_tf=args.clock_tf, flip_policy="hold", reentry_policy="suppress", attach_full=True,
            exit_ladder=args.exit_ladder,
        )
        ledger = summary.get("closed_trades", []) or []
        data_src = args.data
    horizon = float(args.horizon_months)

    rows: List[Dict[str, Any]] = []
    for aid, unit in units.items():
        print(f"[compat]   account {aid} ({unit.kind})", file=sys.stderr)
        rows.append(_evaluate_account(unit, ledger, args, horizon))

    out_dir = Path(args.out_dir) if args.out_dir else (
        _REPO_ROOT / "runtime_logs" / "prop_eval" / date.today().isoformat()
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "generated_at": generated_at, "strategy": label, "data": data_src,
        "symbol": args.symbol, "asset_class": args.asset_class,
        "fee_bps_roundtrip": args.fee_bps_roundtrip,
        "min_survival": args.min_survival, "max_p_breach": args.max_p_breach,
        "n_ledger_trades": len(ledger), "horizon_months": horizon, "rows": rows,
    }
    (out_dir / f"compat_{label}.json").write_text(json.dumps(payload, indent=2, default=str))

    sym_tag = f"{args.symbol} · {args.asset_class}" if args.symbol else args.asset_class
    fee_tag = (f"; fee {args.fee_bps_roundtrip:g} bps round-trip"
               if args.fee_bps_roundtrip is not None else "")
    L = [f"# Per-account compatibility — `{label}` ({horizon:.0f}-mo)", "",
         f"_Generated {generated_at}; {len(ledger)} ledger trades; data {data_src}_",
         f"_Instrument {sym_tag}{fee_tag}; standard gate: survival ≥ "
         f"{args.min_survival:.0%}, P(breach) ≤ {args.max_p_breach:.0%}_", "",
         "| account | kind | class | risk% | size$ | metric | value | extra | verdict |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["value"] is None, -(x["value"] or 0))):
        if r["kind"] == "prop":
            extra = f"P(net>0)={r.get('p_profitable')}"
        else:
            extra = f"P(breach)={r.get('p_breach')}, surv={r.get('survival')}"
        val = "—" if r["value"] is None else (f"${r['value']:,.0f}" if r["metric"] == "ev_net_usd"
                                              else f"{r['value']*100:.1f}%")
        L.append(f"| {r['account']} | {r['kind']} | {r['class']} | {r['risk_pct']} | "
                 f"{r['account_size_usd']:.0f} | {r['metric']} | {val} | {extra} | **{r['verdict']}** |")
    L += ["", "Verdict: **ROUTE** = positive under the account's own ruleset "
          "(prop: +EV @ P(net>0) ≥ threshold; standard: positive mean end-return "
          "AND survival ≥ --min-survival AND P(breach) ≤ --max-p-breach). "
          "Prop verdicts are research on the configured feed — revalidate on the "
          "account's real venue data before live wiring (Tier-3).",
          "",
          "**Standard (real-money / paper equity) caveat:** a ROUTE here is a "
          "net-of-fee research verdict on the supplied ledger — it MUST be "
          "revalidated on the account's own real venue data (the broker's actual "
          "fills + fees) before the strategy is wired live on that account "
          "(Tier-3, operator-approved)."]
    (out_dir / f"compat_{label}.md").write_text("\n".join(L))
    print("\n".join(L))
    print(f"\nwrote {out_dir / ('compat_' + str(label) + '.md')}", file=sys.stderr)
    return 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Per-account compatibility matrix for one strategy.")
    p.add_argument("--strategy", default=None,
                   help="A name in scripts.backtest_system.ROSTER (engine path). "
                        "Not required when --ledger is given; then it only labels the output "
                        "(defaults to the ledger filename stem).")
    p.add_argument("--ledger", default=None,
                   help="Path to a harness --emit-trades JSONL ({entry_time, net_r, ...}). "
                        "Scores an aliased ETF / alt cell directly (SKIPS the ROSTER check + "
                        "engine run); the synthesized ledger round-trips exactly through "
                        "src.prop.montecarlo.ledger_to_r_sequence.")
    p.add_argument("--data", default="/home/user/ict-trader-data/btc_5m.parquet")
    p.add_argument("--symbol", default=None,
                   help="Instrument symbol for this ledger (e.g. IWM, GLD). Resolves "
                        "the asset_class via src.web.api._asset_class and is stamped onto "
                        "the output + every standard row. Optional (defaults to 'unknown').")
    p.add_argument("--fee-bps-roundtrip", type=float, default=None,
                   help="Round-trip fee (bps) the emit ledger was generated at — recorded "
                        "onto the output + standard rows for provenance (the net_r already "
                        "bakes the fee in; this is the audit trail, not a re-charge).")
    p.add_argument("--accounts", default=None, help="Optional CSV filter of account ids.")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--base-account-size", type=float, default=5000.0)
    p.add_argument("--base-risk-pct", type=float, default=0.5)
    p.add_argument("--clock-tf", default="1h", choices=list(bt._PANDAS_TF.keys()))
    p.add_argument("--horizon-months", type=float, default=12.0)
    p.add_argument("--n-paths", type=int, default=3000)
    p.add_argument("--block-len", type=int, default=8)
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--min-p-profitable", type=float, default=0.5,
                   help="Prop ROUTE gate: minimum P(net>0) (default 0.5).")
    p.add_argument("--min-survival", type=float, default=0.90,
                   help="Standard ROUTE gate: minimum horizon survival fraction "
                        "(default 0.90). The cell must survive the horizon this "
                        "often under the account's own soft limits to route.")
    p.add_argument("--max-p-breach", type=float, default=0.10,
                   help="Standard ROUTE gate: maximum P(breach) under the account's "
                        "soft limits (default 0.10).")
    p.add_argument("--refresh-signals", action="store_true")
    p.add_argument("--exit-ladder", dest="exit_ladder", action="store_true",
                   help="Evaluate the strategy with the harness partial-TP exit "
                        "ladder (Unit C Phase 1): bank 50%% @+1.5R + 25%% @+3R, "
                        "residual rides the strategy's tp/trail/SL. Use to gate "
                        "the swap-robust prop EXIT variants (e.g. "
                        "trend_donchian_sol_prop/_eth_prop) against the prop "
                        "EV/survival ruleset BEFORE proposing a shadow->live "
                        "promotion. Default-off → the single-target baseline.")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args(argv[1:])
    if not args.strategy and not args.ledger:
        p.error("one of --strategy or --ledger is required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
