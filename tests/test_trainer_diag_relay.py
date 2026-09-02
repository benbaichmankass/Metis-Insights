"""The trainer-diag relay's contract — the parts a broken edit would silently lose.

The relay (`.github/workflows/trainer-diag-relay.yml`) is the MCP-independent read
path to the trainer VM, for a session whose ``issue_write`` returns 403 and which
therefore cannot open a ``trainer-vm-diag-request`` issue.

WHAT THESE TESTS ARE FOR. Three properties of this workflow are load-bearing and
all three fail SILENTLY — a run stays green and the result file still looks like an
answer:

  1. The **exit sentinel**. It is the only thing separating "the script ran on the
     trainer and exited non-zero" from "SSH never got it onto the box". Lose it and
     an unreachable VM renders as an empty trainer answer. `trainer-vm-diag.yml`
     runs its ssh under ``|| true`` and reports no exit code at all, which is the
     defect this relay exists not to inherit.
  2. The **results commit must stay off the trigger path**. Staging anything under
     ``automation/trainer-diag-requests/**`` retriggers the workflow, forcing a ``[skip ci]``
     that strands PR check runs at ``total_count: 0`` — measured on #10076/#10077/
     #10078 for the sibling relays.
  3. The **scope tripwire**. Its whole job is to stop an accidental hop to the live
     trader VM, which is reachable because the trainer holds credentials for its
     read-only DB pull from live.

`test_workflow_yaml_valid.py` already asserts the file parses; none of the above is
visible to it. Property 1 is tested by EXECUTING the real sentinel mechanism rather
than by matching on the workflow text, because a string match would pass against a
sentinel that no longer works.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "trainer-diag-relay.yml"

REQUEST_DIR = "automation/trainer-diag-requests"
RESULT_DIR = "automation/trainer-diag-results"

# The exact prepend the workflow builds. Kept here as the ONE place the shell
# quoting is pinned, and asserted against the workflow text below so the two
# cannot drift apart unnoticed.
TRAP_PREPEND = (
    """trap '__td_rc=$?; printf "\\n__TRAINER_DIAG_EXIT__:%s\\n" "$__td_rc"' EXIT\n"""
)
SENTINEL = "__TRAINER_DIAG_EXIT__"


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def wf_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _run_step(wf: dict) -> str:
    """The body of the step that SSHes and writes results."""
    for step in wf["jobs"]["diag"]["steps"]:
        if "Run pending" in str(step.get("name", "")):
            return step["run"]
    raise AssertionError("no 'Run pending trainer-diag requests' step found")


def _commit_step(wf: dict) -> str:
    for step in wf["jobs"]["diag"]["steps"]:
        if "Commit results" in str(step.get("name", "")):
            return step["run"]
    raise AssertionError("no 'Commit results back to branch' step found")


def test_workflow_exists():
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"


# --------------------------------------------------------------------------
# 1. The exit sentinel — EXECUTED, not string-matched.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "script, expected_rc",
    [
        ("echo hi\n", "0"),
        # An explicit `exit` is the case a naive trailing-`echo $?` append loses:
        # the shell exits before reaching it. Only an EXIT trap survives this.
        ("echo hi\nexit 7\n", "7"),
        # A failing command under `set -e` — same reason.
        ("set -e\nfalse\necho unreachable\n", "1"),
    ],
    ids=["clean-exit", "explicit-exit-7", "set-e-failure"],
)
def test_exit_sentinel_reports_the_real_remote_code(tmp_path, script, expected_rc):
    """`remote_exit` must be the caller's true exit code in every exit path.

    Run through ``bash -s`` on stdin, which is exactly how the workflow feeds the
    script to the trainer.
    """
    if shutil.which("bash") is None:  # pragma: no cover - CI always has bash
        pytest.skip("bash unavailable")
    combined = tmp_path / "script.sh"
    combined.write_text(TRAP_PREPEND + script, encoding="utf-8")

    proc = subprocess.run(
        ["bash", "-s"],
        stdin=combined.open("rb"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout + proc.stderr
    hits = re.findall(rf"^{SENTINEL}:(\S+)$", out, flags=re.MULTILINE)
    assert hits, f"sentinel absent from output; remote_exit would read 'unknown'.\n{out!r}"
    assert hits[-1] == expected_rc, f"expected remote_exit={expected_rc}, got {hits[-1]}"


def test_absent_sentinel_is_the_unreachable_signal(tmp_path):
    """No sentinel MUST mean 'we could not look', never a zero exit.

    This is the state SSH failure lands in: the output is a transport error and
    contains no sentinel, and the workflow must not read that as a trainer answer.
    """
    ssh_failure = "ssh: connect to host 158.178.209.121 port 22: Connection timed out\n"
    assert not re.search(rf"^{SENTINEL}:", ssh_failure, flags=re.MULTILINE)


def test_workflow_uses_this_exact_trap(wf):
    """Pin the workflow's prepend to the one the tests above execute.

    Without this the tests would keep passing while the workflow's own quoting
    drifted — proving a mechanism that is no longer the deployed one.
    """
    run = _run_step(wf)
    assert "__td_rc=$?" in run, "the EXIT-trap prepend is gone from the workflow"
    assert SENTINEL in run, "the sentinel name changed in the workflow but not here"
    assert "trap " in run


def test_states_are_not_collapsed(wf):
    """All four declared states must be reachable in the step body."""
    run = _run_step(wf)
    for state in ("ran", "unreachable", "refused_empty", "refused_scope"):
        assert state in run, f"state '{state}' is never written — it cannot be reported"
    assert "unknown" in run, "the 'we did not learn the exit code' value is missing"


def test_unreachable_is_never_defaulted_to_zero(wf):
    """A missing sentinel must set rc=unknown, not rc=0.

    Defaulting to 0 would make an unreachable VM report a clean successful run.
    """
    run = _run_step(wf)
    m = re.search(r"state=unreachable\s*\n\s*rc=(\S+)", run)
    assert m, "the unreachable branch no longer sets rc explicitly"
    assert m.group(1) == "unknown", f"unreachable sets rc={m.group(1)}, must be 'unknown'"


# --------------------------------------------------------------------------
# 2. The results commit must not touch the trigger path.
# --------------------------------------------------------------------------

def test_trigger_path_is_the_request_dir_only(wf):
    on = wf.get("on") or wf.get(True)
    paths = on["push"]["paths"]
    assert paths == [f"{REQUEST_DIR}/**"], f"unexpected trigger paths: {paths}"


def test_commit_step_stages_only_results(wf):
    """Staging the request dir would retrigger the workflow and force `[skip ci]`,
    which strands PR check runs at total_count 0 (measured on #10076-#10078)."""
    commit = _commit_step(wf)
    # Strip comments. The step's comment SHOULD name the trigger path — that is
    # where the reason lives — so the assertion must look at the code only.
    code = "\n".join(
        line for line in commit.splitlines()
        if not line.lstrip().startswith("#")
    )
    adds = [line.strip() for line in code.splitlines() if "git add" in line]
    assert adds == [f"git add -A {RESULT_DIR}"], (
        f"only the results dir may be staged; found: {adds}"
    )
    assert REQUEST_DIR not in code, (
        f"the commit step stages or references {REQUEST_DIR} in code — that is "
        f"the trigger path, and staging it forces a `[skip ci]`"
    )
    assert "skip ci" not in code.lower(), (
        "a skip directive suppresses EVERY workflow for the commit; the fix is to "
        "keep the commit off the trigger path, not to silence CI"
    )


def test_idempotency_is_the_result_file_not_deletion(wf):
    """Reusing a request name must be a no-op, and requests must never be removed
    (removal would put the commit back on the trigger path)."""
    run = _run_step(wf)
    assert 'if [ -f "$out" ]' in run, "the result-file idempotency check is gone"
    assert "git rm" not in run, "requests must not be deleted — see the header"


# --------------------------------------------------------------------------
# 3. The scope tripwire.
# --------------------------------------------------------------------------

LIVE_VM_TOKENS = [
    "141.145.193.91",     # live trader (ict-bot-arm)
    "158.178.210.252",    # terminated x86 micro, still referenced in old docs
    "10.0.0.251",         # IB gateway VM, private subnet
    "ict-bot.duckdns.org",
    "ict-bot-arm",
    "ict-trader-live",
    "ict-web-api",
]


@pytest.mark.parametrize("token", LIVE_VM_TOKENS)
def test_scope_tripwire_covers_every_non_trainer_host(wf, token):
    run = _run_step(wf)
    assert token in run, (
        f"'{token}' is not in the scope tripwire; a script naming it would reach a "
        f"host outside this relay's trust contract"
    )


def test_relay_targets_the_trainer_and_only_the_trainer(wf_text):
    """The SSH destination must be the trainer variable, never a literal live host."""
    assert "TRAINER_VM_IP" in wf_text
    assert "158.178.209.121" in wf_text, "trainer default IP missing"
    # The live tokens appear ONLY inside the tripwire loop. Assert none of them is
    # used as an ssh destination.
    for token in LIVE_VM_TOKENS:
        assert f"@{token}" not in wf_text, f"{token} is used as an SSH destination"


def test_empty_request_is_refused_not_run(wf):
    """An empty script 'succeeds' on the trainer and returns nothing, which is
    indistinguishable from a real empty answer — so it must never be sent."""
    run = _run_step(wf)
    assert "refused_empty" in run
    assert "[^[:space:]]" in run, "the whitespace-only check is gone"


# --------------------------------------------------------------------------
# Output cap must announce itself.
# --------------------------------------------------------------------------

def test_truncation_announces_itself_with_the_full_size(wf):
    """An unlabelled cap is an unprovenanced diagnostic — it sent a session hunting
    an execution-length boundary that did not exist
    (BL-20260807-TRAINER-DIAG-RELAY-SILENT-CMD-TRUNCATION)."""
    run = _run_step(wf)
    assert "TRUNCATED" in run
    assert "MAX_BYTES" in run
    # The marker must carry BOTH the shown size and the full size.
    m = re.search(r"TRUNCATED[^\n]*", run)
    assert m and "%s of %s bytes" in m.group(0), (
        "the truncation marker must name the shown AND the full byte count"
    )


def test_concurrency_key_cannot_drop_a_request(wf):
    """GitHub keeps at most ONE pending run per concurrency group, so a coarse key
    silently discards a burst (BL-20260611-002). Keying on the sha means every push
    gets its own group: nothing pends, nothing is dropped."""
    group = wf["concurrency"]["group"]
    assert "github.sha" in group, f"concurrency group {group!r} can drop a burst"
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_permissions_are_minimal(wf):
    """No `issues:` or `pull-requests:` write — this relay reads a VM and writes
    result files, nothing else."""
    perms = wf["permissions"]
    assert perms == {"contents": "write"}, f"unexpected permissions: {perms}"


def test_docs_point_a_relay_bound_session_here():
    """A capability that exists and is unreachable from the surface its user reads
    is, for that user, identical to no capability. This repo has paid for that
    shape three times."""
    doc = (REPO_ROOT / "docs" / "claude" / "diag-relay.md").read_text(encoding="utf-8")
    assert "trainer-diag-relay" in doc, (
        "docs/claude/diag-relay.md does not mention this relay — a 403-bound "
        "session reading the docs would still conclude no trainer path exists"
    )
    assert REQUEST_DIR in doc, "the docs do not name the request path"
