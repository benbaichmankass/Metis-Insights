"""The two diag-token workflows: the visibility gate, and the rotation verdict.

Both fixes are security remediations whose failure mode is *a green run that
asserted nothing*, so these tests EXERCISE the shipped shell rather than
grepping it. Every script under test is extracted from the workflow YAML at
test time — never a copy pasted in here, which is how a guard and the thing it
guards drift apart (`tests/test_merge_slot_guard.py` § the same lesson).

What they hold:

`get-diag-token.yml` (BL-20260818-GET-DIAG-TOKEN-EMITS-SECRET-TO-PUBLIC-SURFACE)
    writes a live DIAG_READ_TOKEN into an issue comment / run artifact. On a
    PUBLIC repo both are world-readable, which is how a token sat readable in
    issue #1615 from 2026-05-21 and still authorized three months later. The
    gate must be three-state and fail closed: `private` delivers, `public`
    refuses, and `unknown` -- WE COULD NOT LOOK -- refuses too. That third
    state is the whole point; collapsing it into the permissive branch
    recreates the defect one API hiccup at a time.

`set-diag-token.yml` (BL-20260818-SET-DIAG-TOKEN-REPORTS-NEW-ON-UNCHANGED-VALUE)
    printed "authorized with the new token" after testing only that the token
    AUTHORIZES -- true of a value that never changed. Run 32117038449 was green
    over an unchanged secret and a live exposure stayed open behind it. The
    verdict must distinguish `rotated` / `unchanged` / `unknown_before` /
    `failed`, and `unchanged` must never render as a rotation.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
GET_WF = REPO / ".github" / "workflows" / "get-diag-token.yml"
SET_WF = REPO / ".github" / "workflows" / "set-diag-token.yml"


def _steps(path: Path, job: str) -> list[dict]:
    doc = yaml.safe_load(path.read_text())
    return doc["jobs"][job]["steps"]


def _step(path: Path, job: str, step_id: str) -> dict:
    for st in _steps(path, job):
        if st.get("id") == step_id:
            return st
    raise AssertionError(f"no step id={step_id!r} in {path.name}:{job}")


# ───────────────────────── get-diag-token: the visibility gate ─────────────

GATE_CASES = [
    # (payload, live, expected_state, expected_rc)
    ("true", "true", "private", 0),
    ("false", "true", "public", 1),
    ("true", "false", "public", 1),
    ("false", "false", "public", 1),
    # A read that did not happen is NOT a private repo.
    ("", "true", "unknown", 1),
    ("true", "", "unknown", 1),
    ("", "", "unknown", 1),
    # Anything that is not the literal GitHub boolean rendering fails closed.
    ("True", "true", "unknown", 1),
    ("yes", "true", "unknown", 1),
]


@pytest.mark.parametrize("payload,live,expected_state,expected_rc", GATE_CASES)
def test_visibility_gate_verdicts(tmp_path, payload, live, expected_state, expected_rc):
    """Only private+private delivers; public and unknown both refuse, distinguishably."""
    script = _step(GET_WF, "get-token", "gate")["run"]
    out_file = tmp_path / "gh_output"
    out_file.touch()
    proc = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PAYLOAD_PRIVATE": payload,
            "LIVE_PRIVATE": live,
            "GITHUB_OUTPUT": str(out_file),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == expected_rc, proc.stdout + proc.stderr
    written = dict(
        line.split("=", 1)
        for line in out_file.read_text().splitlines()
        if "=" in line
    )
    assert written.get("visibility_state") == expected_state

    if expected_rc != 0:
        # The refusal must name its own cause. A message blaming the VM or the
        # secret for a visibility refusal is the unprovenanced-diagnostic class
        # (`docs/CLAUDE-RULES-CANONICAL.md`): a cause no code path tested.
        blob = proc.stdout + proc.stderr
        assert "::error::" in blob
        assert "REFUSING TO DELIVER" in blob
        if expected_state == "public":
            assert "PUBLIC" in blob
        else:
            assert "visibility" in blob.lower()


def test_gate_runs_before_anything_resolves_the_token():
    """The gate cannot be downstream of the step that puts the secret on disk."""
    ids = [st.get("id") or st.get("name") for st in _steps(GET_WF, "get-token")]
    assert ids.index("gate") < ids.index("resolve")


def test_both_delivery_paths_carry_the_gate_condition():
    """Defence in depth: each delivery step re-states the gate in its own `if:`.

    The artifact upload previously carried `always()`, which runs a step even
    after an earlier failure -- a delivery step that opts out of failure
    propagation must carry its own gate or the gate is one reordering away
    from being bypassed.
    """
    names = {"Upload env block artifact", "Reply to issue with the env block"}
    delivery = [st for st in _steps(GET_WF, "get-token") if st.get("name") in names]
    assert {st["name"] for st in delivery} == names, (
        "a delivery step was renamed — this test must be updated deliberately, "
        "not silently reduced to checking nothing"
    )
    for st in delivery:
        cond = str(st.get("if", ""))
        assert "steps.gate.outputs.visibility_state == 'private'" in cond, st.get("name")
        assert "always()" not in cond, st.get("name")


def test_stale_two_principals_rationale_no_longer_stands_as_the_rule():
    """The header asserted an audience instead of checking one. Field beats comment.

    The retired sentence is deliberately still quoted -- it is the record of what
    went wrong -- so this asserts it is quoted AS retired and that the operative
    rule is now a field read at run time, rather than asserting its absence.
    """
    text = GET_WF.read_text()
    if "exactly two principals" in text:
        assert "FIELD BEATS COMMENT" in text, (
            "the retired rationale is present without the correction that retires it"
        )
    assert "repository.private" in text
    assert "GATED ON REPOSITORY VISIBILITY" in text


# ───────────────────────── set-diag-token: the rotation verdict ────────────

_REMOTE_RE = re.compile(r"template=\"\$\(cat <<'REMOTE'\n(?P<body>.*?)\n\s*REMOTE\n", re.S)

# The remote script is a quoted heredoc indented into the workflow; strip the
# uniform indent so it runs standalone the way `bash -s` receives it.
_INDENT = " " * 10


def _remote_script() -> str:
    run = _step(SET_WF, "set-token", "exec")["run"]
    m = _REMOTE_RE.search(run)
    assert m, "could not locate the REMOTE heredoc -- did the exec step change shape?"
    return "\n".join(
        line[len(_INDENT):] if line.startswith(_INDENT) else line
        for line in m.group("body").split("\n")
    )


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip())
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _shims(tmp_path: Path, *, http_code: str) -> Path:
    """A PATH of stand-ins for the four commands that need a real VM.

    `sudo` execs its arguments (we are not root in CI, and the script's
    `install -o root -g root` cannot work there), so every `sudo`-prefixed read
    in the script is a REAL read against the fixtures below -- including
    /proc/<pid>/environ, which is a genuine kernel read of a process this test
    starts, not a mocked string.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write(
        bindir / "sudo",
        r"""
        #!/usr/bin/env bash
        # Run as the current user. When not root, drop the ownership flags the
        # script passes to `install` -- they are the only thing that genuinely
        # needs privilege, and dropping them changes no logic under test.
        args=()
        if [ "$(id -u)" != 0 ] && [ "$1" = install ]; then
          while [ $# -gt 0 ]; do
            case "$1" in
              -o|-g) shift 2 ;;
              *) args+=("$1"); shift ;;
            esac
          done
          set -- "${args[@]}"
        fi
        exec "$@"
        """,
    )
    _write(
        bindir / "systemctl",
        """
        #!/usr/bin/env bash
        # `restart` is a no-op; `is-active` reports what the test asked for.
        case "$1" in
          is-active) echo "${FAKE_UNIT_STATE:-active}" ;;
          *) : ;;
        esac
        """,
    )
    _write(
        bindir / "curl",
        f"""
        #!/usr/bin/env bash
        # The script asks for the HTTP code only and discards the body.
        printf '%s' '{http_code}'
        """,
    )
    _write(
        bindir / "pgrep",
        """
        #!/usr/bin/env bash
        # FAKE_WEBAPI_PID empty => "no web-api process running".
        [ -n "${FAKE_WEBAPI_PID:-}" ] && echo "$FAKE_WEBAPI_PID"
        exit 0
        """,
    )
    return bindir


