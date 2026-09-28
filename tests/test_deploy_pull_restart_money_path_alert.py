"""JC-CA-05 (docs/audits/code-audit-2026-09-27.md §6, `CA-A10-300`, operator
decision 2026-09-28: "Loud alert, manual" -- option B only).

Black-box tests of scripts/deploy_pull_restart.sh's failure alert: the
instant a money-path unit (ict-trader-live / ict-web-api) fails to restart,
or the post-deploy version assertion mismatches, the script must send a
distinct urgent ping (via scripts/send_ping.py) naming the unit, the new
SHA, and the last-known-good SHA -- while preserving every existing exit
code (no auto-revert: a restart failure still returns 0, a version-assertion
failure still returns 4).

Same harness shape as tests/test_deploy_pull_restart_enumeration.py: stub
git/systemctl/sudo/python3/curl/sleep via PATH shadowing and run the real
script as a subprocess.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_pull_restart.sh"


def _make_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_deploy(tmp_path: Path):
    """A fake repo + stubbed toolchain where HEAD advances (oldsha7 ->
    newsha8, so the restart phase runs) and every systemctl restart
    SUCCEEDS by default. Tests control which unit(s) fail via
    ``fail_units_file`` and whether the version-assertion gate is even
    reached via ``WEB_API_UNIT_FILE`` / ``curl_response``.
    """
    repo = tmp_path / "ict-trading-bot"
    (repo / "scripts").mkdir(parents=True)
    (repo / "runtime_logs").mkdir()
    (repo / "runtime_flags").mkdir()
    (repo / "requirements.txt").write_text("")
    install_units = repo / "scripts" / "install_systemd_units.sh"
    install_units.write_text("#!/bin/bash\nexit 0\n")
    install_units.chmod(0o755)

    src = DEPLOY_SCRIPT.read_text()
    patched = (
        src.replace('REPO_DIR="/home/ubuntu/ict-trading-bot"', f'REPO_DIR="{repo}"')
           .replace("/usr/bin/python3", "python3")
    )
    deploy_copy = repo / "scripts" / "deploy_pull_restart.sh"
    deploy_copy.write_text(patched)
    deploy_copy.chmod(0o755)

    bindir = tmp_path / "bin"
    bindir.mkdir()

    # --- git stub: HEAD advances oldsha7 -> newsha8 on the first two reads
    # (PRE_SYNC_HEAD, then POST_SYNC_HEAD), same shape as
    # test_deploy_pull_restart_enumeration.py::fake_repo_with_advance. `git
    # rev-parse -q --verify <sha>^{commit}` (the DEPLOYED_SHA_FILE validity
    # check) and `git diff --name-only` both fall through to the generic
    # `exit 0` / no-output branch, which is what makes the SHA in
    # runtime_logs/deployed_sha.txt "verify" as valid and the runtime-change
    # diff read as empty (=> no non-runtime-commit skip) -- the same
    # behaviour the enumeration tests already rely on.
    counter_file = tmp_path / "rev_parse_counter"
    counter_file.write_text("0")
    head_file = tmp_path / "fake_head"
    head_file.write_text("oldsha7")
    next_head_file = tmp_path / "next_head"
    next_head_file.write_text("newsha8")
    _make_stub(bindir / "git", f"""#!/bin/bash
case "$1" in
  rev-parse)
    case "$2" in
      "HEAD~1") echo "0000000" ;;
      "--short") cat "{next_head_file}" ;;
      *)
        n=$(cat "{counter_file}")
        if [ "$n" = "0" ]; then
          cat "{head_file}"
          echo "1" > "{counter_file}"
        else
          cat "{next_head_file}"
        fi
        ;;
    esac
    ;;
  fetch|reset|diff) exit 0 ;;
  *) exit 0 ;;
esac
""")

    restart_log = tmp_path / "restart.log"
    fail_units_file = tmp_path / "fail_units.txt"
    fail_units_file.write_text("")
    units_file = tmp_path / "units.txt"
    units_file.write_text(
        "ict-trader-live.service     loaded active running ICT live trader\n"
        "ict-web-api.service         loaded active running ICT web API\n"
        "ict-telegram-bot.service    loaded active running ICT telegram bot\n"
    )
    _make_stub(bindir / "systemctl", f"""#!/bin/bash
