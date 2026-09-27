"""init-diag-token must ROTATE, and must never expose the value it rotates to.

WHY THIS EXISTS (2026-09-27, SEC-DT). Before this change
``scripts/ops/init_diag_token.sh``:

  * REUSED any existing token (web-api.env → .env → legacy file) and only
    generated one when none existed — so on the live VM a "rotation" would
    have re-installed the leaked value and rotated nothing;
  * PRINTED ``DIAG_READ_TOKEN=<value>`` to stdout, which system-actions.yml
    posts verbatim to the request issue — on a PUBLIC repo;
  * left the Actions secret untouched, so every diag relay would break.

The route is now split: ``rotate_diag_token_runner.sh`` (GitHub runner)
originates the value and ships it on STDIN; ``init_diag_token.sh`` (VM) writes
it to every file the web API reads. These tests drive BOTH halves end to end
against a simulated VM: ``ssh`` runs the remote command locally, ``systemctl
restart`` recomputes the served token the way systemd does (``.env`` loaded
AFTER ``/etc/ict-trader/web-api.env`` wins), ``curl`` answers 200/401 against
the served token, and ``gh`` keeps the Actions secret in a file.

The load-bearing assertion in every test: neither token appears in stdout,
stderr, or any process argv the stubs saw.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VM_SCRIPT = REPO / "scripts" / "ops" / "init_diag_token.sh"
RUNNER_SCRIPT = REPO / "scripts" / "ops" / "rotate_diag_token_runner.sh"

STUBS = {
    # `sudo -n <cmd...>` → run <cmd...> (the temp files are ours anyway).
    "sudo": r"""
[ "${1:-}" = "-n" ] && shift
exec "$@"
""",
    # restart = what systemd does with the two EnvironmentFile= lines: the
    # later file (.env) overrides the earlier (web-api.env).
    "systemctl": r"""
echo "systemctl $*" >> "$SIM/argv.log"
case "$1" in
  --version) exit 0 ;;
  is-active) echo "${SIM_UNIT_STATE:-active}"; [ "${SIM_UNIT_STATE:-active}" = active ] ;;
  restart)
    tok="$(grep -m1 '^DIAG_READ_TOKEN=' "$SIM/web-api.env" 2>/dev/null | cut -d= -f2-)"
    envtok="$(grep -m1 '^DIAG_READ_TOKEN=' "$SIM/dotenv" 2>/dev/null | cut -d= -f2-)"
    [ -n "$envtok" ] && tok="$envtok"
    printf '%s' "$tok" > "$SIM/served"
    ;;
esac
exit 0
""",
    # The diag endpoint: bearer arrives on stdin via `-H @-`.
    "curl": r"""
echo "curl $*" >> "$SIM/argv.log"
hdr="$(cat)"
presented="${hdr#Authorization: Bearer }"
served="$(cat "$SIM/served")"
if [ -n "${SIM_FORCE_HTTP:-}" ] && [ "$presented" != "$SIM_OLD" ]; then
  printf '%s' "$SIM_FORCE_HTTP"; exit 0
fi
if [ "$presented" = "$served" ]; then printf 200; else printf 401; fi
""",
    # ssh: drop options + host, run the remote command locally, stdin intact.
    "ssh": r"""
echo "ssh $*" >> "$SIM/argv.log"
while [ $# -gt 1 ]; do
  case "$1" in -i|-o) shift 2 ;; *) shift ;; esac
done
exec bash -c "$1"
""",
    "gh": r"""
echo "gh $*" >> "$SIM/argv.log"
case "$*" in
  *"secrets/public-key"*) [ -z "${SIM_GH_DENY:-}" ] ;;
  "api "*"secrets/DIAG_READ_TOKEN"*) cat "$SIM/secret_updated_at" ;;
  "secret set DIAG_READ_TOKEN"*)
    [ -n "${SIM_GH_SET_FAIL:-}" ] && exit 1
    cat > "$SIM/secret"; echo "2026-09-27T12:00:01Z" > "$SIM/secret_updated_at" ;;
  *) exit 1 ;;