def _run_remote(
    tmp_path: Path,
    *,
    new_token: str,
    envfile_token: str | None,
    served_token: str | None = None,
    http_code: str = "200",
    unit_state: str = "active",
):
    """Run the shipped remote script against fixtures; return (proc, envfile)."""
    script = _remote_script()
    script = script.replace("NEW_TOKEN='__TOKEN__'", f"NEW_TOKEN='{new_token}'")
    assert f"NEW_TOKEN='{new_token}'" in script, "token placeholder substitution failed"

    envfile = tmp_path / "web-api.env"
    if envfile_token is not None:
        envfile.write_text(f"OTHER_KEY=keepme\nDIAG_READ_TOKEN={envfile_token}\n")
    else:
        envfile.write_text("OTHER_KEY=keepme\n")

    original = "ENVFILE=/etc/ict-trader/web-api.env"
    assert script.count(original) == 1, "ENVFILE declaration moved or was duplicated"
    script = script.replace(original, f"ENVFILE={envfile}")

    bindir = _shims(tmp_path, http_code=http_code)
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_UNIT_STATE": unit_state,
        "FAKE_WEBAPI_PID": "",
    }

    holder = None
    if served_token is not None:
        # A REAL process carrying the token in its environment, so the script's
        # /proc/<pid>/environ read is a real read.
        holder = subprocess.Popen(
            ["sleep", "60"], env={"DIAG_READ_TOKEN": served_token, "PATH": os.environ["PATH"]}
        )
        env["FAKE_WEBAPI_PID"] = str(holder.pid)
    try:
        proc = subprocess.run(
            ["bash", "-s"], input=script, env=env, capture_output=True, text=True
        )
    finally:
        if holder is not None:
            holder.kill()
            holder.wait()
    return proc, envfile


