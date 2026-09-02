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

# Resolve the runtime_logs root through the canonical, DATA_DIR-aware
# resolver so the inbox matches where the DRAINERS read
# (src.bot.cloud_notifier + src.bot.claude_bridge) and where the other
# producers (execution_diagnostics, liveness_watchdog, coordinator) write.
# On the live VM DATA_DIR=/data/bot-data, so a repo-relative inbox here
# would drop files into a directory no drainer watches → pings silently
# never deliver. Fall back to repo-relative only if the resolver can't be
# imported (e.g. a bare standalone invocation off-repo).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
try:
    from src.utils.paths import runtime_logs_dir as _runtime_logs_dir
    _RUNTIME_LOGS = _runtime_logs_dir()
except Exception:  # noqa: BLE001
    _RUNTIME_LOGS = REPO_ROOT / "runtime_logs"

PENDING_PINGS_DIR = _RUNTIME_LOGS / "pending_pings"
# 2026-05-06 (BUG-058 follow-up): Claude session pings (checkpoint commits,
# blocker PRs, sprint completion, training stages, drained
# pending-pings.jsonl) route through @claude_ict_comms_bot via this
# separate inbox; @bict_trading_bot keeps the existing inbox for
# trade-execution alerts (execution_diagnostics, liveness_watchdog,
# order_monitor). Two-bot separation per CLAUDE.md.
PENDING_CLAUDE_PINGS_DIR = _RUNTIME_LOGS / "pending_claude_pings"

VALID_PRIORITIES = ("urgent", "high", "normal", "low")
VALID_TARGETS = ("trader", "claude")

logger = logging.getLogger("send_ping")


def _inbox_for(target: str) -> Path:
    """Resolve the on-disk inbox for *target*.

    ``trader`` → @bict_trading_bot's inbox (trade alerts).
    ``claude`` → @claude_ict_comms_bot's inbox (Claude session pings).
    """
    if target == "trader":
        return PENDING_PINGS_DIR
    if target == "claude":
        return PENDING_CLAUDE_PINGS_DIR
    raise ValueError(
        f"invalid target {target!r}; must be one of {VALID_TARGETS}"
    )


