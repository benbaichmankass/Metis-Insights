#!/usr/bin/env python3
"""Phase-0 read-only probe for Alpaca options on the live account.

Answers the verification questions the research memo left open
(docs/research/alpaca-options-l3-research-memo.md Section 9), WITHOUT placing any
order — it only issues GETs:

  1. Is options trading **Level 3** actually active on this account?
     (reads ``options_trading_level`` / ``options_approved_level`` + ``options_buying_power``)
  2. Does the operator's **free "indicative" data feed** populate greeks + implied
     volatility, or only quotes? (counts greeks/IV coverage on a live XLF chain)
  3. What does a near-dated, near-the-money XLF contract actually quote at — i.e. is a
     defined-risk debit structure buildable inside the $150 budget?

Run ON THE LIVE VM (where the keys live). Read-only / Tier-1. Example:

    # live account keys are under the *_LIVE env var names (see config/accounts.yaml)
    ALPACA_API_KEY_ID=$ALPACA_API_KEY_ID_LIVE \
    ALPACA_API_SECRET_KEY=$ALPACA_API_SECRET_KEY_LIVE \
    python scripts/options/probe_alpaca_options.py --env live --underlying XLF

Nothing here is wired into the trader; it is an operator/diagnostic tool.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

# Allow running from the repo root without installation.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.units.accounts.alpaca_client import AlpacaClient  # noqa: E402
from src.units.accounts.alpaca_options_data import AlpacaOptionsData  # noqa: E402


def _account_options_status(env: str) -> dict:
    """Read /v2/account, surfacing the options-level + buying-power fields."""
    client = AlpacaClient(env=env)
    raw = client._request("GET", "/v2/account")  # noqa: SLF001 (diagnostic read)
    if raw.get("retCode") != 0:
        return {"ok": False, "error": raw.get("retMsg")}
    acct = raw.get("result") or {}
    return {
        "ok": True,
        "options_trading_level": acct.get("options_trading_level"),
        "options_approved_level": acct.get("options_approved_level"),
        "options_buying_power": acct.get("options_buying_power"),
        "equity": acct.get("equity"),
        "cash": acct.get("cash"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Alpaca options Phase-0 probe.")
    ap.add_argument("--env", default=os.environ.get("ALPACA_ENV", "live"),
                    choices=["paper", "live"])
    ap.add_argument("--underlying", default="XLF")
    ap.add_argument("--feed", default=os.environ.get("ALPACA_OPTIONS_FEED", "indicative"),
                    help="indicative (free) | opra (paid)")
    ap.add_argument("--weeks", type=int, default=6,
                    help="look at expiries within this many weeks")
    args = ap.parse_args()

    print(f"== Alpaca options Phase-0 probe ({args.env}, feed={args.feed}) ==\n")

    # 1) Account / level / buying power
    status = _account_options_status(args.env)
    print("[1] Account options status:")
    if not status["ok"]:
        print(f"    ERROR reading account: {status['error']}")
        return 2
    for k in ("options_trading_level", "options_approved_level",
              "options_buying_power", "equity", "cash"):
        print(f"    {k}: {status.get(k)}")
    lvl = status.get("options_trading_level")
    print(f"    -> Level 3 active? {'YES' if str(lvl) == '3' else 'NO/UNKNOWN (got %r)' % lvl}\n")

    data = AlpacaOptionsData(env=args.env, feed=args.feed)

    # 2) Chain discovery (near-dated calls)
    today = _dt.date.today()
    horizon = today + _dt.timedelta(weeks=args.weeks)
    contracts_env = data.list_option_contracts(
        args.underlying,
        expiration_date_gte=today.isoformat(),
        expiration_date_lte=horizon.isoformat(),
        contract_type="call",
        limit=100,
    )
    print(f"[2] Contract discovery for {args.underlying} "
          f"(calls, {today} .. {horizon}):")
    if contracts_env.get("retCode") != 0:
        print(f"    ERROR: {contracts_env.get('retMsg')}\n")
        return 3
    contracts = (contracts_env.get("result") or {}).get("option_contracts") or []
    print(f"    {len(contracts)} contracts returned"
          + (f" (e.g. {contracts[0].get('symbol')})" if contracts else "") + "\n")

    # 3) Snapshot: greeks / IV coverage on the free feed
    snap_env = data.snapshots(args.underlying, feed=args.feed, limit=100)
    print(f"[3] Snapshot greeks/IV coverage (feed={args.feed}):")
    if snap_env.get("retCode") != 0:
        print(f"    ERROR: {snap_env.get('retMsg')}")
        print("    (a 403/subscription error here confirms the free feed limit)\n")
        return 4
    payload = snap_env.get("result") or {}
    cov = AlpacaOptionsData.greeks_present(payload)
    print(f"    contracts in snapshot: {cov['total']}")
    print(f"    with greeks: {cov['with_greeks']}    with IV: {cov['with_iv']}")
    verdict = (
        "feed RETURNS greeks/IV" if cov["with_greeks"] > 0
        else "feed does NOT return greeks/IV (compute locally or upgrade to OPRA)"
    )
    print(f"    -> {verdict}")

    # sample one quotable near-the-money contract
    snaps = payload.get("snapshots") or {}
    for sym, entry in list(snaps.items())[:200]:
        mid = AlpacaOptionsData.quote_mid(entry)
        if mid:
            est_max_loss = round(mid * 100, 2)
            print(f"    sample {sym}: mid={mid} -> 1-contract long debit max-loss "
                  f"~${est_max_loss} (fits $150? {'yes' if est_max_loss <= 150 else 'no'})")
            break
    print("\nDone (read-only — no orders placed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
