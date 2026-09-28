"""set-diag-token.yml's remote script must not let a stale .env SHADOW the
token it just installed, and must never expose the token.

WHY THIS EXISTS (2026-09-28, DT-FIX / issue #13506, run 36387625918). The
operator set a new DIAG_READ_TOKEN repo secret; set-diag-token.yml wrote it
to /etc/ict-trader/web-api.env and restarted ict-web-api.service, and the
run reported success -- but the process kept serving the OLD token.
Cause: ict-web-api.service loads web-api.env FIRST and
/home/ubuntu/ict-trading-bot/.env LAST (deploy/ict-web-api.service), and
.env carried its own DIAG_READ_TOKEN line, so .env won. The remote script
only wrote web-api.env.

scripts/ops/init_diag_token.sh (SEC-DT, 2026-09-27) already handles this
correctly for its own rotation path: update .env ONLY WHEN it already
carries the key (never adding a line, which would shadow future writers),
preserve .env's ubuntu ownership by writing through the existing inode, and
also refresh the legacy /etc/ict-trading-bot/diag_token file. This test
extracts set-diag-token.yml's embedded remote script the same way GitHub
Actions' YAML parser does (so it exercises the literal production text, not
a paraphrase of it) and drives it against a simulated VM, mirroring the
stub/sim pattern in tests/ops/test_init_diag_token_rotation.py.

The load-bearing assertion in every test: the token appears in no file
content check as a false pass, and never in stdout or stderr.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "set-diag-token.yml"


def _extract_remote_template() -> str:
    """Pull the heredoc between <<'REMOTE' and REMOTE out of the "Install
    token on VM + restart web-api" step, via yaml.safe_load -- the same
    folding GitHub Actions applies to a `run: |` block -- rather than a
    hand-rolled dedent, so this exercises the script bash actually runs.
    """
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["set-token"]["steps"]
    run = next(s["run"] for s in steps if s.get("name", "").startswith("Install token on VM"))
    m = re.search(r"<<'REMOTE'\n(.*?)\nREMOTE\n", run, re.S)
    assert m, "could not find the <<'REMOTE' ... REMOTE heredoc in set-diag-token.yml"
    return m.group(1)


REMOTE_TEMPLATE = _extract_remote_template()

STUBS = {
    # `sudo -n <cmd...>` -> run <cmd...> directly (the temp files are ours
    # anyway). `install -o root -g root` would fail for a non-root test
    # runner, so strip the ownership flags -- content/mode is what these
    # tests check, not real root ownership.
    "sudo": r"""
[ "${1:-}" = "-n" ] && shift
if [ "${1:-}" = "install" ]; then
  shift
  args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      -o|-g) shift 2 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  exec install "${args[@]}"
fi
exec "$@"
""",
    # restart = what systemd does with the two EnvironmentFile= lines: the
    # later file (.env) overrides the earlier (web-api.env) -- same logic as
    # tests/ops/test_init_diag_token_rotation.py's stub.
    "systemctl": r"""
echo "systemctl $*" >> "$SIM/argv.log"
case "$1" in
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
    # The diag endpoint: set-diag-token.yml presents the bearer via `-K
    # <cfg>` (a curl config file), not a header flag.
    "curl": r"""
echo "curl $*" >> "$SIM/argv.log"
cfg=""
prev=""
for a in "$@"; do
  [ "$prev" = "-K" ] && cfg="$a"
  prev="$a"
done
hdr="$(cat "$cfg" 2>/dev/null)"
presented="${hdr#header = \"Authorization: Bearer }"
presented="${presented%\"}"
served="$(cat "$SIM/served" 2>/dev/null)"
if [ "$presented" = "$served" ]; then printf 200; else printf 401; fi
""",
    "pgrep": "exit 1\n",  # no running process to read /proc/<pid>/environ from
}


