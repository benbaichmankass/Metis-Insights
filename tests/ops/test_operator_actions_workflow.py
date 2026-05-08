"""Tests for the operator-actions GitHub workflow + wrapper scripts.

These tests are static — they parse YAML and read shell scripts; they
do NOT execute the workflow or SSH anywhere. They guard the contract
documented in `docs/claude/operator-actions.md`:

* The action allowlist is a single source of truth across the
  workflow, the wrappers, and the doc.
* No freeform / arbitrary-command input ever sneaks into the workflow.
* Every wrapper script exists, is executable, parses with `bash -n`,
  uses `set -euo pipefail`, and sources `_lib.sh`.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "operator-actions.yml"
OPS_DIR = REPO_ROOT / "scripts" / "ops"
DOC = REPO_ROOT / "docs" / "claude" / "operator-actions.md"

# Single source of truth for the allowlist as expected by every layer.
EXPECTED_ACTIONS = {
    "status-check": "status_check.sh",
    "pull-latest-logs": "pull_logs.sh",
    "pull-and-deploy": "pull_and_deploy.sh",
    "restart-bot-service": "restart_bot.sh",
    "reboot-vm": "reboot_vm.sh",
}

TIER_2_ACTIONS = {"pull-and-deploy", "restart-bot-service", "reboot-vm"}


@pytest.fixture(scope="module")
def workflow_dict() -> dict:
    """Parse the workflow YAML.

    PyYAML 5.x+ parses bare `on:` as the boolean `True` (YAML 1.1
    legacy). We treat either key as equivalent.
    """
    if yaml is None:
        pytest.skip("PyYAML not available in this env.")
    with WORKFLOW.open() as f:
        d = yaml.safe_load(f)
    if "on" not in d and True in d:
        d["on"] = d.pop(True)
    return d


def test_workflow_file_exists() -> None:
    assert WORKFLOW.exists(), f"Missing workflow: {WORKFLOW}"


def test_workflow_dispatch_only(workflow_dict: dict) -> None:
    """Workflow must be triggerable only via workflow_dispatch.

    No issue/PR/push triggers — Tier-2 dispatch should require the
    operator's deliberate "Run workflow" click.
    """
    on = workflow_dict["on"]
    assert isinstance(on, dict)
    assert set(on.keys()) == {"workflow_dispatch"}, (
        f"operator-actions must be workflow_dispatch-only; got triggers: {list(on)}"
    )


def test_action_input_is_choice_with_full_allowlist(workflow_dict: dict) -> None:
    inputs = workflow_dict["on"]["workflow_dispatch"]["inputs"]
    assert "action" in inputs
    action = inputs["action"]
    assert action.get("required") is True
    assert action.get("type") == "choice"
    assert set(action.get("options", [])) == set(EXPECTED_ACTIONS), (
        "Workflow `action` choice options drift from EXPECTED_ACTIONS — "
        "update both the workflow and docs/claude/operator-actions.md."
    )


def test_no_freeform_command_input(workflow_dict: dict) -> None:
    """Reject any input named like a generic shell command surface.

    The whole point of operator-actions is the allowlist; an input
    like `command` / `script` / `cmd` would defeat it.
    """
    inputs = workflow_dict["on"]["workflow_dispatch"]["inputs"]
    forbidden = {"command", "cmd", "script", "shell", "exec", "run"}
    bad = forbidden & set(inputs.keys())
    assert not bad, f"Forbidden freeform-command inputs present: {bad}"


def test_no_freeform_command_input_regex_fallback() -> None:
    """Same check, but works even when PyYAML isn't installed.

    Catches the most likely reintroduction: a top-level
    `inputs.command:` block.
    """
    text = WORKFLOW.read_text()
    assert not re.search(r"^\s+command:\s*$", text, re.MULTILINE), (
        "Found a `command:` input — operator-actions allows no freeform shell."
    )


def test_workflow_maps_each_action_to_a_wrapper_script() -> None:
    """The case-arm in `Execute action wrapper` step must list every action."""
    text = WORKFLOW.read_text()
    for action, script in EXPECTED_ACTIONS.items():
        # Looking for: "<action>) ... SCRIPT=\"<script>\""
        pattern = rf'{re.escape(action)}\)\s*SCRIPT="{re.escape(script)}"'
        assert re.search(pattern, text), (
            f"Workflow does not map action '{action}' to wrapper '{script}'. "
            f"Both must be updated together."
        )


def test_workflow_validates_action_choice_explicitly() -> None:
    """The validate step must have a default `*)` arm rejecting unknown actions."""
    text = WORKFLOW.read_text()
    # The validation case statement should reject unknowns with exit 2.
    assert re.search(r"\*\)\s*\n\s*echo \"::error::Unknown action", text), (
        "Validate step must reject unknown actions explicitly with `*) … exit 2`."
    )


def test_workflow_requires_reason_for_tier2_actions() -> None:
    text = WORKFLOW.read_text()
    # The validate step should branch tier-2 actions and require REASON.
    for action in TIER_2_ACTIONS:
        assert action in text, f"Tier-2 action '{action}' missing from workflow"
    assert "Tier-2 action" in text and "non-empty 'reason'" in text, (
        "Workflow must enforce non-empty reason input for Tier-2 actions."
    )


def test_no_appleboy_or_other_third_party_ssh_action() -> None:
    """We deliberately reuse the diag-relay SSH pattern. Reviewers
    shouldn't have to evaluate a new dependency on a marketplace
    action; if someone adds one in a refactor, the test should
    flag it for explicit discussion.
    """
    text = WORKFLOW.read_text()
    assert "appleboy/ssh-action" not in text
    # Same idea — block other common SSH marketplace actions.
    for forbidden in ("garygrossgarten/github-action-ssh", "shimataro/ssh-key-action"):
        assert forbidden not in text


@pytest.mark.parametrize("action,script", list(EXPECTED_ACTIONS.items()))
def test_each_wrapper_exists_and_is_executable(action: str, script: str) -> None:
    path = OPS_DIR / script
    assert path.exists(), f"Missing wrapper for action '{action}': {path}"
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, f"{path} is not executable"


@pytest.mark.parametrize("script", list(EXPECTED_ACTIONS.values()) + ["_lib.sh"])
def test_wrapper_uses_strict_mode_and_sources_lib(script: str) -> None:
    text = (OPS_DIR / script).read_text()
    assert "set -euo pipefail" in text, f"{script} must use `set -euo pipefail`."
    if script != "_lib.sh":
        assert "_lib.sh" in text, f"{script} must source the shared _lib.sh."


@pytest.mark.parametrize(
    "script", list(EXPECTED_ACTIONS.values()) + ["_lib.sh", "notify_run.sh"]
)
def test_wrapper_parses_with_bash_n(script: str) -> None:
    """`bash -n` is a syntax check; it does not execute the script."""
    if shutil.which("bash") is None:
        pytest.skip("bash not available in this test env")
    result = subprocess.run(
        ["bash", "-n", str(OPS_DIR / script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{script} failed `bash -n` syntax check:\n{result.stderr}"
    )


@pytest.mark.parametrize("action", list(EXPECTED_ACTIONS))
def test_doc_lists_every_action(action: str) -> None:
    text = DOC.read_text()
    assert action in text, (
        f"docs/claude/operator-actions.md must mention every action in the "
        f"allowlist; '{action}' is missing."
    )


def test_doc_calls_out_docker_omission() -> None:
    """If a future PR re-adds Docker, this test should fail loudly so
    the doc is updated alongside the workflow."""
    text = DOC.read_text()
    assert "Docker is intentionally absent" in text or "Docker is not canonical" in text


def test_doc_includes_dispatcher_trust_contract() -> None:
    """§ 3.5 must enumerate every dispatcher class and what tier they
    can dispatch autonomously. Drift here means the next session
    re-derives the trust contract from chat — not acceptable."""
    text = DOC.read_text()
    assert "Dispatcher trust contract" in text, (
        "operator-actions.md must keep § 3.5 'Dispatcher trust contract'."
    )
    for dispatcher in ("Operator", "Perplexity", "PM-side Claude"):
        assert dispatcher in text, (
            f"Dispatcher '{dispatcher}' must appear in the trust-contract table."
        )


def test_doc_includes_transparency_rule() -> None:
    """§ 5.5 codifies the operator's 2026-05-08 directive: every run
    notifies the operator, regardless of dispatcher or tier.
    'Autonomy is complemented by full transparency.'"""
    text = DOC.read_text()
    assert "Transparency rule" in text, (
        "operator-actions.md must keep § 5.5 'Transparency rule (always-notify)'."
    )
    # Collapse whitespace so the principle still matches across line wraps.
    collapsed = re.sub(r"\s+", " ", text.lower())
    assert "autonomy is complemented by full transparency" in collapsed, (
        "The transparency principle must be quoted verbatim."
    )


def test_notify_run_script_exists_and_is_executable() -> None:
    path = OPS_DIR / "notify_run.sh"
    assert path.exists(), f"Missing notify wrapper: {path}"
    assert path.stat().st_mode & stat.S_IXUSR, f"{path} is not executable"


def test_notify_run_uses_send_ping_with_claude_target() -> None:
    """The transparency rule routes every ping through the Claude
    bot channel, not the trader bot. send_ping.py --target claude
    is the canonical producer."""
    text = (OPS_DIR / "notify_run.sh").read_text()
    assert "send_ping.py" in text or "send_ping" in text, (
        "notify_run.sh must call the canonical scripts/send_ping.py producer."
    )
    assert "--target" in text and "claude" in text, (
        "notify_run.sh must route to the Claude bot channel "
        "(--target claude), not the trader bot."
    )


def test_notify_run_handles_every_allowlisted_action() -> None:
    """Adding a new action without a notify-priority mapping would
    silently drop into the 'unknown action' arm, which alerts as
    urgent and confuses the operator. Force the mapping to be kept
    in sync with the allowlist."""
    text = (OPS_DIR / "notify_run.sh").read_text()
    for action in EXPECTED_ACTIONS:
        # Each action name must appear in a `case` arm in notify_run.sh.
        assert re.search(rf'\b{re.escape(action)}\b', text), (
            f"notify_run.sh must explicitly map action '{action}' to "
            f"a priority. Update the case statement when extending the "
            f"allowlist."
        )


def test_workflow_invokes_notify_step() -> None:
    """The transparency rule is doc + code now — the workflow must
    actually have the notify step, with `if: always()` so failures
    still notify."""
    text = WORKFLOW.read_text()
    assert "Notify operator via Claude bot channel" in text, (
        "operator-actions.yml must include the transparency-rule "
        "notify step (see docs/claude/operator-actions.md § 5.5)."
    )
    assert "notify_run.sh" in text, (
        "Notify step must invoke scripts/ops/notify_run.sh."
    )
    # `if: always()` is what makes failures notify too. `continue-on-error`
    # ensures a failed ping doesn't flip a successful action to failed.
    notify_block = text.split("Notify operator via Claude bot channel", 1)[1]
    notify_block = notify_block.split("- name:", 1)[0]
    assert "if: always()" in notify_block, (
        "Notify step must run with `if: always()` so failures notify too."
    )
    assert "continue-on-error: true" in notify_block, (
        "Notify step must `continue-on-error: true` so a notify failure "
        "doesn't flip an otherwise-successful action."
    )