esac
""",
}


def _tok() -> str:
    return secrets.token_hex(32)


@pytest.fixture()
def sim(tmp_path: Path):
    sim = tmp_path / "sim"
    sim.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        p = bin_dir / name
        p.write_text("#!/usr/bin/env bash\n" + body)
        p.chmod(0o755)
    old = _tok()
    (sim / "dotenv").write_text(f"OTHER_KEY=keep-me\nDIAG_READ_TOKEN={old}\n")
    (sim / "web-api.env").write_text(f"DIAG_READ_TOKEN={old}\nJWT_SIGNING_KEY=x\n")
    (sim / "legacy_token").write_text(old + "\n")
    (sim / "served").write_text(old)
    (sim / "secret").write_text(old)
    (sim / "secret_updated_at").write_text("2026-01-01T00:00:00Z\n")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        SIM=str(sim),
        SIM_OLD=old,
        REPO_DIR=str(repo_dir),
        DIAG_ENV_FILE=str(sim / "dotenv"),
        DIAG_SYSTEM_ENV=str(sim / "web-api.env"),
        DIAG_LEGACY_TOKEN_FILE=str(sim / "legacy_token"),
        DIAG_RESTART_TIMEOUT_S="2",
        REMOTE_SCRIPT=str(VM_SCRIPT),
        GITHUB_REPOSITORY="owner/repo",
        VM_SSH_USER="ubuntu",
        VM_SSH_HOST="vm.invalid",
        OLD_DIAG_READ_TOKEN=old,
        GH_TOKEN="pat-placeholder",
        VERIFY_ATTEMPTS="2",
        VERIFY_SLEEP_S="0",
    )
    (sim / "argv.log").write_text("")
    return {"dir": sim, "env": env, "old": old, "tmp": tmp_path}


def _new_token_file(sim, value: str | None = None) -> tuple[str, Path]:
    value = value or _tok()
    f = sim["tmp"] / "new_token"
    f.write_text(value + "\n")
    f.chmod(0o600)
    return value, f


def _run_runner(sim, new_file: Path, **extra) -> subprocess.CompletedProcess:
    env = dict(sim["env"], NEW_TOKEN_FILE=str(new_file), **extra)
    return subprocess.run(["bash", str(RUNNER_SCRIPT)], capture_output=True, text=True, env=env)


def _assert_never_exposed(sim, proc: subprocess.CompletedProcess, *tokens: str) -> None:
    argv = (sim["dir"] / "argv.log").read_text()
    for t in tokens:
        assert t not in proc.stdout, "token leaked to stdout"
        assert t not in proc.stderr, "token leaked to stderr"
        assert t not in argv, "token leaked into a process argv"


def _result(proc) -> dict:
    line = [l for l in proc.stdout.splitlines() if l.startswith("DIAG_ROTATION_RESULT=")]
    assert len(line) == 1, proc.stdout
    return json.loads(line[0].split("=", 1)[1])


def _files(sim) -> dict:
    d = sim["dir"]
    return {
        "dotenv": (d / "dotenv").read_text(),
        "web-api.env": (d / "web-api.env").read_text(),
        "legacy": (d / "legacy_token").read_text(),
        "secret": (d / "secret").read_text(),
    }


# ── the happy path ─────────────────────────────────────────────────────────

def test_rotation_end_to_end_installs_new_everywhere_and_prints_neither(sim):
    new, f = _new_token_file(sim)
    proc = _run_runner(sim, f)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    res = _result(proc)
    assert res == {
        "rotated": "yes", "new_token_200": "yes", "old_token_401": "yes",
        "secret_updated": "yes", "unit_post_state": "active", "final_state": "rotated",
    }
    files = _files(sim)
    assert f"DIAG_READ_TOKEN={new}" in files["dotenv"]
    assert "OTHER_KEY=keep-me" in files["dotenv"]          # other keys preserved
    assert f"DIAG_READ_TOKEN={new}" in files["web-api.env"]
    assert "JWT_SIGNING_KEY=x" in files["web-api.env"]
    assert files["legacy"].strip() == new
    assert files["secret"].strip() == new
    for text in files.values():
        assert sim["old"] not in text                        # old value gone
    # commit deleted the backups (they hold the retired token)
    assert not list(sim["dir"].glob("*.diag-rotate.*"))
    _assert_never_exposed(sim, proc, new, sim["old"])


# ── the VM half on its own ─────────────────────────────────────────────────

def test_vm_script_generates_a_new_value_when_one_exists_and_is_silent(sim):
    """The pre-2026-09-27 script REUSED the existing token. It must now replace it."""
    new = _tok()
    proc = subprocess.run(["bash", str(VM_SCRIPT), "rotate"], input=new + "\n",
                          capture_output=True, text=True, env=sim["env"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (sim["dir"] / "served").read_text() == new     # .env won, new value served
    assert (sim["dir"] / "served").read_text() != sim["old"]
    _assert_never_exposed(sim, proc, new, sim["old"])


def test_vm_script_refuses_a_no_op_rotation(sim):
    before = _files(sim)
    proc = subprocess.run(["bash", str(VM_SCRIPT), "rotate"], input=sim["old"] + "\n",
                          capture_output=True, text=True, env=sim["env"])
    assert proc.returncode == 2
    assert "no_op" in proc.stdout
    assert _files(sim) == before
    _assert_never_exposed(sim, proc, sim["old"])


def test_vm_script_refuses_empty_stdin(sim):
    proc = subprocess.run(["bash", str(VM_SCRIPT), "rotate"], input="",
                          capture_output=True, text=True, env=sim["env"])
    assert proc.returncode == 2
    assert "bad_input" in proc.stdout


def test_vm_script_source_has_no_token_print():
    """Belt to the behavioural tests: no echo/printf of the token to stdout."""
    src = VM_SCRIPT.read_text()
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if ("echo" in s or "printf" in s or "log " in s) and "TOKEN}" in s:
            # the only permitted writes of the value go into a temp file
            assert '>> "${tmp}"' in s or '> "${tmp}"' in s, f"token printed: {s}"


# ── every failure leaves a WORKING state ───────────────────────────────────

def test_secret_set_failure_rolls_vm_back_to_old_token(sim):
    before = _files(sim)
    new, f = _new_token_file(sim)
    proc = _run_runner(sim, f, SIM_GH_SET_FAIL="1")
    assert proc.returncode == 1
    res = _result(proc)
    assert res["final_state"] == "rolled_back"
    assert res["secret_updated"] == "no"
    assert _files(sim) == before                           # VM + secret as before
    assert (sim["dir"] / "served").read_text() == sim["old"]
    _assert_never_exposed(sim, proc, new, sim["old"])


def test_verification_failure_rolls_back_and_leaves_secret_untouched(sim):
    before = _files(sim)
    new, f = _new_token_file(sim)
    proc = _run_runner(sim, f, SIM_FORCE_HTTP="500")
    assert proc.returncode == 1
    res = _result(proc)
    assert res["final_state"] == "rolled_back"
    assert res["new_token_200"] == "no"
    assert _files(sim) == before
    assert "secret set" not in (sim["dir"] / "argv.log").read_text()
    _assert_never_exposed(sim, proc, new, sim["old"])


@pytest.mark.parametrize("extra", [{"GH_TOKEN": ""}, {"SIM_GH_DENY": "1"}, {"OLD_DIAG_READ_TOKEN": ""}])
def test_preflight_refusal_touches_nothing(sim, extra):
    before = _files(sim)
    new, f = _new_token_file(sim)
    proc = _run_runner(sim, f, **extra)
    assert proc.returncode == 1
    assert _result(proc)["final_state"] == "not_rotated"
    assert _files(sim) == before
    assert "init_diag_token.sh" not in (sim["dir"] / "argv.log").read_text()  # VM never contacted
    _assert_never_exposed(sim, proc, new, sim["old"])


def test_runner_refuses_new_equal_to_old(sim):
    _, f = _new_token_file(sim, sim["old"])
    before = _files(sim)
    proc = _run_runner(sim, f)
    assert proc.returncode == 1
    assert _result(proc)["final_state"] == "not_rotated"
    assert _files(sim) == before
    _assert_never_exposed(sim, proc, sim["old"])


def test_runner_refuses_a_stale_vm_script_that_would_print_the_token(sim, tmp_path):
    """The pre-2026-09-27 script PRINTS the token. If the VM has not synced yet,
    the runner must refuse rather than call it."""
    stale = tmp_path / "stale_init_diag_token.sh"
    stale.write_text('#!/usr/bin/env bash\necho "DIAG_READ_TOKEN=$(grep -m1 ^DIAG_READ_TOKEN= "$DIAG_ENV_FILE" | cut -d= -f2-)"\n')
    before = _files(sim)
    new, f = _new_token_file(sim)
    proc = _run_runner(sim, f, REMOTE_SCRIPT=str(stale))
    assert proc.returncode == 1
    assert _result(proc)["final_state"] == "not_rotated"
    assert _files(sim) == before
    _assert_never_exposed(sim, proc, new, sim["old"])


def test_env_without_the_key_is_not_given_a_shadowing_line(sim):
    """.env is loaded AFTER web-api.env. Adding a DIAG line there would shadow
    every later web-api.env writer (set-diag-token, deploy_diag.sh)."""
    d = sim["dir"]
    (d / "dotenv").write_text("OTHER_KEY=keep-me\n")
    subprocess.run(["systemctl", "restart", "ict-web-api.service"], env=sim["env"], check=True)
    assert (d / "served").read_text() == sim["old"]        # served from web-api.env
    new, f = _new_token_file(sim)
    proc = _run_runner(sim, f)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (d / "dotenv").read_text() == "OTHER_KEY=keep-me\n"
    assert f"DIAG_READ_TOKEN={new}" in (d / "web-api.env").read_text()
    assert (d / "served").read_text() == new
    _assert_never_exposed(sim, proc, new, sim["old"])
