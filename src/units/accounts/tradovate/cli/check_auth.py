"""Authenticate against the configured environment and print the result.

Usage:
    python -m src.units.accounts.tradovate.cli.check_auth [--env demo|live]
"""
from __future__ import annotations

import argparse
import json
import sys

from ._common import add_env_arg, build_adapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tradovate auth check")
    add_env_arg(parser)
    args = parser.parse_args(argv)

    adapter = build_adapter(args)
    try:
        token = adapter.auth.get_access_token()
        bundle = adapter.auth.current()
        print(json.dumps({
            "env": adapter.config.env.value,
            "authed": True,
            "user_id": bundle.user_id if bundle else None,
            "expires_at": bundle.expires_at.isoformat() if bundle else None,
            "access_token_prefix": token[:8] + "…",
        }, indent=2))
        return 0
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
