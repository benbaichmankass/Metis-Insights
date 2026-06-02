"""List accounts visible to the configured Tradovate user."""
from __future__ import annotations

import argparse
import json
import sys

from ._common import add_env_arg, build_adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List Tradovate accounts")
    add_env_arg(parser)
    args = parser.parse_args(argv)

    adapter = build_adapter(args)
    try:
        accounts = [
            {"id": a.id, "name": a.name, "type": a.account_type,
             "active": a.active, "legal_status": a.legal_status}
            for a in adapter.list_accounts()
        ]
        print(json.dumps(accounts, indent=2))
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