case "$1" in
  --version) echo "systemd 250"; exit 0 ;;
  list-units)
    pattern="${{*: -1}}"
    if [[ "$pattern" == "ict-*.service" ]]; then
      cat "{units_file}"
    fi
    exit 0
    ;;
  restart)
    echo "$2" >> "{restart_log}"
    if grep -qx "$2" "{fail_units_file}"; then
      exit 1
    fi
    exit 0
    ;;
  status|start) exit 0 ;;
  is-active) echo "active"; exit 0 ;;
  *) exit 0 ;;
esac
""")
    _make_stub(bindir / "sudo",
               '#!/bin/bash\nwhile [[ "$1" == -* ]]; do shift; done\nexec "$@"\n')
    _make_stub(bindir / "sleep", "#!/bin/bash\nexit 0\n")  # the version-assert retry loop sleeps 5s x6

    # --- python3 stub. send_ping.py's argv is logged verbatim (one repr()
    # per line) so tests can assert on the exact message; every other
    # invocation (pip install, notify_on_pull.py, the version-JSON `-c`
    # parse) is a harmless no-op, same as the enumeration tests' stub.
    ping_log = tmp_path / "ping_log.txt"
    _make_stub(bindir / "python3", f"""#!/bin/bash
case "$*" in
  *send_ping.py*)
    printf '%s\\n' "$*" >> "{ping_log}"
    exit 0
    ;;
  -c*)
    # The only other python3 -c invocation in this script is the
    # version-assertion JSON parse (git_sha extraction) -- delegate to the
    # SAME interpreter this test is running under (sys.executable, baked in
    # below), rather than reimplementing json.load badly. A hardcoded path
    # like /usr/local/bin/python3 is not portable -- GitHub Actions runners
    # keep python under /opt/hostedtoolcache/, not /usr/local/bin.
    exec {sys.executable} "$@"
    ;;
  *) exit 0 ;;
esac
""")

    # --- curl stub for the post-deploy version assertion: unreachable
    # (nonzero exit, empty stdout) unless a test writes curl_response.json.
    curl_response = tmp_path / "curl_response.json"
    _make_stub(bindir / "curl", f"""#!/bin/bash
if [ -f "{curl_response}" ]; then
  cat "{curl_response}"
  exit 0
