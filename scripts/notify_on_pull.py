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
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger("notify_on_pull")

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_LOG = REPO_ROOT / "docs" / "claude" / "checkpoints" / "CHECKPOINT_LOG.md"
PENDING_PINGS = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"
# VM-local delivery log — every line in PENDING_PINGS that has been
# successfully enqueued has its content sha256 recorded here. Drains
# skip any line whose hash is already present, so old lines that ride
# along on subsequent git pulls don't re-fire. NOT git-tracked
# (``runtime_logs/`` is in .gitignore).
DELIVERED_HASHES = REPO_ROOT / "runtime_logs" / "pending_pings_delivered.txt"

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
GITHUB_COMMIT_URL = "https://github.com/benbaichmankass/ict-trading-bot/commit/{sha}"

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


def _line_hash(raw: str) -> str:
    """Stable sha256 of a stripped pending-pings.jsonl line.

    Used as the dedupe key in ``DELIVERED_HASHES``. Hashing the raw
    JSON line (rather than the parsed body) keeps the key stable
    across changes to the body-formatting code below — if the same
    line appears in a future pull cycle, we recognise it.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_delivered_hashes(path: Path) -> set[str]:
    """Read the VM-local delivery log. Empty / missing file → empty set."""
    if not path.exists():
        return set()
    try:
        return {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError as exc:  # noqa: BLE001
        logger.warning("delivered-hashes read error: %s — treating as empty", exc)
        return set()


def _record_delivered_hash(path: Path, h: str) -> None:
    """Append one hash to the delivery log. Best-effort — failing here
    is logged but not fatal (the next pull would re-fire the line, which
    is the failure mode that pre-dates this fix; we don't want a write
    error to break the ping path entirely)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(h + "\n")
    except OSError as exc:  # noqa: BLE001
        logger.warning("delivered-hashes append error: %s", exc)


# Friendly title per event type — the body the operator sees on the
# Claude update channel. The priority icon (ℹ️/🔔/🚨) is prepended by the
# bridge at send time, so these are content-only. Adding a new ping =
# adding an entry here + (optionally) a default priority below. Schemas
# for each event live in docs/claude/telegram-pings.md § Mandatory ping
# habit.
EVENT_LABELS: dict[str, str] = {
    "sprint-start":            "🟢 Sprint started",
    "checkpoint":              "📍 Checkpoint",
    "sprint-complete":         "✅ Sprint complete",
    "health-review-start":     "🩺 Health review started",
    "health-review-complete":  "🩺 Health review complete",
    "training-start":          "🧠 Training session started",
    "training-complete":       "🧠 Training session complete",
    "waiting-input":           "⏳ Waiting for your input",
    "blocker":                 "🛑 Blocked — needs you",
    "merge-review":            "🔎 Merge review",
}

# Default priority when a pending-pings.jsonl line omits "priority".
# Completions are high (you want to see results); blockers / waiting are
# urgent (you're being waited on); everything else is normal.
EVENT_DEFAULT_PRIORITY: dict[str, str] = {
    "sprint-complete":        "high",
    "health-review-complete": "high",
    "training-complete":      "high",
    "merge-review":           "high",
    "blocker":                "urgent",
    "waiting-input":          "urgent",
}


def _render_event_body(event: str, entry: dict) -> str:
    """Render one pending-pings.jsonl entry into a clean operator message.

    A title line (label — sprint — title) followed by any present detail
    fields, then any URLs. Unknown events fall back to the raw event
    name as the label so nothing is silently dropped.
    """
    head = [EVENT_LABELS.get(event, event)]
    for key in ("sprint", "title"):
        v = entry.get(key)
        if v:
            head.append(str(v))
    lines = [" — ".join(head)]
    for key, prefix in (
        ("cp_id", "CP"), ("next_cp", "Next"), ("phase", "Phase"),
        ("strategy", "Strategy"), ("model", "Model"),
        ("result", "Result"), ("grade", "Grade"),
        ("question", "Q"), ("summary", ""),
    ):
        v = entry.get(key)
        if v:
            lines.append(f"{prefix}: {v}" if prefix else str(v))
    for key in ("pr_url", "commit_url", "chat_url", "summary_url"):
        v = entry.get(key)
        if v:
            lines.append(str(v))
    return "\n".join(lines)


