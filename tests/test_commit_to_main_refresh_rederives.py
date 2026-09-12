"""The stale-branch refresh minted a new sha and pushed the SAME stale file.

`constraint-readout.yml` has concluded `success` **zero** times in its entire
life. Its output is DERIVED FROM the registers it merges, generation takes ~31
minutes, and this repo merges several times an hour — so by the time the checks
run, the base has moved and `session-brief-guard` fails with
`verdict=introduced_registers_changed` (run 34592532948 → PR #11789).

`commit-to-main` already had a one-shot stale-branch refresh for exactly the
"checks ran against a stale base" shape. **It could not help here, for a reason
that had not been written down:** merging `main` in mints a new head sha, which
re-triggers the checks — and that is *all* it does. It never regenerates the
derived file, so the new head carries the same stale content and the same guard
fails one sha later. For a derived-output producer, re-running the checks is not
the remedy; **re-deriving** is.

⚠️ THESE TESTS RUN THE ACTION'S REAL SHELL against a real git repo. A test that
grepped the YAML for `refresh-command` would pass on a step that never executes,
which is the presence-only failure this repo already paid for with
`new-table-wiring-guard`. The logic is extracted verbatim from
`action.yml` by :func:`_rederive_snippet`, and a test asserts the extraction
still finds it — otherwise a rename would silently leave these tests exercising
a stale copy and reporting green.

Run: ``python3 -m pytest tests/test_commit_to_main_refresh_rederives.py``
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ACTION = REPO / ".github" / "actions" / "commit-to-main" / "action.yml"


def _run_block() -> str:
    doc = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    blocks = [s["run"] for s in doc["runs"]["steps"] if s.get("run")]
    assert blocks, "the action has no `run` block — extraction is broken"
    return "\n".join(blocks)


def _rederive_snippet() -> str:
    """The real re-derive logic, lifted from action.yml."""
    src = _run_block()
    start = src.index('REDERIVE_STATE="not_requested"')
    end = src.index('if ! git push origin "HEAD:refs/heads/${BR}"; then', start)
    return textwrap.dedent(src[start:end])


def test_the_snippet_is_actually_found_in_the_action():
    """If the extraction silently failed these tests would exercise nothing."""
    snip = _rederive_snippet()
    for token in ('REFRESH_COMMAND', 'REDERIVE_STATE="rederived"',
                  'REDERIVE_STATE="noop"', 'REDERIVE_STATE="failed_command"'):
        assert token in snip, f"{token} missing — extraction is stale"


def _git(*a, cwd, check=True):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                          text=True, check=check)


@pytest.fixture()
def repo(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    _git("init", "-q", "-b", "main", cwd=d)
    _git("config", "user.email", "t@t", cwd=d)
    _git("config", "user.name", "t", cwd=d)
    (d / "registers.json").write_text("v1\n", encoding="utf-8")
    (d / "derived.md").write_text("from v1\n", encoding="utf-8")
    _git("add", "-A", cwd=d)
    _git("commit", "-qm", "base", cwd=d)
    return d


def _exercise(repo, refresh_command, paths="derived.md"):
    """Run the REAL snippet and return (state, derived-file contents, stdout)."""
    script = (
        "set -u\n"
        f'PATHS="{paths}"\n'
        f'REFRESH_COMMAND={refresh_command!r}\n'
        + _rederive_snippet()
        + '\necho "REDERIVE_STATE=${REDERIVE_STATE}"\n'
    )
    p = repo / "run.sh"
    p.write_text(script, encoding="utf-8")
    r = subprocess.run(["bash", str(p)], cwd=repo, capture_output=True, text=True)
    m = re.search(r"REDERIVE_STATE=(\w+)", r.stdout)
    return (m.group(1) if m else None), (repo / "derived.md").read_text(), r


# ── THE DEFECT THIS FIXES ───────────────────────────────────────────────────

def test_the_derived_file_is_REGENERATED_and_committed():
    """The whole point: a new sha carrying NEW content, not the stale one."""
    pass  # exercised by the repo-backed test below


def test_a_refresh_command_regenerates_and_commits(repo):
    # main moved: the registers advanced under us.
    (repo / "registers.json").write_text("v2\n", encoding="utf-8")
    _git("commit", "-aqm", "registers -> v2", cwd=repo)
    before = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    state, derived, r = _exercise(
        repo, "printf 'from v2\\n' > derived.md")
    assert state == "rederived", r.stdout + r.stderr
    assert derived == "from v2\n", "the derived file must carry the NEW base"
    after = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert after != before, "a re-derive must produce a commit"


def test_without_a_refresh_command_nothing_is_regenerated(repo):
    """THE NEGATIVE CONTROL — and it is the pre-fix behaviour, so it pins
    exactly what was broken. Empty command => the stale file survives."""
    state, derived, r = _exercise(repo, "")
    assert state == "not_requested", r.stdout + r.stderr
    assert derived == "from v1\n", "nothing should have been regenerated"


def test_a_command_that_changes_nothing_is_noop_not_rederived(repo):
    """`noop` is a real answer (the derived file was already right) and must
    not read as `rederived`, nor as the command not having been tried."""
    state, _, r = _exercise(repo, "true")
    assert state == "noop", r.stdout + r.stderr


def test_a_FAILING_command_is_its_own_state_and_is_never_silent(repo):
    """'we re-derived' and 'we tried and could not' are opposite facts about
    the head about to be pushed."""
    state, derived, r = _exercise(repo, "exit 3")
    assert state == "failed_command", r.stdout + r.stderr
    assert derived == "from v1\n", "a failed command must not half-write"


def test_the_four_states_are_all_distinct(repo):
    seen = set()
    for cmd in ("", "true", "exit 3"):
        st, _, _ = _exercise(repo, cmd)
        seen.add(st)
    (repo / "registers.json").write_text("v2\n", encoding="utf-8")
    _git("commit", "-aqm", "v2", cwd=repo)
    st, _, _ = _exercise(repo, "printf 'from v2\\n' > derived.md")
    seen.add(st)
    assert seen == {"not_requested", "noop", "failed_command", "rederived"}, seen


# ── THE SCOPE INVARIANT ─────────────────────────────────────────────────────

def test_a_change_OUTSIDE_the_callers_paths_is_not_committed(repo):
    """`-f` on an explicit pathspec is what stops a caller landing more than it
    declared. A re-derive must not become the hole in that."""
    state, _, r = _exercise(
        repo, "printf 'from v2\\n' > derived.md; printf 'sneak\\n' > other.txt")
    assert state == "rederived", r.stdout + r.stderr
    tracked = _git("ls-files", cwd=repo).stdout.split()
    assert "other.txt" not in tracked, "a file outside PATHS must not be landed"


def test_and_that_out_of_scope_change_is_REPORTED_not_dropped_silently(repo):
    """Otherwise a caller reading `rederived` cannot know what else moved."""
    _, _, r = _exercise(
        repo, "printf 'from v2\\n' > derived.md; printf 'sneak\\n' > other.txt")
    blob = r.stdout + r.stderr
    assert "OUTSIDE this caller's paths" in blob, blob


# ── THE CALLER ──────────────────────────────────────────────────────────────

def test_the_constraint_readout_caller_passes_a_refresh_command():
    doc = yaml.safe_load(
        (REPO / ".github" / "workflows" / "constraint-readout.yml").read_text())
    steps = doc["jobs"]["readout"]["steps"]
    land = [s for s in steps if str(s.get("uses", "")).endswith("commit-to-main")]
    assert len(land) == 1, "expected exactly one commit-to-main call"
    cmd = land[0]["with"].get("refresh-command", "")
    assert "constraint_readout.py --write" in cmd
    assert "render_session_brief.py --write" in cmd, (
        "the brief is the file the guard compares; re-deriving only the readout "
        "leaves the exact failure this fixes")


def test_the_input_is_optional_so_existing_callers_are_unchanged():
    doc = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    spec = doc["inputs"]["refresh-command"]
    assert spec.get("required") is not True
    assert spec.get("default", "") == "", (
        "a non-empty default would run a command for every caller that never "
        "asked for one")
