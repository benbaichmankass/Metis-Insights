"""Look up contract metadata by symbol or prefix."""
from __future__ import annotations

import argparse
import json
import sys

from ._common import add_env_arg, build_adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find Tradovate contracts")
    add_env_arg(parser)
    parser.add_argument("symbol", help="Exact symbol (e.g. MESM6) or prefix for suggest")
    parser.add_argument("--suggest", action="store_true", help="Use prefix suggest")
    args = parser.parse_args(argv)

    adapter = build_adapter(args)
    try:
        if args.suggest:
            contracts = adapter.market_data.suggest(args.symbol)
            out = [{"id": c.id, "name": c.name, "status": c.status} for c in contracts]
        else:
            c = adapter.market_data.find_contract(args.symbol)
            out = None if c is None else {"id": c.id, "name": c.name, "status": c.status}
        print(json.dumps(out, indent=2))
        return 0 if out else 1
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
