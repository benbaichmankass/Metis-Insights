"""Positive control for the PreToolUse merge-slot guard (BL-20260819-MERGE-SLOT-GUARD-DOES-NOT-FIRE).

The guard in ``.claude/settings.json`` is documented as DENYING
``merge_pull_request`` / ``enable_pr_auto_merge`` until a fresh per-PR marker
exists. It was believed broken because ten merges in one session sailed through
with no deny. The measured root cause is NOT the script: the Claude Code **web**
runtime never loads project hooks at all (``Hooks: Found 0 total hooks in
registry``, 1,379 consecutive log lines 2026-08-18 → 2026-08-20, never once a
non-zero count), so the guard is never INVOKED there. It does load and fire in
runtimes that honour project hooks (CLI / desktop).

That split is exactly why this file exists. The guard's *invocation* is
environment-dependent and cannot be asserted from CI; its *logic* can be, and
without a test the logic is free to rot unnoticed in the runtimes where it does
fire — a guard nobody can see failing is the shape this repo keeps paying for.

So: extract the hook's own command from ``.claude/settings.json`` (never a copy —
a copy would drift from the thing that ships) and run it against synthetic stdin.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = REPO / ".claude" / "settings.json"
MATCHER_KEYS = ("mcp__github__merge_pull_request", "mcp__github__enable_pr_auto_merge")


def _guard_command() -> str:
    """The merge-slot guard's shell command, read from the shipping settings file."""
    blocks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    matches = [
        b for b in blocks
        if all(k in b.get("matcher", "") for k in MATCHER_KEYS)
        and "merge-slot" in b["hooks"][0].get("_comment", "").lower()
    ]
    assert len(matches) == 1, f"expected exactly one merge-slot guard block, found {len(matches)}"
    return matches[0]["hooks"][0]["command"]


def _run(session_id: str, pr: int) -> tuple[str, str]:
    payload = json.dumps({"session_id": session_id, "tool_input": {"pullNumber": pr}})
    proc = subprocess.run(
        ["bash", "-c", _guard_command()],
        input=payload, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, "a PreToolUse hook must exit 0; a non-zero exit is read as a hook error"
    return proc.stdout, proc.stderr


def _marker(session_id: str, pr: int) -> pathlib.Path:
    return pathlib.Path(f"/tmp/.claude-merge-claim-{session_id}-{pr}")


@pytest.fixture
def sid(request) -> str:
    """A session id unique per test, with its marker cleaned up afterwards."""
    name = f"pytest{os.getpid()}{abs(hash(request.node.name)) % 100000}"
    yield name
    for p in pathlib.Path("/tmp").glob(f".claude-merge-claim-{name}-*"):
        p.unlink(missing_ok=True)


def test_no_marker_denies(sid):
    """THE control the guard never had: no marker must produce an explicit deny."""
    out, _ = _run(sid, 4242)
    payload = json.loads(out)["hookSpecificOutput"]
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"


def test_deny_names_the_specific_pr(sid):
    """A per-PR guard whose message omits the PR cannot tell you which claim to post."""
    out, _ = _run(sid, 4242)
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "#4242" in reason
    assert f"/tmp/.claude-merge-claim-{sid}-4242" in reason
    assert "6927" in reason, "the deny must name the coordination board issue"


def test_fresh_marker_allows(sid):
    """The escape hatch has to actually open, or the protocol is unrunnable."""
    m = _marker(sid, 4242)
    m.touch()
    try:
        out, _ = _run(sid, 4242)
        assert out.strip() == "", f"a fresh marker must emit nothing (allow), got: {out!r}"
    finally:
        m.unlink(missing_ok=True)


def test_marker_is_per_pr_not_blanket(sid):
    """One claim must not authorise a different PR -- the whole point of keying on <pr>."""
    m = _marker(sid, 4242)
    m.touch()
    try:
        out, _ = _run(sid, 9999)
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        m.unlink(missing_ok=True)


def test_stale_marker_denies(sid):
    """>20min is stale by design, so a claim cannot be left lying around all session."""
    m = _marker(sid, 4242)
    m.touch()
    old = time.time() - 3600
    os.utime(m, (old, old))
    try:
        out, _ = _run(sid, 4242)
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        m.unlink(missing_ok=True)


def test_guard_emits_no_shell_diagnostics(sid):
    """Regression: the reason string is built inside double quotes, so a stray backtick
    runs command substitution -- the word vanishes from the message and bash prints
    'command not found' to stderr. Measured 2026-08-20 on the word 'behind'."""
    out, err = _run(sid, 4242)
    assert "command not found" not in err, f"guard shells out unintentionally: {err.strip()!r}"
    assert err.strip() == "", f"guard must be silent on stderr, got: {err.strip()!r}"
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "behind" in reason, "the require-up-to-date explanation lost its subject to substitution"
