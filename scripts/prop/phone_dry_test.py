#!/usr/bin/env python3
"""Write ONE always-dry phone test ticket — the ``phone-dry-test`` system-action's CLI.

Operator, 2026-10-06 (PI-20261006-APBY4NTV-0009): *"is there no way to get around doing the
full test suite each time? It's a lot of time to check a one line change"* — until this
action, the only operator-free way to send the phone a dry test ticket was bumping
``config/prop_platforms.yaml::phone_accounts.<acct>.dry_test_request``, which costs the full
CI run (~32 min) plus the ~5 min git-sync before the VM sees it. This writes the same ticket
in one issue-dispatched run, through the SAME writer
(:func:`src.prop.phone_executor.make_test_ticket`) the in-app Dry test button and
``dry_test_request`` use — ``meta.test=True``, so ``submit`` is forced ``dry`` on the server
whatever the account mode, and the phone refuses to submit any test ticket on its own side.

Fail-closed (``src.prop.phone_executor.write_dry_test_ticket``): the account must be a
``phone_accounts`` entry and the symbol one of that account's ``instruments``; anything else
is refused with exit 1 and nothing written. There is no flag that writes a non-test ticket.

Must run where ``trade_journal.db`` lives (the live VM, with the runtime data-dir env loaded)
— the ticket has to land in the DB the web API serves on ``/api/bot/prop/tickets`` and the
phone claims from, which is why it is dispatched through the system-action, not from a
sandbox session.

Usage:
    python3 scripts/prop/phone_dry_test.py --account breakout_2 [--symbol ETHUSDT] [--source <who>]

Exit 0 and a JSON line (``ticket_id``, ``account_id``, ``symbol``, ``submit=dry``) on success;
1 on a refusal or when no reference price could be read (no ticket written either way).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the repo root is importable when run as a bare script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", required=True, help="phone_accounts key in config/prop_platforms.yaml")
    ap.add_argument("--symbol", default="ETHUSDT", help="one of that account's instruments (default ETHUSDT)")
    ap.add_argument("--source", default="phone-dry-test", help="recorded in meta.source (who asked)")
    args = ap.parse_args(argv)

    from src.prop.phone_executor import write_dry_test_ticket

    try:
        out = write_dry_test_ticket(args.account, args.symbol, source=args.source)
    except ValueError as e:
        print(json.dumps({"ok": False, "refused": str(e)}))
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
