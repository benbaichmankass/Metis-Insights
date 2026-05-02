#!/usr/bin/env python3
"""S-016 H3 — Telegram ping fanout, called from deploy_pull_restart.sh.

Designed to be lean and bulletproof:

* Stdlib + ``requests`` only — no pandas / no AlertManager / no chain
  of optional imports that could break the ping path when the bot
  itself is broken.
* Idempotent: invoked with ``--pre <sha> --post <sha>``; if HEAD did
  not advance, sends nothing.
* Three sources of pings, processed in priority order:
    1. Blocker commits — any commit in (pre, post] whose subject
       starts with ``[BLOCKED-PM]``. Emitted as ``urgent``.
    2. Drain ``docs/claude/pending-pings.jsonl`` — sandbox-side
       Claude sessions append to this file when they can't reach
       Telegram directly. After drain, the file is truncated by a
       follow-up commit (handled out-of-band; this script just
       reads).
    3. Checkpoint append — if the diff for (pre, post] touched
       ``docs/claude/checkpoints/CHECKPOINT_LOG.md``, parse the
       topmost ``## CP-…`` entry and emit a normal-priority ping.
* Failure modes (per ``docs/claude/telegram-pings.md`` § Failure modes
  the wiring must handle): missing token logs a warning and exits 0.
  Telegram 5xx retries 3× with exponential backoff. Corrupt
  pending-pings.jsonl is moved aside and a diagnostic ping is sent.
* No imports from ``src.runtime.*`` so a broken trader doesn't break
  the ping channel.

Usage on the VM (called from deploy_pull_restart.sh):

    python3 scripts/notify_on_pull.py --pre <pre_sha> --post <post_sha>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional

import requests

logger = logging.getLogger("notify_on_pull")

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_LOG = REPO_ROOT / "docs" / "claude" / "checkpoints" / "CHECKPOINT_LOG.md"
PENDING_PINGS = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
SEND_TIMEOUT_S = 10
RETRY_BACKOFF_S = (1, 4, 16)

PRIORITY_PREFIX = {
    "urgent": "🚨 URGENT",
    "high":   "🔔",
    "normal": "ℹ️",
    "low":    "·",
}

BLOCKER_TAG = "[BLOCKED-PM]"
GITHUB_COMMIT_URL = "https://github.com/the-lizardking/ict-trading-bot/commit/{sha}"

# S-027 PR2 — comms response commits use this prefix. The notify pipeline
# is opt-in (only matches BLOCKER_TAG, TRAINING_TAGS, and
# CHECKPOINT_LOG.md touches), so comms commits are naturally silent. We
# log them at INFO so journalctl shows the pipeline saw and ignored them.
COMMS_RESPONSE_PREFIX = "comms(response):"

# CP-2026-05-02: training/improvement workflow stage tags. Each stage
# emits its own ping by matching the commit subject prefix. Subjects can
# include the tag at the start (commit) or after a fixed prefix
# convention. Priorities follow docs/claude/telegram-pings.md.
TRAINING_TAGS: list[tuple[str, str, str]] = [
    # (subject prefix, label shown to operator, priority)
    ("[TRAINING-START]",         "TRAINING-START — research + hypotheses",          "normal"),
    ("TRAINING-PLAN:",           "TRAINING-PLAN — plan committed, run dispatched",  "high"),
    ("TRAINING-RESULTS:",        "TRAINING-RESULTS — run finished",                 "high"),
    ("TRAINING-RESULTS [FAILED]:",
                                 "TRAINING-RESULTS [FAILED] — run errored",         "high"),
    ("RECOMMENDATIONS (PM REVIEW):",
                                 "RECOMMENDATIONS (PM REVIEW) — writeup ready",     "high"),
    ("IMPLEMENT:",               "IMPLEMENT — strategy/model code change ready",    "high"),
]


# ---------------------------------------------------------------------------
# Telegram transport
# ---------------------------------------------------------------------------


def _post_telegram(token: str, chat_id: str, message: str) -> bool:
    """POST one message. Returns True on 200; False on permanent failure."""
    url = TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": message,
               "disable_web_page_preview": True}
    last_exc: Optional[Exception] = None
    for attempt, backoff in enumerate(RETRY_BACKOFF_S):
        try:
            r = requests.post(url, json=payload, timeout=SEND_TIMEOUT_S)
            if r.status_code == 200:
                return True
            if 500 <= r.status_code < 600:
                logger.warning("telegram %s on attempt %d, backing off %ds",
                               r.status_code, attempt + 1, backoff)
                time.sleep(backoff)
                continue
            logger.error("telegram permanent failure %s: %s", r.status_code, r.text[:200])
            return False
        except requests.RequestException as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("telegram transport error on attempt %d: %s",
                           attempt + 1, exc.__class__.__name__)
            time.sleep(backoff)
    logger.error("telegram retries exhausted: %s", last_exc)
    return False


def _send_priority(token: str, chat_id: str, priority: str, body: str) -> bool:
    prefix = PRIORITY_PREFIX.get(priority, PRIORITY_PREFIX["normal"])
    return _post_telegram(token, chat_id, f"{prefix} {body}")


# ---------------------------------------------------------------------------
# Source 1 — blocker commits in the (pre, post] range
# ---------------------------------------------------------------------------


def _commit_subjects(pre_sha: str, post_sha: str) -> List[tuple[str, str]]:
    """Return [(sha, subject), ...] for commits in (pre_sha, post_sha]."""
    if not pre_sha or pre_sha == "unknown":
        return []
    try:
        out = subprocess.run(
            ["git", "log", "--format=%H%x09%s", f"{pre_sha}..{post_sha}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            check=False, timeout=10,
        )
        if out.returncode != 0:
            logger.warning("git log failed: %s", out.stderr.strip())
            return []
        pairs = []
        for line in out.stdout.splitlines():
            if "\t" not in line:
                continue
            sha, subject = line.split("\t", 1)
            pairs.append((sha.strip(), subject.strip()))
        return pairs
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("git log error: %s", exc)
        return []


def _blocker_pings(pre_sha: str, post_sha: str) -> List[tuple[str, str]]:
    """Return [(priority, body)] for any commit subject starting with the
    blocker tag in the new range."""
    out: List[tuple[str, str]] = []
    for sha, subject in _commit_subjects(pre_sha, post_sha):
        if subject.startswith(COMMS_RESPONSE_PREFIX):
            # Silently audit — comms response writebacks ride on their
            # own channel and must never fire a checkpoint/blocker ping.
            logger.info("notify_on_pull: ignoring comms response commit %s", sha[:8])
            continue
        if not subject.startswith(BLOCKER_TAG):
            continue
        question = subject[len(BLOCKER_TAG):].strip(" :-")
        body = (
            f"BLOCKED — needs PM input\n"
            f"Q: {question}\n"
            f"Commit: {GITHUB_COMMIT_URL.format(sha=sha)}"
        )
        out.append(("urgent", body))
    return out


def _training_workflow_pings(pre_sha: str, post_sha: str) -> List[tuple[str, str]]:
    """Detect training-improvement workflow stage commits in the new range.

    docs/claude/training-improvement-workflow.md defines four stage
    boundaries; each rides on its own commit-subject prefix (logged in
    ``TRAINING_TAGS``). Until CP-2026-05-02 these prefixes were only
    documented — no ping fired when an autonomous Claude session
    advanced through them. This helper matches the prefixes and emits
    one ping per stage transition so the operator gets per-step
    visibility on training runs.
    """
    out: List[tuple[str, str]] = []
    for sha, subject in _commit_subjects(pre_sha, post_sha):
        for prefix, label, priority in TRAINING_TAGS:
            if subject.startswith(prefix):
                detail = subject[len(prefix):].strip(" :-")
                body = (
                    f"{label}\n"
                    + (f"{detail}\n" if detail else "")
                    + f"Commit: {GITHUB_COMMIT_URL.format(sha=sha)}"
                )
                out.append((priority, body))
                break  # one ping per commit; longest-match unnecessary
    return out


# ---------------------------------------------------------------------------
# Source 2 — drain pending-pings.jsonl
# ---------------------------------------------------------------------------


def _drain_pending_pings(path: Path) -> List[tuple[str, str]]:
    """Read lines from *path*, return [(priority, body)] for each.

    The file is left in place — truncation is the responsibility of the
    follow-up commit (so reruns are idempotent if the truncation commit
    hasn't landed yet, the same line will re-fire). This is acceptable
    given we expect the queue to be empty most of the time.
    """
    if not path.exists():
        return []
    out: List[tuple[str, str]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("pending-pings: skipping malformed line: %r", raw[:100])
                continue
            priority = str(entry.get("priority") or "normal")
            event = str(entry.get("event") or "ping")
            # Build a body from the structured fields; fall back to the
            # raw json if the schema doesn't match.
            parts = [event]
            for k in ("sprint", "cp_id", "title", "next_cp",
                      "question", "summary"):
                v = entry.get(k)
                if v:
                    parts.append(f"{k}={v}")
            for k in ("commit_url", "pr_url", "chat_url", "summary_url"):
                v = entry.get(k)
                if v:
                    parts.append(str(v))
            out.append((priority, " | ".join(parts)))
    except OSError as exc:
        logger.warning("pending-pings: read error: %s", exc)
    return out


# ---------------------------------------------------------------------------
# Source 3 — checkpoint-log diff
# ---------------------------------------------------------------------------


_CP_HEADER_RE = re.compile(r"^##\s+(CP-\d{4}-\d{2}-\d{2}-\d+)\s+—\s+(.+?)\s*$")


def _diff_touched_checkpoint_log(pre_sha: str, post_sha: str) -> bool:
    if not pre_sha or pre_sha == "unknown":
        return False
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{pre_sha}..{post_sha}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            check=False, timeout=10,
        )
        names = out.stdout.splitlines() if out.returncode == 0 else []
    except (subprocess.SubprocessError, OSError):
        return False
    return any("docs/claude/checkpoints/CHECKPOINT_LOG.md" in name for name in names)


def _latest_cp_entry(log_path: Path) -> Optional[tuple[str, str, List[str]]]:
    """Parse the topmost ``## CP-…`` entry. Returns (cp_id, title, body_lines)
    or None if the log is empty / malformed."""
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return None
    in_entry = False
    cp_id = title = ""
    body: List[str] = []
    for line in text.splitlines():
        m = _CP_HEADER_RE.match(line)
        if m:
            if in_entry:
                # second header — end of latest entry
                break
            cp_id, title = m.group(1), m.group(2)
            in_entry = True
            continue
        if in_entry:
            body.append(line)
    if not in_entry:
        return None
    return cp_id, title, body


def _checkpoint_ping(post_sha: str) -> Optional[tuple[str, str]]:
    """Build a ping from the top entry of CHECKPOINT_LOG.md."""
    parsed = _latest_cp_entry(CHECKPOINT_LOG)
    if parsed is None:
        return None
    cp_id, title, body = parsed
    next_cp = ""
    sprint = ""
    for line in body:
        line = line.strip()
        if line.startswith("- **Next checkpoint:**"):
            next_cp = line.split("**Next checkpoint:**", 1)[1].strip()
            next_cp = next_cp.lstrip("* ").rstrip()[:200]
        elif line.startswith("- **Sprint:**"):
            sprint = line.split("**Sprint:**", 1)[1].strip()[:120]
    priority = "high" if any(
        kw in title.upper() for kw in ("COMPLETE", "WRAPPED", "SHIPPED")
    ) else "normal"
    msg_lines = [f"{cp_id} — {title}"]
    if sprint:
        msg_lines.append(f"Sprint: {sprint}")
    if next_cp:
        msg_lines.append(f"Next: {next_cp}")
    msg_lines.append(GITHUB_COMMIT_URL.format(sha=post_sha))
    return priority, "\n".join(msg_lines)


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def collect_pings(
    pre_sha: str,
    post_sha: str,
    force_checkpoint: bool = False,
) -> List[tuple[str, str]]:
    """Order: blockers first (urgent), then queue drain, then checkpoint.

    ``force_checkpoint=True`` emits the checkpoint ping even if the diff
    didn't touch ``CHECKPOINT_LOG.md`` — used by the deploy script's
    ``runtime_flags/auto_ping_test.flag`` path to verify the auto-ping
    leg without waiting for a real checkpoint commit.
    """
    pings: List[tuple[str, str]] = []
    pings.extend(_blocker_pings(pre_sha, post_sha))
    pings.extend(_training_workflow_pings(pre_sha, post_sha))
    pings.extend(_drain_pending_pings(PENDING_PINGS))
    if force_checkpoint or _diff_touched_checkpoint_log(pre_sha, post_sha):
        cp_ping = _checkpoint_ping(post_sha)
        if cp_ping is not None:
            pings.append(cp_ping)
    return pings


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pre", required=True, help="HEAD before the pull")
    parser.add_argument("--post", required=True, help="HEAD after the pull")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip the actual Telegram POST")
    parser.add_argument("--force-checkpoint", action="store_true",
                        help="Emit a checkpoint ping even if the diff "
                             "didn't touch CHECKPOINT_LOG.md (for the "
                             "auto_ping_test.flag verification path).")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.pre == args.post and not args.force_checkpoint:
        logger.info("HEAD did not advance (%s); nothing to ping.", args.pre[:8])
        return 0

    pings = collect_pings(args.pre, args.post, force_checkpoint=args.force_checkpoint)
    if not pings:
        logger.info("No pingable events in %s..%s", args.pre[:8], args.post[:8])
        return 0

    if args.dry_run:
        logger.info("Dry-run: would queue %d ping(s)", len(pings))
        for p, body in pings:
            logger.info("  [%s] %s", p, body.splitlines()[0])
        return 0

    # S-019 — enqueue via the bot's pending-pings inbox instead of
    # POSTing direct to Telegram. The bot drains the inbox every ~5 s.
    # No more dependency on TELEGRAM_BOT_TOKEN being in this script's
    # process env (the bot has it; we just write a file).
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from send_ping import enqueue as _enqueue
    except ImportError as exc:
        logger.error("scripts/send_ping.py not importable: %s", exc)
        return 1

    failures = 0
    for priority, body in pings:
        try:
            _enqueue(body, priority=priority)
        except (OSError, ValueError) as exc:
            logger.error("enqueue failed [%s]: %s", priority, exc)
            failures += 1
    if failures:
        logger.error("%d / %d pings failed to enqueue", failures, len(pings))
        return 1
    logger.info("Queued %d ping(s) — bot drains within ~5 s", len(pings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
