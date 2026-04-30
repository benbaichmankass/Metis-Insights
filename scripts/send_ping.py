#!/usr/bin/env python3
"""S-019 — enqueue a Telegram ping for the operator.

The bot (running as ``ict-telegram-bot.service``) drains
``runtime_logs/pending_pings/`` every ~5 seconds and sends each
queued message to the operator chat. This script is the canonical
producer — every other process (deploy_pull_restart.sh,
notify_on_pull.py, smoke runner, future trader hooks) should drop
through here rather than re-implementing the Telegram HTTP path.

Why a queue and not a direct ``requests.post``:

* The bot already has the token loaded into env via
  ``EnvironmentFile=/home/ubuntu/ict-trading-bot/.env``. Producers
  don't need their own copy of the token, so credential exposure is
  reduced.
* The bot uses a single client + retry policy. Producers get
  retries/backoff for free.
* Pings fire as soon as the bot's job queue ticks (~5 s), not when
  the next git-sync timer pulls (~5 min).

Usage::

    python3 scripts/send_ping.py "all systems green"
    python3 scripts/send_ping.py --priority urgent "BLOCKED — needs PM"
    python3 scripts/send_ping.py --priority high "S-018 verified"

Prints the path of the queued JSON file, exits 0. If the inbox
directory cannot be written, exits 1 with a logged reason.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PENDING_PINGS_DIR = REPO_ROOT / "runtime_logs" / "pending_pings"

VALID_PRIORITIES = ("urgent", "high", "normal", "low")

logger = logging.getLogger("send_ping")


def enqueue(body: str, priority: str = "normal") -> Path:
    """Atomically write a ping JSON file. Returns the path of the
    final (committed) file. Atomic via tmp + rename so the bot's
    drain loop never sees a partial write."""
    if priority not in VALID_PRIORITIES:
        raise ValueError(
            f"invalid priority {priority!r}; must be one of {VALID_PRIORITIES}"
        )
    body = (body or "").strip()
    if not body:
        raise ValueError("body must be non-empty")

    PENDING_PINGS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{int(uuid.uuid4().int % 10**12):012d}-{priority}.json"
    path = PENDING_PINGS_DIR / name
    tmp = path.with_suffix(".json.tmp")
    payload = {"priority": priority, "body": body}
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.rename(tmp, path)
    return path


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("body", nargs="+", help="Message body (will be joined with spaces).")
    p.add_argument("--priority", choices=VALID_PRIORITIES, default="normal")
    args = p.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    body = " ".join(args.body)
    try:
        path = enqueue(body, priority=args.priority)
    except (ValueError, OSError) as exc:
        logger.error("enqueue failed: %s", exc)
        return 1
    logger.info("queued %s (%s) — bot drains within ~5 s", path.name, args.priority)
    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
