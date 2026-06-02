"""Cancel every working order on the given account."""
from __future__ import annotations

import argparse
import json
import sys

from ._common import add_env_arg, build_adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cancel all working Tradovate orders")
    add_env_arg(parser)
    parser.add_argument("--account-id", type=int, required=True)
    args = parser.parse_args(argv)

    adapter = build_adapter(args)
    try:
        results = adapter.cancel_all(args.account_id)
        print(json.dumps({"cancelled": len(results), "results": results}, indent=2))
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
