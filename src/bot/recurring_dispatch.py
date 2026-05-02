"""Recurring-session triggers used by the Telegram bot.

The operator types `/audit`, `/improve_strategy`, `/train_model`, or
`/roadmap` in Telegram. The bot:

1. Logs the trigger to ``runtime_logs/recurring_sessions.jsonl`` (audit
   trail; one JSON line per trigger).
2. Replies with a starter prompt the operator pastes into a fresh Claude
   Code session to begin the recurring session.

This module is intentionally infrastructure-light: it does NOT use the
``comms/`` system because the operator is initiating the session, not
asking Claude a question. The full session protocol lives in
``docs/sprints/recurring-*-prompt.md``.

For ``/roadmap``, the helper parses ``ROADMAP.md`` and returns a short
status summary (counts + in-progress + next).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SESSION_LOG_PATH = "runtime_logs/recurring_sessions.jsonl"
ROADMAP_PATH = "ROADMAP.md"

VALID_SESSION_TYPES = ("audit", "improve_strategy", "train_model")

_STARTER_PROMPTS: dict[str, str] = {
    "audit": (
        "Read CLAUDE.md and docs/sprints/recurring-hardening-prompt.md.\n\n"
        "Begin a recurring hardening session. Run Phase 1 (E2E health check) "
        "first. If anything fails, follow the outcome routing in the prompt — "
        "pivot, defer, or proceed only after operator weighs in. Otherwise:\n"
        " - For sessions 1-3, use the predetermined targets in section 2A.\n"
        " - For sessions 4+, use the prioritization formula in section 2B.\n\n"
        "End with the standard summary ping per Phase 3."
    ),
    "improve_strategy": (
        "Read CLAUDE.md and docs/sprints/recurring-strategy-improvement-prompt.md.\n\n"
        "Begin a recurring strategy improvement session{strategy_clause}. Run "
        "Phase 1 first. CRITICAL: this session NEVER edits parameters. It "
        "only proposes changes (Tier 3 — written to docs/strategy-reviews/) "
        "that require operator approval before any sprint touches them.\n\n"
        "End with the standard summary ping per Phase 3."
    ),
    "train_model": (
        "Read CLAUDE.md, docs/claude/ml-training-policy.md, and "
        "docs/sprints/recurring-model-training-prompt.md.\n\n"
        "Begin a recurring model training session{strategy_clause}. Run Phase "
        "1 first. CRITICAL: this session NEVER promotes a model to live. It "
        "trains a candidate, evaluates against the incumbent on holdout, and "
        "writes a promote/reject recommendation to docs/model-evals/.\n\n"
        "End with the standard summary ping per Phase 3."
    ),
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_trigger(
    repo_root: Path,
    session_type: str,
    args: Optional[list[str]] = None,
) -> dict:
    """Append a trigger event to the recurring-sessions audit log.

    Creates ``runtime_logs/`` if it doesn't exist. Returns the JSON
    object that was written so the caller can include the timestamp in
    the reply.
    """
    if session_type not in VALID_SESSION_TYPES:
        raise ValueError(
            f"unknown session_type {session_type!r}; "
            f"must be one of {VALID_SESSION_TYPES}"
        )
    entry = {
        "type": session_type,
        "args": list(args or []),
        "triggered_at": _utcnow_iso(),
    }
    log_path = Path(repo_root) / SESSION_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return entry


def build_starter_prompt(
    session_type: str,
    strategy: Optional[str] = None,
) -> str:
    """Return the prompt the operator pastes into a fresh Claude Code session."""
    if session_type not in _STARTER_PROMPTS:
        raise ValueError(
            f"unknown session_type {session_type!r}; "
            f"must be one of {tuple(_STARTER_PROMPTS)}"
        )
    template = _STARTER_PROMPTS[session_type]
    strategy_clause = ""
    if strategy:
        # Defensive: keep the clause short and shell-safe. The prompt is
        # plain text; we don't expect markdown rendering.
        clean = re.sub(r"[^a-zA-Z0-9_-]", "", strategy)[:64]
        if clean:
            strategy_clause = f" focused on the '{clean}' strategy"
    return template.format(strategy_clause=strategy_clause)


def render_roadmap_summary(roadmap_text: str) -> str:
    """Parse ROADMAP.md text and return a Telegram-ready status summary.

    Looks for:
    - sprint rows with status emojis (✅ Done, 🔄 In Progress, 🔜 Next, 📋 Backlog)
    - the most recent in-progress and next sprint titles

    Output is plain text (no parse_mode) per the BUG-009/030/031 lesson
    in CLAUDE.md.
    """
    counts = {
        "done": roadmap_text.count("✅ Done"),
        "in_progress": roadmap_text.count("🔄 In Progress"),
        "next": roadmap_text.count("🔜 Next"),
        "backlog": roadmap_text.count("📋 Backlog"),
    }

    in_progress_sprint = _extract_first_sprint_with_status(
        roadmap_text, "🔄 In Progress"
    )
    next_sprint = _extract_first_sprint_with_status(roadmap_text, "🔜 Next")

    lines = ["📍 Roadmap Status", ""]
    if in_progress_sprint:
        lines.append(f"🔄 In Progress: {in_progress_sprint}")
    if next_sprint:
        lines.append(f"🔜 Next: {next_sprint}")
    if not in_progress_sprint and not next_sprint:
        lines.append("(no sprint currently marked in progress or next)")
    lines.append("")
    lines.append(
        f"Sprint counts — ✅ {counts['done']} done · "
        f"🔄 {counts['in_progress']} in progress · "
        f"🔜 {counts['next']} next · "
        f"📋 {counts['backlog']} backlog"
    )
    lines.append("")
    lines.append("Full roadmap: ROADMAP.md")
    return "\n".join(lines)


def _extract_first_sprint_with_status(text: str, status_marker: str) -> Optional[str]:
    """Pull a "<sprint_id>: <title>" string from the first row matching status_marker.

    The roadmap rows look like:
        | S-014 | **Web Client V1 ...** — ... | 🔜 Next |
    We extract S-014 and the bold title. Falls back to truncated raw cell.
    """
    for line in text.splitlines():
        if status_marker not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        # Expect at least: '', sprint_id, title, status, ''
        if len(cells) < 4:
            continue
        sprint_id = cells[1]
        title_cell = cells[2]
        if not sprint_id:
            continue
        m = re.search(r"\*\*(.+?)\*\*", title_cell)
        title = m.group(1) if m else title_cell[:80]
        return f"{sprint_id}: {title}"
    return None
