#!/usr/bin/env python3
"""
notify_session.py — small CLI for end-of-session Telegram pings.

Three subcommands:

    session   one short message at end of a Claude session checkpoint
    sprint    one short message when an entire sprint is complete
    alert     user-action-required message when the sprinter is blocked

All reuse `src.runtime.notify.send_telegram_direct`, a stdlib-only POST to
the Telegram sendMessage API. It reads `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID` from env; if either is missing it logs a warning and
returns cleanly (back-compat). Network/HTTP failures DO raise — those are
real delivery failures and surface here as exit 1, so the Stop hook's
`logs/notify_hook.log` shows the failure instead of silently exiting 0.

Usage:
    PYTHONPATH=. python scripts/notify_session.py session \\
        --checkpoint CP-2026-04-28-01 \\
        --summary "M1 timer PR ready for review"

    PYTHONPATH=. python scripts/notify_session.py sprint \\
        --sprint sprint-plan-2026-04-28 \\
        --summary "Live trading hardening + cleanup done"

    PYTHONPATH=. python scripts/notify_session.py alert \\
        --summary "M0 ready for review" \\
        --link "https://github.com/benbaichmankass/ict-trading-bot/pull/86"

This script does not handle secrets itself. It only formats and forwards.
"""
from __future__ import annotations

import argparse
import logging
import sys
import urllib.error

logger = logging.getLogger("notify_session")


def _send(message: str) -> int:
    """Forward to the stdlib-only Telegram helper. Returns exit code.

    Import failures (e.g. ImportError) surface as exit 1 — the Stop hook
    log will show them. Network failures (URLError/HTTPError) likewise
    return exit 1 with a single-line stderr marker. Missing-creds is
    treated as a clean no-op (exit 0) by the helper itself.
    """
    try:
        from src.runtime.notify import send_telegram_direct
    except ImportError as exc:
        print(
            f"[notify_session] telegram-import-error: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        send_telegram_direct(message)
    except urllib.error.HTTPError as exc:
        print(
            f"[notify_session] telegram-http-error: status={exc.code}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(
            f"[notify_session] telegram-network-error: {exc.reason}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(
            f"[notify_session] telegram-send-failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    print(f"[notify_session] dispatched: {message[:80]}")
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


def _cmd_alert(args: argparse.Namespace) -> int:
    msg = (
        f"🚨 Alert! - User Action Required\n"
        f"{args.summary}\n"
        f"👉 {args.link}"
    )
    return _send(msg)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Session/sprint/alert Telegram pings.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("session", help="end-of-session checkpoint ping")
    s.add_argument("--checkpoint", required=True, help="e.g. CP-2026-04-28-01")
    s.add_argument("--summary", required=True, help="one-line summary")
    s.set_defaults(func=_cmd_session)

    sp = sub.add_parser("sprint", help="sprint-complete ping")
    sp.add_argument("--sprint", required=True, help="e.g. sprint-plan-2026-04-28")
    sp.add_argument("--summary", required=True, help="one-line summary")
    sp.set_defaults(func=_cmd_sprint)

    al = sub.add_parser("alert", help="user-action-required ping when sprinter is blocked")
    al.add_argument("--summary", required=True, help="one-line: what is needed from Ben")
    al.add_argument("--link", required=True, help="PR URL or session URL where Ben should act")
    al.set_defaults(func=_cmd_alert)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
