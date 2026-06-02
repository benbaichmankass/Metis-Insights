"""Demo-first smoke test.

What this script does, in order:

1. Authenticate against the configured environment (default: demo).
2. List accounts and pick a simulation account.
3. Look up a contract for ``SYMBOL`` (default: MESM6).
4. Subscribe to its quote stream for ~10 seconds (if ``--with-quotes``).
5. Place a 1-lot market order ONLY if ``ENABLE_ORDER_TEST=true`` is in
   the environment *and* ``--allow-order`` is on the CLI — two switches
   so an accidental ``true`` in shell history can't trigger a live send.

Designed to be the first thing you run after dropping credentials into
``.env.tradovate``. It exits 0 on success and prints a JSON health
report so the result can be parsed by a follow-up script.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from ..cli._common import add_env_arg, build_adapter
from ..exceptions import TradovateError
from ..models import OrderRequest, OrderSide, OrderType


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tradovate demo smoke test")
    add_env_arg(parser)
    parser.add_argument("--symbol", default="MESM6")
    parser.add_argument("--with-quotes", action="store_true",
                        help="Attach the WebSocket and stream quotes for ~10s")
    parser.add_argument("--allow-order", action="store_true",
                        help="Allow a 1-lot demo order; also needs ENABLE_ORDER_TEST=true")
    args = parser.parse_args(argv)

    report: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "env": None, "authed": False, "accounts_found": 0,
        "selected_account": None, "contract": None,
        "ws_connected": False, "quote_seen": False,
        "order_placed": False, "errors": [],
    }

    adapter = build_adapter(args, attach_ws=args.with_quotes)
    report["env"] = adapter.config.env.value

    try:
        adapter.auth.get_access_token()
        report["authed"] = True

        accounts = adapter.list_accounts()
        report["accounts_found"] = len(accounts)
        if not accounts:
            report["errors"].append("no accounts visible")
            print(json.dumps(report, indent=2))
            return 1
        sim = adapter.accounts.pick_simulation_account(accounts)
        if sim is None:
            report["errors"].append("no simulation account found")
            print(json.dumps(report, indent=2))
            return 1
        report["selected_account"] = {"id": sim.id, "name": sim.name}

        contract = adapter.market_data.find_contract(args.symbol)
        if contract is not None:
            report["contract"] = {"id": contract.id, "name": contract.name}
        else:
            report["errors"].append(f"contract not found for {args.symbol}")

        if args.with_quotes and adapter.ws is not None:
            asyncio.run(_stream_briefly(adapter, args.symbol, report))

        env_flag = os.environ.get("ENABLE_ORDER_TEST", "").lower() in {"1", "true", "yes"}
        if args.allow_order and env_flag and contract is not None:
            req = OrderRequest(
                account_id=sim.id, symbol=args.symbol, side=OrderSide.BUY,
                qty=1, order_type=OrderType.MARKET,
            )
            order = adapter.place_order(req)
            report["order_placed"] = True
            report["order"] = {
                "id": order.id, "status": order.status,
                "client_order_id": order.client_order_id,
            }
        elif args.allow_order != env_flag:
            report["errors"].append(
                "order test requires both --allow-order AND ENABLE_ORDER_TEST=true"
            )

        print(json.dumps(report, indent=2))
        return 0 if not report["errors"] else 1
    except TradovateError as e:
        report["errors"].append(str(e))
        print(json.dumps(report, indent=2))
        return 1
    finally:
        adapter.close()


async def _stream_briefly(adapter, symbol: str, report: dict, seconds: int = 10) -> None:
    seen = asyncio.Event()

    def on_quote(_q) -> None:
        if not seen.is_set():
            seen.set()

    adapter.market_data.subscribe_quote(symbol, on_quote)
    try:
        await adapter.ws.start()
        report["ws_connected"] = adapter.ws.connected
        try:
            await asyncio.wait_for(seen.wait(), timeout=seconds)
            report["quote_seen"] = True
        except asyncio.TimeoutError:
            report["quote_seen"] = False
    finally:
        await adapter.ws.stop()


if __name__ == "__main__":
    sys.exit(main())
