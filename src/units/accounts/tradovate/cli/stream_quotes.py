"""Stream quotes for one or more symbols until interrupted."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ._common import add_env_arg, build_adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream Tradovate quotes")
    add_env_arg(parser)
    parser.add_argument("symbols", nargs="+", help="Symbols to subscribe to")
    parser.add_argument("--seconds", type=int, default=30,
                        help="Stop after N seconds (default 30; 0 = forever)")
    args = parser.parse_args(argv)

    adapter = build_adapter(args, attach_ws=True)

    def on_quote(q):
        print(json.dumps({
            "contract_id": q.contract_id,
            "bid": q.bid, "ask": q.ask, "last": q.last,
            "ts": q.ts.isoformat(),
        }))

    for sym in args.symbols:
        adapter.market_data.subscribe_quote(sym, on_quote)

    async def run() -> None:
        await adapter.ws.start()
        if args.seconds > 0:
            await asyncio.sleep(args.seconds)
        else:
            await asyncio.Event().wait()
        await adapter.ws.stop()

    try:
        asyncio.run(run())
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
