"""BL-20260821-DEPLOY-WRAPPER-ASSERTS-A-BOUNCE-IT-DID-NOT-DO.

`scripts/ops/pull_and_deploy.sh` closed by INFERRING the outcome from
``PRE_HEAD`` vs ``POST_HEAD`` and asserting ``"${UNIT} bounced"`` whenever they
matched. That inference cannot be correct, because ``deploy_pull_restart.sh``
does not drive its restart decision off the wrapper's ``PRE_HEAD`` at all — it
drives it off ``runtime_logs/deployed_sha.txt`` (its ``RUNTIME_BASE``) and off
the CONTENT of the diff. So both branches could lie:

* ``PRE_HEAD == POST_HEAD`` usually means NOTHING restarted (the deploy
  script's "already deployed; nothing to deploy" early exit) — yet the wrapper
  printed "bounced".
* ``PRE_HEAD != POST_HEAD`` can ALSO mean nothing restarted, when every changed
  path is docs/tests/.claude/.github (BL-20260529-002).

Four real outcomes collapsed into two sentences derived from a signal that
determines none of them — and every "deployed and verified" claim in the
2026-08 programme quoted that sentence.

The fix MEASURES the asserted fact: a bounced unit has a new
``ActiveEnterTimestampMonotonic`` and a new ``MainPID``. Three states, never
collapsed — ``bounced`` / ``not_bounced`` / ``unknown`` — because an unreadable
``systemctl`` must not read as "did not restart".

⚠️ These tests were SHOWN TO FIRE against the pre-fix script: with the old
closing line, ``test_no_op_deploy_does_not_claim_a_bounce`` fails on the literal
string "bounced", which is the whole point — the assertion has been observed
failing, so a pass means something.

Harness follows tests/test_deploy_pull_restart_runtime_gate.py: stub `git`,
`systemctl` and the inner deploy script on PATH. No network, no VM.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WRAPPER_SRC = _REPO_ROOT / "scripts" / "ops" / "pull_and_deploy.sh"
_LIB_SRC = _REPO_ROOT / "scripts" / "ops" / "_lib.sh"


def _make_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake(tmp_path):
    """A repo + PATH shims where the unit's restart is controllable.

    `fp_file` holds the fingerprint `systemctl show` reports. A test that wants
    a REAL bounce rewrites it while the inner deploy script "runs", which is
    exactly how a genuine restart would look to the wrapper.
    """
    repo = tmp_path / "repo"
    (repo / "scripts" / "ops").mkdir(parents=True)
    (repo / "runtime_logs").mkdir()

    (repo / "scripts" / "ops" / "pull_and_deploy.sh").write_text(_WRAPPER_SRC.read_text())
    (repo / "scripts" / "ops" / "_lib.sh").write_text(_LIB_SRC.read_text())

    bindir = tmp_path / "bin"
    bindir.mkdir()

    # The fingerprint systemctl reports. Tests mutate it (or don't) to model a
    # unit that did (or did not) restart across the deploy.
    fp_file = tmp_path / "fp"
    fp_file.write_text("111 222")
    # What the inner deploy script does when invoked: by default nothing, which
    # is the no-op / non-runtime path.
    deploy_action = tmp_path / "deploy_action.sh"
    deploy_action.write_text("")
    _make_stub(
        repo / "scripts" / "deploy_pull_restart.sh",
        f'#!/bin/bash\nbash "{deploy_action}"\nexit 0\n',
    )

    head_file = tmp_path / "head"
    head_file.write_text("samesha")
    _make_stub(
        bindir / "git",
        f'#!/bin/bash\ncase "$1" in\n  rev-parse) cat "{head_file}" ;;\n  *) exit 0 ;;\nesac\n',
    )
    _make_stub(
        bindir / "systemctl",
        f"""#!/bin/bash
case "$1" in
  --version) echo "systemd 250"; exit 0 ;;
  list-units) exit 0 ;;
  is-active) echo "active"; exit 0 ;;
  show)
    case "$2" in
      -p) case "$3" in
            ActiveEnterTimestampMonotonic) echo "ActiveEnterTimestampMonotonic=$(cut -d' ' -f1 "{fp_file}")" ;;
            MainPID) echo "MainPID=$(cut -d' ' -f2 "{fp_file}")" ;;
            *) exit 0 ;;
          esac ;;
      *) exit 0 ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
""",
    )
    _make_stub(bindir / "journalctl", "#!/bin/bash\nexit 0\n")
    _make_stub(bindir / "sudo", '#!/bin/bash\nwhile [[ "$1" == -* ]]; do shift; done\nexec "$@"\n')

    return {"repo": repo, "bindir": bindir, "fp": fp_file,
            "head": head_file, "deploy_action": deploy_action}


def _run(fake):
    env = {**os.environ,
           "PATH": f"{fake['bindir']}:/usr/bin:/bin",
           "REPO_DIR": str(fake["repo"])}
    return subprocess.run(
        ["bash", str(fake["repo"] / "scripts" / "ops" / "pull_and_deploy.sh")],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _out(proc) -> str:
    return proc.stdout + proc.stderr


def test_no_op_deploy_does_not_claim_a_bounce(fake):
    """THE REGRESSION. HEAD unchanged + unit never restarted => must not say "bounced".

    This is the case the old code got wrong on every docs-only merge, which is
    most of this programme's merges.
    """
    out = _out(_run(fake))
    # NOTE the assertion is on "<unit> bounced", not on the bare word: the
    # machine-readable verdict is `not_bounced`, which CONTAINS "bounced". A
    # naive substring check flags the correct output as the defect — it did,
    # on the first run of this test, which is why the narrower form is here.
    assert "service bounced" not in out.lower(), (
        "the wrapper still claims a bounce on a deploy where the unit's start "
        f"fingerprint never changed:\n{out}"
    )
    assert "NOT restarted" in out, f"expected an explicit not-restarted verdict:\n{out}"


def test_real_restart_is_reported_as_restarted(fake):
    """POSITIVE CONTROL. A genuine bounce must still be reported as one.

    Without this, a wrapper that simply never says "restarted" would pass the
    test above — the assertion needs a case that only a working measurement can
    satisfy.
    """
    fake["deploy_action"].write_text(f'echo "999 888" > "{fake["fp"]}"\n')
    out = _out(_run(fake))
    assert "restarted" in out, f"a real restart was not reported:\n{out}"
    assert "NOT restarted" not in out, f"a real restart was reported as not-restarted:\n{out}"


def test_unreadable_systemctl_reports_unknown_not_not_restarted(fake):
    """"We could not look" must not collapse into "it did not restart"."""
    _make_stub(
        fake["bindir"] / "systemctl",
        '#!/bin/bash\ncase "$1" in\n  --version) echo "systemd 250"; exit 0 ;;\n'
        '  is-active) echo "active"; exit 0 ;;\n  show) exit 1 ;;\n  *) exit 0 ;;\nesac\n',
    )
    out = _out(_run(fake))
    assert "UNKNOWN" in out, f"an unreadable start stamp must report UNKNOWN:\n{out}"
    assert "NOT restarted" not in out, (
        f"'could not look' was collapsed into 'did not restart':\n{out}"
    )


def test_bounce_state_is_recorded_for_the_audit_surface(fake):
    """The verdict reaches the audit JSON, not just the human log.

    The diag relay surfaces runtime_logs/operator_actions/, so a session reading
    the audit trail rather than the run log must see the same three states.
    """
    out = _out(_run(fake))
    assert "not_bounced" in out or "bounce_state" in out, (
        f"the machine-readable verdict is absent:\n{out}"
    )
