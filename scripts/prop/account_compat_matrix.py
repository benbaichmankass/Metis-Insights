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
    P(breach) against the account's own soft limits) at its risk_pct.

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
    # standard account → performance + soft-breach view
    mc = run_montecarlo(ledger, unit.ruleset, **common)
    er = (mc.get("end_return") or {}).get("mean")
    p_breach = mc.get("p_breach")
    route = bool(er is not None and er > 0)
    return {
        "account": unit.account_id, "kind": "standard", "class": unit.account_class,
        "risk_pct": unit.risk_pct, "account_size_usd": unit.account_size_usd,
        "metric": "end_return_mean", "value": er, "p_breach": p_breach,
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
        "n_ledger_trades": len(ledger), "horizon_months": horizon, "rows": rows,
    }
    (out_dir / f"compat_{label}.json").write_text(json.dumps(payload, indent=2, default=str))

    L = [f"# Per-account compatibility — `{label}` ({horizon:.0f}-mo)", "",
         f"_Generated {generated_at}; {len(ledger)} ledger trades; data {data_src}_", "",
         "| account | kind | class | risk% | size$ | metric | value | extra | verdict |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["value"] is None, -(x["value"] or 0))):
        extra = (f"P(net>0)={r.get('p_profitable')}" if r["kind"] == "prop"
                 else f"P(breach)={r.get('p_breach')}")
        val = "—" if r["value"] is None else (f"${r['value']:,.0f}" if r["metric"] == "ev_net_usd"
                                              else f"{r['value']*100:.1f}%")
        L.append(f"| {r['account']} | {r['kind']} | {r['class']} | {r['risk_pct']} | "
                 f"{r['account_size_usd']:.0f} | {r['metric']} | {val} | {extra} | **{r['verdict']}** |")
    L += ["", "Verdict: **ROUTE** = positive under the account's own ruleset "
          "(prop: +EV @ P(net>0) ≥ threshold; standard: positive mean end-return). "
          "Prop verdicts are research on the configured feed — revalidate on the "
          "account's real venue data before live wiring (Tier-3)."]
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
    p.add_argument("--min-p-profitable", type=float, default=0.5)
    p.add_argument("--refresh-signals", action="store_true")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args(argv[1:])
    if not args.strategy and not args.ledger:
        p.error("one of --strategy or --ledger is required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
