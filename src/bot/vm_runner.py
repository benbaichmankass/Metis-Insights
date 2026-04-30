"""VM-resident Claude Code runner — Telegram bridge.

The Telegram bot's ``/vm`` and ``/vm_write`` commands route through this
module. ``handle_vm_command`` is the single entry point:

* parses the prompt off the Telegram message,
* refuses anything resembling a Tier 3 attempt before even spawning,
* writes the prompt to ``/run/claude/prompts/<id>.txt``,
* dispatches a oneshot ``claude-vm-runner@<id>.service`` with the right
  permission profile (read.json for Tier 1, write.json for Tier 2),
* tails the transcript and posts a summary back to the chat.

Tier 2 invocations require a Telegram confirmation step BEFORE this
module is called — the bot's command handler does the
"reply YES to continue" dance and only invokes ``handle_vm_command``
with ``tier=2`` once confirmation is in.

This module is unit-testable: the systemd dispatch is gated behind
``_dispatch`` which the tests replace with a stub.

See ``docs/claude/vm-operator-mode.md`` for the authority contract.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths and tier wiring.
# ---------------------------------------------------------------------------

VM_MARKER = Path("/etc/claude/vm-marker")
PROMPT_DIR = Path("/run/claude/prompts")
TRANSCRIPT_DIR = Path("/var/log/claude-vm")

PROFILE_BY_TIER = {
    1: "/etc/claude/permissions.read.json",
    2: "/etc/claude/permissions.write.json",
}

MAX_PROMPT_CHARS = 4000
MAX_REPLY_CHARS = 3500
RUNNER_TIMEOUT_S = 300

# ---------------------------------------------------------------------------
# Tier 3 pre-flight refusals.
# ---------------------------------------------------------------------------

# These patterns are matched against the raw prompt text BEFORE the prompt
# is handed to Claude. Belt-and-braces with the deny rules in the
# permission profiles: even if the operator types it, the runner won't
# spawn. Patterns are intentionally broad — false positives just mean
# "rephrase your request."
TIER3_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\borders?\.py\b",
     "live-trading orders code is immutable from VM-runner"),
    (r"\brisk_counters?\.py\b",
     "risk counters are immutable from VM-runner"),
    (r"\bsignal_writer\.py\b",
     "signal writer is immutable from VM-runner"),
    (r"\bmaster[-_]secrets",
     "master secrets file cannot be touched from VM-runner"),
    (r"\bforce[- ]?push",
     "force-push is hard-blocked"),
    (r"\bpush.*\bmain\b",
     "direct push to main is hard-blocked — use a PR"),
    (r"\b(ANTHROPIC|TELEGRAM|JWT|WEBAPP|BYBIT|BINANCE).*?(KEY|TOKEN|SHA256|SECRET)\b",
     "credential rotation is out-of-band only"),
    (r"\brm\s+-rf\b",
     "rm -rf is hard-blocked"),
    (r"\bDROP\s+TABLE\b",
     "DROP TABLE against the journal is hard-blocked"),
    (r"\bsystemctl\s+(disable|mask)\s+ict-trader-live",
     "disabling/masking the trader is hard-blocked"),
    (r"\bcat\s+.*\.env\b",
     "echoing env files is hard-blocked"),
)

_TIER3_COMPILED = tuple((re.compile(p, re.IGNORECASE), reason)
                        for p, reason in TIER3_PATTERNS)


def screen_for_tier3(prompt: str) -> Optional[str]:
    """Return a refusal reason if the prompt trips a Tier 3 guard, else None."""
    for pattern, reason in _TIER3_COMPILED:
        m = pattern.search(prompt)
        if m:
            return f"TIER 3 BLOCKED: '{m.group(0)}' — {reason}"
    return None


# ---------------------------------------------------------------------------
# Result dataclass — what the bot posts back to Telegram.
# ---------------------------------------------------------------------------


@dataclass
class RunnerResult:
    ok: bool
    tier: int
    invocation_id: str
    transcript_path: Optional[Path]
    summary: str  # The text to send to Telegram. Already truncated.

    def telegram_text(self) -> str:
        return self.summary[:MAX_REPLY_CHARS]


# ---------------------------------------------------------------------------
# Dispatch — split out so tests can replace it without invoking systemd.
# ---------------------------------------------------------------------------


DispatchFn = Callable[[str, int, Path, Path], int]
"""Signature: (invocation_id, tier, prompt_path, transcript_path) -> return code."""


def _systemd_dispatch(
    invocation_id: str, tier: int, prompt_path: Path, transcript_path: Path
) -> int:
    import subprocess  # noqa: WPS433 — local import keeps tests hermetic
    cmd = [
        "systemd-run",
        f"--unit=claude-vm-runner@{invocation_id}",
        "--wait",
        "--collect",
        f"--property=TimeoutStartSec={RUNNER_TIMEOUT_S}",
        f"--setenv=CLAUDE_VM_PROFILE={PROFILE_BY_TIER[tier]}",
        f"--setenv=CLAUDE_VM_PROMPT_FILE={prompt_path}",
        f"--setenv=CLAUDE_VM_TRANSCRIPT={transcript_path}",
        "/bin/systemctl", "start", f"claude-vm-runner@{invocation_id}.service",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def handle_vm_command(
    raw_prompt: str,
    tier: int,
    *,
    now: Optional[float] = None,
    dispatch: DispatchFn = _systemd_dispatch,
    prompt_dir: Path = PROMPT_DIR,
    transcript_dir: Path = TRANSCRIPT_DIR,
    vm_marker: Path = VM_MARKER,
) -> RunnerResult:
    """Top-level handler for ``/vm`` and ``/vm_write``.

    Returns a ``RunnerResult`` with the message to post back to Telegram.
    Never raises — failure modes return ``ok=False`` with a human-readable
    summary.
    """
    if tier not in (1, 2):
        return RunnerResult(
            ok=False, tier=tier, invocation_id="",
            transcript_path=None,
            summary=f"Internal error: unknown tier {tier}.",
        )

    if not vm_marker.exists():
        return RunnerResult(
            ok=False, tier=tier, invocation_id="",
            transcript_path=None,
            summary="VM marker /etc/claude/vm-marker missing — bootstrap not run "
                    "or runner is being invoked off-VM. Refusing.",
        )

    prompt = (raw_prompt or "").strip()
    if not prompt:
        return RunnerResult(
            ok=False, tier=tier, invocation_id="",
            transcript_path=None,
            summary="Empty prompt — nothing to do.",
        )
    if len(prompt) > MAX_PROMPT_CHARS:
        return RunnerResult(
            ok=False, tier=tier, invocation_id="",
            transcript_path=None,
            summary=f"Prompt too long ({len(prompt)} chars; max {MAX_PROMPT_CHARS}). "
                    "Trim it down or split into smaller invocations.",
        )

    refusal = screen_for_tier3(prompt)
    if refusal:
        logger.warning("vm_runner refusal: %s", refusal)
        return RunnerResult(
            ok=False, tier=tier, invocation_id="",
            transcript_path=None,
            summary=refusal,
        )

    invocation_id = str(int(now if now is not None else time.time()))
    prompt_path = prompt_dir / f"{invocation_id}.txt"
    transcript_path = transcript_dir / f"{invocation_id}.log"

    try:
        prompt_dir.mkdir(parents=True, exist_ok=True)
        transcript_dir.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        # The systemd unit reads ${CLAUDE_VM_PROMPT_FILE}; ubuntu-readable
        # is enough since the runner runs as ubuntu.
        os.chmod(prompt_path, 0o640)
    except OSError as exc:
        return RunnerResult(
            ok=False, tier=tier, invocation_id=invocation_id,
            transcript_path=None,
            summary=f"Could not stage prompt file: {exc.__class__.__name__}.",
        )

    try:
        rc = dispatch(invocation_id, tier, prompt_path, transcript_path)
    except Exception as exc:  # noqa: BLE001 — we explicitly surface the failure
        logger.exception("vm_runner dispatch failed")
        return RunnerResult(
            ok=False, tier=tier, invocation_id=invocation_id,
            transcript_path=None,
            summary=f"Runner dispatch failed: {exc.__class__.__name__}.",
        )

    summary = _build_summary(rc, transcript_path, tier, invocation_id)
    return RunnerResult(
        ok=(rc == 0),
        tier=tier,
        invocation_id=invocation_id,
        transcript_path=transcript_path if transcript_path.exists() else None,
        summary=summary,
    )


def _build_summary(
    rc: int, transcript_path: Path, tier: int, invocation_id: str
) -> str:
    header = (
        f"VM runner — tier {tier}, id {invocation_id}, exit {rc}\n"
        "----------------------------------------\n"
    )
    if not transcript_path.exists():
        return header + "(no transcript captured)"
    try:
        text = transcript_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return header + f"(could not read transcript: {exc.__class__.__name__})"
    # Trim to fit Telegram limits, prefer the tail (it usually has the
    # final answer / error).
    body_budget = MAX_REPLY_CHARS - len(header) - 32
    if len(text) > body_budget:
        text = "...\n" + text[-body_budget:]
    return header + text