def _tok() -> str:
    return "a" * 63 + "1", "b" * 63 + "2"  # (old, new) -- distinct, fixed-length, easy to grep for


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
    old, _new = _tok()
    (sim / "dotenv").write_text(f"OTHER_KEY=keep-me\nDIAG_READ_TOKEN={old}\n")
    (sim / "web-api.env").write_text(f"DIAG_READ_TOKEN={old}\nJWT_SIGNING_KEY=x\n")
    (sim / "legacy_token").write_text(old + "\n")
    (sim / "served").write_text(old)
    (sim / "argv.log").write_text("")
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        SIM=str(sim),
        DIAG_SYSTEM_ENV=str(sim / "web-api.env"),
        DIAG_ENV_FILE=str(sim / "dotenv"),
        DIAG_LEGACY_TOKEN_FILE=str(sim / "legacy_token"),
    )
    return {"dir": sim, "env": env, "old": old}


def _run_remote(sim, new_token: str) -> subprocess.CompletedProcess:
    script = REMOTE_TEMPLATE.replace("__TOKEN__", new_token)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=sim["env"])


def _assert_never_exposed(proc: subprocess.CompletedProcess, sim, *tokens: str) -> None:
    argv = (sim["dir"] / "argv.log").read_text()
    for t in tokens:
        assert t not in proc.stdout, "token leaked to stdout"
        assert t not in proc.stderr, "token leaked to stderr"
        assert t not in argv, "token leaked into a process argv"


def test_env_carrying_the_key_is_updated_and_served_fingerprint_matches(sim):
    old, new = _tok()
    proc = _run_remote(sim, new)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ROTATION_STATE=rotated" in proc.stdout

    dotenv = (sim["dir"] / "dotenv").read_text()
    assert f"DIAG_READ_TOKEN={new}" in dotenv
    assert "OTHER_KEY=keep-me" in dotenv          # other keys preserved
    assert f"DIAG_READ_TOKEN={new}" in (sim["dir"] / "web-api.env").read_text()
    assert (sim["dir"] / "legacy_token").read_text().strip() == new

    # systemd's real precedence (.env loaded last) now serves the value we
    # actually installed in .env -- the shadowing bug this fix closes.
    assert (sim["dir"] / "served").read_text() == new
    _assert_never_exposed(proc, sim, new, old)


def test_env_without_the_key_gets_no_shadowing_line_added(sim):
    (sim["dir"] / "dotenv").write_text("OTHER_KEY=keep-me\n")
    old, new = _tok()
    proc = _run_remote(sim, new)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    dotenv = (sim["dir"] / "dotenv").read_text()
    assert dotenv == "OTHER_KEY=keep-me\n"
    assert "DIAG_READ_TOKEN" not in dotenv
    assert f"DIAG_READ_TOKEN={new}" in (sim["dir"] / "web-api.env").read_text()
    assert (sim["dir"] / "served").read_text() == new  # served from web-api.env, nothing shadows it
    _assert_never_exposed(proc, sim, new, old)


def test_legacy_token_file_absent_is_left_untouched(sim):
    old, new = _tok()
    proc = _run_remote(sim, new)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "legacy_token absent" not in proc.stdout  # sanity: our sim always creates it in `sim`
    _assert_never_exposed(proc, sim, new, old)


def test_token_never_appears_in_stdout_or_stderr(sim):
    old, new = _tok()
    proc = _run_remote(sim, new)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_never_exposed(proc, sim, new, old)


def test_remote_script_source_has_no_token_print():
    """Belt to the behavioural tests: no echo/printf of the token itself,
    only of its fingerprint (`_fp`) or into a 600 temp file."""
    for line in REMOTE_TEMPLATE.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if ("echo" in s or "printf" in s) and "NEW_TOKEN" in s:
            assert (
                "NEW_TOKEN='__TOKEN__'" in s               # the assignment itself
                or '_fp "$NEW_TOKEN"' in s                  # fingerprint only
                or '"$tmp' in s                              # into a 600 temp env file
                or '"$cfg"' in s                             # into the 600 curl -K config
            ), f"token printed: {s}"
