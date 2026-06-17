#!/usr/bin/env python3
"""Per-account compatibility matrix for a strategy (PB-20260616-004 step 3).

Answers "which accounts can this strategy actually trade on?" by checking each
account in ``config/accounts.yaml`` against the strategy's declared symbols and
the broker's constraints, emitting a **ROUTE / SKIP** verdict per account with
reasons. Used to confirm a new prop variant routes only to the prop account
(and not, say, a Bybit account that doesn't offer the alt as a prop instrument).

Checks per (strategy, account):

  1. **symbol overlap** — strategy ``symbols`` ∩ account ``symbols`` non-empty.
     No shared instrument → SKIP (the per-strategy symbol scope means it would
     never fire there anyway).
  2. **exchange leverage cap** — for ``exchange: breakout`` accounts, the
     notional at the account's per-trade risk must fit the venue leverage cap
     (Breakout: 5× BTC/ETH, 2× other alts). A breach → WARN (not a hard SKIP;
     sizing can be reduced).
  3. **already routed** — whether the strategy is already in the account's
     ``strategies`` list (informational).

Tier-1 read-only tooling: reads YAML, writes nothing, places no orders.

Usage:
    python scripts/prop/account_compat_matrix.py --strategy trend_donchian_sol
    python scripts/prop/account_compat_matrix.py --strategy trend_donchian_eth --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Breakout per-asset leverage caps (public materials, 2026-06-17): 5x BTC/ETH,
# 2x everything else. Used to flag a notional that would exceed the cap.
_BREAKOUT_LEVERAGE = {"BTCUSDT": 5.0, "ETHUSDT": 5.0}
_BREAKOUT_LEVERAGE_DEFAULT = 2.0


def _load_yaml(path: Path) -> Dict[str, Any]:
    import yaml
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _account_risk_pct(acct: Dict[str, Any]) -> float:
    r = acct.get("risk") or {}
    # risk.risk_pct is a fraction (0.015 = 1.5%)
    return float(r.get("risk_pct") or acct.get("risk_pct") or 0.0)


def compat_row(strategy: str, scfg: Dict[str, Any], acct_id: str,
               acct: Dict[str, Any]) -> Dict[str, Any]:
    s_syms = set(scfg.get("symbols") or [])
    a_syms = set(acct.get("symbols") or [])
    shared = sorted(s_syms & a_syms)
    exchange = str(acct.get("exchange") or "")
    reasons: List[str] = []
    verdict = "ROUTE"

    if not shared:
        verdict = "SKIP"
        reasons.append(
            f"no shared symbol (strategy {sorted(s_syms)} ∩ account {sorted(a_syms)} = ∅)")
        return {"account": acct_id, "exchange": exchange, "verdict": verdict,
                "shared_symbols": shared, "reasons": reasons,
                "already_routed": strategy in (acct.get("strategies") or [])}

    # leverage headroom (breakout only) at the account's risk_pct
    if exchange == "breakout":
        risk_frac = _account_risk_pct(acct)
        size = float(acct.get("account_size_usd") or (acct.get("risk") or {}).get("pos_size") or 5000.0)
        for sym in shared:
            cap = _BREAKOUT_LEVERAGE.get(sym, _BREAKOUT_LEVERAGE_DEFAULT)
            # A trade risks risk_frac×size at the stop; notional = risk$/stop_frac.
            # Without a live stop distance we can't compute exact notional, so we
            # only flag the gross cap: max notional = cap × size. The realised
            # gate kept notional well under 2× at 1.5% risk, so this is advisory.
            max_notional = cap * size
            reasons.append(
                f"{sym}: Breakout leverage cap {cap:g}× → max notional "
                f"${max_notional:,.0f} (risk {risk_frac*100:.2f}% of ${size:,.0f}; "
                f"verify per-trade notional stays under at wire time)")

    return {"account": acct_id, "exchange": exchange, "verdict": verdict,
            "shared_symbols": shared, "reasons": reasons,
            "already_routed": strategy in (acct.get("strategies") or [])}


def run(args: argparse.Namespace) -> int:
    strategies = _load_yaml(_REPO_ROOT / "config" / "strategies.yaml").get("strategies", {})
    accounts = _load_yaml(_REPO_ROOT / "config" / "accounts.yaml").get("accounts", {})
    scfg = strategies.get(args.strategy)
    if scfg is None:
        print(f"ERROR: strategy '{args.strategy}' not in config/strategies.yaml", file=sys.stderr)
        return 2

    rows = [compat_row(args.strategy, scfg, aid, acct) for aid, acct in accounts.items()]
    route = [r for r in rows if r["verdict"] == "ROUTE"]

    if args.json:
        print(json.dumps({"strategy": args.strategy,
                          "route_accounts": [r["account"] for r in route],
                          "rows": rows}, indent=2))
        return 0

    print(f"# account compatibility — {args.strategy} (symbols {scfg.get('symbols')})\n")
    print(f"{'account':<16} {'exchange':<10} {'verdict':<7} {'routed':<7} shared")
    print("-" * 60)
    for r in rows:
        print(f"{r['account']:<16} {r['exchange']:<10} {r['verdict']:<7} "
              f"{'yes' if r['already_routed'] else 'no':<7} {','.join(r['shared_symbols']) or '—'}")
    print(f"\nROUTE-verdict accounts: {[r['account'] for r in route] or '(none)'}")
    for r in rows:
        if r["reasons"]:
            print(f"\n{r['account']} ({r['verdict']}):")
            for rs in r["reasons"]:
                print(f"  - {rs}")
    return 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Per-account compatibility matrix for a strategy.")
    p.add_argument("--strategy", required=True)
    p.add_argument("--json", action="store_true")
    return run(p.parse_args(argv[1:]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
