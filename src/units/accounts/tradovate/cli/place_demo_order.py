"""Place a single demo order.

By default runs in dry-run (no network call) so it can be wired up
against demo credentials before the operator is comfortable sending
even paper money. Pass ``--live-fire`` to actually submit.
"""
from __future__ import annotations

import argparse
import json
import sys

from ..exceptions import TradovateRiskRejection
from ..models import OrderRequest, OrderSide, OrderType
from ._common import add_env_arg, build_adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Place a demo Tradovate order")
    add_env_arg(parser)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=("buy", "sell"), required=True)
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--live-fire", action="store_true",
                        help="Disable dry-run and actually send the order")
    args = parser.parse_args(argv)

    adapter = build_adapter(args)
    try:
        if args.live_fire:
            adapter.orders._dry_run = False  # noqa: SLF001

        req = OrderRequest(
            account_id=args.account_id,
            symbol=args.symbol.upper(),
            side=OrderSide.BUY if args.side == "buy" else OrderSide.SELL,
            qty=args.qty,
            order_type=OrderType.MARKET,
        )
        try:
            order = adapter.place_order(req)
        except TradovateRiskRejection as e:
            print(json.dumps({"rejected": True, "reason": e.reason, "detail": e.detail}, indent=2))
            return 2

        print(json.dumps({
            "id": order.id, "status": order.status, "symbol": order.symbol,
            "side": order.side.value if order.side else None,
            "qty": order.qty, "client_order_id": order.client_order_id,
        }, indent=2))
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