def _drain_pending_pings(
    path: Path, delivered: Optional[set[str]] = None,
) -> List[Tuple[str, str, str]]:
    """Read lines from *path* and return ``[(priority, body, line_hash)]``.

    Lines whose ``_line_hash`` is already in *delivered* are skipped —
    those have been enqueued on a prior pull and must not re-fire.

    The file is left in place; the dedupe via ``DELIVERED_HASHES``
    replaces the old "truncate in a follow-up commit" contract that
    quietly re-fired old pings on every merge. The caller is expected
    to record each delivered hash via ``_record_delivered_hash`` once
    enqueue succeeds.
    """
    if delivered is None:
        delivered = set()
    if not path.exists():
        return []
    out: List[Tuple[str, str, str]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            h = _line_hash(raw)
            if h in delivered:
                logger.info(
                    "pending-pings: skipping already-delivered line "
                    "(hash=%s…); old entries on subsequent pulls don't re-fire.",
                    h[:12],
                )
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("pending-pings: skipping malformed line: %r", raw[:100])
                continue
            event = str(entry.get("event") or "ping")
            priority = str(
                entry.get("priority")
                or EVENT_DEFAULT_PRIORITY.get(event, "normal")
            )
            out.append((priority, _render_event_body(event, entry), h))
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


def _diff_added_cp_ids(pre_sha: str, post_sha: str) -> List[str]:
    """Return CP-IDs whose ``## CP-…`` header was added (not just touched)
    in the diff range, newest first.

    Pre-fix the checkpoint ping fired whenever any commit in the pull
    window touched ``CHECKPOINT_LOG.md`` — which includes feature-PR
    merges that bring in an *old* sprint's checkpoint commit, and
    in-place edits to existing entries. Both shapes pinged the
    operator with the file's current topmost entry, even though that
    entry was already announced in a prior pull.

    The fix: parse the diff for added lines matching the CP header
    regex; only emit a ping when the topmost entry's CP-ID is in
    that set.
    """
    if not pre_sha or pre_sha == "unknown":
        return []
    try:
        out = subprocess.run(
            ["git", "diff", "-U0",
             f"{pre_sha}..{post_sha}",
             "--", "docs/claude/checkpoints/CHECKPOINT_LOG.md"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            check=False, timeout=10,
        )
        if out.returncode != 0:
            return []
    except (subprocess.SubprocessError, OSError):
        return []
    added: List[str] = []
    for line in out.stdout.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        m = _CP_HEADER_RE.match(line[1:])
        if m:
            added.append(m.group(1))
    return added


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
) -> List[Tuple[str, str, Optional[str]]]:
    """Order: blockers first (urgent), then queue drain, then checkpoint.

    Returns ``[(priority, body, line_hash_or_None)]``. Only drained
    ``pending-pings.jsonl`` entries carry a ``line_hash``; blocker /
    training / checkpoint pings naturally dedupe by their commit-range
    gating and do not need it. The caller records each non-None hash
    after a successful enqueue so subsequent pulls skip the same line.

    ``force_checkpoint=True`` emits the checkpoint ping even if the diff
    didn't touch ``CHECKPOINT_LOG.md`` — used by the deploy script's
    ``runtime_flags/auto_ping_test.flag`` path to verify the auto-ping
    leg without waiting for a real checkpoint commit.
    """
    pings: List[Tuple[str, str, Optional[str]]] = []
    for pri, body in _blocker_pings(pre_sha, post_sha):
        pings.append((pri, body, None))
    for pri, body in _training_workflow_pings(pre_sha, post_sha):
        pings.append((pri, body, None))
    delivered = _load_delivered_hashes(DELIVERED_HASHES)
    for pri, body, h in _drain_pending_pings(PENDING_PINGS, delivered):
        pings.append((pri, body, h))
    # Checkpoint ping only fires when the diff *added* a new CP header
    # whose CP-ID matches the file's current topmost entry. A merge
    # commit that brings an old checkpoint into main, or an in-place
    # edit to an existing entry, no longer re-pings the operator —
    # those events ride on the original checkpoint commit's ping.
    if force_checkpoint:
        cp_ping = _checkpoint_ping(post_sha)
        if cp_ping is not None:
            pings.append((cp_ping[0], cp_ping[1], None))
    elif _diff_touched_checkpoint_log(pre_sha, post_sha):
        added_ids = _diff_added_cp_ids(pre_sha, post_sha)
        parsed = _latest_cp_entry(CHECKPOINT_LOG)
        if parsed is not None and added_ids and parsed[0] == added_ids[0]:
            cp_ping = _checkpoint_ping(post_sha)
            if cp_ping is not None:
                pings.append((cp_ping[0], cp_ping[1], None))
        elif added_ids:
            logger.info(
                "notify_on_pull: skipping checkpoint ping — diff added "
                "%s but the file's topmost entry is %s (already pinged "
                "on its original commit)",
                added_ids[0], parsed[0] if parsed else "<unparsed>",
            )
        else:
            logger.info(
                "notify_on_pull: skipping checkpoint ping — diff touched "
                "CHECKPOINT_LOG.md but added no new CP header (in-place "
                "edit / merge of an old sprint commit)",
            )
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
        for p, body, _h in pings:
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
    for priority, body, line_hash in pings:
        try:
            # 2026-05-06 (BUG-058 follow-up): all session pings emitted
            # by this script — blockers, training stages, drained
            # pending-pings.jsonl entries, checkpoint commits — route
            # through @claude_ict_comms_bot per CLAUDE.md's two-bot
            # separation. Trade-execution alerts keep using the
            # default "trader" target via execution_diagnostics +
            # liveness_watchdog + order_monitor producers.
            _enqueue(body, priority=priority, target="claude")
        except (OSError, ValueError) as exc:
            logger.error("enqueue failed [%s]: %s", priority, exc)
            failures += 1
            continue
        # Record the hash *after* a successful enqueue so a transient
        # write failure on the bot's inbox dir doesn't permanently
        # mark a ping as delivered.
        if line_hash is not None:
            _record_delivered_hash(DELIVERED_HASHES, line_hash)
    if failures:
        logger.error("%d / %d pings failed to enqueue", failures, len(pings))
        return 1
    logger.info("Queued %d ping(s) — bot drains within ~5 s", len(pings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
