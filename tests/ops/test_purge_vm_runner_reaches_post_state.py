"""purge_vm_runner must REACH its post-state block for every check outcome.

`BL-20260813-VM-RUNNER-ZOMBIE-SUDOERS-ROOT-GRANT`.

WHY THIS EXISTS. The action failed twice on the live VM for two DIFFERENT
reasons, and both were in the verification rather than the work:

  #8993  post-state ran as `ubuntu` against root-only /etc/sudoers.d, so
         `[ -f ]` was false for every file there. It printed "[ok] absent" for
         a path it could not read, and the removal step — reading the same
         false negative — never attempted the removal at all.

  #8997  the fix for that introduced `priv_exists "$p"; state=$?`. Under
         `set -e` a non-zero return in plain command position ABORTS, so the
         first genuinely-absent answer (return 1) killed the script before it
         printed any post-state. The helper's entire purpose is that non-zero
         is DATA; `set -e` read it as an error.

Both failures share a shape: **the script did not produce a trustworthy
post-state, while looking like it had run.** So the property under test is not
"does the purge work" (that needs a real VM) but "does every outcome path
arrive at the post-state block and report honestly".

`sudo` is stubbed so each of the three `priv_exists` states can be forced. That
is the only way to exercise this without root — and forcing them is exactly
what neither live run did.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ops" / "purge_vm_runner.sh"


def _run(tmp_path: Path, sudo_body: str) -> subprocess.CompletedProcess:
    """Run the script with a stubbed `sudo` on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text("#!/usr/bin/env bash\n" + sudo_body)
    sudo.chmod(0o755)

    # A repo dir carrying the replacement sudoers, so step 1 proceeds.
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy" / "ict-ufw.sudoers").write_text(
        "ubuntu ALL=(root) NOPASSWD: /usr/sbin/ufw\n")

    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", REPO_DIR=str(repo))
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)


# `sudo -n -l /usr/sbin/ufw` must succeed or the script aborts early by design.
# `sudo -n test -e <path>` is what priv_exists calls; its exit code is the state.
_STUB = """
args="$*"
case "$args" in
  *"-l /usr/sbin/ufw"*) exit 0 ;;                 # ufw grant resolves
  *"visudo"*)           exit 0 ;;
  *"install"*)          exit 0 ;;
  *"-n true"*)          exit {sudo_alive} ;;      # can sudo run at all?
  # ict-ufw was just installed by step 1, so it IS present. Answering "absent"
  # for it too would model a world the script never operates in and would fail
  # the post-state for the wrong reason.
  *"test -e /etc/sudoers.d/ict-ufw"*) exit 0 ;;
  *"test -e"*)          exit {exists} ;;          # 0 present / 1 absent
  *"rm -f"*)            exit 0 ;;
  *)                    exit 0 ;;
esac
"""


def test_all_absent_reaches_post_state_and_passes(tmp_path):
    """THE #8997 REGRESSION: every artifact genuinely absent (return 1).

    Before the fix this aborted at the first check with no post-state at all.
    """
    r = _run(tmp_path, _STUB.format(exists=1, sudo_alive=0))
    assert "POST-STATE" in r.stdout + r.stderr, "never reached the post-state block"
    out = r.stdout + r.stderr
    assert "absent (verified as root)" in out
    assert r.returncode == 0, out


def test_old_grant_still_present_is_removed_then_verified(tmp_path):
    """The case the first live run could not see: the grant IS there."""
    # Present on the first probe; the post-state re-probes and we keep saying
    # present, so the script must FAIL rather than claim success.
    r = _run(tmp_path, _STUB.format(exists=0, sudo_alive=0))
    out = r.stdout + r.stderr
    assert "POST-STATE" in out
    assert "still present" in out, "a still-present artifact must be reported"
    assert r.returncode != 0, "must not exit 0 while an artifact remains"


def test_undetermined_fails_and_is_never_reported_as_absence(tmp_path):
    """sudo itself cannot run -> state 2. This is the whole point of the fix:
    'I could not look' must not render as 'there is nothing there'."""
    r = _run(tmp_path, _STUB.format(exists=1, sudo_alive=1))
    out = r.stdout + r.stderr
    assert r.returncode != 0, "UNDETERMINED must fail, not pass"
    assert "absent (verified as root)" not in out, (
        "an undeterminable path was reported as a verified absence")


def test_script_is_strict_mode_and_still_reaches_post_state(tmp_path):
    """set -euo pipefail is required by the conformance guard; this pins that
    it coexists with a helper whose non-zero return is meaningful data."""
    assert "set -euo pipefail" in SCRIPT.read_text()
    r = _run(tmp_path, _STUB.format(exists=1, sudo_alive=0))
    assert "purge-vm-runner: OK" in r.stdout + r.stderr


@pytest.mark.parametrize("form", ["priv_exists \"${OLD_SUDOERS}\"; _old_state=$?",
                                  "priv_exists \"${p}\"; _st=$?",
                                  "priv_exists \"${NEW_SUDOERS}\"; _new_st=$?"])
def test_the_aborting_call_form_is_not_reintroduced(form):
    """`f "$x"; v=$?` is fatal under set -e. Pin the exact strings that broke
    #8997 so a future edit cannot quietly restore them."""
    assert form not in SCRIPT.read_text(), (
        f"reintroduced the set -e aborting form: {form!r} — "
        "use `v=0; priv_exists \"$x\" || v=$?`")
