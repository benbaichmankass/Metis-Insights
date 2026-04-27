#!/usr/bin/env python3
"""
scripts/check_env_paper.py

Safe smoke-test: verify that required env variable NAMES are present after
loading a rendered .env.* file.  Never prints or logs secret values.

Usage:
    python scripts/check_env_paper.py                   # loads .env.paper
    python scripts/check_env_paper.py --env .env.paper
    python scripts/check_env_paper.py --env /content/ict-trading-bot/.env.paper

Exit codes:
    0  all required variables are present and safety flags look correct
    1  one or more required variables missing OR safety flags indicate live mode
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("ERROR: python-dotenv is required.  Install with: pip install python-dotenv")


# ---------------------------------------------------------------------------
# Required variables for the paper profile.
# Entries that are tuples accept *any* of the listed names as equivalent
# (e.g. BYBIT_TESTNET_API_KEY is the paper-profile name; BYBIT_API_KEY is
# what the runtime validation.py expects — the runtime will need one of them).
# ---------------------------------------------------------------------------
REQUIRED: list[str | tuple[str, ...]] = [
    # Core config
    "EXCHANGE",
    "MODE",
    "DRY_RUN",
    "ALLOW_LIVE_TRADING",
    # Telegram credentials (required for alerts/emergency stop)
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    # Bybit credentials — accept testnet-prefixed name OR canonical name
    ("BYBIT_TESTNET_API_KEY", "BYBIT_API_KEY"),
    ("BYBIT_TESTNET_API_SECRET", "BYBIT_API_SECRET"),
    # Trading parameters
    "SYMBOL",
    "TIMEFRAME",
    "RISK_PER_TRADE",
    # Path variables
    "DATA_DIR",
    "LOG_DIR",
]

# ---------------------------------------------------------------------------
# Safety flags — values must match these for paper mode.
# These are non-secret booleans/strings; printing them is safe.
# ---------------------------------------------------------------------------
SAFETY_CHECKS: list[tuple[str, str, str]] = [
    # (key, expected_lowercase_value, reason_if_wrong)
    ("DRY_RUN",            "true",  "must be true for paper mode (no real orders)"),
    ("ALLOW_LIVE_TRADING", "false", "must be false for paper mode"),
    ("MODE",               "paper", "should be PAPER for paper-mode smoke test"),
]


def _present(key: str) -> bool:
    return bool(os.getenv(key, "").strip())


def check(env_path: Path) -> int:
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}", file=sys.stderr)
        return 1

    load_dotenv(dotenv_path=env_path, override=True)

    missing: list[str] = []
    ok_count = 0

    print(f"\nEnv file : {env_path}")
    print("-" * 56)
    print("Required variables:")

    for spec in REQUIRED:
        if isinstance(spec, tuple):
            found_key = next((k for k in spec if _present(k)), None)
            label = " or ".join(spec)
            if found_key:
                print(f"  OK      {label}")
                ok_count += 1
            else:
                print(f"  MISSING {label}")
                missing.append(label)
        else:
            if _present(spec):
                print(f"  OK      {spec}")
                ok_count += 1
            else:
                print(f"  MISSING {spec}")
                missing.append(spec)

    print()
    print("Safety flags (paper-mode guard):")
    safety_failures: list[str] = []
    for key, expected, reason in SAFETY_CHECKS:
        actual = os.getenv(key, "").strip().lower()
        if actual == expected:
            print(f"  SAFE    {key} = {os.getenv(key, '').strip()}")
        else:
            display = os.getenv(key, "(not set)").strip() or "(not set)"
            print(f"  WARN    {key} = {display}  ({reason})")
            safety_failures.append(key)

    print("-" * 56)
    if missing:
        print(f"FAIL  : {len(missing)} required variable(s) missing: {', '.join(missing)}")
    if safety_failures:
        print(f"WARN  : safety flags not paper-safe: {', '.join(safety_failures)}")
    if not missing and not safety_failures:
        print(f"PASS  : all {ok_count} required variables present; safety flags OK.")
    elif not missing:
        print(f"PASS  : all {ok_count} required variables present (see safety warnings above).")

    print()
    print("NOTE  : BYBIT_TESTNET_API_KEY/SECRET are the paper-profile names.")
    print("        src/runtime/validation.py expects BYBIT_API_KEY/SECRET.")
    print("        Export aliases before running the bot:")
    print("          export BYBIT_API_KEY=$BYBIT_TESTNET_API_KEY")
    print("          export BYBIT_API_SECRET=$BYBIT_TESTNET_API_SECRET")

    return 1 if (missing or safety_failures) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--env",
        default=".env.paper",
        help="Path to the rendered env file to check (default: .env.paper)",
    )
    args = parser.parse_args()
    return check(Path(args.env))


if __name__ == "__main__":
    sys.exit(main())
