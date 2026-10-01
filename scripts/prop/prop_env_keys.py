#!/usr/bin/env python3
"""Print the .env key NAMES a prop account's terminal run needs (never values).

Used by ``scripts/ops/breakout_login_check_action.sh`` and
``scripts/ops/prop_feed_tick.sh`` to decide which keys to export from the VM
``.env``: the account's ``username_env`` / ``password_env`` from
``config/prop_platforms.yaml``, plus its executor kill switch
(``src.prop.prop_executor.mode_env_for``). One space-separated line on stdout.

Exit 1 (and nothing on stdout) when the account has no platform entry, so a
wrapper fails closed instead of exporting another account's login.

TRADEIFY-WIRE (2026-09-30): before this the two wrappers hardcoded
``BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD``, so a second prop account's login
could never reach its check. For ``breakout_1`` this prints exactly the keys
the wrappers hardcoded (``tests/test_prop_second_account_wiring.py``).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def env_keys(account_id: str) -> list:
    from src.prop.platform import load_platform_config
    from src.prop.prop_executor import mode_env_for

    cfg = load_platform_config(account_id)
    keys = [str(cfg.get("username_env") or ""), str(cfg.get("password_env") or ""), mode_env_for(account_id)]
    if not all(keys):
        raise KeyError(f"{account_id!r}: username_env / password_env missing in prop_platforms.yaml")
    return keys


def main(argv: list) -> int:
    if len(argv) != 1:
        print("usage: prop_env_keys.py <account_id>", file=sys.stderr)
        return 1
    try:
        keys = env_keys(argv[0])
    except Exception as exc:  # noqa: BLE001 — any failure fails closed
        print(f"prop_env_keys: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(" ".join(keys))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
