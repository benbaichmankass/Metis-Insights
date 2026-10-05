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
    prune_log = tmp_path / "prune.log"
    prune_rc = tmp_path / "prune_rc"
    prune_rc.write_text("0")
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
  prune) echo "$*" >> "{prune_log}"; exit "$(cat "{prune_rc}")" ;;
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
        "prune_log": prune_log,
        "prune_rc": prune_rc,
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


# RESTART-SAFE (PI-20261003-OJTPWGCC-0001): paths no long-running process
# imports or caches. 45 of 112 trader restarts in 2026-09-30..10-03 changed only
# these (research queue/results, CI-committed runtime_logs, evidence records).
@pytest.mark.parametrize("path", [
    "research/queue/RQ-20261002-001.yaml",
    "research/results/RQ-20261002-002/37086545949.jsonl",
    "research/THEMES.yaml",
    "runtime_logs/replay_pregate/latest.json",
    "scripts/research/regime_matrix.py",
    "scripts/ci/check_wip_ceiling.py",
    "comms/research/d3_realized_slippage/2026-09-24.json",
    "comms/strategy_evidence/trend_donchian_sol_4h.json",
    # PI-20261004-PUQ1APTH-0006: read per trader tick, never cached at import
    "comms/macro/valuation_snapshots.jsonl",
    "comms/macro/econ_calendar_captures/x.json",
    # scripts/research/ outside the brief's import closure stays non-runtime
    "scripts/research/queue_throughput_notes.py",
    "scripts/research/x/queue_throughput.py",
])
def test_widened_non_runtime_paths_skip_restart(fake_repo, path):
    fake_repo["diff_files"].write_text(f"docs/x.md\n{path}\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert _restarted(fake_repo) == []
    assert "Non-runtime commit" in res.stdout


@pytest.mark.parametrize("path", [
    "scripts/ops/pipeline.py",            # imported by the web API
    "scripts/ml/replay_pregate_live.py",  # imported by src/
    "scripts/prop/prop_executor_tick.py",  # coupled to src/prop
    "comms/strategy_reviews/2026-09-01/INDEX.json",
    "comms/macroeconomics/x.json",         # prefix must be the comms/macro/ directory
    # OA-16(d): imported in-process by the web-api brief (render_daily_brief.py)
    "scripts/research/queue_throughput.py",
    "scripts/research/queue_grade.py",
    "scripts/research/m20_fleet_exit_sweep.py",
    "researchy/x.py",                      # prefix must be a directory
    "config/strategies.yaml",
])
def test_runtime_paths_next_to_the_widened_set_still_restart(fake_repo, path):
    fake_repo["diff_files"].write_text(f"research/queue/x.yaml\n{path}\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert "ict-trader-live.service" in _restarted(fake_repo)


def test_diff_reports_both_sides_of_a_rename_so_a_move_out_of_src_restarts(tmp_path):
    """Review 3: with rename detection `git diff --name-only` names only the NEW
    path, so src/x.py -> scripts/research/x.py would read as research-only and
    skip the restart. Runs the script's own diff command + filter on a real repo."""
    import re as _re
    text = DEPLOY_SCRIPT.read_text()
    diff_line = next(ln for ln in text.splitlines() if "CHANGED_FILES=\"$(git diff" in ln)
    assert "--no-renames" in diff_line
    regex = _re.search(r"grep -vE '([^']+)'", text).group(1)
    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("print('runtime code')\n" * 20)
    run("git", "add", "-A")
    run("git", "commit", "-qm", "a")
    base = run("git", "rev-parse", "HEAD").stdout.strip()
    (repo / "scripts" / "research").mkdir(parents=True)
    run("git", "mv", "src/x.py", "scripts/research/x.py")
    run("git", "commit", "-qm", "b")
    head = run("git", "rev-parse", "HEAD").stdout.strip()
    with_renames = run("git", "diff", "--name-only", base, head).stdout.split()
    no_renames = run("git", "diff", "--no-renames", "--name-only", base, head).stdout.split()
    keep = lambda files: [f for f in files if not _re.match(regex, f)]  # noqa: E731
    assert keep(with_renames) == []                     # the defect: would skip the restart
    assert keep(no_renames) == ["src/x.py"]             # the fix: restarts


# --- PI-20261004-PUQ1APTH-0007 (2): stuck auto-gc ------------------------------

def _seed_gc_log(fake) -> Path:
    gc_log = fake["repo"] / ".git" / "gc.log"
    gc_log.parent.mkdir(parents=True, exist_ok=True)
    gc_log.write_text("warning: There are too many unreachable loose objects\n")
    return gc_log


def test_gc_log_triggers_a_bounded_prune_and_is_removed(fake_repo):
    gc_log = _seed_gc_log(fake_repo)
    fake_repo["diff_files"].write_text("docs/x.md\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert fake_repo["prune_log"].read_text().split() == ["prune", "--expire=1.hour.ago"]
    assert not gc_log.exists()
    assert "removed .git/gc.log" in res.stdout


def test_failed_prune_never_blocks_the_pull_and_keeps_gc_log(fake_repo):
    gc_log = _seed_gc_log(fake_repo)
    fake_repo["prune_rc"].write_text("1")
    fake_repo["diff_files"].write_text("src/main.py\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert gc_log.exists()  # retried on the next tick
    assert "git prune failed" in res.stdout
    assert "ict-trader-live.service" in _restarted(fake_repo)  # the deploy still ran


def test_no_gc_log_means_no_prune(fake_repo):
    fake_repo["diff_files"].write_text("docs/x.md\n")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    assert not fake_repo["prune_log"].exists()


# --- OA-16(d): the web-api brief's in-process scripts/research closure ---------

def _in_process_pattern() -> str:
    import re as _re
    m = _re.search(r"\| grep -E '(\^scripts/research/[^']+)' \|\| true\)\"", DEPLOY_SCRIPT.read_text())
    assert m, "IN_PROCESS_CHANGES grep not found in deploy_pull_restart.sh"
    return m.group(1)


_CLOSURE_PROBE = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from pathlib import Path
from scripts.ops import render_daily_brief as r
b = r.build(root=Path('.'))
r.render(b)
root = os.path.join(os.getcwd(), 'scripts', 'research') + os.sep
files = sorted({
    os.path.relpath(os.path.abspath(m.__file__), os.getcwd())
    for m in list(sys.modules.values())
    if getattr(m, '__file__', None) and os.path.abspath(m.__file__).startswith(root)
})
print(json.dumps({'throughput': b.get('researchThroughput') is not None, 'files': files}))
"""


def test_every_scripts_research_module_the_brief_loads_is_runtime():
    """MEASURE the closure instead of trusting the list. Build and render the
    brief the way /api/bot/work/brief does, then collect every loaded file
    under scripts/research/. Each one must be in the deploy's in-process
    exception, or a change to it would leave the web-api serving stale code."""
    import json as _json
    import re as _re
    import sys as _sys
    out = subprocess.run([_sys.executable, "-c", _CLOSURE_PROBE], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    got = _json.loads(out.stdout.strip().splitlines()[-1])
    # Positive control: the probe must actually reach queue_throughput, or an
    # empty closure would pass vacuously.
    assert got["throughput"], "brief could not compute researchThroughput; the probe proves nothing"
    assert "scripts/research/queue_throughput.py" in got["files"]
    pat = _re.compile(_in_process_pattern())
    missing = [f for f in got["files"] if not pat.match(f)]
    assert not missing, f"the brief loads these in-process but the deploy treats them as non-runtime: {missing}"