def enqueue(
    body: str, priority: str = "normal", target: str = "trader",
) -> Path:
    """Atomically write a ping JSON file. Returns the path of the
    final (committed) file. Atomic via tmp + rename so the bot's
    drain loop never sees a partial write.

    *target* picks which bot delivers the ping — default ``trader``
    keeps backward-compat with every existing producer (trade alerts,
    diagnostics, smoke tests). Claude session pings should pass
    ``target="claude"`` so they ride on the @claude_ict_comms_bot
    bridge per CLAUDE.md's two-bot separation.
    """
    if priority not in VALID_PRIORITIES:
        raise ValueError(
            f"invalid priority {priority!r}; must be one of {VALID_PRIORITIES}"
        )
    if target not in VALID_TARGETS:
        raise ValueError(
            f"invalid target {target!r}; must be one of {VALID_TARGETS}"
        )
    body = (body or "").strip()
    if not body:
        raise ValueError("body must be non-empty")

    inbox = _inbox_for(target)
    inbox.mkdir(parents=True, exist_ok=True)
    name = f"{int(uuid.uuid4().int % 10**12):012d}-{priority}.json"
    path = inbox / name
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
    # Format-B fields. --why is what makes a ping readable COLD: "MERGED #10666"
    # a day later says nothing without opening a link nobody opens on a phone.
    # Owned by src/runtime/claude_ping.py so the four producers reaching this
    # channel cannot drift into four shapes.
    p.add_argument("--kind", choices=("decision", "state_change", "lifecycle"),
                   help="ping class; gates rate-limiting and the lifecycle switch")
    p.add_argument("--why", help="Format-B line 2: what CHANGED for the reader")
    p.add_argument("--unproven", help="what this does NOT yet establish")
    p.add_argument("--icon", default="•")
    p.add_argument(
        "--target", choices=VALID_TARGETS, default="trader",
        help="Which bot delivers the ping. 'trader' = @bict_trading_bot "
             "(trade alerts, default); 'claude' = @claude_ict_comms_bot "
             "(Claude session pings — checkpoints, blockers, sprint completes).",
    )
    args = p.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    body = " ".join(args.body)

    # ⚠️ --why / --unproven WITHOUT --kind ARE REFUSED, NEVER SILENTLY DROPPED.
    #
    # The Format-B block below runs only under `--kind`, so before this guard a
    # caller who passed `--why` alone got their second line accepted by argparse,
    # discarded here, and the bare body queued — with exit 0 and a "queued"
    # log line. Measured 2026-09-01 during the operator-requested ping test:
    # `--why "this should not vanish"` queued `{"body": "LOCAL TEST body"}`.
    #
    # That is the same failure the neighbouring guards already refuse in the
    # other direction (`--kind` without `--why` exits 1) and that
    # scripts/ops/send_ping_action.sh refuses at the wrapper layer: a caller who
    # asked for a formatted ping has no way to tell they did not get one. The
    # wrapper's guard does not cover this file, which #10683's own body calls
    # "also reachable directly on the VM" — so the check belongs here too.
    if not args.kind and (args.why or args.unproven):
        logger.error(
            "--why/--unproven require --kind: they are read ONLY on the "
            "Format-B path, so without a class they would be accepted and "
            "silently discarded. Pass --kind, or drop them to send the body "
            "as-is (the passthrough shape carries your text unaltered).")
        return 1

    # Format B + class gating, when a --kind is declared. Producers that pass no
    # --kind keep the legacy free-text path byte-for-byte, so nothing existing
    # changes shape on this commit.
    formatted = False
    if args.kind:
        # ⚠️ AN UNAVAILABLE FORMATTER MUST NOT COST THE PING.
        #
        # scripts/notify_on_pull.py states the invariant this path has to
        # respect, verbatim: "No imports from src.runtime.* so a broken trader
        # doesn't break the ping channel." This module's own docstring calls it
        # "the canonical producer — every other process should drop through
        # here", so a hard failure here takes the channel down with the tree.
        #
        # The first cut of this returned 1, which meant a producer that opted
        # into Format B lost its ping outright on exactly the broken tree an
        # operator most needs to hear about. That is the same direction the
        # limiter already refuses to fail in ("an unreadable limiter SENDS"),
        # applied to the state file and missed on the import.
        #
        # ⚠️ THE FALLBACK IS DELIBERATELY NOT FORMAT B. A second module-shaped
        # renderer here would be the drift claude_ping exists to prevent, so
        # the degraded body is visibly plain — no icon, no indent, an explicit
        # "why:" prefix — and cannot be mistaken for the canonical shape.
        claude_ping = None
        try:
            from src.runtime import claude_ping  # noqa: F811
        except ImportError as exc:  # pragma: no cover - import guard
            logger.warning(
                "claude_ping unavailable (%s) — sending UNFORMATTED rather "
                "than dropping the ping; class gating is unavailable too, and "
                "sending is the safe direction on a notification path", exc)
            if args.why:
                body = f"{body}\nwhy: {args.why}"
            if args.unproven:
                body = f"{body}\nnot yet established: {args.unproven}"
            claude_ping = None  # nothing left to gate or format with

        if claude_ping is not None:
            if not args.why:
                logger.error(
                    "--kind requires --why: Format B's second line is the "
                    "whole point, and an event with nothing to say about what "
                    "changed is activity, which must not ping")
                return 1
            admit, reason = claude_ping.admits(args.kind)
            if not admit:
                # ⚠️ Reported, never silent. "we suppressed it" and "there was
                # nothing to say" are different facts and exit 0 alone
                # conflates them; the reason names which.
                logger.info("ping withheld (%s): %s", args.kind, reason)
                print(f"withheld: {reason}")
                return 0
            try:
                body = claude_ping.format_ping(
                    body, args.why, unproven=args.unproven, icon=args.icon)
                formatted = True
            except ValueError as exc:
                logger.error("format failed: %s", exc)
                return 1

    try:
        path = enqueue(body, priority=args.priority, target=args.target)
    except (ValueError, OSError) as exc:
        logger.error("enqueue failed: %s", exc)
        return 1
    if args.kind and formatted:
        # Only on a CONFIRMED enqueue — recording an attempt would let a failed
        # send suppress its own retry. `formatted` also keeps the degraded
        # import-fallback above out of here: nothing was gated, so there is no
        # send to record against the limiter.
        from src.runtime import claude_ping as _cp
        _cp.record_sent(args.kind)
    logger.info(
        "queued %s (%s, target=%s) — bot drains within ~5 s",
        path.name, args.priority, args.target,
    )
    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
