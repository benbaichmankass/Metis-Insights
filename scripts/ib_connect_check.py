#!/usr/bin/env python3
"""Connectivity self-test for the Interactive Brokers accounts.

Reads every ``exchange: interactive_brokers`` account from
``config/accounts.yaml`` and runs a non-mutating connection probe against
its IB Gateway endpoint: connect, read the server version + managed
accounts + NetLiquidation, disconnect. **No orders are placed.**

This is the operator-facing answer to "is the IB account actually
connected through Python?" — it exercises the exact ``ib_client_for`` →
``IBClient.connect`` path the live trader uses, so a green run here means
the trader can reach the Gateway too.

Usage
-----
    python scripts/ib_connect_check.py                # probe all IB accounts
    python scripts/ib_connect_check.py ib_paper       # probe one account
    python scripts/ib_connect_check.py --json         # machine-readable

Exit code is 0 when every probed account connects, 1 otherwise (so a CI
step or a diag relay can gate on it). Requires ib_insync (or ib_async)
installed and a running IB Gateway / TWS with the API enabled on the
configured port.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make ``src`` importable when run from the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_ib_accounts(config_path: str) -> list[dict]:
    """Return raw IB account dicts (with the account_id folded in)."""
    import yaml

    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    out = []
    for name, cfg in (raw.get("accounts") or {}).items():
        if str(cfg.get("exchange", "")).lower() not in ("interactive_brokers", "ib"):
            continue
        entry = dict(cfg)
        entry["account_id"] = name
        out.append(entry)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "accounts",
        nargs="*",
        help="Account ids to probe (default: all IB accounts in accounts.yaml).",
    )
    parser.add_argument(
        "--config",
        default=os.path.join(_REPO_ROOT, "config", "accounts.yaml"),
        help="Path to accounts.yaml.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    from src.units.accounts.clients import ib_client_for

    ib_accounts = _load_ib_accounts(args.config)
    if args.accounts:
        wanted = set(args.accounts)
        ib_accounts = [a for a in ib_accounts if a["account_id"] in wanted]

    if not ib_accounts:
        msg = "No interactive_brokers accounts found in config/accounts.yaml."
        print(json.dumps({"error": msg}) if args.json else msg)
        return 1

    results = []
    all_ok = True
    for acct in ib_accounts:
        client = ib_client_for(acct)
        if client is None:
            snap = {
                "account_id": acct["account_id"],
                "connected": False,
                "error": "ib_client_for returned None (ib_port unset?)",
            }
        else:
            snap = client.self_test()
            snap["account_id"] = acct["account_id"]
            snap["mode"] = acct.get("mode")
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        all_ok = all_ok and bool(snap.get("connected"))
        results.append(snap)

    if args.json:
        print(json.dumps({"ok": all_ok, "results": results}, indent=2))
    else:
        for snap in results:
            status = "OK  " if snap.get("connected") else "FAIL"
            line = (
                f"[{status}] {snap.get('account_id')}  "
                f"{snap.get('host')}:{snap.get('port')}  "
                f"mode={snap.get('mode')}  account={snap.get('account')}  "
                f"clientId={snap.get('client_id')}"
            )
            print(line)
            if snap.get("connected"):
                print(
                    f"        serverVersion={snap.get('server_version')}  "
                    f"managedAccounts={snap.get('accounts')}  "
                    f"netLiquidation={snap.get('net_liquidation')}"
                )
            if snap.get("error"):
                print(f"        error: {snap.get('error')}")
        print(f"\n{'ALL CONNECTED' if all_ok else 'ONE OR MORE FAILED'}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
