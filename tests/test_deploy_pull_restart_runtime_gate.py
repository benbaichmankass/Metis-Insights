"""BL-20260529-002 — pin the non-runtime-commit restart gate in
``scripts/deploy_pull_restart.sh``.

A docs/comms-only commit (session handoff, health-review backlog touch,
sprint log) used to run the full dependency-install + unit-refresh +
restart-every-ict-*-service path, bouncing the live money-path trader for
a change the running processes never load. The gate diffs PRE..POST and
skips the restart only when EVERY changed path is in a known-safe,
non-runtime set (``docs/``, ``tests/``, ``.claude/``, top-level ``*.md``).

These tests stub ``git`` (with a configurable ``diff --name-only`` output)
+ ``systemctl`` + ``python3`` via PATH shadowing and verify:

1. A docs-only diff SKIPS the restart entirely (no units restarted).
2. A runtime diff (``src/``) restarts the long-running units as before.
3. A mixed diff (docs + ``src/``) restarts (any runtime path wins).
4. ``DEPLOY_FORCE_RESTART=1`` forces the restart even for a docs-only diff.
5. FAIL-SAFE: an empty diff while HEAD advanced still restarts.

Companion to test_deploy_pull_restart_enumeration.py (restart-loop body)
and test_deploy_pull_restart_notify_state.py (no-advance early exit).
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_pull_restart.sh"


def _make_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_repo(tmp_path: Path):
    """Fake repo where HEAD advances (PRE != POST) so the gate is reached.
    ``diff_files`` controls what ``git diff --name-only`` reports — write
    to it from the test before running."""
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
        src.replace('REPO_DIR="/home/ubuntu/ict-trading-bot"',
                    f'REPO_DIR="{repo}"')
           .replace("/usr/bin/python3", "python3")
    )
    deploy_copy = repo / "scripts" / "deploy_pull_restart.sh"
    deploy_copy.write_text(patched)
    deploy_copy.chmod(0o755)

    bindir = tmp_path / "bin"
    bindir.mkdir()

    counter_file = tmp_path / "rev_parse_counter"
    counter_file.write_text("0")
    head_file = tmp_path / "fake_head"
    head_file.write_text("oldsha7")
    next_head_file = tmp_path / "next_head"
    next_head_file.write_text("newsha8")
    # diff_files: one path per line; what `git diff --name-only PRE POST`
    # returns. Default empty (fail-safe restart) until a test sets it.
    diff_files = tmp_path / "diff_files"
    diff_files.write_text("")
    _make_stub(
        bindir / "git",
        f"""#!/bin/bash
case "$1" in
  rev-parse)
    case "$2" in
      "HEAD~1") echo "0000000" ;;
      "--short") cat "{next_head_file}" ;;
      *)
        n=$(cat "{counter_file}")
        if [ "$n" = "0" ]; then
          cat "{head_file}"; echo "1" > "{counter_file}"
        else
          cat "{next_head_file}"
        fi
        ;;
    esac
    ;;
  diff) cat "{diff_files}" ;;
  fetch|reset) exit 0 ;;
  *) exit 0 ;;
esac
""",
    )

    units_file = tmp_path / "units.txt"
    units_file.write_text(
        "ict-trader-live.service     loaded active running ICT live trader\n"
        "ict-telegram-bot.service    loaded active running ICT telegram bot\n"
        "ict-web-api.service         loaded active running ICT web API\n"
    )
    restart_log = tmp_path / "restart.log"
    _make_stub(
        bindir / "systemctl",
        f"""#!/bin/bash
case "$1" in
  --version) echo "systemd 250"; exit 0 ;;
  list-units)
    pattern="${{*: -1}}"
    if [[ "$pattern" == "ict-*.service" ]]; then cat "{units_file}"; fi
    exit 0 ;;
  restart) echo "$2" >> "{restart_log}"; exit 0 ;;
  status|start) exit 0 ;;
  is-active) echo "active"; exit 0 ;;
  *) exit 0 ;;
esac
""",
    )
    _make_stub(
        bindir / "sudo",
        '#!/bin/bash\nwhile [[ "$1" == -* ]]; do shift; done\nexec "$@"\n',
    )
    _make_stub(bindir / "python3", "#!/bin/bash\nexit 0\n")

    return {
        "repo": repo,
        "deploy": deploy_copy,
        "bindir": bindir,
        "restart_log": restart_log,
        "diff_files": diff_files,
    }


def _run(fake, env_extra=None):
    env = {**os.environ, "PATH": f"{fake['bindir']}:/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(fake["deploy"])],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _restarted(fake) -> list[str]:
    if not fake["restart_log"].exists():
        return []
    return [ln.strip() for ln in fake["restart_log"].read_text().splitlines() if ln.strip()]


def test_docs_only_diff_skips_restart(fake_repo):
    """Every changed path is docs/tests/.claude/top-level-md → no restart."""
    fake_repo["diff_files"].write_text(
        "docs/sprint-logs/S-FOO-2026-06-01.md\n"
        "docs/claude/health-review-backlog.json\n"
        "CLAUDE.md\n"
        ".claude/skills/health-review/SKILL.md\n"
        "tests/test_something.py\n"
    )
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert _restarted(fake_repo) == []  # nothing restarted
    assert "restart skipped" in res.stdout
    assert "Non-runtime commit" in res.stdout


def test_runtime_diff_restarts(fake_repo):
    """A src/ change falls through to the normal restart loop."""
    fake_repo["diff_files"].write_text("src/main.py\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    restarted = _restarted(fake_repo)
    assert "ict-trader-live.service" in restarted
    assert "ict-web-api.service" in restarted


def test_mixed_diff_restarts(fake_repo):
    """Docs + a single runtime path → the runtime path forces a restart."""
    fake_repo["diff_files"].write_text("docs/readme.md\nconfig/strategies.yaml\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert "ict-trader-live.service" in _restarted(fake_repo)


def test_comms_change_restarts(fake_repo):
    """comms/ is read at runtime (insights/order-package/comms-handler), so
    a comms-only change is NOT in the safe-list and must restart."""
    fake_repo["diff_files"].write_text("comms/claude_strategy_scores.jsonl\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert "ict-trader-live.service" in _restarted(fake_repo)


def test_force_restart_overrides_docs_only(fake_repo):
    """DEPLOY_FORCE_RESTART=1 restarts even when the diff is docs-only."""
    fake_repo["diff_files"].write_text("docs/only.md\n")
    res = _run(fake_repo, env_extra={"DEPLOY_FORCE_RESTART": "1"})
    assert res.returncode == 0, res.stderr
    assert "ict-trader-live.service" in _restarted(fake_repo)


def test_empty_diff_fails_safe_to_restart(fake_repo):
    """FAIL-SAFE: HEAD advanced but the diff is empty (anomalous) → restart
    rather than risk pinning stale code. This is also the existing
    enumeration-test scenario, so behaviour there is unchanged."""
    fake_repo["diff_files"].write_text("")  # empty
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert "ict-trader-live.service" in _restarted(fake_repo)