def _state(proc) -> str:
    hits = re.findall(r"^ROTATION_STATE=(\S+)$", proc.stdout, re.M)
    assert len(hits) == 1, f"expected exactly one verdict sentinel, got {hits}\n{proc.stdout}"
    return hits[0]


OLD = "a" * 40
NEW = "b" * 40


def test_rotated_when_the_served_value_actually_changed(tmp_path):
    proc, envfile = _run_remote(
        tmp_path, new_token=NEW, envfile_token=OLD, served_token=OLD
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _state(proc) == "rotated"
    assert "RESULT: rotated" in proc.stdout
    assert "before_token_source: process" in proc.stdout
    # The install must land, and must not eat the other keys in the file.
    body = envfile.read_text()
    assert f"DIAG_READ_TOKEN={NEW}" in body
    assert "OTHER_KEY=keepme" in body
    assert OLD not in body


def test_unchanged_is_a_loud_no_op_not_a_rotation(tmp_path):
    """The exact 2026-08-18 condition: green, authorized, and nothing rotated."""
    proc, _ = _run_remote(
        tmp_path, new_token=OLD, envfile_token=OLD, served_token=OLD
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _state(proc) == "unchanged"
    assert "RESULT: unchanged" in proc.stdout
    assert "NOTHING WAS ROTATED" in proc.stdout
    # The sentence that made a no-op read as a rotation must not be reachable.
    assert "authorized with the new token" not in proc.stdout
    assert "RESULT: rotated" not in proc.stdout


def test_unknown_before_when_the_pre_state_could_not_be_read(tmp_path):
    """No process and no key in the file => we did not look. Never `rotated`."""
    proc, _ = _run_remote(tmp_path, new_token=NEW, envfile_token=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _state(proc) == "unknown_before"
    assert "before_token_source: unresolved" in proc.stdout
    assert "a rotation or a no-op" in proc.stdout
    assert "RESULT: rotated" not in proc.stdout


def test_envfile_is_the_fallback_source_when_no_process_is_running(tmp_path):
    proc, _ = _run_remote(tmp_path, new_token=NEW, envfile_token=OLD)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _state(proc) == "rotated"
    assert "before_token_source: envfile" in proc.stdout


def test_failed_when_the_token_does_not_authorize(tmp_path):
    proc, _ = _run_remote(
        tmp_path, new_token=NEW, envfile_token=OLD, served_token=OLD, http_code="401"
    )
    assert proc.returncode == 1
    assert _state(proc) == "failed"
    assert "RESULT: failed" in proc.stdout
    # A failure must not also claim a rotation on the way out.
    assert "RESULT: rotated" not in proc.stdout


def test_failed_when_the_unit_is_not_active(tmp_path):
    proc, _ = _run_remote(
        tmp_path,
        new_token=NEW,
        envfile_token=OLD,
        served_token=OLD,
        unit_state="failed",
    )
    assert proc.returncode == 1
    assert _state(proc) == "failed"


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(new_token=NEW, envfile_token=OLD, served_token=OLD),
        dict(new_token=OLD, envfile_token=OLD, served_token=OLD),
        dict(new_token=NEW, envfile_token=None),
        dict(new_token=NEW, envfile_token=OLD, served_token=OLD, http_code="401"),
    ],
)
def test_no_token_value_ever_reaches_the_transcript(tmp_path, kwargs):
    """The transcript is echoed to the run log AND into a public issue comment.

    Fingerprints are fine (a 12-hex prefix of a sha256 over a >=128-bit token
    is not invertible); the values are not.
    """
    proc, _ = _run_remote(tmp_path, **kwargs)
    blob = proc.stdout + proc.stderr
    for token in {kwargs["new_token"], kwargs.get("envfile_token"), kwargs.get("served_token")}:
        if token:
            assert token not in blob, "a raw token value reached the transcript"


def test_runner_lifts_the_verdict_and_defaults_it_to_unknown():
    """An absent sentinel must not default to `rotated`."""
    run = _step(SET_WF, "set-token", "exec")["run"]
    assert "rotation_state=${state:-unknown}" in run
    assert 'sed -n \'s/^ROTATION_STATE=//p\'' in run


def test_success_comment_reports_the_verdict_rather_than_the_run_being_green():
    reply = next(
        st for st in _steps(SET_WF, "set-token")
        if str(st.get("name", "")).endswith("with success")
    )
    body = reply["with"]["script"]
    assert "ROTATION_STATE" in str(reply.get("env", {}))
    for state in ("rotated", "unchanged", "unknown_before", "unknown"):
        assert f"{state}:" in body, f"success comment does not branch on {state}"
    assert "HEAD.unknown" in body, "an unrecognised verdict must fall back loudly"
