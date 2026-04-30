"""Tests for src.bot.vm_runner — Tier 1/2 dispatch and Tier 3 refusals."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bot import vm_runner


# ---------------------------------------------------------------------------
# screen_for_tier3 — pre-flight refusals.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", [
    "please edit src/runtime/orders.py to bump the cap",
    "edit risk_counters.py",
    "git push --force origin main",
    "git push origin main",
    "rotate ANTHROPIC_API_KEY",
    "rotate the JWT_SIGNING_KEY",
    "rotate WEBAPP_PASSWORD_SHA256",
    "remove TELEGRAM_BOT_TOKEN",
    "rm -rf /home/ubuntu",
    "DROP TABLE trades",
    "systemctl mask ict-trader-live",
    "systemctl disable ict-trader-live.service",
    "cat /etc/ict-trader/web-api.env",
    "edit master-secrets.template.yaml",
    "open signal_writer.py and remove the audit log",
])
def test_tier3_patterns_refuse(prompt):
    refusal = vm_runner.screen_for_tier3(prompt)
    assert refusal is not None
    assert refusal.startswith("TIER 3 BLOCKED")


@pytest.mark.parametrize("prompt", [
    "what services are active?",
    "show the trader uptime and last error from journalctl",
    "git log --oneline -20",
    "systemctl restart ict-web-api",  # tier 2, not tier 3
    "edit src/web/api/main.py",        # not in tier 3 list
    "run pytest tests/test_web_api_pnl.py",
    "summarise the diff vs origin/main",
])
def test_benign_prompts_pass_screen(prompt):
    assert vm_runner.screen_for_tier3(prompt) is None


# ---------------------------------------------------------------------------
# handle_vm_command — happy path with stub dispatch.
# ---------------------------------------------------------------------------


@pytest.fixture
def vm_dirs(tmp_path):
    marker = tmp_path / "vm-marker"
    marker.write_text("host: testvm\n")
    prompt_dir = tmp_path / "prompts"
    transcript_dir = tmp_path / "transcripts"
    return marker, prompt_dir, transcript_dir


def _stub_dispatch_writes_transcript(message: str, rc: int = 0):
    def _inner(invocation_id, tier, prompt_path, transcript_path):
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(message, encoding="utf-8")
        return rc
    return _inner


def test_tier1_happy_path_returns_transcript(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    result = vm_runner.handle_vm_command(
        "show me the trader uptime",
        tier=1,
        now=1700_000_000.0,
        dispatch=_stub_dispatch_writes_transcript("uptime: 5d"),
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is True
    assert result.tier == 1
    assert result.invocation_id == "1700000000"
    assert "uptime: 5d" in result.summary
    assert result.transcript_path is not None
    assert result.transcript_path.exists()
    # The prompt should have been staged.
    staged = list(prompts.iterdir())
    assert len(staged) == 1
    assert staged[0].read_text(encoding="utf-8") == "show me the trader uptime"


def test_tier2_happy_path(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    result = vm_runner.handle_vm_command(
        "restart the web api",
        tier=2,
        now=1700_000_001.0,
        dispatch=_stub_dispatch_writes_transcript("restarted ict-web-api"),
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is True
    assert result.tier == 2
    assert "restarted ict-web-api" in result.summary


def test_dispatch_nonzero_rc_marks_not_ok(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    result = vm_runner.handle_vm_command(
        "show me the trader uptime",
        tier=1,
        now=1700_000_002.0,
        dispatch=_stub_dispatch_writes_transcript("partial output\nERROR: oom", rc=137),
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is False
    assert "exit 137" in result.summary
    assert "ERROR: oom" in result.summary


# ---------------------------------------------------------------------------
# Refusals at the entry point.
# ---------------------------------------------------------------------------


def test_missing_marker_refuses(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    marker.unlink()
    called = []
    def _spy(*a, **kw):
        called.append(a)
        return 0
    result = vm_runner.handle_vm_command(
        "show me the trader uptime",
        tier=1,
        dispatch=_spy,
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is False
    assert "VM marker" in result.summary
    assert called == []  # dispatch must not be invoked


def test_empty_prompt_refuses(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    result = vm_runner.handle_vm_command(
        "   ",
        tier=1,
        dispatch=_stub_dispatch_writes_transcript("noop"),
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is False
    assert "Empty prompt" in result.summary


def test_oversized_prompt_refuses(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    long_prompt = "x" * (vm_runner.MAX_PROMPT_CHARS + 1)
    result = vm_runner.handle_vm_command(
        long_prompt,
        tier=1,
        dispatch=_stub_dispatch_writes_transcript("noop"),
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is False
    assert "too long" in result.summary


def test_unknown_tier_refuses(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    result = vm_runner.handle_vm_command(
        "show me the trader uptime",
        tier=3,
        dispatch=_stub_dispatch_writes_transcript("noop"),
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is False
    assert "unknown tier 3" in result.summary


def test_tier3_pattern_blocks_before_dispatch(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    called = []
    def _spy(*a, **kw):
        called.append(a)
        return 0
    result = vm_runner.handle_vm_command(
        "git push --force origin main",
        tier=2,
        dispatch=_spy,
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is False
    assert result.summary.startswith("TIER 3 BLOCKED")
    assert called == []


def test_dispatch_exception_surfaces_cleanly(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    def _boom(*a, **kw):
        raise RuntimeError("systemd-run failed to start")
    result = vm_runner.handle_vm_command(
        "show me the trader uptime",
        tier=1,
        dispatch=_boom,
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is False
    assert "Runner dispatch failed" in result.summary
    assert "RuntimeError" in result.summary


def test_summary_truncated_to_telegram_limit(vm_dirs):
    marker, prompts, transcripts = vm_dirs
    big = "A" * 20_000
    result = vm_runner.handle_vm_command(
        "produce big output",
        tier=1,
        now=1700_000_100.0,
        dispatch=_stub_dispatch_writes_transcript(big),
        prompt_dir=prompts,
        transcript_dir=transcripts,
        vm_marker=marker,
    )
    assert result.ok is True
    text = result.telegram_text()
    assert len(text) <= vm_runner.MAX_REPLY_CHARS
    # Truncation prefers the tail so the trailing answer is preserved.
    assert text.rstrip().endswith("A")


# ---------------------------------------------------------------------------
# Permission profile JSON files — schema sanity.
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename,expected_tier", [
    ("claude-permissions.read.json", "1"),
    ("claude-permissions.write.json", "2"),
])
def test_tier_permission_files_are_valid_json(filename, expected_tier):
    path = REPO_ROOT / "deploy" / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"]["CLAUDE_VM_TIER"] == expected_tier
    assert data["permissions"]["defaultMode"] == "default"
    assert isinstance(data["permissions"]["allow"], list)
    assert isinstance(data["permissions"]["deny"], list)


def test_tier3_paths_denied_in_both_profiles():
    """Both profiles must deny edits to live-trading code, secrets, and
    /etc paths. This is the Tier 3 immutability guarantee."""
    for filename in ("claude-permissions.read.json", "claude-permissions.write.json"):
        path = REPO_ROOT / "deploy" / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        deny = " ".join(data["permissions"]["deny"])
        # Live-trading code is hard-blocked everywhere.
        assert "src/runtime/orders.py" in deny, f"{filename}: orders.py must be denied"
        assert "src/runtime/risk_counters.py" in deny, f"{filename}: risk_counters.py must be denied"
        assert "src/runtime/notify.py" in deny, f"{filename}: notify.py must be denied"
        # Secrets surfaces.
        assert "/etc/claude/" in deny, f"{filename}: /etc/claude must be denied"
        assert "rm -rf" in deny, f"{filename}: rm -rf must be denied"
        # Push-to-main protection.
        assert any("git push" in d and "main" in d for d in data["permissions"]["deny"]), (
            f"{filename}: push-to-main must be denied"
        )
        assert any("git push --force" in d for d in data["permissions"]["deny"]), (
            f"{filename}: force-push must be denied"
        )


def test_read_profile_denies_all_writes():
    """The tier-1 profile must deny every form of mutation, not just
    Tier 3. A tier-1 invocation is read-only by definition."""
    path = REPO_ROOT / "deploy" / "claude-permissions.read.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    deny = data["permissions"]["deny"]
    for required in ("Edit", "Write", "Bash(git push:*)", "Bash(git commit:*)"):
        assert required in deny, f"tier-1 must deny {required!r}"
