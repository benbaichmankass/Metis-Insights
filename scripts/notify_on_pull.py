#!/usr/bin/env python3
"""S-016 H3 — Telegram ping fanout, called from deploy_pull_restart.sh.

Designed to be lean and bulletproof:

* Stdlib + ``requests`` only — no pandas / no AlertManager / no chain
  of optional imports that could break the ping path when the bot
  itself is broken.
* Idempotent: invoked with ``--pre <sha> --post <sha>``; if HEAD did
  not advance, sends nothing.
* Four sources of pings, processed in priority order:
    1. Blocker commits — any commit in (pre, post] whose subject
       starts with ``[BLOCKED-PM]``. Emitted as ``urgent``.
    1b. PR merges — every commit in (pre, post] that landed a PR on
       ``main``, rolled up into ONE message that names each merge
       individually. This is the operator's standing "tell me when a
       PR merges" ask; nothing implemented it before 2026-09-02.
       See § "Source 4" for why it lives here and not in a workflow,
       and for the rate-limit decision.
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
* **A queued row's BODY is never silently dropped.** The renderer
  hand-maintains the transport ENVELOPE and renders everything else,
  rather than hand-maintaining a list of content keys and discarding
  what is not on it — see ``ENVELOPE_KEYS``. Until 2026-09-02 it did
  the latter, and **21 of the 50 rows** then in
  ``docs/claude/pending-pings.jsonl`` rendered to nothing but their
  event label. A row that genuinely carries no content is delivered
  saying so, never as a bare label.

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
# One owner of the repo slug. The old name is kept deliberately: CLAUDE.md
# records that GitHub's 301 resolves it everywhere and that a rename sweep is
# NOT to be chased. `GITHUB_COMMIT_URL` is byte-identical to what it was.
GITHUB_REPO_URL = "https://github.com/benbaichmankass/ict-trading-bot"
GITHUB_COMMIT_URL = GITHUB_REPO_URL + "/commit/{sha}"
GITHUB_COMPARE_URL = GITHUB_REPO_URL + "/compare/{pre}...{post}"

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
    """Return [(sha, subject), ...] for commits in (pre_sha, post_sha].

    ⚠️ COLLAPSES "we could not look" INTO "nothing is there" — an unknown
    ``pre_sha`` and a failed ``git log`` both come back ``[]``, exactly like a
    genuinely empty range. That is tolerable for the blocker/training scanners
    (a missed tag re-fires on the next pull, because the state file only
    advances on success) and is NOT tolerable for a caller that wants to say
    "no PRs merged" out loud. Such a caller uses
    ``_commit_subjects_or_none`` below and gets ``None`` for the read failure.
    """
    return _commit_subjects_or_none(pre_sha, post_sha) or []


def _commit_subjects_or_none(
    pre_sha: str, post_sha: str,
) -> Optional[List[tuple[str, str]]]:
    """``[(sha, subject), ...]``, or ``None`` when the range could not be read.

    Three states, never collapsed: a list of commits · ``[]`` (we looked, the
    range is empty) · ``None`` (*we did not look* — no usable ``pre_sha``, or
    git refused). ``_commit_subjects`` folds the last two together for its
    historical callers; nothing else should.
    """
    if not pre_sha or pre_sha == "unknown":
        return None
    try:
        out = subprocess.run(
            ["git", "log", "--format=%H%x09%s", f"{pre_sha}..{post_sha}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            check=False, timeout=10,
        )
        if out.returncode != 0:
            logger.warning("git log failed: %s", out.stderr.strip())
            return None
        pairs = []
        for line in out.stdout.splitlines():
            if "\t" not in line:
                continue
            sha, subject = line.split("\t", 1)
            pairs.append((sha.strip(), subject.strip()))
        return pairs
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("git log error: %s", exc)
        return None


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
# Source 4 — PR merges that landed on main in the pulled range
# ---------------------------------------------------------------------------
#
# WHY THIS LIVES HERE AND NOT IN A SANDBOX-SIDE EMITTER. The operator's standing
# ask — "every time a PR merges, I'm supposed to get updated about that" — had
# NO implementation. `scripts/ops/work_phase_ping.py` was the nearest thing and
# it pings on a work object's `lifecycle` moving; it has no concept of a PR at
# all, which is why its journal line read `No pingable events in
# 90b541a9..2156e8a6` across a range that contained five merges. The code was
# doing exactly what it said; nobody had built the thing being asked for.
#
# This script already receives the EXACT commit range that was pulled
# (`--pre <last-notified-head> --post <post-sync-head>`, advanced only on a
# successful send) and already walks it for blocker and training tags. A merge
# is in that range by construction. So the merge ping needs no new queue file,
# no new committer, and no new clock:
#
#   * NO CLOCK PROBLEM. It rides `ict-git-sync.timer` (OnUnitActiveSec=5min),
#     which is observably firing, rather than a GitHub Actions cron — measured
#     on `work-digest.yml`, which declares `20 * * * *` and fired 5 times in a
#     day at :19, :10, :33 and :47, never on its declared minute.
#   * NO FEEDBACK LOOP. A workflow that committed a ping row to `main` would
#     itself be a merge to `main`, so it would ping about its own ping. Nothing
#     here writes to the repo, so that shape cannot arise.
#   * NO EXTRA CI. Landing a row through `.github/actions/commit-to-main` costs
#     a PR plus a full required-check run (pytest alone is 12.9-14.6 min) per
#     merge. On a 20-merge day that is 20 extra PRs to announce 20 merges.
#
# RATE LIMIT — THE DECISION, STATED. This repo has measured what an un-latched
# alarm does: 202 of 376 CRITICALs in one window were a single alert, which
# trained the operator past the one channel reserved for an unprotected
# position. So the choice between "one ping per merge" and "one rolled-up
# message per pull" is made deliberately, and it is the second — with the
# constraint that NOTHING IS DROPPED. Every merge in the range is named
# individually, with its PR number and subject; the roll-up bounds the number
# of MESSAGES (at most one per ~5-minute pull), never the number of MERGES
# reported. A window containing one merge therefore produces a message about
# that one merge, which is the per-merge ping that was asked for.
#
# AUTOMATION MERGES ARE INCLUDED, deliberately and visibly. `chore(ops): …
# (auto)` bookkeeping merges are real merges to `main`, and silently filtering
# them would be this module deciding what the operator is allowed to see. They
# are counted separately in the header so the decision to suppress them later
# is the operator's, made against a number.

# Every landing on `main` is a squash merge whose subject ends `(#NNNN)`.
# MEASURED over the last 500 commits on `origin/main` (2026-09-02): 500 of 500
# match this shape, 0 true merge commits, 0 unmatched. A direct push to `main`
# is refused (GH006, 3 required checks), so there is no third shape today — the
# merge-commit form below is defensive, for a repo setting that changes.
_SQUASH_MERGE_RE = re.compile(r"^(?P<title>.+?)\s+\(#(?P<pr>\d+)\)$")
_MERGE_COMMIT_RE = re.compile(r"^Merge pull request #(?P<pr>\d+) from \S+")

# How many merges are named before the message says it is truncating. A pull
# window normally holds 0-3; a VM that was down for a day can hold dozens, and a
# 200-line Telegram message is not a notification. The overflow is STATED in the
# body rather than silently cut — a message that quietly drops rows is the
# failure this whole module was repaired for on 2026-09-02.
MERGE_PING_MAX_LISTED = 15
_MERGE_TITLE_MAX = 110


def _parse_merge_subject(subject: str) -> Optional[tuple[str, str]]:
    """``(pr_number, title)`` for a subject that landed a PR, else ``None``."""
    m = _SQUASH_MERGE_RE.match(subject)
    if m:
        return m.group("pr"), m.group("title").strip()
    m = _MERGE_COMMIT_RE.match(subject)
    if m:
        return m.group("pr"), ""
    return None


def _merge_pings(pre_sha: str, post_sha: str) -> List[tuple[str, str]]:
    """One rolled-up ping naming every PR that merged in (pre_sha, post_sha].

    Returns ``[]`` for BOTH "nothing merged" and "we could not read the range",
    because a caller that appends to a ping list has nowhere to put a third
    state — but the two are logged differently and must never be reported to the
    operator as the same thing. Silence here is not evidence that nothing
    merged.
    """
    subjects = _commit_subjects_or_none(pre_sha, post_sha)
    if subjects is None:
        logger.warning(
            "merge-ping: could not read the commit range %s..%s — NOT the same "
            "as 'no PRs merged'; nothing is reported for this pull.",
            (pre_sha or "<empty>")[:8], (post_sha or "<empty>")[:8],
        )
        return []

    # git log is newest-first; the operator reads "what happened since I last
    # looked", so present it in the order it happened.
    merges = []
    for sha, subject in reversed(subjects):
        parsed = _parse_merge_subject(subject)
        if parsed is None:
            continue
        pr, title = parsed
        merges.append((sha, pr, title))

    if not merges:
        logger.info("merge-ping: looked at %d commit(s) in %s..%s; none landed "
                    "a PR.", len(subjects), pre_sha[:8], post_sha[:8])
        return []

    auto = sum(1 for _s, _p, t in merges if "(auto)" in t)
    header = f"🔀 {len(merges)} PR{'s' if len(merges) != 1 else ''} merged to main"
    if auto:
        header += f" ({auto} automated)"
    lines = [header]
    for _sha, pr, title in merges[:MERGE_PING_MAX_LISTED]:
        if len(title) > _MERGE_TITLE_MAX:
            title = title[: _MERGE_TITLE_MAX - 1].rstrip() + "…"
        lines.append(f"#{pr} {title}".rstrip())
    hidden = len(merges) - MERGE_PING_MAX_LISTED
    if hidden > 0:
        lines.append(
            f"…and {hidden} more not listed here (cap {MERGE_PING_MAX_LISTED}) "
            f"— the compare link below has all {len(merges)}."
        )
    lines.append(GITHUB_COMPARE_URL.format(pre=pre_sha, post=post_sha))

    # ⚠️ THE SUCCESS PATH MUST LOG TOO, AND SHIPPING WITHOUT THIS WAS A REAL
    # GAP — found 2026-09-03 trying to verify the mechanism on the fleet.
    #
    # The two branches above log when the range is UNREADABLE and when it holds
    # NO merges, and `main()` logs only `Queued N ping(s)` with no source. So on
    # a healthy run — the one anybody wants to confirm — the journal said
    # nothing about which source produced the ping, and zero "merge-ping" lines
    # was indistinguishable between *it found merges every time* and *it never
    # ran at all*. Measured on the live VM over 5h13m of `ict-git-sync` journal
    # (1500 lines, 00:54:47Z-06:07:32Z on 2026-09-03): 5 notify runs, 5 queued
    # pings, and **0 lines mentioning merge-ping in either direction**. The
    # mechanism had to be reconstructed by replaying the five pulled ranges
    # through the deployed code instead of read off the box.
    #
    # A reconstruction is not an observation, which is the whole distinction
    # this repo keeps paying for. One line closes it.
    logger.info("merge-ping: %d merge(s) in %s..%s -> %s", len(merges),
                pre_sha[:8], post_sha[:8],
                ", ".join(f"#{pr}" for _sha, pr, _t in merges))
    return [("normal", "\n".join(lines))]


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


# ── What the RENDERER may drop, and what it may not ──────────────────────────
#
# ⚠️ THE HAND-MAINTAINED LIST IS THE **ENVELOPE**, NEVER THE CONTENT. That
# inversion is the whole fix, and it is not a style choice.
#
# Until 2026-09-02 the body was built from a fixed list of CONTENT keys, and any
# key not on it was silently discarded. That list is open-ended by construction:
# it grows with every producer, so the next producer that picks a key nobody
# thought of fails identically and just as silently. It had already failed for
# **21 of the 50 rows** in ``docs/claude/pending-pings.jsonl`` (measured
# 2026-09-02 over the committed file, n=50) — every row keyed on ``message``,
# which is what BOTH ``scripts/ops/work_phase_ping.py`` (Phase A) and
# ``scripts/ops/work_digest.py`` (Phase B) write. The operator's 03:24 message
# on 2026-09-02 read, in its entirety, ``work_digest``.
#
# The envelope, by contrast, is CLOSED: it is exactly the fields this transport
# itself sets or consumes — the row's timestamp, plus the three arguments of
# ``send_ping.enqueue(body, priority, target)`` and the event discriminator. It
# cannot grow without a change to the transport, and the failure directions are
# opposite:
#
#   * a new CONTENT key not listed here  → it RENDERS. Content preserved.
#   * a new ENVELOPE key not listed here → one metadata line leaks into the
#     operator's channel. Visible, cosmetic, self-announcing.
#
# So the residual risk of getting this list wrong is a leaked key, not a lost
# message. That is the trade this design accepts, deliberately.
#
# Pinned by ``tests/test_notify_render_no_silent_drop.py`` against the live
# queue file, the way ``LIVE_BACKLOGS`` is pinned against the backlogs on disk
# (``tests/test_backlog_append.py``) — a coverage list that can fall behind
# unnoticed is the defect, not the list.
ENVELOPE_KEYS: frozenset[str] = frozenset({
    "at",           # producer timestamp
    "reviewed_at",  # ditto, the older spelling — /system-review's producers
    "target",       # -> send_ping.enqueue(target=)
    "priority",     # -> send_ping.enqueue(priority=)
    "event",        # the discriminator; becomes the label
})

# Curated CONTENT keys — order and labels are the operator-facing presentation,
# and are deliberately unchanged from the pre-fix renderer so that every ping
# already rendering correctly keeps rendering byte-identically.
HEAD_KEYS: tuple[str, ...] = ("sprint", "title")
BODY_KEYS: tuple[tuple[str, str], ...] = (
    ("cp_id", "CP"), ("next_cp", "Next"), ("phase", "Phase"),
    ("strategy", "Strategy"), ("model", "Model"),
    ("result", "Result"), ("grade", "Grade"),
    ("question", "Q"), ("summary", ""),
)
URL_KEYS: tuple[str, ...] = ("pr_url", "commit_url", "chat_url", "summary_url")
CURATED_KEYS: frozenset[str] = frozenset(
    HEAD_KEYS + tuple(k for k, _ in BODY_KEYS) + URL_KEYS
)

_UNKNOWN_VALUE_MAX = 600


def _render_unknown_value(key: str, value: object) -> Optional[str]:
    """Render one key this module has never heard of. ``None`` == nothing to say.

    The rule keys on the SHAPE OF THE VALUE, not on a list of names — a list of
    names is the thing that failed:

      * a multi-line string is PROSE, and renders bare. A ``message:`` label in
        front of a five-line digest is noise, and the producer already wrote the
        text for a human.
      * any other scalar renders ``key: value``, so an unrecognised field is
        legible even though nobody chose a label for it.
      * a dict/list renders as compact JSON, truncated — it is almost certainly
        not meant for the operator, but showing it is still better than
        dropping it, and its ugliness is the signal to give it a real label.
    """
    if value is None or value is False:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return text if "\n" in text else f"{key}: {text}"
    if isinstance(value, (int, float, bool)):
        return f"{key}: {value}"
    try:
        blob = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = repr(value)
    if len(blob) > _UNKNOWN_VALUE_MAX:
        blob = blob[:_UNKNOWN_VALUE_MAX] + "…"
    return f"{key}: {blob}"


def render_event_parts(event: str, entry: dict) -> tuple[list[str], int]:
    """``(lines, content_count)`` for one entry — the ONE owner of "is this row
    empty?".

    Split out from ``_render_event_body`` so the CI guard
    (``scripts/ci/check_pending_pings_render.py``) can ask that question by
    IMPORTING the answer rather than re-deriving it from the rendered text. The
    guard's first draft did re-derive it — testing whether the body equalled the
    bare label — and that predicate was already stale against this module's own
    empty-ping notice, so it reported a planted defect as clean. Two copies of a
    predicate is exactly how they drift; this is the same reasoning that makes
    ``work_digest`` import ``PING_WORTHY`` instead of restating it.
    """
    head = [EVENT_LABELS.get(event, event)]
    content = 0
    for key in HEAD_KEYS:
        v = entry.get(key)
        if v:
            head.append(str(v))
            # ⚠️ A HEAD FIELD IS CONTENT. `sprint`/`title` are rendered into the
            # title line rather than onto their own line, and an earlier draft
            # of this counter forgot that — which made every well-formed
            # `sprint-start` row (label + sprint + title, and nothing else)
            # trip the empty-ping notice. Caught by the byte-identical
            # regression proof over the live queue, not by review.
            content += 1
    lines = [" — ".join(head)]
    for key, prefix in BODY_KEYS:
        v = entry.get(key)
        if v:
            lines.append(f"{prefix}: {v}" if prefix else str(v))
            content += 1
    # Everything the curated list has never heard of. Insertion order is the
    # producer's own order (json.loads preserves it), so a row reads the way it
    # was written.
    for key, value in entry.items():
        if key in ENVELOPE_KEYS or key in CURATED_KEYS:
            continue
        rendered = _render_unknown_value(key, value)
        if rendered is not None:
            lines.append(rendered)
            content += 1
    for key in URL_KEYS:
        v = entry.get(key)
        if v:
            lines.append(str(v))
            content += 1
    return lines, content


def _render_event_body(event: str, entry: dict) -> str:
    """Render one pending-pings.jsonl entry into a clean operator message.

    A title line (label — sprint — title), then the curated detail fields, then
    **every content key this module does not recognise**, then any URLs.

    ⚠️ **NOTHING IS SILENTLY DROPPED — neither the label nor the body.** The old
    docstring asserted exactly that guarantee (*"Unknown events fall back to the
    raw event name as the label so nothing is silently dropped"*) while
    providing only half of it: the LABEL was preserved and the BODY was
    discarded. A comment that promises a property the code does not have is how
    the next reader stops looking, so it is corrected rather than softened.

    A row that renders to nothing but its label is a **producer defect**, and it
    says so out loud in the operator's channel rather than arriving as a
    plausible empty ping — see ``_EMPTY_BODY_NOTE``.
    """
    lines, content = render_event_parts(event, entry)
    if content == 0:
        logger.error(
            "pending-pings: row for event=%r rendered NO body; keys=%s. "
            "Queuing it with an explicit empty-ping notice rather than sending "
            "a bare label.", event, sorted(entry),
        )
        lines.append(_EMPTY_BODY_NOTE.format(keys=", ".join(sorted(entry)) or "(none)"))
    return "\n".join(lines)


# ⚠️ WHICH WAY AN EMPTY ROW FAILS, AND WHY THAT DIRECTION IS THE SAFE ONE.
#
# The alternative was to REFUSE to enqueue a body-less row. That is loud at
# enqueue time — and enqueue time is inside ``notify_on_pull.py`` on the live
# VM, where "loud" means a ``logger.error`` into journald. This repo has
# measured, repeatedly, that journald is where an alarm goes to be unread: the
# IB over-cover and Bybit over-cover pages both detected correctly for weeks and
# reached NOBODY because ``logger.error`` never touches ``outcomes.jsonl``, and
# so never touches Telegram, the notifications banner, or /api/bot/logs.
# Refusing here would move a silent drop from the renderer into journald — the
# third silent drop, wearing a different hat.
#
# So the row is sent, and it announces its own emptiness. The operator learns
# that a ping fired with nothing to say, which is a defect report they can act
# on; they do not learn nothing, and they never receive a confident bare label
# that looks like oversight happened.
_EMPTY_BODY_NOTE = (
    "⚠️ EMPTY PING — this row carried no renderable content (keys: {keys}). "
    "That is a PRODUCER defect, not a quiet day: something queued a "
    "notification with nothing in it."
)


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
    """Order: blockers first (urgent), then merges, then queue drain, then checkpoint.

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
    for pri, body in _merge_pings(pre_sha, post_sha):
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