fi
exit 7
""")

    return {
        "repo": repo, "deploy": deploy_copy, "bindir": bindir,
        "restart_log": restart_log, "fail_units_file": fail_units_file,
        "ping_log": ping_log, "curl_response": curl_response,
        "deployed_sha_file": repo / "runtime_logs" / "deployed_sha.txt",
    }


def _run(fake, env_extra=None):
    env = {**os.environ, "PATH": f"{fake['bindir']}:/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(fake["deploy"])],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _pings(fake) -> list[str]:
    if not fake["ping_log"].exists():
        return []
    return [ln for ln in fake["ping_log"].read_text().splitlines() if ln.strip()]


def test_money_path_restart_failure_sends_ping_and_keeps_exit_0(fake_deploy):
    fake_deploy["deployed_sha_file"].write_text("priorsha0")
    fake_deploy["fail_units_file"].write_text("ict-trader-live.service\n")

    res = _run(fake_deploy)

    assert res.returncode == 0, res.stderr  # JC-CA-05: no auto-revert, exit code unchanged
    restarted = fake_deploy["restart_log"].read_text().splitlines()
    assert "ict-trader-live.service" in restarted  # the restart was still ATTEMPTED

    pings = _pings(fake_deploy)
    assert len(pings) == 1, pings
    ping = pings[0]
    assert "ict-trader-live.service" in ping
    assert "newsha8" in ping   # the new (broken) SHA
    assert "priorsh" in ping   # the last-known-good SHA (7-char short form)
    assert "pull-and-deploy" in ping  # names the rollback route
    assert "urgent" in ping   # --priority urgent
    assert "trader" in ping   # --target trader


def test_web_api_restart_failure_also_pings(fake_deploy):
    """Both named money-path units alarm, not just the trader."""
    fake_deploy["deployed_sha_file"].write_text("priorsha0")
    fake_deploy["fail_units_file"].write_text("ict-web-api.service\n")

    res = _run(fake_deploy)

    assert res.returncode == 0, res.stderr
    pings = _pings(fake_deploy)
    assert len(pings) == 1, pings
    assert "ict-web-api.service" in pings[0]


def test_non_money_path_restart_failure_does_not_ping(fake_deploy):
    """ict-telegram-bot.service is NOT a money-path unit -- CA-A10-300's own
    `fix` field names exactly ict-trader-live / ict-web-api, and a ping on
    every restart failure would be the alarm-fatigue P1
    docs/CLAUDE-RULES-CANONICAL.md already names."""
    fake_deploy["deployed_sha_file"].write_text("priorsha0")
    fake_deploy["fail_units_file"].write_text("ict-telegram-bot.service\n")

    res = _run(fake_deploy)

    assert res.returncode == 0, res.stderr
    restarted = fake_deploy["restart_log"].read_text().splitlines()
    assert "ict-telegram-bot.service" in restarted
    assert _pings(fake_deploy) == []


def test_clean_deploy_never_pings(fake_deploy):
    """Sanity / negative control: nothing fails -> no alert at all."""
    fake_deploy["deployed_sha_file"].write_text("priorsha0")

    res = _run(fake_deploy)

    assert res.returncode == 0, res.stderr
    assert _pings(fake_deploy) == []


def test_version_assertion_mismatch_sends_ping_and_exits_4(fake_deploy):
    fake_deploy["deployed_sha_file"].write_text("priorsha0")
    web_api_unit_file = fake_deploy["repo"] / "ict-web-api.service"
    web_api_unit_file.write_text("")  # WEB_API_UNIT_FILE override just needs to exist

    res = _run(fake_deploy, env_extra={
        "WEB_API_UNIT_FILE": str(web_api_unit_file),
        "DIAG_READ_TOKEN": "test-token",
    })
    # curl_response.json was never written -> every attempt is "unreachable"
    # -> ASSERT_OK never sets -> the existing exit 4 path fires.

    assert res.returncode == 4, res.stderr
    pings = _pings(fake_deploy)
    assert len(pings) == 1, pings
    ping = pings[0]
    assert "version assertion" in ping
    assert "newsha8" in ping
    assert "priorsh" in ping
    assert "pull-and-deploy" in ping


def test_version_assertion_match_does_not_ping(fake_deploy):
    """Negative control for the version-assertion path: a MATCHING response
    must not alarm."""
    fake_deploy["deployed_sha_file"].write_text("priorsha0")
    web_api_unit_file = fake_deploy["repo"] / "ict-web-api.service"
    web_api_unit_file.write_text("")
    fake_deploy["curl_response"].write_text('{"git_sha": "newsha8"}')

    res = _run(fake_deploy, env_extra={
        "WEB_API_UNIT_FILE": str(web_api_unit_file),
        "DIAG_READ_TOKEN": "test-token",
    })

    assert res.returncode == 0, res.stderr
    assert _pings(fake_deploy) == []


def test_ping_message_is_parseable_shell_argv(fake_deploy):
    """The logged line is exactly what argv the script passed to
    send_ping.py -- confirms the message is one well-formed shell argument
    (no stray word-splitting from an unquoted variable), not just that some
    text landed in the log."""
    fake_deploy["deployed_sha_file"].write_text("priorsha0")
    fake_deploy["fail_units_file"].write_text("ict-trader-live.service\n")

    res = _run(fake_deploy)
    assert res.returncode == 0, res.stderr

    pings = _pings(fake_deploy)
    assert len(pings) == 1
    # printf '%s\n' "$*" joins argv with single spaces: $1 is send_ping.py's
    # own path, then --priority urgent --target trader, then the message
    # itself as ONE final argument (not split across several words) -- that
    # last part is the one an unquoted "${msg}" in the script would break.
    fields = pings[0].split(" ", 5)
    assert fields[0].endswith("send_ping.py")
    assert fields[1] == "--priority"
    assert fields[2] == "urgent"
    assert fields[3] == "--target"
    assert fields[4] == "trader"
    assert fields[5].startswith("DEPLOY FAILURE (JC-CA-05):")
