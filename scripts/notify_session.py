#!/usr/bin/env python3
"""
notify_session.py — small CLI for end-of-session Telegram pings.

Two subcommands:

    session   one short message at end of a Claude session checkpoint
    sprint    one short message when an entire sprint is complete

Both reuse the existing safe helper `src.runtime.notify.send_via_alert_manager`,
which reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from env. If those are
missing, the helper logs a warning and exits cleanly — never raises and never
prints the secrets.

Usage:
    PYTHONPATH=. python scripts/notify_session.py session \\
        --checkpoint CP-2026-04-28-01 \\
        --summary "M1 timer PR ready for review"

    PYTHONPATH=. python scripts/notify_session.py sprint \\
        --sprint sprint-plan-2026-04-28 \\
        --summary "Live trading hardening + cleanup done"

This script does not handle secrets itself. It only formats and forwards.
"""
from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger("notify_session")


def _send(message: str) -> int:
    """Forward to the existing safe Telegram helper. Returns exit code."""
    try:
        from src.runtime.notify import send_via_alert_manager
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not import send_via_alert_manager: %s", exc)
        # Don't fail the build — workflow continues even without Telegram.
        return 0

    try:
        send_via_alert_manager(message)
        print(f"[notify_session] dispatched: {message[:80]}")
        return 0
    except Exception as exc:  # noqa: BLE001
        # send_via_alert_manager already swallows most failures, but be defensive.
        logger.warning("Telegram send failed (non-fatal): %s", exc)
        return 0


def _cmd_session(args: argparse.Namespace) -> int:
    msg = (
        f"✅ ICT bot session checkpoint {args.checkpoint}\n"
        f"{args.summary}"
    )
    return _send(msg)


def _cmd_sprint(args: argparse.Namespace) -> int:
    msg = (
        f"🏁 ICT bot sprint complete: {args.sprint}\n"
        f"{args.summary}"
    )
    return _send(msg)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Session/sprint Telegram pings.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("session", help="end-of-session checkpoint ping")
    s.add_argument("--checkpoint", required=True, help="e.g. CP-2026-04-28-01")
    s.add_argument("--summary", required=True, help="one-line summary")
    s.set_defaults(func=_cmd_session)

    sp = sub.add_parser("sprint", help="sprint-complete ping")
    sp.add_argument("--sprint", required=True, help="e.g. sprint-plan-2026-04-28")
    sp.add_argument("--summary", required=True, help="one-line summary")
    sp.set_defaults(func=_cmd_sprint)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
